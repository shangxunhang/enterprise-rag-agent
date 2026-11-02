from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.post_write import (
    ArtifactScope,
    ArtifactStateRefSchema,
    CoverageRelation,
    PostWriteRequirementEntrySchema,
    RequirementAdherenceObservation,
    RequirementDomainCompletenessState,
    SemanticPresenceObservation,
    ValidationFactFamily,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactCoverageMatchTargetSetRefSchema,
    CoverageRequirementTargetSchema,
    RequirementArtifactAssessmentSchema,
)
from apps.enterprise_document.services.post_write import (
    CoverageProjector,
    CoverageRequirementTargetMaterializer,
    CoverageSemanticEvaluator,
    CoverageStructuralEvaluator,
    CoverageTargetProjector,
    RequirementArtifactAssessmentProjector,
    RequirementBatchConsumer,
    RequirementBatchEvaluator,
    RequirementMaterializer,
    RequirementStore,
    RelevancyTargetProjector,
    SharedRequirementArtifactCandidateDomainBuilder,
    SharedRequirementIntegrityError,
    ValidationIntermediateRuntime,
)

from backend.tests.test_post_write_validation_foundation import _created_run


def _coverage_fixture(
    tmp_path,
    *,
    domain_state=RequirementDomainCompletenessState.COMPLETE,
    semantic_presence=SemanticPresenceObservation.MATCH_OBSERVED,
    adherence=RequirementAdherenceObservation.SATISFIED,
    matched_refs=("unit:risk",),
    complete_domain=True,
    required_count=1,
    artifact_scope=ArtifactScope.SECTION,
):
    database, store, _lifecycle, run = _created_run(tmp_path)
    requirement = PostWriteRequirementEntrySchema(
        requirement_id="req:risk-content",
        requirement_kind="CONTENT_PRESENCE_OBLIGATION",
        statement="必须包含风险分析及应对内容",
        mandatory=True,
        constraints={"coverage_semantics": "CONTENT_TARGET"},
    )
    requirement_ref = RequirementMaterializer(
        RequirementStore(database)
    ).materialize(
        semantic_contract_version="requirements-v1",
        source_business_ref="business:test",
        source_task_ref="task:test",
        requirements=(requirement,),
    )
    artifact_ref = ArtifactStateRefSchema(
        artifact_id=(
            "document-artifact:coverage"
            if artifact_scope is ArtifactScope.DOCUMENT
            else "section-artifact:coverage"
        ),
        artifact_version=1,
        artifact_hash="b" * 64,
        artifact_scope=artifact_scope,
    )
    runtime = ValidationIntermediateRuntime(store)
    domain_builder = SharedRequirementArtifactCandidateDomainBuilder(runtime)
    domain = domain_builder.build(
        artifact_state_ref=artifact_ref,
        validation_scope=artifact_scope.value,
        subject_selector_ref="document:1" if artifact_scope is ArtifactScope.DOCUMENT else "section:1",
        complete_applicable_domain_refs=("unit:risk", "unit:response"),
        prioritized_candidate_refs=("unit:risk",),
        consumer_requirements=(requirement.requirement_id,),
        completeness_state=domain_state,
        provenance_refs=("artifact-visible://coverage",),
    )
    domain_ref = domain_builder.commit(
        validation_run_id=run.validation_run_id,
        domain=domain,
    )
    coverage_materializer = CoverageRequirementTargetMaterializer(runtime)
    target = coverage_materializer.materialize(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        validation_scope=artifact_scope.value,
        target_specs=(
            {
                "target_kind": "CONTENT_TARGET",
                "canonical_subject_selector": "risk-treatment",
                "normalized_presence_constraint": "risk-and-response-present",
                "required_count": required_count,
                "provenance_refs": ("requirement-projection:coverage",),
            },
        ),
    )[0]
    target_ref = coverage_materializer.commit(
        validation_run_id=run.validation_run_id,
        target=target,
    )
    coverage_target_projector = CoverageTargetProjector(runtime)
    match_set = coverage_target_projector.project(
        coverage_target=target,
        candidate_domain=domain,
        matched_unit_refs=matched_refs,
        match_state=(
            "MATCH_OBSERVED" if matched_refs else "NO_MATCH_OBSERVED"
        ),
        complete_domain=complete_domain,
        provenance_refs=("coverage-match:test",),
    )
    match_ref = coverage_target_projector.commit(
        validation_run_id=run.validation_run_id,
        target_set=match_set,
    )

    relevancy_target = RelevancyTargetProjector(runtime).project(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        candidate_domain=domain,
        response_unit_refs=("unit:risk",),
    )
    consumer = RequirementBatchConsumer(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        candidate_domain=domain,
        relevancy_target=relevancy_target,
        coverage_target=target,
        coverage_projection=match_set,
    )

    calls = {"count": 0}

    def evaluator(_consumer):
        calls["count"] += 1
        return {
            "adherence_observation": adherence,
            "semantic_presence_observation": semantic_presence,
            "response_refs": ("unit:risk",),
            "presence_refs": tuple(matched_refs),
            "observed_count": len(tuple(matched_refs)),
            "logical_consumer_binding_refs": (
                "consumer:relevancy",
                "consumer:coverage",
            ),
        }

    assessment_projector = RequirementArtifactAssessmentProjector(runtime)
    assessment = RequirementBatchEvaluator(
        projector=assessment_projector,
        evaluator=evaluator,
    ).evaluate(
        validation_run_id=run.validation_run_id,
        consumers=(consumer,),
    )[0]
    assessment_ref = assessment_projector.commit(assessment)
    return {
        "database": database,
        "store": store,
        "run": run,
        "runtime": runtime,
        "requirement": requirement,
        "requirement_ref": requirement_ref,
        "artifact_ref": artifact_ref,
        "domain": domain,
        "domain_ref": domain_ref,
        "target": target,
        "target_ref": target_ref,
        "match_set": match_set,
        "match_ref": match_ref,
        "relevancy_target": relevancy_target,
        "assessment": assessment,
        "assessment_ref": assessment_ref,
        "calls": calls,
    }


def test_coverage_target_identity_and_cardinality_are_exact(tmp_path) -> None:
    fx = _coverage_fixture(tmp_path, required_count=2)

    assert fx["target"].required_count == 2
    assert fx["target"].requirement_ref == fx["requirement_ref"]
    replay = fx["runtime"].resolve_typed(
        fx["target_ref"],
        CoverageRequirementTargetSchema,
    )
    assert replay == fx["target"]


def test_structural_coverage_is_deterministic(tmp_path) -> None:
    fx = _coverage_fixture(
        tmp_path,
        matched_refs=("unit:risk",),
        required_count=2,
    )
    relation = CoverageStructuralEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
    )

    assert relation is CoverageRelation.PARTIAL
    fact = CoverageProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target=fx["target"],
        match_target_set=fx["match_set"],
        relation=relation,
        observed_count=1,
    )
    assert fact.family is ValidationFactFamily.COVERAGE_COMPLETENESS
    assert fact.semantic_state is ValidationSemanticState.FAIL


def test_semantic_missing_requires_complete_domain_proof(tmp_path) -> None:
    fx = _coverage_fixture(
        tmp_path,
        semantic_presence=SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN,
        matched_refs=(),
        complete_domain=True,
    )

    relation = CoverageSemanticEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
        assessment=fx["assessment"],
    )
    assert relation is CoverageRelation.MISSING


def test_top_k_no_match_cannot_directly_produce_missing(tmp_path) -> None:
    database, store, _lifecycle, run = _created_run(tmp_path)
    requirement = PostWriteRequirementEntrySchema(
        requirement_id="req:semantic",
        requirement_kind="CONTENT_PRESENCE_OBLIGATION",
        statement="must contain semantic target",
    )
    requirement_ref = RequirementMaterializer(
        RequirementStore(database)
    ).materialize(
        semantic_contract_version="requirements-v1",
        source_business_ref="business:test",
        source_task_ref="task:test",
        requirements=(requirement,),
    )
    artifact_ref = ArtifactStateRefSchema(
        artifact_id="section-artifact:top-k",
        artifact_version=1,
        artifact_hash="c" * 64,
        artifact_scope=ArtifactScope.SECTION,
    )
    runtime = ValidationIntermediateRuntime(store)
    domain = SharedRequirementArtifactCandidateDomainBuilder(runtime).build(
        artifact_state_ref=artifact_ref,
        validation_scope="SECTION",
        subject_selector_ref="section:top-k",
        complete_applicable_domain_refs=("unit:a", "unit:b", "unit:c"),
        prioritized_candidate_refs=("unit:a",),
        consumer_requirements=(requirement.requirement_id,),
        completeness_state=RequirementDomainCompletenessState.WIDEN_REQUIRED,
    )
    target = CoverageRequirementTargetMaterializer(runtime).materialize(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        validation_scope="SECTION",
        target_specs=(
            {
                "target_kind": "CONTENT_TARGET",
                "canonical_subject_selector": "semantic",
                "normalized_presence_constraint": "present",
            },
        ),
    )[0]
    match_set = CoverageTargetProjector(runtime).project(
        coverage_target=target,
        candidate_domain=domain,
        matched_unit_refs=(),
        match_state="TOP_K_NO_MATCH",
        complete_domain=False,
    )
    consumer = RequirementBatchConsumer(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        candidate_domain=domain,
        coverage_target=target,
        coverage_projection=match_set,
    )
    projector = RequirementArtifactAssessmentProjector(runtime)

    with pytest.raises(
        SharedRequirementIntegrityError,
        match="complete domain",
    ):
        projector.project(
            validation_run_id=run.validation_run_id,
            consumer=consumer,
            assessment_contract_version="v1",
            adherence_observation=None,
            semantic_presence_observation=(
                SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN
            ),
        )


def test_relevancy_satisfied_does_not_imply_coverage_complete(tmp_path) -> None:
    fx = _coverage_fixture(
        tmp_path,
        adherence=RequirementAdherenceObservation.SATISFIED,
        semantic_presence=SemanticPresenceObservation.INDETERMINATE,
        matched_refs=(),
        complete_domain=True,
    )

    relation = CoverageSemanticEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
        assessment=fx["assessment"],
    )
    assert (
        fx["assessment"].adherence_observation
        is RequirementAdherenceObservation.SATISFIED
    )
    assert relation is CoverageRelation.INDETERMINATE


def test_shared_semantic_presence_observation_is_intermediate_only(tmp_path) -> None:
    fx = _coverage_fixture(tmp_path)

    assert not hasattr(fx["assessment"], "fact_id")
    relation = CoverageSemanticEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
        assessment=fx["assessment"],
    )
    fact = CoverageProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target=fx["target"],
        match_target_set=fx["match_set"],
        relation=relation,
        observed_count=1,
        related_refs=(fx["assessment"].assessment_ref,),
    )
    assert fact.family is ValidationFactFamily.COVERAGE_COMPLETENESS
    assert "adherence_observation" not in fact.payload


def test_document_coverage_independently_exposes_missing_required_section(
    tmp_path,
) -> None:
    fx = _coverage_fixture(
        tmp_path,
        semantic_presence=SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN,
        matched_refs=(),
        complete_domain=True,
        artifact_scope=ArtifactScope.DOCUMENT,
    )
    relation = CoverageStructuralEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
    )

    assert fx["artifact_ref"].artifact_scope is ArtifactScope.DOCUMENT
    assert relation is CoverageRelation.MISSING


def test_partial_assembly_produces_exact_missing_obligation_evidence(
    tmp_path,
) -> None:
    fx = _coverage_fixture(
        tmp_path,
        matched_refs=("unit:risk",),
        required_count=2,
    )
    relation = CoverageStructuralEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
    )
    fact = CoverageProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target=fx["target"],
        match_target_set=fx["match_set"],
        relation=relation,
        observed_count=1,
        observation_codes=("REQUIRED_COUNT_NOT_MET",),
    )

    assert relation is CoverageRelation.PARTIAL
    assert fact.payload["required_count"] == 2
    assert fact.payload["observed_count"] == 1
    assert (
        fact.payload["artifact_match_target_set_ref"]
        == fx["match_set"].match_target_set_ref
    )


def test_coverage_replay_restores_exact_complete_domain_chain_without_evaluator(
    tmp_path,
) -> None:
    fx = _coverage_fixture(tmp_path)
    before = fx["calls"]["count"]

    match_set = fx["runtime"].resolve_typed(
        fx["match_ref"],
        ArtifactCoverageMatchTargetSetRefSchema,
    )
    assessment = fx["runtime"].resolve_typed(
        fx["assessment_ref"],
        RequirementArtifactAssessmentSchema,
    )

    assert fx["calls"]["count"] == before
    assert match_set == fx["match_set"]
    assert assessment == fx["assessment"]
    assert match_set.complete_domain is True
