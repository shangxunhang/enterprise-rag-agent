"""把标准 Document 切分为“父块上下文 + 子块匹配”两级结构。

父块较大，用于在召回后恢复完整生成上下文；子块较小，用于 Embedding 和关键词匹配。
当前实现按字符固定窗口切分，父块和子块均记录字符边界、Token 近似数、Chunker 版本
以及父子身份映射。它不执行清洗、Embedding 或索引写入。
"""

from typing import Any, Dict, List

from rag.schema.Chunk_Schema import (
    build_child_chunk,
    build_parent_chunk,
)
from rag.util.text_utils import normalize_text
from rag.util.token_utils import get_default_token_counter


class ChildParentChunker:
    """基于固定字符窗口生成父块和子块。

    Attributes:
        parent_chunk_size: 父块最大字符数。
        parent_chunk_overlap: 相邻父块重叠字符数。
        child_chunk_size: 子块最大字符数。
        child_chunk_overlap: 相邻子块重叠字符数。
        chunker_name: 写入 Chunk 血缘的实现名称。
        chunker_version: 写入 Chunk 血缘的版本。
        token_counter: 用于记录 token_count 的默认 Token 计数器。
    """

    def __init__(
        self,
        parent_chunk_size: int,
        parent_chunk_overlap: int,
        child_chunk_size: int,
        child_chunk_overlap: int,
        chunker_name: str = "fixed_parent_child_chunker_v1",
        chunker_version: str = "parent_child_chunker_v1.0",
    ) -> None:
        """校验两级窗口参数并初始化 Token 计数器。

        Raises:
            ValueError: 任一块大小非正、重叠为负或重叠不小于对应块大小时抛出。
        """

        # 第一步：父块和子块都必须具有正窗口，保证至少能容纳一个字符。
        if parent_chunk_size <= 0:
            raise ValueError("parent_chunk_size must be positive")
        if child_chunk_size <= 0:
            raise ValueError("child_chunk_size must be positive")

        # 第二步：重叠量不能为负，也必须小于窗口大小以保证步长为正。
        if parent_chunk_overlap < 0:
            raise ValueError(
                "parent_chunk_overlap must be non-negative"
            )
        if child_chunk_overlap < 0:
            raise ValueError(
                "child_chunk_overlap must be non-negative"
            )
        if parent_chunk_overlap >= parent_chunk_size:
            raise ValueError(
                "parent_chunk_overlap must be smaller than "
                "parent_chunk_size"
            )
        if child_chunk_overlap >= child_chunk_size:
            raise ValueError(
                "child_chunk_overlap must be smaller than "
                "child_chunk_size"
            )

        # 第三步：保存确定性切分和血缘参数。
        self.parent_chunk_size = parent_chunk_size
        self.parent_chunk_overlap = parent_chunk_overlap
        self.child_chunk_size = child_chunk_size
        self.child_chunk_overlap = child_chunk_overlap
        self.chunker_name = chunker_name
        self.chunker_version = chunker_version

        # 第四步：Token 计数优先使用本地 Hugging Face Tokenizer，失败时正则回退。
        self.token_counter = get_default_token_counter()

    @staticmethod
    def _windows(
        text: str,
        size: int,
        overlap: int,
    ) -> List[tuple[int, int, str]]:
        """把文本切为带绝对字符边界的固定窗口。

        Args:
            text: 已规范化文本。
            size: 窗口最大字符数。
            overlap: 相邻窗口重叠字符数。

        Returns:
            ``(start_char, end_char, chunk_text)`` 列表。空文本返回空列表。
        """

        if not text:
            return []

        windows: List[tuple[int, int, str]] = []
        start = 0
        step = size - overlap
        text_length = len(text)

        # 按正步长推进窗口；末块自然截断到文本末尾。
        while start < text_length:
            end = min(start + size, text_length)
            chunk_text = text[start:end].strip()
            if chunk_text:
                windows.append((start, end, chunk_text))

            # 已覆盖全文时退出，避免因重叠产生额外空窗口。
            if end >= text_length:
                break
            start += step

        return windows

    def chunk_document(
        self,
        document: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """把一个标准 Document 转换为父块和子块列表。

        Args:
            document: 至少包含 ``doc_id`` 和 ``text`` 的标准文档字典。

        Returns:
            ``(parent_chunks, child_chunks)``。空正文返回两个空列表。

        Raises:
            ValueError: doc_id 为空时抛出，因为所有块身份和父子血缘都依赖它。
        """

        # 第一步：读取并规范化文档身份和正文。
        doc_id = str(document.get("doc_id") or "").strip()
        if not doc_id:
            raise ValueError("document.doc_id cannot be empty")

        text = normalize_text(document.get("text") or "")
        if not text:
            return [], []

        # 第二步：提取父子块共同继承的来源和业务字段。
        shared = {
            "source": document.get("source"),
            "source_path": document.get("source_path"),
            "title": document.get("title"),
            "section": document.get("section"),
            "page_num": document.get("page_num"),
            "language": document.get("language") or "zh",
            "status": document.get("status") or "active",
            "security_level": (
                document.get("security_level") or "internal"
            ),
        }

        # 第三步：文档 extra 复制到每个块，避免块之间共享同一个可变字典。
        document_extra = dict(document.get("extra") or {})

        parent_chunks: List[Dict[str, Any]] = []
        child_chunks: List[Dict[str, Any]] = []
        global_child_index = 0

        # 第四步：先按父级窗口切分全文。
        parent_windows = self._windows(
            text,
            self.parent_chunk_size,
            self.parent_chunk_overlap,
        )

        for parent_index, (
            parent_start,
            parent_end,
            parent_text,
        ) in enumerate(parent_windows):
            # 父块 ID 由文档身份和父级顺序确定，跨相同输入构建稳定。
            parent_chunk_id = (
                f"{doc_id}_parent_{parent_index:04d}"
            )

            # 第五步：在父块文本内部继续切分子窗口。
            child_windows = self._windows(
                parent_text,
                self.child_chunk_size,
                self.child_chunk_overlap,
            )
            child_ids: List[str] = []

            for child_index, (
                relative_start,
                relative_end,
                child_text,
            ) in enumerate(child_windows):
                child_chunk_id = (
                    f"{parent_chunk_id}_child_{child_index:04d}"
                )
                child_ids.append(child_chunk_id)

                # 子块字符偏移必须转换回原文绝对位置，便于来源定位。
                absolute_start = parent_start + relative_start
                absolute_end = min(
                    parent_start + relative_end,
                    parent_end,
                )

                child_extra = {
                    **document_extra,
                    "parent_start_char": parent_start,
                    "parent_end_char": parent_end,
                    "token_counter_backend": (
                        self.token_counter.backend
                    ),
                }

                # 第六步：构造子块，记录父块顺序、局部顺序和全局顺序。
                child_chunks.append(
                    build_child_chunk(
                        child_chunk_id=child_chunk_id,
                        parent_chunk_id=parent_chunk_id,
                        doc_id=doc_id,
                        text=child_text,
                        child_index=child_index,
                        parent_index=parent_index,
                        global_chunk_index=global_child_index,
                        start_char=absolute_start,
                        end_char=absolute_end,
                        token_count=self.token_counter.count(
                            child_text
                        ),
                        chunker_name=self.chunker_name,
                        chunker_version=self.chunker_version,
                        extra=child_extra,
                        **shared,
                    )
                )
                global_child_index += 1

            # 第七步：子块全部生成后构造父块，并写入有序 child_chunk_ids。
            parent_extra = {
                **document_extra,
                "token_counter_backend": self.token_counter.backend,
            }
            parent_chunks.append(
                build_parent_chunk(
                    parent_chunk_id=parent_chunk_id,
                    doc_id=doc_id,
                    text=parent_text,
                    parent_index=parent_index,
                    child_chunk_ids=child_ids,
                    start_char=parent_start,
                    end_char=parent_end,
                    token_count=self.token_counter.count(
                        parent_text
                    ),
                    chunker_name=self.chunker_name,
                    chunker_version=self.chunker_version,
                    extra=parent_extra,
                    **shared,
                )
            )

        return parent_chunks, child_chunks

    def chunk_documents(
        self,
        documents: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """按输入顺序批量切分多个文档。

        Args:
            documents: 标准 Document 字典列表。

        Returns:
            所有父块和所有子块的拼接列表。每篇文档内部顺序保持稳定，文档之间顺序
            与输入一致。
        """

        all_parents: List[Dict[str, Any]] = []
        all_children: List[Dict[str, Any]] = []

        # 逐文档复用单文档逻辑，异常直接暴露给离线构建调用方。
        for document in documents:
            parents, children = self.chunk_document(document)
            all_parents.extend(parents)
            all_children.extend(children)

        return all_parents, all_children
