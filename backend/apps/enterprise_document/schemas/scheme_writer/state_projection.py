"""Strongly typed Multi-Agent state projections owned by the production graphs.

These models do not form another Shared State.  They are nested business payloads
whose persistence authority remains the existing top-level and section LangGraph
checkpoints.  Existing plan, evidence, section, gate, task and runtime fields remain
the source of truth during the migration; the models below provide typed projections
for later Planner/Research/Writer/Verifier integration.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator

from schemas.common import SchemaBase


class PlanProjectionStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    INVALID = "invalid"
    SUPERSEDED = "superseded"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class ResearchProjectionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"


class WritingProjectionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ClaimProjectionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class VerificationProjectionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SectionLifecycleStatus(str, Enum):
    PENDING = "pending"
    PLANNED = "planned"
    RESEARCHING = "researching"
    EVIDENCE_READY = "evidence_ready"
    GENERATING = "generating"
    PAUSED_FOR_PRECHECK = "paused_for_precheck"
    GENERATED = "generated"
    VERIFYING = "verifying"
    ACCEPTED = "accepted"
    PARTIALLY_ACCEPTED = "partially_accepted"
    WAITING_HUMAN = "waiting_human"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentValidationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"
    WAITING_HUMAN = "waiting_human"


def _unique_non_empty(values: list[str], *, field_name: str) -> list[str]:
    normalized = [str(item).strip() for item in values]
    if any(not item for item in normalized):
        raise ValueError(f"{field_name} cannot contain empty values")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique values")
    return normalized


class PlanStateSchema(SchemaBase):
    """Control projection for the canonical ``DocumentPlanSchema``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "plan_state_projection_v1"
    status: PlanProjectionStatus = PlanProjectionStatus.PENDING
    canonical_plan_id: str | None = None
    plan_revision: int = Field(default=0, ge=0)
    active_plan_revision_id: str | None = None
    input_snapshot_version: str | None = None
    section_order: list[str] = Field(default_factory=list)
    affected_section_ids: list[str] = Field(default_factory=list)
    impact_analysis_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("section_order", "affected_section_ids")
    @classmethod
    def _unique_section_ids(cls, value: list[str], info) -> list[str]:
        return _unique_non_empty(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _ready_plan_has_identity(self) -> "PlanStateSchema":
        if self.status == PlanProjectionStatus.READY:
            if not self.canonical_plan_id:
                raise ValueError("ready plan state requires canonical_plan_id")
            if self.plan_revision < 1:
                raise ValueError("ready plan state requires plan_revision >= 1")
        return self


class AttemptStateSchema(SchemaBase):
    """One section execution attempt bound to a plan and input snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_attempt_state_v1"
    attempt_id: str | None = None
    attempt_number: int = Field(default=0, ge=0)
    execution_id: str | None = None
    plan_revision: int = Field(default=0, ge=0)
    input_snapshot_version: str | None = None
    status: AttemptStatus = AttemptStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    last_error_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_terminal_timestamp(self) -> "AttemptStateSchema":
        terminal = {
            AttemptStatus.COMPLETED,
            AttemptStatus.PARTIAL,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
            AttemptStatus.SUPERSEDED,
        }
        if self.finished_at is not None and self.status not in terminal:
            raise ValueError("finished_at requires a terminal attempt status")
        return self


class ResearchStateSchema(SchemaBase):
    """Typed projection of section retrieval and evidence assessment."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_research_state_v1"
    status: ResearchProjectionStatus = ResearchProjectionStatus.PENDING
    evidence_package_ref: str | None = None
    evidence_contract_sha256: str | None = None
    accepted_evidence_ids: list[str] = Field(default_factory=list)
    rejected_evidence_ids: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    research_rounds: int = Field(default=0, ge=0)
    tool_call_ids: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    assessment: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "accepted_evidence_ids",
        "rejected_evidence_ids",
        "evidence_gaps",
        "tool_call_ids",
    )
    @classmethod
    def _unique_values(cls, value: list[str], info) -> list[str]:
        return _unique_non_empty(value, field_name=info.field_name)


class WritingStateSchema(SchemaBase):
    """Typed projection of section draft and future paragraph progress."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_writing_state_v1"
    status: WritingProjectionStatus = WritingProjectionStatus.PENDING
    draft_ref: str | None = None
    draft_version: int = Field(default=0, ge=0)
    paragraph_refs: dict[str, str] = Field(default_factory=dict)
    paragraph_versions: dict[str, int] = Field(default_factory=dict)
    expected_paragraph_ids: list[str] = Field(default_factory=list)
    generation_complete: bool = False
    generation_cursor: str | None = None
    change_manifest_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_paragraph_ids")
    @classmethod
    def _unique_expected_paragraphs(cls, value: list[str]) -> list[str]:
        return _unique_non_empty(value, field_name="expected_paragraph_ids")

    @model_validator(mode="after")
    def _validate_paragraph_maps(self) -> "WritingStateSchema":
        if any(not str(key).strip() or not str(value).strip() for key, value in self.paragraph_refs.items()):
            raise ValueError("paragraph_refs requires non-empty ids and refs")
        if any(not str(key).strip() or version < 1 for key, version in self.paragraph_versions.items()):
            raise ValueError("paragraph_versions requires non-empty ids and positive versions")
        if set(self.paragraph_versions) - set(self.paragraph_refs):
            raise ValueError("paragraph_versions cannot reference unknown paragraph_refs")
        if self.generation_complete and set(self.expected_paragraph_ids) - set(self.paragraph_refs):
            raise ValueError("completed writing state must contain all expected paragraphs")
        return self


class ClaimStateSchema(SchemaBase):
    """Typed projection of extracted and active section claims."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_claim_state_v1"
    status: ClaimProjectionStatus = ClaimProjectionStatus.PENDING
    claim_ids: list[str] = Field(default_factory=list)
    active_claim_ids: list[str] = Field(default_factory=list)
    claim_set_version: int = Field(default=0, ge=0)
    extraction_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("claim_ids", "active_claim_ids")
    @classmethod
    def _unique_claim_ids(cls, value: list[str], info) -> list[str]:
        return _unique_non_empty(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _active_claims_are_known(self) -> "ClaimStateSchema":
        if set(self.active_claim_ids) - set(self.claim_ids):
            raise ValueError("active_claim_ids cannot reference unknown claim_ids")
        return self


class VerificationStateSchema(SchemaBase):
    """Typed projection of paragraph/section verification decisions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_verification_state_v1"
    status: VerificationProjectionStatus = VerificationProjectionStatus.PENDING
    execution_mode: str | None = None
    recommended_action: str | None = None
    verification_ref: str | None = None
    issue_ids: list[str] = Field(default_factory=list)
    accepted_paragraph_ids: list[str] = Field(default_factory=list)
    rejected_paragraph_ids: list[str] = Field(default_factory=list)
    verification_rounds: int = Field(default=0, ge=0)
    checks: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "issue_ids",
        "accepted_paragraph_ids",
        "rejected_paragraph_ids",
    )
    @classmethod
    def _unique_verification_ids(cls, value: list[str], info) -> list[str]:
        return _unique_non_empty(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _accepted_and_rejected_are_disjoint(self) -> "VerificationStateSchema":
        overlap = set(self.accepted_paragraph_ids) & set(self.rejected_paragraph_ids)
        if overlap:
            raise ValueError(
                "accepted_paragraph_ids and rejected_paragraph_ids must be disjoint"
            )
        return self


class SectionStateSchema(SchemaBase):
    """Section-owned state nested in the existing document/section payloads."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "section_state_projection_v1"
    section_id: str
    section_title: str
    section_order: int = Field(ge=0)
    lifecycle_status: SectionLifecycleStatus = SectionLifecycleStatus.PENDING
    applicable: bool = True
    execution_group: str | None = None
    can_run_in_parallel: bool = False
    requires_global_context: bool = True
    hard_dependencies: list[str] = Field(default_factory=list)
    consistency_references: list[str] = Field(default_factory=list)
    attempt: AttemptStateSchema = Field(default_factory=AttemptStateSchema)
    research: ResearchStateSchema = Field(default_factory=ResearchStateSchema)
    writing: WritingStateSchema = Field(default_factory=WritingStateSchema)
    claims: ClaimStateSchema = Field(default_factory=ClaimStateSchema)
    verification: VerificationStateSchema = Field(
        default_factory=VerificationStateSchema
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("section_id", "section_title")
    @classmethod
    def _non_empty_identity(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("section identity fields cannot be empty")
        return normalized

    @field_validator("hard_dependencies", "consistency_references")
    @classmethod
    def _unique_dependencies(cls, value: list[str], info) -> list[str]:
        return _unique_non_empty(value, field_name=info.field_name)


class DocumentValidationStateSchema(SchemaBase):
    """Document-level verification and closure projection in the top-level payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "document_validation_state_v1"
    status: DocumentValidationStatus = DocumentValidationStatus.PENDING
    validation_revision: int = Field(default=0, ge=0)
    verification_ref: str | None = None
    conflict_repair_plan_ref: str | None = None
    assembly_validation_ref: str | None = None
    final_artifact_review_ref: str | None = None
    generation_report_ref: str | None = None
    document_version: str | None = None
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("issues", "warnings")
    @classmethod
    def _unique_messages(cls, value: list[str], info) -> list[str]:
        return _unique_non_empty(value, field_name=info.field_name)
