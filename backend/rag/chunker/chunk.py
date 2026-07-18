"""
src/rag_template/chunker/chunk.py
=================================核心 chunk_documents 入口。

保留旧入口：
    from rag_template.chunker.chunk import chunk_documents

内部通过 CHUNK_STRATEGY 选择不同 Chunker：
    fixed / recursive / heading / heading_recursive
"""

from typing import Dict, List, Optional

from rag.chunker.chunk_factory import build_chunker


def chunk_documents(
    documents: List[Dict],
    chunk_size: int,
    chunk_overlap: int,
    chunk_strategy: Optional[str] = None,
    created_at: str = None,
    updated_at: str = None,
) -> List[Dict]:
    """
    批量切分 documents，生成统一 Chunk Schema。

    Args:
        documents: 标准 Document Schema 列表
        chunk_size: chunk 最大 token 数，Recursive/Heading 按 token 控制
        chunk_overlap: 相邻 chunk 重叠 token 数，Recursive/Heading 按 token 控制
        chunk_strategy: fixed / recursive / heading / heading_recursive；不传则读取 RAGConfig.CHUNK_STRATEGY
        created_at: 兼容旧参数，当前不再直接使用
        updated_at: 兼容旧参数，当前不再直接使用
    """
    if chunk_strategy is None:
        try:
            from rag.configs.RAGConfig import CHUNK_STRATEGY
            chunk_strategy = CHUNK_STRATEGY
        except Exception:
            chunk_strategy = "fixed"

    chunker = build_chunker(
        chunk_strategy=chunk_strategy,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return chunker.chunk_documents(documents)
