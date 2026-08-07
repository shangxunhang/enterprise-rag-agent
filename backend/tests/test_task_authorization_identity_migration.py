"""Privileged offline Task authorization identity migration tests."""

from __future__ import annotations

import json
from multiprocessing import get_context
import os
from pathlib import Path

import pytest

from application.existing_run_authorization import ExistingRunAuthorizationError
from application.mainline_service import MainlineApplicationService
from application.task_factory import TaskFactory
from schemas.task_state import TaskAuthorizationIdentitySchema
from scripts.migrate_task_authorization_identities import (
    main as migration_main,
    migrate_task_authorization_identities,
)
from task.task_manager import (
    JsonlTaskManager,
    TaskAuthorizationIdentityMigrationStatus,
)
from test_p0_existing_run_tenant_authorization import (
    _Factory,
    _common,
    _persist_task,
    _scope,
)


_IDENTITY_FIELDS = {
    "schema_version",
    "task_id",
    "run_id",
    "task_type",
    "tenant_id",
    "canonical_task_request_sha256",
}


def _legacy_task(
    root: Path,
    *,
    task_id: str,
    with_running_event: bool = False,
):
    task = TaskFactory().build(
        task_id=task_id,
        run_id=f"run-{task_id}",
        user_input="legacy sensitive task body",
        created_at="2026-08-02T00:00:00+00:00",
        project_input={"task_id": task_id, "tenant_id": "tenant-a"},
        allow_demo_defaults=True,
    )
    manager = JsonlTaskManager(root)
    manager.create_task(task)
    if with_running_event:
        manager.mark_running(task)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    identity_path.unlink()
    return task, manager


def _migration_process(
    tasks_dir: str,
    task_id: str,
    start_event,
    ready_queue,
    outcome_queue,
) -> None:
    manager = JsonlTaskManager(tasks_dir)
    ready_queue.put(os.getpid())
    if not start_event.wait(timeout=30):
        outcome_queue.put("failed")
        return
    result = manager.migrate_legacy_authorization_identity(task_id, apply=True)
    outcome_queue.put(result.status.value)


def test_legacy_task_requires_explicit_migration_before_existing_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, manager = _persist_task(tmp_path)
    task_path = manager.get_task_path(task.task_id)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    identity_path.unlink()
    original_task_bytes = task_path.read_bytes()
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)

    def forbidden_load(*_args, **_kwargs):
        raise AssertionError("online authorization read the full Task state")

    def forbidden_events(*_args, **_kwargs):
        raise AssertionError("online authorization read Task events")

    with monkeypatch.context() as patcher:
        patcher.setattr(JsonlTaskManager, "_load_existing_task", forbidden_load)
        patcher.setattr(JsonlTaskManager, "_read_task_events", forbidden_events)
        with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
            service.inspect_run(**_common(tmp_path, _scope("tenant-a")))

    assert not identity_path.exists()
    assert task_path.read_bytes() == original_task_bytes
    assert factory.counters["build"] == 0

    dry_run = migrate_task_authorization_identities(
        manager.output_dir,
        task_ids=[task.task_id],
    )
    assert dry_run["mode"] == "dry-run"
    assert dry_run["scanned"] == 1
    assert dry_run["would_create"] == 1
    assert not identity_path.exists()
    assert task_path.read_bytes() == original_task_bytes

    applied = manager.migrate_legacy_authorization_identity(
        task.task_id,
        apply=True,
    )
    assert applied.status == TaskAuthorizationIdentityMigrationStatus.CREATED
    identity_payload = json.loads(identity_path.read_text(encoding="utf-8"))
    assert set(identity_payload) == _IDENTITY_FIELDS
    assert task_path.read_bytes() == original_task_bytes

    inspection = service.inspect_run(**_common(tmp_path, _scope("tenant-a")))
    assert inspection["task_id"] == task.task_id
    assert factory.counters["build"] == 1

    reads = {"load": 0, "events": 0}
    original_load = JsonlTaskManager._load_existing_task
    original_read = JsonlTaskManager._read_task_events

    def spy_load(self, requested_task_id, *args, **kwargs):
        reads["load"] += 1
        return original_load(self, requested_task_id, *args, **kwargs)

    def spy_read(self, path, *args, **kwargs):
        reads["events"] += 1
        return original_read(self, path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(JsonlTaskManager, "_load_existing_task", spy_load)
        patcher.setattr(JsonlTaskManager, "_read_task_events", spy_read)
        with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
            service.inspect_run(**_common(tmp_path, _scope("tenant-b")))

    assert reads == {"load": 0, "events": 0}
    assert factory.counters["build"] == 1
    assert task_path.read_bytes() == original_task_bytes


def test_migration_is_idempotent_without_rewriting_identity_or_task_log(
    tmp_path: Path,
) -> None:
    task, manager = _legacy_task(
        tmp_path / "tasks",
        task_id="task-legacy-idempotent",
    )
    task_path = manager.get_task_path(task.task_id)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    task_bytes = task_path.read_bytes()

    first = manager.migrate_legacy_authorization_identity(task.task_id, apply=True)
    assert first.status == TaskAuthorizationIdentityMigrationStatus.CREATED
    identity_bytes = identity_path.read_bytes()

    second = manager.migrate_legacy_authorization_identity(task.task_id, apply=True)
    assert second.status == (
        TaskAuthorizationIdentityMigrationStatus.ALREADY_CURRENT
    )
    assert identity_path.read_bytes() == identity_bytes
    assert task_path.read_bytes() == task_bytes


def test_conflicting_identity_is_not_overwritten_and_cli_exits_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task, manager = _legacy_task(
        tmp_path / "tasks",
        task_id="task-legacy-conflict",
    )
    task_path = manager.get_task_path(task.task_id)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    conflicting = TaskAuthorizationIdentitySchema(
        task_id=task.task_id,
        run_id=task.run_id,
        task_type=task.task_type,
        tenant_id="tenant-other",
        canonical_task_request_sha256="f" * 64,
    )
    identity_path.write_text(
        conflicting.model_dump_json() + "\n",
        encoding="utf-8",
    )
    identity_bytes = identity_path.read_bytes()
    task_bytes = task_path.read_bytes()

    result = manager.migrate_legacy_authorization_identity(task.task_id, apply=True)
    assert result.status == TaskAuthorizationIdentityMigrationStatus.CONFLICT
    assert identity_path.read_bytes() == identity_bytes
    assert task_path.read_bytes() == task_bytes

    exit_code = migration_main(
        [
            "--tasks-dir",
            str(manager.output_dir),
            "--task-id",
            task.task_id,
            "--apply",
        ]
    )
    capsys.readouterr()
    assert exit_code == 1
    assert identity_path.read_bytes() == identity_bytes
    assert task_path.read_bytes() == task_bytes


def _damage_task_log(task_path: Path, mutation: str) -> bytes:
    lines = [
        json.loads(line)
        for line in task_path.read_text(encoding="utf-8").splitlines()
    ]
    if mutation == "invalid_json":
        damaged = b"{not valid json}\n"
    elif mutation == "revision_gap":
        lines[1]["revision"] = 3
        lines[1]["snapshot"]["revision"] = 3
        damaged = _serialize_lines(lines)
    elif mutation == "task_id_mismatch":
        lines[1]["task_id"] = "task-other"
        lines[1]["snapshot"]["task_id"] = "task-other"
        damaged = _serialize_lines(lines)
    elif mutation == "run_id_mismatch":
        lines[1]["run_id"] = "run-other"
        lines[1]["snapshot"]["run_id"] = "run-other"
        damaged = _serialize_lines(lines)
    elif mutation == "task_type_mismatch":
        lines[1]["task_type"] = "other_task_type"
        lines[1]["snapshot"]["task_type"] = "other_task_type"
        damaged = _serialize_lines(lines)
    elif mutation == "tenant_drift":
        lines[1]["snapshot"]["tenant_id"] = "tenant-other"
        damaged = _serialize_lines(lines)
    elif mutation == "hash_drift":
        lines[1]["snapshot"]["canonical_task_request_sha256"] = "e" * 64
        damaged = _serialize_lines(lines)
    elif mutation == "hash_missing":
        for line in lines:
            line["snapshot"]["canonical_task_request_sha256"] = None
        damaged = _serialize_lines(lines)
    elif mutation == "torn_record":
        damaged = task_path.read_bytes() + b'{"incomplete":'
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    task_path.write_bytes(damaged)
    return damaged


def _serialize_lines(lines: list[dict]) -> bytes:
    return (
        "".join(
            json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
            for line in lines
        )
    ).encode("utf-8")


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_json",
        "revision_gap",
        "task_id_mismatch",
        "run_id_mismatch",
        "task_type_mismatch",
        "tenant_drift",
        "hash_drift",
        "hash_missing",
        "torn_record",
    ],
)
def test_invalid_history_is_rejected_without_modification_or_identity(
    tmp_path: Path,
    mutation: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task, manager = _legacy_task(
        tmp_path / mutation,
        task_id=f"task-legacy-{mutation}",
        with_running_event=True,
    )
    task_path = manager.get_task_path(task.task_id)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    damaged_bytes = _damage_task_log(task_path, mutation)

    result = manager.migrate_legacy_authorization_identity(task.task_id, apply=True)

    assert result.status == (
        TaskAuthorizationIdentityMigrationStatus.INVALID_TASK_LOG
    )
    assert not identity_path.exists()
    assert task_path.read_bytes() == damaged_bytes
    if mutation == "invalid_json":
        assert migration_main(
            [
                "--tasks-dir",
                str(manager.output_dir),
                "--task-id",
                task.task_id,
                "--apply",
            ]
        ) == 1
        capsys.readouterr()
        assert task_path.read_bytes() == damaged_bytes


def test_missing_task_log_is_reported_without_identity(tmp_path: Path) -> None:
    manager = JsonlTaskManager(tmp_path / "tasks")
    result = manager.migrate_legacy_authorization_identity(
        "task-does-not-exist",
        apply=True,
    )
    assert result.status == (
        TaskAuthorizationIdentityMigrationStatus.MISSING_TASK_LOG
    )
    assert not manager.get_authorization_identity_path(
        "task-does-not-exist"
    ).exists()


def test_two_processes_migrate_one_legacy_task_idempotently(
    tmp_path: Path,
) -> None:
    task, manager = _legacy_task(
        tmp_path / "tasks",
        task_id="task-legacy-multiprocess",
        with_running_event=True,
    )
    task_path = manager.get_task_path(task.task_id)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    task_bytes = task_path.read_bytes()
    context = get_context("spawn")
    start_event = context.Event()
    ready_queue = context.Queue()
    outcome_queue = context.Queue()
    processes = [
        context.Process(
            target=_migration_process,
            args=(
                str(manager.output_dir),
                task.task_id,
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
    assert sorted(outcomes) == ["already_current", "created"]
    identity = TaskAuthorizationIdentitySchema.model_validate_json(
        identity_path.read_text(encoding="utf-8"),
        strict=True,
    )
    assert identity.task_id == task.task_id
    assert task_path.read_bytes() == task_bytes
    assert JsonlTaskManager(manager.output_dir).get_task(task.task_id).task_id == (
        task.task_id
    )
    assert not list(
        manager.output_dir.glob(f".{identity_path.name}.*.tmp")
    )
