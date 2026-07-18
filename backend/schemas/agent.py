"""Agent protocol schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional



from .citation import CitationSchema
from .common import ErrorSchema, SchemaBase, WarningSchema
from .status import ExecutionStatus

from typing import List
from pydantic import Field


class AgentSpecSchema(SchemaBase):
    schema_version: str = "agent_spec_v1"

    agent_name: str
    agent_type: str  # supervisor | sub_agent

    description: Optional[str] = None

    capabilities: List[str] = Field(default_factory=list)
    supported_task_types: List[str] = Field(default_factory=list)

    required_tools: List[str] = Field(default_factory=list)
    optional_tools: List[str] = Field(default_factory=list)

    input_schema: str
    output_schema: str

    state_read_keys: List[str] = Field(default_factory=list)
    state_write_keys: List[str] = Field(default_factory=list)

    max_retries: int = 0
    timeout_seconds: int = 300

    is_active: bool = True

    extra: Dict[str, Any] = Field(default_factory=dict)


class AgentMessageSchema(SchemaBase):
    schema_version: str = "agent_message_v1"

    message_id: str
    task_id: str
    run_id: str

    from_agent: str
    to_agent: str

    message_type: str  # task_dispatch | status_update | result_notice | error_notice | control

    step_id: Optional[str] = None
    step_name: Optional[str] = None

    payload: Dict[str, Any] = Field(default_factory=dict)

    created_at: str

    extra: Dict[str, Any] = Field(default_factory=dict)


class AgentResultSchema(SchemaBase):
    schema_version: str = "agent_result_v1"

    result_id: str
    task_id: str
    run_id: str

    agent_name: str
    agent_type: str  # supervisor | sub_agent

    status: ExecutionStatus
    result_type: str

    result: Dict[str, Any] = Field(default_factory=dict)

    citations: List[CitationSchema] = Field(default_factory=list)
    warnings: List[WarningSchema] = Field(default_factory=list)

    need_human_review: bool = True

    error: Optional[ErrorSchema] = None

    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
