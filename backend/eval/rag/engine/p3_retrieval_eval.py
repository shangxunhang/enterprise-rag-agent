"""向旧版评测调用方重新导出生产 RAG 的检索指标实现。

检索指标的唯一实现位于 ``rag.evaluation.retrieval_metrics``。本模块只保留历史导入
路径，避免离线评测复制另一套 hit@k、MRR 或关键词命中逻辑，从而造成线上与离线
口径漂移。
"""

from rag.evaluation.retrieval_metrics import (
    compute_context_keyword_hit,
    compute_hit_at_k,
    compute_mrr,
    evaluate_retrieval_results_v2,
)

# 限定兼容模块的公开 API，调用方不会依赖未声明的内部导入。
__all__ = [
    "compute_context_keyword_hit",
    "compute_hit_at_k",
    "compute_mrr",
    "evaluate_retrieval_results_v2",
]
