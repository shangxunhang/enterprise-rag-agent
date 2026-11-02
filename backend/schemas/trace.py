"""定义单次任务运行中的统一 Trace v2 事件外壳。

工作流、Agent、工具和模型事件共享同一结构，通过 trace/span 标识形成层级调用链。
Trace 只负责 observation/correlation；业务 lifecycle、currentness、idempotency、Gate、
Budget、Artifact/Evidence truth 和 retry authorization 始终由各自 canonical authority
拥有。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from .common import ErrorSchema, SchemaBase
from .failure import FailureOrigin


class TraceRefSchema(SchemaBase):
    """可解析 Trace span 的结构化引用，不替代 Run/Attempt/Invocation identity。"""

    schema_version: str = "trace_ref_v1"
    trace_id: str
    run_id: str
    span_id: str
    anchor_event_id: Optional[str] = None

    @field_validator("trace_id", "run_id", "span_id", "anchor_event_id", mode="before")
    @classmethod
    def _non_empty_ref_text(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("TraceRef identity fields cannot be empty")
        return normalized


class TraceCorrelationSchema(SchemaBase):
    """Trace v2 的唯一 typed correlation block。

    字段只承载同名 identity/ref。它们用于把物理执行观察连接到外部 authority，
    不能反向决定 lifecycle/currentness/idempotency/authorization/canonical truth。
    call_id、metadata、payload 等旧位置只保留兼容展示语义。
    """

    schema_version: str = "trace_correlation_v1"

    attempt_scope_ref: Optional[str] = None
    attempt_id: Optional[str] = None
    section_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    research_round_id: Optional[str] = None
    agent_invocation_id: Optional[str] = None
    execution_id: Optional[str] = None
    logical_operation_id: Optional[str] = None
    commit_operation_id: Optional[str] = None

    model_call_id: Optional[str] = None
    provider_attempt_id: Optional[str] = None
    provider_attempt_ordinal: Optional[int] = Field(default=None, ge=1)

    tool_operation_id: Optional[str] = None
    tool_attempt_id: Optional[str] = None
    tool_attempt_ordinal: Optional[int] = Field(default=None, ge=1)

    reservation_id: Optional[str] = None
    settlement_id: Optional[str] = None
    budget_revision: Optional[int] = Field(default=None, ge=0)

    prewrite_route_decision_ref: Optional[Dict[str, Any]] = None
    retry_successor_operation_ref: Optional[str] = None
    predecessor_attempt_ref: Optional[str] = None
    successor_attempt_ref: Optional[str] = None
    recovery_ref: Optional[str] = None

    retrieval_trace_id: Optional[str] = None
    external_trace_refs: List[str] = Field(default_factory=list)

    input_refs: List[str] = Field(default_factory=list)
    output_refs: List[str] = Field(default_factory=list)
    source_refs: List[str] = Field(default_factory=list)
    decision_refs: List[str] = Field(default_factory=list)
    artifact_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)

    @field_validator(
        "attempt_scope_ref",
        "attempt_id",
        "section_id",
        "paragraph_id",
        "research_round_id",
        "agent_invocation_id",
        "execution_id",
        "logical_operation_id",
        "commit_operation_id",
        "model_call_id",
        "provider_attempt_id",
        "tool_operation_id",
        "tool_attempt_id",
        "reservation_id",
        "settlement_id",
        "retry_successor_operation_ref",
        "predecessor_attempt_ref",
        "successor_attempt_ref",
        "recovery_ref",
        "retrieval_trace_id",
        mode="before",
    )
    @classmethod
    def _optional_identity_is_non_empty(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("typed Trace correlation identities cannot be empty")
        return normalized

    @field_validator(
        "external_trace_refs",
        "input_refs",
        "output_refs",
        "source_refs",
        "decision_refs",
        "artifact_refs",
        "evidence_refs",
        mode="before",
    )
    @classmethod
    def _normalize_ref_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, bytes, bytearray)):
            raise TypeError("Trace reference groups must be lists, not scalar strings")
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise ValueError("Trace reference groups cannot contain empty refs")
            if text not in normalized:
                normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def _physical_attempts_have_logical_owners(self) -> "TraceCorrelationSchema":
        if self.attempt_scope_ref is not None and self.attempt_id is None:
            raise ValueError("attempt_scope_ref requires attempt_id")

        provider_id = self.provider_attempt_id is not None
        provider_ordinal = self.provider_attempt_ordinal is not None
        if provider_id != provider_ordinal:
            raise ValueError(
                "provider_attempt_id and provider_attempt_ordinal must be supplied together"
            )
        if provider_id and self.model_call_id is None:
            raise ValueError("physical provider attempt requires model_call_id")

        tool_id = self.tool_attempt_id is not None
        tool_ordinal = self.tool_attempt_ordinal is not None
        if tool_id != tool_ordinal:
            raise ValueError(
                "tool_attempt_id and tool_attempt_ordinal must be supplied together"
            )
        if tool_id and self.tool_operation_id is None:
            raise ValueError("physical tool attempt requires tool_operation_id")

        retry_refs = (
            self.retry_successor_operation_ref,
            self.predecessor_attempt_ref,
            self.successor_attempt_ref,
        )
        if any(item is not None for item in retry_refs) and not all(
            item is not None for item in retry_refs
        ):
            raise ValueError(
                "retry correlation requires operation + predecessor + successor Attempt refs"
            )
        return self


class RunTraceEventSchema(SchemaBase):
    """表示一次任务运行中的一个结构化事件或 span 生命周期记录。"""

    schema_version: str = "run_trace_event_v2"

    event_id: str
    event_sequence: int = 0
    trace_id: str
    run_id: str
    task_id: str

    parent_event_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    span_name: Optional[str] = None
    span_kind: str = "internal"
    phase: str = "event"

    event_type: str
    event_name: Optional[str] = None

    component_type: str
    component_name: str
    component_version: Optional[str] = None

    workflow_id: Optional[str] = None
    workflow_version: Optional[str] = None
    step_id: Optional[str] = None
    step_name: Optional[str] = None
    step_order: Optional[int] = None

    correlation: TraceCorrelationSchema = Field(default_factory=TraceCorrelationSchema)

    call_id: Optional[str] = None
    caller: Optional[str] = None
    callee: Optional[str] = None

    status: Optional[str] = None
    error: Optional[ErrorSchema] = None
    failure_origin: FailureOrigin = FailureOrigin.UNKNOWN
    error_message: Optional[str] = None

    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    input_summary: Dict[str, Any] = Field(default_factory=dict)
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    lineage: Dict[str, Any] = Field(default_factory=dict)

    payload: Dict[str, Any] = Field(default_factory=dict)

    latency_ms: Optional[int] = None
    token_usage: Dict[str, Any] = Field(default_factory=dict)
    cost: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)

    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str

    session_id: Optional[str] = None
    user_id: Optional[str] = None
    request_id: Optional[str] = None

    model_name: Optional[str] = None
    tool_name: Optional[str] = None
    agent_name: Optional[str] = None

    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _span_identity_is_physical_only(self) -> "RunTraceEventSchema":
        if self.span_id is not None and self.parent_span_id == self.span_id:
            raise ValueError("Trace span cannot be its own parent")
        return self


__all__ = [
    "RunTraceEventSchema",
    "TraceCorrelationSchema",
    "TraceRefSchema",
]
