"""P1 application recovery, retry policy, CAS and cursor reuse tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from multiprocessing import get_context
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from agent.supervisor_agent import SupervisorAgent

from application.mainline_service import MainlineApplicationService
from application.task_factory import TaskFactory
from apps.enterprise_document.runtime.agent_rag_v1.mainline_recovery import (
    MainlineRunningInvocationRecovery,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction,
    RunningInvocationRecoveryInputSchema,
    UnderlyingInvocationStatus,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from core.config import get_settings
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager
from test_phase4_section_agents_mainline import _latest_section_payloads, _run_fake


_ORIGINAL_PRECHECK_PARAGRAPH = SectionExecutionGraph._precheck_paragraph
_ENV = {
    "USE_REAL_RAG_TOOL": "false",
    "FAKE_RAG_SCENARIO": "default",
    "FAKE_LLM_SCENARIO": "default",
    "ENABLE_AGENT_SELF_RAG": "false",
    "ENABLE_SEMANTIC_GATE": "false",
    "SECTION_MAX_CONCURRENCY": "1",
}


def _task(task_id: str = "task-retry-policy", run_id: str = "run-retry-policy"):
    return TaskFactory().build(
        task_id=task_id,
        run_id=run_id,
        user_input="recover the durable run",
        created_at="2026-08-02T00:00:00+00:00",
        allow_demo_defaults=True,
    )


def _retryable_error() -> ErrorSchema:
    return ErrorSchema(
        error_code="TRANSIENT_TIMEOUT",
        error_type="TimeoutError",
        message="transient worker interruption",
        recoverable=True,
        retryable=True,
        failure_category=FailureCategory.TIMEOUT,
        failed_node="execute_sections",
    )


def _retryable_state(manager: JsonlTaskManager, task):
    manager.create_task(task)
    manager.mark_running(task)
    return manager.mark_failed(task, _retryable_error(), retryable=True)


def _multiprocess_recovery_attempt(
    tasks_dir: str,
    task_json: str,
    expected_revision: int,
    start_event,
    ready_queue,
    outcome_queue,
) -> None:
    """Run one recovery CAS in a spawned interpreter process."""

    manager = JsonlTaskManager(tasks_dir)
    task = TaskSchema.model_validate_json(task_json)
    ready_queue.put(os.getpid())
    if not start_event.wait(timeout=30):
        outcome_queue.put(("error", "start barrier timed out"))
        return
    try:
        started = manager.mark_recovery_started(
            task,
            expected_revision=expected_revision,
            recovery_reason="multiprocess_concurrent_retry",
            recovery_policy="retryable_failed_checkpoint_v1",
            max_recovery_count=3,
            retry_only=True,
        )
    except ValueError as exc:
        if "revision conflict" in str(exc):
            outcome_queue.put(("conflict", str(exc)))
        else:
            outcome_queue.put(("error", f"{type(exc).__name__}: {exc}"))
    except BaseException as exc:
        outcome_queue.put(("error", f"{type(exc).__name__}: {exc}"))
    else:
        outcome_queue.put(("won", started.revision))


def test_retryable_failed_only_reenters_running_through_dedicated_event(
    tmp_path: Path,
) -> None:
    task = _task()
    manager = JsonlTaskManager(tmp_path / "tasks")
    failed = _retryable_state(manager, task)
    immutable_hash = failed.canonical_task_request_sha256

    with pytest.raises(ValueError, match="dedicated recovery"):
        manager.mark_running(task)

    started = manager.mark_recovery_started(
        task,
        expected_revision=failed.revision,
        recovery_reason="worker_process_crash",
        recovery_policy="retryable_failed_checkpoint_v1",
        max_recovery_count=3,
        retry_only=True,
    )
    event = started.events[-1]
    assert started.status == ExecutionStatus.RUNNING
    assert started.revision == failed.revision + 1
    assert started.canonical_task_request_sha256 == immutable_hash
    assert event.event_type == "task_retry_started"
    assert event.previous_status == ExecutionStatus.RETRYABLE_FAILED
    assert event.metadata == {
        "recovery_attempt_number": 1,
        "recovery_reason": "worker_process_crash",
        "previous_status": "retryable_failed",
        "previous_revision": failed.revision,
        "new_revision": started.revision,
        "canonical_immutable_request_hash": immutable_hash,
        "recovery_policy": "retryable_failed_checkpoint_v1",
        "max_recovery_count": 3,
        "request_timestamp": event.created_at,
    }

    replayed = JsonlTaskManager(tmp_path / "tasks").get_task(task.task_id)
    assert replayed.revision == started.revision
    assert replayed.metadata["recovery_attempt_count"] == 1


def test_recovery_attempt_limit_and_revision_continuity(tmp_path: Path) -> None:
    task = _task("task-retry-limit", "run-retry-limit")
    manager = JsonlTaskManager(tmp_path / "tasks")
    current = _retryable_state(manager, task)
    for attempt in (1, 2):
        current = manager.mark_recovery_started(
            task,
            expected_revision=current.revision,
            recovery_reason=f"transient_failure_{attempt}",
            recovery_policy="retryable_failed_checkpoint_v1",
            max_recovery_count=2,
            retry_only=True,
        )
        assert current.metadata["recovery_attempt_count"] == attempt
        current = manager.mark_failed(task, _retryable_error(), retryable=True)

    with pytest.raises(ValueError, match="maximum recovery count"):
        manager.mark_recovery_started(
            task,
            expected_revision=current.revision,
            recovery_reason="one_attempt_too_many",
            recovery_policy="retryable_failed_checkpoint_v1",
            max_recovery_count=2,
            retry_only=True,
        )
    assert [event.revision for event in current.events] == list(
        range(len(current.events))
    )


def test_two_recovery_requests_on_one_revision_have_one_cas_winner(
    tmp_path: Path,
) -> None:
    task = _task("task-recovery-cas", "run-recovery-cas")
    first_manager = JsonlTaskManager(tmp_path / "tasks")
    failed = _retryable_state(first_manager, task)

    def attempt() -> str:
        manager = JsonlTaskManager(tmp_path / "tasks")
        try:
            manager.mark_recovery_started(
                task,
                expected_revision=failed.revision,
                recovery_reason="concurrent_retry",
                recovery_policy="retryable_failed_checkpoint_v1",
                max_recovery_count=3,
                retry_only=True,
            )
            return "won"
        except ValueError as exc:
            assert "revision conflict" in str(exc)
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: attempt(), range(2)))
    assert sorted(outcomes) == ["conflict", "won"]
    replayed = JsonlTaskManager(tmp_path / "tasks").get_task(task.task_id)
    assert sum(
        event.event_type == "task_retry_started" for event in replayed.events
    ) == 1


def test_multiprocess_recovery_cas_has_one_winner_across_ten_races(
    tmp_path: Path,
) -> None:
    context = get_context("spawn")

    for iteration in range(10):
        tasks_dir = tmp_path / f"multiprocess-cas-{iteration:02d}"
        task = _task(
            f"task-multiprocess-cas-{iteration:02d}",
            f"run-multiprocess-cas-{iteration:02d}",
        )
        manager = JsonlTaskManager(tasks_dir)
        failed = _retryable_state(manager, task)
        start_event = context.Event()
        ready_queue = context.Queue()
        outcome_queue = context.Queue()
        processes = [
            context.Process(
                target=_multiprocess_recovery_attempt,
                args=(
                    str(tasks_dir),
                    task.model_dump_json(),
                    failed.revision,
                    start_event,
                    ready_queue,
                    outcome_queue,
                ),
            )
            for _index in range(2)
        ]

        try:
            for process in processes:
                process.start()
            ready_pids = {ready_queue.get(timeout=60) for _index in range(2)}
            assert len(ready_pids) == 2
            start_event.set()
            outcomes = [outcome_queue.get(timeout=60) for _index in range(2)]
        finally:
            for process in processes:
                process.join(timeout=60)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
            ready_queue.close()
            outcome_queue.close()
            ready_queue.join_thread()
            outcome_queue.join_thread()

        assert [process.exitcode for process in processes] == [0, 0]
        assert sorted(kind for kind, _detail in outcomes) == ["conflict", "won"]

        replayed = JsonlTaskManager(tasks_dir).get_task(task.task_id)
        recovery_events = [
            event
            for event in replayed.events
            if event.event_type == "task_retry_started"
        ]
        assert len(recovery_events) == 1
        assert replayed.revision == failed.revision + 1
        assert [event.revision for event in replayed.events] == list(
            range(len(replayed.events))
        )

        raw_jsonl = manager.get_task_path(task.task_id).read_bytes()
        assert raw_jsonl.endswith(b"\n")
        raw_lines = raw_jsonl.splitlines()
        assert len(raw_lines) == len(replayed.events)
        assert all(json.loads(line) for line in raw_lines)


@pytest.mark.parametrize(
    "terminal_status",
    [
        ExecutionStatus.SUCCESS,
        ExecutionStatus.PARTIAL_SUCCESS,
        ExecutionStatus.CANCELLED,
    ],
)
def test_terminal_business_states_are_not_recoverable(
    tmp_path: Path,
    terminal_status: ExecutionStatus,
) -> None:
    task = _task(f"task-{terminal_status.value}", f"run-{terminal_status.value}")
    manager = JsonlTaskManager(tmp_path / terminal_status.value)
    manager.create_task(task)
    manager.mark_running(task)
    result = AgentResultSchema(
        result_id=f"result-{terminal_status.value}",
        task_id=task.task_id,
        run_id=task.run_id,
        agent_name="SupervisorAgent",
        agent_type="supervisor",
        status=terminal_status,
        result_type="workflow_result",
    )
    if terminal_status == ExecutionStatus.SUCCESS:
        state = manager.mark_success(task, result)
    elif terminal_status == ExecutionStatus.PARTIAL_SUCCESS:
        state = manager.mark_partial_success(task, result)
    else:
        state = manager.mark_cancelled(task, _retryable_error(), result=result)

    with pytest.raises(ValueError, match="not recoverable"):
        manager.mark_recovery_started(
            task,
            expected_revision=state.revision,
            recovery_reason="must_not_restart_business_terminal",
            recovery_policy="durable_checkpoint_recovery_v1",
            max_recovery_count=3,
        )


def test_business_failure_without_recoverable_classification_is_rejected(
    tmp_path: Path,
) -> None:
    task = _task("task-hard-gate", "run-hard-gate")
    manager = JsonlTaskManager(tmp_path / "tasks")
    manager.create_task(task)
    manager.mark_running(task)
    state = manager.mark_failed(
        task,
        ErrorSchema(
            error_code="HARD_GATE_REJECTED",
            error_type="BusinessQualityError",
            message="quality gate rejected the document",
            recoverable=False,
            retryable=False,
            failure_category=FailureCategory.BUSINESS_QUALITY,
        ),
    )
    with pytest.raises(ValueError, match="not classified as recoverable"):
        manager.mark_recovery_started(
            task,
            expected_revision=state.revision,
            recovery_reason="invalid_business_revival",
            recovery_policy="durable_checkpoint_recovery_v1",
            max_recovery_count=3,
        )


def test_recover_run_does_not_revive_failed_native_attempt_or_duplicate_paragraph(
    tmp_path: Path,
) -> None:
    crashed = {"done": False}

    def crash_after_first_commit(self, state):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("round81 injected crash after paragraph commit")
        return _ORIGINAL_PRECHECK_PARAGRAPH(self, state)

    summary, factory = _run_fake(
        tmp_path,
        run_id="round81_application_paragraph_recovery",
        patchers=(
            patch.object(
                SectionExecutionGraph,
                "_precheck_paragraph",
                crash_after_first_commit,
            ),
        ),
    )
    assert crashed["done"] is True
    assert summary["status"] in {"failed", "retryable_failed"}
    database = Path(factory.engines[0].checkpointer.path)
    before_payloads = _latest_section_payloads(database)
    namespace, before = next(
        (namespace, payload)
        for namespace, payload in before_payloads.items()
        if payload["paragraph_artifacts"] and not payload["completed"]
    )
    committed_ids = [
        item["paragraph_id"] for item in before["paragraph_artifacts"]
    ]
    before_revision = summary["task_state"]["revision"]
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        max_recovery_attempts=3,
    )

    engine = factory.engines[0]
    native_paths = [database, engine.lifecycle_service.path,
                    engine.stage_service.section_execution_coordinator.require_pre_write_services().writer_invocation.execution_store.database_path,
                    engine.stage_service.section_execution_coordinator.paragraph_runtime.manifest_ledger_path,
                    tmp_path / "tasks" / f"{summary['task_id']}_state.jsonl"]
    before_bytes = {path:path.read_bytes() for path in native_paths}
    before_task = JsonlTaskManager(tmp_path / "tasks").get_task(summary["task_id"])
    with patch.dict(os.environ, _ENV), \
            patch.object(JsonlTaskManager, "mark_recovery_started", side_effect=AssertionError("unproved recovery event")), \
            patch.object(SupervisorAgent, "recover", side_effect=AssertionError("unproved graph re-entry")), \
            pytest.raises(ValueError, match="INDETERMINATE"):
        MainlineApplicationService().recover_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            recovery_reason="injected_process_crash",
            expected_revision=before_revision,
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )

    after = _latest_section_payloads(database)[namespace]
    final_ids = [item["paragraph_id"] for item in after["paragraph_artifacts"]]
    assert {path:path.read_bytes() for path in native_paths} == before_bytes
    assert JsonlTaskManager(tmp_path / "tasks").get_task(summary["task_id"]) == before_task
    assert engine.lifecycle_service.load_run_lifecycle(summary["run_id"]).status.value in {"failed", "retryable_failed"}
    assert after["completed"] is False
    assert final_ids[: len(committed_ids)] == committed_ids
    assert len(final_ids) == len(set(final_ids))
    assert all(final_ids.count(item) == 1 for item in committed_ids)
    assert after == before


def test_application_retry_run_requires_native_proof_before_event(
    tmp_path: Path,
) -> None:
    crashed = {"done": False}

    def timeout_once(self, state):
        if not crashed["done"]:
            crashed["done"] = True
            raise TimeoutError("round81 retryable paragraph timeout")
        return _ORIGINAL_PRECHECK_PARAGRAPH(self, state)

    summary, factory = _run_fake(
        tmp_path,
        run_id="round81-application-retry-run",
        patchers=(
            patch.object(
                SectionExecutionGraph,
                "_precheck_paragraph",
                timeout_once,
            ),
        ),
    )
    assert summary["status"] == "retryable_failed"
    previous_revision = summary["task_state"]["revision"]
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        max_recovery_attempts=3,
    )
    engine = factory.engines[0]
    database = Path(engine.checkpointer.path)
    owner = engine.stage_service.section_execution_coordinator
    native_paths = [database, engine.lifecycle_service.path,
                    owner.require_pre_write_services().writer_invocation.execution_store.database_path,
                    owner.paragraph_runtime.manifest_ledger_path,
                    tmp_path / "tasks" / f"{summary['task_id']}_state.jsonl"]
    before_bytes = {path:path.read_bytes() for path in native_paths}
    before_task = JsonlTaskManager(tmp_path / "tasks").get_task(summary["task_id"])
    with patch.dict(os.environ, _ENV), \
            patch.object(JsonlTaskManager, "mark_recovery_started", side_effect=AssertionError("unproved retry event")), \
            patch.object(SupervisorAgent, "recover", side_effect=AssertionError("unproved graph re-entry")), \
            pytest.raises(ValueError, match="INDETERMINATE"):
        MainlineApplicationService().retry_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            expected_revision=previous_revision,
            recovery_reason="transient_timeout_retry",
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )

    assert {path:path.read_bytes() for path in native_paths} == before_bytes
    after_task = JsonlTaskManager(tmp_path / "tasks").get_task(summary["task_id"])
    assert after_task == before_task and after_task.revision == previous_revision
    assert engine.lifecycle_service.load_run_lifecycle(summary["run_id"]).status.value == "retryable_failed"
    assert not any(event.event_type == "task_retry_started" for event in after_task.events)


def test_human_review_checkpoint_is_routed_to_resume_not_recovery(
    tmp_path: Path,
) -> None:
    from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
    original_stage = EnterpriseDocumentWorkflowEngine._run_stage

    def pause_for_review(self, state, *, workflow, node_name, stage):
        # The recovery router consumes native interrupts. A legacy business
        # ProjectInput extra no longer guarantees a Target PAUSED checkpoint.
        if node_name == "plan_document":
            return self._human_review_interrupt(state, workflow=workflow)
        return original_stage(self, state, workflow=workflow, node_name=node_name, stage=stage)

    summary, _factory = _run_fake(tmp_path, run_id="round81-human-recovery-route",
        patchers=(patch.object(EnterpriseDocumentWorkflowEngine, "_run_stage", pause_for_review),))
    settings = replace(get_settings(), data_root=tmp_path)
    manager = JsonlTaskManager(tmp_path / "tasks")
    before = manager.get_task(summary["task_id"])
    assert before.status == ExecutionStatus.PAUSED
    with patch.dict(os.environ, _ENV), pytest.raises(
        ValueError,
        match="use resume_run",
    ):
        MainlineApplicationService().recover_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    after = manager.get_task(summary["task_id"])
    assert after.revision == before.revision
    assert after.status == ExecutionStatus.PAUSED


def test_running_invocation_recovery_actions_cover_resume_replay_retry_and_stale(
    tmp_path: Path,
) -> None:
    # Preserve the actual native publication precondition for the adapter proof.
    # This fake-only recovery fixture has explicit startup token headroom; the
    # default-capacity refusal is preserved in the first I10 shard receipt.
    default_path = Path(__file__).resolve().parents[1] / "apps/enterprise_document/config/grounded_generation_v1.yaml"
    default_bytes = default_path.read_bytes()
    policy = yaml.safe_load(default_bytes)
    assert policy["canonical_budget"]["logical_model_calls"] == 44
    assert policy["canonical_budget"]["model_tokens_total"] == 24000
    policy["canonical_budget"]["model_tokens_total"] = 64000
    profile = tmp_path / "explicit_fake_recovery_policy.json"
    profile.write_text(json.dumps(policy), encoding="utf-8")
    summary, factory = _run_fake(
        tmp_path,
        run_id="round81-running-invocation-actions",
        patchers=(patch.dict(os.environ, {"GROUNDED_GENERATION_POLICY_FILE": str(profile)}),),
    )
    assert summary["target_release_publication"] is not None
    budget = factory.engines[0].budget_runtime(summary["task_id"], summary["run_id"]).canonical_state()
    assert budget.limits.logical_model_calls == 44
    assert budget.limits.model_tokens_total == 64000
    assert budget.used.logical_model_calls == 43
    assert all(value == 0 for value in budget.active_reserved.model_dump().values())
    assert default_path.read_bytes() == default_bytes
    payloads = _latest_section_payloads(Path(factory.engines[0].checkpointer.path))
    raw = next(payload for payload in payloads.values() if payload["section_state"])
    payload = SectionExecutionGraphPayloadSchema.model_validate(raw)
    # This adapter's compatibility invocation namespace is an explicit policy
    # fixture; Target no longer emits legacy writer_agent_payloads. Its attempt
    # and snapshot still come from the real persisted Section used below.
    attempt = payload.section_state.attempt
    invocation_id = f"{payload.run_id}:{payload.section_id}:writer:a{max(1, attempt.attempt_number)}"
    common = {
        "invocation_id": invocation_id,
        "task_id": raw["task_id"],
        "run_id": raw["run_id"],
        "execution_id": f"{invocation_id}:execution",
        "attempt_id": attempt.attempt_id,
        "input_snapshot_version": attempt.input_snapshot_version,
        "idempotency_key": f"idem:{invocation_id}",
    }
    recovery = MainlineRunningInvocationRecovery()

    replay = recovery.decide(
        payload=payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **common,
            result_persisted=True,
            result_ref="artifact://writer/result",
        ),
    )
    resume = recovery.decide(
        payload=payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **common,
            underlying_queryable=True,
            underlying_status=UnderlyingInvocationStatus.RUNNING,
        ),
    )
    retry = recovery.decide(
        payload=payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **common,
            underlying_queryable=True,
            underlying_status=UnderlyingInvocationStatus.NOT_FOUND,
        ),
    )
    stale = recovery.decide(
        payload=payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **{**common, "attempt_id": f"{common['attempt_id']}:old"}
        ),
    )
    for unbound in (replay, resume, retry):
        assert unbound.action == InvocationRecoveryAction.DO_NOT_RESUME
        assert unbound.reason_code == "INDETERMINATE" and not unbound.starts_new_call
    assert stale.action == InvocationRecoveryAction.DISCARD_STALE
    assert summary["deliverable_final_result"] is not None
