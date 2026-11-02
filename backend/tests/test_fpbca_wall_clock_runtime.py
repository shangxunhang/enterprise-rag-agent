"""Fresh local controls for aggregate clock observation and retained owners.

These tests never recover an old Run/control or call an external Provider.
Observation limits release fixture work; they do not cancel production.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionRunner
from core.runtime.execution_control import (
    WorkflowExecutionControl, WorkflowExecutionCancelled, current_execution_control,
)
from core.runtime.side_effects import InMemorySideEffectCommitStore, JsonlSideEffectCommitStore
from core.hashing import canonical_sha256


NOW = "2026-10-08T00:00:00+00:00"


def _launch(callback):
    box = {}
    def run():
        try:
            box["value"] = callback()
        except BaseException as exc:
            box["error"] = exc
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def _runner_call(runner, callback, *, threshold=.02):
    return runner.execute(callback, execution_id="fresh-wall-clock-worker",
        commit_scope_id="fresh-wall-clock-operation", timeout_seconds=threshold,
        thread_name="wall-clock-control", attempt_succeeded=lambda value: True)


def test_healthy_worker_stays_registered_and_authorized_after_aggregate_threshold():
    runner = WorkflowNodeExecutionRunner(now_iso=lambda: NOW)
    entered, release = threading.Event(), threading.Event()
    controls = []
    def work(control):
        controls.append(control); entered.set(); assert release.wait(3)
        control.checkpoint()
        return {"actual_value":"completed"}
    thread, box = _launch(lambda: _runner_call(runner, work))
    try:
        assert entered.wait(1)
        thread.join(.08)  # Observer budget only; no production cancellation.
        assert thread.is_alive(), "aggregate clock returned a synthetic terminal outcome"
        assert runner.active_worker_count == 1
        assert not runner.wait_for_idle(.01)
        assert controls[0].deadline_exceeded and not controls[0].should_stop
        assert not controls[0].is_cancelled
        release.set(); thread.join(2)
        assert "error" not in box
        assert box["value"].kind == "ok"
        assert box["value"].value == {"actual_value":"completed"}
    finally:
        release.set(); thread.join(3); assert runner.wait_for_idle(3)


@pytest.mark.parametrize("failure_type", [RuntimeError, TimeoutError])
def test_late_concrete_owner_exception_retains_identity_and_is_not_clock_timeout(failure_type):
    runner = WorkflowNodeExecutionRunner(now_iso=lambda: NOW)
    entered, release = threading.Event(), threading.Event()
    failure = failure_type("concrete local operation failure")
    failure.c1_triggering_attempt_id = "exact-local-child-attempt"
    def work(control):
        entered.set(); assert release.wait(3)
        raise failure
    thread, box = _launch(lambda: _runner_call(runner, work))
    try:
        assert entered.wait(1); thread.join(.08); release.set(); thread.join(2)
        assert "error" not in box
        outcome = box["value"]
        assert outcome.kind == "error"
        assert outcome.exception is failure
        assert outcome.exception.c1_triggering_attempt_id == "exact-local-child-attempt"
        assert not outcome.execution_control.is_cancelled
    finally:
        release.set(); thread.join(3); assert runner.wait_for_idle(3)


def test_elapsed_control_checkpoint_and_commit_keep_exact_authority():
    control = WorkflowExecutionControl.with_timeout(execution_id="elapsed-fresh", timeout_seconds=0)
    assert control.deadline_exceeded and control.remaining_seconds() == 0
    assert not control.should_stop and not control.is_cancelled
    control.checkpoint()
    calls = []
    control.defer_external_commit(lambda: calls.append("exact-native-commit"), description="fresh-local",
        side_effect_id="fresh-local-effect", semantic_fingerprint="a" * 64)
    summary = control.finalize_external_commits(attempt_succeeded=True)
    assert calls == ["exact-native-commit"] and summary["committed"] == 1
    assert not control.is_cancelled
    metadata = control.metadata()
    assert metadata["remaining_seconds"] == 0
    assert metadata["wall_clock_authority"] == "observability_only"
    assert metadata["deadline_exceeded"] is True


@pytest.mark.parametrize("reason", ["explicit_user_cancel", "deadline_exceeded"])
def test_explicit_or_historically_revoked_control_never_regains_commit_authority(reason):
    control = WorkflowExecutionControl.with_timeout(execution_id="already-revoked", timeout_seconds=0)
    calls = []
    assert control.cancel(reason)
    assert control.should_stop and control.cancel_reason == reason
    assert not issubclass(WorkflowExecutionCancelled, Exception)
    for operation in (control.checkpoint,
        lambda: control.defer_external_commit(lambda: calls.append("forbidden"), description="late"),
        lambda: control.finalize_external_commits(attempt_succeeded=True)):
        with pytest.raises(WorkflowExecutionCancelled): operation()
    assert calls == [] and control.is_cancelled


def test_actual_cancel_stops_controller_but_keeps_physical_worker_tracked():
    runner = WorkflowNodeExecutionRunner(now_iso=lambda: NOW)
    entered, release = threading.Event(), threading.Event()
    def work(control):
        entered.set(); assert release.wait(3); control.checkpoint()
    thread, box = _launch(lambda: _runner_call(runner, work, threshold=.5))
    try:
        assert entered.wait(1)
        assert runner.cancel_active_workers("explicit_operator_cancel") == 1
        thread.join(1)
        assert not thread.is_alive() and "error" not in box
        assert box["value"].kind == "cancelled"
        assert box["value"].execution_control.cancel_reason == "explicit_operator_cancel"
        assert runner.active_worker_count == 1 and not runner.wait_for_idle(.01)
    finally:
        release.set(); thread.join(3); assert runner.wait_for_idle(3)


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_native_duplicate_claim_wait_survives_elapsed_and_honors_explicit_cancel(tmp_path, persistent, cancel_waiter):
    native = JsonlSideEffectCommitStore(tmp_path / "effects.jsonl") if persistent else InMemorySideEffectCommitStore()
    entered, release, wait_entered = threading.Event(), threading.Event(), threading.Event()
    calls, waits = [], []
    class TrackingNativeStore:
        def __getattr__(self, name): return getattr(native, name)
        def wait_for_resolution(self, side_effect_id, *, timeout_seconds):
            waits.append(timeout_seconds); wait_entered.set()
            return native.wait_for_resolution(side_effect_id, timeout_seconds=timeout_seconds)
    owner = WorkflowExecutionControl.with_timeout(execution_id="real-owner", timeout_seconds=60, side_effect_store=native)
    def commit_once():
        entered.set(); assert release.wait(3); calls.append("owner")
    owner.defer_external_commit(commit_once, description="exact shared effect", side_effect_id="same-native-effect",
        semantic_fingerprint="a" * 64)
    owner_thread, owner_box = _launch(lambda: owner.finalize_external_commits(attempt_succeeded=True))
    waiter = WorkflowExecutionControl.with_timeout(execution_id="native-waiter", timeout_seconds=60,
        side_effect_store=TrackingNativeStore())
    waiter.defer_external_commit(lambda: calls.append("duplicate"), description="exact shared effect",
        side_effect_id="same-native-effect", semantic_fingerprint="a" * 64)
    waiter_thread = None
    try:
        assert entered.wait(1)
        waiter.deadline_monotonic = time.monotonic() + .02
        waiter_thread, waiter_box = _launch(lambda: waiter.finalize_external_commits(attempt_succeeded=True))
        assert wait_entered.wait(1)
        waiter_thread.join(.08)
        assert waiter_thread.is_alive() and not waiter.is_cancelled
        assert waits and all(value > 0 for value in waits) and len(waits) < 20
        if cancel_waiter:
            waiter.cancel("explicit_wait_cancel"); waiter_thread.join(1)
            assert isinstance(waiter_box.get("error"), WorkflowExecutionCancelled)
            assert waiter.cancel_reason == "explicit_wait_cancel"
        release.set(); owner_thread.join(2); waiter_thread.join(2)
        assert "error" not in owner_box and calls == ["owner"]
        if not cancel_waiter:
            assert "error" not in waiter_box and waiter_box["value"]["already_committed"] == 1
        if persistent:
            events = [json.loads(line) for line in (tmp_path / "effects.jsonl").read_text().splitlines()]
            assert sum(row["event_type"] == "committed" for row in events) == 1
    finally:
        release.set(); owner_thread.join(3)
        if waiter_thread is not None: waiter_thread.join(3)


def test_late_external_commit_error_is_kept_as_concrete_owner_failure():
    runner = WorkflowNodeExecutionRunner(now_iso=lambda: NOW)
    entered, release = threading.Event(), threading.Event()
    failure = OSError("actual local commit failed")
    def bad_commit(): raise failure
    def work(control):
        entered.set(); assert release.wait(3)
        control.defer_external_commit(bad_commit, description="local storage error",
            side_effect_id="failed-native-effect", semantic_fingerprint="b" * 64)
        return "actual result"
    thread, box = _launch(lambda: _runner_call(runner, work))
    try:
        assert entered.wait(1); thread.join(.08); release.set(); thread.join(2)
        assert "error" not in box and box["value"].kind == "external_commit_error"
        assert box["value"].exception is failure
    finally:
        release.set(); thread.join(3); assert runner.wait_for_idle(3)


def test_generic_graph_accepts_actual_success_after_elapsed_and_reports_observation():
    from agent.agent_registry import AgentRegistry
    from agent.base_agent import BaseAgent
    from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
    from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
    from schemas.agent import AgentResultSchema
    from schemas.status import ExecutionStatus
    from backend.tests.test_step_15_graph_state_workflow_engine import _state
    entered, release = threading.Event(), threading.Event()
    class LocalAgent(BaseAgent):
        agent_name = "WallClockLocal"; agent_type = "sub_agent"
        def run(self, state):
            entered.set(); assert release.wait(3)
            current_execution_control().checkpoint()
            state.structured_facts.append({"fact":"actual late result"})
            return AgentResultSchema(result_id="late-actual", task_id=state.task_id, run_id=state.run_id,
                agent_name=self.agent_name, agent_type=self.agent_type, status=ExecutionStatus.SUCCESS,
                result_type="local", result={"actual":True})
    registry = AgentRegistry(); registry.register(LocalAgent())
    workflow = WorkflowDefinitionSchema(workflow_id="wall-clock-local", workflow_name="Observation",
        task_type="test", workflow_version="v1", created_at=NOW, steps=[WorkflowStepSchema(step_id="local",
            step_name="local", step_type="agent", target_name="WallClockLocal", output_keys=["structured_facts"],
            write_paths=["structured_facts"], timeout_seconds=.02, order=1)])
    engine = LangGraphWorkflowEngine(registry)
    thread, box = _launch(lambda: engine.execute(workflow, _state()))
    try:
        assert entered.wait(1); thread.join(.08)
        assert thread.is_alive()
        release.set(); thread.join(2)
        assert "error" not in box and box["value"].status is ExecutionStatus.SUCCESS
        output = box["value"].node_outputs[0]
        assert output.status is ExecutionStatus.SUCCESS
        assert output.metadata["execution_control"]["cancel_requested"] is False
        assert "observation" in box["value"].metadata["timeout_guard"]
    finally:
        release.set(); thread.join(3); assert engine.wait_for_idle(3)


def test_historical_typed_timeout_still_suppresses_overlap_retry():
    from agent.agent_registry import AgentRegistry
    from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
    from backend.tests.test_step_15_graph_state_workflow_engine import _state, _workflow
    engine = LangGraphWorkflowEngine(AgentRegistry())
    state, workflow = _state(), _workflow(); step = workflow.steps[0]
    node_input = engine.projector.build_node_input(step=step, state=state, workflow=workflow)
    control = WorkflowExecutionControl.with_timeout(execution_id="historically-canceled", timeout_seconds=0)
    control.cancel("deadline_exceeded")
    output = engine._build_timeout_output(step=step, node_input=node_input, state=state,
        started_at=NOW, latency_ms=1, execution_control=control, worker_still_active=True)
    assert output.result.error.error_code == "WORKFLOW_NODE_TIMEOUT"
    assert not output.result.error.retryable
    assert engine._is_retryable(output, budget_remaining=True) is False
    assert control.is_cancelled


def test_actual_parallel_native_results_cross_parent_threshold_then_continue_without_harness_mutation(tmp_path, monkeypatch):
    from application.mainline_service import MainlineApplicationService
    from core.config import get_settings
    from eval.full_chain.contracts import StageObservation
    from apps.enterprise_document.services.scheme_writer.writer_invocation import PreWriteWriterInvocationService
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import InterProcessFileLock
    from apps.enterprise_document.schemas.pre_write_writer import WriterExecutionRefSchema, WriterGenerationInvocationRefSchema
    from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, WriterExecutionState
    from backend.tests.test_multi_agent_v3_workflow_recovery import (
        _W13InspectingFactory, _W13TwoSectionNormalization, _w13_two_section_project_input,
        _w13_current_new_task_project_input, _w15_parallel_budget_policy_file, _latest_w13_top_level_payload,
    )
    run_id, task_id = "wall_clock_healthy_parallel", "task_wall_clock_healthy_parallel"
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(_w15_parallel_budget_policy_file(tmp_path)))
    environment = {"USE_REAL_RAG_TOOL":"false", "FAKE_RAG_SCENARIO":"default", "FAKE_LLM_SCENARIO":"default",
        "ENABLE_AGENT_SELF_RAG":"false", "ENABLE_SEMANTIC_GATE":"false"}
    settings = replace(get_settings(), data_root=tmp_path, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True)
    ready, release, lock = threading.Event(), threading.Event(), threading.Lock()
    native_rows, controls = [], []
    original = PreWriteWriterInvocationService.execute_initial
    def held_native(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        key = result.logical_execution_key; stored = self.execution_store.get_writer_execution(key)
        assert stored is not None and stored.state is WriterExecutionState.SUCCEEDED
        result_ref = WriterExecutionRefSchema(logical_execution_key=key, result_sha256=canonical_sha256(result.model_dump(mode="json")))
        generation_ref = WriterGenerationInvocationRefSchema(logical_execution_key=key, invocation_sha256=stored.semantic_fingerprint)
        cold = SQLitePreWriteExecutionStore(self.execution_store.database_path, run_commit_guard=self.execution_store.run_commit_guard,
            require_run_commit_guard=self.execution_store.require_run_commit_guard)
        assert cold.resolve_writer_execution(result_ref) == result
        assert canonical_sha256(cold.resolve_writer_generation_invocation(generation_ref).model_dump(mode="json")) == stored.semantic_fingerprint
        with lock:
            native_rows.append({"key":key.model_dump(mode="json"), "state":stored.state.value,
                "input_fingerprint":stored.semantic_fingerprint, "result_ref":result_ref.model_dump(mode="json"),
                "generation_ref":generation_ref.model_dump(mode="json"), "cold_reopen_exact":True})
            controls.append(current_execution_control())
            if len(native_rows) == 2: ready.set()
        assert release.wait(30)
        return result
    class ObservingFactory(_W13InspectingFactory):
        def build(self, **kwargs):
            supervisor = super().build(**kwargs); engine = supervisor.workflow_engine
            engine.node_contracts["execute_sections"] = replace(engine.node_contracts["execute_sections"], timeout_seconds=5)
            return supervisor
    factory = ObservingFactory(_W13TwoSectionNormalization(), force_parallel=True)
    def actual_run():
        return MainlineApplicationService(supervisor_factory=factory).run(project_root=Path(__file__).resolve().parents[2],
            user_input="生成 Wave13 双章节并行验证文档", run_id=run_id, task_id=task_id, output_root=tmp_path,
            clean_existing=True, settings=settings, project_input=_w13_current_new_task_project_input(_w13_two_section_project_input(task_id)).model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False)
    thread = None
    with patch.dict("os.environ", environment), patch.object(PreWriteWriterInvocationService, "execute_initial", held_native):
        try:
            thread, box = _launch(actual_run)
            assert ready.wait(12), "fixture failed to reach both actual native Writer results"
            engine = factory.engines[0]; life = engine.lifecycle_service
            assert len(factory.parallel_spies[0].batches[0]) == 2
            before = life.path.read_bytes()
            # Stop only observation after threshold; production remains untouched.
            threading.Event().wait(max(0, controls[0].deadline_monotonic - time.monotonic()) + .1)
            thread.join(.02)
            with life._lock, InterProcessFileLock(life.lock_path):
                runs, attempts = life._with_state()
                current = life._current_attempts_for_run(attempts, run_id)
                snapshot = {"run":runs[run_id].model_dump(mode="json"),
                    "attempts":[row.model_dump(mode="json") for row in current]}
            observation = StageObservation(unit_id="F02", subject_id=run_id,
                source_segment="test-harness-observer", observed_execution_status="INCOMPLETE",
                measurement_state="UNMEASURED", payload={"evaluation_result":"TEST_TIME_BUDGET_EXHAUSTED",
                    "production_failure":False, "snapshot":snapshot, "native_rows":native_rows,
                    "control_metadata":[control.metadata() for control in controls],
                    "canonical_bytes_unchanged":before == life.path.read_bytes()})
            (tmp_path / "wall_clock_observer_cut.json").write_text(json.dumps(asdict(observation), ensure_ascii=False, indent=2), encoding="utf-8")
            print("WALL_CLOCK_OBSERVER_CUT " + json.dumps(asdict(observation), ensure_ascii=False))
            assert thread.is_alive(), "aggregate wall-clock ended healthy actual composition"
            assert len(native_rows) == 2 and len({control.execution_id for control in controls}) == 1
            assert all(control.deadline_exceeded and not control.is_cancelled and not control.should_stop for control in controls)
            assert before == life.path.read_bytes()
            assert snapshot["run"]["status"] == "running" and len(current) == 2
            assert all(row.status.value == "running" for row in current)
            release.set(); thread.join(20)
            assert not thread.is_alive() and "error" not in box
            payload = _latest_w13_top_level_payload(engine)
            assert len(payload.section_execution_results) == 2
            assert all(item.can_run_in_parallel and not item.requires_global_context and not item.depends_on for item in payload.document_plan.sections)
            assert life.load_run_lifecycle(run_id).status.value == "success"
            assert len(native_rows) == 2
            execution = box["value"]["supervisor_result"]["result"]["workflow_execution"]
            assert "observation" in execution["metadata"]["timeout_guard"]
        finally:
            release.set()
            if thread is not None: thread.join(20)
            if factory.engines: assert factory.engines[0].wait_for_idle(5)
