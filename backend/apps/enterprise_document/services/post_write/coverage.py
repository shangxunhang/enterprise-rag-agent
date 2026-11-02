"""Target Coverage / Completeness validator."""

from __future__ import annotations

from typing import Iterable, Mapping

from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
    ValidationFactFamily,
    ValidationFactSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactCoverageMatchTargetSetRefSchema,
    CoverageCompletenessPayloadSchema,
    CoverageRelation,
    CoverageRequirementTargetSchema,
    RequirementArtifactAssessmentSchema,
    SemanticPresenceObservation,
)
from apps.enterprise_document.schemas.post_write.requirement import (
    PostWriteRequirementEntrySchema,
    RequirementRefSchema,
)
from core.hashing import canonical_sha256

from .execution import ValidationIntermediateRuntime


class CoverageIntegrityError(ValueError):
    pass


def _canon(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


class CoverageRequirementTargetMaterializer:
    """Coverage-only projection over already-canonical Requirement semantics.

    target_specs must be explicit canonical presence/completeness projections.
    This class never infers a new Coverage obligation from free-form requirement
    text.
    """

    MATERIALIZER_ID = "coverage-requirement-target-materializer"
    MATERIALIZER_VERSION = "v1"

    def __init__(
        self,
        intermediate_runtime: ValidationIntermediateRuntime | None = None,
    ) -> None:
        self.intermediate_runtime = intermediate_runtime

    @classmethod
    def materialize(
        cls,
        *,
        requirement_ref: RequirementRefSchema,
        requirement: PostWriteRequirementEntrySchema,
        artifact_state_ref: ArtifactStateRefSchema,
        validation_scope: str,
        target_specs: Iterable[Mapping],
        provenance_refs: Iterable[str] = (),
    ) -> tuple[CoverageRequirementTargetSchema, ...]:
        targets = []
        for raw in target_specs:
            spec = dict(raw)
            if str(spec.get("requirement_id") or requirement.requirement_id) != (
                requirement.requirement_id
            ):
                raise CoverageIntegrityError(
                    "Coverage target spec cannot change canonical requirement_id"
                )
            kwargs = {
                "requirement_ref": requirement_ref,
                "requirement_id": requirement.requirement_id,
                "source_requirement_group_ref": spec.get(
                    "source_requirement_group_ref"
                ),
                "artifact_state_ref": artifact_state_ref,
                "validation_scope": str(validation_scope).strip(),
                "target_kind": str(spec["target_kind"]).strip(),
                "canonical_subject_selector": str(
                    spec["canonical_subject_selector"]
                ).strip(),
                "normalized_presence_constraint": str(
                    spec["normalized_presence_constraint"]
                ).strip(),
                "required_count": spec.get("required_count"),
                "materializer_id": cls.MATERIALIZER_ID,
                "materializer_version": cls.MATERIALIZER_VERSION,
                "provenance_refs": _canon(
                    (*provenance_refs, *spec.get("provenance_refs", ()))
                ),
            }
            placeholder = CoverageRequirementTargetSchema.model_construct(
                coverage_target_ref="pending",
                target_hash="0" * 64,
                **kwargs,
            )
            payload = placeholder.model_dump(mode="json")
            payload.pop("coverage_target_ref", None)
            payload.pop("target_hash", None)
            digest = canonical_sha256(payload)
            targets.append(
                CoverageRequirementTargetSchema(
                    coverage_target_ref=(
                        "coverage-requirement-target://sha256/" + digest
                    ),
                    target_hash=digest,
                    **kwargs,
                )
            )
        return tuple(sorted(targets, key=lambda item: item.coverage_target_ref))

    def commit(
        self,
        *,
        validation_run_id: str,
        target: CoverageRequirementTargetSchema,
    ) -> ValidationIntermediateRefSchema:
        if self.intermediate_runtime is None:
            raise CoverageIntegrityError(
                "Coverage target commit requires ValidationIntermediateRuntime"
            )
        exact = CoverageRequirementTargetSchema.model_validate(target)
        return self.intermediate_runtime.commit(
            validation_run_id=validation_run_id,
            producer_ref=(
                self.MATERIALIZER_ID + ":" + self.MATERIALIZER_VERSION
            ),
            target_ref=exact.coverage_target_ref,
            typed_intermediate=exact,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )


class CoverageStructuralEvaluator:
    """Deterministic structural presence/cardinality evaluator."""

    @staticmethod
    def evaluate(
        *,
        target: CoverageRequirementTargetSchema,
        match_target_set: ArtifactCoverageMatchTargetSetRefSchema,
    ) -> CoverageRelation:
        exact = CoverageRequirementTargetSchema.model_validate(target)
        matches = ArtifactCoverageMatchTargetSetRefSchema.model_validate(
            match_target_set
        )
        if (
            matches.coverage_target_ref != exact.coverage_target_ref
            or matches.artifact_state_ref != exact.artifact_state_ref
        ):
            raise CoverageIntegrityError(
                "Coverage structural target/match exact binding mismatch"
            )
        if not matches.complete_domain:
            return CoverageRelation.INDETERMINATE
        observed = len(matches.matched_unit_refs)
        required = exact.required_count or 1
        if observed == 0:
            return CoverageRelation.MISSING
        if observed < required:
            return CoverageRelation.PARTIAL
        return CoverageRelation.PRESENT


class CoverageSemanticEvaluator:
    """Projects shared semantic-presence observation without borrowing Relevancy."""

    @staticmethod
    def evaluate(
        *,
        target: CoverageRequirementTargetSchema,
        match_target_set: ArtifactCoverageMatchTargetSetRefSchema,
        assessment: RequirementArtifactAssessmentSchema,
    ) -> CoverageRelation:
        exact = CoverageRequirementTargetSchema.model_validate(target)
        matches = ArtifactCoverageMatchTargetSetRefSchema.model_validate(
            match_target_set
        )
        assessed = RequirementArtifactAssessmentSchema.model_validate(
            assessment
        )
        if (
            assessed.coverage_target_ref != exact.coverage_target_ref
            or assessed.coverage_projection_ref != matches.match_target_set_ref
            or assessed.coverage_projection_hash != matches.target_hash
            or assessed.artifact_state_ref != exact.artifact_state_ref
        ):
            raise CoverageIntegrityError(
                "semantic Coverage assessment/target exact binding mismatch"
            )
        observation = assessed.semantic_presence_observation
        if observation is SemanticPresenceObservation.INDETERMINATE:
            return CoverageRelation.INDETERMINATE
        if observation is SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN:
            if not matches.complete_domain:
                raise CoverageIntegrityError(
                    "semantic MISSING lacks complete-domain match proof"
                )
            return CoverageRelation.MISSING
        if observation is not SemanticPresenceObservation.MATCH_OBSERVED:
            raise CoverageIntegrityError(
                "semantic Coverage assessment lacks presence observation"
            )
        observed = assessed.observed_count
        if observed is None:
            observed = len(assessed.presence_refs)
        required = exact.required_count or 1
        if observed <= 0:
            raise CoverageIntegrityError(
                "MATCH_OBSERVED cannot carry zero semantic matches"
            )
        if observed < required:
            return CoverageRelation.PARTIAL
        return CoverageRelation.PRESENT


class CoverageProjector:
    VALIDATOR_ID = "post_write_coverage"
    VALIDATOR_VERSION = "v1"
    FACT_CONTRACT_VERSION = "coverage_completeness_fact_v1"

    _STATE = {
        CoverageRelation.PRESENT: ValidationSemanticState.PASS,
        CoverageRelation.MISSING: ValidationSemanticState.FAIL,
        CoverageRelation.PARTIAL: ValidationSemanticState.FAIL,
        CoverageRelation.INDETERMINATE: ValidationSemanticState.INDETERMINATE,
    }

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        target: CoverageRequirementTargetSchema,
        match_target_set: ArtifactCoverageMatchTargetSetRefSchema | None,
        relation: CoverageRelation,
        observed_count: int | None = None,
        observation_codes: Iterable[str] = (),
        related_refs: Iterable[str] = (),
    ) -> ValidationFactSchema:
        exact = CoverageRequirementTargetSchema.model_validate(target)
        match_ref = None
        derived_refs = set(_canon(related_refs))
        if match_target_set is not None:
            matches = ArtifactCoverageMatchTargetSetRefSchema.model_validate(
                match_target_set
            )
            if (
                matches.coverage_target_ref != exact.coverage_target_ref
                or matches.artifact_state_ref != exact.artifact_state_ref
            ):
                raise CoverageIntegrityError(
                    "Coverage projection target/match binding mismatch"
                )
            match_ref = matches.match_target_set_ref
            derived_refs.add(match_ref)
        payload = CoverageCompletenessPayloadSchema(
            coverage_target_ref=exact.coverage_target_ref,
            artifact_match_target_set_ref=match_ref,
            source_requirement_group_ref=exact.source_requirement_group_ref,
            coverage_relation=relation,
            observed_count=observed_count,
            required_count=exact.required_count,
            observation_codes=_canon(observation_codes),
            related_refs=tuple(sorted(derived_refs)),
        )
        identity = {
            "family": ValidationFactFamily.COVERAGE_COMPLETENESS.value,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "target_ref": exact.coverage_target_ref,
            "contract_version": cls.FACT_CONTRACT_VERSION,
            "payload": payload.model_dump(mode="json"),
        }
        fact_id = "validation-fact:coverage-completeness:" + canonical_sha256(
            identity
        )
        base = {
            "schema_version": "validation_fact_v1",
            "contract_version": cls.FACT_CONTRACT_VERSION,
            "fact_id": fact_id,
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "producer_validator_id": cls.VALIDATOR_ID,
            "producer_validator_version": cls.VALIDATOR_VERSION,
            "family": ValidationFactFamily.COVERAGE_COMPLETENESS,
            "target_ref": exact.coverage_target_ref,
            "semantic_state": cls._STATE[relation],
            "payload": payload.model_dump(mode="json"),
        }
        return ValidationFactSchema(
            **base,
            fact_sha256=canonical_sha256(base),
        )


__all__ = [
    "CoverageIntegrityError",
    "CoverageProjector",
    "CoverageRequirementTargetMaterializer",
    "CoverageSemanticEvaluator",
    "CoverageStructuralEvaluator",
]
