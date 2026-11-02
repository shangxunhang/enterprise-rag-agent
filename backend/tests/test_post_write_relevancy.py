from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.post_write import (
    ArtifactScope,
    ArtifactStateRefSchema,
    PostWriteRequirementEntrySchema,
    RequirementAdherenceObservation,
    RequirementDomainCompletenessState,
    SemanticPresenceObservation,
    ValidationFactFamily,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactResponseTargetSetRefSchema,
    RequirementArtifactAssessmentSchema,
    SharedRequirementArtifactCandidateDomainSchema,
)
from apps.enterprise_document.services.post_write import (
    RelevancyTargetProjector,
    RelevancyValidator,
    RequirementArtifactAssessmentProjector,
    RequirementBatchConsumer,
    RequirementBatchEvaluator,
    RequirementMaterializer,
    RequirementStore,
    SharedRequirementArtifactCandidateDomainBuilder,
    ValidationIntermediateRuntime,
)

from backend.tests.test_post_write_validation_foundation import _created_run


def _relevancy_fixture(
    tmp_path,
    *,
    adherence=RequirementAdherenceObservation.SATISFIED,
    presence=SemanticPresenceObservation.MATCH_OBSERVED,
):
    database, store, _lifecycle, run = _created_run(tmp_path)
    requirement = PostWriteRequirementEntrySchema(
        requirement_id="req:risk-treatment",
        requirement_kind="POSITIVE_SEMANTIC_OBLIGATION",
        statement="风险分析必须说明主要风险及应对措施",
        mandatory=True,
        constraints={"source_group_ref": "requirement-group:risk"},
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
        artifact_id="section-artifact:relevancy",
        artifact_version=1,
        artifact_hash="a" * 64,
        artifact_scope=ArtifactScope.SECTION,
    )
    intermediate_runtime = ValidationIntermediateRuntime(store)
    domain_builder = SharedRequirementArtifactCandidateDomainBuilder(
        intermediate_runtime
    )
    domain = domain_builder.build(
        artifact_state_ref=artifact_ref,
        validation_scope="SECTION",
        subject_selector_ref="section:1",
        complete_applicable_domain_refs=("unit:risk", "unit:response"),
        prioritized_candidate_refs=("unit:risk", "unit:response"),
        consumer_requirements=(requirement.requirement_id,),
        completeness_state=RequirementDomainCompletenessState.COMPLETE,
        provenance_refs=("artifact-visible://test",),
    )
    domain_ref = domain_builder.commit(
        validation_run_id=run.validation_run_id,
        domain=domain,
    )
    target_projector = RelevancyTargetProjector(intermediate_runtime)
    target = target_projector.project(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        candidate_domain=domain,
        response_unit_refs=("unit:risk", "unit:response"),
    )
    target_ref = target_projector.commit(
        validation_run_id=run.validation_run_id,
        target_set=target,
    )
    consumer = RequirementBatchConsumer(
        requirement_ref=requirement_ref,
        requirement=requirement,
        artifact_state_ref=artifact_ref,
        candidate_domain=domain,
        relevancy_target=target,
    )
    calls = {"count": 0}

    def evaluator(_consumer):
        calls["count"] += 1
        return {
            "adherence_observation": adherence,
            "semantic_presence_observation": presence,
            "response_refs": ("unit:risk", "unit:response"),
            "presence_refs": ("unit:risk",),
            "observed_count": 1,
            "logical_consumer_binding_refs": ("consumer:relevancy",),
            "reason_codes": (
                ("SEMANTIC_REQUIREMENT_VIOLATED",)
                if adherence is RequirementAdherenceObservation.VIOLATED
                else ()
            ),
        }

    assessment_projector = RequirementArtifactAssessmentProjector(
        intermediate_runtime
    )
    assessment = RequirementBatchEvaluator(
        projector=assessment_projector,
        evaluator=evaluator,
    ).evaluate(
        validation_run_id=run.validation_run_id,
        consumers=(consumer,),
    )[0]
    assessment_ref = assessment_projector.commit(assessment)
    fact = RelevancyValidator.project(
        validation_run_id=run.validation_run_id,
        subject_hash=run.subject_key.subject_hash,
        target_set=target,
        assessment=assessment,
    )
    fact_ref = store.put_fact_if_absent_or_same(fact)
    return {
        "database": database,
        "store": store,
        "run": run,
        "requirement": requirement,
        "requirement_ref": requirement_ref,
        "artifact_ref": artifact_ref,
        "runtime": intermediate_runtime,
        "domain": domain,
        "domain_ref": domain_ref,
        "target": target,
        "target_ref": target_ref,
        "assessment": assessment,
        "assessment_ref": assessment_ref,
        "fact": fact,
        "fact_ref": fact_ref,
        "calls": calls,
    }


def test_relevancy_uses_canonical_requirement_ref_only(tmp_path) -> None:
    fx = _relevancy_fixture(tmp_path)

    assert fx["target"].requirement_ref == fx["requirement_ref"]
    assert fx["assessment"].requirement_ref == fx["requirement_ref"]
    resolved = RequirementStore(fx["database"]).resolve_exact(
        fx["requirement_ref"]
    )
    assert resolved.requirements == (fx["requirement"],)


def test_relevancy_target_set_is_exact_and_hash_stable(tmp_path) -> None:
    fx = _relevancy_fixture(tmp_path)
    replay = fx["runtime"].resolve_typed(
        fx["target_ref"],
        ArtifactResponseTargetSetRefSchema,
    )

    assert replay == fx["target"]
    assert replay.candidate_domain_ref == fx["domain"].candidate_domain_ref
    assert replay.response_unit_refs == ("unit:response", "unit:risk")


@pytest.mark.parametrize(
    ("observation", "expected_state"),
    [
        (
            RequirementAdherenceObservation.SATISFIED,
            ValidationSemanticState.PASS,
        ),
        (
            RequirementAdherenceObservation.VIOLATED,
            ValidationSemanticState.FAIL,
        ),
        (
            RequirementAdherenceObservation.INDETERMINATE,
            ValidationSemanticState.INDETERMINATE,
        ),
    ],
)
def test_relevancy_exact_projection_states(
    tmp_path,
    observation,
    expected_state,
) -> None:
    fx = _relevancy_fixture(tmp_path, adherence=observation)

    assert fx["fact"].family is ValidationFactFamily.REQUIREMENT_ADHERENCE
    assert fx["fact"].semantic_state is expected_state
    assert fx["fact"].payload["adherence_observation"] == observation.value


def test_content_can_be_present_while_requirement_is_violated(tmp_path) -> None:
    fx = _relevancy_fixture(
        tmp_path,
        adherence=RequirementAdherenceObservation.VIOLATED,
        presence=SemanticPresenceObservation.MATCH_OBSERVED,
    )

    assert (
        fx["assessment"].semantic_presence_observation
        is SemanticPresenceObservation.MATCH_OBSERVED
    )
    assert (
        fx["assessment"].adherence_observation
        is RequirementAdherenceObservation.VIOLATED
    )
    assert fx["fact"].semantic_state is ValidationSemanticState.FAIL


def test_relevancy_never_infers_coverage_state(tmp_path) -> None:
    fx = _relevancy_fixture(tmp_path)

    assert "coverage_relation" not in fx["fact"].payload
    assert "coverage_target_ref" not in fx["fact"].payload
    assert fx["fact"].family is ValidationFactFamily.REQUIREMENT_ADHERENCE


def test_shared_requirement_assessment_is_intermediate_not_fact_authority(
    tmp_path,
) -> None:
    fx = _relevancy_fixture(tmp_path)

    assert not hasattr(fx["assessment"], "fact_id")
    assert not hasattr(fx["assessment"], "family")
    assert fx["fact"].target_ref == fx["target"].target_set_ref


def test_relevancy_replay_restores_exact_domain_target_and_assessment_without_evaluator(
    tmp_path,
) -> None:
    fx = _relevancy_fixture(tmp_path)
    before = fx["calls"]["count"]

    domain = fx["runtime"].resolve_typed(
        fx["domain_ref"],
        SharedRequirementArtifactCandidateDomainSchema,
    )
    target = fx["runtime"].resolve_typed(
        fx["target_ref"],
        ArtifactResponseTargetSetRefSchema,
    )
    assessment = fx["runtime"].resolve_typed(
        fx["assessment_ref"],
        RequirementArtifactAssessmentSchema,
    )

    assert fx["calls"]["count"] == before
    assert domain == fx["domain"]
    assert target == fx["target"]
    assert assessment == fx["assessment"]
    assert fx["store"].resolve_fact(fx["fact_ref"]) == fx["fact"]
