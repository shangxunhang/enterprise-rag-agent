from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pytest

from apps.enterprise_document.config.post_write import (
    ConsistencyExecutionProfile,
    load_post_write_config,
)
from apps.enterprise_document.schemas.post_write import (
    ArtifactConsistencyRelation,
    ArtifactScope,
    ArtifactStateRefSchema,
    ConsistencyCandidateDiscoveryResultSchema,
    ConsistencyDiscoveryPlanSchema,
    ConsistencyPlanConformanceResultSchema,
    ConsistencyPlanConformanceState,
    ConsistencyRelationMateriality,
    ExactConsistencyEvaluationResultSchema,
    FormalExecutabilityResultSchema,
    FormalExecutabilityState,
    FormalRelationCandidateSchema,
    SemanticConsistencyEvaluationResultSchema,
    SemanticRelationBindingSchema,
    ValidationFactFamily,
    ValidationSemanticState,
)
from apps.enterprise_document.services.post_write import (
    ConsistencyAssertionMaterializer,
    ConsistencyCandidateDiscoveryExecutor,
    ConsistencyDiscoveryPlanner,
    ConsistencyIntegrityError,
    ConsistencyPlanConformanceGuard,
    ConsistencyProjector,
    ExactConsistencyEvaluator,
    FormalExecutabilityGuard,
    SemanticConsistencyEvaluator,
    ValidationIntermediateRuntime,
)
from core.hashing import canonical_sha256

from backend.tests.test_post_write_validation_foundation import _created_run


def _formal_candidate(lhs_ref: str, rhs_ref: str, predicate: str = "EQ"):
    kwargs = {
        "lhs_assertion_ref": lhs_ref,
        "predicate": predicate,
        "rhs_assertion_ref": rhs_ref,
        "precision_tolerance": None,
        "formal_contract_version": "formal-consistency-v1",
    }
    placeholder = FormalRelationCandidateSchema.model_construct(
        formal_candidate_ref="pending",
        formal_candidate_hash="0" * 64,
        **kwargs,
    )
    payload = placeholder.model_dump(mode="json")
    payload.pop("formal_candidate_ref", None)
    payload.pop("formal_candidate_hash", None)
    digest = canonical_sha256(payload)
    return FormalRelationCandidateSchema(
        formal_candidate_ref="formal-relation-candidate://sha256/" + digest,
        formal_candidate_hash=digest,
        **kwargs,
    )


def _consistency_fixture(
    tmp_path,
    *,
    lhs_value=100,
    rhs_value=100,
    include_candidate=True,
    complete=True,
    formal=True,
):
    database, store, _lifecycle, run = _created_run(tmp_path)
    runtime = ValidationIntermediateRuntime(store)
    artifact_ref = ArtifactStateRefSchema(
        artifact_id="document-artifact:consistency",
        artifact_version=1,
        artifact_hash="d" * 64,
        artifact_scope=ArtifactScope.DOCUMENT,
    )
    materializer = ConsistencyAssertionMaterializer(runtime)
    assertions = materializer.materialize(
        artifact_state_ref=artifact_ref,
        validation_subject_hash=run.subject_key.subject_hash,
        assertions=(
            {
                "source_unit_ref": "unit:declared-total",
                "canonical_text": "总量为100",
                "typed_value": lhs_value,
                "value_unit": "unit",
            },
            {
                "source_unit_ref": "unit:computed-total",
                "canonical_text": "合计为100",
                "typed_value": rhs_value,
                "value_unit": "unit",
            },
        ),
    )
    assertion_refs = tuple(
        materializer.commit(
            validation_run_id=run.validation_run_id,
            assertion=item,
        )
        for item in assertions
    )
    profile = ConsistencyExecutionProfile.build(
        profile_id="consistency-test",
        profile_version="v1",
        small_n_binary_member_limit=8,
    )
    planner = ConsistencyDiscoveryPlanner(
        profile=profile,
        intermediate_runtime=runtime,
    )
    plan = planner.plan(
        validation_run_id=run.validation_run_id,
        artifact_state_ref=artifact_ref,
        validation_subject_hash=run.subject_key.subject_hash,
        assertions=assertions,
    )
    plan_ref = planner.commit(plan)
    relation_key = ConsistencyDiscoveryPlanner.relation_key(
        item.assertion_ref for item in assertions
    )
    discovery_executor = ConsistencyCandidateDiscoveryExecutor(runtime)
    discovery = discovery_executor.execute(
        plan=plan,
        candidate_relations=(
            {relation_key: tuple(item.assertion_ref for item in assertions)}
            if include_candidate
            else {}
        ),
        complete_required_plan_accounting=complete,
        discovery_profile_version=profile.profile_version,
    )
    discovery_ref = discovery_executor.commit(
        validation_run_id=run.validation_run_id,
        result=discovery,
    )
    conformance_guard = ConsistencyPlanConformanceGuard(runtime)
    conformance = conformance_guard.evaluate(
        plan=plan,
        discovery=discovery,
    )
    conformance_ref = conformance_guard.commit(
        validation_run_id=run.validation_run_id,
        result=conformance,
    )

    formal_candidate = (
        _formal_candidate(
            assertions[0].assertion_ref,
            assertions[1].assertion_ref,
        )
        if formal
        else None
    )

    def semantic_relation(_assertions):
        return {
            "relation_materiality": (
                ConsistencyRelationMateriality.MATERIAL_RELATION
            ),
            "semantic_relation_description": (
                "declared total must equal computed total"
            ),
            "role_bindings": ("declared-total", "computed-total"),
            "formal_relation_candidate": formal_candidate,
            "logical_consumer_binding_refs": ("consumer:consistency",),
        }

    semantic = SemanticConsistencyEvaluator(
        semantic_evaluator=semantic_relation,
        intermediate_runtime=runtime,
    )
    binding = semantic.bind(
        validation_run_id=run.validation_run_id,
        validation_scope="DOCUMENT_GLOBAL",
        scope_subject_ref="document:consistency",
        assertions=assertions,
    )
    binding_ref = semantic.commit_binding(binding)
    assertion_map = {item.assertion_ref: item for item in assertions}
    formal_guard = FormalExecutabilityGuard(runtime)
    executability = formal_guard.evaluate(
        binding=binding,
        assertions_by_ref=assertion_map,
    )
    executability_ref = formal_guard.commit(
        validation_run_id=run.validation_run_id,
        result=executability,
    )
    exact_evaluator = ExactConsistencyEvaluator(runtime)
    evaluation = None
    evaluation_ref = None
    if executability.state is FormalExecutabilityState.EXECUTABLE:
        evaluation = exact_evaluator.evaluate(
            binding=binding,
            executability=executability,
            assertions_by_ref=assertion_map,
        )
        evaluation_ref = exact_evaluator.commit(
            validation_run_id=run.validation_run_id,
            result=evaluation,
        )
    return {
        "database": database,
        "store": store,
        "run": run,
        "runtime": runtime,
        "artifact_ref": artifact_ref,
        "assertions": assertions,
        "assertion_refs": assertion_refs,
        "profile": profile,
        "planner": planner,
        "plan": plan,
        "plan_ref": plan_ref,
        "relation_key": relation_key,
        "discovery": discovery,
        "discovery_ref": discovery_ref,
        "conformance": conformance,
        "conformance_ref": conformance_ref,
        "semantic": semantic,
        "binding": binding,
        "binding_ref": binding_ref,
        "executability": executability,
        "executability_ref": executability_ref,
        "evaluation": evaluation,
        "evaluation_ref": evaluation_ref,
        "assertion_map": assertion_map,
    }


def test_consistency_execution_profile_is_versioned_hash_bound_and_loadable() -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "enterprise_document"
        / "config"
        / "post_write_v1.yaml"
    )
    config = load_post_write_config(config_path)
    profile = config.consistency

    payload = profile.model_dump(mode="json")
    digest = payload.pop("execution_profile_hash")
    assert canonical_sha256(payload) == digest
    assert profile.procedure_contract_version == "consistency-procedure-v1"


def test_consistency_small_n_plan_exactly_enumerates_eligible_member_pairs(
    tmp_path,
) -> None:
    fx = _consistency_fixture(tmp_path)

    assert fx["plan"].execution_profile_hash == fx["profile"].execution_profile_hash
    assert (
        fx["plan"].procedure_contract_version
        == fx["profile"].procedure_contract_version
    )
    expected = tuple(
        sorted(
            ConsistencyDiscoveryPlanner.relation_key(pair)
            for pair in combinations(
                (item.assertion_ref for item in fx["assertions"]),
                2,
            )
        )
    )
    assert fx["plan"].discovery_mode == "SMALL_N_EXHAUSTIVE_MEMBERS"
    assert fx["plan"].required_relation_keys == expected


def test_consistency_positive_fact_requires_plan_conformance(tmp_path) -> None:
    fx = _consistency_fixture(tmp_path)
    assert (
        fx["conformance"].state
        is ConsistencyPlanConformanceState.CONFORMING
    )
    fact = ConsistencyProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        plan=fx["plan"],
        conformance=fx["conformance"],
        binding=fx["binding"],
        evaluation=fx["evaluation"],
    )

    assert fact.family is ValidationFactFamily.ARTIFACT_CONSISTENCY
    assert fact.semantic_state is ValidationSemanticState.PASS


def test_missing_required_plan_obligation_cannot_pass(tmp_path) -> None:
    fx = _consistency_fixture(
        tmp_path,
        include_candidate=False,
        complete=False,
    )
    assert (
        fx["conformance"].state
        is ConsistencyPlanConformanceState.NONCONFORMING
    )

    with pytest.raises(
        ConsistencyIntegrityError,
        match="plan conformance",
    ):
        ConsistencyProjector.project(
            validation_run_id=fx["run"].validation_run_id,
            subject_hash=fx["run"].subject_key.subject_hash,
            plan=fx["plan"],
            conformance=fx["conformance"],
            binding=fx["binding"],
            evaluation=fx["evaluation"],
        )


def test_large_n_semantic_discovery_remains_open_world_and_requires_explicit_obligation(
    tmp_path,
) -> None:
    _database, store, _lifecycle, run = _created_run(tmp_path)
    runtime = ValidationIntermediateRuntime(store)
    artifact_ref = ArtifactStateRefSchema(
        artifact_id="document-artifact:large-n",
        artifact_version=1,
        artifact_hash="e" * 64,
        artifact_scope=ArtifactScope.DOCUMENT,
    )
    materializer = ConsistencyAssertionMaterializer(runtime)
    assertions = materializer.materialize(
        artifact_state_ref=artifact_ref,
        validation_subject_hash=run.subject_key.subject_hash,
        assertions=tuple(
            {
                "source_unit_ref": f"unit:{idx}",
                "canonical_text": f"assertion {idx}",
                "typed_value": idx,
            }
            for idx in range(9)
        ),
    )
    profile = ConsistencyExecutionProfile.build(
        profile_id="large-n",
        profile_version="v1",
        small_n_binary_member_limit=4,
    )
    planner = ConsistencyDiscoveryPlanner(profile=profile)
    required_pair = (assertions[0].assertion_ref, assertions[-1].assertion_ref)
    plan = planner.plan(
        validation_run_id=run.validation_run_id,
        artifact_state_ref=artifact_ref,
        validation_subject_hash=run.subject_key.subject_hash,
        assertions=assertions,
        required_relation_members=(required_pair,),
    )
    assert plan.discovery_mode == "LARGE_N_HIGH_RECALL"
    assert plan.required_relation_keys == (
        ConsistencyDiscoveryPlanner.relation_key(required_pair),
    )


def test_deterministic_rule_cannot_drop_ambiguous_semantic_candidate(tmp_path) -> None:
    fx = _consistency_fixture(tmp_path)

    assert fx["relation_key"] in fx["discovery"].candidate_relation_keys
    assert fx["discovery"].candidate_member_refs == (
        tuple(item.assertion_ref for item in fx["assertions"]),
    )


def test_exact_formal_mechanics_run_only_after_semantic_binding(tmp_path) -> None:
    fx = _consistency_fixture(tmp_path)

    assert (
        fx["binding"].relation_materiality
        is ConsistencyRelationMateriality.MATERIAL_RELATION
    )
    assert fx["binding"].formal_relation_candidate is not None
    assert (
        fx["executability"].state
        is FormalExecutabilityState.EXECUTABLE
    )
    assert (
        fx["evaluation"].relation
        is ArtifactConsistencyRelation.CONSISTENT
    )


def test_formal_not_executable_is_distinct_from_relation_false(tmp_path) -> None:
    fx = _consistency_fixture(
        tmp_path,
        lhs_value=None,
        rhs_value=None,
        formal=True,
    )

    assert (
        fx["executability"].state
        is FormalExecutabilityState.NOT_EXECUTABLE
    )
    semantic_result = fx["semantic"].evaluate(
        binding=fx["binding"],
        relation=ArtifactConsistencyRelation.INDETERMINATE,
        observation_codes=("FORMAL_NOT_EXECUTABLE",),
    )
    assert (
        semantic_result.relation
        is ArtifactConsistencyRelation.INDETERMINATE
    )


def test_zero_discovered_relations_alone_cannot_imply_pass(tmp_path) -> None:
    fx = _consistency_fixture(
        tmp_path,
        include_candidate=False,
        complete=True,
    )

    assert not fx["discovery"].candidate_relation_keys
    assert (
        fx["conformance"].state
        is ConsistencyPlanConformanceState.NONCONFORMING
    )


def test_supported_individual_assertion_values_may_still_be_inconsistent(
    tmp_path,
) -> None:
    fx = _consistency_fixture(
        tmp_path,
        lhs_value=100,
        rhs_value=120,
    )

    assert (
        fx["evaluation"].relation
        is ArtifactConsistencyRelation.INCONSISTENT
    )
    fact = ConsistencyProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        plan=fx["plan"],
        conformance=fx["conformance"],
        binding=fx["binding"],
        evaluation=fx["evaluation"],
    )
    assert fact.semantic_state is ValidationSemanticState.FAIL


def test_consistency_replay_restores_exact_plan_binding_formal_chain(tmp_path) -> None:
    fx = _consistency_fixture(tmp_path)

    assert (
        fx["runtime"].resolve_typed(
            fx["plan_ref"],
            ConsistencyDiscoveryPlanSchema,
        )
        == fx["plan"]
    )
    assert (
        fx["runtime"].resolve_typed(
            fx["discovery_ref"],
            ConsistencyCandidateDiscoveryResultSchema,
        )
        == fx["discovery"]
    )
    assert (
        fx["runtime"].resolve_typed(
            fx["conformance_ref"],
            ConsistencyPlanConformanceResultSchema,
        )
        == fx["conformance"]
    )
    assert (
        fx["runtime"].resolve_typed(
            fx["binding_ref"],
            SemanticRelationBindingSchema,
        )
        == fx["binding"]
    )
    assert (
        fx["runtime"].resolve_typed(
            fx["executability_ref"],
            FormalExecutabilityResultSchema,
        )
        == fx["executability"]
    )
    assert (
        fx["runtime"].resolve_typed(
            fx["evaluation_ref"],
            ExactConsistencyEvaluationResultSchema,
        )
        == fx["evaluation"]
    )


def test_semantic_consistency_result_replays_without_formal_execution(tmp_path) -> None:
    fx = _consistency_fixture(
        tmp_path,
        lhs_value=None,
        rhs_value=None,
    )
    result = fx["semantic"].evaluate(
        binding=fx["binding"],
        relation=ArtifactConsistencyRelation.INCONSISTENT,
        observation_codes=("SEMANTIC_CONTRADICTION",),
    )
    ref = fx["semantic"].commit_evaluation(
        validation_run_id=fx["run"].validation_run_id,
        result=result,
    )

    assert (
        fx["runtime"].resolve_typed(
            ref,
            SemanticConsistencyEvaluationResultSchema,
        )
        == result
    )


def test_semantic_relation_binding_is_sole_target_canonical_name() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    quality_source = (
        backend_root
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "post_write"
        / "quality.py"
    ).read_text(encoding="utf-8")
    service_source = (
        backend_root
        / "apps"
        / "enterprise_document"
        / "services"
        / "post_write"
        / "consistency.py"
    ).read_text(encoding="utf-8")

    assert "class SemanticRelationBindingSchema" in quality_source
    assert "SemanticRoleBindingSchema" not in quality_source
    assert "SemanticRoleBindingSchema" not in service_source
