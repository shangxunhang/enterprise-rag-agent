"""历史 ``chunk_documents`` 批量切分入口。

未显式传入策略时读取旧 ``RAGConfig.CHUNK_STRATEGY``，配置不可用则保守回退 fixed。
具体对象由 ``chunk_factory`` 构造；``created_at/updated_at`` 仅为旧调用签名兼容参数，
当前 Chunk Schema 的时间来源不在此入口控制。
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
    """按指定策略批量切分标准 Document，并返回平铺的 Chunk 列表。

    Fixed 的大小沿用字符窗实现；Recursive/Heading 以 tokenizer 估算 Token 预算。调用方
    不应仅凭参数名假设所有策略使用相同单位。
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
