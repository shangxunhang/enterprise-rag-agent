"""End-to-end mainline application service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from application.run_workspace import RunWorkspace
from application.task_factory import TaskFactory
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import AppSettings, get_settings
from core.runtime.clock import Clock, SystemClock
from task.task_manager import JsonlTaskManager


class MainlineApplicationService:
    def __init__(
        self,
        *,
        task_factory: TaskFactory | None = None,
        supervisor_factory: SupervisorFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.task_factory = task_factory or TaskFactory()
        self.supervisor_factory = supervisor_factory or SupervisorFactory()
        self.clock = clock or SystemClock()

    def _timestamp(self) -> str:
        return datetime.fromisoformat(self.clock.now_iso()).strftime("%Y%m%d_%H%M%S")

    def run(
        self,
        *,
        project_root: Path,
        user_input: str,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        output_root: Optional[str | Path] = None,
        clean_existing: bool = False,
        settings: Optional[AppSettings] = None,
        retrieval_strategy: Optional[str] = None,
        enable_agent_self_rag: Optional[bool] = None,
        project_input: Optional[Dict[str, Any]] = None,
        allow_demo_defaults: bool = False,
    ) -> Dict[str, Any]:
        settings = settings or get_settings()
        created_at = self.clock.now_iso()
        run_id = run_id or f"run_demo_{self._timestamp()}"
        input_task_id = (
            str((project_input or {}).get("task_id") or "").strip()
            if project_input is not None
            else ""
        )
        if task_id is None:
            task_id = input_task_id or f"task_{run_id}"
        elif input_task_id and task_id != input_task_id:
            raise ValueError(
                f"task_id mismatch: runtime={task_id}, ProjectInput={input_task_id}"
            )

        workspace = RunWorkspace(
            output_root=(
                Path(output_root) if output_root is not None else settings.data_root
            ),
            task_id=task_id,
            run_id=run_id,
        )
        if clean_existing:
            workspace.clean()

        task = self.task_factory.build(
            task_id=task_id,
            run_id=run_id,
            user_input=user_input,
            created_at=created_at,
            project_input=project_input,
            allow_demo_defaults=allow_demo_defaults,
        )
        task_manager = JsonlTaskManager(output_dir=workspace.tasks_dir)
        task_manager.create_task(task)
        workflow = build_scheme_generation_workflow(created_at=created_at)
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            retrieval_strategy=retrieval_strategy,
            enable_agent_self_rag=enable_agent_self_rag,
        )
        supervisor = self.supervisor_factory.build(
            workflow=workflow,
            runs_dir=workspace.runs_dir,
            captures_dir=workspace.captures_dir,
            task_manager=task_manager,
            settings=settings,
            options=options,
        )
        result = supervisor.run(task)

        scheme_draft: Dict[str, Any] = {}
        scheme_writer_output: Dict[str, Any] = {}
        for sub_result in result.result.get("sub_agent_results", []):
            if sub_result.get("agent_name") in {
                "SchemeWriterAgent",
                "FakeSchemeWriterAgent",
            }:
                payload = sub_result.get("result", {}) or {}
                scheme_draft = payload.get("scheme_draft", {})
                scheme_writer_output = payload.get("scheme_writer_output", {})
                break

        return {
            "task_id": task_id,
            "run_id": run_id,
            "status": (
                result.status.value
                if hasattr(result.status, "value")
                else str(result.status)
            ),
            "agent_name": result.agent_name,
            "result_type": result.result_type,
            "paths": workspace.paths(),
            "scheme_draft": scheme_draft,
            "scheme_writer_output": scheme_writer_output,
            "task_state": task_manager.get_task(task_id).model_dump(),
            "settings": {
                **settings.as_dict(),
                "rag_retrieval_strategy": options.retrieval_strategy,
                "enable_agent_self_rag": options.enable_agent_self_rag,
                "enable_semantic_gate": options.enable_semantic_gate,
                "semantic_gate_model_name": options.semantic_gate_model_name,
            },
            "supervisor_result": result.model_dump(mode="json"),
        }
