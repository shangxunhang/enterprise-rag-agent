"""Shared Requirement↔Artifact semantic execution intermediates.

This module may share physical semantic work between Relevancy and semantic
Coverage, but it owns no canonical ValidationFact family.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactCoverageMatchTargetSetRefSchema,
    ArtifactResponseTargetSetRefSchema,
    CoverageRequirementTargetSchema,
    RequirementAdherenceObservation,
    RequirementArtifactAssessmentSchema,
    RequirementDomainCompletenessState,
    SemanticPresenceObservation,
    SharedRequirementArtifactCandidateDomainSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import (
    PostWriteRequirementEntrySchema,
    RequirementRefSchema,
)
from core.hashing import canonical_sha256

from .execution import ValidationIntermediateRuntime


class SharedRequirementIntegrityError(ValueError):
    pass


def _canon(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


class SharedRequirementArtifactCandidateDomainBuilder:
    PRODUCER_REF = "post-write-shared-requirement-domain:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def build(
        self,
        *,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_scope: str,
        subject_selector_ref: str,
        complete_applicable_domain_refs: Iterable[str],
        prioritized_candidate_refs: Iterable[str],
        consumer_requirements: Iterable[str],
        completeness_state: RequirementDomainCompletenessState,
        resolver_id: str = "shared-requirement-domain",
        resolver_version: str = "v1",
        selection_policy_version: str = "accuracy-first-v1",
        provenance_refs: Iterable[str] = (),
    ) -> SharedRequirementArtifactCandidateDomainSchema:
        complete = _canon(complete_applicable_domain_refs)
        prioritized = _canon(prioritized_candidate_refs)
        consumers = _canon(consumer_requirements)
        if not set(prioritized) <= set(complete):
            raise SharedRequirementIntegrityError(
                "prioritized Requirement candidates escape complete domain"
            )
        if completeness_state is RequirementDomainCompletenessState.COMPLETE:
            if not complete:
                raise SharedRequirementIntegrityError(
                    "COMPLETE Requirement domain cannot be empty without explicit no-domain owner"
                )
        kwargs = {
            "artifact_state_ref": artifact_state_ref,
            "validation_scope": str(validation_scope).strip(),
            "subject_selector_ref": str(subject_selector_ref).strip(),
            "complete_applicable_domain_refs": complete,
            "prioritized_candidate_refs": prioritized,
            "consumer_requirements": consumers,
            "resolver_id": str(resolver_id).strip(),
            "resolver_version": str(resolver_version).strip(),
            "selection_policy_version": str(selection_policy_version).strip(),
            "completeness_state": completeness_state,
            "provenance_refs": _canon(provenance_refs),
        }
        placeholder = SharedRequirementArtifactCandidateDomainSchema.model_construct(
            candidate_domain_ref="pending",
            domain_hash="0" * 64,
            **kwargs,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("candidate_domain_ref", None)
        payload.pop("domain_hash", None)
        digest = canonical_sha256(payload)
        return SharedRequirementArtifactCandidateDomainSchema(
            candidate_domain_ref="requirement-candidate-domain://sha256/" + digest,
            domain_hash=digest,
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        domain: SharedRequirementArtifactCandidateDomainSchema,
    ) -> ValidationIntermediateRefSchema:
        if self.intermediate_runtime is None:
            raise SharedRequirementIntegrityError(
                "candidate-domain commit requires ValidationIntermediateRuntime"
            )
        exact = SharedRequirementArtifactCandidateDomainSchema.model_validate(domain)
        return self.intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.candidate_domain_ref,
            typed_intermediate=exact,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class RelevancyTargetProjector:
    PRODUCER_REF = "post-write-relevancy-target-projector:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def project(
        self,
        *,
        requirement_ref: RequirementRefSchema,
        requirement: PostWriteRequirementEntrySchema,
        artifact_state_ref: ArtifactStateRefSchema,
        candidate_domain: SharedRequirementArtifactCandidateDomainSchema,
        response_unit_refs: Iterable[str],
        projection_contract_version: str = "relevancy-target-projection-v1",
    ) -> ArtifactResponseTargetSetRefSchema:
        domain = SharedRequirementArtifactCandidateDomainSchema.model_validate(
            candidate_domain
        )
        if domain.artifact_state_ref != artifact_state_ref:
            raise SharedRequirementIntegrityError(
                "Relevancy candidate domain belongs to another Artifact"
            )
        response_units = _canon(response_unit_refs)
        if not set(response_units) <= set(domain.complete_applicable_domain_refs):
            raise SharedRequirementIntegrityError(
                "Relevancy target projection escapes shared candidate domain"
            )
        kwargs = {
            "requirement_ref": requirement_ref,
            "requirement_id": requirement.requirement_id,
            "artifact_state_ref": artifact_state_ref,
            "candidate_domain_ref": domain.candidate_domain_ref,
            "candidate_domain_hash": domain.domain_hash,
            "response_unit_refs": response_units,
            "projection_contract_version": str(
                projection_contract_version
            ).strip(),
        }
        placeholder = ArtifactResponseTargetSetRefSchema.model_construct(
            target_set_ref="pending",
            projection_hash="0" * 64,
            **kwargs,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("target_set_ref", None)
        payload.pop("projection_hash", None)
        digest = canonical_sha256(payload)
        return ArtifactResponseTargetSetRefSchema(
            target_set_ref="artifact-response-target-set://sha256/" + digest,
            projection_hash=digest,
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        target_set: ArtifactResponseTargetSetRefSchema,
    ) -> ValidationIntermediateRefSchema:
        if self.intermediate_runtime is None:
            raise SharedRequirementIntegrityError(
                "Relevancy target commit requires ValidationIntermediateRuntime"
            )
        exact = ArtifactResponseTargetSetRefSchema.model_validate(target_set)
        return self.intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.target_set_ref,
            typed_intermediate=exact,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class CoverageTargetProjector:
    PRODUCER_REF = "post-write-coverage-target-projector:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def project(
        self,
        *,
        coverage_target: CoverageRequirementTargetSchema,
        candidate_domain: SharedRequirementArtifactCandidateDomainSchema,
        matched_unit_refs: Iterable[str],
        match_state: str,
        complete_domain: bool,
        resolver_id: str = "coverage-target-projector",
        resolver_version: str = "v1",
        provenance_refs: Iterable[str] = (),
    ) -> ArtifactCoverageMatchTargetSetRefSchema:
        target = CoverageRequirementTargetSchema.model_validate(coverage_target)
        domain = SharedRequirementArtifactCandidateDomainSchema.model_validate(
            candidate_domain
        )
        if target.artifact_state_ref != domain.artifact_state_ref:
            raise SharedRequirementIntegrityError(
                "Coverage target/domain Artifact mismatch"
            )
        candidates = domain.complete_applicable_domain_refs
        matches = _canon(matched_unit_refs)
        if not set(matches) <= set(candidates):
            raise SharedRequirementIntegrityError(
                "Coverage matched units escape shared candidate domain"
            )
        if complete_domain and (
            domain.completeness_state
            is not RequirementDomainCompletenessState.COMPLETE
        ):
            raise SharedRequirementIntegrityError(
                "Coverage cannot claim complete domain from incomplete shared domain"
            )
        kwargs = {
            "coverage_target_ref": target.coverage_target_ref,
            "artifact_state_ref": target.artifact_state_ref,
            "candidate_unit_refs": candidates,
            "matched_unit_refs": matches,
            "match_state": str(match_state).strip(),
            "resolver_id": str(resolver_id).strip(),
            "resolver_version": str(resolver_version).strip(),
            "complete_domain": bool(complete_domain),
            "provenance_refs": _canon(provenance_refs),
        }
        placeholder = ArtifactCoverageMatchTargetSetRefSchema.model_construct(
            match_target_set_ref="pending",
            target_hash="0" * 64,
            **kwargs,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("match_target_set_ref", None)
        payload.pop("target_hash", None)
        digest = canonical_sha256(payload)
        return ArtifactCoverageMatchTargetSetRefSchema(
            match_target_set_ref=(
                "artifact-coverage-match-target-set://sha256/" + digest
            ),
            target_hash=digest,
            **kwargs,
        )

    def commit(
        self,
        *,
        validation_run_id: str,
        target_set: ArtifactCoverageMatchTargetSetRefSchema,
    ) -> ValidationIntermediateRefSchema:
        if self.intermediate_runtime is None:
            raise SharedRequirementIntegrityError(
                "Coverage target commit requires ValidationIntermediateRuntime"
            )
        exact = ArtifactCoverageMatchTargetSetRefSchema.model_validate(target_set)
        return self.intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.match_target_set_ref,
            typed_intermediate=exact,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


@dataclass(frozen=True)
class RequirementBatchConsumer:
    requirement_ref: RequirementRefSchema
    requirement: PostWriteRequirementEntrySchema
    artifact_state_ref: ArtifactStateRefSchema
    candidate_domain: SharedRequirementArtifactCandidateDomainSchema
    relevancy_target: ArtifactResponseTargetSetRefSchema | None = None
    coverage_target: CoverageRequirementTargetSchema | None = None
    coverage_projection: ArtifactCoverageMatchTargetSetRefSchema | None = None


class RequirementBatchCompatibilityGuard:
    @staticmethod
    def validate(consumers: Iterable[RequirementBatchConsumer]) -> tuple[
        RequirementBatchConsumer, ...
    ]:
        items = tuple(consumers)
        if not items:
            raise SharedRequirementIntegrityError(
                "RequirementBatch requires at least one logical consumer"
            )
        artifact_refs = {item.artifact_state_ref for item in items}
        domain_refs = {
            item.candidate_domain.candidate_domain_ref for item in items
        }
        if len(artifact_refs) != 1 or len(domain_refs) != 1:
            raise SharedRequirementIntegrityError(
                "RequirementBatch consumers require exact compatible Artifact/domain"
            )
        for item in items:
            if item.requirement.requirement_id not in (
                item.candidate_domain.consumer_requirements
            ):
                raise SharedRequirementIntegrityError(
                    "Requirement consumer absent from shared domain contract"
                )
            if (
                item.relevancy_target is not None
                and item.relevancy_target.candidate_domain_ref
                != item.candidate_domain.candidate_domain_ref
            ):
                raise SharedRequirementIntegrityError(
                    "Relevancy target binds another candidate domain"
                )
            if (
                item.coverage_projection is not None
                and item.coverage_target is None
            ):
                raise SharedRequirementIntegrityError(
                    "Coverage projection requires exact Coverage target"
                )
        return items


RequirementSemanticEvaluator = Callable[
    [RequirementBatchConsumer],
    dict,
]


class RequirementArtifactAssessmentProjector:
    PRODUCER_REF = "post-write-requirement-assessment-projector:v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    def project(
        self,
        *,
        validation_run_id: str,
        consumer: RequirementBatchConsumer,
        assessment_contract_version: str,
        adherence_observation: RequirementAdherenceObservation | None,
        semantic_presence_observation: SemanticPresenceObservation | None,
        response_refs: Iterable[str] = (),
        presence_refs: Iterable[str] = (),
        observed_count: int | None = None,
        ambiguity_codes: Iterable[str] = (),
        reason_codes: Iterable[str] = (),
        physical_model_call_ref: str | None = None,
        logical_consumer_binding_refs: Iterable[str] = (),
        provenance_refs: Iterable[str] = (),
    ) -> RequirementArtifactAssessmentSchema:
        domain = consumer.candidate_domain
        if (
            semantic_presence_observation
            is SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN
            and domain.completeness_state
            is not RequirementDomainCompletenessState.COMPLETE
        ):
            raise SharedRequirementIntegrityError(
                "semantic Coverage MISSING observation requires complete domain"
            )
        kwargs = {
            "assessment_contract_version": str(
                assessment_contract_version
            ).strip(),
            "validation_run_id": str(validation_run_id).strip(),
            "requirement_ref": consumer.requirement_ref,
            "requirement_id": consumer.requirement.requirement_id,
            "coverage_target_ref": (
                consumer.coverage_target.coverage_target_ref
                if consumer.coverage_target is not None
                else None
            ),
            "artifact_state_ref": consumer.artifact_state_ref,
            "shared_candidate_domain_ref": domain.candidate_domain_ref,
            "shared_candidate_domain_hash": domain.domain_hash,
            "relevancy_projection_ref": (
                consumer.relevancy_target.target_set_ref
                if consumer.relevancy_target is not None
                else None
            ),
            "relevancy_projection_hash": (
                consumer.relevancy_target.projection_hash
                if consumer.relevancy_target is not None
                else None
            ),
            "coverage_projection_ref": (
                consumer.coverage_projection.match_target_set_ref
                if consumer.coverage_projection is not None
                else None
            ),
            "coverage_projection_hash": (
                consumer.coverage_projection.target_hash
                if consumer.coverage_projection is not None
                else None
            ),
            "adherence_observation": adherence_observation,
            "semantic_presence_observation": semantic_presence_observation,
            "response_refs": _canon(response_refs),
            "presence_refs": _canon(presence_refs),
            "observed_count": observed_count,
            "domain_completeness_state": domain.completeness_state,
            "ambiguity_codes": _canon(ambiguity_codes),
            "reason_codes": _canon(reason_codes),
            "physical_model_call_ref": (
                str(physical_model_call_ref).strip()
                if physical_model_call_ref
                else None
            ),
            "logical_consumer_binding_refs": _canon(
                logical_consumer_binding_refs
            ),
            "provenance_refs": _canon(provenance_refs),
        }
        placeholder = RequirementArtifactAssessmentSchema.model_construct(
            assessment_ref="pending",
            assessment_hash="0" * 64,
            **kwargs,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("assessment_ref", None)
        payload.pop("assessment_hash", None)
        digest = canonical_sha256(payload)
        return RequirementArtifactAssessmentSchema(
            assessment_ref=(
                "requirement-artifact-assessment://sha256/" + digest
            ),
            assessment_hash=digest,
            **kwargs,
        )

    def commit(
        self,
        assessment: RequirementArtifactAssessmentSchema,
    ) -> ValidationIntermediateRefSchema:
        if self.intermediate_runtime is None:
            raise SharedRequirementIntegrityError(
                "Requirement assessment commit requires ValidationIntermediateRuntime"
            )
        exact = RequirementArtifactAssessmentSchema.model_validate(assessment)
        return self.intermediate_runtime.commit(
            validation_run_id=exact.validation_run_id,
            producer_ref=self.PRODUCER_REF,
            target_ref=exact.assessment_ref,
            typed_intermediate=exact,
            intermediate_kind=ValidationIntermediateKind.MODEL_DERIVATION,
        )


class RequirementBatchEvaluator:
    """Compatibility-gated shared physical semantic evaluator.

    The injected evaluator may share one physical model call in a higher runtime.
    This class validates and projects independent logical typed outputs only.
    """

    def __init__(
        self,
        *,
        projector: RequirementArtifactAssessmentProjector,
        evaluator: RequirementSemanticEvaluator,
    ) -> None:
        self.projector = projector
        self.evaluator = evaluator

    def evaluate(
        self,
        *,
        validation_run_id: str,
        consumers: Iterable[RequirementBatchConsumer],
        assessment_contract_version: str = "requirement-artifact-assessment-v1",
    ) -> tuple[RequirementArtifactAssessmentSchema, ...]:
        items = RequirementBatchCompatibilityGuard.validate(consumers)
        assessments = []
        for consumer in items:
            raw = dict(self.evaluator(consumer))
            assessment = self.projector.project(
                validation_run_id=validation_run_id,
                consumer=consumer,
                assessment_contract_version=assessment_contract_version,
                adherence_observation=raw.get("adherence_observation"),
                semantic_presence_observation=raw.get(
                    "semantic_presence_observation"
                ),
                response_refs=raw.get("response_refs", ()),
                presence_refs=raw.get("presence_refs", ()),
                observed_count=raw.get("observed_count"),
                ambiguity_codes=raw.get("ambiguity_codes", ()),
                reason_codes=raw.get("reason_codes", ()),
                physical_model_call_ref=raw.get("physical_model_call_ref"),
                logical_consumer_binding_refs=raw.get(
                    "logical_consumer_binding_refs", ()
                ),
                provenance_refs=raw.get("provenance_refs", ()),
            )
            assessments.append(assessment)
        return tuple(
            sorted(assessments, key=lambda item: item.assessment_ref)
        )


__all__ = [
    "CoverageTargetProjector",
    "RequirementArtifactAssessmentProjector",
    "RequirementBatchCompatibilityGuard",
    "RequirementBatchConsumer",
    "RequirementBatchEvaluator",
    "RequirementSemanticEvaluator",
    "RelevancyTargetProjector",
    "SharedRequirementArtifactCandidateDomainBuilder",
    "SharedRequirementIntegrityError",
]
