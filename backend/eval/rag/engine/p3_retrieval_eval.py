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
