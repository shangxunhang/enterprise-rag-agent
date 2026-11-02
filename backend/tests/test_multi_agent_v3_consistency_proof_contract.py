"""348 approved positive and indeterminate proof transport acceptance."""
import json

import pytest

from apps.enterprise_document.schemas.post_write.foundation import (
    ExpectedItemExecutionAccountingSchema,
    ValidationCompletionItemSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidatorExecutionRecordSchema,
)
from apps.enterprise_document.schemas.post_write import (
    ArtifactConsistencyRelation, RequiredDimensionConditionSchema,
    ValidationCompletionItemState, ValidationFactFamily,
)
from apps.enterprise_document.services.post_write import (
    ConsistencyIntegrityError, ConsistencyProjector,
    SemanticConsistencyEvaluator, ValidationIntermediateRuntime,
)
from apps.enterprise_document.services.post_write.gate import GateInputIntegrityError
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from apps.enterprise_document.services.post_write.policy import QualityPolicyProjector
from apps.enterprise_document.services.post_write.execution import ValidationCompletionBuilder
from core.hashing import canonical_sha256
import test_post_write_consistency as owner
import test_post_write_delivery_gate as gate_tests
import test_post_write_validation_foundation as foundation_tests


def _proof(run="run-1"):
    return ValidationIntermediateRefSchema(
        validation_run_id=run, event_id="proof-1",
        intermediate_kind="TARGET_ACCOUNTING", intermediate_sha256="a" * 64,
    )


@pytest.mark.parametrize("carrier,legacy", [
    (ValidationCompletionItemSchema, {
        "schema_version": "validation_completion_item_v1",
        "expected_validation_item_ref": "item-1", "required": True,
        "state": "NOT_RUN", "fact_refs": [], "technical_failure_ref": None,
    }),
    (ExpectedItemExecutionAccountingSchema, {
        "expected_validation_item_ref": "item-1", "execution_state": "COMPLETED",
        "fact_refs": [], "technical_failure_ref": None, "provenance_refs": [],
    }),
])
def test_legacy_empty_proof_carriers_preserve_exact_json_and_hash(carrier, legacy):
    value = carrier.model_validate_json(json.dumps(legacy))
    assert value.positive_proof_refs == ()
    assert value.indeterminate_proof_refs == ()
    assert value.model_dump(mode="json") == legacy
    assert canonical_sha256(value.model_dump(mode="json")) == canonical_sha256(legacy)
    assert "positive_proof_refs" not in json.loads(value.model_dump_json())
    assert "indeterminate_proof_refs" not in json.loads(value.model_dump_json())


@pytest.mark.parametrize("state", ["FAIL", "INDETERMINATE", "TECHNICAL_FAILURE", "NOT_RUN", "NOT_APPLICABLE"])
def test_positive_proof_cannot_impersonate_other_terminal_states(state):
    with pytest.raises(ValueError):
        ValidationCompletionItemSchema(
            expected_validation_item_ref="item-1", required=True,
            state=state, positive_proof_refs=(_proof(),),
        )


def test_completion_rejects_foreign_run_proof_and_execution_failure_proof():
    item = ValidationCompletionItemSchema(
        expected_validation_item_ref="item-1", required=True,
        state="PASS", positive_proof_refs=(_proof(),),
    )
    with pytest.raises(ValueError, match="another ValidationRun"):
        ValidationCompletionBuilder.build(
            validation_run_id="foreign", subject_hash="b" * 64, items=(item,),
        )
    with pytest.raises(ValueError, match="positive proof requires COMPLETED"):
        ExpectedItemExecutionAccountingSchema(
            expected_validation_item_ref="item-1", execution_state="TECHNICAL_FAILURE",
            technical_failure_ref={"kind": "timeout"}, positive_proof_refs=(_proof(),),
        )


def _proof_gate(tmp_path, *, with_proof=True, indeterminate=False):
    def create(store, run, item_ref):
        fx = owner._consistency_fixture(tmp_path / "proof-source")
        # This test exercises generic Gate transport, using the real typed plan.
        # The source plan itself is rebound to the exact run by its owner.
        plan = owner.ConsistencyDiscoveryPlanner(profile=fx["profile"]).plan(
            validation_run_id=run.validation_run_id,
            artifact_state_ref=fx["artifact_ref"],
            validation_subject_hash=fx["run"].subject_key.subject_hash,
            assertions=fx["assertions"],
        )
        proof = ValidationIntermediateRuntime(store).commit(
            validation_run_id=run.validation_run_id, producer_ref="post_write_consistency",
            target_ref=item_ref, typed_intermediate=plan,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        )
        stage = foundation_tests._stage_plan()
        envelope = foundation_tests._input_envelope(run.validation_run_id, run.subject_key.subject_hash, stage, item_ref)
        with store.database.read_transaction() as connection:
            sequence = store._next_event_sequence(connection, run.validation_run_id)
        execution_ref = "proof-gate-execution"
        event = store._event(
            validation_run_id=run.validation_run_id, event_id="proof-gate-terminal",
            sequence=sequence, event_kind="VALIDATOR_COMPLETED",
            payload={"validator_execution_ref": execution_ref}, occurred_at_epoch=0.0,
        )
        accounting = ExpectedItemExecutionAccountingSchema(
            expected_validation_item_ref=item_ref, execution_state="COMPLETED",
            positive_proof_refs=() if indeterminate else (proof,),
            indeterminate_proof_refs=(proof,) if indeterminate else (),
        )
        base = dict(
            schema_version="validator_execution_record_v1", validator_execution_ref=execution_ref,
            validation_run_id=run.validation_run_id, subject_hash=run.subject_key.subject_hash,
            validator_id=stage.validator_id, validator_version=stage.validator_version,
            stage_plan_ref=stage.stage_plan_ref, stage_plan_version=stage.stage_plan_version,
            input_hash=envelope.input_hash, terminal_event_id=event.event_id,
            terminal_execution_state="COMPLETED", expected_item_accounting=[accounting.model_dump(mode="json")],
            produced_fact_refs=[], intermediate_refs=[proof.model_dump(mode="json")],
        )
        store.finalize_validator_execution(event, ValidatorExecutionRecordSchema(**base, record_sha256=canonical_sha256(base)),
                                          stage_plan=stage, input_envelope=envelope)
        return (proof,)
    return gate_tests._gate_fixture(
        tmp_path, fact_states=(),
        completion_states=(("consistency:item:1", True,
                            (ValidationCompletionItemState.INDETERMINATE if indeterminate else ValidationCompletionItemState.PASS) if with_proof
                            else ValidationCompletionItemState.NOT_RUN, None),),
        conditions=(RequiredDimensionConditionSchema(
            dimension_id="consistency", fact_families=(ValidationFactFamily.ARTIFACT_CONSISTENCY,),
        ),),
        completion_proof_provider=create if with_proof else None,
    )


@pytest.mark.parametrize("damage", [None, "missing", "corrupt"])
@pytest.mark.parametrize("indeterminate", [False, True])
def test_gate_resolves_proof_exactly_or_fails_closed(tmp_path, damage, indeterminate):
    fx = _proof_gate(tmp_path, indeterminate=indeterminate)
    item = fx["completion"].items[0]
    proof = (item.indeterminate_proof_refs if indeterminate else item.positive_proof_refs)[0]
    if damage:
        with fx["database"].write_transaction(immediate=True) as connection:
            if damage == "missing":
                connection.execute("DELETE FROM validation_execution_events WHERE event_id=?", (proof.event_id,))
            else:
                connection.execute("UPDATE validation_execution_events SET event_json='{}' WHERE event_id=?", (proof.event_id,))
        with pytest.raises(GateInputIntegrityError):
            gate_tests._evaluate(fx)
    else:
        result = gate_tests._evaluate(fx)
        condition = result.quality_policy_projection.condition_results[0]
        assert condition.state.value == ("UNRESOLVED" if indeterminate else "SATISFIED")
        assert (condition.indeterminate_proof_refs if indeterminate else condition.positive_proof_refs) == (proof,)
        assert not fx["validation_store"].list_facts_for_run(fx["run_id"])


def test_gate_does_not_infer_satisfied_from_absent_facts(tmp_path):
    result = gate_tests._evaluate(_proof_gate(tmp_path, with_proof=False))
    assert result.quality_policy_projection.condition_results[0].state.value != "SATISFIED"


def test_cons_348_09_unresolved_materiality_uses_exact_proof_without_fake_fact(tmp_path):
    fx = owner._consistency_fixture(tmp_path)
    semantic = SemanticConsistencyEvaluator(
        semantic_evaluator=lambda _: {
            "relation_materiality": "INDETERMINATE",
            "semantic_relation_description": "The relation scope is ambiguous.",
            "ambiguity_codes": ("SCOPE_UNRESOLVED",),
        }, intermediate_runtime=fx["runtime"],
    )
    binding = semantic.bind(
        validation_run_id=fx["run"].validation_run_id, validation_scope="SECTION",
        scope_subject_ref="section-1", assertions=fx["assertions"],
    )
    proof = semantic.commit_binding(binding)
    evaluation = semantic.evaluate(binding=binding, relation=ArtifactConsistencyRelation.INDETERMINATE)
    with pytest.raises(ConsistencyIntegrityError, match="only MATERIAL_RELATION"):
        ConsistencyProjector.project(
            validation_run_id=fx["run"].validation_run_id,
            subject_hash=fx["run"].subject_key.subject_hash,
            plan=fx["plan"], conformance=fx["conformance"],
            binding=binding, evaluation=evaluation,
        )
    item = ValidationCompletionItemSchema(
        expected_validation_item_ref="consistency:item:1", required=True,
        state="INDETERMINATE", indeterminate_proof_refs=(proof,),
    )
    assert item.state.value == "INDETERMINATE" and not item.fact_refs
    assert not item.positive_proof_refs
    assert fx["store"].resolve_intermediate(proof) == binding.model_dump(mode="json")
    assert not fx["store"].list_facts_for_run(fx["run"].validation_run_id)


@pytest.mark.parametrize("state", ["PASS", "FAIL", "TECHNICAL_FAILURE", "NOT_RUN", "NOT_APPLICABLE"])
def test_indeterminate_proof_is_state_bound(state):
    with pytest.raises(ValueError):
        ValidationCompletionItemSchema(
            expected_validation_item_ref="item-1", required=True, state=state,
            indeterminate_proof_refs=(_proof(),),
        )


def test_indeterminate_requires_explicit_fact_or_proof_and_no_mixed_proofs():
    with pytest.raises(ValueError):
        ValidationCompletionItemSchema(expected_validation_item_ref="item-1", required=True, state="INDETERMINATE")
    with pytest.raises(ValueError):
        ValidationCompletionItemSchema(
            expected_validation_item_ref="item-1", required=True, state="INDETERMINATE",
            positive_proof_refs=(_proof(),), indeterminate_proof_refs=(_proof(),),
        )
    item = ValidationCompletionItemSchema(
        expected_validation_item_ref="item-1", required=True, state="INDETERMINATE",
        indeterminate_proof_refs=(_proof(),),
    )
    with pytest.raises(ValueError, match="another ValidationRun"):
        ValidationCompletionBuilder.build(validation_run_id="foreign", subject_hash="b" * 64, items=(item,))


@pytest.mark.parametrize("damage", ["missing_execution", "different_accounting", "cross_run"])
def test_indeterminate_proof_requires_exact_terminal_execution(tmp_path, damage):
    fx = _proof_gate(tmp_path, indeterminate=True)
    store = fx["validation_store"]
    completion = fx["completion"]
    if damage == "missing_execution":
        with fx["database"].write_transaction(immediate=True) as connection:
            connection.execute("DELETE FROM validator_execution_records")
    elif damage == "different_accounting":
        original = completion.items[0]
        item = ValidationCompletionItemSchema(
            expected_validation_item_ref=original.expected_validation_item_ref,
            required=True, state="PASS", positive_proof_refs=original.indeterminate_proof_refs,
        )
        completion = ValidationCompletionBuilder.build(
            validation_run_id=completion.validation_run_id, subject_hash=completion.subject_hash, items=(item,),
        )
    else:
        # Bypass only the outer schema to exercise persistence's independent guard.
        proof = completion.items[0].indeterminate_proof_refs[0].model_copy(update={"validation_run_id": "foreign"})
        item = completion.items[0].model_copy(update={"indeterminate_proof_refs": (proof,)})
        completion = completion.model_copy(update={"items": (item,)})
    with pytest.raises(PostWritePersistenceIntegrityError):
        store.validate_completion_proof_accounting(completion)


def test_indeterminate_proof_keeps_condition_unresolved_even_with_satisfied_fact(tmp_path):
    fx = _proof_gate(tmp_path, indeterminate=True)
    fact = gate_tests._fact(
        fx["run_id"], fx["completion"].subject_hash, fact_id="satisfied-consistency",
        family=ValidationFactFamily.ARTIFACT_CONSISTENCY,
        state=gate_tests.ValidationSemanticState.PASS, target_ref="material-relation-1",
    )
    result = QualityPolicyProjector._evaluate_condition(
        RequiredDimensionConditionSchema(dimension_id="consistency"), (fact,), fx["completion"],
    )
    assert result.state.value == "UNRESOLVED"
    assert result.indeterminate_proof_refs == fx["completion"].items[0].indeterminate_proof_refs
    assert not result.positive_proof_refs


def test_technical_failure_cannot_hide_behind_indeterminate_proof_even_with_failure_ref():
    with pytest.raises(ValueError):
        ValidationCompletionItemSchema(
            expected_validation_item_ref="item-1", required=True, state="TECHNICAL_FAILURE",
            technical_failure_ref={"kind": "provider_failure"}, indeterminate_proof_refs=(_proof(),),
        )
