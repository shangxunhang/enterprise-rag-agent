"""按空行识别自然段，并把相邻短段合并到字符上限的平面 Chunker。

该策略适合政策、报告和说明类文档。它不拆分单个超长段落，也不实现 overlap；需要严格
Token 上限或超长段落兜底时应使用 RecursiveChunker 或结构化父子分块插件。
"""

import re
from typing import List, Dict

from rag.chunker.base_chunker import BaseChunker
from rag.schema.Chunk_Schema import build_chunk


class ParagraphChunker(BaseChunker):
    """以段落为不可分割单元，顺序合并到 ``chunk_size`` 字符。"""

    def chunk_document(self, document: Dict) -> List[Dict]:
        """拆分段落、合并短段，并按原顺序构造旧 Document 风格 Chunk。"""
        text = document["text"]

        paragraphs = self._split_paragraphs(text)
        merged_paragraphs = self._merge_short_paragraphs(paragraphs)

        chunks = []

        for idx, para in enumerate(merged_paragraphs):
            chunk = build_chunk(
                doc=document,
                chunk_text=para,
                chunk_index=idx,
                chunk_type="paragraph",
            )
            chunks.append(chunk)

        return chunks

    def _split_paragraphs(self, text: str) -> List[str]:
        """把一个或多个空白行视为段落边界，并丢弃空段。"""
        paragraphs = re.split(r"\n\s*\n", text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _merge_short_paragraphs(self, paragraphs: List[str]) -> List[str]:
        """贪心合并连续段落；加入下一段会超限时提交当前 Buffer。"""
        merged = []
        buffer = ""

        for para in paragraphs:
            if len(buffer) + len(para) <= self.chunk_size:
                buffer = buffer + "\n\n" + para if buffer else para
            else:
                if buffer:
                    merged.append(buffer)
                buffer = para

        if buffer:
            merged.append(buffer)

        return merged
