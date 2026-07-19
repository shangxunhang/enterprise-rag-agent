# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：FixedSizeChunker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Compatibility adapter for the historical CamelCase import path."""
from typing import Dict, List

from rag.chunker.base_chunker import BaseChunker
from rag.chunker.fixed_size_core import chunk_fixed_size


# 阅读注释（类）：封装 fixed size chunker，集中封装相关状态、依赖和行为。
class FixedSizeChunker(BaseChunker):
    """Preserve the historical document-style chunk metadata contract."""

    # 阅读注释（函数）：处理 文本块 文档 相关逻辑。
    def chunk_document(self, document: Dict) -> List[Dict]:
        """处理 文本块 文档 相关逻辑。

        参数:
            document: 文档，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict]

        阅读提示:
            主要直接调用：chunk_fixed_size。
        """
        return chunk_fixed_size(
            document,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            schema_style="document",
        )
