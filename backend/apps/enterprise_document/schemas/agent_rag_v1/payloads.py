"""四个业务 Agent 的强类型结果 Payload。"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.scheme_writer.planning import DocumentPlanSchema

from ._base import ContractSchema
from .enums import (
    ResearchRecommendedAction,
    ResearchStatus,
    VerifierAction,
    VerifierExecutionMode,
)


class PlannerResultPayloadSchema(ContractSchema):
    schema_version: str = "planner_result_payload_v2"
    plan_ref: str
    plan_revision: int = Field(ge=1)
    section_task_ids: List[str] = Field(default_factory=list)
    affected_section_ids: List[str] = Field(default_factory=list)
    impact_analysis_ref: Optional[str] = None

    # The production mainline requires the actual canonical plan. Standalone
    # compatibility remains artifact-ref based and may omit this field.
    document_plan: Optional[DocumentPlanSchema] = None
    planner_metadata: Dict[str, Any] = Field(default_factory=dict)


class RejectedEvidenceSchema(ContractSchema):
    evidence_id: str
    reason_code: str


class ResearchEvidencePackageSchema(ContractSchema):
    """Research-local evidence projection.

    ``research_status`` and ``recommended_action`` are retained only so archived
    standalone fixtures can still parse their historical payloads.  Target
    mainline Research producers leave both fields unset; neither field is a
    workflow-control contract.
    """

    schema_version: str = "research_evidence_package_v1"
    section_id: str
    accepted_evidence_ids: List[str] = Field(default_factory=list)
    rejected_evidence: List[RejectedEvidenceSchema] = Field(default_factory=list)
    supported_topics: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    research_status: Optional[ResearchStatus] = None
    recommended_action: Optional[ResearchRecommendedAction] = None


class ResearchResultPayloadSchema(ContractSchema):
    schema_version: str = "research_result_payload_v2"
    section_id: str
    evidence_package_ref: str
    evidence_summary: ResearchEvidencePackageSchema
    evidence_contract_sha256: Optional[str] = None
    tool_call_ids: List[str] = Field(default_factory=list)
    research_round: int = Field(default=0, ge=0)
    research_mode: str = "initial"
    research_execution_status: str = "completed"
    agent_metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("research_execution_status")
    @classmethod
    def _research_execution_status_is_technical(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"completed", "partial"}:
            raise ValueError(
                "research_execution_status must be completed or partial; "
                "technical failure is carried by AgentExecutionResult.error"
            )
        return normalized


class ParagraphArtifactSchema(ContractSchema):
    """One checkpoint-persisted paragraph artifact.

    ``content`` is intentionally embedded in the existing Section checkpoint.
    ``content_ref`` is a stable checkpoint URI used by Agent contracts and
    downstream projections; it does not create a second artifact authority.
    """

    paragraph_id: str
    paragraph_version: int = Field(ge=1)
    order: int = Field(ge=0)
    role: str
    content_ref: str
    content_hash: str
    content: str = ""

    @field_validator("paragraph_id", "role", "content_ref")
    @classmethod
    def _non_empty_paragraph_fields(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("paragraph artifact identity fields cannot be empty")
        return normalized

    @field_validator("content_hash")
    @classmethod
    def _valid_content_hash(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized.startswith("sha256:"):
            raise ValueError("paragraph content_hash must use sha256: prefix")
        if not normalized.removeprefix("sha256:").strip():
            raise ValueError("paragraph content_hash cannot be empty")
        return normalized

    @field_validator("content")
    @classmethod
    def _content_is_not_whitespace(cls, value: str) -> str:
        if value and not value.strip():
            raise ValueError("paragraph content cannot be whitespace only")
        return value

    def verify_content_integrity(self) -> None:
        if not self.content:
            return
        actual = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if actual != self.content_hash:
            raise ValueError(
                f"paragraph content hash mismatch: {self.paragraph_id}"
            )


class WriterResultPayloadSchema(ContractSchema):
    schema_version: str = "writer_result_payload_v2"
    section_id: str
    draft_ref: str
    draft_version: int = Field(ge=1)
    paragraphs: List[ParagraphArtifactSchema] = Field(default_factory=list)
    generation_complete: bool = False
    expected_paragraph_ids: List[str] = Field(default_factory=list)
    change_manifest_ref: Optional[str] = None
    generation_cursor: Optional[str] = None
    section_snapshot: Optional[SchemeSectionSchema] = None
    writer_metadata: Dict[str, Any] = Field(default_factory=dict)

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
    schema_version: str = "verifier_result_payload_v2"
    execution_mode: VerifierExecutionMode
    action: VerifierAction
    issue_ids: List[str] = Field(default_factory=list)
    accepted_paragraph_ids: List[str] = Field(default_factory=list)
    rejected_paragraph_ids: List[str] = Field(default_factory=list)
    verification_ref: str
    repair_plan_ref: Optional[str] = None
    checks: Dict[str, bool] = Field(default_factory=dict)
    decision_metadata: Dict[str, Any] = Field(default_factory=dict)
