"""当前推荐的固定字符窗 Chunker，输出字段式兼容 Chunk Schema。"""
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker
from rag.chunker.fixed_size_core import chunk_fixed_size


class FixedSizeChunker(BaseChunker):
    """固定字符窗切分，并尽力记录每块在原文中的字符偏移。"""

    def chunk_document(self, document: Dict) -> List[Dict]:
        """委托共享算法，并选择 ``fields`` Schema 风格。"""
        return chunk_fixed_size(
            document,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            schema_style="fields",
        )
