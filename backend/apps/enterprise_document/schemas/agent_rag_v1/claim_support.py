"""Canonical M6 shared Claim/Evidence support contracts.

CLAIM_EVIDENCE_SUPPORT is the sole canonical semantic fact for one exact
Claim-support subject and one exact Evidence target set. It is deliberately
independent from Post-write and Gate schemas.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ._base import ContractSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenClaimSupportContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class ClaimSupportAuthorityKind(str, Enum):
    SIF07_CLAIM = "SIF07_CLAIM"
    POST_WRITE_FOREIGN_LOCAL = "POST_WRITE_FOREIGN_LOCAL"


class SupportRelation(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INDETERMINATE = "INDETERMINATE"


class ClaimSupportSubjectRefSchema(_FrozenClaimSupportContract):
    schema_version: Literal["claim_support_subject_ref_v1"] = (
        "claim_support_subject_ref_v1"
    )
    authority_kind: ClaimSupportAuthorityKind
    subject_ref: NonEmptyStr


class EvidenceTargetSetRefSchema(_FrozenClaimSupportContract):
    schema_version: Literal["evidence_target_set_ref_v1"] = "evidence_target_set_ref_v1"
    evidence_target_set_id: NonEmptyStr
    claim_support_subject_ref: ClaimSupportSubjectRefSchema
    evidence_member_refs: tuple[NonEmptyStr, ...] = ()
    target_set_contract_version: NonEmptyStr
    target_set_semantic_hash: Sha256Hex

    @model_validator(mode="after")
    def _canonical_members(self) -> "EvidenceTargetSetRefSchema":
        if tuple(sorted(self.evidence_member_refs)) != self.evidence_member_refs:
            raise ValueError("Evidence target members must be canonically sorted")
        if len(self.evidence_member_refs) != len(set(self.evidence_member_refs)):
            raise ValueError("Evidence target members must be unique")
        return self


class ClaimEvidenceSupportCandidateSchema(_FrozenClaimSupportContract):
    schema_version: Literal["claim_evidence_support_candidate_v1"] = (
        "claim_evidence_support_candidate_v1"
    )
    claim_support_subject_ref: ClaimSupportSubjectRefSchema
    evidence_target_set_ref: EvidenceTargetSetRefSchema
    support_semantic_contract_version: NonEmptyStr
    relation: SupportRelation
    supporting_refs: tuple[NonEmptyStr, ...] = ()
    contradicting_refs: tuple[NonEmptyStr, ...] = ()
    evaluation_occurrence_ref: NonEmptyStr
    evaluator_policy_id: NonEmptyStr
    evaluator_policy_version: NonEmptyStr
    diagnostic_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _candidate_is_coherent(self) -> "ClaimEvidenceSupportCandidateSchema":
        if (
            self.evidence_target_set_ref.claim_support_subject_ref
            != self.claim_support_subject_ref
        ):
            raise ValueError("support subject disagrees with Evidence target set")
        members = set(self.evidence_target_set_ref.evidence_member_refs)
        if not set(self.supporting_refs) <= members:
            raise ValueError("supporting refs escape exact Evidence target set")
        if not set(self.contradicting_refs) <= members:
            raise ValueError("contradicting refs escape exact Evidence target set")
        if set(self.supporting_refs) & set(self.contradicting_refs):
            raise ValueError("Evidence ref cannot be both supporting and contradicting")
        if tuple(sorted(self.supporting_refs)) != self.supporting_refs:
            raise ValueError("supporting refs must be canonically sorted")
        if tuple(sorted(self.contradicting_refs)) != self.contradicting_refs:
            raise ValueError("contradicting refs must be canonically sorted")
        if len(self.supporting_refs) != len(set(self.supporting_refs)):
            raise ValueError("supporting refs must be unique")
        if len(self.contradicting_refs) != len(set(self.contradicting_refs)):
            raise ValueError("contradicting refs must be unique")
        if self.relation is SupportRelation.SUPPORTED and not self.supporting_refs:
            raise ValueError("SUPPORTED requires at least one supporting Evidence ref")
        if self.relation is SupportRelation.CONTRADICTED and not self.contradicting_refs:
            raise ValueError("CONTRADICTED requires contradicting Evidence refs")
        return self


class ClaimEvidenceSupportRefSchema(_FrozenClaimSupportContract):
    schema_version: Literal["claim_evidence_support_ref_v1"] = (
        "claim_evidence_support_ref_v1"
    )
    claim_evidence_support_id: NonEmptyStr
    claim_support_subject_ref: ClaimSupportSubjectRefSchema
    evidence_target_set_ref: EvidenceTargetSetRefSchema
    support_semantic_contract_version: NonEmptyStr
    support_semantic_fingerprint: Sha256Hex


class ClaimEvidenceSupportRecordSchema(_FrozenClaimSupportContract):
    schema_version: Literal["claim_evidence_support_record_v1"] = (
        "claim_evidence_support_record_v1"
    )
    support_ref: ClaimEvidenceSupportRefSchema
    claim_support_subject_ref: ClaimSupportSubjectRefSchema
    evidence_target_set_ref: EvidenceTargetSetRefSchema
    support_semantic_contract_version: NonEmptyStr
    relation: SupportRelation
    supporting_refs: tuple[NonEmptyStr, ...] = ()
    contradicting_refs: tuple[NonEmptyStr, ...] = ()
    evaluator_policy_id: NonEmptyStr
    evaluator_policy_version: NonEmptyStr
    evaluation_occurrence_ref: NonEmptyStr
    committed_operation_id: NonEmptyStr

    @model_validator(mode="after")
    def _record_is_exact(self) -> "ClaimEvidenceSupportRecordSchema":
        ref = self.support_ref
        if ref.claim_support_subject_ref != self.claim_support_subject_ref:
            raise ValueError("support record subject/ref mismatch")
        if ref.evidence_target_set_ref != self.evidence_target_set_ref:
            raise ValueError("support record target/ref mismatch")
        if ref.support_semantic_contract_version != self.support_semantic_contract_version:
            raise ValueError("support record contract/ref mismatch")
        return self


__all__ = [
    "ClaimEvidenceSupportCandidateSchema",
    "ClaimEvidenceSupportRecordSchema",
    "ClaimEvidenceSupportRefSchema",
    "ClaimSupportAuthorityKind",
    "ClaimSupportSubjectRefSchema",
    "EvidenceTargetSetRefSchema",
    "SupportRelation",
]
