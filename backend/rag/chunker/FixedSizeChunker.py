"""历史 CamelCase import 路径的 FixedSizeChunker 兼容适配器。"""
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker
from rag.chunker.fixed_size_core import chunk_fixed_size


class FixedSizeChunker(BaseChunker):
    """保留旧 ``build_chunk(doc=...)`` 生成的 Document 风格 metadata。"""

    def chunk_document(self, document: Dict) -> List[Dict]:
        """委托共享算法，并选择历史 ``document`` Schema 风格。"""
        return chunk_fixed_size(
            document,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            schema_style="document",
        )
