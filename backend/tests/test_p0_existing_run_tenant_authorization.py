"""P0 existing-run authorization tests at the application boundary."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from application.existing_run_authorization import ExistingRunAuthorizationError
from application.mainline_service import MainlineApplicationService
from application.run_workspace import RunWorkspace
from application.task_factory import TaskFactory
from core.config import get_settings
from schemas.agent import AgentResultSchema
from schemas.rag import RetrievalAccessScopeSchema
from schemas.status import ExecutionStatus
from schemas.workflow_stream import WorkflowStreamEventSchema
from task.task_manager import JsonlTaskManager


def _scope(tenant_id: str) -> RetrievalAccessScopeSchema:
    return RetrievalAccessScopeSchema(
        tenant_id=tenant_id,
        authorized_kb_ids=["kb-authorized"],
    )


def _persist_task(
    root: Path,
    *,
    tenant_id: str = "tenant-a",
    current_project_id: str | None = None,
):
    task = TaskFactory().build(
        task_id="task-existing-auth",
        run_id="run-existing-auth",
        user_input="sensitive tenant-owned request",
        created_at="2026-08-02T00:00:00+00:00",
        project_input={
            "task_id": "task-existing-auth",
            "tenant_id": tenant_id,
            "current_project_id": current_project_id,
        },
        allow_demo_defaults=True,
    )
    manager = JsonlTaskManager(root / "tasks")
    manager.create_task(task)
    return task, manager


class _AuthorizedSupervisor:
    def __init__(self, task, counters: dict[str, int], *, mutation: str | None):
        self.task = task
        self.counters = counters
        self.mutation = mutation
        self.closed = False

    def inspect(self, **_kwargs) -> dict[str, Any]:
        self.counters["inspect"] += 1
        raw_task = self.task.model_dump(mode="json")
        graph_tenant = self.task.tenant_id
        if self.mutation == "checkpoint_tenant":
            raw_task["tenant_id"] = "tenant-b"
            graph_tenant = "tenant-b"
        elif self.mutation == "project_input_tenant":
            raw_task["project_input"]["tenant_id"] = "tenant-b"
        return {
            "task_id": self.task.task_id,
            "run_id": self.task.run_id,
            "graph_state": {
                "task_id": self.task.task_id,
                "run_id": self.task.run_id,
                "task_type": self.task.task_type,
                "tenant_id": graph_tenant,
                "task": raw_task,
            },
            "interrupts": [],
            "next_nodes": [],
            "checkpoint_config": {"configurable": {"thread_id": self.task.run_id}},
        }

    def set_prior_model_usage(self, _usage: dict) -> None:
        self.counters["model_usage_loaded"] += 1

    def model_usage_snapshot(self) -> dict:
        return {}

    def _result(self) -> AgentResultSchema:
        return AgentResultSchema(
            result_id="result-existing-auth",
            task_id=self.task.task_id,
            run_id=self.task.run_id,
            agent_name="SupervisorAgent",
            agent_type="supervisor",
            status=ExecutionStatus.SUCCESS,
            result_type="workflow_result",
            result={"model_usage": {}},
        )

    def resume(self, _task, **_kwargs) -> AgentResultSchema:
        self.counters["resume"] += 1
        return self._result()

    def cancel(self, _task, **_kwargs) -> AgentResultSchema:
        self.counters["cancel"] += 1
        return self._result()

    def stream_resume(self, _task, **_kwargs):
        self.counters["stream_resume"] += 1
        return iter([self._event("resume")])

    def stream_cancel(self, _task, **_kwargs):
        self.counters["stream_cancel"] += 1
        return iter([self._event("cancel")])

    def _event(self, stream_kind: str) -> WorkflowStreamEventSchema:
        return WorkflowStreamEventSchema(
            event_id=f"event-{stream_kind}",
            sequence=1,
            stream_session_id=f"session-{stream_kind}",
            stream_kind=stream_kind,
            timestamp="2026-08-02T00:00:00+00:00",
            task_id=self.task.task_id,
            run_id=self.task.run_id,
            workflow_id="scheme_generation_v2",
            workflow_version="2.0.0",
            event_type="workflow_completed",
            status=ExecutionStatus.SUCCESS,
            payload={"result": self._result().model_dump(mode="json")},
        )

    def close(self) -> None:
        self.closed = True
        self.counters["close"] += 1


class _Factory:
    def __init__(self, task, *, mutation: str | None = None):
        self.task = task
        self.mutation = mutation
        self.last_build_scope = None
        self.counters = {
            "build": 0,
            "inspect": 0,
            "model_usage_loaded": 0,
            "resume": 0,
            "cancel": 0,
            "stream_resume": 0,
            "stream_cancel": 0,
            "close": 0,
        }

    def build(self, **_kwargs):
        self.counters["build"] += 1
        self.last_build_scope = _kwargs.get("retrieval_access_scope")
        return _AuthorizedSupervisor(
            self.task,
            self.counters,
            mutation=self.mutation,
        )


def _common(root: Path, scope: RetrievalAccessScopeSchema) -> dict[str, Any]:
    return {
        "project_root": Path(__file__).resolve().parents[2],
        "task_id": "task-existing-auth",
        "run_id": "run-existing-auth",
        "output_root": root,
        "settings": replace(get_settings(), data_root=root),
        "allow_demo_defaults": False,
        "retrieval_access_scope": scope,
    }


def test_existing_run_rebuilds_current_project_document_whitelist(tmp_path: Path) -> None:
    task, _manager = _persist_task(
        tmp_path,
        tenant_id="default",
        current_project_id="gov_cloud_framework",
    )
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    scope = RetrievalAccessScopeSchema(
        tenant_id="default",
        authorized_kb_ids=[
            "historical_case_kb",
            "policy_standard_kb",
            "project_fact_kb",
            "technical_reference_kb",
            "template_style_kb",
        ],
    )

    service.inspect_run(**_common(tmp_path, scope))

    assert factory.last_build_scope is not None
    assert len(factory.last_build_scope.allowed_doc_ids) == 26
    assert "doc_5bd4faa071c0d2cb316c1add" not in factory.last_build_scope.allowed_doc_ids
    assert "doc_dc9385d9cce409724dabdd8b" in factory.last_build_scope.allowed_doc_ids


def test_same_tenant_all_existing_run_entry_points_succeed(tmp_path: Path) -> None:
    task, _manager = _persist_task(tmp_path)
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    common = _common(tmp_path, _scope("tenant-a"))

    inspection = service.inspect_run(**common)
    resumed = service.resume_run(
        decision="approved",
        input_snapshot_version="sha256:client-snapshot",
        **common,
    )
    stream_resumed = list(
        service.stream_resume_run(
            decision="approved",
            input_snapshot_version="sha256:client-snapshot",
            **common,
        )
    )
    cancelled = service.cancel_run(**common)
    stream_cancelled = list(service.stream_cancel_run(**common))

    assert inspection["task_id"] == task.task_id
    assert resumed["task_id"] == task.task_id
    assert stream_resumed[-1].task_id == task.task_id
    assert cancelled["task_id"] == task.task_id
    assert stream_cancelled[-1].task_id == task.task_id
    assert factory.counters == {
        "build": 5,
        "inspect": 5,
        "model_usage_loaded": 5,
        "resume": 1,
        "cancel": 1,
        "stream_resume": 1,
        "stream_cancel": 1,
        "close": 5,
    }


@pytest.mark.parametrize(
    ("entrypoint", "kwargs"),
    [
        ("inspect_run", {}),
        (
            "resume_run",
            {"decision": "approved", "input_snapshot_version": "sha256:x"},
        ),
        (
            "stream_resume_run",
            {"decision": "approved", "input_snapshot_version": "sha256:x"},
        ),
        ("cancel_run", {}),
        ("stream_cancel_run", {}),
        ("recover_run", {}),
        ("retry_run", {}),
    ],
)
def test_cross_tenant_is_rejected_before_runtime_or_sensitive_usage_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    kwargs: dict[str, Any],
) -> None:
    task, manager = _persist_task(tmp_path)
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    task_path = manager.get_task_path(task.task_id)
    before_events = task_path.read_bytes()
    task_reads = {"load": 0, "events": 0}
    original_load = JsonlTaskManager._load_existing_task
    original_read_events = JsonlTaskManager._read_task_events

    def spy_load(self, requested_task_id):
        task_reads["load"] += 1
        return original_load(self, requested_task_id)

    def spy_read_events(self, path):
        task_reads["events"] += 1
        return original_read_events(self, path)

    def forbidden_model_usage_read(_self):
        raise AssertionError("model usage was read before tenant authorization")

    monkeypatch.setattr(JsonlTaskManager, "_load_existing_task", spy_load)
    monkeypatch.setattr(JsonlTaskManager, "_read_task_events", spy_read_events)
    monkeypatch.setattr(RunWorkspace, "read_model_usage", forbidden_model_usage_read)
    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        operation = getattr(service, entrypoint)(
            **_common(tmp_path, _scope("tenant-b")),
            **kwargs,
        )
        list(operation) if entrypoint.startswith("stream_") else operation

    assert factory.counters["build"] == 0
    assert factory.counters["inspect"] == 0
    assert task_reads == {"load": 0, "events": 0}
    assert task_path.read_bytes() == before_events


def test_identity_projection_contains_only_immutable_authorization_fields(
    tmp_path: Path,
) -> None:
    task, manager = _persist_task(tmp_path)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    payload = json.loads(identity_path.read_text(encoding="utf-8"))

    assert set(payload) == {
        "schema_version",
        "task_id",
        "run_id",
        "task_type",
        "tenant_id",
        "canonical_task_request_sha256",
    }
    assert not {
        "user_input",
        "project_input",
        "project_name",
        "metadata",
        "error",
        "error_message",
        "result",
        "artifact",
        "checkpoint",
        "status",
        "revision",
    } & set(payload)


@pytest.mark.parametrize("damage", ["missing", "invalid_json"])
def test_missing_or_damaged_identity_denies_without_reading_task_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    task, manager = _persist_task(tmp_path)
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    task_path = manager.get_task_path(task.task_id)
    before_events = task_path.read_bytes()
    if damage == "missing":
        identity_path.unlink()
    else:
        identity_path.write_text("{not-json", encoding="utf-8")

    task_reads = {"load": 0, "events": 0}

    def forbidden_load(*_args, **_kwargs):
        task_reads["load"] += 1
        raise AssertionError("full Task JSONL was read before identity authorization")

    def forbidden_read_events(*_args, **_kwargs):
        task_reads["events"] += 1
        raise AssertionError("Task events were parsed before identity authorization")

    def forbidden_model_usage_read(_self):
        raise AssertionError("model usage was read before identity authorization")

    monkeypatch.setattr(JsonlTaskManager, "_load_existing_task", forbidden_load)
    monkeypatch.setattr(JsonlTaskManager, "_read_task_events", forbidden_read_events)
    monkeypatch.setattr(RunWorkspace, "read_model_usage", forbidden_model_usage_read)

    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        service.inspect_run(**_common(tmp_path, _scope("tenant-a")))

    assert task_reads == {"load": 0, "events": 0}
    assert factory.counters["build"] == 0
    assert factory.counters["inspect"] == 0
    assert task_path.read_bytes() == before_events


@pytest.mark.parametrize("field", ["tenant_id", "canonical_task_request_sha256"])
def test_tampered_identity_is_rejected_before_supervisor_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    task, manager = _persist_task(tmp_path)
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    payload = json.loads(identity_path.read_text(encoding="utf-8"))
    payload[field] = "tenant-b" if field == "tenant_id" else "0" * 64
    identity_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before_events = manager.get_task_path(task.task_id).read_bytes()

    def forbidden_model_usage_read(_self):
        raise AssertionError("model usage was read after identity tampering")

    monkeypatch.setattr(RunWorkspace, "read_model_usage", forbidden_model_usage_read)
    requested_tenant = "tenant-b" if field == "tenant_id" else "tenant-a"
    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        service.inspect_run(**_common(tmp_path, _scope(requested_tenant)))

    assert factory.counters["build"] == 0
    assert factory.counters["inspect"] == 0
    assert manager.get_task_path(task.task_id).read_bytes() == before_events


def test_task_jsonl_identity_mismatch_is_rejected_before_supervisor_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, manager = _persist_task(tmp_path)
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    task_path = manager.get_task_path(task.task_id)
    event = json.loads(task_path.read_text(encoding="utf-8"))
    event["snapshot"]["tenant_id"] = "tenant-b"
    task_path.write_text(
        json.dumps(event, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tampered_events = task_path.read_bytes()

    def forbidden_model_usage_read(_self):
        raise AssertionError("model usage was read after Task identity mismatch")

    monkeypatch.setattr(RunWorkspace, "read_model_usage", forbidden_model_usage_read)
    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        service.inspect_run(**_common(tmp_path, _scope("tenant-a")))

    assert factory.counters["build"] == 0
    assert factory.counters["inspect"] == 0
    assert task_path.read_bytes() == tampered_events


def test_duplicate_create_task_requires_exact_immutable_identity(
    tmp_path: Path,
) -> None:
    task, manager = _persist_task(tmp_path)
    identity_path = manager.get_authorization_identity_path(task.task_id)
    task_path = manager.get_task_path(task.task_id)
    identity_bytes = identity_path.read_bytes()
    event_bytes = task_path.read_bytes()

    repeated = manager.create_task(task.model_copy(deep=True))
    assert repeated.task_id == task.task_id
    assert identity_path.read_bytes() == identity_bytes
    assert task_path.read_bytes() == event_bytes

    conflicting = TaskFactory().build(
        task_id=task.task_id,
        run_id="run-conflicting-identity",
        user_input="different immutable request",
        created_at="2026-08-02T00:00:00+00:00",
        project_input={
            "task_id": task.task_id,
            "tenant_id": "tenant-b",
        },
        allow_demo_defaults=True,
    )
    with pytest.raises(ValueError, match="immutable request mismatch"):
        manager.create_task(conflicting)

    assert identity_path.read_bytes() == identity_bytes
    assert task_path.read_bytes() == event_bytes


@pytest.mark.parametrize("mutation", ["checkpoint_tenant", "project_input_tenant"])
def test_checkpoint_and_project_input_tenant_mismatch_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    task, manager = _persist_task(tmp_path)
    factory = _Factory(task, mutation=mutation)
    service = MainlineApplicationService(supervisor_factory=factory)
    before_events = manager.get_task_path(task.task_id).read_bytes()

    def forbidden_model_usage_read(_self):
        raise AssertionError("model usage was read before checkpoint authorization")

    monkeypatch.setattr(RunWorkspace, "read_model_usage", forbidden_model_usage_read)
    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        service.inspect_run(**_common(tmp_path, _scope("tenant-a")))

    assert factory.counters["inspect"] == 1
    assert factory.counters["model_usage_loaded"] == 0
    assert factory.counters["close"] == 1
    assert manager.get_task_path(task.task_id).read_bytes() == before_events


def test_production_requires_typed_authenticated_scope(tmp_path: Path) -> None:
    task, _manager = _persist_task(tmp_path)
    service = MainlineApplicationService(supervisor_factory=_Factory(task))
    common = _common(tmp_path, _scope("tenant-a"))

    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        service.inspect_run(**{**common, "retrieval_access_scope": None})
    with pytest.raises(TypeError, match="trusted RetrievalAccessScopeSchema"):
        service.inspect_run(
            **{
                **common,
                "retrieval_access_scope": {
                    "tenant_id": "tenant-a",
                    "authorized_kb_ids": ["kb-authorized"],
                },
            }
        )


def test_demo_bypass_cannot_read_non_demo_tenant(tmp_path: Path) -> None:
    task, _manager = _persist_task(tmp_path, tenant_id="tenant-private")
    factory = _Factory(task)
    service = MainlineApplicationService(supervisor_factory=factory)
    common = _common(tmp_path, _scope("tenant-private"))

    with pytest.raises(ExistingRunAuthorizationError, match="not authorized"):
        service.inspect_run(
            **{
                **common,
                "allow_demo_defaults": True,
                "retrieval_access_scope": None,
            }
        )
    assert factory.counters["build"] == 0
