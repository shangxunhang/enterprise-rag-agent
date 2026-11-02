"""Actual REC consumer handoff and source-bound canceled-owner read controls."""
from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import InvocationRecoveryAction
from apps.enterprise_document.schemas.agent_rag_v1 import UnderlyingInvocationStatus
from apps.enterprise_document.schemas.section_execution_graph import SECTION_EXECUTION_GRAPH_STATE_VERSION
from apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery import RunningInvocationRecoveryService
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimVerificationRevisionRefSchema
from core.runtime.execution_control import (
    WorkflowExecutionControl, WorkflowExecutionCancelled, activate_execution_control,
    checkpoint_current_execution,
)
from core.runtime.side_effects import LogicalOperationRef
from backend.tests.test_fpbca_bcd_reconciliation import native, _persist, _reconcile
from backend.tests.test_fpbca_bcd_reconciliation_runtime import production_writer_cut


@pytest.mark.parametrize("field", ["task_id", "execution_id", "invocation_id", "ordinal"])
def test_native_writer_transport_identity_requires_exact_canonical_ordinal(native, field):
    _result, result_ref, generation_ref = _persist(native)
    request = native["request"]
    changed = {"task_id": "foreign-task", "execution_id": "foreign-execution",
               "invocation_id": request.invocation_id + "-junk",
               "ordinal": request.invocation_id.rsplit("a", 1)[0] + "a2"}[field]
    if field == "ordinal":
        request = request.model_copy(update={"invocation_id": changed, "execution_id": changed + ":execution"})
    else:
        request = request.model_copy(update={field: changed})
    assert _reconcile(native, ref=result_ref, generation_ref=generation_ref, request=request)["outcome"] == "INDETERMINATE"


def test_unbound_completion_provider_and_policy_flags_never_authorize_new_call(native):
    service = RunningInvocationRecoveryService(writer_execution_store=native["reopen"]())
    for flags in ({"completion_event_persisted": True},
                  {"result_persisted": True, "result_ref": "unbound-provider-output"},
                  {"underlying_queryable": True, "underlying_status": UnderlyingInvocationStatus.COMPLETED},
                  {"underlying_queryable": True, "underlying_status": UnderlyingInvocationStatus.RUNNING}):
        decision = service.decide(native["request"].model_copy(update=flags), native["context"])
        assert decision.action is InvocationRecoveryAction.DO_NOT_RESUME
        assert decision.reason_code == "INDETERMINATE" and not decision.starts_new_call


def test_actual_mainline_committed_read_handoff_uses_normal_route_and_typed_refs_without_writer_call(production_writer_cut):
    cut = production_writer_cut
    graph, payload = cut["graph"], cut["payload"]
    state = {"section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
             "section_payload": graph.codec.dump_payload(payload)}
    before = cut["store"].database_path.read_bytes()
    with patch.object(graph.coordinator, "invoke_pre_write_writer", side_effect=AssertionError("fresh Writer call")):
        update = graph._invoke_pre_write_writer(state)
    recovered = graph.codec.restore_payload({"section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
                                            "section_payload": update["section_payload"]})
    assert recovered.writer_execution_ref == cut["ref"]
    assert recovered.writer_generation_invocation_ref == cut["generation_ref"]
    assert recovered.pending_writer_generation_invocation is None
    assert recovered.section.model_dump(mode="json") == cut["result"].model_dump(mode="json")["artifact"]
    assert recovered.route == "prepare_paragraph_pipeline"
    assert cut["store"].database_path.read_bytes() == before and len(cut["calls"]) == 1


def test_actual_mainline_wrapper_identity_drifts_and_canceled_consumer_preserve_exact_source(production_writer_cut):
    cut = production_writer_cut
    graph, payload = cut["graph"], cut["payload"]
    recovery = graph.running_invocation_recovery
    before = cut["store"].database_path.read_bytes()
    for field in ("task_id", "run_id", "attempt_id", "input_snapshot_version", "idempotency_key",
                  "execution_id", "invocation_id"):
        changed = cut["invocation"].model_copy(update={field: "changed"})
        assert recovery.reconcile_writer(payload, changed)["outcome"] == "INDETERMINATE", field
    for field, changed in (
        ("writer_execution_ref", cut["ref"].model_copy(update={"result_sha256": "0" * 64})),
        ("writer_generation_invocation_ref", cut["generation_ref"].model_copy(update={"invocation_sha256": "0" * 64})),
        ("active_execution_lineage", payload.active_execution_lineage.model_copy(update={"input_snapshot_version": "changed"})),
        ("writer_logical_execution_key", cut["key"].model_copy(update={"write_instance_id": "changed"})),
    ):
        assert recovery.reconcile_writer(payload.model_copy(update={field: changed}))["outcome"] == "INDETERMINATE", field
    control = WorkflowExecutionControl.with_timeout(execution_id="revoked-read-consumer", timeout_seconds=60)
    control.cancel("explicit_cancel")
    with activate_execution_control(control):
        assert recovery.reconcile_writer(payload)["outcome"] == "RECOVERED_DURABLE_RESULT"
        state = {"section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
                 "section_payload": graph.codec.dump_payload(payload)}
        with pytest.raises(WorkflowExecutionCancelled):
            graph._invoke_pre_write_writer(state)
    assert control.is_cancelled and not cut["control"].is_cancelled
    assert cut["store"].database_path.read_bytes() == before and len(cut["calls"]) == 1


def test_actual_read_handoff_keeps_existing_context_currentness_guard(production_writer_cut):
    cut = production_writer_cut
    graph = cut["graph"]
    composer = graph.coordinator.require_pre_write_services().writer_invocation.builder.target_context_composer
    state = {"section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
             "section_payload": graph.codec.dump_payload(cut["payload"])}
    before = cut["store"].database_path.read_bytes()
    with patch.object(composer, "validate_pre_model_call", side_effect=ValueError("exact Context source is stale")), \
            patch.object(graph.coordinator, "invoke_pre_write_writer", side_effect=AssertionError("fresh Writer call")):
        with pytest.raises(ValueError, match="exact Context source is stale"):
            graph._invoke_pre_write_writer(state)
    assert cut["store"].database_path.read_bytes() == before and len(cut["calls"]) == 1


@pytest.mark.parametrize("production_writer_cut", [True], indirect=True)
def test_actual_technical_failure_native_read_precedes_c1_terminalization(production_writer_cut):
    cut = production_writer_cut
    recovery = cut["graph"].running_invocation_recovery
    original, observations = recovery.reconcile_writer, []
    def observe(payload, invocation=None):
        view = original(payload, invocation)
        observations.append((view["outcome"], cut["engine"].lifecycle_service.load_run_lifecycle(cut["run_id"]).status.value))
        return view
    with patch.object(recovery, "reconcile_writer", observe):
        cut["finish"]()
    assert ("DURABLE_FAILURE", "running") in observations
    assert cut["engine"].lifecycle_service.load_run_lifecycle(cut["run_id"]).status.value == "failed"
    assert len(cut["calls"]) == 1


@pytest.mark.parametrize("production_writer_cut", [True], indirect=True)
def test_actual_terminal_attempt_exact_read_never_restores_commit_or_writer_entry(production_writer_cut):
    cut = production_writer_cut
    cut["finish"]()
    lifecycle = cut["engine"].lifecycle_service
    assert lifecycle.load_run_lifecycle(cut["run_id"]).status.value == "failed"
    before = {path:path.read_bytes() for path in (lifecycle.path, cut["store"].database_path)}
    view = cut["graph"].running_invocation_recovery.reconcile_writer(cut["payload"])
    assert view["outcome"] == "DURABLE_FAILURE" and view["result"] == cut["result"]
    with pytest.raises(ValueError):
        lifecycle.assert_attempt_committable(run_id=cut["run_id"],
            attempt_scope_ref=f"section:{cut['payload'].section_id}",
            attempt_id=cut["invocation"].attempt_id,
            input_snapshot_version=cut["invocation"].input_snapshot_version)
    state = {"section_graph_state_version": SECTION_EXECUTION_GRAPH_STATE_VERSION,
             "section_payload": cut["graph"].codec.dump_payload(cut["payload"])}
    from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import PreWriteWriterExecutionFailed
    with patch.object(cut["graph"].coordinator, "invoke_pre_write_writer", side_effect=AssertionError("terminal writer re-entry")):
        with pytest.raises((ValueError, PreWriteWriterExecutionFailed)):
            cut["graph"]._invoke_pre_write_writer(state)
    assert {path:path.read_bytes() for path in before} == before and len(cut["calls"]) == 1


def test_canceled_support_read_requires_committed_owner_not_candidate_or_ack(tmp_path):
    from backend.tests.test_multi_agent_v3_claim_support_runtime import _setup_support_runtime
    _env, _claims, _claim, subject, runtime, context, calls, _state = _setup_support_runtime(tmp_path)
    target = runtime.materialize_evidence_target_set(subject, ["evidence:C1"], "target-set-v1")
    candidate = runtime.evaluate_support_candidate(subject, target, "support-contract-v1", context).candidate
    service = RunningInvocationRecoveryService(support_runtime=runtime)
    control = WorkflowExecutionControl.with_timeout(execution_id="canceled-support-reader", timeout_seconds=60)
    control.cancel("explicit_cancel")
    fingerprint = runtime.support_semantic_fingerprint(candidate)
    prospective_ref = runtime._support_ref(candidate, fingerprint)
    prospective_op = runtime._operation_ref(subject=subject, target=target,
        support_semantic_contract_version=candidate.support_semantic_contract_version, context=context)
    before_intermediate = runtime.ledger_path.read_bytes() if runtime.ledger_path.exists() else None
    intermediate_calls = dict(calls)
    with patch.object(runtime, "_ack_side_effect", side_effect=AssertionError("candidate read cannot ACK")), activate_execution_control(control):
        assert service.reconcile_support(prospective_op, fingerprint, prospective_ref)["outcome"] == "INDETERMINATE"
    assert (runtime.ledger_path.read_bytes() if runtime.ledger_path.exists() else None) == before_intermediate
    assert calls == intermediate_calls
    record = runtime.commit_support(candidate, context)
    frame = json.loads(runtime.ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    operation = LogicalOperationRef(**frame["operation_ref"])
    before, old_calls = runtime.ledger_path.read_bytes(), dict(calls)
    with patch.object(runtime, "_ack_side_effect", side_effect=AssertionError("read cannot ACK")), activate_execution_control(control):
        assert service.reconcile_support(operation, frame["semantic_fingerprint"], record.support_ref)["result"] == record
        assert service.reconcile_support(operation, frame["semantic_fingerprint"], candidate)["outcome"] == "INDETERMINATE"
        assert service.reconcile_support(replace(operation, attempt_id="foreign-attempt"), frame["semantic_fingerprint"], record.support_ref)["outcome"] == "INDETERMINATE"
        with pytest.raises(WorkflowExecutionCancelled):
            checkpoint_current_execution()
    assert runtime.ledger_path.read_bytes() == before and calls == old_calls


def test_canceled_verifier_read_requires_exact_commit_revision_and_no_replay_ack(tmp_path):
    from backend.tests.test_multi_agent_v3_claim_verification_runtime import (
        _setup_verification_chain, _verifier_candidate, _verification_context,
    )
    env, runtime, claim, binding, _support_runtime, support, _state = _setup_verification_chain(tmp_path)
    service = RunningInvocationRecoveryService(verification_runtime=runtime)
    candidate = _verifier_candidate(claim, binding, support)
    context = _verification_context(env, candidate.verification_occurrence_ref)
    operation = runtime._verification_operation(claim_record=claim, binding_record=binding,
        support_record=support, verifier_candidate=candidate, context=context)
    fingerprint = runtime.verification_commit_semantic_fingerprint(claim_record=claim,
        binding_record=binding, support_record=support, verifier_candidate=candidate)
    prospective_ref = runtime._verification_result_ref(claim_record=claim,
        operation_ref=operation, semantic_fingerprint=fingerprint)
    assert runtime.resolve_claim_head(claim.claim_ref) is None
    prospective_revision = ClaimVerificationRevisionRefSchema(claim_ref=claim.claim_ref, verification_revision=1)
    before_intermediate = runtime.ledger_path.read_bytes()
    control = WorkflowExecutionControl.with_timeout(execution_id="canceled-verifier-reader", timeout_seconds=60)
    control.cancel("explicit_cancel")
    with patch.object(runtime, "_reconcile_coordination", side_effect=AssertionError("candidate read cannot ACK")), activate_execution_control(control):
        assert service.reconcile_verification(operation, fingerprint, prospective_ref, prospective_revision)["outcome"] == "INDETERMINATE"
    assert runtime.ledger_path.read_bytes() == before_intermediate
    committed = runtime.commit_verification(claim.claim_ref, binding.binding_facts_ref, support.support_ref,
        candidate, None, context)
    frame = json.loads(runtime.ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    operation = LogicalOperationRef(**frame["operation_ref"])
    before = runtime.ledger_path.read_bytes()
    result_ref, revision_ref = committed.verification_result.verification_result_ref, committed.verification_revision.revision_ref
    with patch.object(runtime, "_reconcile_coordination", side_effect=AssertionError("read cannot reconcile/ACK")), activate_execution_control(control):
        view = service.reconcile_verification(operation, frame["semantic_fingerprint"], result_ref, revision_ref)
        assert view["result"] == committed.verification_result and view["revision"] == committed.verification_revision
        assert service.reconcile_verification(operation, frame["semantic_fingerprint"], candidate, revision_ref)["outcome"] == "INDETERMINATE"
        assert service.reconcile_verification(replace(operation, logical_operation_id="foreign-operation"), frame["semantic_fingerprint"], result_ref, revision_ref)["outcome"] == "INDETERMINATE"
        assert service.reconcile_verification(operation, frame["semantic_fingerprint"], result_ref,
            revision_ref.model_copy(update={"verification_revision": 2}))["outcome"] == "INDETERMINATE"
        with pytest.raises(WorkflowExecutionCancelled):
            checkpoint_current_execution()
    assert runtime.ledger_path.read_bytes() == before
