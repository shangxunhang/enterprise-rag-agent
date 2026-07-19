# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：RunTraceEventSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run trace schemas.

This scheme is designed as a stable v1 envelope for workflow / agent / tool / model tracing.
Future extensions should mainly go into input_payload, output_payload, metadata, metrics, tags, or extra.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import Field

from .common import ErrorSchema, SchemaBase


# 阅读注释（类）：封装 run Trace 事件 Schema，定义跨模块传递的数据结构与字段约束。
class RunTraceEventSchema(SchemaBase):
    """One trace event in a task run.

    A complete task run contains many events.
    All events from the same run should share the same trace_id.
    Each event has its own event_id.
    """

    schema_version: str = "run_trace_event_v2"

    # Event identity
    event_id: str
    event_sequence: int = 0
    trace_id: str
    run_id: str
    task_id: str

    # Optional hierarchy information
    parent_event_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    span_name: Optional[str] = None
    span_kind: str = "internal"
    phase: str = "event"  # start | end | error | event

    # Event type
    event_type: str
    event_name: Optional[str] = None

    # Component information
    component_type: str
    component_name: str
    component_version: Optional[str] = None

    # Workflow position
    workflow_id: Optional[str] = None
    workflow_version: Optional[str] = None
    step_id: Optional[str] = None
    step_name: Optional[str] = None
    step_order: Optional[int] = None

    # Call identity
    call_id: Optional[str] = None
    caller: Optional[str] = None
    callee: Optional[str] = None

    # Runtime status
    status: Optional[str] = None
    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None

    # Main data capture fields
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    input_summary: Dict[str, Any] = Field(default_factory=dict)
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    lineage: Dict[str, Any] = Field(default_factory=dict)

    # Compatible generic payload field
    payload: Dict[str, Any] = Field(default_factory=dict)

    # Metrics
    latency_ms: Optional[int] = None
    token_usage: Dict[str, Any] = Field(default_factory=dict)
    cost: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)

    # Time
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str

    # Trace context
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    request_id: Optional[str] = None

    # Search / model / tool related optional fields
    model_name: Optional[str] = None
    tool_name: Optional[str] = None
    agent_name: Optional[str] = None

    # Debug and filtering
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Reserved extension field
    extra: Dict[str, Any] = Field(default_factory=dict)