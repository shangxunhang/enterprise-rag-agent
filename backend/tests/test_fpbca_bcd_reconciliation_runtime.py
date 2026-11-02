"""Fresh REC controls on actual native owners and production recovery callers.

Only local Fake Target execution is used. Old aggregate-clock controls and old
receipts remain immutable; these tests create no deadline-reconciliation hook.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from agent.supervisor_agent import SupervisorAgent
from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction, RunningInvocationRecoveryInputSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema, WriterExecutionStatus, WriterGenerationInvocationRefSchema,
)
from apps.enterprise_document.services.scheme_writer.writer_invocation import PreWriteWriterInvocationService
from core.config import get_settings
from core.hashing import canonical_sha256
from core.runtime.execution_control import (
    WorkflowExecutionControl, WorkflowExecutionCancelled, activate_execution_control,
    checkpoint_current_execution, current_execution_control,
)
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, WriterExecutionState
from task.task_manager import JsonlTaskManager
from backend.tests.test_fpbca_bcd_reconciliation import native, _persist, _reconcile
from backend.tests.test_phase4_section_agents_mainline import _latest_section_payloads
from backend.tests.test_multi_agent_v3_workflow_recovery import (
    _W13InspectingFactory, _W13TwoSectionNormalization, _w13_two_section_project_input,
    _w13_current_new_task_project_input, _w15_parallel_budget_policy_file,
)


def test_native_r7_exact_read_retains_revocation_and_never_calls_deferred_effect(native):
    result, ref, generation_ref = _persist(native)
    before = native["path"].read_bytes()
    control = WorkflowExecutionControl.with_timeout(execution_id="historically-revoked-reader", timeout_seconds=60)
    calls = []
    control.defer_external_commit(lambda: calls.append("old-effect"), description="old pending effect")
    control.cancel("explicit_user_cancel")
    with activate_execution_control(control):
        assert _reconcile(native, ref=ref, generation_ref=generation_ref)["result"] == result
        with pytest.raises(WorkflowExecutionCancelled):
            checkpoint_current_execution()
    with pytest.raises(WorkflowExecutionCancelled):
        control.defer_external_commit(lambda: calls.append("new-effect"), description="rejected new effect")
    with pytest.raises(WorkflowExecutionCancelled):
        control.finalize_external_commits(attempt_succeeded=True)
    assert calls == [] and control.is_cancelled
    assert native["path"].read_bytes() == before


def _configure_target(tmp_path, monkeypatch, *, suffix):
    environment = {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false", "SECTION_MAX_CONCURRENCY": "1",
        "GROUNDED_GENERATION_POLICY_FILE": str(_w15_parallel_budget_policy_file(tmp_path))}
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False,
        trace_enabled=True, data_capture_enabled=True)
    task_id, run_id = f"task_rec_actual_{suffix}", f"run_rec_actual_{suffix}"
    factory = _W13InspectingFactory(_W13TwoSectionNormalization())
    project = _w13_current_new_task_project_input(_w13_two_section_project_input(task_id))
    def run():
        return MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=tmp_path, clean_existing=True,
            settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False)
    return factory, run, settings, task_id, run_id


def _pending_writer_payload(graph, *, workflow_namespace, run_id, section_id, key):
    for row in graph.checkpoint_history(workflow_namespace=workflow_namespace,
            run_id=run_id, section_id=section_id):
        values = (row.checkpoint or {}).get("channel_values", {})
        if not isinstance(values.get("section_payload"), dict):
            continue
        payload = graph.codec.restore_payload({
            "section_graph_state_version": values.get("section_graph_state_version"),
            "section_payload": values["section_payload"]})
        if payload.writer_logical_execution_key == key and payload.pending_writer_generation_invocation is not None:
            return payload
    raise AssertionError("actual native Writer cut has no matching typed pending checkpoint")


def _writer_request(payload, life):
    lineage = payload.active_execution_lineage
    assert lineage is not None
    generation = payload.pending_writer_generation_invocation
    subject = generation.model_dump(mode="json")["unified_writer_input_snapshot"]["target_context_package"]["invocation_subject"]
    assert subject["attempt_id"] == lineage.attempt_id
    # The actual ContextInvocationSubject schema intentionally has no snapshot field.
    assert "input_snapshot_version" not in subject
    ordinal = life.load_attempt(lineage.attempt_id).identity.attempt_ordinal
    invocation_id = f"{payload.run_id}:{payload.section_id}:writer:a{ordinal}"
    return RunningInvocationRecoveryInputSchema(invocation_id=invocation_id,
        task_id=payload.task_id, run_id=payload.run_id, execution_id=f"{invocation_id}:execution",
        attempt_id=lineage.attempt_id, input_snapshot_version=lineage.input_snapshot_version,
        idempotency_key=subject["logical_operation_id"])


@pytest.fixture
def production_writer_cut(tmp_path, monkeypatch, request):
    """Hold a real result after persistence and before controller/C1 projection."""
    failure = bool(getattr(request, "param", False))
    factory, run, settings, task_id, run_id = _configure_target(tmp_path, monkeypatch,
        suffix="failure" if failure else "success")
    ready, release = threading.Event(), threading.Event()
    box, cut, calls = {}, {}, []
    original = PreWriteWriterInvocationService.execute_initial
    local_error = TimeoutError("concrete local Writer operation timeout")
    def held(service, *args, **kwargs):
        first = not calls
        calls.append(kwargs["writer_subject_ref"].section_id)
        if first and failure:
            with patch.object(service.section_generation_service,
                    "generate_from_pre_write_invocation", side_effect=local_error):
                result = original(service, *args, **kwargs)
        else:
            result = original(service, *args, **kwargs)
        if first:
            record = service.execution_store.get_writer_execution(result.logical_execution_key)
            assert record is not None and record.result == result
            expected = WriterExecutionState.CONFIRMED_FAILED if failure else WriterExecutionState.SUCCEEDED
            assert record.state is expected
            cold = SQLitePreWriteExecutionStore(service.execution_store.database_path,
                run_commit_guard=service.execution_store.run_commit_guard,
                require_run_commit_guard=service.execution_store.require_run_commit_guard)
            assert cold.get_writer_execution(result.logical_execution_key) == record
            ref = WriterExecutionRefSchema(logical_execution_key=result.logical_execution_key,
                result_sha256=canonical_sha256(result.model_dump(mode="json")))
            generation_ref = WriterGenerationInvocationRefSchema(logical_execution_key=result.logical_execution_key,
                invocation_sha256=record.semantic_fingerprint)
            if not failure:
                assert cold.resolve_writer_execution(ref) == result
                assert canonical_sha256(cold.resolve_writer_generation_invocation(generation_ref).model_dump(mode="json")) == record.semantic_fingerprint
            cut.update(store=service.execution_store, result=result, key=result.logical_execution_key,
                fingerprint=record.semantic_fingerprint, ref=ref, generation_ref=generation_ref,
                control=current_execution_control(), cold_reopen_exact=True)
            ready.set()
            assert release.wait(30), "test observer did not release the held native result"
        return result
    def run_thread():
        try:
            box["summary"] = run()
        except BaseException as exc:
            box["error"] = exc
    def finish():
        release.set(); thread.join(25)
        assert not thread.is_alive(), "actual Target worker did not drain"
        assert "error" not in box, repr(box.get("error"))
        assert factory.engines[0].wait_for_idle(5)
        return box["summary"]
    with patch.object(PreWriteWriterInvocationService, "execute_initial", held):
        thread = threading.Thread(target=run_thread, daemon=True)
        thread.start()
        try:
            assert ready.wait(15), f"native cut setup failed: {box!r}"
            engine = factory.engines[0]
            graph = engine.section_execution_graph
            # Obtain the exact namespace from the durable section payload itself.
            namespace, raw = next((namespace, raw)
                for namespace, raw in _latest_section_payloads(Path(engine.checkpointer.path)).items()
                if raw.get("run_id") == run_id
                and raw.get("section_id") == cut["key"].writer_subject_ref.section_id
                and raw.get("pending_writer_generation_invocation") is not None)
            workflow_namespace = namespace.rsplit("/sections/", 1)[0]
            assert namespace == graph.namespace(workflow_namespace=workflow_namespace,
                section_id=cut["key"].writer_subject_ref.section_id)
            payload = _pending_writer_payload(graph, workflow_namespace=workflow_namespace, run_id=run_id,
                section_id=cut["key"].writer_subject_ref.section_id, key=cut["key"])
            assert canonical_sha256(payload.pending_writer_generation_invocation.model_dump(mode="json")) == cut["fingerprint"]
            invocation = _writer_request(payload, engine.lifecycle_service)
            assert engine.lifecycle_service.load_run_lifecycle(run_id).status.value == "running"
            assert engine.lifecycle_service.load_attempt(invocation.attempt_id).status.value == "running"
            assert not cut["control"].is_cancelled
            cut.update(engine=engine, graph=graph, payload=payload, invocation=invocation,
                factory=factory, calls=calls, finish=finish, task_id=task_id, run_id=run_id, root=tmp_path)
            cut["workflow_namespace"] = workflow_namespace
            print("REC_ACTUAL_NATIVE_CUT " + json.dumps({"run_id":run_id, "task_id":task_id,
                "key":cut["key"].model_dump(mode="json"), "input_fingerprint":cut["fingerprint"],
                "result_ref":cut["ref"].model_dump(mode="json"), "cold_reopen_exact":True,
                "attempt_id":invocation.attempt_id, "snapshot":invocation.input_snapshot_version,
                "logical_operation_id":invocation.idempotency_key,
                "state":"confirmed_failed" if failure else "succeeded"}, ensure_ascii=False))
            yield cut
        finally:
            finish()


def test_actual_production_owner_wiring_and_current_native_result_read(production_writer_cut):
    cut = production_writer_cut
    graph, engine = cut["graph"], cut["engine"]
    recovery = graph.running_invocation_recovery
    coordinator = engine.stage_service.section_execution_coordinator
    service = recovery.service
    assert recovery.lifecycle_service is engine.lifecycle_service
    assert service.writer_execution_store is coordinator.require_pre_write_services().writer_invocation.execution_store
    assert service.support_runtime is coordinator.claim_support_composition.runtime
    assert service.verification_runtime is coordinator.claim_verification_runtime
    before = cut["store"].database_path.read_bytes()
    decision = graph.recover_running_invocation(workflow_namespace=cut["workflow_namespace"],
        run_id=cut["run_id"], section_id=cut["payload"].section_id, invocation=cut["invocation"])
    assert decision.action is InvocationRecoveryAction.NO_ACTION
    assert decision.reason_code == "RECOVERED_DURABLE_RESULT" and not decision.starts_new_call
    assert cut["store"].database_path.read_bytes() == before
    assert len(cut["calls"]) == 1 and not cut["control"].is_cancelled
    for field in ("task_id", "run_id", "attempt_id", "input_snapshot_version", "idempotency_key"):
        changed = cut["invocation"].model_copy(update={field:"changed"})
        view = service.reconcile_writer(changed, cut["key"], cut["fingerprint"],
            result_ref=cut["ref"], generation_invocation_ref=cut["generation_ref"],
            generation_invocation=cut["payload"].pending_writer_generation_invocation)
        assert view["outcome"] == "INDETERMINATE", field
    assert cut["store"].database_path.read_bytes() == before


@pytest.mark.parametrize("production_writer_cut", [True], indirect=True)
def test_actual_owner_failure_is_read_before_c1_and_primary_error_remains(production_writer_cut):
    cut = production_writer_cut
    service = cut["graph"].running_invocation_recovery.service
    before = cut["store"].database_path.read_bytes()
    view = service.reconcile_writer(cut["invocation"], cut["key"], cut["fingerprint"], result_ref=cut["ref"],
        generation_invocation=cut["payload"].pending_writer_generation_invocation)
    assert view["outcome"] == "DURABLE_FAILURE" and view["result"] == cut["result"]
    assert cut["result"].status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert cut["store"].database_path.read_bytes() == before
    assert cut["engine"].lifecycle_service.load_run_lifecycle(cut["run_id"]).status.value == "running"
    cut["finish"]()
    life = cut["engine"].lifecycle_service
    assert life.load_run_lifecycle(cut["run_id"]).status.value == "failed"
    assert life.load_attempt(cut["invocation"].attempt_id).status.value == "failed"
    error = JsonlTaskManager(cut["root"] / "tasks").get_task(cut["task_id"]).error
    assert error.error_code == cut["result"].error.error_code
    assert error.error_type == "TimeoutError" and error.message == cut["result"].error.message
    assert error.details["c1_triggering_attempt_id"] == cut["invocation"].attempt_id
    assert len(cut["calls"]) == 1


@pytest.mark.parametrize("method", ["recover_run", "retry_run"])
def test_actual_application_unproved_reservation_blocks_before_recovery_event(tmp_path, monkeypatch, method):
    factory, run, settings, task_id, run_id = _configure_target(tmp_path, monkeypatch, suffix=method)
    handoffs = []
    def interrupted_handoff(service, *args, **kwargs):
        handoffs.append((service.execution_store, kwargs["writer_subject_ref"], kwargs["write_instance_id"]))
        raise TimeoutError("local pre-dispatch handoff failed with unresolved native reservation")
    with patch.object(PreWriteWriterInvocationService, "execute_initial", interrupted_handoff):
        summary = run()
    assert summary["status"] == "retryable_failed"
    engine = factory.engines[0]
    assert engine.lifecycle_service.load_run_lifecycle(run_id).status.value == "retryable_failed"
    assert len(handoffs) == 1
    payload_rows = [raw for raw in _latest_section_payloads(Path(engine.checkpointer.path)).values()
        if raw.get("run_id") == run_id and raw.get("writer_logical_execution_key")]
    raw = next(item for item in payload_rows if item.get("pending_writer_generation_invocation") is not None)
    from apps.enterprise_document.schemas.pre_write_writer import WriterLogicalExecutionKey
    key = WriterLogicalExecutionKey.model_validate(raw["writer_logical_execution_key"])
    native_owner = SQLitePreWriteExecutionStore(handoffs[0][0].database_path)
    record = native_owner.get_writer_execution(key)
    assert record.state is WriterExecutionState.RESERVED and record.result is None
    assert record.restore_condition.value == "NEVER_DISPATCHED"
    manager = JsonlTaskManager(tmp_path / "tasks")
    task = manager.get_task(task_id)
    before_task = {path.name:path.read_bytes() for path in (tmp_path / "tasks").glob("*") if path.is_file() and not path.name.endswith(".lock")}
    before_lifecycle = engine.lifecycle_service.path.read_bytes()
    before_native = native_owner.database_path.read_bytes()
    event_calls, reentries = [], []
    original_event = JsonlTaskManager.mark_recovery_started
    def observe_event(self, *args, **kwargs):
        event_calls.append(kwargs)
        return original_event(self, *args, **kwargs)
    def reject_unproved_reentry(self, *args, **kwargs):
        reentries.append(kwargs)
        raise AssertionError("unproved native reservation reached graph re-entry")
    with patch.object(JsonlTaskManager, "mark_recovery_started", observe_event), patch.object(SupervisorAgent, "recover", reject_unproved_reentry):
        with pytest.raises(ValueError, match="INDETERMINATE|native.*(proof|effect)|unproved"):
            getattr(MainlineApplicationService(supervisor_factory=factory), method)(
                project_root=Path(__file__).resolve().parents[2], task_id=task_id, run_id=run_id,
                expected_revision=task.revision, recovery_reason="REC native negative-proof control",
                output_root=tmp_path, settings=settings, allow_demo_defaults=True, enable_agent_self_rag=False)
    assert event_calls == [] and reentries == []
    assert {path.name:path.read_bytes() for path in (tmp_path / "tasks").glob("*") if path.is_file() and not path.name.endswith(".lock")} == before_task
    assert engine.lifecycle_service.path.read_bytes() == before_lifecycle
    assert native_owner.database_path.read_bytes() == before_native
    assert native_owner.get_writer_execution(key).state is WriterExecutionState.RESERVED
    assert len(handoffs) == 1
