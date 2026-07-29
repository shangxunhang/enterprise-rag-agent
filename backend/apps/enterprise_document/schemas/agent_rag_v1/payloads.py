"""四个业务 Agent 的强类型结果 Payload。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from ._base import ContractSchema
from .enums import (
    ResearchRecommendedAction,
    ResearchStatus,
    VerifierAction,
    VerifierExecutionMode,
)


class PlannerResultPayloadSchema(ContractSchema):
    plan_ref: str
    plan_revision: int = Field(ge=1)
    section_task_ids: List[str] = Field(default_factory=list)
    affected_section_ids: List[str] = Field(default_factory=list)
    impact_analysis_ref: Optional[str] = None


class RejectedEvidenceSchema(ContractSchema):
    evidence_id: str
    reason_code: str


class ResearchEvidencePackageSchema(ContractSchema):
    schema_version: str = "research_evidence_package_v1"
    section_id: str
    accepted_evidence_ids: List[str] = Field(default_factory=list)
    rejected_evidence: List[RejectedEvidenceSchema] = Field(default_factory=list)
    supported_topics: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    research_status: ResearchStatus
    recommended_action: ResearchRecommendedAction


class ResearchResultPayloadSchema(ContractSchema):
    section_id: str
    evidence_package_ref: str
    evidence_summary: ResearchEvidencePackageSchema


class ParagraphArtifactSchema(ContractSchema):
    paragraph_id: str
    paragraph_version: int = Field(ge=1)
    order: int = Field(ge=0)
    role: str
    content_ref: str
    content_hash: str


class WriterResultPayloadSchema(ContractSchema):
    section_id: str
    draft_ref: str
    draft_version: int = Field(ge=1)
    paragraphs: List[ParagraphArtifactSchema] = Field(default_factory=list)
    generation_complete: bool = False
    expected_paragraph_ids: List[str] = Field(default_factory=list)
    change_manifest_ref: Optional[str] = None
    generation_cursor: Optional[str] = None

    @field_validator("expected_paragraph_ids")
    @classmethod
    def _unique_expected_paragraphs(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("expected paragraph ids must be unique")
        return value


class VerifierIssueSchema(ContractSchema):
    issue_id: str
    issue_code: str
    severity: str
    message: str
    section_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    claim_ids: List[str] = Field(default_factory=list)
    details_ref: Optional[str] = None


class VerifierResultPayloadSchema(ContractSchema):
    execution_mode: VerifierExecutionMode
    action: VerifierAction
    issue_ids: List[str] = Field(default_factory=list)
    accepted_paragraph_ids: List[str] = Field(default_factory=list)
    rejected_paragraph_ids: List[str] = Field(default_factory=list)
    verification_ref: str
    repair_plan_ref: Optional[str] = None
