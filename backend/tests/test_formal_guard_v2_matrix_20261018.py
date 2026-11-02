"""Bounded Guard v2 proofs; all writable fixtures use pytest's supplied root.

The saved October 15 row is a read-only input fixture, never a runtime root.
The v1 class below comes from this round's frozen pre-change source. It is not
claimed to reconstruct October 15's complete effective runtime environment.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.post_write import (
    ArtifactConsistencyRelation,
    ConsistencyAssertionSchema,
    ConsistencyRelationMateriality,
    FormalExecutabilityState,
    SemanticConsistencyEvaluationResultSchema,
    ValidationFactSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.services.post_write import (
    ConsistencyAssertionMaterializer,
    ConsistencyIntegrityError,
    ConsistencyProjector,
    ExactConsistencyEvaluator,
    FormalExecutabilityGuard,
    SemanticConsistencyEvaluator,
)
from apps.enterprise_document.services.post_write import consistency as production
from backend.tests.test_post_write_consistency import _consistency_fixture
from core.hashing import canonical_sha256


EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "docs/sdc_stage2_formal_guard_v2_d1a_archive_only_20261018_r02"
)
TEXT_REASON = "FORMAL_TEXT_OPERAND_NOT_PROVEN_COMPARABLE"
PREDICATES = ("EQ", "NE", "LT", "LE", "GT", "GE")


def _rebind(value, ref_field, hash_field, **changes):
    payload = value.model_dump(mode="json")
    prefix = payload.pop(ref_field).split("/sha256/", 1)[0] + "/sha256/"
    payload.pop(hash_field)
    payload.update(changes)
    digest = canonical_sha256(payload)
    return type(value)(
        **payload, **{ref_field: prefix + digest, hash_field: digest}
    )


def _binding(fx, predicate="EQ", tolerance=None, reverse=False):
    by_source = {item.source_unit_ref: item for item in fx["assertions"]}
    lhs = by_source["unit:declared-total"]
    rhs = by_source["unit:computed-total"]
    if reverse:
        lhs, rhs = rhs, lhs
    candidate = _rebind(
        fx["binding"].formal_relation_candidate,
        "formal_candidate_ref",
        "formal_candidate_hash",
        lhs_assertion_ref=lhs.assertion_ref,
        rhs_assertion_ref=rhs.assertion_ref,
        predicate=predicate,
        precision_tolerance=tolerance,
    )
    return _rebind(
        fx["binding"],
        "binding_ref",
        "binding_hash",
        formal_relation_candidate=candidate.model_dump(mode="json"),
    )


def _frozen_v1_guard():
    source = EVIDENCE / "frozen_prechange/consistency_v1.py.txt"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    guard_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "FormalExecutabilityGuard"
    )
    isolated_globals = dict(vars(production))
    exec(
        compile(ast.Module(body=[guard_class], type_ignores=[]), str(source), "exec"),
        isolated_globals,
    )
    guard = isolated_globals["FormalExecutabilityGuard"]
    assert guard.PRODUCER_REF == "post-write-formal-executability:v1"
    assert production.FormalExecutabilityGuard is FormalExecutabilityGuard
    return guard


def test_saved_real_row_v1_exact_false_v2_existing_semantic_relation():
    source = EVIDENCE / "historical_fixed_input.json"
    preserved_bytes = source.read_bytes()
    fixed = json.loads(preserved_bytes)
    assert fixed["evidence_level"].startswith("SAVED_REAL_READ_ONLY_EXACT")
    assert fixed["raw_model_row"] == fixed["model_row"]
    assert fixed["assertions"] == fixed["prompt_assertions"]
    binding = production.SemanticRelationBindingSchema.model_validate(fixed["binding"])
    assertions = {
        value["assertion_ref"]: ConsistencyAssertionSchema.model_validate(value)
        for value in fixed["assertions"]
    }
    assert all(isinstance(value.typed_value, str) for value in assertions.values())
    assert binding.formal_relation_candidate.predicate == "EQ"
    old = _frozen_v1_guard()().evaluate(
        binding=binding, assertions_by_ref=assertions
    )
    assert old.model_dump(mode="json") == fixed["historical_guard"]
    old_exact = ExactConsistencyEvaluator().evaluate(
        binding=binding, executability=old, assertions_by_ref=assertions
    )
    assert old_exact.model_dump(mode="json") == fixed["historical_exact_evaluation"]
    assert old_exact.reason_codes == ("FORMAL_RELATION_FALSE",)

    guard = FormalExecutabilityGuard().evaluate(
        binding=binding, assertions_by_ref=assertions
    )
    assert FormalExecutabilityGuard.PRODUCER_REF == "post-write-formal-executability:v2"
    assert guard.state is FormalExecutabilityState.NOT_EXECUTABLE
    assert guard.reason_codes == (TEXT_REASON,)
    with pytest.raises(ConsistencyIntegrityError, match="EXECUTABLE"):
        ExactConsistencyEvaluator().evaluate(
            binding=binding, executability=guard, assertions_by_ref=assertions
        )

    def forbid_rediscovery(_assertions):
        raise AssertionError("Existing semantic relation must not invoke another model")

    semantic = SemanticConsistencyEvaluator(semantic_evaluator=forbid_rediscovery)
    result = semantic.evaluate(
        binding=binding,
        relation=ArtifactConsistencyRelation(fixed["model_row"]["relation"]),
        observation_codes=fixed["model_row"]["reason_codes"],
    )
    assert result.relation is ArtifactConsistencyRelation.CONSISTENT
    assert result.observation_codes == tuple(sorted(fixed["model_row"]["reason_codes"]))
    assert "FORMAL_RELATION_FALSE" not in result.observation_codes
    historical_fact = ValidationFactSchema.model_validate(fixed["historical_fact"])
    assert historical_fact.semantic_state is ValidationSemanticState.FAIL
    assert source.read_bytes() == preserved_bytes


@pytest.mark.parametrize("predicate", PREDICATES)
def test_text_six_predicates_conservatively_reject_sentences_and_atomic_ids(
    tmp_path, predicate
):
    for index, (lhs, rhs) in enumerate(
        (
            ("本项目新建服务器12台。", "已确定的新建数量为12台，部署状态未知。"),
            ("MODEL-001", "MODEL-001"),
            ("12", "13"),
        )
    ):
        fx = _consistency_fixture(tmp_path / str(index), lhs_value=lhs, rhs_value=rhs)
        binding = _binding(fx, predicate)
        guard = FormalExecutabilityGuard().evaluate(
            binding=binding, assertions_by_ref=fx["assertion_map"]
        )
        assert guard.state is FormalExecutabilityState.NOT_EXECUTABLE
        assert guard.reason_codes == (TEXT_REASON,)
        with pytest.raises(ConsistencyIntegrityError, match="EXECUTABLE"):
            ExactConsistencyEvaluator().evaluate(
                binding=binding,
                executability=guard,
                assertions_by_ref=fx["assertion_map"],
            )


@pytest.mark.parametrize("predicate", PREDICATES)
def test_numeric_six_predicates_keep_int_float_and_lhs_rhs_direction(tmp_path, predicate):
    forward = {"EQ": False, "NE": True, "LT": True, "LE": True, "GT": False, "GE": False}
    backward = {"EQ": False, "NE": True, "LT": False, "LE": False, "GT": True, "GE": True}
    for index, (lhs, rhs) in enumerate(((3, 4), (3.0, 4.0), (3, 4.0))):
        fx = _consistency_fixture(tmp_path / str(index), lhs_value=lhs, rhs_value=rhs)
        by_source = {item.source_unit_ref: item for item in fx["assertions"]}
        assert type(by_source["unit:declared-total"].typed_value) is type(lhs)
        assert type(by_source["unit:computed-total"].typed_value) is type(rhs)
        for reverse, expected in ((False, forward[predicate]), (True, backward[predicate])):
            binding = _binding(fx, predicate, reverse=reverse)
            guard = FormalExecutabilityGuard().evaluate(
                binding=binding, assertions_by_ref=fx["assertion_map"]
            )
            assert guard.state is FormalExecutabilityState.EXECUTABLE
            assert guard.reason_codes == ()
            result = ExactConsistencyEvaluator().evaluate(
                binding=binding, executability=guard, assertions_by_ref=fx["assertion_map"]
            )
            assert result.relation is (
                ArtifactConsistencyRelation.CONSISTENT
                if expected
                else ArtifactConsistencyRelation.INCONSISTENT
            )
            assert result.reason_codes == (
                "FORMAL_RELATION_TRUE" if expected else "FORMAL_RELATION_FALSE",
            )


def test_numeric_tolerance_boundary_and_equal_values_keep_existing_exact_rules(tmp_path):
    for index, (lhs, rhs, tolerance, predicate, expected) in enumerate(
        (
            (8, 8.0, None, "EQ", True),
            (8, 8.125, 0.125, "EQ", True),
            (8, 8.125, 0.124, "EQ", False),
            (8, 8.125, None, "EQ", False),
            (8, 8.125, 0.125, "NE", True),
        )
    ):
        fx = _consistency_fixture(tmp_path / str(index), lhs_value=lhs, rhs_value=rhs)
        binding = _binding(fx, predicate, tolerance)
        guard = FormalExecutabilityGuard().evaluate(
            binding=binding, assertions_by_ref=fx["assertion_map"]
        )
        assert guard.state is FormalExecutabilityState.EXECUTABLE
        result = ExactConsistencyEvaluator().evaluate(
            binding=binding, executability=guard, assertions_by_ref=fx["assertion_map"]
        )
        assert result.relation is (
            ArtifactConsistencyRelation.CONSISTENT
            if expected
            else ArtifactConsistencyRelation.INCONSISTENT
        )


@pytest.mark.parametrize(
    ("lhs", "rhs", "lhs_unit", "rhs_unit", "missing", "state", "reason"),
    (
        (None, "text", "m", "s", True, "TECHNICAL_FAILURE", "FORMAL_OPERAND_REF_UNRESOLVED"),
        (None, "text", "m", "s", False, "NOT_EXECUTABLE", "FORMAL_TYPED_OPERAND_UNAVAILABLE"),
        (1, "text", "m", "s", False, "NOT_EXECUTABLE", "FORMAL_UNIT_CONVERSION_UNAVAILABLE"),
        ("a", "b", "m", "s", False, "NOT_EXECUTABLE", "FORMAL_UNIT_CONVERSION_UNAVAILABLE"),
        (1, "text", "m", "m", False, "NOT_EXECUTABLE", "FORMAL_OPERAND_TYPE_MISMATCH"),
        ("a", "b", None, None, False, "NOT_EXECUTABLE", TEXT_REASON),
    ),
)
def test_original_ref_none_unit_type_precedence_is_preserved(
    tmp_path, lhs, rhs, lhs_unit, rhs_unit, missing, state, reason
):
    fx = _consistency_fixture(tmp_path)
    assertions = ConsistencyAssertionMaterializer().materialize(
        artifact_state_ref=fx["artifact_ref"],
        validation_subject_hash=fx["run"].subject_key.subject_hash,
        assertions=(
            {"source_unit_ref": "lhs", "canonical_text": "left", "typed_value": lhs, "value_unit": lhs_unit},
            {"source_unit_ref": "rhs", "canonical_text": "right", "typed_value": rhs, "value_unit": rhs_unit},
        ),
    )
    candidate = _rebind(
        fx["binding"].formal_relation_candidate,
        "formal_candidate_ref", "formal_candidate_hash",
        lhs_assertion_ref=assertions[0].assertion_ref,
        rhs_assertion_ref=assertions[1].assertion_ref,
    )
    binding = _rebind(
        fx["binding"], "binding_ref", "binding_hash",
        member_assertion_refs=sorted(item.assertion_ref for item in assertions),
        formal_relation_candidate=candidate.model_dump(mode="json"),
    )
    assertion_map = {item.assertion_ref: item for item in assertions}
    if missing:
        assertion_map.pop(candidate.lhs_assertion_ref)
    guard = FormalExecutabilityGuard().evaluate(binding=binding, assertions_by_ref=assertion_map)
    assert guard.state.value == state
    assert guard.reason_codes == (reason,)
    with pytest.raises(ConsistencyIntegrityError, match="EXECUTABLE"):
        ExactConsistencyEvaluator().evaluate(
            binding=binding, executability=guard, assertions_by_ref=assertion_map
        )


def test_no_formal_candidate_remains_not_executable(tmp_path):
    fx = _consistency_fixture(tmp_path, formal=False)
    assert fx["executability"].state is FormalExecutabilityState.NOT_EXECUTABLE
    assert fx["executability"].reason_codes == ("NO_FORMAL_CANDIDATE",)
    assert fx["evaluation"] is None


@pytest.mark.parametrize(
    ("relation", "state"),
    (
        (ArtifactConsistencyRelation.CONSISTENT, ValidationSemanticState.PASS),
        (ArtifactConsistencyRelation.INCONSISTENT, ValidationSemanticState.FAIL),
        (ArtifactConsistencyRelation.INDETERMINATE, ValidationSemanticState.INDETERMINATE),
    ),
)
def test_same_returned_semantic_relation_preserves_all_three_states_without_second_call(
    tmp_path, relation, state
):
    fx = _consistency_fixture(tmp_path, lhs_value="完整陈述甲", rhs_value="完整陈述乙")
    calls = []
    row = {
        "relation_materiality": "MATERIAL_RELATION",
        "semantic_relation_description": "same already returned relation row",
        "formal_relation_candidate": fx["binding"].formal_relation_candidate,
        "relation": relation.value,
        "reason_codes": ("EXISTING_MODEL_ROW",),
        "physical_model_call_ref": "model-call:already-returned",
    }

    def already_returned_row(assertions):
        calls.append(tuple(item.assertion_ref for item in assertions))
        return row

    semantic = SemanticConsistencyEvaluator(
        semantic_evaluator=already_returned_row,
        intermediate_runtime=fx["runtime"],
    )
    binding = semantic.bind(
        validation_run_id=fx["run"].validation_run_id,
        validation_scope="DOCUMENT_GLOBAL",
        scope_subject_ref="document:consistency",
        assertions=fx["assertions"],
    )
    guard = FormalExecutabilityGuard().evaluate(
        binding=binding, assertions_by_ref=fx["assertion_map"]
    )
    assert guard.state is FormalExecutabilityState.NOT_EXECUTABLE
    result = semantic.evaluate(
        binding=binding,
        relation=ArtifactConsistencyRelation(row["relation"]),
        observation_codes=row["reason_codes"],
    )
    ref = semantic.commit_evaluation(
        validation_run_id=fx["run"].validation_run_id, result=result
    )
    assert fx["runtime"].resolve_typed(ref, SemanticConsistencyEvaluationResultSchema) == result
    fact = ConsistencyProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        plan=fx["plan"], conformance=fx["conformance"], binding=binding, evaluation=result,
    )
    assert fact.semantic_state is state
    assert fact.payload["observation_codes"] == ["EXISTING_MODEL_ROW"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "materiality",
    (
        ConsistencyRelationMateriality.NO_MATERIAL_RELATION,
        ConsistencyRelationMateriality.INDETERMINATE,
    ),
)
def test_nonmaterial_and_unresolved_materiality_cannot_be_projected_as_a_pass(
    tmp_path, materiality
):
    fx = _consistency_fixture(tmp_path, formal=False)
    binding = _rebind(
        fx["binding"], "binding_ref", "binding_hash", relation_materiality=materiality.value
    )
    result = fx["semantic"].evaluate(
        binding=binding, relation=ArtifactConsistencyRelation.CONSISTENT
    )
    with pytest.raises(ConsistencyIntegrityError, match="only MATERIAL_RELATION"):
        ConsistencyProjector.project(
            validation_run_id=fx["run"].validation_run_id,
            subject_hash=fx["run"].subject_key.subject_hash,
            plan=fx["plan"], conformance=fx["conformance"], binding=binding, evaluation=result,
        )
