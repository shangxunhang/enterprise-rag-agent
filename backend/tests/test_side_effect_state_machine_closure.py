from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from agent.agent_registry import AgentRegistry
from agent.base_agent import BaseAgent
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
from agent.runtime.workflow_schema import (
    WorkflowDefinitionSchema,
    WorkflowStepSchema,
)
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    WorkflowExecutionControl,
    activate_execution_control,
    current_execution_control,
)
from core.runtime.side_effects import (
    JsonlSideEffectCommitStore,
    SideEffectClaimStatus,
)
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from rag.data_capture.rag_run_capture import RagRunCapture
from schemas.agent import AgentResultSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus


def _control(
    store: JsonlSideEffectCommitStore,
    *,
    execution_id: str,
    callback: Callable[[], None],
    timeout_seconds: float = 2.0,
    side_effect_id: str = "shared-effect",
) -> WorkflowExecutionControl:
    control = WorkflowExecutionControl.with_timeout(
        execution_id=execution_id,
        timeout_seconds=timeout_seconds,
        side_effect_store=store,
    )
    control.defer_external_commit(
        callback,
        description="state-machine closure",
        side_effect_id=side_effect_id,
    )
    return control


def _ledger_terminal(path: Path, side_effect_id: str = "shared-effect") -> str:
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matching = [
        event for event in events if event.get("side_effect_id") == side_effect_id
    ]
    assert matching
    return str(matching[-1]["event_type"])


@pytest.mark.parametrize("_iteration", range(50))
def test_failed_owner_cannot_leave_waiter_with_false_success(
    tmp_path: Path,
    _iteration: int,
) -> None:
    store = JsonlSideEffectCommitStore(
        tmp_path / "side-effects.jsonl",
        claim_lease_seconds=0.5,
    )
    owner_entered = threading.Event()
    allow_owner_failure = threading.Event()
    waiter_finished = threading.Event()
    observable: list[str] = []
    results: dict[str, object] = {}

    def failing_owner_callback() -> None:
        owner_entered.set()
        assert allow_owner_failure.wait(timeout=1)
        raise RuntimeError("injected owner failure")

    def waiter_callback() -> None:
        observable.append("waiter")

    owner = _control(
        store,
        execution_id="owner",
        callback=failing_owner_callback,
    )
    waiter = _control(
        JsonlSideEffectCommitStore(store.path, claim_lease_seconds=0.5),
        execution_id="waiter",
        callback=waiter_callback,
    )

    def run_owner() -> None:
        try:
            owner.finalize_external_commits(attempt_succeeded=True)
        except BaseException as exc:
            results["owner_error"] = exc

    def run_waiter() -> None:
        try:
            results["waiter_summary"] = waiter.finalize_external_commits(
                attempt_succeeded=True
            )
        except BaseException as exc:
            results["waiter_error"] = exc
        finally:
            waiter_finished.set()

    owner_thread = threading.Thread(target=run_owner)
    waiter_thread = threading.Thread(target=run_waiter)
    owner_thread.start()
    assert owner_entered.wait(timeout=1)
    waiter_thread.start()

    assert not waiter_finished.wait(timeout=0.05)
    allow_owner_failure.set()
    owner_thread.join(timeout=2)
    waiter_thread.join(timeout=2)

    assert not owner_thread.is_alive()
    assert not waiter_thread.is_alive()
    assert isinstance(results.get("owner_error"), RuntimeError)
    assert "waiter_error" not in results
    assert results["waiter_summary"]["committed"] == 1
    assert observable == ["waiter"]
    assert _ledger_terminal(store.path) == "committed"


def test_release_persistence_failure_does_not_block_expired_lease_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "side-effects.jsonl"
    store = JsonlSideEffectCommitStore(path, claim_lease_seconds=0.05)

    def fail_release(*args, **kwargs) -> bool:
        del args, kwargs
        raise OSError("injected release persistence failure")

    monkeypatch.setattr(store, "release", fail_release)

    def fail_callback() -> None:
        raise RuntimeError("injected callback failure")

    owner = _control(
        store,
        execution_id="owner",
        callback=fail_callback,
    )
    with pytest.raises(RuntimeError, match="injected callback failure"):
        owner.finalize_external_commits(attempt_succeeded=True)

    time.sleep(0.075)
    restarted = JsonlSideEffectCommitStore(path, claim_lease_seconds=0.05)
    recovered = restarted.claim(
        "shared-effect",
        execution_id="recovery",
        description="state-machine closure",
    )

    assert recovered.status == SideEffectClaimStatus.ACQUIRED
    assert restarted.release(recovered, reason="test cleanup") is True


def test_immediate_owner_failure_forces_waiter_to_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JsonlSideEffectCommitStore(tmp_path / "side-effects.jsonl")
    owner_releasing = threading.Event()
    allow_release = threading.Event()
    original_release = store.release
    results: dict[str, object] = {}
    observable: list[str] = []

    def blocking_release(claim, *, reason: str) -> bool:
        owner_releasing.set()
        assert allow_release.wait(timeout=1)
        return original_release(claim, reason=reason)

    monkeypatch.setattr(store, "release", blocking_release)

    owner = _control(
        store,
        execution_id="owner",
        callback=lambda: (_ for _ in ()).throw(RuntimeError("immediate failure")),
    )
    waiter = _control(
        JsonlSideEffectCommitStore(store.path),
        execution_id="waiter",
        callback=lambda: observable.append("waiter"),
    )

    def run(name: str, control: WorkflowExecutionControl) -> None:
        try:
            results[name] = control.finalize_external_commits(
                attempt_succeeded=True
            )
        except BaseException as exc:
            results[f"{name}_error"] = exc

    owner_thread = threading.Thread(target=run, args=("owner", owner))
    waiter_thread = threading.Thread(target=run, args=("waiter", waiter))
    owner_thread.start()
    assert owner_releasing.wait(timeout=1)
    waiter_thread.start()
    time.sleep(0.01)
    allow_release.set()
    owner_thread.join(timeout=2)
    waiter_thread.join(timeout=2)

    assert isinstance(results.get("owner_error"), RuntimeError)
    assert "waiter_error" not in results
    assert results["waiter"]["committed"] == 1
    assert observable == ["waiter"]
    assert _ledger_terminal(store.path) == "committed"


@pytest.mark.parametrize("_iteration", range(50))
def test_three_way_failed_owner_takeover_has_one_successful_callback(
    tmp_path: Path,
    _iteration: int,
) -> None:
    path = tmp_path / "side-effects.jsonl"
    owner_entered = threading.Event()
    allow_owner_failure = threading.Event()
    observable: list[str] = []
    observable_lock = threading.Lock()
    results: dict[str, object] = {}

    def failing_owner() -> None:
        owner_entered.set()
        assert allow_owner_failure.wait(timeout=1)
        raise RuntimeError("first owner failed")

    def successful_waiter() -> None:
        with observable_lock:
            observable.append("effect")

    controls = {
        "owner": _control(
            JsonlSideEffectCommitStore(path),
            execution_id="owner",
            callback=failing_owner,
        ),
        "waiter-b": _control(
            JsonlSideEffectCommitStore(path),
            execution_id="waiter-b",
            callback=successful_waiter,
        ),
        "waiter-c": _control(
            JsonlSideEffectCommitStore(path),
            execution_id="waiter-c",
            callback=successful_waiter,
        ),
    }

    def run(name: str) -> None:
        try:
            results[name] = controls[name].finalize_external_commits(
                attempt_succeeded=True
            )
        except BaseException as exc:
            results[f"{name}_error"] = exc

    owner_thread = threading.Thread(target=run, args=("owner",))
    owner_thread.start()
    assert owner_entered.wait(timeout=1)
    waiter_threads = [
        threading.Thread(target=run, args=(name,))
        for name in ("waiter-b", "waiter-c")
    ]
    for thread in waiter_threads:
        thread.start()
    allow_owner_failure.set()
    owner_thread.join(timeout=2)
    for thread in waiter_threads:
        thread.join(timeout=2)

    assert isinstance(results.get("owner_error"), RuntimeError)
    assert "waiter-b_error" not in results
    assert "waiter-c_error" not in results
    assert observable == ["effect"]
    assert sorted(
        [
            results["waiter-b"]["committed"],
            results["waiter-c"]["committed"],
        ]
    ) == [0, 1]
    assert sum(
        [
            results["waiter-b"]["already_committed"],
            results["waiter-c"]["already_committed"],
        ]
    ) == 1
    assert _ledger_terminal(path) == "committed"


def test_waiter_deadline_is_failure_not_success(tmp_path: Path) -> None:
    path = tmp_path / "side-effects.jsonl"
    owner_entered = threading.Event()
    allow_owner = threading.Event()
    owner_result: dict[str, object] = {}

    def slow_owner() -> None:
        owner_entered.set()
        assert allow_owner.wait(timeout=1)

    owner = _control(
        JsonlSideEffectCommitStore(path),
        execution_id="owner",
        callback=slow_owner,
    )
    waiter = _control(
        JsonlSideEffectCommitStore(path),
        execution_id="waiter",
        callback=lambda: pytest.fail("waiter must not steal a live owner"),
        timeout_seconds=0.05,
    )

    def run_owner() -> None:
        owner_result["summary"] = owner.finalize_external_commits(
            attempt_succeeded=True
        )

    owner_thread = threading.Thread(target=run_owner)
    owner_thread.start()
    assert owner_entered.wait(timeout=1)
    with pytest.raises(WorkflowExecutionCancelled, match="deadline_exceeded"):
        waiter.finalize_external_commits(attempt_succeeded=True)
    assert waiter.cancel_reason == "deadline_exceeded"

    allow_owner.set()
    owner_thread.join(timeout=2)
    assert owner_result["summary"]["committed"] == 1
    assert _ledger_terminal(path) == "committed"


def test_langgraph_cannot_return_success_with_required_effect_in_progress(
    tmp_path: Path,
) -> None:
    class DeferredCommitAgent(BaseAgent):
        agent_name = "DeferredCommitAgent"
        agent_type = "sub_agent"

        def run(self, shared_state) -> AgentResultSchema:
            control = current_execution_control()
            assert control is not None
            control.defer_external_commit(
                lambda: pytest.fail("live owner's callback cannot be stolen"),
                description="blocked required effect",
                side_effect_id="blocked-effect",
            )
            return AgentResultSchema(
                result_id="result",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="test",
                result={"ok": True},
            )

    path = tmp_path / "side-effects.jsonl"
    store = JsonlSideEffectCommitStore(path, claim_lease_seconds=1)
    live_owner = store.claim(
        "blocked-effect",
        execution_id="existing-owner",
        description="blocked required effect",
    )
    registry = AgentRegistry()
    registry.register(DeferredCommitAgent())
    workflow = WorkflowDefinitionSchema(
        workflow_id="side-effect-coordination",
        workflow_name="side-effect coordination",
        task_type="test",
        workflow_version="v1",
        steps=[
            WorkflowStepSchema(
                step_id="commit",
                step_name="commit",
                step_type="agent",
                target_name="DeferredCommitAgent",
                timeout_seconds=0.05,
                order=1,
            )
        ],
        created_at="2026-07-24T00:00:00+00:00",
    )
    state = GraphStateSchema(
        task_id="task",
        run_id="run",
        task_type="test",
        user_input="test side-effect coordination",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="test"),
            task=TaskContextSchema(
                task_id="task",
                run_id="run",
                task_type="test",
            ),
        ),
        created_at="2026-07-24T00:00:00+00:00",
    )

    execution = LangGraphWorkflowEngine(
        registry,
        side_effect_commit_store=store,
    ).execute(workflow, state)

    assert execution.status == ExecutionStatus.FAILED
    assert execution.node_outputs[0].status == ExecutionStatus.FAILED
    assert execution.node_outputs[0].error is not None
    assert execution.node_outputs[0].error.error_code in {
        "WORKFLOW_NODE_CANCELLED",
        "WORKFLOW_NODE_TIMEOUT",
    }
    assert store.release(live_owner, reason="test cleanup") is True


@pytest.mark.parametrize("_iteration", range(50))
def test_short_lease_cannot_steal_live_local_owner(
    tmp_path: Path,
    _iteration: int,
) -> None:
    path = tmp_path / "side-effects.jsonl"
    owner_entered = threading.Event()
    allow_owner = threading.Event()
    active = 0
    maximum_active = 0
    observable = 0
    lock = threading.Lock()
    summaries: list[dict[str, int]] = []
    errors: list[BaseException] = []

    def callback() -> None:
        nonlocal active, maximum_active, observable
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        owner_entered.set()
        assert allow_owner.wait(timeout=1)
        with lock:
            observable += 1
            active -= 1

    controls = [
        _control(
            JsonlSideEffectCommitStore(path, claim_lease_seconds=0.01),
            execution_id=f"attempt-{index}",
            callback=callback,
        )
        for index in range(2)
    ]

    def run(control: WorkflowExecutionControl) -> None:
        try:
            summaries.append(
                control.finalize_external_commits(attempt_succeeded=True)
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run, args=(controls[0],))
    second = threading.Thread(target=run, args=(controls[1],))
    first.start()
    assert owner_entered.wait(timeout=1)
    time.sleep(0.02)
    second.start()
    time.sleep(0.01)
    allow_owner.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert maximum_active == 1
    assert observable == 1
    assert sum(item["committed"] for item in summaries) == 1
    assert sum(item["already_committed"] for item in summaries) == 1
    assert _ledger_terminal(path) == "committed"


def test_dead_owner_thread_does_not_leave_stale_memory_blocking_recovery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "side-effects.jsonl"
    store = JsonlSideEffectCommitStore(path, claim_lease_seconds=0.05)
    acquired: list[object] = []

    def abandoned_owner() -> None:
        acquired.append(
            store.claim(
                "shared-effect",
                execution_id="abandoned",
                description="state-machine closure",
            )
        )

    owner_thread = threading.Thread(target=abandoned_owner)
    owner_thread.start()
    owner_thread.join(timeout=1)
    assert acquired[0].status == SideEffectClaimStatus.ACQUIRED
    time.sleep(0.075)

    restarted = JsonlSideEffectCommitStore(path, claim_lease_seconds=0.05)
    recovered = restarted.claim(
        "shared-effect",
        execution_id="recovered",
        description="state-machine closure",
    )
    assert recovered.status == SideEffectClaimStatus.ACQUIRED
    assert restarted.release(recovered, reason="test cleanup") is True


@pytest.mark.parametrize("adapter_kind", ["data_capture", "rag_capture"])
def test_adapter_write_once_survives_ack_and_release_double_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_kind: str,
) -> None:
    path = tmp_path / "side-effects.jsonl"
    ledger = JsonlSideEffectCommitStore(path, claim_lease_seconds=0.05)
    original_mark = ledger.mark_committed
    original_release = ledger.release
    mark_calls = 0
    release_calls = 0

    def fail_first_mark(claim) -> bool:
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 1:
            raise OSError("injected acknowledgement failure")
        return original_mark(claim)

    def fail_first_release(claim, *, reason: str) -> bool:
        nonlocal release_calls
        release_calls += 1
        if release_calls == 1:
            raise OSError("injected release persistence failure")
        return original_release(claim, reason=reason)

    monkeypatch.setattr(ledger, "mark_committed", fail_first_mark)
    monkeypatch.setattr(ledger, "release", fail_first_release)

    if adapter_kind == "data_capture":
        recorder = JsonlDataCaptureRecorder(tmp_path / "captures")

        def stage(control: WorkflowExecutionControl) -> None:
            with activate_execution_control(control):
                recorder.record(
                    capture_type="eval_samples",
                    capture_stage="candidate",
                    task_id="task",
                    run_id="run",
                    generated_draft_output="draft",
                )

        output = (
            tmp_path
            / "captures"
            / "eval_samples"
            / "run_eval_samples.jsonl"
        )
    else:
        output = tmp_path / "rag-runs.jsonl"
        capture = RagRunCapture(output)

        def stage(control: WorkflowExecutionControl) -> None:
            with activate_execution_control(control):
                capture.capture({"run_id": "run-rag", "query": "混合检索"})

    first = WorkflowExecutionControl.with_timeout(
        execution_id="first",
        timeout_seconds=1,
        commit_scope_id=f"stable:{adapter_kind}",
        side_effect_store=ledger,
    )
    stage(first)
    with pytest.raises(OSError, match="acknowledgement failure"):
        first.finalize_external_commits(attempt_succeeded=True)

    time.sleep(0.075)
    retry = WorkflowExecutionControl.with_timeout(
        execution_id="retry",
        timeout_seconds=1,
        commit_scope_id=f"stable:{adapter_kind}",
        side_effect_store=ledger,
    )
    stage(retry)
    summary = retry.finalize_external_commits(attempt_succeeded=True)

    logical_records = [
        line
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert summary["committed"] == 1
    assert len(logical_records) == 1
    side_effect_id = next(
        json.loads(line)["side_effect_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event_type"] == "committed"
    )
    assert _ledger_terminal(path, side_effect_id) == "committed"
