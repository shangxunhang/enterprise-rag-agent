"""端到端主链的应用层入口。

本服务负责可信入口校验、运行目录隔离、Task 构造、依赖装配、Supervisor 启动以及结果
文件汇总。业务 Agent 不应自行处理这些进程级职责。阅读 ``run`` 时按已有“阶段”注释
向下跟踪，可看到 ProjectInput 如何最终变成可交付结果与审计产物。
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from application.run_workspace import RunWorkspace
from application.task_factory import TaskFactory
from apps.enterprise_document.schemas.project_input_schema import (
    ProjectInputDemoSeedV1Schema,
    ProjectInputSchema,
)
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import AppSettings, get_settings
from core.runtime.clock import Clock, SystemClock
from schemas.rag import RetrievalAccessScopeSchema
from schemas.agent import AgentResultSchema
from schemas.task import TaskSchema
from schemas.workflow_stream import WorkflowStreamEventSchema
from task.task_manager import JsonlTaskManager


logger = logging.getLogger(__name__)


class MainlineApplicationService:
    """协调一次完整主链运行，但不承载章节生成或检索等领域逻辑。"""
    def __init__(
        self,
        *,
        task_factory: TaskFactory | None = None,
        supervisor_factory: SupervisorFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        """注入任务映射、Supervisor 装配和时间来源，便于测试替换外部依赖。"""
        self.task_factory = task_factory or TaskFactory()
        self.supervisor_factory = supervisor_factory or SupervisorFactory()
        self.clock = clock or SystemClock()

    def _timestamp(self) -> str:
        """把统一时钟输出转换为仅用于默认运行标识的紧凑时间戳。"""
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
        enable_agent_self_rag: Optional[bool] = None,
        project_input: Optional[Dict[str, Any]] = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """执行一次隔离、可恢复且可审计的端到端主链。

        入口先以严格 Pydantic 模式校验 ProjectInput；只有 ``allow_demo_defaults`` 明确为真
        时才接受 demo seed。认证边界提供的 ``retrieval_access_scope`` 必须是可信 Schema
        实例，并与 ProjectInput 租户一致，防止业务输入扩大检索权限。

        随后创建运行工作区和 Task 状态，装配确定性 Workflow 与 Supervisor，执行或恢复
        工作流，并汇总 Trace、任务快照、草稿、拒绝产物和最终可交付物路径。返回值是运行
        摘要，不是领域 Agent 的原始内部状态。
        """
        # 阶段 1：解析运行配置，并为本次端到端执行生成稳定的 run_id/task_id。
        settings = settings or get_settings()
        if (
            retrieval_access_scope is not None
            and not isinstance(
                retrieval_access_scope,
                RetrievalAccessScopeSchema,
            )
        ):
            raise TypeError(
                "retrieval_access_scope must be a trusted "
                "RetrievalAccessScopeSchema instance"
            )
        created_at = self.clock.now_iso()
        run_id = run_id or f"run_demo_{self._timestamp()}"
        input_task_id = ""
        if project_input is not None:
            ingress_schema = (
                ProjectInputDemoSeedV1Schema
                if allow_demo_defaults
                else ProjectInputSchema
            )
            validated_ingress = ingress_schema.model_validate(
                project_input,
                strict=True,
            )
            #校验租户
            input_task_id = str(validated_ingress.task_id or "").strip()
            if (
                retrieval_access_scope is not None
                and validated_ingress.tenant_id
                != retrieval_access_scope.tenant_id
            ):
                raise ValueError(
                    "ProjectInput tenant_id conflicts with authenticated "
                    "retrieval tenant"
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
        if (
            options.use_real_rag
            and not allow_demo_defaults
            and retrieval_access_scope is None
        ):
            raise ValueError(
                "trusted retrieval_access_scope is required for production "
                "secure retrieval"
            )
        # 阶段 6：通过工厂把 Workflow、Agent、Tool、RAG、Trace 等依赖注入 Supervisor。
        supervisor = self.supervisor_factory.build(
            workflow=workflow,
            runs_dir=workspace.runs_dir,
            captures_dir=workspace.captures_dir,
            task_manager=task_manager,
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        # 阶段 7：正式进入 Agent/Workflow 主链；无论成功、失败还是异常，都统一释放本次运行持有的资源。
        try:
            result = supervisor.run(task)
            usage_snapshot = getattr(supervisor, "model_usage_snapshot", None)
            model_usage = (
                dict(usage_snapshot())
                if callable(usage_snapshot)
                else dict(result.result.get("model_usage") or {})
            )

            def persist_model_usage(*, finalized: bool) -> None:
                current_usage = (
                    dict(usage_snapshot())
                    if callable(usage_snapshot)
                    else dict(model_usage)
                )
                workspace.write_model_usage(
                    {
                        "schema_version": "model_usage_v1",
                        "task_id": task_id,
                        "run_id": run_id,
                        "created_at": self.clock.now_iso(),
                        "finalized": finalized,
                        "usage": current_usage,
                    }
                )

            defer_until_idle = getattr(supervisor, "defer_until_idle", None)
            if callable(defer_until_idle):
                # Preserve a point-in-time snapshot immediately, then replace
                # it after a timed-out native/CUDA worker physically exits.
                persist_model_usage(finalized=False)
                defer_until_idle(lambda: persist_model_usage(finalized=True))
            else:
                persist_model_usage(finalized=True)
        finally:
            supervisor.close()

        generated_draft: Dict[str, Any] = {}
        rejected_artifact: Dict[str, Any] | None = None
        deliverable_final_result: Dict[str, Any] | None = None
        scheme_writer_output: Dict[str, Any] = {}
        for sub_result in result.result.get("sub_agent_results", []):
            if sub_result.get("agent_name") in {
                "SchemeWriterAgent",
                "FakeSchemeWriterAgent",
            }:
                payload = sub_result.get("result", {}) or {}
                generated_draft = payload.get("generated_draft", {})
                rejected_artifact = payload.get("rejected_artifact")
                deliverable_final_result = payload.get("deliverable_final_result")
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
            "generated_draft": generated_draft,
            "rejected_artifact": rejected_artifact,
            "deliverable_final_result": deliverable_final_result,
            "scheme_writer_output": scheme_writer_output,
            "model_usage": model_usage,
            "task_state": task_manager.get_task(task_id).model_dump(),
            "settings": {
                **settings.as_dict(),
                "enable_agent_self_rag": options.enable_agent_self_rag,
                "enable_semantic_gate": options.enable_semantic_gate,
                "semantic_gate_model_name": options.semantic_gate_model_name,
            },
            "supervisor_result": result.model_dump(mode="json"),
        }

    @staticmethod
    def _close_runtime_stream(
        stream: Any,
        supervisor: Any,
        *,
        primary_error: BaseException | None,
    ) -> None:
        """Close the inner generator before its checkpoint-owning runtime."""

        close_error: Exception | None = None
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                close_error = exc
                logger.warning(
                    "runtime stream close failed before supervisor shutdown",
                    exc_info=True,
                )
        supervisor.close()
        if close_error is not None and primary_error is None:
            raise close_error

    def stream_run(
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
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
        runnable_config: Dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream a new run while retaining the same task and security setup."""

        settings = settings or get_settings()
        if (
            retrieval_access_scope is not None
            and not isinstance(
                retrieval_access_scope,
                RetrievalAccessScopeSchema,
            )
        ):
            raise TypeError(
                "retrieval_access_scope must be a trusted "
                "RetrievalAccessScopeSchema instance"
            )
        created_at = self.clock.now_iso()
        run_id = run_id or f"run_demo_{self._timestamp()}"
        input_task_id = ""
        if project_input is not None:
            ingress_schema = (
                ProjectInputDemoSeedV1Schema
                if allow_demo_defaults
                else ProjectInputSchema
            )
            validated_ingress = ingress_schema.model_validate(
                project_input,
                strict=True,
            )
            input_task_id = str(validated_ingress.task_id or "").strip()
            if (
                retrieval_access_scope is not None
                and validated_ingress.tenant_id
                != retrieval_access_scope.tenant_id
            ):
                raise ValueError(
                    "ProjectInput tenant_id conflicts with authenticated "
                    "retrieval tenant"
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
            enable_agent_self_rag=enable_agent_self_rag,
        )
        if (
            options.use_real_rag
            and not allow_demo_defaults
            and retrieval_access_scope is None
        ):
            raise ValueError(
                "trusted retrieval_access_scope is required for production "
                "secure retrieval"
            )
        supervisor = self.supervisor_factory.build(
            workflow=workflow,
            runs_dir=workspace.runs_dir,
            captures_dir=workspace.captures_dir,
            task_manager=task_manager,
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        stream = supervisor.stream(
            task,
            runnable_config=runnable_config,
        )
        primary_error: BaseException | None = None
        try:
            for stream_event in stream:
                raw_result = stream_event.payload.get("result")
                if raw_result is None:
                    yield stream_event
                    continue
                result = AgentResultSchema.model_validate(raw_result)
                model_usage = supervisor.model_usage_snapshot()
                workspace.write_model_usage(
                    {
                        "schema_version": "model_usage_v1",
                        "task_id": task_id,
                        "run_id": run_id,
                        "created_at": self.clock.now_iso(),
                        "finalized": True,
                        "usage": model_usage,
                    }
                )
                summary = self._continuation_summary(
                    workspace=workspace,
                    task_manager=task_manager,
                    result=result,
                )
                summary["settings"] = {
                    **settings.as_dict(),
                    "enable_agent_self_rag": options.enable_agent_self_rag,
                    "enable_semantic_gate": options.enable_semantic_gate,
                    "semantic_gate_model_name": options.semantic_gate_model_name,
                }
                yield stream_event.model_copy(
                    update={
                        "payload": {
                            **stream_event.payload,
                            "agent_result": raw_result,
                            "result": summary,
                        }
                    }
                )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_runtime_stream(
                stream,
                supervisor,
                primary_error=primary_error,
            )

    def _build_existing_runtime(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        output_root: str | Path | None,
        settings: AppSettings | None,
        enable_agent_self_rag: bool | None,
        allow_demo_defaults: bool,
        retrieval_access_scope: RetrievalAccessScopeSchema | None,
    ):
        """Reassemble services for an existing run without creating a task."""

        settings = settings or get_settings()
        if (
            retrieval_access_scope is not None
            and not isinstance(
                retrieval_access_scope,
                RetrievalAccessScopeSchema,
            )
        ):
            raise TypeError(
                "retrieval_access_scope must be a trusted "
                "RetrievalAccessScopeSchema instance"
            )
        workspace = RunWorkspace(
            output_root=(
                Path(output_root) if output_root is not None else settings.data_root
            ),
            task_id=task_id,
            run_id=run_id,
        )
        task_manager = JsonlTaskManager(output_dir=workspace.tasks_dir)
        task_state = task_manager.get_task(task_id)
        if task_state.run_id != run_id:
            raise ValueError(
                f"run_id mismatch: task={task_state.run_id}, requested={run_id}"
            )
        workflow = build_scheme_generation_workflow(
            created_at=task_state.created_at
        )
        if task_state.task_type != workflow.task_type:
            raise ValueError(
                "persisted task type is not supported by this application service"
            )
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=enable_agent_self_rag,
        )
        if (
            options.use_real_rag
            and not allow_demo_defaults
            and retrieval_access_scope is None
        ):
            raise ValueError(
                "trusted retrieval_access_scope is required for production "
                "secure retrieval"
            )
        supervisor = self.supervisor_factory.build(
            workflow=workflow,
            runs_dir=workspace.runs_dir,
            captures_dir=workspace.captures_dir,
            task_manager=task_manager,
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        supervisor.set_prior_model_usage(workspace.read_model_usage())
        return (
            workspace,
            task_manager,
            task_state,
            workflow,
            supervisor,
            settings,
            options,
        )

    @staticmethod
    def _task_from_inspection(inspection: Dict[str, Any]) -> TaskSchema:
        graph_state = inspection.get("graph_state") or {}
        raw_task = graph_state.get("task")
        if not isinstance(raw_task, dict):
            raise RuntimeError("durable checkpoint does not contain the original task")
        # Checkpoints are a JSON boundary.  Validate through Pydantic's JSON
        # path so strict enums remain strict while their wire values are
        # decoded to the canonical Python enum instances.
        task = TaskSchema.model_validate_json(
            json.dumps(raw_task, ensure_ascii=False)
        )
        if (
            task.task_id != inspection.get("task_id")
            or task.run_id != inspection.get("run_id")
        ):
            raise ValueError("durable task identity does not match inspection")
        return task

    def inspect_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Inspect the original task/run checkpoint without creating a new run."""

        (
            workspace,
            _task_manager,
            task_state,
            _workflow,
            supervisor,
            _settings,
            _options,
        ) = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        try:
            inspection = supervisor.inspect(
                task_id=task_id,
                run_id=run_id,
                task_type=task_state.task_type,
            )
            return {
                **inspection,
                "paths": workspace.paths(),
            }
        finally:
            supervisor.close()

    def _continuation_summary(
        self,
        *,
        workspace: RunWorkspace,
        task_manager: JsonlTaskManager,
        result: AgentResultSchema,
    ) -> Dict[str, Any]:
        return {
            "task_id": result.task_id,
            "run_id": result.run_id,
            "status": result.status.value,
            "paths": workspace.paths(),
            "generated_draft": result.result.get("generated_draft"),
            "rejected_artifact": result.result.get("rejected_artifact"),
            "deliverable_final_result": result.result.get(
                "deliverable_final_result"
            ),
            "model_usage": result.result.get("model_usage", {}),
            "task_state": task_manager.get_task(result.task_id).model_dump(
                mode="json"
            ),
            "supervisor_result": result.model_dump(mode="json"),
        }

    def _stream_existing_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        decision: str,
        feedback: str | None,
        reviewer_id: str | None,
        cancel: bool,
        output_root: str | Path | None,
        settings: AppSettings | None,
        enable_agent_self_rag: bool | None,
        allow_demo_defaults: bool,
        retrieval_access_scope: RetrievalAccessScopeSchema | None,
        runnable_config: Dict[str, Any] | None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Rebuild, stream and close one existing durable runtime."""

        (
            workspace,
            task_manager,
            task_state,
            _workflow,
            supervisor,
            _settings,
            _options,
        ) = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        stream = None
        primary_error: BaseException | None = None
        try:
            inspection = supervisor.inspect(
                task_id=task_id,
                run_id=run_id,
                task_type=task_state.task_type,
            )
            task = self._task_from_inspection(inspection)
            stream = (
                supervisor.stream_cancel(
                    task,
                    reason=feedback or "workflow_cancelled",
                    runnable_config=runnable_config,
                )
                if cancel
                else supervisor.stream_resume(
                    task,
                    decision=decision,
                    feedback=feedback,
                    reviewer_id=reviewer_id,
                    runnable_config=runnable_config,
                )
            )
            for stream_event in stream:
                raw_result = stream_event.payload.get("result")
                if raw_result is None:
                    yield stream_event
                    continue
                result = AgentResultSchema.model_validate(raw_result)
                model_usage = supervisor.model_usage_snapshot()
                workspace.write_model_usage(
                    {
                        "schema_version": "model_usage_v1",
                        "task_id": task_id,
                        "run_id": run_id,
                        "created_at": self.clock.now_iso(),
                        "finalized": True,
                        "usage": model_usage,
                    }
                )
                summary = self._continuation_summary(
                    workspace=workspace,
                    task_manager=task_manager,
                    result=result,
                )
                yield stream_event.model_copy(
                    update={
                        "payload": {
                            **stream_event.payload,
                            "agent_result": raw_result,
                            "result": summary,
                        }
                    }
                )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_runtime_stream(
                stream,
                supervisor,
                primary_error=primary_error,
            )

    def stream_resume_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        decision: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
        runnable_config: Dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream approval/rejection of an existing paused task."""

        return self._stream_existing_run(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            decision=decision,
            feedback=feedback,
            reviewer_id=reviewer_id,
            cancel=False,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            runnable_config=runnable_config,
        )

    def stream_cancel_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        reason: str = "workflow_cancelled",
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
        runnable_config: Dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream cancellation of an existing paused task."""

        return self._stream_existing_run(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            decision="cancelled",
            feedback=reason,
            reviewer_id=None,
            cancel=True,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            runnable_config=runnable_config,
        )

    def resume_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        decision: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Resume an existing paused task while preserving task/run identity."""

        (
            workspace,
            task_manager,
            task_state,
            _workflow,
            supervisor,
            _settings,
            _options,
        ) = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        try:
            inspection = supervisor.inspect(
                task_id=task_id,
                run_id=run_id,
                task_type=task_state.task_type,
            )
            task = self._task_from_inspection(inspection)
            result = supervisor.resume(
                task,
                decision=decision,
                feedback=feedback,
                reviewer_id=reviewer_id,
            )
            workspace.write_model_usage(
                {
                    "schema_version": "model_usage_v1",
                    "task_id": task_id,
                    "run_id": run_id,
                    "created_at": self.clock.now_iso(),
                    "finalized": True,
                    "usage": supervisor.model_usage_snapshot(),
                }
            )
            return self._continuation_summary(
                workspace=workspace,
                task_manager=task_manager,
                result=result,
            )
        finally:
            supervisor.close()

    def cancel_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        reason: str = "workflow_cancelled",
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Cancel an existing paused task through its durable graph."""

        (
            workspace,
            task_manager,
            task_state,
            _workflow,
            supervisor,
            _settings,
            _options,
        ) = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        try:
            inspection = supervisor.inspect(
                task_id=task_id,
                run_id=run_id,
                task_type=task_state.task_type,
            )
            task = self._task_from_inspection(inspection)
            result = supervisor.cancel(task, reason=reason)
            return self._continuation_summary(
                workspace=workspace,
                task_manager=task_manager,
                result=result,
            )
        finally:
            supervisor.close()
