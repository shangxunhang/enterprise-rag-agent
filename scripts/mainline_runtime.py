"""Compatibility facade for the modular mainline runtime.

The actual application logic lives under ``backend/application`` and
``backend/bootstrap``.  CLI adapters keep importing this module so external
entry points remain stable during the refactor.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from agent.supervisor_agent import SupervisorAgent
from application.mainline_service import MainlineApplicationService
from application.project_input_factory import ProjectInputFactory
from application.task_factory import TaskFactory
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import AppSettings, get_settings
from core.runtime.clock import SystemClock
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager

MAINLINE_RUNTIME_VERSION = "stage1-mainline-stability-v7.9.0-20260718"


def resolve_project_path(
    path: str | Path,
    *,
    project_root: str | Path = PROJECT_ROOT,
) -> Path:
    """Resolve CLI/config paths against the project root, never process cwd."""

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (Path(project_root).expanduser().resolve() / candidate).resolve()

_project_input_factory = ProjectInputFactory()
_task_factory = TaskFactory(_project_input_factory)
_supervisor_factory = SupervisorFactory()
_mainline_service = MainlineApplicationService(
    task_factory=_task_factory,
    supervisor_factory=_supervisor_factory,
)


def _now_iso() -> str:
    return SystemClock().now_iso()


def build_project_input(
    task_id: str,
    user_input: str,
    raw_project_input: Optional[Dict[str, Any]] = None,
    *,
    allow_demo_defaults: bool = True,
):
    return _project_input_factory.build(
        task_id,
        user_input,
        raw_project_input,
        allow_demo_defaults=allow_demo_defaults,
    )


def build_task(
    task_id: str,
    run_id: str,
    user_input: str,
    created_at: str,
    project_input: Optional[Dict[str, Any]] = None,
    *,
    allow_demo_defaults: bool = True,
) -> TaskSchema:
    return _task_factory.build(
        task_id,
        run_id,
        user_input,
        created_at,
        project_input,
        allow_demo_defaults=allow_demo_defaults,
    )


def build_supervisor(
    workflow: WorkflowDefinitionSchema,
    runs_dir: Path,
    captures_dir: Path,
    task_manager: JsonlTaskManager,
    settings: AppSettings,
    retrieval_strategy: Optional[str] = None,
    enable_agent_self_rag: Optional[bool] = None,
) -> SupervisorAgent:
    options = RuntimeOptions.resolve(
        settings,
        PROJECT_ROOT,
        retrieval_strategy=retrieval_strategy,
        enable_agent_self_rag=enable_agent_self_rag,
    )
    return _supervisor_factory.build(
        workflow=workflow,
        runs_dir=runs_dir,
        captures_dir=captures_dir,
        task_manager=task_manager,
        settings=settings,
        options=options,
    )


def run_mainline(
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
    return _mainline_service.run(
        project_root=PROJECT_ROOT,
        user_input=user_input,
        run_id=run_id,
        task_id=task_id,
        output_root=output_root,
        clean_existing=clean_existing,
        settings=settings or get_settings(),
        retrieval_strategy=retrieval_strategy,
        enable_agent_self_rag=enable_agent_self_rag,
        project_input=project_input,
        allow_demo_defaults=allow_demo_defaults,
    )
