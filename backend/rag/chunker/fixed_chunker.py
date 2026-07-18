"""Canonical fixed-size chunker."""
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker
from rag.chunker.fixed_size_core import chunk_fixed_size


class FixedSizeChunker(BaseChunker):
    """Fixed-size chunking with offset-aware metadata."""

    def chunk_document(self, document: Dict) -> List[Dict]:
        return chunk_fixed_size(
            document,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            schema_style="fields",
        )
