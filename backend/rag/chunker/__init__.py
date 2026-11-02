"""文档分块函数、内置 Chunker 和工厂公开入口。"""

from rag.chunker.chunk import chunk_documents
from rag.chunker.fixed_chunker import FixedSizeChunker
from rag.chunker.RecursiveChunker import RecursiveChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.chunk_factory import build_chunker

# 只公开稳定构建接口；具体实现类仍可从其定义模块直接导入。
__all__ = [
    "chunk_documents",
    "FixedSizeChunker",
    "RecursiveChunker",
    "HeadingChunker",
    "build_chunker",
]
