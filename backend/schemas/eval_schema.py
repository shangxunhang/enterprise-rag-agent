# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：EvalExpectedSchema、EvalSampleSchema、EvalMetricsSchema、EvalResultSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Evaluation sample/result schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import ErrorSchema, SchemaBase, WarningSchema


# 阅读注释（类）：封装 评测 expected Schema，定义跨模块传递的数据结构与字段约束。
class EvalExpectedSchema(SchemaBase):
    """封装 评测 expected Schema，定义跨模块传递的数据结构与字段约束。"""
    required_sections: List[str] = Field(default_factory=list)
    expected_doc_ids: List[str] = Field(default_factory=list)
    expected_keywords: List[str] = Field(default_factory=list)
    expected_citation_required: bool = True
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 评测 sample Schema，定义跨模块传递的数据结构与字段约束。
class EvalSampleSchema(SchemaBase):
    """封装 评测 sample Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "eval_sample_v1"

    sample_id: str
    task_type: str

    user_input: str
    project_input: Dict[str, Any] = Field(default_factory=dict)

    file_ids: List[str] = Field(default_factory=list)
    kb_ids: List[str] = Field(default_factory=list)
    template_id: Optional[str] = None

    expected: EvalExpectedSchema = Field(default_factory=EvalExpectedSchema)

    eval_set_id: str
    eval_set_version: str

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 评测 指标 Schema，定义跨模块传递的数据结构与字段约束。
class EvalMetricsSchema(SchemaBase):
    """封装 评测 指标 Schema，定义跨模块传递的数据结构与字段约束。"""
    success: bool
    has_required_sections: Optional[bool] = None
    has_citations: Optional[bool] = None
    hit_at_k: Optional[float] = None
    mrr: Optional[float] = None
    context_keyword_hit: Optional[float] = None
    latency_ms: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 评测 结果 Schema，定义跨模块传递的数据结构与字段约束。
class EvalResultSchema(SchemaBase):
    """封装 评测 结果 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "eval_result_v1"

    eval_result_id: str
    sample_id: str

    task_id: str
    run_id: str

    task_type: str

    metrics: EvalMetricsSchema
    score: Optional[float] = None

    error: Optional[ErrorSchema] = None
    warnings: List[WarningSchema] = Field(default_factory=list)

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)
