# =============================================================================
# 中文阅读说明：系统主应用服务：把 ProjectInput 转为 Task，创建 Supervisor 并启动端到端主链。
# 主要定义：MainlineApplicationService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（类）：封装 主链 application 服务，封装一组可复用的业务能力。
class MainlineApplicationService:
    """封装 主链 application 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：初始化 MainlineApplicationService，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        task_factory: TaskFactory | None = None,
        supervisor_factory: SupervisorFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化 MainlineApplicationService，保存运行所需的依赖、配置或状态。

        参数:
            task_factory: 任务 工厂，具体约束请结合类型标注和调用方确认。
            supervisor_factory: supervisor 工厂，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：TaskFactory, SupervisorFactory, SystemClock。
        """
        self.task_factory = task_factory or TaskFactory()
        self.supervisor_factory = supervisor_factory or SupervisorFactory()
        self.clock = clock or SystemClock()

    # 阅读注释（函数）：处理 timestamp 相关逻辑。
    def _timestamp(self) -> str:
        """处理 timestamp 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：strftime, datetime.fromisoformat, self.clock.now_iso。
        """
        return datetime.fromisoformat(self.clock.now_iso()).strftime("%Y%m%d_%H%M%S")

    # 阅读注释（函数）：执行 MainlineApplicationService 的主流程。
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
        enable_agent_self_rag: Optional[bool] = None,
        project_input: Optional[Dict[str, Any]] = None,
        allow_demo_defaults: bool = False,
    ) -> Dict[str, Any]:
        """执行 MainlineApplicationService 的主流程。

        参数:
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
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
            主要直接调用：get_settings, self.clock.now_iso, self._timestamp, strip, str, get, ValueError, RunWorkspace。
        """
        # 阶段 1：解析运行配置，并为本次端到端执行生成稳定的 run_id/task_id。
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

        # 阶段 2：创建本次运行的隔离工作区；Trace、任务状态和生成结果都写入该目录。
        workspace = RunWorkspace(
            output_root=(
                Path(output_root) if output_root is not None else settings.data_root
            ),
            task_id=task_id,
            run_id=run_id,
        )
        if clean_existing:
            workspace.clean()

        # 阶段 3：把用户输入和 ProjectInput 转换为统一 TaskSchema，后续 Agent 只依赖该协议。
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
        # 阶段 4：装配当前唯一的方案生成 Workflow；这里不是 LLM 自由规划，而是确定性工作流定义。
        workflow = build_scheme_generation_workflow(created_at=created_at)
        # 阶段 5：解析模型、RAG Profile、缓存和质量插件等运行时选项。
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=enable_agent_self_rag,
        )
        # 阶段 6：通过工厂把 Workflow、Agent、Tool、RAG、Trace 等依赖注入 Supervisor。
        supervisor = self.supervisor_factory.build(
            workflow=workflow,
            runs_dir=workspace.runs_dir,
            captures_dir=workspace.captures_dir,
            task_manager=task_manager,
            settings=settings,
            options=options,
        )
        # 阶段 7：正式进入 Agent/Workflow 主链；此调用返回整个任务的统一 AgentResult。
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
                "enable_agent_self_rag": options.enable_agent_self_rag,
                "enable_semantic_gate": options.enable_semantic_gate,
                "semantic_gate_model_name": options.semantic_gate_model_name,
            },
            "supervisor_result": result.model_dump(mode="json"),
        }
