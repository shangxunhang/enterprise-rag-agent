"""Agent-RAG V1 的分域 SectionState 与 SharedState 协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from ._base import ContractSchema
from .enums import (
    SectionStatus,
    SectionStrategy,
    TaskStatus,
    VerifierAction,
    VerifierExecutionMode,
)


class SectionDomainVersionsSchema(ContractSchema):
    research: int = Field(default=0, ge=0)
    writing: int = Field(default=0, ge=0)
    claims: int = Field(default=0, ge=0)
    verification: int = Field(default=0, ge=0)
    execution: int = Field(default=0, ge=0)


class SectionExecutionStateSchema(ContractSchema):
    status: SectionStatus = SectionStatus.PENDING
    execution_id: Optional[str] = None
    active_attempt_id: Optional[str] = None
    active_plan_version: Optional[int] = Field(default=None, ge=1)
    input_snapshot_version: str
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    hard_dependencies: List[str] = Field(default_factory=list)
    consistency_references: List[str] = Field(default_factory=list)
    generation_cursor: Optional[str] = None
    last_error_ref: Optional[str] = None


class SectionResearchStateSchema(ContractSchema):
    evidence_package_ref: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    evidence_gap_count: int = Field(default=0, ge=0)
    research_rounds: int = Field(default=0, ge=0)
    tool_call_refs: List[str] = Field(default_factory=list)


class SectionWritingStateSchema(ContractSchema):
    draft_ref: Optional[str] = None
    draft_version: int = Field(default=0, ge=0)
    paragraph_refs: Dict[str, str] = Field(default_factory=dict)
    paragraph_versions: Dict[str, int] = Field(default_factory=dict)
    expected_paragraph_ids: List[str] = Field(default_factory=list)
    generation_complete: bool = False
    change_manifest_ref: Optional[str] = None


class SectionClaimsStateSchema(ContractSchema):
    claim_ids: List[str] = Field(default_factory=list)
    active_claim_ids: List[str] = Field(default_factory=list)
    claim_set_version: int = Field(default=0, ge=0)
    extraction_ref: Optional[str] = None


class SectionVerificationStateSchema(ContractSchema):
    execution_mode: Optional[VerifierExecutionMode] = None
    recommended_action: Optional[VerifierAction] = None
    verification_ref: Optional[str] = None
    issue_ids: List[str] = Field(default_factory=list)
    accepted_paragraph_ids: List[str] = Field(default_factory=list)
    rejected_paragraph_ids: List[str] = Field(default_factory=list)
    verification_rounds: int = Field(default=0, ge=0)


class SectionStateSchema(ContractSchema):
    """单章节的五个独立并发域；禁止由 Agent 整体覆盖。"""

    schema_version: str = "section_state_v1"
    section_id: str
    section_title: str
    section_order: int = Field(ge=0)
    applicable: bool = True
    strategy: SectionStrategy
    execution_group: Optional[str] = None
    can_run_in_parallel: bool = False
    requires_global_context: bool = True
    execution: SectionExecutionStateSchema
    research: SectionResearchStateSchema = Field(
        default_factory=SectionResearchStateSchema
    )
    writing: SectionWritingStateSchema = Field(
        default_factory=SectionWritingStateSchema
    )
    claims: SectionClaimsStateSchema = Field(
        default_factory=SectionClaimsStateSchema
    )
    verification: SectionVerificationStateSchema = Field(
        default_factory=SectionVerificationStateSchema
    )

    @model_validator(mode="after")
    def _validate_strategy_status(self) -> "SectionStateSchema":
        if self.strategy == SectionStrategy.SKIP and self.execution.status not in {
            SectionStatus.PENDING,
            SectionStatus.PLANNED,
            SectionStatus.SKIPPED,
        }:
            raise ValueError("skip strategy cannot enter an executable status")
        return self


class StateMetaSchema(ContractSchema):
    schema_version: str = "multi_agent_shared_state_v1"
    task_id: str
    run_id: str
    input_snapshot_version: str
    created_at: datetime
    updated_at: datetime
    event_sequence: int = Field(default=0, ge=0)


class TaskContextStateSchema(ContractSchema):
    task_status: TaskStatus = TaskStatus.PENDING
    project_name: Optional[str] = None
    template_version: Optional[str] = None
    requirements_ref: Optional[str] = None
    task_context_ref: str


class SourceSnapshotStateSchema(ContractSchema):
    source_snapshot_ref: str
    fact_snapshot_ref: str
    template_schema_ref: str
    source_inventory_ref: str
    source_versions: Dict[str, str] = Field(default_factory=dict)


class ExecutionPlanStateSchema(ContractSchema):
    plan_ref: Optional[str] = None
    plan_version: int = Field(default=0, ge=0)
    active_plan_revision_id: Optional[str] = None
    section_order: List[str] = Field(default_factory=list)
    version: int = Field(default=0, ge=0)


class DocumentValidationStateSchema(ContractSchema):
    verification_ref: Optional[str] = None
    conflict_repair_plan_ref: Optional[str] = None
    validation_version: int = Field(default=0, ge=0)
    status: str = "pending"
    issue_ids: List[str] = Field(default_factory=list)


class ArtifactStateSchema(ContractSchema):
    assembly_ref: Optional[str] = None
    assembly_validation_ref: Optional[str] = None
    final_artifact_ref: Optional[str] = None
    generation_report_ref: Optional[str] = None
    document_version: Optional[str] = None
    version: int = Field(default=0, ge=0)


class RuntimeControlStateSchema(ContractSchema):
    draining: bool = False
    cancelled: bool = False
    active_execution_ids: List[str] = Field(default_factory=list)
    budget_reserved: Dict[str, int] = Field(default_factory=dict)
    budget_settled: Dict[str, int] = Field(default_factory=dict)
    checkpoint_ref: Optional[str] = None
    version: int = Field(default=0, ge=0)


class HumanReviewStateSchema(ContractSchema):
    review_id: str
    scope: str
    section_id: Optional[str] = None
    status: str
    request_ref: str
    decision_ref: Optional[str] = None


class FinalResultStateSchema(ContractSchema):
    task_status: TaskStatus = TaskStatus.PENDING
    document_version: Optional[str] = None
    document_artifact_ref: Optional[str] = None
    generation_report_ref: Optional[str] = None
    published: bool = False


class DomainVersionsSchema(ContractSchema):
    task_context: int = Field(default=0, ge=0)
    execution_plan: int = Field(default=0, ge=0)
    document_validation: int = Field(default=0, ge=0)
    artifact_state: int = Field(default=0, ge=0)
    runtime_control: int = Field(default=0, ge=0)
    final_result: int = Field(default=0, ge=0)
    sections: Dict[str, SectionDomainVersionsSchema] = Field(default_factory=dict)


class MultiAgentSharedStateSchema(ContractSchema):
    """规格定义的新状态树；Phase 2 通过 StateGateway 与现有状态桥接。"""

    schema_version: str = "multi_agent_shared_state_v1"
    state_meta: StateMetaSchema
    task_context: TaskContextStateSchema
    source_snapshot: SourceSnapshotStateSchema
    execution_plan: ExecutionPlanStateSchema = Field(
        default_factory=ExecutionPlanStateSchema
    )
    section_states: Dict[str, SectionStateSchema] = Field(default_factory=dict)
    document_validation: DocumentValidationStateSchema = Field(
        default_factory=DocumentValidationStateSchema
    )
    artifact_state: ArtifactStateSchema = Field(default_factory=ArtifactStateSchema)
    runtime_control: RuntimeControlStateSchema = Field(
        default_factory=RuntimeControlStateSchema
    )
    human_reviews: Dict[str, HumanReviewStateSchema] = Field(default_factory=dict)
    final_result: FinalResultStateSchema = Field(default_factory=FinalResultStateSchema)
    domain_versions: DomainVersionsSchema = Field(default_factory=DomainVersionsSchema)

    @field_validator("section_states")
    @classmethod
    def _section_keys_match(
        cls,
        value: Dict[str, SectionStateSchema],
    ) -> Dict[str, SectionStateSchema]:
        mismatched = [
            key for key, section in value.items() if key != section.section_id
        ]
        if mismatched:
            raise ValueError(
                "section state keys must match section_id: "
                + ", ".join(sorted(mismatched))
            )
        return value

    @model_validator(mode="after")
    def _validate_identity_and_versions(self) -> "MultiAgentSharedStateSchema":
        if self.state_meta.task_id == "" or self.state_meta.run_id == "":
            raise ValueError("shared state identity cannot be empty")
        section_ids = set(self.section_states)
        if set(self.domain_versions.sections) - section_ids:
            raise ValueError(
                "domain versions cannot reference unknown section states"
            )
        return self
