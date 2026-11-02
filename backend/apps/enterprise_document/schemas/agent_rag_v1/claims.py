"""Canonical M6 Claim existence / coverage contracts plus legacy compatibility.

Target ClaimRecord is immutable Claim-existence truth only. It deliberately
contains no Evidence-support verdict, verification verdict, lifecycle head, or
Gate decision. Legacy ClaimSchema remains compatibility input/projection.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from ._base import ContractSchema
from .artifacts import ArtifactVersionRefSchema
from .facts import (
    CanonicalFactRecordSchema,
    FactSnapshotBindingSchema,
    FactSnapshotContentSchema,
    FactSourceBindingSchema,
)
from .enums import (
    ClaimLifecycleStatus,
    ClaimSupportType,
    ClaimVerificationStatus,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenClaimContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class ClaimTextSpanSchema(_FrozenClaimContract):
    schema_version: Literal["claim_text_span_v1"] = "claim_text_span_v1"
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    text: NonEmptyStr

    @model_validator(mode="after")
    def _valid_span(self) -> "ClaimTextSpanSchema":
        if self.end_offset <= self.start_offset:
            raise ValueError("Claim text span end_offset must be > start_offset")
        if len(self.text) != self.end_offset - self.start_offset:
            raise ValueError(
                "Claim text span length must match exact start/end offsets"
            )
        return self


class ClaimOccurrenceRefSchema(_FrozenClaimContract):
    schema_version: Literal["claim_occurrence_ref_v1"] = "claim_occurrence_ref_v1"
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    paragraph_version_ref: ArtifactVersionRefSchema
    claim_type: NonEmptyStr
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    text_span_hash: Sha256Hex
    span_locator_policy_id: NonEmptyStr
    span_locator_policy_version: NonEmptyStr
    normalized_value_hash: Sha256Hex
    normalization_policy_id: NonEmptyStr
    normalization_policy_version: NonEmptyStr
    claim_identity_policy_id: NonEmptyStr
    claim_identity_policy_version: NonEmptyStr

    @model_validator(mode="after")
    def _bind_exact_paragraph(self) -> "ClaimOccurrenceRefSchema":
        ref = self.paragraph_version_ref
        if ref.logical_ref.artifact_type != "paragraph":
            raise ValueError("Claim occurrence requires Paragraph ArtifactVersion")
        if ref.logical_ref.run_id != self.run_id:
            raise ValueError("Claim occurrence Paragraph belongs to another Run")
        if ref.logical_ref.domain_scope_ref != f"section:{self.section_id}":
            raise ValueError("Claim occurrence Paragraph belongs to another Section")
        if self.end_offset <= self.start_offset:
            raise ValueError("Claim occurrence end_offset must be > start_offset")
        return self


class ClaimRefSchema(_FrozenClaimContract):
    schema_version: Literal["claim_ref_v1"] = "claim_ref_v1"
    claim_id: NonEmptyStr
    claim_occurrence_ref: ClaimOccurrenceRefSchema


class ClaimExtractionOrigin(str, Enum):
    DETERMINISTIC_EXTRACTOR = "DETERMINISTIC_EXTRACTOR"
    SEMANTIC_EXTRACTOR = "SEMANTIC_EXTRACTOR"
    WRITER_ANNOTATION = "WRITER_ANNOTATION"
    OTHER_AUTHORIZED_EXTRACTOR = "OTHER_AUTHORIZED_EXTRACTOR"


class ClaimCandidateSchema(_FrozenClaimContract):
    schema_version: Literal["claim_candidate_v1"] = "claim_candidate_v1"
    claim_occurrence_ref: ClaimOccurrenceRefSchema
    paragraph_version_ref: ArtifactVersionRefSchema
    section_id: NonEmptyStr
    claim_type: NonEmptyStr
    text_span: ClaimTextSpanSchema
    text_span_hash: Sha256Hex
    normalized_value: Any
    normalized_value_hash: Sha256Hex
    unit: NonEmptyStr | None = None
    extraction_origin: ClaimExtractionOrigin
    extraction_occurrence_ref: NonEmptyStr
    candidate_capture_ref: NonEmptyStr | None = None
    candidate_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _candidate_identity_is_consistent(self) -> "ClaimCandidateSchema":
        occurrence = self.claim_occurrence_ref
        if self.paragraph_version_ref != occurrence.paragraph_version_ref:
            raise ValueError("ClaimCandidate Paragraph ref disagrees with occurrence")
        if self.section_id != occurrence.section_id:
            raise ValueError("ClaimCandidate section disagrees with occurrence")
        if self.claim_type != occurrence.claim_type:
            raise ValueError("ClaimCandidate claim_type disagrees with occurrence")
        if (
            self.text_span.start_offset != occurrence.start_offset
            or self.text_span.end_offset != occurrence.end_offset
            or self.text_span_hash != occurrence.text_span_hash
            or self.normalized_value_hash != occurrence.normalized_value_hash
        ):
            raise ValueError("ClaimCandidate semantic identity disagrees with occurrence")
        if (
            self.extraction_origin is ClaimExtractionOrigin.SEMANTIC_EXTRACTOR
            and self.candidate_capture_ref is None
        ):
            raise ValueError(
                "semantic Claim extraction requires durable candidate_capture_ref"
            )
        return self


class ClaimRecordSchema(_FrozenClaimContract):
    """Immutable Claim-existence truth; support/verification/lifecycle are external."""

    schema_version: Literal["claim_record_v1"] = "claim_record_v1"
    claim_ref: ClaimRefSchema
    claim_occurrence_ref: ClaimOccurrenceRefSchema
    paragraph_version_ref: ArtifactVersionRefSchema
    section_id: NonEmptyStr
    claim_type: NonEmptyStr
    text_span: ClaimTextSpanSchema
    text_span_hash: Sha256Hex
    normalized_value: Any
    normalized_value_hash: Sha256Hex
    unit: NonEmptyStr | None = None
    extraction_occurrence_ref: NonEmptyStr
    claim_record_commit_ref: NonEmptyStr

    @model_validator(mode="after")
    def _definition_is_exact(self) -> "ClaimRecordSchema":
        if self.claim_ref.claim_occurrence_ref != self.claim_occurrence_ref:
            raise ValueError("ClaimRecord claim_ref occurrence mismatch")
        if self.paragraph_version_ref != self.claim_occurrence_ref.paragraph_version_ref:
            raise ValueError("ClaimRecord Paragraph ref mismatch")
        if self.section_id != self.claim_occurrence_ref.section_id:
            raise ValueError("ClaimRecord section mismatch")
        if self.claim_type != self.claim_occurrence_ref.claim_type:
            raise ValueError("ClaimRecord claim_type mismatch")
        if (
            self.text_span.start_offset != self.claim_occurrence_ref.start_offset
            or self.text_span.end_offset != self.claim_occurrence_ref.end_offset
            or self.text_span_hash != self.claim_occurrence_ref.text_span_hash
            or self.normalized_value_hash
            != self.claim_occurrence_ref.normalized_value_hash
        ):
            raise ValueError("ClaimRecord semantic definition mismatch")
        return self


class ClaimCoverageAccountingState(str, Enum):
    FACTUAL_CLAIMS_MATERIALIZED = "FACTUAL_CLAIMS_MATERIALIZED"
    NO_APPLICABLE_FACTUAL_CLAIM = "NO_APPLICABLE_FACTUAL_CLAIM"
    INDETERMINATE = "INDETERMINATE"


class ArtifactClaimCoverageUnitSchema(_FrozenClaimContract):
    schema_version: Literal["artifact_claim_coverage_unit_v1"] = (
        "artifact_claim_coverage_unit_v1"
    )
    paragraph_version_ref: ArtifactVersionRefSchema
    coverage_unit_id: NonEmptyStr
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    accounting_state: ClaimCoverageAccountingState
    claim_occurrence_refs: tuple[ClaimOccurrenceRefSchema, ...] = ()
    accounting_reason_ref: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _unit_is_coherent(self) -> "ArtifactClaimCoverageUnitSchema":
        if self.paragraph_version_ref.logical_ref.artifact_type != "paragraph":
            raise ValueError("Claim coverage unit requires Paragraph ArtifactVersion")
        if self.end_offset <= self.start_offset:
            raise ValueError("coverage unit end_offset must be > start_offset")
        if (
            self.accounting_state
            is ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED
            and not self.claim_occurrence_refs
        ):
            raise ValueError(
                "FACTUAL_CLAIMS_MATERIALIZED coverage unit requires Claim occurrences"
            )
        if (
            self.accounting_state
            is ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM
            and self.claim_occurrence_refs
        ):
            raise ValueError(
                "NO_APPLICABLE_FACTUAL_CLAIM cannot name Claim occurrences"
            )
        for occurrence in self.claim_occurrence_refs:
            if occurrence.paragraph_version_ref != self.paragraph_version_ref:
                raise ValueError("coverage Claim occurrence belongs to another Paragraph")
            if occurrence.start_offset < self.start_offset or occurrence.end_offset > self.end_offset:
                raise ValueError("coverage Claim occurrence escapes its coverage unit")
        ids = [
            (
                item.claim_type,
                item.start_offset,
                item.end_offset,
                item.normalized_value_hash,
            )
            for item in self.claim_occurrence_refs
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("coverage unit Claim occurrences must be unique")
        return self


class ClaimCoverageAccountingSchema(_FrozenClaimContract):
    schema_version: Literal["claim_coverage_accounting_v1"] = (
        "claim_coverage_accounting_v1"
    )
    coverage_scope_ref: NonEmptyStr
    paragraph_version_refs: tuple[ArtifactVersionRefSchema, ...] = Field(min_length=1)
    coverage_units: tuple[ArtifactClaimCoverageUnitSchema, ...] = Field(min_length=1)
    accounting_state: ClaimCoverageAccountingState
    extraction_occurrence_ref: NonEmptyStr
    coverage_semantic_contract_version: NonEmptyStr

    @model_validator(mode="after")
    def _lossless_partition(self) -> "ClaimCoverageAccountingSchema":
        if len(self.paragraph_version_refs) != len(set(self.paragraph_version_refs)):
            raise ValueError("coverage Paragraph refs must be unique")
        allowed = set(self.paragraph_version_refs)
        units_by_paragraph: dict[ArtifactVersionRefSchema, list[ArtifactClaimCoverageUnitSchema]] = {}
        for unit in self.coverage_units:
            if unit.paragraph_version_ref not in allowed:
                raise ValueError("coverage unit targets Paragraph outside coverage scope")
            units_by_paragraph.setdefault(unit.paragraph_version_ref, []).append(unit)
        if set(units_by_paragraph) != allowed:
            raise ValueError("every coverage Paragraph requires at least one unit")

        for paragraph_ref, units in units_by_paragraph.items():
            ordered = sorted(units, key=lambda item: (item.start_offset, item.end_offset))
            if ordered[0].start_offset != 0:
                raise ValueError("coverage partition must begin at Paragraph offset 0")
            for prior, current in zip(ordered, ordered[1:]):
                if prior.end_offset != current.start_offset:
                    raise ValueError("coverage partition must be contiguous and non-overlapping")
            unit_ids = [item.coverage_unit_id for item in ordered]
            if len(unit_ids) != len(set(unit_ids)):
                raise ValueError("coverage unit ids must be unique per Paragraph")

        states = {item.accounting_state for item in self.coverage_units}
        expected = (
            ClaimCoverageAccountingState.INDETERMINATE
            if ClaimCoverageAccountingState.INDETERMINATE in states
            else ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED
            if ClaimCoverageAccountingState.FACTUAL_CLAIMS_MATERIALIZED in states
            else ClaimCoverageAccountingState.NO_APPLICABLE_FACTUAL_CLAIM
        )
        if self.accounting_state is not expected:
            raise ValueError("coverage aggregate accounting_state is inconsistent")
        return self


class ClaimCoverageProofRefSchema(_FrozenClaimContract):
    schema_version: Literal["claim_coverage_proof_ref_v1"] = "claim_coverage_proof_ref_v1"
    coverage_proof_id: NonEmptyStr
    coverage_scope_ref: NonEmptyStr
    coverage_semantic_fingerprint: Sha256Hex


class ClaimRecordCommitReceiptSchema(_FrozenClaimContract):
    schema_version: Literal["claim_record_commit_receipt_v1"] = (
        "claim_record_commit_receipt_v1"
    )
    committed_operation_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    claim_refs: tuple[ClaimRefSchema, ...]
    coverage_proof_ref: ClaimCoverageProofRefSchema


# ---------------------------------------------------------------------------
# Legacy compatibility contracts
# ---------------------------------------------------------------------------


class ClaimSourceBindingSchema(ContractSchema):
    source_type: str
    source_ref: str
    support_type: ClaimSupportType


class ClaimSchema(ContractSchema):
    schema_version: str = "claim_v1"
    claim_id: str
    section_id: str
    paragraph_id: str
    claim_type: str
    text_span_ref: str
    normalized_value: Any
    unit: Optional[str] = None
    source_bindings: List[ClaimSourceBindingSchema] = Field(default_factory=list)
    verification_status: ClaimVerificationStatus = ClaimVerificationStatus.PENDING
    lifecycle_status: ClaimLifecycleStatus = ClaimLifecycleStatus.ACTIVE
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "claim_id",
        "section_id",
        "paragraph_id",
        "claim_type",
        "text_span_ref",
        mode="before",
    )
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("claim identifiers cannot be empty")
        return normalized


class ExcelDeviceFactSchema(ContractSchema):
    schema_version: str = "excel_device_fact_v1"
    fact_id: str
    device_name: str
    aliases: List[str] = Field(default_factory=list)
    device_type: Optional[str] = None
    model: Optional[str] = None
    specification: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    formula: Optional[str] = None
    calculation_result: Optional[Decimal] = None
    source_ref: str

    @field_validator("aliases")
    @classmethod
    def _unique_aliases(cls, value: List[str]) -> List[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("device aliases cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("device aliases must be unique")
        return normalized


class FactSnapshotSchema(ContractSchema):
    schema_version: str = "fact_snapshot_v1"
    snapshot_version: str
    artifact_ref: str
    devices: List[ExcelDeviceFactSchema] = Field(default_factory=list)
    construction_scope: List[str] = Field(default_factory=list)
    calculation_parameters: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_fact_ids(self) -> "FactSnapshotSchema":
        fact_ids = [item.fact_id for item in self.devices]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("FactSnapshot device fact ids must be unique")
        return self


__all__ = [
    "ArtifactClaimCoverageUnitSchema",
    "ClaimCandidateSchema",
    "ClaimCoverageAccountingSchema",
    "ClaimCoverageAccountingState",
    "ClaimCoverageProofRefSchema",
    "ClaimExtractionOrigin",
    "ClaimOccurrenceRefSchema",
    "ClaimRecordCommitReceiptSchema",
    "ClaimRecordSchema",
    "ClaimRefSchema",
    "ClaimSchema",
    "ClaimSourceBindingSchema",
    "ClaimTextSpanSchema",
    "ExcelDeviceFactSchema",
    "FactSnapshotSchema",
]
