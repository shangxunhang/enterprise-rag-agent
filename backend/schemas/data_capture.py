# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：DataCaptureRecordSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Data capture schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal
from pydantic import ConfigDict, Field, model_validator

from .common import SchemaBase


# 阅读注释（类）：封装 数据 capture 记录 Schema，定义跨模块传递的数据结构与字段约束。
class DataCaptureRecordSchema(SchemaBase):
    """A reusable data capture record.

    This record is used to save data that may later become:
    - SFT sample
    - DPO preference sample
    - evaluation sample
    - human review sample
    - rejected sample
    """

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: Literal["data_capture_record_v2"] = "data_capture_record_v2"

    record_id: str
    lifecycle_revision: int = Field(default=0, ge=0)

    # Classification
    capture_type: str
    capture_stage: Literal[
        "raw",
        "candidate",
        "evaluated",
        "accepted",
        "rejected",
        "runtime_rejected",
        "training_ready",
        "eval_ready",
    ] = "candidate"

    # Link to runtime
    task_id: str
    run_id: str
    trace_id: Optional[str] = None

    # Source information
    source_component_type: Optional[str] = None
    source_component_name: Optional[str] = None
    source_event_type: Optional[str] = None

    # Main business data
    user_input: Optional[str] = None
    rewritten_query: Optional[str] = None

    rag_context: Dict[str, Any] = Field(default_factory=dict)
    structured_facts: Dict[str, Any] = Field(default_factory=dict)

    prompt: Optional[str] = None
    model_output: Optional[str] = None
    generated_draft_output: Optional[str] = None
    deliverable_output: Optional[str] = None

    # Human feedback / label
    human_feedback: Dict[str, Any] = Field(default_factory=dict)
    label: Dict[str, Any] = Field(default_factory=dict)

    # Quality control
    quality_score: Optional[float] = None
    quality_flags: List[str] = Field(default_factory=list)
    need_human_review: bool = True
    is_usable_for_training: bool = False
    is_usable_for_eval: bool = False

    # Related artifacts
    related_trace_event_ids: List[str] = Field(default_factory=list)
    related_file_ids: List[str] = Field(default_factory=list)
    lineage_refs: Dict[str, Any] = Field(default_factory=dict)

    # Time
    created_at: str

    # Extension fields
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)

    # RAG explicit data
    retrieved_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    rag_trace: Dict[str, Any] = Field(default_factory=dict)

    # Prompt / model explicit data
    prompt_info: Dict[str, Any] = Field(default_factory=dict)
    model_info: Dict[str, Any] = Field(default_factory=dict)

    # Evaluation-oriented sample
    eval_sample: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_readiness_stage(self) -> "DataCaptureRecordSchema":
        if self.is_usable_for_training != (self.capture_stage == "training_ready"):
            raise ValueError(
                "capture_stage='training_ready' iff is_usable_for_training=True"
            )
        if self.is_usable_for_eval != (self.capture_stage == "eval_ready"):
            raise ValueError("capture_stage='eval_ready' iff is_usable_for_eval=True")
        if self.capture_stage == "runtime_rejected" and (
            self.is_usable_for_training or self.is_usable_for_eval
        ):
            raise ValueError("runtime_rejected capture cannot be readiness-eligible")
        return self
