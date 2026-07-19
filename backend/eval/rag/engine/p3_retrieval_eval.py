# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Compatibility exports for canonical retrieval metrics.

Runtime RAG owns the metric implementation; offline evaluation imports it from
here only for backward compatibility.
"""

from rag.evaluation.retrieval_metrics import (
    compute_context_keyword_hit,
    compute_hit_at_k,
    compute_mrr,
    evaluate_retrieval_results_v2,
)

__all__ = [
    "compute_context_keyword_hit",
    "compute_hit_at_k",
    "compute_mrr",
    "evaluate_retrieval_results_v2",
]
