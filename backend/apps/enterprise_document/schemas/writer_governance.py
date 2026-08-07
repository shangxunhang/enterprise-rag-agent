"""Stage E contracts for writer-side enforcement and artifact integrity."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.gate_decision import (
    DeliveryMode,
    GateDecisionSchema,
)
from rag.catalog.document_profile import ClaimType


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WriterExecutionStatus(str, Enum):
    GROUNDED = "grounded"
    QUALIFIED = "qualified"
    FRAMEWORK_ONLY = "framework_only"
    BLOCKED = "blocked"
    HUMAN_REVIEW = "human_review"
    NOT_ASSESSED = "not_assessed"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class WriterGateBindingSchema(_FrozenContract):
    """Immutable binding between one section writer invocation and Stage D output."""

    schema_version: Literal["writer_gate_binding_v2"] = "writer_gate_binding_v2"
    section_id: NonEmptyStr
    section_title: NonEmptyStr
    section_order: int | None = Field(default=None, ge=1)
    document_id: NonEmptyStr | None = None
    run_id: NonEmptyStr | None = None
    gate_decision: GateDecisionSchema
    generation_allowed: bool
    evidence_contract_sha256: Sha256Hex | None = None
    prompt_evidence_source: NonEmptyStr | None = None
    prompt_evidence_contract_sha256: Sha256Hex | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _binding_is_consistent(self) -> "WriterGateBindingSchema":
        mode = self.gate_decision.delivery_mode
        permitted = mode in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY,
        }
        if self.generation_allowed != permitted:
            raise ValueError(
                "generation_allowed must match the final gate delivery_mode"
            )
        if mode is DeliveryMode.HUMAN_REVIEW and not self.gate_decision.human_review_required:
            raise ValueError("human_review binding requires human_review_required=true")
        if mode is DeliveryMode.BLOCKED and self.gate_decision.allowed_claim_types:
            raise ValueError("blocked writer binding cannot allow claim types")
        return self

    @property
    def identity_fields_present(self) -> bool:
        return bool(
            self.section_order is not None
            and self.document_id is not None
            and self.run_id is not None
        )

    @property
    def evidence_lineage_fields_present(self) -> bool:
        return bool(
            self.evidence_contract_sha256 is not None
            and self.prompt_evidence_contract_sha256 is not None
        )

    @property
    def ordinary_generation_permitted(self) -> bool:
        return bool(
            self.generation_allowed
            and self.identity_fields_present
            and self.evidence_lineage_fields_present
        )

    @property
    def legacy_artifact_incomplete(self) -> bool:
        return bool(
            not self.identity_fields_present
            or (
                self.generation_allowed
                and not self.evidence_lineage_fields_present
            )
        )

    @property
    def execution_status(self) -> WriterExecutionStatus:
        mapping = {
            DeliveryMode.GROUNDED: WriterExecutionStatus.GROUNDED,
            DeliveryMode.QUALIFIED: WriterExecutionStatus.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY: WriterExecutionStatus.FRAMEWORK_ONLY,
            DeliveryMode.BLOCKED: WriterExecutionStatus.BLOCKED,
            DeliveryMode.HUMAN_REVIEW: WriterExecutionStatus.HUMAN_REVIEW,
        }
        return mapping[self.gate_decision.delivery_mode]


class ClaimAuditIntegritySchema(_FrozenContract):
    """Cryptographic binding between an audit and the exact writer artifact."""

    schema_version: Literal["claim_audit_integrity_v1"] = (
        "claim_audit_integrity_v1"
    )
    section_id: NonEmptyStr
    section_title: NonEmptyStr
    content_sha256: Sha256Hex
    normalized_content_sha256: Sha256Hex
    writer_gate_binding_sha256: Sha256Hex
    evidence_contract_sha256: Sha256Hex | None = None
    prompt_evidence_contract_sha256: Sha256Hex | None = None
    classifier_id: NonEmptyStr
    classifier_version: NonEmptyStr
    classifier_policy_sha256: Sha256Hex
    substantive_detector_version: NonEmptyStr

    @property
    def evidence_lineage_fields_present(self) -> bool:
        return bool(
            self.evidence_contract_sha256 is not None
            and self.prompt_evidence_contract_sha256 is not None
        )


class GeneratedClaimSchema(_FrozenContract):
    """One deterministic post-generation claim annotation."""

    schema_version: Literal["generated_claim_v2"] = "generated_claim_v2"
    claim_id: NonEmptyStr
    section_id: NonEmptyStr
    sentence_index: int = Field(ge=0)
    claim_type: ClaimType
    text: NonEmptyStr
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    classifier_id: NonEmptyStr
    classifier_version: NonEmptyStr
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _span_is_valid(self) -> "GeneratedClaimSchema":
        if self.span_end <= self.span_start:
            raise ValueError("claim span_end must be greater than span_start")
        return self


class ClaimAuditViolationSchema(_FrozenContract):
    schema_version: Literal["claim_audit_violation_v2"] = (
        "claim_audit_violation_v2"
    )
    violation_code: NonEmptyStr
    message: NonEmptyStr
    claim_id: NonEmptyStr | None = None
    claim_type: ClaimType | None = None
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimAuditResultSchema(_FrozenContract):
    """Writer-side proof that the exact final content obeys the final Stage D gate."""

    schema_version: Literal["claim_audit_result_v2"] = "claim_audit_result_v2"
    section_id: NonEmptyStr
    execution_status: WriterExecutionStatus
    passed: bool
    human_review_required: bool = False
    integrity: ClaimAuditIntegritySchema
    audit_created_at: datetime
    generated_claims: list[GeneratedClaimSchema] = Field(default_factory=list)
    violations: list[ClaimAuditViolationSchema] = Field(default_factory=list)
    allowed_claim_types: list[ClaimType] = Field(default_factory=list)
    forbidden_claim_types: list[ClaimType] = Field(default_factory=list)
    required_qualifiers: list[NonEmptyStr] = Field(default_factory=list)
    applied_qualifiers: list[NonEmptyStr] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _audit_is_consistent(self) -> "ClaimAuditResultSchema":
        if self.audit_created_at.tzinfo is None:
            raise ValueError("audit_created_at must be timezone-aware")
        if self.integrity.section_id != self.section_id:
            raise ValueError("audit integrity section_id must match audit section_id")
        claim_ids = [item.claim_id for item in self.generated_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("generated claim ids must be unique")
        if any(item.section_id != self.section_id for item in self.generated_claims):
            raise ValueError("every generated claim must belong to the audit section")
        if len(self.allowed_claim_types) != len(set(self.allowed_claim_types)):
            raise ValueError("allowed_claim_types must be unique")
        if len(self.forbidden_claim_types) != len(set(self.forbidden_claim_types)):
            raise ValueError("forbidden_claim_types must be unique")
        if set(self.allowed_claim_types) & set(self.forbidden_claim_types):
            raise ValueError("allowed and forbidden claim types cannot overlap")
        if len(self.required_qualifiers) != len(set(self.required_qualifiers)):
            raise ValueError("required_qualifiers must be unique")
        if len(self.applied_qualifiers) != len(set(self.applied_qualifiers)):
            raise ValueError("applied_qualifiers must be unique")
        if self.passed != (not self.violations and all(self.checks.values())):
            raise ValueError("passed must equal violation-free all-checks-true state")
        if self.execution_status in {
            WriterExecutionStatus.BLOCKED,
            WriterExecutionStatus.HUMAN_REVIEW,
            WriterExecutionStatus.NOT_ASSESSED,
        } and self.generated_claims:
            raise ValueError(
                "blocked, human_review, and not_assessed executions cannot publish claims"
            )
        return self


__all__ = [
    "ClaimAuditIntegritySchema",
    "ClaimAuditResultSchema",
    "ClaimAuditViolationSchema",
    "GeneratedClaimSchema",
    "Sha256Hex",
    "WriterExecutionStatus",
    "WriterGateBindingSchema",
]
