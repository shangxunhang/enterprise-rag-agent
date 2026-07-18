"""Compatibility adapter for the historical CamelCase import path."""
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker
from rag.chunker.fixed_size_core import chunk_fixed_size


class FixedSizeChunker(BaseChunker):
    """Preserve the historical document-style chunk metadata contract."""

    def chunk_document(self, document: Dict) -> List[Dict]:
        return chunk_fixed_size(
            document,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            schema_style="document",
        )
