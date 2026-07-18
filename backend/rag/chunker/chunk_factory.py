# src/rag_template/chunker/chunk_factory.py
from rag.chunker.fixed_chunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.RecursiveChunker import RecursiveChunker


def build_chunker(chunk_strategy: str, chunk_size: int, chunk_overlap: int):
    """
    根据策略名创建 Chunker。

    支持：
    - fixed
    - recursive
    - heading
    - heading_recursive: 等同 heading，因为 HeadingChunker 内部已经对超长 section 递归兜底
    """
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
