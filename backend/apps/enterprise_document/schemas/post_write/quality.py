"""Post-write semantic quality projection contracts.

This module is deliberately downstream of canonical M6 Claim/Support authority.
It may project SIF-07 ClaimRecord truth or represent a genuinely foreign
Post-write-local claim target, but it never redefines Claim existence,
Claim coverage, Evidence target-set identity, or CLAIM_EVIDENCE_SUPPORT truth.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from apps.enterprise_document.schemas.agent_rag_v1.claims import (
    ArtifactClaimCoverageUnitSchema,
    ClaimCoverageAccountingSchema,
    ClaimCoverageAccountingState,
    ClaimRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRefSchema,
    ClaimSupportAuthorityKind,
    EvidenceTargetSetRefSchema,
    SupportRelation,
)

from .foundation import ArtifactStateRefSchema, ValidationFactRefSchema, ValidationIntermediateRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerificationResultRefSchema, VerificationVerdict,
)
from .requirement import RequirementRefSchema
from core.hashing import canonical_sha256


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenQualityContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class ClaimTargetAuthorityKind(str, Enum):
    SIF07_PROJECTION = "SIF07_PROJECTION"
    POST_WRITE_FOREIGN_LOCAL = "POST_WRITE_FOREIGN_LOCAL"


class UpstreamSIF07ClaimAuthorityState(str, Enum):
    PRESENT = "UPSTREAM_SIF07_CLAIM_AUTHORITY_PRESENT"
    NOT_APPLICABLE = "UPSTREAM_SIF07_CLAIM_AUTHORITY_NOT_APPLICABLE"
    UNRESOLVED = "UPSTREAM_SIF07_CLAIM_AUTHORITY_UNRESOLVED"


class UpstreamSIF07ClaimAuthorityResolutionSchema(_FrozenQualityContract):
    schema_version: Literal["upstream_sif07_claim_authority_resolution_v1"] = (
        "upstream_sif07_claim_authority_resolution_v1"
    )
    state: UpstreamSIF07ClaimAuthorityState
    claim_refs: tuple[ClaimRefSchema, ...] = ()
    resolution_reason_ref: NonEmptyStr

    @model_validator(mode="after")
    def _state_shape(self) -> "UpstreamSIF07ClaimAuthorityResolutionSchema":
        if self.state is UpstreamSIF07ClaimAuthorityState.PRESENT:
            if not self.claim_refs:
                raise ValueError("PRESENT upstream Claim authority requires Claim refs")
        elif self.claim_refs:
            raise ValueError(
                "NOT_APPLICABLE/UNRESOLVED upstream Claim authority cannot carry Claim refs"
            )
        ids = [ref.claim_id for ref in self.claim_refs]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("upstream Claim refs must be canonical ordered-unique")
        return self


class ForeignClaimTargetCandidateSchema(_FrozenQualityContract):
    schema_version: Literal["foreign_claim_target_candidate_v1"] = (
        "foreign_claim_target_candidate_v1"
    )
    local_claim_occurrence_ref: NonEmptyStr
    canonical_claim_text: NonEmptyStr
    source_span_ref: NonEmptyStr
    local_semantic_contract_version: NonEmptyStr


class PostWriteForeignClaimTargetRefSchema(_FrozenQualityContract):
    schema_version: Literal["post_write_foreign_claim_target_ref_v1"] = (
        "post_write_foreign_claim_target_ref_v1"
    )
    authority_kind: Literal[ClaimTargetAuthorityKind.POST_WRITE_FOREIGN_LOCAL] = (
        ClaimTargetAuthorityKind.POST_WRITE_FOREIGN_LOCAL
    )
    claim_target_ref: NonEmptyStr
    validation_subject_hash: Sha256Hex
    artifact_state_ref: ArtifactStateRefSchema
    local_claim_occurrence_ref: NonEmptyStr
    local_semantic_contract_version: NonEmptyStr

    @field_validator("claim_target_ref")
    @classmethod
    def _reserved_namespace(cls, value: str) -> str:
        if not value.startswith("post-write-foreign-claim://sha256/"):
            raise ValueError(
                "foreign ClaimTarget ref must use post-write-foreign-claim://sha256/ namespace"
            )
        return value

    @model_validator(mode="after")
    def _identity_hash(self) -> "PostWriteForeignClaimTargetRefSchema":
        preimage = {
            "authority_kind": self.authority_kind.value,
            "artifact_state_ref": self.artifact_state_ref.model_dump(mode="json"),
            "validation_subject_hash": self.validation_subject_hash,
            "local_claim_occurrence_ref": self.local_claim_occurrence_ref,
            "local_semantic_contract_version": self.local_semantic_contract_version,
        }
        expected = "post-write-foreign-claim://sha256/" + canonical_sha256(preimage)
        if self.claim_target_ref != expected:
            raise ValueError("foreign ClaimTarget ref/hash mismatch")
        return self


class ArtifactClaimTargetSchema(_FrozenQualityContract):
    """Validation target projection; never a second ClaimRecord authority."""

    schema_version: Literal["artifact_claim_target_v1"] = "artifact_claim_target_v1"
    claim_target_id: NonEmptyStr
    authority_kind: ClaimTargetAuthorityKind
    artifact_state_ref: ArtifactStateRefSchema
    validation_subject_hash: Sha256Hex
    source_claim_ref: ClaimRefSchema | None = None
    foreign_local_ref: PostWriteForeignClaimTargetRefSchema | None = None
    canonical_claim_text: NonEmptyStr
    source_span_ref: NonEmptyStr
    semantic_version: NonEmptyStr
    claim_target_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exclusive_authority(self) -> "ArtifactClaimTargetSchema":
        if self.authority_kind is ClaimTargetAuthorityKind.SIF07_PROJECTION:
            if self.source_claim_ref is None or self.foreign_local_ref is not None:
                raise ValueError(
                    "SIF07_PROJECTION requires exact source ClaimRef and forbids foreign ref"
                )
        else:
            if self.foreign_local_ref is None or self.source_claim_ref is not None:
                raise ValueError(
                    "POST_WRITE_FOREIGN_LOCAL requires exact foreign ref and forbids SIF-07 ref"
                )
            if self.foreign_local_ref.artifact_state_ref != self.artifact_state_ref:
                raise ValueError("foreign ClaimTarget artifact binding mismatch")
            if (
                self.foreign_local_ref.validation_subject_hash
                != self.validation_subject_hash
            ):
                raise ValueError("foreign ClaimTarget ValidationSubject mismatch")
            if self.claim_target_id != self.foreign_local_ref.claim_target_ref:
                raise ValueError("foreign ClaimTarget identity must equal canonical foreign ref")

        if self.authority_kind is ClaimTargetAuthorityKind.SIF07_PROJECTION:
            base = {
                "authority_kind": self.authority_kind.value,
                "artifact_state_ref": self.artifact_state_ref.model_dump(mode="json"),
                "validation_subject_hash": self.validation_subject_hash,
                "source_claim_ref": self.source_claim_ref.model_dump(mode="json"),
                "canonical_claim_text": self.canonical_claim_text,
                "source_span_ref": self.source_span_ref,
                "semantic_version": self.semantic_version,
            }
            expected_target_id = (
                "post-write-claim-projection://sha256/" + canonical_sha256(base)
            )
            final = {**base, "claim_target_id": expected_target_id}
            expected_hash = canonical_sha256(final)
        else:
            expected_target_id = self.foreign_local_ref.claim_target_ref
            final = {
                "claim_target_id": expected_target_id,
                "authority_kind": self.authority_kind.value,
                "artifact_state_ref": self.artifact_state_ref.model_dump(mode="json"),
                "validation_subject_hash": self.validation_subject_hash,
                "foreign_local_ref": self.foreign_local_ref.model_dump(mode="json"),
                "canonical_claim_text": self.canonical_claim_text,
                "source_span_ref": self.source_span_ref,
                "semantic_version": self.semantic_version,
            }
            expected_hash = canonical_sha256(final)
        if self.claim_target_id != expected_target_id:
            raise ValueError("ArtifactClaimTarget id/hash preimage mismatch")
        if self.claim_target_sha256 != expected_hash:
            raise ValueError("ArtifactClaimTarget canonical hash mismatch")
        return self


class ClaimEvidenceSupportPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["claim_evidence_support_payload_v1"] = (
        "claim_evidence_support_payload_v1"
    )
    claim_target_id: NonEmptyStr
    evidence_target_set_ref: EvidenceTargetSetRefSchema
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    relation: SupportRelation

    @model_validator(mode="after")
    def _exact_support_binding(self) -> "ClaimEvidenceSupportPayloadSchema":
        if (
            self.claim_evidence_support_ref.evidence_target_set_ref
            != self.evidence_target_set_ref
        ):
            raise ValueError("support payload target-set/ref mismatch")
        return self


class FaithfulnessCertaintyRelation(str, Enum):
    ALIGNED = "ALIGNED"
    ESCALATED = "ESCALATED"
    INDETERMINATE = "INDETERMINATE"


class FaithfulnessCertaintyObservationPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["faithfulness_certainty_observation_v1"] = (
        "faithfulness_certainty_observation_v1"
    )
    claim_target_id: NonEmptyStr
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    evidence_target_set_ref: EvidenceTargetSetRefSchema
    requirement_ref: RequirementRefSchema
    applicable_requirement_ids: tuple[NonEmptyStr, ...] = ()
    certainty_relation: FaithfulnessCertaintyRelation
    reason_codes: tuple[NonEmptyStr, ...] = ()
    physical_support_receipt_ref: NonEmptyStr

    @model_validator(mode="after")
    def _canonical_certainty(self) -> "FaithfulnessCertaintyObservationPayloadSchema":
        for name in ("applicable_requirement_ids", "reason_codes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if (
            self.claim_evidence_support_ref.evidence_target_set_ref
            != self.evidence_target_set_ref
        ):
            raise ValueError("Faithfulness certainty target differs from canonical Support target")
        return self


class FaithfulnessPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["faithfulness_payload_v1"] = "faithfulness_payload_v1"
    claim_target_id: NonEmptyStr
    claim_evidence_support_ref: ClaimEvidenceSupportRefSchema
    support_relation: SupportRelation
    rule_ids: tuple[NonEmptyStr, ...]
    observation_codes: tuple[NonEmptyStr, ...] = ()
    related_refs: tuple[NonEmptyStr, ...] = ()
    certainty_observation: FaithfulnessCertaintyObservationPayloadSchema | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _canonical_lists(self) -> "FaithfulnessPayloadSchema":
        for name in ("rule_ids", "observation_codes", "related_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if not self.rule_ids:
            raise ValueError("Faithfulness payload requires at least one rule id")
        return self


class FaithfulnessVerificationPayloadSchema(_FrozenQualityContract):
    """Thin Result V2 verdict projection; Result remains the canonical owner."""

    schema_version: Literal["faithfulness_verification_payload_v1"] = "faithfulness_verification_payload_v1"
    claim_target_id: NonEmptyStr
    verification_result_ref: VerificationResultRefSchema
    verification_verdict: VerificationVerdict
    source_snapshot_sha256: Sha256Hex
    rule_ids: tuple[NonEmptyStr, ...]
    observation_codes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _canonical_lists(self) -> "FaithfulnessVerificationPayloadSchema":
        for name in ("rule_ids", "observation_codes"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if not self.rule_ids:
            raise ValueError("Faithfulness verification requires at least one rule id")
        return self


class CitationOccurrenceBindingState(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    INDETERMINATE = "INDETERMINATE"


class CitationOccurrenceTargetSchema(_FrozenQualityContract):
    schema_version: Literal["citation_occurrence_target_v1"] = (
        "citation_occurrence_target_v1"
    )
    occurrence_ref: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    validation_subject_hash: Sha256Hex
    citation_alias: NonEmptyStr
    source_span_ref: NonEmptyStr
    occurrence_hash: Sha256Hex


class CitationTargetRefSchema(_FrozenQualityContract):
    schema_version: Literal["citation_target_ref_v1"] = "citation_target_ref_v1"
    citation_target_ref: NonEmptyStr
    exact_evidence_ref: NonEmptyStr
    target_hash: Sha256Hex


class CitationIntegrityPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["citation_integrity_payload_v1"] = (
        "citation_integrity_payload_v1"
    )
    occurrence_ref: NonEmptyStr
    binding_state: CitationOccurrenceBindingState
    citation_target_ref: CitationTargetRefSchema | None = None
    reason_codes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _binding_shape(self) -> "CitationIntegrityPayloadSchema":
        if (
            self.binding_state is CitationOccurrenceBindingState.VALID
            and self.citation_target_ref is None
        ):
            raise ValueError("VALID citation occurrence requires exact CitationTargetRef")
        if (
            self.binding_state is CitationOccurrenceBindingState.INVALID
            and self.citation_target_ref is not None
        ):
            raise ValueError("INVALID citation occurrence cannot claim a usable target")
        values = tuple(self.reason_codes)
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("citation integrity reason_codes must be canonical ordered-unique")
        return self


class CitationClaimAssociationState(str, Enum):
    ASSOCIATED = "ASSOCIATED"
    NO_APPLICABLE_CLAIM = "NO_APPLICABLE_CLAIM"
    INDETERMINATE = "INDETERMINATE"


class CitationClaimAssociationSchema(_FrozenQualityContract):
    schema_version: Literal["citation_claim_association_v1"] = (
        "citation_claim_association_v1"
    )
    association_ref: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    occurrence_ref: NonEmptyStr
    candidate_claim_target_ids: tuple[NonEmptyStr, ...] = ()
    associated_claim_target_ids: tuple[NonEmptyStr, ...] = ()
    association_state: CitationClaimAssociationState
    materializer_id: NonEmptyStr
    materializer_version: NonEmptyStr
    association_hash: Sha256Hex

    @model_validator(mode="after")
    def _association_shape(self) -> "CitationClaimAssociationSchema":
        for name in ("candidate_claim_target_ids", "associated_claim_target_ids"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if self.association_state is CitationClaimAssociationState.ASSOCIATED:
            if not self.associated_claim_target_ids:
                raise ValueError("ASSOCIATED citation requires at least one ClaimTarget")
            if not set(self.associated_claim_target_ids) <= set(
                self.candidate_claim_target_ids
            ):
                raise ValueError("associated ClaimTargets escape the frozen candidate set")
        elif self.associated_claim_target_ids:
            raise ValueError(
                "NO_APPLICABLE_CLAIM/INDETERMINATE cannot carry associated ClaimTargets"
            )
        return self


class CitationAssociationEvidenceSchema(_FrozenQualityContract):
    """Committed semantic observation, bound to the exact frozen model input."""
    schema_version: Literal["citation_association_evidence_v1"] = "citation_association_evidence_v1"
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    occurrence: CitationOccurrenceTargetSchema
    association: CitationClaimAssociationSchema
    input_hash: Sha256Hex
    input_receipt_ref: NonEmptyStr
    policy_id: Literal["citation-claim-association"] = "citation-claim-association"
    policy_version: Literal["v1"] = "v1"
    evidence_hash: Sha256Hex

    @model_validator(mode="after")
    def _evidence_identity(self):
        occurrence, association = self.occurrence, self.association
        if (occurrence.validation_subject_hash != self.subject_hash
                or association.artifact_state_ref != occurrence.artifact_state_ref
                or association.occurrence_ref != occurrence.occurrence_ref
                or association.materializer_id != self.policy_id
                or association.materializer_version != self.policy_version):
            raise ValueError("association evidence source/policy binding differs")
        material = association.model_dump(mode="json")
        material.pop("association_ref"); material.pop("association_hash")
        digest = canonical_sha256({"association": material, "input_hash": self.input_hash})
        if association.association_hash != digest or association.association_ref != "citation-association://sha256/" + digest:
            raise ValueError("association evidence association identity/hash differs")
        payload = self.model_dump(mode="json"); payload.pop("evidence_hash")
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("association evidence hash differs")
        return self


class CitationGroundingUncertaintyPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["citation_grounding_uncertainty_v1"] = "citation_grounding_uncertainty_v1"
    uncertainty_kind: Literal["ASSOCIATION_INDETERMINATE", "PREREQUISITE_INDETERMINATE"]
    semantic_state: Literal["INDETERMINATE"] = "INDETERMINATE"
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    artifact_state_ref: ArtifactStateRefSchema
    occurrence: CitationOccurrenceTargetSchema
    association_evidence_ref: ValidationIntermediateRefSchema | None = None
    prerequisite_fact_ref: ValidationFactRefSchema | None = None
    prerequisite_occurrence_ref: ValidationIntermediateRefSchema | None = None
    policy_id: NonEmptyStr
    policy_version: NonEmptyStr
    basis_hash: Sha256Hex

    @model_validator(mode="after")
    def _uncertainty_binding(self):
        if (self.occurrence.artifact_state_ref != self.artifact_state_ref
                or self.occurrence.validation_subject_hash != self.subject_hash):
            raise ValueError("uncertainty occurrence Artifact/subject differs")
        association = self.uncertainty_kind == "ASSOCIATION_INDETERMINATE"
        if association != (self.association_evidence_ref is not None) or association == (self.prerequisite_fact_ref is not None):
            raise ValueError("uncertainty requires exactly its own typed evidence branch")
        ref = self.association_evidence_ref if association else self.prerequisite_fact_ref
        if ref.validation_run_id != self.validation_run_id:
            raise ValueError("uncertainty evidence belongs to another ValidationRun")
        if association and self.prerequisite_occurrence_ref is not None:
            raise ValueError("association uncertainty cannot carry prerequisite occurrence basis")
        if not association and (self.prerequisite_occurrence_ref is None
                or self.prerequisite_occurrence_ref.validation_run_id != self.validation_run_id):
            raise ValueError("prerequisite uncertainty requires committed exact occurrence basis")
        if not association and ref.subject_hash != self.subject_hash:
            raise ValueError("prerequisite evidence belongs to another subject")
        return self


class CitationGroundingGroupKind(str, Enum):
    SINGLE = "SINGLE"
    JOINT = "JOINT"


class CitationGroundingTargetSchema(_FrozenQualityContract):
    schema_version: Literal["citation_grounding_target_v1"] = (
        "citation_grounding_target_v1"
    )
    grounding_target_ref: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    validation_subject_hash: Sha256Hex
    claim_target_id: NonEmptyStr
    group_kind: CitationGroundingGroupKind
    association_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    occurrence_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    citation_target_refs: tuple[CitationTargetRefSchema, ...] = Field(min_length=1)
    individual_evidence_target_set_refs: tuple[EvidenceTargetSetRefSchema, ...] = (
        Field(min_length=1)
    )
    joint_evidence_target_set_ref: EvidenceTargetSetRefSchema
    target_hash: Sha256Hex

    @model_validator(mode="after")
    def _grounding_shape(self) -> "CitationGroundingTargetSchema":
        for name in ("association_refs", "occurrence_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if len(self.occurrence_refs) != len(self.citation_target_refs):
            raise ValueError("citation occurrence/target cardinality mismatch")
        if len(self.occurrence_refs) != len(self.individual_evidence_target_set_refs):
            raise ValueError("citation occurrence/Evidence target-set cardinality mismatch")
        if self.group_kind is CitationGroundingGroupKind.SINGLE and len(
            self.occurrence_refs
        ) != 1:
            raise ValueError("SINGLE grounding group requires exactly one occurrence")
        if self.group_kind is CitationGroundingGroupKind.JOINT and len(
            self.occurrence_refs
        ) < 2:
            raise ValueError("JOINT grounding group requires at least two occurrences")
        return self


class CitationGroundingRelation(str, Enum):
    GROUNDED = "GROUNDED"
    PARTIALLY_GROUNDED = "PARTIALLY_GROUNDED"
    NOT_GROUNDED = "NOT_GROUNDED"
    CONTRADICTED = "CONTRADICTED"
    INDETERMINATE = "INDETERMINATE"


class CitationGroundingPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["citation_grounding_payload_v1", "citation_grounding_payload_v2"] = (
        "citation_grounding_payload_v1"
    )
    grounding_target_ref: NonEmptyStr
    claim_target_id: NonEmptyStr
    occurrence_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    citation_target_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    joint_support_ref: ClaimEvidenceSupportRefSchema | None = None
    member_support_refs: tuple[ClaimEvidenceSupportRefSchema, ...] = ()
    grounding_relation: CitationGroundingRelation
    observation_codes: tuple[NonEmptyStr, ...] = ()
    related_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _canonical_projection(self) -> "CitationGroundingPayloadSchema":
        # v2 source refs are an occurrence-aligned mapping and may repeat.
        # Historical v1 records retain their original ordered-unique validation.
        names = ("occurrence_refs", "observation_codes", "related_refs")
        if self.schema_version == "citation_grounding_payload_v1":
            names += ("citation_target_refs",)
        for name in names:
            values = tuple(getattr(self, name))
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if len(self.occurrence_refs) != len(self.citation_target_refs):
            raise ValueError("grounding occurrence/target cardinality mismatch")
        if self.joint_support_ref is None:
            raise ValueError(
                "materialized Citation Grounding semantic result requires joint support ref"
            )
        return self



# ---------------------------------------------------------------------------
# Requirement / Relevancy / Coverage shared typed contracts
# ---------------------------------------------------------------------------


class RequirementDomainCompletenessState(str, Enum):
    COMPLETE = "COMPLETE"
    WIDEN_REQUIRED = "WIDEN_REQUIRED"
    UNRESOLVED = "UNRESOLVED"


class RequirementAdherenceObservation(str, Enum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    INDETERMINATE = "INDETERMINATE"


class SemanticPresenceObservation(str, Enum):
    MATCH_OBSERVED = "MATCH_OBSERVED"
    NO_MATCH_AFTER_COMPLETE_DOMAIN = "NO_MATCH_AFTER_COMPLETE_DOMAIN"
    INDETERMINATE = "INDETERMINATE"


class CoverageRelation(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    PARTIAL = "PARTIAL"
    INDETERMINATE = "INDETERMINATE"


class SharedRequirementArtifactCandidateDomainSchema(_FrozenQualityContract):
    schema_version: Literal[
        "shared_requirement_artifact_candidate_domain_v1"
    ] = "shared_requirement_artifact_candidate_domain_v1"
    candidate_domain_ref: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    validation_scope: NonEmptyStr
    subject_selector_ref: NonEmptyStr
    complete_applicable_domain_refs: tuple[NonEmptyStr, ...]
    prioritized_candidate_refs: tuple[NonEmptyStr, ...]
    consumer_requirements: tuple[NonEmptyStr, ...]
    resolver_id: NonEmptyStr
    resolver_version: NonEmptyStr
    selection_policy_version: NonEmptyStr
    completeness_state: RequirementDomainCompletenessState
    provenance_refs: tuple[NonEmptyStr, ...] = ()
    domain_hash: Sha256Hex

    @model_validator(mode="after")
    def _domain_integrity(self) -> "SharedRequirementArtifactCandidateDomainSchema":
        for name in (
            "complete_applicable_domain_refs",
            "prioritized_candidate_refs",
            "consumer_requirements",
            "provenance_refs",
        ):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if not set(self.prioritized_candidate_refs) <= set(
            self.complete_applicable_domain_refs
        ):
            raise ValueError("prioritized candidates escape complete applicable domain")
        payload = self.model_dump(mode="json")
        payload.pop("candidate_domain_ref", None)
        payload.pop("domain_hash", None)
        expected = canonical_sha256(payload)
        if self.domain_hash != expected:
            raise ValueError("shared Requirement candidate domain hash mismatch")
        if self.candidate_domain_ref != (
            "requirement-candidate-domain://sha256/" + expected
        ):
            raise ValueError("shared Requirement candidate domain ref mismatch")
        return self


class ArtifactResponseTargetSetRefSchema(_FrozenQualityContract):
    schema_version: Literal["artifact_response_target_set_ref_v1"] = (
        "artifact_response_target_set_ref_v1"
    )
    target_set_ref: NonEmptyStr
    requirement_ref: RequirementRefSchema
    requirement_id: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    candidate_domain_ref: NonEmptyStr
    candidate_domain_hash: Sha256Hex
    response_unit_refs: tuple[NonEmptyStr, ...]
    projection_contract_version: NonEmptyStr
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _target_integrity(self) -> "ArtifactResponseTargetSetRefSchema":
        values = tuple(self.response_unit_refs)
        if values != tuple(sorted(set(values))):
            raise ValueError("Relevancy response_unit_refs must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("target_set_ref", None)
        payload.pop("projection_hash", None)
        expected = canonical_sha256(payload)
        if self.projection_hash != expected:
            raise ValueError("Relevancy target-set hash mismatch")
        if self.target_set_ref != (
            "artifact-response-target-set://sha256/" + expected
        ):
            raise ValueError("Relevancy target-set ref mismatch")
        return self


class CoverageRequirementTargetSchema(_FrozenQualityContract):
    schema_version: Literal["coverage_requirement_target_v1"] = (
        "coverage_requirement_target_v1"
    )
    coverage_target_ref: NonEmptyStr
    requirement_ref: RequirementRefSchema
    requirement_id: NonEmptyStr
    source_requirement_group_ref: NonEmptyStr | None = None
    artifact_state_ref: ArtifactStateRefSchema
    validation_scope: NonEmptyStr
    target_kind: NonEmptyStr
    canonical_subject_selector: NonEmptyStr
    normalized_presence_constraint: NonEmptyStr
    required_count: int | None = Field(default=None, ge=1)
    materializer_id: NonEmptyStr
    materializer_version: NonEmptyStr
    provenance_refs: tuple[NonEmptyStr, ...] = ()
    target_hash: Sha256Hex

    @model_validator(mode="after")
    def _target_integrity(self) -> "CoverageRequirementTargetSchema":
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("Coverage target provenance_refs must be canonical")
        payload = self.model_dump(mode="json")
        payload.pop("coverage_target_ref", None)
        payload.pop("target_hash", None)
        expected = canonical_sha256(payload)
        if self.target_hash != expected:
            raise ValueError("CoverageRequirementTarget hash mismatch")
        if self.coverage_target_ref != (
            "coverage-requirement-target://sha256/" + expected
        ):
            raise ValueError("CoverageRequirementTarget ref mismatch")
        return self


class ArtifactCoverageMatchTargetSetRefSchema(_FrozenQualityContract):
    schema_version: Literal["artifact_coverage_match_target_set_ref_v1"] = (
        "artifact_coverage_match_target_set_ref_v1"
    )
    match_target_set_ref: NonEmptyStr
    coverage_target_ref: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    candidate_unit_refs: tuple[NonEmptyStr, ...]
    matched_unit_refs: tuple[NonEmptyStr, ...]
    match_state: NonEmptyStr
    resolver_id: NonEmptyStr
    resolver_version: NonEmptyStr
    complete_domain: bool
    target_hash: Sha256Hex
    provenance_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _match_integrity(self) -> "ArtifactCoverageMatchTargetSetRefSchema":
        for name in ("candidate_unit_refs", "matched_unit_refs", "provenance_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if not set(self.matched_unit_refs) <= set(self.candidate_unit_refs):
            raise ValueError("Coverage matches escape candidate domain")
        payload = self.model_dump(mode="json")
        payload.pop("match_target_set_ref", None)
        payload.pop("target_hash", None)
        expected = canonical_sha256(payload)
        if self.target_hash != expected:
            raise ValueError("ArtifactCoverageMatchTargetSet hash mismatch")
        if self.match_target_set_ref != (
            "artifact-coverage-match-target-set://sha256/" + expected
        ):
            raise ValueError("ArtifactCoverageMatchTargetSet ref mismatch")
        return self


class RequirementArtifactAssessmentSchema(_FrozenQualityContract):
    schema_version: Literal["requirement_artifact_assessment_v1"] = (
        "requirement_artifact_assessment_v1"
    )
    assessment_ref: NonEmptyStr
    assessment_contract_version: NonEmptyStr
    validation_run_id: NonEmptyStr
    requirement_ref: RequirementRefSchema
    requirement_id: NonEmptyStr
    coverage_target_ref: NonEmptyStr | None = None
    artifact_state_ref: ArtifactStateRefSchema
    shared_candidate_domain_ref: NonEmptyStr
    shared_candidate_domain_hash: Sha256Hex
    relevancy_projection_ref: NonEmptyStr | None = None
    relevancy_projection_hash: Sha256Hex | None = None
    coverage_projection_ref: NonEmptyStr | None = None
    coverage_projection_hash: Sha256Hex | None = None
    adherence_observation: RequirementAdherenceObservation | None = None
    semantic_presence_observation: SemanticPresenceObservation | None = None
    response_refs: tuple[NonEmptyStr, ...] = ()
    presence_refs: tuple[NonEmptyStr, ...] = ()
    observed_count: int | None = Field(default=None, ge=0)
    domain_completeness_state: RequirementDomainCompletenessState
    ambiguity_codes: tuple[NonEmptyStr, ...] = ()
    reason_codes: tuple[NonEmptyStr, ...] = ()
    physical_model_call_ref: NonEmptyStr | None = None
    logical_consumer_binding_refs: tuple[NonEmptyStr, ...] = ()
    provenance_refs: tuple[NonEmptyStr, ...] = ()
    assessment_hash: Sha256Hex

    @model_validator(mode="after")
    def _assessment_integrity(self) -> "RequirementArtifactAssessmentSchema":
        if (self.relevancy_projection_ref is None) != (
            self.relevancy_projection_hash is None
        ):
            raise ValueError("Relevancy projection ref/hash must be paired")
        if (self.coverage_projection_ref is None) != (
            self.coverage_projection_hash is None
        ):
            raise ValueError("Coverage projection ref/hash must be paired")
        if (
            self.semantic_presence_observation
            is SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN
            and self.domain_completeness_state
            is not RequirementDomainCompletenessState.COMPLETE
        ):
            raise ValueError(
                "semantic no-match may be positive only after COMPLETE domain"
            )
        for name in (
            "response_refs",
            "presence_refs",
            "ambiguity_codes",
            "reason_codes",
            "logical_consumer_binding_refs",
            "provenance_refs",
        ):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("assessment_ref", None)
        payload.pop("assessment_hash", None)
        expected = canonical_sha256(payload)
        if self.assessment_hash != expected:
            raise ValueError("RequirementArtifactAssessment hash mismatch")
        if self.assessment_ref != (
            "requirement-artifact-assessment://sha256/" + expected
        ):
            raise ValueError("RequirementArtifactAssessment ref mismatch")
        return self


class RequirementAdherencePayloadSchema(_FrozenQualityContract):
    schema_version: Literal["requirement_adherence_payload_v1"] = (
        "requirement_adherence_payload_v1"
    )
    requirement_ref: RequirementRefSchema
    requirement_id: NonEmptyStr
    artifact_response_target_set_ref: NonEmptyStr
    requirement_artifact_assessment_ref: NonEmptyStr
    adherence_observation: RequirementAdherenceObservation
    observation_codes: tuple[NonEmptyStr, ...] = ()
    related_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _canonical_payload(self) -> "RequirementAdherencePayloadSchema":
        for name in ("observation_codes", "related_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        return self


class CoverageCompletenessPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["coverage_completeness_payload_v1"] = (
        "coverage_completeness_payload_v1"
    )
    coverage_target_ref: NonEmptyStr
    artifact_match_target_set_ref: NonEmptyStr | None = None
    source_requirement_group_ref: NonEmptyStr | None = None
    coverage_relation: CoverageRelation
    observed_count: int | None = Field(default=None, ge=0)
    required_count: int | None = Field(default=None, ge=1)
    observation_codes: tuple[NonEmptyStr, ...] = ()
    related_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _canonical_payload(self) -> "CoverageCompletenessPayloadSchema":
        for name in ("observation_codes", "related_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        return self


# ---------------------------------------------------------------------------
# Consistency typed contracts
# ---------------------------------------------------------------------------


class ConsistencyRelationShape(str, Enum):
    BINARY = "BINARY"
    COMPOSITE = "COMPOSITE"


class ConsistencyRelationMateriality(str, Enum):
    MATERIAL_RELATION = "MATERIAL_RELATION"
    NO_MATERIAL_RELATION = "NO_MATERIAL_RELATION"
    INDETERMINATE = "INDETERMINATE"


class ArtifactConsistencyRelation(str, Enum):
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    INDETERMINATE = "INDETERMINATE"


class FormalExecutabilityState(str, Enum):
    EXECUTABLE = "EXECUTABLE"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class ConsistencyPlanConformanceState(str, Enum):
    CONFORMING = "CONFORMING"
    NONCONFORMING = "NONCONFORMING"
    INDETERMINATE = "INDETERMINATE"


class ConsistencyAssertionSchema(_FrozenQualityContract):
    schema_version: Literal["consistency_assertion_v1"] = (
        "consistency_assertion_v1"
    )
    assertion_ref: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    validation_subject_hash: Sha256Hex
    source_unit_ref: NonEmptyStr
    canonical_text: NonEmptyStr
    typed_value: float | int | str | None = None
    value_unit: NonEmptyStr | None = None
    assertion_hash: Sha256Hex

    @model_validator(mode="after")
    def _assertion_integrity(self) -> "ConsistencyAssertionSchema":
        payload = self.model_dump(mode="json")
        payload.pop("assertion_ref", None)
        payload.pop("assertion_hash", None)
        expected = canonical_sha256(payload)
        if self.assertion_hash != expected:
            raise ValueError("ConsistencyAssertion hash mismatch")
        if self.assertion_ref != "consistency-assertion://sha256/" + expected:
            raise ValueError("ConsistencyAssertion ref mismatch")
        return self


class ConsistencyDiscoveryPlanSchema(_FrozenQualityContract):
    schema_version: Literal["consistency_discovery_plan_v1"] = (
        "consistency_discovery_plan_v1"
    )
    plan_ref: NonEmptyStr
    validation_run_id: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    validation_subject_hash: Sha256Hex
    assertion_refs: tuple[NonEmptyStr, ...]
    required_relation_keys: tuple[NonEmptyStr, ...]
    discovery_mode: NonEmptyStr
    profile_id: NonEmptyStr
    profile_version: NonEmptyStr
    execution_profile_hash: Sha256Hex
    procedure_contract_version: NonEmptyStr
    procedure_input_hash: Sha256Hex | None = Field(default=None, exclude_if=lambda value: value is None)
    runtime_procedure_input_hash: Sha256Hex | None = Field(default=None, exclude_if=lambda value: value is None)
    required_logical_obligation_refs: tuple[NonEmptyStr, ...] = Field(default=(), exclude_if=lambda value: not value)
    plan_hash: Sha256Hex

    @model_validator(mode="after")
    def _plan_integrity(self) -> "ConsistencyDiscoveryPlanSchema":
        for name in ("assertion_refs", "required_relation_keys", "required_logical_obligation_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("plan_ref", None)
        payload.pop("plan_hash", None)
        expected = canonical_sha256(payload)
        if self.plan_hash != expected:
            raise ValueError("ConsistencyDiscoveryPlan hash mismatch")
        if self.plan_ref != "consistency-discovery-plan://sha256/" + expected:
            raise ValueError("ConsistencyDiscoveryPlan ref mismatch")
        return self


class ConsistencyCandidateDiscoveryResultSchema(_FrozenQualityContract):
    schema_version: Literal["consistency_candidate_discovery_result_v1"] = (
        "consistency_candidate_discovery_result_v1"
    )
    discovery_ref: NonEmptyStr
    plan_ref: NonEmptyStr
    candidate_relation_keys: tuple[NonEmptyStr, ...]
    candidate_member_refs: tuple[tuple[NonEmptyStr, ...], ...]
    complete_required_plan_accounting: bool
    discovery_profile_version: NonEmptyStr
    provenance_refs: tuple[NonEmptyStr, ...] = ()
    accounted_logical_obligation_refs: tuple[NonEmptyStr, ...] = Field(default=(), exclude_if=lambda value: not value)
    discovery_hash: Sha256Hex

    @model_validator(mode="after")
    def _discovery_integrity(self) -> "ConsistencyCandidateDiscoveryResultSchema":
        if self.accounted_logical_obligation_refs != tuple(sorted(set(self.accounted_logical_obligation_refs))):
            raise ValueError("logical obligation accounting must be canonical ordered-unique")
        if self.candidate_relation_keys != tuple(
            sorted(set(self.candidate_relation_keys))
        ):
            raise ValueError("candidate relation keys must be canonical")
        normalized_members = tuple(
            tuple(sorted(set(members))) for members in self.candidate_member_refs
        )
        if normalized_members != self.candidate_member_refs:
            raise ValueError("candidate member refs must be canonical")
        if len(self.candidate_relation_keys) != len(self.candidate_member_refs):
            raise ValueError("candidate relation/member cardinality mismatch")
        if self.provenance_refs != tuple(sorted(set(self.provenance_refs))):
            raise ValueError("discovery provenance_refs must be canonical")
        payload = self.model_dump(mode="json")
        payload.pop("discovery_ref", None)
        payload.pop("discovery_hash", None)
        expected = canonical_sha256(payload)
        if self.discovery_hash != expected:
            raise ValueError("Consistency discovery hash mismatch")
        if self.discovery_ref != "consistency-discovery://sha256/" + expected:
            raise ValueError("Consistency discovery ref mismatch")
        return self


class FormalRelationCandidateSchema(_FrozenQualityContract):
    schema_version: Literal["formal_relation_candidate_v1"] = (
        "formal_relation_candidate_v1"
    )
    formal_candidate_ref: NonEmptyStr
    lhs_assertion_ref: NonEmptyStr
    predicate: Literal["EQ", "NE", "LT", "LE", "GT", "GE"]
    rhs_assertion_ref: NonEmptyStr
    precision_tolerance: float | None = Field(default=None, ge=0)
    formal_contract_version: NonEmptyStr
    formal_candidate_hash: Sha256Hex

    @model_validator(mode="after")
    def _formal_integrity(self) -> "FormalRelationCandidateSchema":
        payload = self.model_dump(mode="json")
        payload.pop("formal_candidate_ref", None)
        payload.pop("formal_candidate_hash", None)
        expected = canonical_sha256(payload)
        if self.formal_candidate_hash != expected:
            raise ValueError("FormalRelationCandidate hash mismatch")
        if self.formal_candidate_ref != (
            "formal-relation-candidate://sha256/" + expected
        ):
            raise ValueError("FormalRelationCandidate ref mismatch")
        return self


class SemanticRelationBindingSchema(_FrozenQualityContract):
    schema_version: Literal["semantic_relation_binding_v1"] = (
        "semantic_relation_binding_v1"
    )
    binding_ref: NonEmptyStr
    semantic_contract_version: NonEmptyStr
    validation_run_id: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    validation_scope: NonEmptyStr
    scope_subject_ref: NonEmptyStr
    member_assertion_refs: tuple[NonEmptyStr, ...] = Field(min_length=2)
    source_context_refs: tuple[NonEmptyStr, ...] = ()
    semantic_relation_description: NonEmptyStr
    role_bindings: tuple[NonEmptyStr, ...] = ()
    scope_condition_bindings: tuple[NonEmptyStr, ...] = ()
    relation_shape: ConsistencyRelationShape
    relation_materiality: ConsistencyRelationMateriality
    formal_relation_candidate: FormalRelationCandidateSchema | None = None
    ambiguity_codes: tuple[NonEmptyStr, ...] = ()
    reason_codes: tuple[NonEmptyStr, ...] = ()
    physical_model_call_ref: NonEmptyStr | None = None
    logical_consumer_binding_refs: tuple[NonEmptyStr, ...] = ()
    provenance_refs: tuple[NonEmptyStr, ...] = ()
    binding_hash: Sha256Hex

    @model_validator(mode="after")
    def _binding_integrity(self) -> "SemanticRelationBindingSchema":
        for name in (
            "member_assertion_refs",
            "source_context_refs",
            "role_bindings",
            "scope_condition_bindings",
            "ambiguity_codes",
            "reason_codes",
            "logical_consumer_binding_refs",
            "provenance_refs",
        ):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if self.relation_shape is ConsistencyRelationShape.BINARY:
            if len(self.member_assertion_refs) != 2:
                raise ValueError("BINARY relation requires exactly two assertions")
        payload = self.model_dump(mode="json")
        payload.pop("binding_ref", None)
        payload.pop("binding_hash", None)
        expected = canonical_sha256(payload)
        if self.binding_hash != expected:
            raise ValueError("SemanticRelationBinding hash mismatch")
        if self.binding_ref != "semantic-relation-binding://sha256/" + expected:
            raise ValueError("SemanticRelationBinding ref mismatch")
        return self


class FormalExecutabilityResultSchema(_FrozenQualityContract):
    schema_version: Literal["formal_executability_result_v1"] = (
        "formal_executability_result_v1"
    )
    result_ref: NonEmptyStr
    binding_ref: NonEmptyStr
    formal_candidate_ref: NonEmptyStr | None = None
    state: FormalExecutabilityState
    reason_codes: tuple[NonEmptyStr, ...] = ()
    result_hash: Sha256Hex

    @model_validator(mode="after")
    def _result_integrity(self) -> "FormalExecutabilityResultSchema":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("formal executability reasons must be canonical")
        if (
            self.state is FormalExecutabilityState.EXECUTABLE
            and self.formal_candidate_ref is None
        ):
            raise ValueError("EXECUTABLE requires exact formal candidate")
        payload = self.model_dump(mode="json")
        payload.pop("result_ref", None)
        payload.pop("result_hash", None)
        expected = canonical_sha256(payload)
        if self.result_hash != expected:
            raise ValueError("FormalExecutabilityResult hash mismatch")
        if self.result_ref != "formal-executability://sha256/" + expected:
            raise ValueError("FormalExecutabilityResult ref mismatch")
        return self


class ConsistencyPlanConformanceResultSchema(_FrozenQualityContract):
    schema_version: Literal["consistency_plan_conformance_result_v1"] = (
        "consistency_plan_conformance_result_v1"
    )
    result_ref: NonEmptyStr
    plan_ref: NonEmptyStr
    discovery_ref: NonEmptyStr
    required_relation_keys: tuple[NonEmptyStr, ...]
    accounted_relation_keys: tuple[NonEmptyStr, ...]
    state: ConsistencyPlanConformanceState
    missing_relation_keys: tuple[NonEmptyStr, ...] = ()
    result_hash: Sha256Hex

    @model_validator(mode="after")
    def _conformance_integrity(self) -> "ConsistencyPlanConformanceResultSchema":
        for name in (
            "required_relation_keys",
            "accounted_relation_keys",
            "missing_relation_keys",
        ):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("result_ref", None)
        payload.pop("result_hash", None)
        expected = canonical_sha256(payload)
        if self.result_hash != expected:
            raise ValueError("ConsistencyPlanConformanceResult hash mismatch")
        if self.result_ref != "consistency-plan-conformance://sha256/" + expected:
            raise ValueError("ConsistencyPlanConformanceResult ref mismatch")
        return self


class ExactConsistencyEvaluationResultSchema(_FrozenQualityContract):
    schema_version: Literal["exact_consistency_evaluation_result_v1"] = (
        "exact_consistency_evaluation_result_v1"
    )
    evaluation_ref: NonEmptyStr
    binding_ref: NonEmptyStr
    formal_candidate_ref: NonEmptyStr
    relation: ArtifactConsistencyRelation
    reason_codes: tuple[NonEmptyStr, ...] = ()
    evaluation_hash: Sha256Hex

    @model_validator(mode="after")
    def _evaluation_integrity(self) -> "ExactConsistencyEvaluationResultSchema":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("exact consistency reasons must be canonical")
        payload = self.model_dump(mode="json")
        payload.pop("evaluation_ref", None)
        payload.pop("evaluation_hash", None)
        expected = canonical_sha256(payload)
        if self.evaluation_hash != expected:
            raise ValueError("ExactConsistencyEvaluationResult hash mismatch")
        if self.evaluation_ref != "exact-consistency-evaluation://sha256/" + expected:
            raise ValueError("ExactConsistencyEvaluationResult ref mismatch")
        return self


class SemanticConsistencyEvaluationResultSchema(_FrozenQualityContract):
    schema_version: Literal["semantic_consistency_evaluation_result_v1"] = (
        "semantic_consistency_evaluation_result_v1"
    )
    evaluation_ref: NonEmptyStr
    binding_ref: NonEmptyStr
    relation: ArtifactConsistencyRelation
    observation_codes: tuple[NonEmptyStr, ...] = ()
    evaluation_hash: Sha256Hex

    @model_validator(mode="after")
    def _evaluation_integrity(self) -> "SemanticConsistencyEvaluationResultSchema":
        if self.observation_codes != tuple(sorted(set(self.observation_codes))):
            raise ValueError("semantic consistency observation_codes must be canonical")
        payload = self.model_dump(mode="json")
        payload.pop("evaluation_ref", None)
        payload.pop("evaluation_hash", None)
        expected = canonical_sha256(payload)
        if self.evaluation_hash != expected:
            raise ValueError("SemanticConsistencyEvaluationResult hash mismatch")
        if self.evaluation_ref != (
            "semantic-consistency-evaluation://sha256/" + expected
        ):
            raise ValueError("SemanticConsistencyEvaluationResult ref mismatch")
        return self


class ArtifactConsistencyPayloadSchema(_FrozenQualityContract):
    schema_version: Literal["artifact_consistency_payload_v1"] = (
        "artifact_consistency_payload_v1"
    )
    plan_ref: NonEmptyStr
    plan_conformance_result_ref: NonEmptyStr
    semantic_relation_binding_ref: NonEmptyStr
    evaluation_ref: NonEmptyStr
    relation: ArtifactConsistencyRelation
    observation_codes: tuple[NonEmptyStr, ...] = ()
    related_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _canonical_payload(self) -> "ArtifactConsistencyPayloadSchema":
        for name in ("observation_codes", "related_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        return self


__all__ = [
    "ArtifactClaimCoverageUnitSchema",
    "ArtifactClaimTargetSchema",
    "ArtifactConsistencyPayloadSchema",
    "ArtifactConsistencyRelation",
    "ArtifactCoverageMatchTargetSetRefSchema",
    "ArtifactResponseTargetSetRefSchema",
    "CitationClaimAssociationSchema",
    "CitationClaimAssociationState",
    "CitationGroundingGroupKind",
    "CitationGroundingPayloadSchema",
    "CitationGroundingRelation",
    "CitationGroundingTargetSchema",
    "CitationIntegrityPayloadSchema",
    "CitationOccurrenceBindingState",
    "CitationOccurrenceTargetSchema",
    "CitationTargetRefSchema",
    "ConsistencyAssertionSchema",
    "ConsistencyCandidateDiscoveryResultSchema",
    "ConsistencyDiscoveryPlanSchema",
    "ConsistencyPlanConformanceResultSchema",
    "ConsistencyPlanConformanceState",
    "ConsistencyRelationMateriality",
    "ConsistencyRelationShape",
    "CoverageCompletenessPayloadSchema",
    "CoverageRelation",
    "CoverageRequirementTargetSchema",
    "ClaimCoverageAccountingSchema",
    "ClaimCoverageAccountingState",
    "ClaimEvidenceSupportPayloadSchema",
    "ClaimTargetAuthorityKind",
    "EvidenceTargetSetRefSchema",
    "FaithfulnessPayloadSchema",
    "FaithfulnessVerificationPayloadSchema",
    "ExactConsistencyEvaluationResultSchema",
    "FormalExecutabilityResultSchema",
    "FormalExecutabilityState",
    "FormalRelationCandidateSchema",
    "ForeignClaimTargetCandidateSchema",
    "PostWriteForeignClaimTargetRefSchema",
    "RequirementAdherenceObservation",
    "RequirementAdherencePayloadSchema",
    "RequirementArtifactAssessmentSchema",
    "RequirementDomainCompletenessState",
    "SemanticConsistencyEvaluationResultSchema",
    "SemanticPresenceObservation",
    "SemanticRelationBindingSchema",
    "SharedRequirementArtifactCandidateDomainSchema",
    "UpstreamSIF07ClaimAuthorityResolutionSchema",
    "UpstreamSIF07ClaimAuthorityState",
]
