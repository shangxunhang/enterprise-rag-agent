"""REC/T1-R implementation controls: real native owners, no external calls.

These controls replace the prior audit's authored policy oracle with the actual
recovery service and workflow deadline entry points. Native SUCCESS/failure
fixtures are authored local data, never Provider/business acceptance evidence.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import threading
import time

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction, RunningInvocationRecoveryContextSchema,
    RunningInvocationRecoveryInputSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunIdentitySchema, RunLifecycleStatus
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery import RunningInvocationRecoveryService
from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterLogicalExecutionKey, WriterExecutionRefSchema, WriterExecutionResultSchema,
    WriterExecutionStatus, WriterExecutionFailureKind, WriterGenerationInvocationSchema,
    WriterGenerationInvocationRefSchema,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, WriterExecutionState
from core.runtime.execution_control import (
    WorkflowExecutionControl, WorkflowExecutionCancelled, activate_execution_control,
    checkpoint_current_execution,
)
from schemas.common import ErrorSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema


@pytest.fixture
def native(tmp_path):
    life = RunAttemptLifecycleService(tmp_path / "lifecycle.jsonl")
    run = life.claim_new_run(RunIdentitySchema(run_id="rec-run", task_id="rec-task",
        tenant_id="offline", canonical_request_sha256="a" * 64))
    run = life.transition_run(run.identity.run_id, run.lifecycle_revision, RunLifecycleStatus.RUNNING)
    attempt = life.open_attempt(run.identity.run_id, "section:section", "snapshot", attempt_id="rec-old")
    attempt = life.activate_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision)
    key = WriterLogicalExecutionKey(writer_subject_ref=WriterSubjectRefSchema(run_id="rec-run",
        document_id="rec-doc", section_id="section"), write_instance_id="rec-old",
        route_decision_ref={"route_decision_id": "rec-route"})
    bundle = EvidenceBundleSchema(query="offline", context=RAGContextSchema(context_text="", used_context_chars=0))
    operation = "writer-model-call:" + canonical_sha256({"run_id":"rec-run", "section_id":"section",
        "attempt_id":"rec-old", "route_decision_id":"rec-route"})
    generation = WriterGenerationInvocationSchema(writer_subject_ref=key.writer_subject_ref,
        write_instance_id=key.write_instance_id, route_decision_ref=key.route_decision_ref,
        generation_class="normal_write", canonical_evidence_input={**bundle.model_dump(mode="json"),
            "evidence_bundle_sha256":canonical_sha256(bundle.model_dump(mode="json"))},
        unified_writer_input_snapshot={"task_id":"rec-task", "run_id":"rec-run",
            "target_context_package":{"invocation_subject":{"run_id":"rec-run", "attempt_id":"rec-old",
                "input_snapshot_version":"snapshot", "consumer_scope_ref":"section:section",
                "logical_operation_id":operation}}})
    fingerprint = canonical_sha256(generation.model_dump(mode="json"))
    path = tmp_path / "writer.sqlite3"
    def reopen():
        return SQLitePreWriteExecutionStore(path, run_commit_guard=life.assert_run_committable,
            require_run_commit_guard=True)
    request = RunningInvocationRecoveryInputSchema(invocation_id="rec-run:section:writer:a1",
        task_id="rec-task", run_id="rec-run", execution_id="rec-run:section:writer:a1:execution",
        attempt_id="rec-old", input_snapshot_version="snapshot", idempotency_key=operation)
    context = RunningInvocationRecoveryContextSchema(task_id=request.task_id, run_id=request.run_id,
        input_snapshot_version=request.input_snapshot_version, active_execution_ids=[request.execution_id],
        active_attempt_ids=[request.attempt_id])
    return {"life":life, "run":run, "attempt":attempt, "key":key, "generation":generation,
        "fingerprint":fingerprint, "store":reopen(), "reopen":reopen, "request":request,
        "context":context, "operation":operation, "path":path}


def _persist(env, *, failure=False):
    store, key, fingerprint = env["store"], env["key"], env["fingerprint"]
    store.reserve_writer_execution(key, semantic_fingerprint=fingerprint)
    assert store.mark_writer_dispatched(key, semantic_fingerprint=fingerprint)
    if failure:
        result = WriterExecutionResultSchema(logical_execution_key=key, status=WriterExecutionStatus.TECHNICAL_FAILURE,
            failure_kind=WriterExecutionFailureKind.MODEL_TIMEOUT,
            error=ErrorSchema(error_code="OFFLINE_FAILURE", error_type="LocalFixture", message="No Provider"),
            generation_provenance={"offline_authored":True})
    else:
        result = WriterExecutionResultSchema(logical_execution_key=key, status=WriterExecutionStatus.SUCCESS,
            artifact=SchemeSectionSchema(section_id="section", section_title="Offline", section_order=1,
                content="Canonical local result", status="success",
                input={"writer_generation_invocation":env["generation"].model_dump(mode="json")}),
            generation_provenance={"offline_authored":True})
    store.persist_writer_result(key, semantic_fingerprint=fingerprint, result=result)
    ref = WriterExecutionRefSchema(logical_execution_key=key, result_sha256=canonical_sha256(result.model_dump(mode="json")))
    generation_ref = WriterGenerationInvocationRefSchema(logical_execution_key=key, invocation_sha256=fingerprint)
    return result, ref, generation_ref


def _reconcile(env, *, ref=None, generation_ref=None, request=None, key=None, fingerprint=None):
    service = RunningInvocationRecoveryService(writer_execution_store=env["reopen"]())
    return service.reconcile_writer(request or env["request"], key or env["key"],
        fingerprint or env["fingerprint"], result_ref=ref, generation_invocation_ref=generation_ref,
        generation_invocation=env["generation"])


def _assert_closed(decision):
    assert decision.action is InvocationRecoveryAction.DO_NOT_RESUME
    assert decision.starts_new_call is False
    assert decision.reason_code == "INDETERMINATE"


def test_r1_reserved_observation_does_not_authorize_retry_before_late_native_cas(native):
    native["store"].reserve_writer_execution(native["key"], semantic_fingerprint=native["fingerprint"])
    go, ready = threading.Event(), threading.Event()
    def contender():
        own = native["reopen"](); ready.set()
        assert go.wait(5)
        return own.mark_writer_dispatched(native["key"], semantic_fingerprint=native["fingerprint"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contender); assert ready.wait(5)
        observed = native["store"].get_writer_execution(native["key"])
        decision = RunningInvocationRecoveryService().decide(native["request"], native["context"])
        go.set(); assert future.result(timeout=5)
    assert observed.state is WriterExecutionState.RESERVED
    assert native["reopen"]().get_writer_execution(native["key"]).state is WriterExecutionState.DISPATCHED
    _assert_closed(decision)


def test_r2_reserved_reopen_and_absent_remain_indeterminate(native):
    native["store"].reserve_writer_execution(native["key"], semantic_fingerprint=native["fingerprint"])
    row = native["reopen"]().get_writer_execution(native["key"])
    assert row.state is WriterExecutionState.RESERVED and row.restore_condition.value == "NEVER_DISPATCHED"
    _assert_closed(RunningInvocationRecoveryService().decide(native["request"], native["context"]))
    assert _reconcile(native)["outcome"] == "INDETERMINATE"
    absent = native["key"].model_copy(update={"write_instance_id":"absent"})
    assert _reconcile(native, key=absent)["outcome"] == "INDETERMINATE"


def test_r3_dispatched_without_exact_result_remains_indeterminate(native):
    native["store"].reserve_writer_execution(native["key"], semantic_fingerprint=native["fingerprint"])
    native["store"].mark_writer_dispatched(native["key"], semantic_fingerprint=native["fingerprint"])
    assert native["reopen"]().get_writer_execution(native["key"]).result is None
    _assert_closed(RunningInvocationRecoveryService().decide(native["request"], native["context"]))
    assert _reconcile(native)["outcome"] == "INDETERMINATE"


def test_r4_exact_durable_writer_success_is_recovered_without_redispatch(native):
    result, ref, generation_ref = _persist(native)
    before = native["path"].read_bytes()
    view = _reconcile(native, ref=ref, generation_ref=generation_ref)
    assert view["outcome"] == "RECOVERED_DURABLE_RESULT" and view["result"] == result
    assert view["result_ref"] == ref and native["path"].read_bytes() == before
    service = RunningInvocationRecoveryService(writer_execution_store=native["reopen"]())
    decision = service.decide(native["request"], native["context"], writer_key=native["key"],
        writer_semantic_fingerprint=native["fingerprint"], writer_execution_ref=ref,
        writer_invocation_ref=generation_ref)
    assert decision.action is InvocationRecoveryAction.NO_ACTION
    assert decision.reason_code == "RECOVERED_DURABLE_RESULT" and not decision.starts_new_call


def test_r5_exact_terminal_technical_failure_is_durable_failure(native):
    result, ref, _ = _persist(native, failure=True)
    before = native["path"].read_bytes()
    view = _reconcile(native, ref=ref)
    assert view["outcome"] == "DURABLE_FAILURE" and view["result"] == result
    assert view["result_ref"] == ref and native["path"].read_bytes() == before


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("dimension", ["run_id", "attempt_id", "input_snapshot_version", "idempotency_key",
    "write_instance_id", "route", "result_hash", "invocation_hash"])
def test_r6_exact_native_and_caller_identity_drift_is_rejected(native, dimension, failure):
    _result, ref, generation_ref = _persist(native, failure=failure)
    request, key = native["request"], native["key"]
    if dimension in {"run_id", "attempt_id", "input_snapshot_version", "idempotency_key"}:
        request = request.model_copy(update={dimension:"changed"})
    elif dimension == "write_instance_id": key = key.model_copy(update={"write_instance_id":"changed"})
    elif dimension == "route": key = key.model_copy(update={"route_decision_ref":key.route_decision_ref.model_copy(update={"route_decision_id":"changed"})})
    elif dimension == "result_hash": ref = ref.model_copy(update={"result_sha256":"0"*64})
    else: generation_ref = generation_ref.model_copy(update={"invocation_sha256":"0"*64})
    assert "attempt_id" not in WriterLogicalExecutionKey.model_fields
    assert _reconcile(native, ref=ref, generation_ref=generation_ref, request=request, key=key)["outcome"] == "INDETERMINATE"


def test_r7_committed_read_does_not_restore_canceled_control(native):
    result, ref, generation_ref = _persist(native)
    control = WorkflowExecutionControl.with_timeout(execution_id="old-worker", timeout_seconds=60)
    calls = []
    control.defer_external_commit(lambda:calls.append("effect")); control.cancel("workflow_node_deadline")
    with activate_execution_control(control):
        assert _reconcile(native, ref=ref, generation_ref=generation_ref)["result"] == result
        with pytest.raises(WorkflowExecutionCancelled): checkpoint_current_execution()
    with pytest.raises(WorkflowExecutionCancelled): control.defer_external_commit(lambda:calls.append("new"))
    with pytest.raises(WorkflowExecutionCancelled): control.finalize_external_commits(attempt_succeeded=True)
    assert calls == [] and control.is_cancelled


def test_r8_successor_attempt_does_not_grant_old_unresolved_external_retry(native):
    native["store"].reserve_writer_execution(native["key"], semantic_fingerprint=native["fingerprint"])
    native["store"].mark_writer_dispatched(native["key"], semantic_fingerprint=native["fingerprint"])
    successor = native["life"].open_attempt("rec-run", "section:section", "next-snapshot", attempt_id="rec-successor")
    assert successor.identity.attempt_id != native["request"].attempt_id
    _assert_closed(RunningInvocationRecoveryService().decide(native["request"], native["context"]))
    assert _reconcile(native)["outcome"] == "INDETERMINATE"
    assert native["reopen"]().get_writer_execution(native["key"]).state is WriterExecutionState.DISPATCHED


def test_committed_support_recovery_never_evaluates_or_replays_commit(tmp_path):
    from backend.tests.test_multi_agent_v3_claim_support_runtime import _setup_support_runtime
    env, _claims, _claim, subject, runtime, context, calls, _state = _setup_support_runtime(tmp_path)
    target = runtime.materialize_evidence_target_set(subject, ["evidence:C1"], "target-set-v1")
    candidate = runtime.evaluate_support_candidate(subject, target, "support-contract-v1", context).candidate
    record = runtime.commit_support(candidate, context)
    from core.runtime.side_effects import LogicalOperationRef
    frame = json.loads(runtime.ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    operation = LogicalOperationRef(**frame["operation_ref"])
    before = runtime.ledger_path.read_bytes(); old_calls = dict(calls)
    service = RunningInvocationRecoveryService(support_runtime=runtime)
    control = WorkflowExecutionControl.with_timeout(execution_id="support-old", timeout_seconds=60)
    control.cancel("deadline")
    with activate_execution_control(control):
        view = service.reconcile_support(operation, frame["semantic_fingerprint"], record.support_ref)
    assert view["outcome"] == "RECOVERED_DURABLE_RESULT" and view["result"] == record
    assert calls == old_calls and runtime.ledger_path.read_bytes() == before
    assert service.reconcile_support(operation, "0"*64, record.support_ref)["outcome"] == "INDETERMINATE"


def test_committed_verifier_recovery_requires_exact_result_and_revision(tmp_path):
    from backend.tests.test_multi_agent_v3_claim_verification_runtime import (
        _setup_verification_chain, _verifier_candidate, _verification_context,
    )
    from core.runtime.side_effects import LogicalOperationRef
    env, runtime, claim, binding, _support_runtime, support, _state = _setup_verification_chain(tmp_path)
    candidate = _verifier_candidate(claim, binding, support)
    committed = runtime.commit_verification(claim.claim_ref, binding.binding_facts_ref, support.support_ref,
        candidate, None, _verification_context(env, candidate.verification_occurrence_ref))
    frame = json.loads(runtime.ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    operation = LogicalOperationRef(**frame["operation_ref"])
    before = runtime.ledger_path.read_bytes()
    service = RunningInvocationRecoveryService(verification_runtime=runtime)
    result_ref = committed.verification_result.verification_result_ref
    revision_ref = committed.verification_revision.revision_ref
    control = WorkflowExecutionControl.with_timeout(execution_id="verifier-old", timeout_seconds=60)
    control.cancel("deadline")
    with activate_execution_control(control):
        view = service.reconcile_verification(operation, frame["semantic_fingerprint"], result_ref, revision_ref)
    assert view["outcome"] == "RECOVERED_DURABLE_RESULT"
    assert view["result"] == committed.verification_result and view["revision"] == committed.verification_revision
    assert runtime.ledger_path.read_bytes() == before
    changed = revision_ref.model_copy(update={"verification_revision":2})
    assert service.reconcile_verification(operation, frame["semantic_fingerprint"], result_ref, changed)["outcome"] == "INDETERMINATE"


def test_actual_deadline_invokes_native_reconciliation_before_controller_terminalization(native):
    from agent.agent_registry import AgentRegistry
    from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
    from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
    from agent.base_agent import BaseAgent
    from schemas.agent import AgentResultSchema
    from schemas.status import ExecutionStatus
    from backend.tests.test_step_15_graph_state_workflow_engine import _state
    from core.runtime.execution_control import current_execution_control
    result, ref, generation_ref = _persist(native)
    release = threading.Event(); observed = []
    class Slow(BaseAgent):
        agent_name = "RECSlow"; agent_type = "sub_agent"
        def run(self, state):
            release.wait(2)
            return AgentResultSchema(result_id="late", task_id=state.task_id, run_id=state.run_id,
                agent_name=self.agent_name, agent_type=self.agent_type, status=ExecutionStatus.SUCCESS,
                result_type="late", result={})
    def reconcile(**kwargs):
        assert kwargs["execution_control"].is_cancelled
        observed.append("native-read")
        return _reconcile(native, ref=ref, generation_ref=generation_ref)
    registry = AgentRegistry(); registry.register(Slow())
    workflow = WorkflowDefinitionSchema(workflow_id="rec-deadline", workflow_name="REC", task_type="test",
        workflow_version="v1", created_at="2026-10-07T00:00:00+00:00", steps=[WorkflowStepSchema(
            step_id="slow", step_name="slow", step_type="agent", target_name="RECSlow", order=1, timeout_seconds=.01)])
    engine = LangGraphWorkflowEngine(registry, deadline_reconciler=reconcile)
    try:
        execution = engine.execute(workflow, _state())
        assert observed == ["native-read"]
        metadata = execution.node_outputs[0].metadata
        assert metadata["reconciliation"]["outcome"] == "RECOVERED_DURABLE_RESULT"
        assert metadata["retry_suppressed_after_timeout"] is True
        assert execution.node_outputs[0].error.retryable is False
    finally:
        release.set(); assert engine.wait_for_idle(timeout_seconds=3)


def test_deadline_reconciliation_is_bounded_and_cannot_enable_retry():
    from agent.agent_registry import AgentRegistry
    from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
    from agent.runtime.workflow_schema import WorkflowStepSchema
    from agent.runtime.graph_state_ops import GraphStateProjector
    from backend.tests.test_step_15_graph_state_workflow_engine import _state, _workflow
    release = threading.Event()
    def stalled(**kwargs):
        release.wait(2)
        return {"outcome":"RECOVERED_DURABLE_RESULT", "result":None}
    engine = LangGraphWorkflowEngine(AgentRegistry(), deadline_reconciler=stalled,
        reconciliation_timeout_seconds=.03)
    state, workflow = _state(), _workflow()
    step = workflow.steps[0]
    node_input = engine.projector.build_node_input(step=step, state=state, workflow=workflow)
    control = WorkflowExecutionControl.with_timeout(execution_id="bounded-old", timeout_seconds=60)
    control.cancel("workflow_node_deadline")
    started = time.monotonic()
    try:
        output = engine._build_timeout_output(step=step, node_input=node_input, state=state,
            started_at="2026-10-07T00:00:00+00:00", latency_ms=1, execution_control=control,
            worker_still_active=True)
        assert time.monotonic() - started < .5
        assert output.metadata["reconciliation"]["outcome"] == "INDETERMINATE"
        assert not output.error.retryable and control.is_cancelled
    finally:
        release.set(); assert engine.wait_for_idle(timeout_seconds=3)


def test_actual_production_composition_recovers_native_writer_before_c1_failure(tmp_path, monkeypatch):
    """Real composition, local Fake Writer, native persistence and timeout cut."""
    from dataclasses import replace
    from unittest.mock import patch
    from backend.tests.test_phase2_runtime_safety_closure import _run_production_fake
    from apps.enterprise_document.services.scheme_writer.writer_invocation import PreWriteWriterInvocationService
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "1")
    original = PreWriteWriterInvocationService.execute_initial
    persisted, release = threading.Event(), threading.Event()
    reads = []
    def after_native_success(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if not persisted.is_set():
            assert result.status is WriterExecutionStatus.SUCCESS
            persisted.set(); release.wait(4)
        return result
    def configure(engine):
        # Fails on the baseline before dispatch; GREEN executes the real cut.
        actual = engine._reconcile_timed_out_sections
        def observe(**kwargs):
            assert kwargs["execution_control"].is_cancelled
            assert engine.lifecycle_service.load_run_lifecycle(kwargs["payload"].run_id).status is RunLifecycleStatus.RUNNING
            view = actual(**kwargs); reads.append(view); return view
        engine._reconcile_timed_out_sections = observe
        engine.node_contracts["execute_sections"] = replace(engine.node_contracts["execute_sections"],
            timeout_seconds=1.0, max_retries=2)
    try:
        summary, factory = _run_production_fake(tmp_path, run_id="rec_actual_timeout", mutate_engine=configure,
            patchers=(patch.object(PreWriteWriterInvocationService, "execute_initial", after_native_success),))
        assert persisted.is_set() and reads
        recovered = [row for row in reads[-1]["sections"] if row["outcome"] == "RECOVERED_DURABLE_RESULT"]
        assert recovered and recovered[0]["result_ref"]
        assert "RECOVERED_DURABLE_RESULT" in json.dumps(summary, ensure_ascii=False)
        assert summary["terminal_outcome"]["failure_category"] == "timeout"
        assert factory.engines[0].lifecycle_service.load_run_lifecycle("rec_actual_timeout").status is RunLifecycleStatus.FAILED
    finally:
        release.set()
        if "factory" in locals(): assert factory.engines[0].wait_for_idle(timeout_seconds=5)


def test_aggregate_native_parent_deadline_requires_exact_composite_terminal_contract(tmp_path, monkeypatch):
    """Expose a real N-trigger gap; canonical RUNNING is never counted as PASS.

    Two actual concurrent Target Writer invocations persist SUCCESS while their
    shared parent worker is held. The deadline owner has no exception carrying
    one particular Section identity. The frozen C1 postimage still requires one
    exact triggering FAILED Attempt and canceled siblings before terminal Run.
    """
    from dataclasses import replace
    from pathlib import Path
    from unittest.mock import patch
    from application.mainline_service import MainlineApplicationService
    from core.config import get_settings
    from core.runtime.execution_control import current_execution_control
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import InterProcessFileLock
    from backend.tests.test_multi_agent_v3_workflow_recovery import (
        _W13InspectingFactory, _W13TwoSectionNormalization,
        _w13_two_section_project_input, _w13_current_new_task_project_input,
        _w15_parallel_budget_policy_file, _latest_w13_top_level_payload,
    )
    from apps.enterprise_document.services.scheme_writer.writer_invocation import PreWriteWriterInvocationService
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "2")
    run_id, task_id = "rec_n_parent_deadline", "task_rec_n_parent_deadline"
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(_w15_parallel_budget_policy_file(tmp_path)))
    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False,
        trace_enabled=True, data_capture_enabled=True)
    environment = {"USE_REAL_RAG_TOOL":"false", "FAKE_RAG_SCENARIO":"default",
        "FAKE_LLM_SCENARIO":"default", "ENABLE_AGENT_SELF_RAG":"false",
        "ENABLE_SEMANTIC_GATE":"false"}
    original = PreWriteWriterInvocationService.execute_initial
    release = threading.Event(); lock = threading.Lock()
    native_results, deadline_views, primary = [], [], []

    def canonical_current(life):
        with life._lock, InterProcessFileLock(life.lock_path):
            runs, attempts = life._with_state()
            rows = life._current_attempts_for_run(attempts, run_id)
            return {"run":runs[run_id].model_dump(mode="json"),
                "current_attempts":[row.model_dump(mode="json") for row in rows]}

    def hold_two(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        assert result.status is WriterExecutionStatus.SUCCESS
        key = result.logical_execution_key
        stored = self.execution_store.get_writer_execution(key)
        assert stored is not None and stored.state is WriterExecutionState.SUCCEEDED
        assert stored.result == result
        result_ref = WriterExecutionRefSchema(logical_execution_key=key,
            result_sha256=canonical_sha256(stored.result.model_dump(mode="json")))
        generation_ref = WriterGenerationInvocationRefSchema(logical_execution_key=key,
            invocation_sha256=stored.semantic_fingerprint)
        reopened = SQLitePreWriteExecutionStore(self.execution_store.database_path,
            run_commit_guard=self.execution_store.run_commit_guard,
            require_run_commit_guard=self.execution_store.require_run_commit_guard)
        reopened_result = reopened.resolve_writer_execution(result_ref)
        reopened_generation = reopened.resolve_writer_generation_invocation(generation_ref)
        assert reopened_result == result
        assert canonical_sha256(reopened_generation.model_dump(mode="json")) == stored.semantic_fingerprint
        generation_dump = reopened_generation.model_dump(mode="json")
        control = current_execution_control()
        assert control is not None
        row = {"key":key.model_dump(mode="json"), "status":result.status.value,
            "state":stored.state.value, "sha256":result_ref.result_sha256,
            "input_fingerprint":stored.semantic_fingerprint,
            "generation_ref":generation_ref.model_dump(mode="json"),
            "result_ref":result_ref.model_dump(mode="json"),
            "generation_identity":{field:generation_dump[field] for field in (
                "writer_subject_ref", "write_instance_id", "route_decision_ref")},
            "generation_snapshot_sha256":canonical_sha256(generation_dump["unified_writer_input_snapshot"]),
            "database_path":str(reopened.database_path), "cold_reopen_exact":True,
            "control_execution_id":control.execution_id}
        with lock:
            native_results.append(row)
        # The original owner has already persisted SUCCESS; cleanup never calls it again.
        release.wait(30)
        return result

    class DeadlineFactory(_W13InspectingFactory):
        def build(self, **kwargs):
            supervisor = super().build(**kwargs)
            engine = supervisor.workflow_engine
            engine.node_contracts["execute_sections"] = replace(engine.node_contracts["execute_sections"],
                timeout_seconds=5.0, max_retries=2)
            error_from_outcome = engine._error_from_outcome
            def observe(**fields):
                error = error_from_outcome(**fields)
                if fields["node_name"] == "execute_sections" and fields["outcome"].kind == "timeout":
                    outcome = fields["outcome"]
                    view = {"control_cancelled":outcome.execution_control.is_cancelled,
                        "control_execution_id":outcome.execution_control.execution_id,
                        "exception_is_none":outcome.exception is None,
                        "original_error":error.model_dump(mode="json"),
                        **canonical_current(engine.lifecycle_service)}
                    deadline_views.append(view)
                return error
            engine._error_from_outcome = observe
            return supervisor

    factory = DeadlineFactory(_W13TwoSectionNormalization(), force_parallel=True)
    try:
        try:
            with patch.dict("os.environ", environment), patch.object(PreWriteWriterInvocationService, "execute_initial", hold_two):
                summary = MainlineApplicationService(supervisor_factory=factory).run(
                    project_root=Path(__file__).resolve().parents[2],
                    user_input="生成 Wave13 双章节并行验证文档", run_id=run_id, task_id=task_id,
                    output_root=tmp_path, clean_existing=True, settings=settings,
                    project_input=_w13_current_new_task_project_input(_w13_two_section_project_input(task_id)).model_dump(mode="json"),
                    allow_demo_defaults=False, enable_agent_self_rag=False)
            primary.append(summary.get("terminal_outcome", {}))
        except BaseException as exc:
            primary.append({"exception":type(exc).__name__, "message":str(exc)})
        assert len(native_results) == 2, "actual parallel native cut did not reach two Writer results"
        engine = factory.engines[0]
        plan = _latest_w13_top_level_payload(engine).document_plan
        assert len(plan.sections) == 2 and all(item.can_run_in_parallel
            and not item.requires_global_context and not item.depends_on for item in plan.sections)
        assert len({item.execution_group for item in plan.sections}) == 1
        assert len(factory.parallel_spies) == 1 and len(factory.parallel_spies[0].batches) == 1
        assert len(factory.parallel_spies[0].batches[0]) == 2
        assert len(deadline_views) == 1
        deadline = deadline_views[0]
        assert deadline["control_cancelled"] and deadline["exception_is_none"]
        assert deadline["original_error"]["error_code"] == "WORKFLOW_NODE_TIMEOUT"
        assert deadline["original_error"]["failure_category"] == "timeout"
        assert "c1_triggering_attempt_id" not in deadline["original_error"]["details"]
        assert len(deadline["current_attempts"]) == 2
        assert {row["identity"]["attempt_id"] for row in deadline["current_attempts"]} == {
            row["key"]["write_instance_id"] for row in native_results}
        assert {row["control_execution_id"] for row in native_results} == {deadline["control_execution_id"]}
        life = engine.lifecycle_service
        run = life.load_run_lifecycle(run_id)
        attempts = [life.load_attempt(row["key"]["write_instance_id"]) for row in native_results]
        evidence = {"native_results":native_results, "run_status":run.status.value,
            "attempt_statuses":{row.identity.attempt_id:row.status.value for row in attempts},
            "actual_plan":plan.model_dump(mode="json"), "actual_batches":factory.parallel_spies[0].batches,
            "deadline":deadline, "primary":primary,
            "contract":"exact trigger FAILED + siblings CANCELLED + terminal FAILED Run"}
        print("REC_N_NATIVE_DEADLINE_EVIDENCE " + json.dumps(evidence, ensure_ascii=False))
        (tmp_path / "rec_n_native_deadline_evidence.json").write_text(json.dumps(evidence,
            ensure_ascii=False, indent=2), encoding="utf-8")
        assert run.status is RunLifecycleStatus.FAILED, "source blocker: N parent deadline has no exact trigger / canonical terminal"
        assert sorted(row.status.value for row in attempts) == ["cancelled", "failed"]
        assert primary and primary[0].get("failure_category") == "timeout"
    finally:
        release.set()
        if factory.engines: assert factory.engines[0].wait_for_idle(timeout_seconds=5)
