# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：RecursiveChunker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
# src/rag_template/chunker/RecursiveChunker.py
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rag.chunker.base_chunker import BaseChunker
from rag.schema.Chunk_Schema import build_chunk
from rag.util.token_utils import TokenCounter, get_default_token_counter


# 阅读注释（类）：封装 recursive chunker，集中封装相关状态、依赖和行为。
class RecursiveChunker(BaseChunker):
    """
    Token 级递归切分器。

    设计目标：
    1. chunk_size / chunk_overlap 按 token 数解释，不再按字符数解释。
    2. 优先按自然边界切：段落 -> 换行 -> 句子 -> 分号 -> 逗号 -> 空格 -> token 级兜底。
    3. 输出 chunk 尽量不超过 chunk_size tokens。
    4. 给 HeadingChunker 等结构切分器提供超长 section 的兜底拆分能力。

    注意：
    - 如果 HuggingFace tokenizer 可用，则使用真实 tokenizer 计数。
    - 如果不可用，则使用 fallback_regex：中文单字、英文单词、标点近似计数。
    """

    DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "；", ";", "，", ",", " ", ""]

    # 阅读注释（函数）：初始化 RecursiveChunker，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: Optional[List[str]] = None,
        token_counter: Optional[TokenCounter] = None,
    ):
        """初始化 RecursiveChunker，保存运行所需的依赖、配置或状态。

        参数:
            chunk_size: 文本块 size，具体约束请结合类型标注和调用方确认。
            chunk_overlap: 文本块 overlap，具体约束请结合类型标注和调用方确认。
            separators: separators，具体约束请结合类型标注和调用方确认。
            token_counter: Token counter，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：__init__, super, get_default_token_counter。
        """
        super().__init__(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.separators = separators or self.DEFAULT_SEPARATORS
        self.token_counter = token_counter or get_default_token_counter()

    # 阅读注释（函数）：处理 Token count 相关逻辑。
    def token_count(self, text: str) -> int:
        """处理 Token count 相关逻辑。

        参数:
            text: 待处理文本。

        返回:
            int

        阅读提示:
            主要直接调用：self.token_counter.count。
        """
        return self.token_counter.count(text or "")

    # 阅读注释（函数）：处理 文本块 文档 相关逻辑。
    def chunk_document(self, document: Dict) -> List[Dict]:
        """处理 文本块 文档 相关逻辑。

        参数:
            document: 文档，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict]

        阅读提示:
            主要直接调用：document.get, self.split_text_with_offsets, enumerate, chunks.append, build_chunk, self.token_count。
        """
        doc_id = document["doc_id"]
        text = document.get("text", "")
        doc_metadata = document.get("metadata", {})

        pieces = self.split_text_with_offsets(text)
        chunks: List[Dict] = []

        for idx, (chunk_text, start_char, end_char) in enumerate(pieces):
            chunks.append(
                build_chunk(
                    doc_id=doc_id,
                    text=chunk_text,
                    idx=idx,
                    doc_metadata=doc_metadata,
                    chunk_type="recursive",
                    start_char=start_char,
                    end_char=end_char,
                    token_count=self.token_count(chunk_text),
                    extra={"chunk_unit": "token", "tokenizer_backend": self.token_counter.backend},
                )
            )

        return chunks

    # 阅读注释（函数）：处理 split 文本 相关逻辑。
    def split_text(self, text: str) -> List[str]:
        """只返回文本片段，供其他 Chunker 复用。"""
        return [piece for piece, _, _ in self.split_text_with_offsets(text)]

    # 阅读注释（函数）：处理 split 文本 with offsets 相关逻辑。
    def split_text_with_offsets(self, text: str) -> List[Tuple[str, Optional[int], Optional[int]]]:
        """返回：[(chunk_text, start_char, end_char), ...]。"""
        if not text or not text.strip():
            return []

        stripped = text.strip()
        raw_pieces = self._recursive_split(stripped, self.separators)
        merged = self._merge_pieces(raw_pieces)
        return self._add_offsets(text, merged)

    # 阅读注释（函数）：处理 recursive split 相关逻辑。
    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        """处理 recursive split 相关逻辑。

        参数:
            text: 待处理文本。
            separators: separators，具体约束请结合类型标注和调用方确认。

        返回:
            List[str]

        阅读提示:
            主要直接调用：text.strip, self.token_count, self._split_by_token_length, self._recursive_split, text.split, enumerate, part.strip, len。
        """
        text = text.strip()
        if not text:
            return []

        if self.token_count(text) <= self.chunk_size:
            return [text]

        if not separators:
            return self._split_by_token_length(text)

        sep = separators[0]
        rest_separators = separators[1:]

        if sep == "":
            return self._split_by_token_length(text)

        if sep not in text:
            return self._recursive_split(text, rest_separators)

        parts = text.split(sep)
        pieces: List[str] = []

        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            # 中文句号/分号/逗号这类分隔符切分后，保留在当前片段末尾。
            if sep in ["。", "；", ";", "，", ","] and i < len(parts) - 1:
                part = part + sep

            if self.token_count(part) <= self.chunk_size:
                pieces.append(part)
            else:
                pieces.extend(self._recursive_split(part, rest_separators))

        return pieces

    # 阅读注释（函数）：合并 pieces。
    def _merge_pieces(self, pieces: List[str]) -> List[str]:
        """把过碎 piece 合并成接近 chunk_size token 的 chunk。"""
        chunks: List[str] = []
        buffer = ""

        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue

            candidate = f"{buffer}\n\n{piece}" if buffer else piece

            if self.token_count(candidate) <= self.chunk_size:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer.strip())
                buffer = piece

        if buffer:
            chunks.append(buffer.strip())

        if self.chunk_overlap <= 0 or len(chunks) <= 1:
            return chunks

        overlapped: List[str] = []
        prev_tail = ""
        for idx, chunk in enumerate(chunks):
            if idx == 0:
                overlapped.append(chunk)
            else:
                prefix = prev_tail.strip()
                if prefix and not chunk.startswith(prefix):
                    combined = f"{prefix}\n{chunk}"
                    # overlap 后如果超过 chunk_size，说明 prefix 太长；重新截一次 token 级尾巴。
                    if self.token_count(combined) > self.chunk_size:
                        prefix = self._token_suffix(chunk, max(0, self.chunk_size - self.token_count(chunk)))
                        combined = f"{prefix}\n{chunk}" if prefix else chunk
                    overlapped.append(combined.strip())
                else:
                    overlapped.append(chunk)
            prev_tail = self._token_suffix(chunk, self.chunk_overlap)

        return overlapped

    # 阅读注释（函数）：处理 split by Token length 相关逻辑。
    def _split_by_token_length(self, text: str) -> List[str]:
        """
        token 级兜底切分。

        不直接 decode token，而是在原始字符串上用二分搜索找到不超过 chunk_size token 的最大字符窗口。
        这样可以尽量保留原文，offset 也更容易追踪。
        """
        text = text.strip()
        if not text:
            return []

        chunks: List[str] = []
        start = 0
        n = len(text)

        while start < n:
            end = self._max_end_within_token_limit(text, start, self.chunk_size)
            if end <= start:
                end = min(start + 1, n)

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= n:
                break

            if self.chunk_overlap > 0:
                next_start = self._overlap_start(text, start, end, self.chunk_overlap)
                if next_start <= start:
                    next_start = end
                start = next_start
            else:
                start = end

        return chunks

    # 阅读注释（函数）：处理 max end within Token limit 相关逻辑。
    def _max_end_within_token_limit(self, text: str, start: int, token_limit: int) -> int:
        """处理 max end within Token limit 相关逻辑。

        参数:
            text: 待处理文本。
            start: start，具体约束请结合类型标注和调用方确认。
            token_limit: Token limit，具体约束请结合类型标注和调用方确认。

        返回:
            int

        阅读提示:
            主要直接调用：len, self.token_count。
        """
        low = start + 1
        high = len(text)
        best = start

        while low <= high:
            mid = (low + high) // 2
            if self.token_count(text[start:mid]) <= token_limit:
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        return best

    # 阅读注释（函数）：处理 overlap start 相关逻辑。
    def _overlap_start(self, text: str, chunk_start: int, chunk_end: int, overlap_tokens: int) -> int:
        """返回一个字符位置，使 text[pos:chunk_end] 尽量接近但不超过 overlap_tokens。"""
        if overlap_tokens <= 0:
            return chunk_end

        low = chunk_start
        high = chunk_end
        best = chunk_end

        while low <= high:
            mid = (low + high) // 2
            cnt = self.token_count(text[mid:chunk_end])
            if cnt <= overlap_tokens:
                best = mid
                high = mid - 1
            else:
                low = mid + 1

        return best

    # 阅读注释（函数）：处理 Token suffix 相关逻辑。
    def _token_suffix(self, text: str, max_tokens: int) -> str:
        """处理 Token suffix 相关逻辑。

        参数:
            text: 待处理文本。
            max_tokens: max tokens，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：self._overlap_start, len, strip。
        """
        if max_tokens <= 0 or not text:
            return ""
        start = self._overlap_start(text, 0, len(text), max_tokens)
        return text[start:].strip()

    # 阅读注释（函数）：处理 add offsets 相关逻辑。
    def _add_offsets(self, original_text: str, chunks: List[str]) -> List[Tuple[str, Optional[int], Optional[int]]]:
        """处理 add offsets 相关逻辑。

        参数:
            original_text: original 文本，具体约束请结合类型标注和调用方确认。
            chunks: chunks，具体约束请结合类型标注和调用方确认。

        返回:
            List[Tuple[str, Optional[int], Optional[int]]]

        阅读提示:
            主要直接调用：original_text.find, strip, chunk.replace, result.append, len, max, min。
        """
        result: List[Tuple[str, Optional[int], Optional[int]]] = []
        cursor = 0

        for chunk in chunks:
            start = original_text.find(chunk, cursor)
            if start < 0:
                compact = chunk.replace("\n", " ").strip()
                start = original_text.find(compact, cursor)

            if start < 0:
                result.append((chunk, None, None))
            else:
                end = start + len(chunk)
                result.append((chunk, start, end))
                cursor = max(start + 1, end - min(len(chunk), self.chunk_overlap))

        return result
