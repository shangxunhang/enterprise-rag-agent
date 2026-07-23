"""Contracts for publishing one aggregate model-usage artifact per run."""

from __future__ import annotations

import json
from pathlib import Path

from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


class _FixedClock:
    def now_iso(self) -> str:
        return "2026-07-23T00:00:00+00:00"


class _Supervisor:
    def __init__(self) -> None:
        self.closed = False
        self.usage = {
            "budget_semantics": "logical_model_call_v1",
            "logical_calls": 1,
            "provider_attempts": 2,
            "calls_by_model": {"m1": 1, "m2": 1},
        }

    def run(self, task) -> AgentResultSchema:
        return AgentResultSchema(
            result_id=f"result_{task.run_id}",
            task_id=task.task_id,
            run_id=task.run_id,
            agent_name="SupervisorAgent",
            agent_type="supervisor",
            status=ExecutionStatus.SUCCESS,
            result_type="workflow_result",
            result={"sub_agent_results": [], "model_usage": dict(self.usage)},
        )

    def model_usage_snapshot(self) -> dict:
        return dict(self.usage)

    def close(self) -> None:
        self.closed = True


class _SupervisorFactory:
    def __init__(self, supervisor: _Supervisor) -> None:
        self.supervisor = supervisor

    def build(self, **_):
        return self.supervisor


def test_mainline_publishes_model_usage_before_resource_close(tmp_path: Path) -> None:
    supervisor = _Supervisor()
    service = MainlineApplicationService(
        supervisor_factory=_SupervisorFactory(supervisor),
        clock=_FixedClock(),
    )

    summary = service.run(
        project_root=Path(__file__).resolve().parents[2],
        user_input="生成测试方案",
        task_id="task-usage",
        run_id="run-usage",
        output_root=tmp_path,
        settings=get_settings(),
        allow_demo_defaults=True,
    )

    usage_path = Path(summary["paths"]["model_usage"])
    payload = json.loads(usage_path.read_text(encoding="utf-8"))
    assert supervisor.closed is True
    assert payload == {
        "schema_version": "model_usage_v1",
        "task_id": "task-usage",
        "run_id": "run-usage",
        "created_at": "2026-07-23T00:00:00+00:00",
        "finalized": True,
        "usage": supervisor.usage,
    }
    assert summary["model_usage"] == supervisor.usage
    assert not list(usage_path.parent.glob(f".{usage_path.name}.*.tmp"))


def test_timed_out_worker_republishes_final_usage_after_it_becomes_idle(
    tmp_path: Path,
) -> None:
    class _DeferredSupervisor(_Supervisor):
        def __init__(self) -> None:
            super().__init__()
            self.idle_callback = None

        def defer_until_idle(self, callback) -> bool:
            self.idle_callback = callback
            return True

    supervisor = _DeferredSupervisor()
    service = MainlineApplicationService(
        supervisor_factory=_SupervisorFactory(supervisor),
        clock=_FixedClock(),
    )

    summary = service.run(
        project_root=Path(__file__).resolve().parents[2],
        user_input="生成测试方案",
        task_id="task-deferred-usage",
        run_id="run-deferred-usage",
        output_root=tmp_path,
        settings=get_settings(),
        allow_demo_defaults=True,
    )

    usage_path = Path(summary["paths"]["model_usage"])
    initial = json.loads(usage_path.read_text(encoding="utf-8"))
    assert initial["finalized"] is False
    assert supervisor.closed is True
    assert callable(supervisor.idle_callback)

    supervisor.usage["provider_attempts"] = 3
    supervisor.usage["calls_by_model"] = {"m1": 1, "m2": 2}
    supervisor.idle_callback()

    final = json.loads(usage_path.read_text(encoding="utf-8"))
    assert final["finalized"] is True
    assert final["usage"]["provider_attempts"] == 3
    assert final["usage"]["calls_by_model"] == {"m1": 1, "m2": 2}
    assert not list(usage_path.parent.glob(f".{usage_path.name}.*.tmp"))
