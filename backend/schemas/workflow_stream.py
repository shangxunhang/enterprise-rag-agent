"""Stable application-owned workflow event protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from schemas.common import ErrorSchema, SchemaBase
from schemas.status import ExecutionStatus


WorkflowEventType = Literal[
    "workflow_started",
    "workflow_resumed",
    "node_started",
    "node_completed",
    "node_failed",
    "retrieval_started",
    "retrieval_completed",
    "section_started",
    "section_generated",
    "section_repaired",
    "section_completed",
    "section_cancelled",
    "checkpoint_created",
    "human_review_required",
    "human_review_resumed",
    "artifact_persisted",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
]


class WorkflowStreamEventSchema(SchemaBase):
    """Versioned event boundary; raw LangGraph events never escape the engine."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "workflow_stream_event_v1"
    event_id: str
    sequence: int = Field(ge=1)
    stream_session_id: str = Field(min_length=1)
    resume_count: int = Field(default=0, ge=0)
    stream_kind: Literal["initial", "resume", "cancel"]
    timestamp: str
    task_id: str
    run_id: str
    workflow_id: str
    workflow_version: str
    node_name: str | None = None
    section_id: str | None = None
    event_type: WorkflowEventType
    status: ExecutionStatus
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)
    error: ErrorSchema | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
