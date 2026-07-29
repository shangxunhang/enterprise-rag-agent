"""四个业务 Agent 共享的无状态调用外壳。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import Field, field_validator, model_validator

from ._base import ContractSchema
from .enums import AgentInvocationStatus, AgentPayloadType, AgentRole
from .errors import WorkflowErrorSchema
from .patches import StatePatchRequestSchema
from .payloads import (
    PlannerResultPayloadSchema,
    ResearchResultPayloadSchema,
    VerifierResultPayloadSchema,
    WriterResultPayloadSchema,
)

AgentPayloadSchema = Union[
    PlannerResultPayloadSchema,
    ResearchResultPayloadSchema,
    WriterResultPayloadSchema,
    VerifierResultPayloadSchema,
]


class AgentExecutionContextSchema(ContractSchema):
    """Agent 仅接收必要引用、权限、预算和版本，不接收整个 Shared State。"""

    schema_version: str = "agent_execution_context_v1"
    invocation_id: str
    logical_operation_id: str
    task_id: str
    run_id: str
    section_id: Optional[str] = None
    agent_role: AgentRole
    execution_mode: str
    execution_id: str
    attempt_id: str
    input_snapshot_version: str
    expected_domain_versions: Dict[str, int] = Field(default_factory=dict)
    task_context_ref: str
    source_snapshot_ref: str
    execution_plan_ref: Optional[str] = None
    section_state_ref: Optional[str] = None
    authorized_artifact_refs: List[str] = Field(default_factory=list)
    authorized_tool_names: List[str] = Field(default_factory=list)
    writable_state_paths: List[str] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    trigger: Dict[str, Any] = Field(default_factory=dict)
    trace_context: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_domain_versions")
    @classmethod
    def _non_negative_versions(cls, value: Dict[str, int]) -> Dict[str, int]:
        if any(version < 0 for version in value.values()):
            raise ValueError("expected domain versions cannot be negative")
        return value

    @model_validator(mode="after")
    def _validate_section_context(self) -> "AgentExecutionContextSchema":
        if self.section_state_ref and not self.section_id:
            raise ValueError("section_state_ref requires section_id")
        return self


class AgentExecutionResultSchema(ContractSchema):
    """统一执行外壳；业务结果必须由与 payload_type 对应的强类型模型承载。"""

    schema_version: str = "agent_execution_result_v1"
    invocation_id: str
    agent_role: AgentRole
    execution_mode: str
    execution_id: str
    attempt_id: str
    input_snapshot_version: str
    status: AgentInvocationStatus
    payload_type: AgentPayloadType
    payload: Optional[AgentPayloadSchema] = None
    proposed_state_patches: List[StatePatchRequestSchema] = Field(
        default_factory=list
    )
    emitted_signals: List[str] = Field(default_factory=list)
    artifact_refs: List[str] = Field(default_factory=list)
    tool_call_refs: List[str] = Field(default_factory=list)
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[WorkflowErrorSchema] = None
    trace_ref: str

    @model_validator(mode="after")
    def _validate_payload_and_error(self) -> "AgentExecutionResultSchema":
        expected_payloads = {
            AgentPayloadType.PLANNER_RESULT: PlannerResultPayloadSchema,
            AgentPayloadType.RESEARCH_RESULT: ResearchResultPayloadSchema,
            AgentPayloadType.WRITER_RESULT: WriterResultPayloadSchema,
            AgentPayloadType.VERIFIER_RESULT: VerifierResultPayloadSchema,
        }
        expected = expected_payloads[self.payload_type]
        if self.status != AgentInvocationStatus.FAILED and not isinstance(
            self.payload,
            expected,
        ):
            raise ValueError(
                f"{self.payload_type.value} requires {expected.__name__}"
            )
        if self.status == AgentInvocationStatus.FAILED and self.error is None:
            raise ValueError("failed agent result requires WorkflowError")
        if self.status == AgentInvocationStatus.COMPLETED and self.error is not None:
            raise ValueError("completed agent result cannot carry WorkflowError")
        return self
