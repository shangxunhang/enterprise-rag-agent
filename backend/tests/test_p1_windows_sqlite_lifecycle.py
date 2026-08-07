"""P1 deterministic stream/Supervisor/SQLite lifecycle tests."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from agent.supervisor_agent import SupervisorAgent
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from core.config import get_settings
from test_architecture_hardening import _stream_to_pause
from test_phase4_durable_resume_hitl import _run_paused
from test_phase4_section_agents_mainline import _run_fake


_ORIGINAL_PRECHECK_PARAGRAPH = SectionExecutionGraph._precheck_paragraph
_ENV = {
    "USE_REAL_RAG_TOOL": "false",
    "FAKE_RAG_SCENARIO": "default",
    "FAKE_LLM_SCENARIO": "default",
    "ENABLE_AGENT_SELF_RAG": "false",
    "ENABLE_SEMANTIC_GATE": "false",
    "SECTION_MAX_CONCURRENCY": "1",
}


def _snapshot(summary: dict) -> str:
    return summary["supervisor_result"]["human_review_request"]["metadata"][
        "input_snapshot_version"
    ]


def _common(root: Path, summary: dict, settings) -> dict:
    return {
        "project_root": Path(__file__).resolve().parents[2],
        "task_id": summary["task_id"],
        "run_id": summary["run_id"],
        "output_root": root,
        "settings": settings,
        "allow_demo_defaults": True,
        "enable_agent_self_rag": False,
    }


def test_same_process_mainline_lifecycle_leaves_sqlite_immediately_removable(
    tmp_path: Path,
) -> None:
    service = MainlineApplicationService()

    paused, _factory = _run_paused(
        tmp_path,
        run_id="sqlite-lifecycle-sync",
    )
    snapshot = _snapshot(paused)
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
    )
    with patch.dict(os.environ, _ENV):
        inspected = service.inspect_run(**_common(tmp_path, paused, settings))
        resumed = service.resume_run(
            decision="approved",
            input_snapshot_version=snapshot,
            **_common(tmp_path, paused, settings),
        )
        inspected_terminal = service.inspect_run(
            **_common(tmp_path, paused, settings)
        )
    assert inspected["interrupts"]
    assert resumed["status"] in {"success", "partial_success"}
    assert not inspected_terminal["interrupts"]

    early_events, early_settings = _stream_to_pause(
        tmp_path,
        run_id="sqlite-lifecycle-early-close",
    )
    early_summary = early_events[-1].payload["result"]
    early_snapshot = early_events[-1].payload["agent_result"][
        "human_review_request"
    ]["metadata"]["input_snapshot_version"]
    with patch.dict(os.environ, _ENV):
        stream = service.stream_resume_run(
            decision="approved",
            input_snapshot_version=early_snapshot,
            **_common(tmp_path, early_summary, early_settings),
        )
        assert next(stream).event_type == "workflow_resumed"
        stream.close()

    exception_summary, _factory2 = _run_paused(
        tmp_path,
        run_id="sqlite-lifecycle-exception",
    )
    exception_snapshot = _snapshot(exception_summary)
    exception_settings = settings

    def fail_stream(*_args, **_kwargs):
        raise RuntimeError("injected stream terminal failure")

    with patch.dict(os.environ, _ENV), patch.object(
        SupervisorAgent,
        "stream_resume",
        fail_stream,
    ), pytest.raises(RuntimeError, match="injected stream terminal failure"):
        list(
            service.stream_resume_run(
                decision="approved",
                input_snapshot_version=exception_snapshot,
                **_common(
                    tmp_path,
                    exception_summary,
                    exception_settings,
                ),
            )
        )

    crashed = {"done": False}

    def crash_once(self, state):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("sqlite lifecycle recovery crash")
        return _ORIGINAL_PRECHECK_PARAGRAPH(self, state)

    failed, _factory3 = _run_fake(
        tmp_path,
        run_id="sqlite-lifecycle-recover",
        patchers=(
            patch.object(
                SectionExecutionGraph,
                "_precheck_paragraph",
                crash_once,
            ),
        ),
    )
    recovery_settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        max_recovery_attempts=3,
    )
    with patch.dict(os.environ, _ENV):
        recovered = service.recover_run(
            recovery_reason="same_process_lifecycle_recovery",
            expected_revision=failed["task_state"]["revision"],
            **_common(tmp_path, failed, recovery_settings),
        )
    assert recovered["status"] in {"success", "partial_success"}

    checkpoint = tmp_path / "runs" / "langgraph_checkpoints.sqlite3"
    renamed = checkpoint.with_suffix(".renamed")
    checkpoint.replace(renamed)
    renamed.replace(checkpoint)
    checkpoint.unlink()
    assert not checkpoint.exists()


def test_inner_stream_close_failure_still_closes_supervisor_and_propagates() -> None:
    calls: list[str] = []

    class FailingStream:
        def close(self) -> None:
            calls.append("stream")
            raise RuntimeError("inner stream close failed")

    class ClosingSupervisor:
        def close(self) -> None:
            calls.append("supervisor")

    with pytest.raises(RuntimeError, match="inner stream close failed"):
        MainlineApplicationService._close_runtime_stream(
            FailingStream(),
            ClosingSupervisor(),
            primary_error=None,
        )
    assert calls == ["stream", "supervisor"]


def test_supervisor_close_defers_resource_until_idle_after_timeout() -> None:
    calls: list[str] = []
    deferred: list[object] = []

    class Engine:
        def cancel_active_workers(self, reason: str) -> int:
            calls.append(f"cancel:{reason}")
            return 1

        def wait_for_idle(self, timeout: float) -> bool:
            calls.append(f"wait:{timeout}")
            return False

        def defer_until_idle(self, callback) -> bool:
            calls.append("defer")
            deferred.append(callback)
            return True

    class Resource:
        def close(self) -> None:
            calls.append("resource")

    supervisor = object.__new__(SupervisorAgent)
    supervisor.workflow_engine = Engine()
    supervisor._owned_resources = (Resource(),)
    supervisor._resource_close_lock = threading.Lock()
    supervisor._resource_close_requested = False
    supervisor._resources_closed = False
    supervisor._resource_close_event = threading.Event()
    supervisor._resource_close_event.set()
    supervisor._shutdown_diagnostics = []

    with pytest.raises(TimeoutError, match="deferred until idle"):
        supervisor.close()
    assert supervisor._resources_closed is False
    assert calls == [
        "cancel:supervisor_close",
        f"wait:{SupervisorAgent.SHUTDOWN_IDLE_TIMEOUT_SECONDS}",
        "defer",
    ]

    deferred[0]()
    assert supervisor._resources_closed is True
    supervisor.close()
    assert calls[-1] == "resource"
