# chunker/paragraph_chunker.py

import re
from typing import List, Dict

from rag.chunker.base_chunker import BaseChunker
from rag.legacy.schema.Chunk_Schema import build_chunk


class ParagraphChunker(BaseChunker):
    """
    段落切分。
    适合政策、报告、说明文档、普通长文档。
    """

    def chunk_document(self, document: Dict) -> List[Dict]:
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
        paragraphs = re.split(r"\n\s*\n", text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _merge_short_paragraphs(self, paragraphs: List[str]) -> List[str]:
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