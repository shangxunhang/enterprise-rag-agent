# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：build_chunker。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
# chunker/chunk_factory.py
from rag.chunker.FixedSizeChunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.ParagraphChunker import ParagraphChunker
from rag.chunker.RecursiveChunker import RecursiveChunker


# 阅读注释（函数）：构建 chunker。
def build_chunker(
    chunker_type: str,
    chunk_size: int,
    chunk_overlap: int,
):
    """构建 chunker。

    参数:
        chunker_type: chunker 类型，具体约束请结合类型标注和调用方确认。
        chunk_size: 文本块 size，具体约束请结合类型标注和调用方确认。
        chunk_overlap: 文本块 overlap，具体约束请结合类型标注和调用方确认。

    返回:
        未显式标注；请结合调用方和实际返回语句理解。

    阅读提示:
        主要直接调用：FixedSizeChunker, ParagraphChunker, HeadingChunker, RecursiveChunker, ValueError。
    """
    if chunker_type == "fixed_size":
        return FixedSizeChunker(chunk_size, chunk_overlap)

    if chunker_type == "paragraph":
        return ParagraphChunker(chunk_size, chunk_overlap)

    if chunker_type == "heading":
        return HeadingChunker(chunk_size, chunk_overlap)

    if chunker_type == "recursive":
        return RecursiveChunker(chunk_size, chunk_overlap)

    raise ValueError(f"Unsupported chunker_type: {chunker_type}")