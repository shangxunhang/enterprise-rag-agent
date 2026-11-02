"""旧平面 Chunker 的策略名到实现对象映射。

``heading_recursive`` 不是独立算法：HeadingChunker 对超长章节已经内置 RecursiveChunker
兜底，因此两个策略名指向同一实现。
"""
from rag.chunker.fixed_chunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.RecursiveChunker import RecursiveChunker


def build_chunker(chunk_strategy: str, chunk_size: int, chunk_overlap: int):
    """规范化策略别名并构造 Fixed、Recursive 或 Heading Chunker。"""
    strategy = (chunk_strategy or "fixed").lower().strip()

    if strategy in ["fixed", "fixed_size"]:
        return FixedSizeChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    if strategy in ["recursive", "recursive_chunker"]:
        return RecursiveChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    if strategy in ["heading", "heading_recursive", "section", "section_heading"]:
        return HeadingChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    raise ValueError(
        f"Unsupported CHUNK_STRATEGY={chunk_strategy}. "
        f"可选：fixed / recursive / heading / heading_recursive"
    )
