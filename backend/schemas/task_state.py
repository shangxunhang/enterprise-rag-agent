"""Event-sourced task lifecycle schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from .common import ErrorSchema, SchemaBase
from .status import ExecutionStatus


class TaskStateSnapshotSchema(SchemaBase):
    """Complete current-state projection carried by every canonical event."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: str = "task_state_snapshot_v3"
    task_id: str
    run_id: str
    task_type: str
    tenant_id: str = "default"
    task_name: Optional[str] = None
    project_name: Optional[str] = None
    user_input: Optional[str] = None
    canonical_task_request_sha256: Optional[str] = None
    status: ExecutionStatus
    current_step: Optional[str] = None
    revision: int = Field(ge=0)
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_id: Optional[str] = None
    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskStateEventSchema(SchemaBase):
    """One canonical append-only task transition."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: str = "task_state_event_v3"
    event_id: str
    task_id: str
    run_id: str
    task_type: str
    revision: int = Field(ge=0)
    event_type: str
    previous_status: Optional[ExecutionStatus] = None
    current_status: ExecutionStatus
    current_step: Optional[str] = None
    result_id: Optional[str] = None
    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None
    created_at: str
    snapshot: TaskStateSnapshotSchema
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskStateRecordSchema(SchemaBase):
    """In-memory projection rebuilt exclusively from canonical events."""

    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    schema_version: str = "task_state_record_v3"
    task_id: str
    run_id: str
    task_type: str
    tenant_id: str = "default"
    task_name: Optional[str] = None
    project_name: Optional[str] = None
    user_input: Optional[str] = None
    canonical_task_request_sha256: Optional[str] = None
    status: ExecutionStatus
    current_step: Optional[str] = None
    revision: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_id: Optional[str] = None
    error: Optional[ErrorSchema] = None
    error_message: Optional[str] = None
    events: List[TaskStateEventSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


class TaskAuthorizationIdentitySchema(SchemaBase):
    """Immutable identity projection safe to read before tenant authorization."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        arbitrary_types_allowed=True,
    )

    schema_version: Literal["task_authorization_identity_v1"] = (
        "task_authorization_identity_v1"
    )
    task_id: str
    run_id: str
    task_type: str
    tenant_id: str
    canonical_task_request_sha256: str


# Transitional import alias.  It intentionally points at the immutable
# identity-only schema and therefore cannot expose status, revision or content.
TaskAuthorizationMetadataSchema = TaskAuthorizationIdentitySchema
