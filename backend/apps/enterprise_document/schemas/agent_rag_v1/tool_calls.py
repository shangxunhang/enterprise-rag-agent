"""受控 Tool Gateway 调用与底层 Handler 输出协议。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from ._base import ContractSchema
from .enums import AgentRole, ToolResultStatus


class ToolInvocationSchema(ContractSchema):
    schema_version: str = "tool_invocation_v1"
    call_id: str
    agent_role: AgentRole
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    task_id: str
    run_id: str
    section_id: Optional[str] = None
    execution_id: str
    attempt_id: str
    input_snapshot_version: str


class ToolHandlerOutputSchema(ContractSchema):
    schema_version: str = "tool_handler_output_v1"
    status: ToolResultStatus = ToolResultStatus.SUCCESS
    data_type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    source_refs: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    raw_result: Optional[Any] = None
    error_code: Optional[str] = None
