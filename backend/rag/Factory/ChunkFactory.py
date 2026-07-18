# chunker/chunk_factory.py
from rag.chunker.FixedSizeChunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.ParagraphChunker import ParagraphChunker
from rag.chunker.RecursiveChunker import RecursiveChunker


def build_chunker(
    chunker_type: str,
    chunk_size: int,
    chunk_overlap: int,
):
    if chunker_type == "fixed_size":
        return FixedSizeChunker(chunk_size, chunk_overlap)

    if chunker_type == "paragraph":
        return ParagraphChunker(chunk_size, chunk_overlap)

    if chunker_type == "heading":
        return HeadingChunker(chunk_size, chunk_overlap)

    if chunker_type == "recursive":
        return RecursiveChunker(chunk_size, chunk_overlap)

    raise ValueError(f"Unsupported chunker_type: {chunker_type}")