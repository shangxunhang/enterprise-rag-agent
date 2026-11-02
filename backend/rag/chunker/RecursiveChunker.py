"""按 Token 预算递归选择自然边界的通用 Chunker。

分隔优先级为段落、换行、句号、分号、逗号、空格，最后在原字符串上二分搜索 Token
窗口。切分后会重新合并过碎片段并加入 Token 级 overlap；可用 HuggingFace tokenizer
时使用真实计数，否则由 TokenCounter 使用稳定正则近似。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rag.chunker.base_chunker import BaseChunker
from rag.schema.Chunk_Schema import build_chunk
from rag.util.token_utils import TokenCounter, get_default_token_counter


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

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: Optional[List[str]] = None,
        token_counter: Optional[TokenCounter] = None,
    ):
        """保存 Token 窗口、分隔优先级和可注入计数器。"""
        super().__init__(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.separators = separators or self.DEFAULT_SEPARATORS
        self.token_counter = token_counter or get_default_token_counter()

    def token_count(self, text: str) -> int:
        """通过统一 TokenCounter 计算文本长度。"""
        return self.token_counter.count(text or "")

    def chunk_document(self, document: Dict) -> List[Dict]:
        """切分一个标准 Document，并记录 Token 计数、Backend 和原文偏移。"""
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

    def split_text(self, text: str) -> List[str]:
        """只返回正文片段的便捷接口，供 HeadingChunker 等复用。"""
        return [piece for piece, _, _ in self.split_text_with_offsets(text)]

    def split_text_with_offsets(self, text: str) -> List[Tuple[str, Optional[int], Optional[int]]]:
        """递归切分、重新合并并尽力定位每块在原文中的字符区间。"""
        if not text or not text.strip():
            return []

        stripped = text.strip()
        raw_pieces = self._recursive_split(stripped, self.separators)
        merged = self._merge_pieces(raw_pieces)
        return self._add_offsets(text, merged)

    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        """按当前最高优先级分隔符拆分，超长子段继续递归到更细边界。

        句号、分号和逗号会保留在前一片段末尾；所有自然边界耗尽后转入 Token 窗口兜底。
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

    def _merge_pieces(self, pieces: List[str]) -> List[str]:
        """贪心合并细粒度片段，并从前一块尾部加入不超过预算的 Token overlap。"""
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

    def _split_by_token_length(self, text: str) -> List[str]:
        """在原字符串上二分查找最大 Token 合规窗口，并按 Token 数回退 overlap。

        不直接 decode Token，因而尽量保持原文字符与偏移可追踪。
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

    def _max_end_within_token_limit(self, text: str, start: int, token_limit: int) -> int:
        """二分查找从 ``start`` 起不超过 Token 上限的最远字符位置。"""
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

    def _overlap_start(self, text: str, chunk_start: int, chunk_end: int, overlap_tokens: int) -> int:
        """二分查找尾部 Token 数最接近且不超过 overlap 上限的起点。"""
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

    def _token_suffix(self, text: str, max_tokens: int) -> str:
        """提取不超过指定 Token 数的文本尾部。"""
        if max_tokens <= 0 or not text:
            return ""
        start = self._overlap_start(text, 0, len(text), max_tokens)
        return text[start:].strip()

    def _add_offsets(self, original_text: str, chunks: List[str]) -> List[Tuple[str, Optional[int], Optional[int]]]:
        """从游标后顺序定位 Chunk；无法无歧义匹配时保留空偏移。

        游标会考虑 overlap 向前回退，允许下一块在前一块尾部开始。
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
