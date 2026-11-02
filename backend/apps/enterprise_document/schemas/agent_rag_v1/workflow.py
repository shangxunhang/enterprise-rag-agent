"""Phase 3 Router、调度、恢复、影响分析与版本策略协议。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from ._base import ContractSchema
from .enums import (
    ResumeStage,
    ResumeTriggerKind,
    RouterAction,
    RouterSignalKind,
    SectionImpactAction,
    SectionStatus,
)


class RouterSignalSchema(ContractSchema):
    signal_id: str
    kind: RouterSignalKind
    section_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class RouterDecisionSchema(ContractSchema):
    schema_version: str = "router_decision_v1"
    action: RouterAction
    selected_signal_id: str
    priority: int = Field(ge=1, le=11)
    section_id: Optional[str] = None
    discarded_signal_ids: List[str] = Field(default_factory=list)
    reason: str


class ScheduledSectionBatchSchema(ContractSchema):
    schema_version: str = "scheduled_section_batch_v1"
    section_ids: List[str] = Field(default_factory=list)
    blocked_section_ids: List[str] = Field(default_factory=list)
    max_parallel_sections: int = Field(ge=1)


class SectionGroupResultSchema(ContractSchema):
    schema_version: str = "section_group_result_v1"
    group_id: str
    status: SectionStatus
    applicable_section_ids: List[str] = Field(default_factory=list)
    deliverable_section_ids: List[str] = Field(default_factory=list)
    unavailable_section_ids: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)


class ResumeRequestSchema(ContractSchema):
    task_id: str
    section_ids: List[str] = Field(min_length=1)
    trigger_kind: ResumeTriggerKind
    trigger_ref: Optional[str] = None


class ResumeDecisionSchema(ContractSchema):
    schema_version: str = "resume_decision_v1"
    stage: ResumeStage
    affected_section_ids: List[str] = Field(min_length=1)
    invalidated_domains: List[str] = Field(default_factory=list)
    preserved_domains: List[str] = Field(default_factory=list)
    reason_code: str


class SectionBindingSnapshotSchema(ContractSchema):
    section_id: str
    fact_refs: List[str] = Field(default_factory=list)
    claim_refs: List[str] = Field(default_factory=list)
    evidence_topics: List[str] = Field(default_factory=list)
    hard_dependencies: List[str] = Field(default_factory=list)
    consistency_references: List[str] = Field(default_factory=list)


class ChangeSetSchema(ContractSchema):
    changed_fact_refs: List[str] = Field(default_factory=list)
    changed_claim_refs: List[str] = Field(default_factory=list)
    changed_evidence_topics: List[str] = Field(default_factory=list)
    excel_scope_changed: bool = False
    device_categories_changed: bool = False
    core_model_or_quantity_changed: bool = False
    template_structure_changed: bool = False
    project_boundary_changed: bool = False
    broad_replan: bool = False


class SectionImpactSchema(ContractSchema):
    section_id: str
    action: SectionImpactAction
    reason_codes: List[str] = Field(default_factory=list)


class ImpactAnalysisSchema(ContractSchema):
    schema_version: str = "impact_analysis_v1"
    section_impacts: List[SectionImpactSchema] = Field(default_factory=list)
    affected_ratio: float = Field(ge=0.0, le=1.0)
    full_regeneration: bool = False
    major_change: bool = False


class VersionDecisionSchema(ContractSchema):
    schema_version: str = "version_decision_v1"
    previous_version: Optional[str] = None
    next_version: str
    change_level: str
    reason_codes: List[str] = Field(default_factory=list)

    @field_validator("change_level")
    @classmethod
    def _valid_change_level(cls, value: str) -> str:
        if value not in {"initial", "minor", "major"}:
            raise ValueError("change_level must be initial, minor or major")
        return value
