# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：RAGEvalMetricSchema、RAGEvalResultSchema、RAGEvalReportSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Schemas for RAGAS-style lightweight RAG evaluation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import Field

from schemas.common import SchemaBase


# 阅读注释（类）：封装 rageval 指标 Schema，定义跨模块传递的数据结构与字段约束。
class RAGEvalMetricSchema(SchemaBase):
    """封装 rageval 指标 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_eval_metric_v1"
    name: str
    score: float = 0.0
    reason: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 rageval 结果 Schema，定义跨模块传递的数据结构与字段约束。
class RAGEvalResultSchema(SchemaBase):
    """封装 rageval 结果 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_eval_result_v1"

    sample_id: str
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    capture_type: Optional[str] = None

    query: str = ""
    answer: str = ""

    context_precision: RAGEvalMetricSchema
    context_recall_proxy: RAGEvalMetricSchema
    faithfulness_proxy: RAGEvalMetricSchema
    answer_relevance_proxy: RAGEvalMetricSchema
    citation_coverage: RAGEvalMetricSchema
    completeness_proxy: RAGEvalMetricSchema

    overall_score: float = 0.0
    need_human_review: bool = True
    quality_flags: List[str] = Field(default_factory=list)

    retrieved_chunk_num: int = 0
    citation_num: int = 0
    context_chars: int = 0
    answer_chars: int = 0

    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 rageval report Schema，定义跨模块传递的数据结构与字段约束。
class RAGEvalReportSchema(SchemaBase):
    """封装 rageval report Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "rag_eval_report_v1"

    report_id: str
    run_id: Optional[str] = None
    source_path: str
    created_at: str

    total: int = 0
    average_overall_score: float = 0.0
    average_context_precision: float = 0.0
    average_context_recall_proxy: float = 0.0
    average_faithfulness_proxy: float = 0.0
    average_answer_relevance_proxy: float = 0.0
    average_citation_coverage: float = 0.0
    average_completeness_proxy: float = 0.0

    results: List[RAGEvalResultSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
