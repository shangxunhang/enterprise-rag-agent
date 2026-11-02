"""Target Relevancy / Requirement Adherence validator."""

from __future__ import annotations

from apps.enterprise_document.schemas.post_write.foundation import (
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactResponseTargetSetRefSchema,
    RequirementAdherenceObservation,
    RequirementAdherencePayloadSchema,
    RequirementArtifactAssessmentSchema,
)
from core.hashing import canonical_sha256


FACT_CONTRACT_VERSION = "requirement_adherence_fact_v1"


class RelevancyIntegrityError(ValueError):
    pass


def _fact(
    *,
    validation_run_id: str,
    subject_hash: str,
    target_ref: str,
    semantic_state: ValidationSemanticState,
    payload: dict,
) -> ValidationFactSchema:
    identity = {
        "family": ValidationFactFamily.REQUIREMENT_ADHERENCE.value,
        "validation_run_id": validation_run_id,
        "subject_hash": subject_hash,
        "target_ref": target_ref,
        "contract_version": FACT_CONTRACT_VERSION,
        "payload": payload,
    }
    fact_id = "validation-fact:requirement-adherence:" + canonical_sha256(
        identity
    )
    base = {
        "schema_version": "validation_fact_v1",
        "contract_version": FACT_CONTRACT_VERSION,
        "fact_id": fact_id,
        "validation_run_id": validation_run_id,
        "subject_hash": subject_hash,
        "producer_validator_id": "post_write_relevancy",
        "producer_validator_version": "v1",
        "family": ValidationFactFamily.REQUIREMENT_ADHERENCE,
        "target_ref": target_ref,
        "semantic_state": semantic_state,
        "payload": payload,
    }
    return ValidationFactSchema(
        **base,
        fact_sha256=canonical_sha256(base),
    )


class RelevancyValidator:
    """Projects exact shared Requirement assessment into Relevancy truth only."""

    _STATE = {
        RequirementAdherenceObservation.SATISFIED: ValidationSemanticState.PASS,
        RequirementAdherenceObservation.VIOLATED: ValidationSemanticState.FAIL,
        RequirementAdherenceObservation.INDETERMINATE: (
            ValidationSemanticState.INDETERMINATE
        ),
    }

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        target_set: ArtifactResponseTargetSetRefSchema,
        assessment: RequirementArtifactAssessmentSchema,
    ) -> ValidationFactSchema:
        target = ArtifactResponseTargetSetRefSchema.model_validate(target_set)
        exact = RequirementArtifactAssessmentSchema.model_validate(assessment)
        if exact.validation_run_id != validation_run_id:
            raise RelevancyIntegrityError(
                "Requirement assessment belongs to another ValidationRun"
            )
        if (
            exact.requirement_ref != target.requirement_ref
            or exact.requirement_id != target.requirement_id
            or exact.artifact_state_ref != target.artifact_state_ref
            or exact.shared_candidate_domain_ref != target.candidate_domain_ref
            or exact.shared_candidate_domain_hash != target.candidate_domain_hash
            or exact.relevancy_projection_ref != target.target_set_ref
            or exact.relevancy_projection_hash != target.projection_hash
        ):
            raise RelevancyIntegrityError(
                "Relevancy assessment/target exact binding mismatch"
            )
        observation = exact.adherence_observation
        if observation is None:
            raise RelevancyIntegrityError(
                "Relevancy assessment lacks typed adherence observation"
            )
        payload = RequirementAdherencePayloadSchema(
            requirement_ref=target.requirement_ref,
            requirement_id=target.requirement_id,
            artifact_response_target_set_ref=target.target_set_ref,
            requirement_artifact_assessment_ref=exact.assessment_ref,
            adherence_observation=observation,
            observation_codes=exact.reason_codes,
            related_refs=tuple(
                sorted(
                    {
                        exact.assessment_ref,
                        target.target_set_ref,
                        *exact.response_refs,
                    }
                )
            ),
        )
        return _fact(
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            target_ref=target.target_set_ref,
            semantic_state=cls._STATE[observation],
            payload=payload.model_dump(mode="json"),
        )


__all__ = ["RelevancyIntegrityError", "RelevancyValidator"]
