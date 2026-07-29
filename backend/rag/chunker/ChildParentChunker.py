"""把标准 Document 切分为“父块上下文 + 子块匹配”两级结构。

父块较大，用于在召回后恢复完整生成上下文；子块较小，用于 Embedding 和关键词匹配。
当前实现按字符固定窗口切分，父块和子块均记录字符边界、Token 近似数、Chunker 版本
以及父子身份映射。它不执行清洗、Embedding 或索引写入。
"""

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from rag.schema.Chunk_Schema import (
    build_child_chunk,
    build_child_chunk_v1,
    build_parent_chunk,
    build_parent_chunk_v1,
)
from rag.ports.chunking import ParentChildChunkResult
from rag.util.text_utils import normalize_text
from rag.util.token_utils import get_default_token_counter, get_token_counter


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
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 150,
        child_chunk_size: int = 500,
        child_chunk_overlap: int = 50,
        chunker_name: str = "fixed_parent_child_chunker_v1",
        chunker_version: str = "parent_child_chunker_v1.0",
        *,
        unit: str = "char",
        parent_chunk_strategy: Optional[str] = None,
        child_chunk_strategy: Optional[str] = None,
        parent_chunk_version: Optional[str] = None,
        child_chunk_version: Optional[str] = None,
        tokenizer_model_name: Optional[str] = None,
        tokenizer_local_files_only: bool = True,
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
        self.unit = str(unit or "char").strip().lower()
        if self.unit not in {"char", "token"}:
            raise ValueError("unit must be exactly 'char' or 'token'")
        self.parent_chunk_strategy = (
            str(parent_chunk_strategy or "fixed").strip() or "fixed"
        )
        self.child_chunk_strategy = (
            str(child_chunk_strategy or "fixed").strip() or "fixed"
        )
        self.parent_chunk_version = str(
            parent_chunk_version or "parent_chunk_v1.0"
        )
        self.child_chunk_version = str(
            child_chunk_version or "child_chunk_v1.0"
        )

        # 第四步：Token 计数优先使用本地 Hugging Face Tokenizer，失败时正则回退。
        self.token_counter = (
            get_token_counter(
                tokenizer_model_name,
                tokenizer_local_files_only,
            )
            if tokenizer_model_name
            else get_default_token_counter()
        )

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

    def _split_text_windows_with_offsets(
        self,
        text: str,
        *,
        chunk_size: int,
        overlap: int,
    ) -> List[tuple[str, int, int]]:
        """Return text windows with source character offsets."""

        value = text or ""
        if not value.strip():
            return []
        if self.unit == "token" and self.token_counter.tokenizer is not None:
            try:
                encoded = self.token_counter.tokenizer(
                    value,
                    add_special_tokens=False,
                    return_offsets_mapping=True,
                )
                token_ids = encoded.get("input_ids") or []
                offsets = encoded.get("offset_mapping") or []
                if token_ids and len(token_ids) == len(offsets):
                    windows: List[tuple[str, int, int]] = []
                    token_start = 0
                    step = chunk_size - overlap
                    while token_start < len(token_ids):
                        token_end = min(token_start + chunk_size, len(token_ids))
                        char_start = int(offsets[token_start][0])
                        char_end = int(offsets[token_end - 1][1])
                        piece = value[char_start:char_end].strip()
                        if piece:
                            windows.append((piece, char_start, char_end))
                        if token_end >= len(token_ids):
                            break
                        token_start += step
                    return windows
            except Exception:
                pass
        return [
            (piece, start, end)
            for start, end, piece in self._windows(
                value,
                chunk_size,
                overlap,
            )
        ]

    @staticmethod
    def _record_value(
        records: List[Dict[str, Any]],
        key: str,
        default: Any = None,
        *,
        reverse: bool = False,
    ) -> Any:
        source = reversed(records) if reverse else records
        for record in source:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return default

    @staticmethod
    def _record_bounds(
        records: List[Dict[str, Any]],
        key: str,
        *,
        maximum: bool,
    ) -> Optional[int]:
        values: List[int] = []
        for record in records:
            value = record.get(key)
            if value in (None, ""):
                continue
            try:
                values.append(int(value))
            except Exception:
                continue
        if not values:
            return None
        return max(values) if maximum else min(values)

    def chunk_records(
        self,
        records: Iterable[Dict[str, Any]],
    ) -> ParentChildChunkResult:
        """Adapt cleaned text units to the stable parent/child result contract."""

        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for raw in records:
            record = dict(raw)
            doc_id = str(record.get("doc_id") or "").strip()
            text = normalize_text(record.get("text") or "")
            if not doc_id or not text:
                continue
            record["doc_id"] = doc_id
            record["text"] = text
            groups[doc_id].append(record)

        output_parents: List[Dict[str, Any]] = []
        output_children: List[Dict[str, Any]] = []
        global_child_index = 1
        for doc_id in sorted(groups):
            units = sorted(
                groups[doc_id],
                key=lambda item: int(item.get("unit_order") or 0),
            )
            pieces: List[str] = []
            unit_spans: List[tuple[int, int, Dict[str, Any]]] = []
            cursor = 0
            for unit in units:
                if pieces:
                    pieces.append("\n")
                    cursor += 1
                value = str(unit["text"])
                start = cursor
                pieces.append(value)
                cursor += len(value)
                unit_spans.append((start, cursor, unit))
            document_text = "".join(pieces)
            document = {
                "doc_id": doc_id,
                "text": document_text,
                "source": self._record_value(units, "source_uri"),
                "source_path": self._record_value(units, "source_uri"),
                "title": self._record_value(units, "title", reverse=True),
                "section": self._record_value(units, "section", reverse=True),
                "language": self._record_value(units, "language", "unknown"),
                "extra": {},
            }
            raw_parents, raw_children = self.chunk_document(document)
            parent_by_id = {
                str(item["parent_chunk_id"]): item for item in raw_parents
            }
            for raw_parent in raw_parents:
                start = int(raw_parent.get("start_char") or 0)
                end = int(raw_parent.get("end_char") or start)
                parent_units = [
                    unit
                    for unit_start, unit_end, unit in unit_spans
                    if unit_start < end and unit_end > start
                ] or units
                output_parents.append(
                    build_parent_chunk_v1(
                        parent_chunk_id=str(raw_parent["parent_chunk_id"]),
                        doc_id=doc_id,
                        text=str(raw_parent.get("text") or ""),
                        tenant_id=self._record_value(
                            parent_units, "tenant_id", "default"
                        ),
                        kb_id=self._record_value(
                            parent_units, "kb_id", "default"
                        ),
                        file_id=self._record_value(parent_units, "file_id"),
                        source_type=self._record_value(
                            parent_units, "source_type", "offline"
                        ),
                        source_unit_ids=[
                            str(item.get("unit_id"))
                            for item in parent_units
                            if item.get("unit_id")
                        ],
                        child_chunk_ids=list(
                            raw_parent.get("child_chunk_ids") or []
                        ),
                        title=self._record_value(
                            parent_units, "title", reverse=True
                        ),
                        section=self._record_value(
                            parent_units, "section", reverse=True
                        ),
                        section_level=self._record_value(
                            parent_units, "section_level", reverse=True
                        ),
                        page_start=self._record_bounds(
                            parent_units, "page_start", maximum=False
                        ),
                        page_end=self._record_bounds(
                            parent_units, "page_end", maximum=True
                        ),
                        parent_chunk_index=int(
                            raw_parent.get("parent_index") or 0
                        )
                        + 1,
                        parent_chunk_strategy=self.parent_chunk_strategy,
                        cleaning_version=self._record_value(
                            parent_units,
                            "cleaning_version",
                            "cleaning_v1.0",
                            reverse=True,
                        ),
                        parent_chunk_version=self.parent_chunk_version,
                        token_count=raw_parent.get("token_count"),
                        extra={
                            "chunker_name": self.chunker_name,
                            "source_char_start": start,
                            "source_char_end": end,
                        },
                    )
                )
            for raw_child in raw_children:
                parent = parent_by_id[str(raw_child["parent_chunk_id"])]
                start = int(parent.get("start_char") or 0)
                end = int(parent.get("end_char") or start)
                child_units = [
                    unit
                    for unit_start, unit_end, unit in unit_spans
                    if unit_start < end and unit_end > start
                ] or units
                output_children.append(
                    build_child_chunk_v1(
                        child_chunk_id=str(raw_child["child_chunk_id"]),
                        parent_chunk_id=str(raw_child["parent_chunk_id"]),
                        doc_id=doc_id,
                        text=str(raw_child.get("text") or ""),
                        tenant_id=self._record_value(
                            child_units, "tenant_id", "default"
                        ),
                        kb_id=self._record_value(
                            child_units, "kb_id", "default"
                        ),
                        file_id=self._record_value(child_units, "file_id"),
                        source_type=self._record_value(
                            child_units, "source_type", "offline"
                        ),
                        source_unit_ids=[
                            str(item.get("unit_id"))
                            for item in child_units
                            if item.get("unit_id")
                        ],
                        title=self._record_value(
                            child_units, "title", reverse=True
                        ),
                        section=self._record_value(
                            child_units, "section", reverse=True
                        ),
                        section_level=self._record_value(
                            child_units, "section_level", reverse=True
                        ),
                        page_start=self._record_bounds(
                            child_units, "page_start", maximum=False
                        ),
                        page_end=self._record_bounds(
                            child_units, "page_end", maximum=True
                        ),
                        child_chunk_index=global_child_index,
                        child_index_in_parent=int(
                            raw_child.get("child_index") or 0
                        )
                        + 1,
                        child_chunk_strategy=self.child_chunk_strategy,
                        char_start_in_parent=max(
                            0,
                            int(raw_child.get("start_char") or 0) - start,
                        ),
                        char_end_in_parent=max(
                            0,
                            int(raw_child.get("end_char") or 0) - start,
                        ),
                        cleaning_version=self._record_value(
                            child_units,
                            "cleaning_version",
                            "cleaning_v1.0",
                            reverse=True,
                        ),
                        parent_chunk_version=self.parent_chunk_version,
                        child_chunk_version=self.child_chunk_version,
                        token_count=raw_child.get("token_count"),
                        extra={"chunker_name": self.chunker_name},
                    )
                )
                global_child_index += 1
        return ParentChildChunkResult(
            parents=output_parents,
            children=output_children,
        )
