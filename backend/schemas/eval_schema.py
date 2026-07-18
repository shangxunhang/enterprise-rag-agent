"""Evaluation sample/result schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import ErrorSchema, SchemaBase, WarningSchema


class EvalExpectedSchema(SchemaBase):
    required_sections: List[str] = Field(default_factory=list)
    expected_doc_ids: List[str] = Field(default_factory=list)
    expected_keywords: List[str] = Field(default_factory=list)
    expected_citation_required: bool = True
    extra: Dict[str, Any] = Field(default_factory=dict)


class EvalSampleSchema(SchemaBase):
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


class EvalMetricsSchema(SchemaBase):
    success: bool
    has_required_sections: Optional[bool] = None
    has_citations: Optional[bool] = None
    hit_at_k: Optional[float] = None
    mrr: Optional[float] = None
    context_keyword_hit: Optional[float] = None
    latency_ms: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class EvalResultSchema(SchemaBase):
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
