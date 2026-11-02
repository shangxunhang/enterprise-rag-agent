"""Main V3 projects Result V2; independent FTH-03 V2 projects support certainty."""

from __future__ import annotations

from typing import Iterable

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimEvidenceSupportRecordSchema,
    EvidenceTargetSetRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerificationResultRecordSchema, VerificationVerdict,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactClaimTargetSchema,
    FaithfulnessCertaintyObservationPayloadSchema,
    FaithfulnessCertaintyRelation,
    FaithfulnessPayloadSchema,
    FaithfulnessVerificationPayloadSchema, ClaimTargetAuthorityKind,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.services.post_write.shared_claim_support import (
    ClaimTargetSupportSubjectAdapter,
    SharedClaimEvidenceSupportEvaluator,
)
from core.hashing import canonical_sha256


class FaithfulnessIntegrityError(ValueError):
    """Faithfulness target/support/projection integrity failure."""


def _canonical_strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


class FaithfulnessEvidenceTargetResolver:
    """Completeness-preserving target resolver over exact selected Evidence refs."""

    def __init__(self, support_adapter: SharedClaimEvidenceSupportEvaluator) -> None:
        self.support_adapter = support_adapter

    def resolve(
        self,
        *,
        claim_target: ArtifactClaimTargetSchema,
        selected_evidence_refs: Iterable[str],
        structurally_excluded_refs: Iterable[str] = (),
        target_set_contract_version: str,
    ) -> EvidenceTargetSetRefSchema:
        selected = _canonical_strings(selected_evidence_refs)
        excluded = _canonical_strings(structurally_excluded_refs)
        if not set(excluded) <= set(selected):
            raise FaithfulnessIntegrityError(
                "Faithfulness exclusions escape exact Writer-visible Evidence domain"
            )
        members = tuple(item for item in selected if item not in set(excluded))
        return self.support_adapter.materialize_evidence_target_set(
            claim_target=claim_target,
            evidence_member_refs=members,
            target_set_contract_version=target_set_contract_version,
        )


class FaithfulnessProjector:
    """Deterministic FAITHFULNESS Fact projector over one exact Result V2."""

    VALIDATOR_ID = "post_write_faithfulness"
    VALIDATOR_VERSION = "v3"
    FACT_CONTRACT_VERSION = "faithfulness_verification_fact_v1"
    PROJECTION_CONTRACT_VERSION = "faithfulness-verification-projection-v1"
    PAYLOAD_CONTRACT_VERSION = "faithfulness_verification_payload_v1"

    _STATE = {
        VerificationVerdict.VERIFIED: ValidationSemanticState.PASS,
        VerificationVerdict.CONFLICTED: ValidationSemanticState.FAIL,
        VerificationVerdict.UNSUPPORTED: ValidationSemanticState.FAIL,
        VerificationVerdict.REJECTED: ValidationSemanticState.FAIL,
    }

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        claim_target: ArtifactClaimTargetSchema,
        verification_result: VerificationResultRecordSchema,
        source_snapshot_sha256: str,
        additional_rule_ids: Iterable[str] = (),
        observation_codes: Iterable[str] = (),
    ) -> ValidationFactSchema:
        target = ArtifactClaimTargetSchema.model_validate(claim_target)
        if target.validation_subject_hash != subject_hash:
            raise FaithfulnessIntegrityError(
                "Faithfulness ClaimTarget belongs to a different ValidationSubject"
            )
        if (target.authority_kind is not ClaimTargetAuthorityKind.SIF07_PROJECTION
                or target.source_claim_ref is None):
            raise FaithfulnessIntegrityError("Faithfulness V3 requires an exact SIF07 ClaimTarget")
        if (not isinstance(verification_result, VerificationResultRecordSchema)
                or verification_result.schema_version != "verification_result_record_v2"):
            raise FaithfulnessIntegrityError("Faithfulness V3 requires typed Result V2")
        result = VerificationResultRecordSchema.model_validate(verification_result.model_dump(mode="json"))
        if (result.claim_ref != target.source_claim_ref
                or result.verification_result_ref.claim_ref != target.source_claim_ref):
            raise FaithfulnessIntegrityError("Faithfulness Result belongs to a different ClaimTarget")

        rule_ids = _canonical_strings(("PW-R-FTH-01", *tuple(additional_rule_ids)))
        payload = FaithfulnessVerificationPayloadSchema(
            claim_target_id=target.claim_target_id,
            verification_result_ref=result.verification_result_ref,
            verification_verdict=result.verdict,
            source_snapshot_sha256=source_snapshot_sha256,
            rule_ids=rule_ids,
            observation_codes=_canonical_strings(observation_codes),
        )
        identity = {
            "family": ValidationFactFamily.FAITHFULNESS.value,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "claim_target_id": target.claim_target_id,
            "verification_result_ref": result.verification_result_ref.model_dump(mode="json"),
            "verification_verdict": result.verdict.value,
            "source_snapshot_sha256": source_snapshot_sha256,
            "fact_contract_version": cls.FACT_CONTRACT_VERSION,
        }
        fact_id = "validation-fact:faithfulness:" + canonical_sha256(identity)
        base = {
            "schema_version": "validation_fact_v1",
            "contract_version": cls.FACT_CONTRACT_VERSION,
            "fact_id": fact_id,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "producer_validator_id": cls.VALIDATOR_ID,
            "producer_validator_version": cls.VALIDATOR_VERSION,
            "family": ValidationFactFamily.FAITHFULNESS,
            "target_ref": target.claim_target_id,
            "semantic_state": cls._STATE[result.verdict],
            "payload": payload.model_dump(mode="json"),
        }
        return ValidationFactSchema(
            **base,
            fact_sha256=canonical_sha256(base),
        )


class FaithfulnessCertaintyProjector:
    """Project FTH-03 without becoming a second Claim/Evidence support authority."""

    VALIDATOR_ID = "post_write_faithfulness"
    VALIDATOR_VERSION = "v2"
    FACT_CONTRACT_VERSION = "faithfulness_certainty_fact_v1"

    _STATE = {
        FaithfulnessCertaintyRelation.ALIGNED: ValidationSemanticState.PASS,
        FaithfulnessCertaintyRelation.ESCALATED: ValidationSemanticState.FAIL,
        FaithfulnessCertaintyRelation.INDETERMINATE: ValidationSemanticState.INDETERMINATE,
    }

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        claim_target: ArtifactClaimTargetSchema,
        support_record: ClaimEvidenceSupportRecordSchema,
        requirement_ref: RequirementRefSchema,
        applicable_requirement_ids: Iterable[str] = (),
        certainty_relation: FaithfulnessCertaintyRelation | str,
        certainty_reason_codes: Iterable[str] = (),
        physical_support_receipt_ref: str,
        related_refs: Iterable[str] = (),
    ) -> ValidationFactSchema:
        target = ArtifactClaimTargetSchema.model_validate(claim_target)
        if target.validation_subject_hash != subject_hash:
            raise FaithfulnessIntegrityError(
                "FTH-03 ClaimTarget belongs to a different ValidationSubject"
            )
        support = ClaimEvidenceSupportRecordSchema.model_validate(support_record)
        if support.claim_support_subject_ref != ClaimTargetSupportSubjectAdapter.adapt(target):
            raise FaithfulnessIntegrityError(
                "FTH-03 SupportRecord belongs to a different ClaimTarget"
            )
        exact_requirement_ref = RequirementRefSchema.model_validate(requirement_ref)
        relation = FaithfulnessCertaintyRelation(certainty_relation)
        observation = FaithfulnessCertaintyObservationPayloadSchema(
            claim_target_id=target.claim_target_id,
            claim_evidence_support_ref=support.support_ref,
            evidence_target_set_ref=support.evidence_target_set_ref,
            requirement_ref=exact_requirement_ref,
            applicable_requirement_ids=_canonical_strings(applicable_requirement_ids),
            certainty_relation=relation,
            reason_codes=_canonical_strings(certainty_reason_codes),
            physical_support_receipt_ref=str(physical_support_receipt_ref).strip(),
        )
        payload = FaithfulnessPayloadSchema(
            claim_target_id=target.claim_target_id,
            claim_evidence_support_ref=support.support_ref,
            support_relation=support.relation,
            rule_ids=("PW-R-FTH-03",),
            observation_codes=("EXACT_SUPPORT_CALL_CERTAINTY_PROJECTION",),
            related_refs=_canonical_strings(related_refs),
            certainty_observation=observation,
        )
        identity = {
            "family": ValidationFactFamily.FAITHFULNESS.value,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "claim_target_id": target.claim_target_id,
            "support_ref": support.support_ref.model_dump(mode="json"),
            "certainty_observation": observation.model_dump(mode="json"),
            "fact_contract_version": cls.FACT_CONTRACT_VERSION,
        }
        fact_id = "validation-fact:faithfulness-certainty:" + canonical_sha256(identity)
        base = {
            "schema_version": "validation_fact_v1",
            "contract_version": cls.FACT_CONTRACT_VERSION,
            "fact_id": fact_id,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "producer_validator_id": cls.VALIDATOR_ID,
            "producer_validator_version": cls.VALIDATOR_VERSION,
            "family": ValidationFactFamily.FAITHFULNESS,
            "target_ref": target.claim_target_id,
            "semantic_state": cls._STATE[relation],
            "payload": payload.model_dump(mode="json"),
        }
        return ValidationFactSchema(**base, fact_sha256=canonical_sha256(base))


class FaithfulnessValidator:
    """Pure V3 semantic facade over the existing deterministic projector."""

    def evaluate(self, *, validation_run_id, subject_hash, claim_target,
                 verification_result, source_snapshot_sha256,
                 additional_rule_ids=(), observation_codes=()):
        return FaithfulnessProjector.project(
            validation_run_id=validation_run_id, subject_hash=subject_hash,
            claim_target=claim_target, verification_result=verification_result,
            source_snapshot_sha256=source_snapshot_sha256,
            additional_rule_ids=additional_rule_ids, observation_codes=observation_codes)


__all__ = [
    "FaithfulnessEvidenceTargetResolver",
    "FaithfulnessCertaintyProjector",
    "FaithfulnessEvidenceTargetResolver",
    "FaithfulnessIntegrityError",
    "FaithfulnessProjector",
    "FaithfulnessValidator",
]
