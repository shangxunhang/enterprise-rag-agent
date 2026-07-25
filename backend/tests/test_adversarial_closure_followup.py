from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from application.mainline_service import MainlineApplicationService
from application.project_input_factory import ProjectInputFactory
from core.runtime.execution_control import (
    WorkflowExecutionControl,
    activate_execution_control,
)
from core.runtime.side_effects import (
    JsonlSideEffectCommitStore,
    SideEffectClaimStatus,
)
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from data_capture.lifecycle import (
    DataCaptureLifecycleStore,
    DataCaptureTransitionService,
)
from data_capture.migrations import migrate_data_capture_record_v1
from eval.rag.engine.rag_eval_case_schema import (
    migrate_legacy_rag_eval_case_v1_payload,
)
from rag.data_capture.rag_run_capture import RagRunCapture
from rag.schema.cleaned_text_unit import migrate_legacy_cleaned_text_unit_payload
from schemas.agent import AgentResultSchema
from schemas.data_capture import DataCaptureRecordSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


NOW = "2026-07-24T00:00:00+00:00"


def _capture() -> DataCaptureRecordSchema:
    return DataCaptureRecordSchema(
        record_id="capture-torn",
        capture_type="eval_samples",
        capture_stage="candidate",
        task_id="task-torn",
        run_id="run-torn",
        created_at=NOW,
    )


def _task() -> TaskSchema:
    return TaskSchema(
        task_id="task-torn",
        run_id="run-torn",
        task_type="scheme_generation",
        user_input="生成方案",
        project_input={},
        created_at=NOW,
    )


@pytest.mark.parametrize("_iteration", range(50))
def test_same_id_concurrent_finalize_executes_callback_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _iteration: int,
) -> None:
    store = JsonlSideEffectCommitStore(tmp_path / "side-effects.jsonl")
    start = threading.Barrier(2)
    both_claims_attempted = threading.Event()
    claim_count = 0
    claim_count_lock = threading.Lock()
    observed: list[str] = []
    observed_lock = threading.Lock()
    errors: list[BaseException] = []
    summaries: list[dict[str, int]] = []

    original_claim = store.claim

    def tracked_claim(*args, **kwargs):
        nonlocal claim_count
        claim = original_claim(*args, **kwargs)
        with claim_count_lock:
            claim_count += 1
            if claim_count >= 2:
                both_claims_attempted.set()
        return claim

    monkeypatch.setattr(store, "claim", tracked_claim)

    def callback() -> None:
        assert both_claims_attempted.wait(timeout=2)
        with observed_lock:
            observed.append("effect")

    controls = []
    for execution_id in ("attempt-a", "attempt-b"):
        control = WorkflowExecutionControl.with_timeout(
            execution_id=execution_id,
            timeout_seconds=5,
            side_effect_store=store,
        )
        control.defer_external_commit(
            callback,
            description="same logical effect",
            side_effect_id="same-effect",
        )
        controls.append(control)

    def run(control: WorkflowExecutionControl) -> None:
        try:
            start.wait(timeout=2)
            summary = control.finalize_external_commits(attempt_succeeded=True)
            summaries.append(summary)
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(control,)) for control in controls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert observed == ["effect"]
    assert sum(item["committed"] for item in summaries) == 1
    assert sum(item["already_committed"] for item in summaries) == 1
    assert sum(item["already_in_progress"] for item in summaries) == 0


@pytest.mark.parametrize("_iteration", range(50))
def test_two_store_instances_atomically_compete_for_same_claim(
    tmp_path: Path,
    _iteration: int,
) -> None:
    path = tmp_path / "side-effects.jsonl"
    stores = [JsonlSideEffectCommitStore(path), JsonlSideEffectCommitStore(path)]
    start = threading.Barrier(2)
    acquired = []
    statuses: list[SideEffectClaimStatus] = []

    def run(index: int) -> None:
        start.wait(timeout=2)
        claim = stores[index].claim(
            "same-effect",
            execution_id=f"attempt-{index}",
            description="same logical effect",
        )
        statuses.append(claim.status)
        if claim.acquired:
            acquired.append((stores[index], claim))

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(status.value for status in statuses) == [
        "acquired",
        "already_in_progress",
    ]
    assert len(acquired) == 1
    assert acquired[0][0].release(acquired[0][1], reason="test complete") is True


def test_callback_failure_releases_claim_and_successful_retry_is_terminal(
    tmp_path: Path,
) -> None:
    store = JsonlSideEffectCommitStore(tmp_path / "side-effects.jsonl")
    attempts = 0

    def callback() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected callback failure")

    first = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-first",
        timeout_seconds=5,
        side_effect_store=store,
    )
    first.defer_external_commit(
        callback,
        description="retryable effect",
        side_effect_id="retryable-effect",
    )
    with pytest.raises(RuntimeError, match="injected callback failure"):
        first.finalize_external_commits(attempt_succeeded=True)

    retry = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-retry",
        timeout_seconds=5,
        side_effect_store=JsonlSideEffectCommitStore(store.path),
    )
    retry.defer_external_commit(
        callback,
        description="retryable effect",
        side_effect_id="retryable-effect",
    )
    assert retry.finalize_external_commits(attempt_succeeded=True)["committed"] == 1

    terminal_retry = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-terminal-retry",
        timeout_seconds=5,
        side_effect_store=JsonlSideEffectCommitStore(store.path),
    )
    terminal_retry.defer_external_commit(
        callback,
        description="retryable effect",
        side_effect_id="retryable-effect",
    )
    summary = terminal_retry.finalize_external_commits(attempt_succeeded=True)
    assert summary["already_committed"] == 1
    assert attempts == 2


def _fail_first_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
    store: JsonlSideEffectCommitStore,
) -> None:
    original = store.mark_committed
    calls = 0

    def flaky_mark(claim):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected acknowledgement failure")
        return original(claim)

    monkeypatch.setattr(store, "mark_committed", flaky_mark)


def test_data_capture_adapter_write_once_survives_acknowledgement_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = JsonlSideEffectCommitStore(tmp_path / "side-effects.jsonl")
    _fail_first_acknowledgement(monkeypatch, ledger)
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

    first = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-first",
        timeout_seconds=5,
        commit_scope_id="run:step:stable",
        side_effect_store=ledger,
    )
    stage(first)
    with pytest.raises(OSError, match="acknowledgement failure"):
        first.finalize_external_commits(attempt_succeeded=True)

    retry = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-retry",
        timeout_seconds=5,
        commit_scope_id="run:step:stable",
        side_effect_store=ledger,
    )
    stage(retry)
    assert retry.finalize_external_commits(attempt_succeeded=True)["committed"] == 1

    path = tmp_path / "captures" / "eval_samples" / "run_eval_samples.jsonl"
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1


def test_rag_capture_adapter_write_once_survives_acknowledgement_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = JsonlSideEffectCommitStore(tmp_path / "side-effects.jsonl")
    _fail_first_acknowledgement(monkeypatch, ledger)
    output = tmp_path / "rag-runs.jsonl"
    capture = RagRunCapture(output)

    def stage(control: WorkflowExecutionControl) -> None:
        with activate_execution_control(control):
            capture.capture({"run_id": "run-rag", "query": "混合检索"})

    first = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-first",
        timeout_seconds=5,
        commit_scope_id="run:rag:stable",
        side_effect_store=ledger,
    )
    stage(first)
    with pytest.raises(OSError, match="acknowledgement failure"):
        first.finalize_external_commits(attempt_succeeded=True)

    retry = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-retry",
        timeout_seconds=5,
        commit_scope_id="run:rag:stable",
        side_effect_store=ledger,
    )
    stage(retry)
    assert retry.finalize_external_commits(attempt_succeeded=True)["committed"] == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_capture_trailing_torn_event_recovers_last_complete_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    DataCaptureLifecycleStore(root).initialize(_capture())
    event_path = root / "lifecycle" / "events" / "capture-torn.jsonl"
    with event_path.open("ab") as handle:
        handle.write(b'{"schema_version":')

    recovered = DataCaptureLifecycleStore(root).load_current("capture-torn")
    assert recovered.capture_stage == "candidate"
    assert recovered.lifecycle_revision == 0


def test_capture_torn_initial_append_can_be_initialized_cleanly(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    event_path = root / "lifecycle" / "events" / "capture-torn.jsonl"
    event_path.parent.mkdir(parents=True)
    event_path.write_bytes(b'{"schema_version":')

    initialized = DataCaptureLifecycleStore(root).initialize(_capture())
    assert initialized.lifecycle_revision == 0
    assert len(event_path.read_text(encoding="utf-8").splitlines()) == 1


def test_capture_recovers_revision_one_then_continues_without_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    store = DataCaptureLifecycleStore(root)
    store.initialize(_capture())
    store.transition(
        "capture-torn",
        "evaluated",
        transition_service=DataCaptureTransitionService(),
        reason="evaluated",
        authority="test",
        expected_revision=0,
    )
    event_path = root / "lifecycle" / "events" / "capture-torn.jsonl"
    with event_path.open("ab") as handle:
        handle.write(b'{"schema_version":')

    restarted = DataCaptureLifecycleStore(root)
    recovered = restarted.load_current("capture-torn")
    assert recovered.lifecycle_revision == 1
    assert recovered.capture_stage == "evaluated"
    accepted = restarted.transition(
        "capture-torn",
        "accepted",
        transition_service=DataCaptureTransitionService(),
        reason="policy accepted",
        authority="test",
        policy_decision="accepted",
        expected_revision=1,
    )
    assert accepted.lifecycle_revision == 2
    assert len(event_path.read_text(encoding="utf-8").splitlines()) == 3


def test_capture_middle_corruption_with_later_event_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "captures"
    store = DataCaptureLifecycleStore(root)
    store.initialize(_capture())
    store.transition(
        "capture-torn",
        "evaluated",
        transition_service=DataCaptureTransitionService(),
        reason="evaluated",
        authority="test",
    )
    store.transition(
        "capture-torn",
        "accepted",
        transition_service=DataCaptureTransitionService(),
        reason="accepted",
        authority="test",
        policy_decision="accepted",
    )
    event_path = root / "lifecycle" / "events" / "capture-torn.jsonl"
    lines = event_path.read_bytes().splitlines(keepends=True)
    event_path.write_bytes(lines[0] + b'{"corrupted":\n' + lines[2])

    with pytest.raises(ValueError, match="invalid capture lifecycle event"):
        DataCaptureLifecycleStore(root).load_current("capture-torn")


def test_task_trailing_torn_event_recovers_last_complete_revision(
    tmp_path: Path,
) -> None:
    manager = JsonlTaskManager(tmp_path / "tasks")
    manager.create_task(_task())
    event_path = tmp_path / "tasks" / "task-torn_state.jsonl"
    with event_path.open("ab") as handle:
        handle.write(b'{"schema_version":')

    recovered = JsonlTaskManager(tmp_path / "tasks").get_task("task-torn")
    assert recovered.status == ExecutionStatus.PENDING
    assert recovered.revision == 0


def test_task_torn_initial_append_can_be_created_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    root.mkdir(parents=True)
    event_path = root / "task-torn_state.jsonl"
    event_path.write_bytes(b'{"schema_version":')

    created = JsonlTaskManager(root).create_task(_task())
    assert created.status == ExecutionStatus.PENDING
    assert created.revision == 0
    assert len(event_path.read_text(encoding="utf-8").splitlines()) == 1


def _success_result() -> AgentResultSchema:
    return AgentResultSchema(
        result_id="result-torn",
        task_id="task-torn",
        run_id="run-torn",
        agent_name="SupervisorAgent",
        agent_type="supervisor",
        status=ExecutionStatus.SUCCESS,
        result_type="workflow_result",
    )


def test_task_recovers_running_then_retries_success_without_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tasks"
    manager = JsonlTaskManager(root)
    task = _task()
    manager.create_task(task)
    manager.mark_running(task, expected_revision=0)
    event_path = root / "task-torn_state.jsonl"
    with event_path.open("ab") as handle:
        handle.write(b'{"schema_version":')

    restarted = JsonlTaskManager(root)
    running = restarted.get_task("task-torn")
    assert running.status == ExecutionStatus.RUNNING
    assert running.revision == 1
    success = restarted.mark_success(
        task,
        _success_result(),
        expected_revision=1,
    )
    assert success.status == ExecutionStatus.SUCCESS
    assert success.revision == 2
    assert len(event_path.read_text(encoding="utf-8").splitlines()) == 3


def test_task_middle_corruption_with_later_event_fails_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tasks"
    manager = JsonlTaskManager(root)
    task = _task()
    manager.create_task(task)
    manager.mark_running(task)
    manager.mark_success(task, _success_result())
    event_path = root / "task-torn_state.jsonl"
    lines = event_path.read_bytes().splitlines(keepends=True)
    event_path.write_bytes(lines[0] + b'{"corrupted":\n' + lines[2])

    with pytest.raises(ValueError, match="invalid task event"):
        JsonlTaskManager(root).get_task("task-torn")


@pytest.mark.parametrize("invalid", [[], "", 0, False, [["task_id", "t"]]])
def test_demo_adapter_rejects_falsy_non_mapping_before_defaults(invalid) -> None:
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            "t",
            "q",
            invalid,
            allow_demo_defaults=True,
        )


@pytest.mark.parametrize("empty_seed", [None, {}])
def test_demo_adapter_accepts_only_none_or_mapping_empty_seed(empty_seed) -> None:
    result = ProjectInputFactory().build(
        "t",
        "q",
        empty_seed,
        allow_demo_defaults=True,
    )
    assert result.task_id == "t"
    assert result.user_query == "q"


def _load_mainline_runtime_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "mainline_runtime.py"
    spec = importlib.util.spec_from_file_location(
        "followup_mainline_runtime",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "invalid",
    [[], "", 0, False, [["task_id", "t"]]],
)
def test_demo_invalid_seed_is_rejected_by_all_real_ingress_paths(
    invalid,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            "t",
            "q",
            invalid,
            allow_demo_defaults=True,
        )

    helper = _load_mainline_runtime_module()
    with pytest.raises(ValidationError):
        helper.build_project_input(
            "t",
            "q",
            invalid,
            allow_demo_defaults=True,
        )

    with pytest.raises(ValidationError):
        MainlineApplicationService().run(
            project_root=tmp_path,
            user_input="q",
            run_id="run-invalid-demo",
            task_id="t",
            output_root=tmp_path / "output",
            project_input=invalid,
            allow_demo_defaults=True,
        )


@pytest.mark.parametrize(
    "migration,args",
    [
        (migrate_data_capture_record_v1, ()),
        (migrate_legacy_cleaned_text_unit_payload, ()),
        (
            migrate_legacy_rag_eval_case_v1_payload,
            (),
        ),
    ],
)
def test_explicit_migrations_reject_non_object_containers(
    migration,
    args,
) -> None:
    kwargs = (
        {"fallback_sample_id": "legacy-1"}
        if migration is migrate_legacy_rag_eval_case_v1_payload
        else {}
    )
    with pytest.raises(TypeError):
        migration([["schema_version", "legacy"]], *args, **kwargs)
