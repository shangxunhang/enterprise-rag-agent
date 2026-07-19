# =============================================================================
# 中文阅读说明：主链运行辅助脚本：封装运行参数、路径准备和 MainlineApplicationService 调用。
# 主要定义：resolve_project_path、_now_iso、build_project_input、build_task、build_supervisor、run_mainline。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（函数）：解析并确定 项目 路径。
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


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：now_iso, SystemClock。
    """
    return SystemClock().now_iso()


# 阅读注释（函数）：构建 项目 输入。
def build_project_input(
    task_id: str,
    user_input: str,
    raw_project_input: Optional[Dict[str, Any]] = None,
    *,
    allow_demo_defaults: bool = True,
):
    """构建 项目 输入。

    参数:
        task_id: 任务唯一标识。
        user_input: user 输入，具体约束请结合类型标注和调用方确认。
        raw_project_input: raw 项目 输入，具体约束请结合类型标注和调用方确认。
        allow_demo_defaults: allow 演示 defaults，具体约束请结合类型标注和调用方确认。

    返回:
        未显式标注；请结合调用方和实际返回语句理解。

    阅读提示:
        主要直接调用：_project_input_factory.build。
    """
    return _project_input_factory.build(
        task_id,
        user_input,
        raw_project_input,
        allow_demo_defaults=allow_demo_defaults,
    )


# 阅读注释（函数）：构建 任务。
def build_task(
    task_id: str,
    run_id: str,
    user_input: str,
    created_at: str,
    project_input: Optional[Dict[str, Any]] = None,
    *,
    allow_demo_defaults: bool = True,
) -> TaskSchema:
    """构建 任务。

    参数:
        task_id: 任务唯一标识。
        run_id: 本次运行唯一标识。
        user_input: user 输入，具体约束请结合类型标注和调用方确认。
        created_at: created at，具体约束请结合类型标注和调用方确认。
        project_input: 规范化后的项目输入。
        allow_demo_defaults: allow 演示 defaults，具体约束请结合类型标注和调用方确认。

    返回:
        TaskSchema

    阅读提示:
        主要直接调用：_task_factory.build。
    """
    return _task_factory.build(
        task_id,
        run_id,
        user_input,
        created_at,
        project_input,
        allow_demo_defaults=allow_demo_defaults,
    )


# 阅读注释（函数）：构建 supervisor。
def build_supervisor(
    workflow: WorkflowDefinitionSchema,
    runs_dir: Path,
    captures_dir: Path,
    task_manager: JsonlTaskManager,
    settings: AppSettings,
    enable_agent_self_rag: Optional[bool] = None,
) -> SupervisorAgent:
    """构建 supervisor。

    参数:
        workflow: 工作流，具体约束请结合类型标注和调用方确认。
        runs_dir: runs dir，具体约束请结合类型标注和调用方确认。
        captures_dir: captures dir，具体约束请结合类型标注和调用方确认。
        task_manager: 任务 管理器，具体约束请结合类型标注和调用方确认。
        settings: settings，具体约束请结合类型标注和调用方确认。
        enable_agent_self_rag: enable Agent Self RAG，具体约束请结合类型标注和调用方确认。

    返回:
        SupervisorAgent

    阅读提示:
        主要直接调用：RuntimeOptions.resolve, _supervisor_factory.build。
    """
    options = RuntimeOptions.resolve(
        settings,
        PROJECT_ROOT,
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


# 阅读注释（函数）：执行 主链 运行时 的主流程。
def run_mainline(
    user_input: str,
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    output_root: Optional[str | Path] = None,
    clean_existing: bool = False,
    settings: Optional[AppSettings] = None,
    enable_agent_self_rag: Optional[bool] = None,
    project_input: Optional[Dict[str, Any]] = None,
    allow_demo_defaults: bool = False,
) -> Dict[str, Any]:
    """执行 主链 运行时 的主流程。

    参数:
        user_input: user 输入，具体约束请结合类型标注和调用方确认。
        run_id: 本次运行唯一标识。
        task_id: 任务唯一标识。
        output_root: 输出 root，具体约束请结合类型标注和调用方确认。
        clean_existing: clean existing，具体约束请结合类型标注和调用方确认。
        settings: settings，具体约束请结合类型标注和调用方确认。
        enable_agent_self_rag: enable Agent Self RAG，具体约束请结合类型标注和调用方确认。
        project_input: 规范化后的项目输入。
        allow_demo_defaults: allow 演示 defaults，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：_mainline_service.run, get_settings。
    """
    return _mainline_service.run(
        project_root=PROJECT_ROOT,
        user_input=user_input,
        run_id=run_id,
        task_id=task_id,
        output_root=output_root,
        clean_existing=clean_existing,
        settings=settings or get_settings(),
        enable_agent_self_rag=enable_agent_self_rag,
        project_input=project_input,
        allow_demo_defaults=allow_demo_defaults,
    )
