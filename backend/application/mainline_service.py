"""端到端主链的应用层入口。

本服务负责可信入口校验、运行目录隔离、Task 构造、依赖装配、Supervisor 启动以及结果
文件汇总。业务 Agent 不应自行处理这些进程级职责。阅读 ``run`` 时按已有“阶段”注释
向下跟踪，可看到 ProjectInput 如何最终变成可交付结果与审计产物。
"""

from __future__ import annotations

from application.terminal_outcome_projector import outcome_from_result, terminal_payload, public_terminal_result

from agent.supervisor_agent import BudgetTopupCommandSchema

from dataclasses import dataclass
import logging
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from application.existing_run_authorization import (
    ExistingRunAuthorizationError,
    ExistingRunAuthorizer,
)
from application.retrieval_access_policy import narrow_retrieval_access_scope
from application.run_workspace import RunWorkspace
from application.task_factory import TaskFactory
from apps.enterprise_document.schemas.project_input_schema import (
    ProjectInputDemoSeedV1Schema,
    ProjectInputSchema,
)
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    normalize_new_project_input,
)
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from contracts.document_catalog import DocumentCatalogPort
from core.config import AppSettings, get_settings
from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, TimestampedUuidIdGenerator
from schemas.rag import RetrievalAccessScopeSchema
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from schemas.workflow_stream import WorkflowStreamEventSchema
from task.task_manager import JsonlTaskManager
from core.diagnostics import current_diagnostics, capture_event


logger = logging.getLogger(__name__)


def _observe_new_run(function):
    """Observe pre-Task failures and wall/close without creating business IDs."""
    @wraps(function)
    def observed(self, *args, **kwargs):
        diagnostic = current_diagnostics()
        if not diagnostic.enabled:
            return function(self, *args, **kwargs)
        from uuid import uuid4
        subject = 'evaluation-ingress-' + uuid4().hex
        source_identity = {}
        try:
            from core.hashing import canonical_sha256
            project = kwargs.get('project_input')
            # Digests bind original ingress without exporting raw business text,
            # settings or untrusted optional credential-bearing metadata.
            source_identity['source_request_hash'] = canonical_sha256({
                'user_input':kwargs.get('user_input'),
                'project_input':{k:v for k,v in project.items() if k not in
                    {'credentials','secrets','api_key','metadata'}} if isinstance(project,dict) else None})
            scope = kwargs.get('retrieval_access_scope')
            source_identity['authenticated_scope_hash'] = canonical_sha256(
                scope.model_dump(mode='json')) if isinstance(scope,RetrievalAccessScopeSchema) else None
            source_identity['scope_identity_proven'] = isinstance(scope,RetrievalAccessScopeSchema)
        except Exception:
            source_identity['source_identity_state'] = 'CAPTURE_INCOMPLETE'
        capture_event('P-INGRESS', {'subject_id': subject, 'task_id': None,
            'run_id': None, 'requested_task_id': kwargs.get('task_id'),
            'requested_run_id': kwargs.get('run_id'), 'phase': 'begin',
            'project_input_present': kwargs.get('project_input') is not None,
            'scope_schema': type(kwargs.get('retrieval_access_scope')).__name__, **source_identity},
            unit_id='B01', operation_id=subject)
        with diagnostic.span(subject, 'WORKFLOW_WALL', unit_id='F06'):
            try:
                result = function(self, *args, **kwargs)
            except BaseException as error:
                capture_event('P-INGRESS', {'subject_id':subject, 'phase':'error',
                    'task_id':None,'run_id':None,'error_type':type(error).__name__,
                    'failure_stage':'ingress_or_assembly', **source_identity},unit_id='B01',operation_id=subject)
                raise
        # Settings and credentials never enter the diagnostic public projection.
        if isinstance(result, dict):
            capture_event('S-PUBLIC', {key:result.get(key) for key in (
                'task_id','run_id','status','terminal_outcome','release_publication',
                'target_release_publication','deliverable_final_result','shutdown_diagnostics')},
                unit_id='E11',operation_id=subject,representation_kind='PUBLIC_DELIVERY_PROJECTION')
        return result
    return observed


@dataclass(frozen=True)
class _AuthorizedExistingRuntime:
    workspace: RunWorkspace
    task_manager: JsonlTaskManager
    task_state: Any
    workflow: Any
    supervisor: Any
    settings: AppSettings
    options: RuntimeOptions
    inspection: Dict[str, Any]
    task: TaskSchema


class MainlineApplicationService:
    """协调一次完整主链运行，但不承载章节生成或检索等领域逻辑。"""
    def __init__(
        self,
        *,
        task_factory: TaskFactory | None = None,
        supervisor_factory: SupervisorFactory | None = None,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
        document_catalog: DocumentCatalogPort | None = None,
    ) -> None:
        """注入任务映射、Supervisor 装配、时间来源和文档事实读取端口。"""
        self.task_factory = task_factory or TaskFactory()
        self.supervisor_factory = supervisor_factory or SupervisorFactory()
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or TimestampedUuidIdGenerator(self.clock)
        self.document_catalog = document_catalog

    def _bind_current_project_retrieval_scope(
        self,
        *,
        project_root: Path,
        current_project_id: str,
        retrieval_access_scope: RetrievalAccessScopeSchema,
    ) -> RetrievalAccessScopeSchema:
        """Resolve final project-scoped authorization from trusted catalog facts."""

        if self.document_catalog is None:
            raise ValueError(
                "document_catalog is required for project-scoped secure retrieval"
            )
        descriptors = self.document_catalog.list_access_descriptors(
            project_root=project_root,
        )
        return narrow_retrieval_access_scope(
            descriptors=descriptors,
            current_project_id=current_project_id,
            retrieval_access_scope=retrieval_access_scope,
        )

    @_observe_new_run
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
        durable_memory_required: bool = False,
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
        run_id = run_id or self.id_generator.new_id("run")
        input_task_id = ""
        current_project_id = ""
        if project_input is not None:
            validated_ingress = (
                ProjectInputDemoSeedV1Schema.model_validate(
                    project_input,
                    strict=True,
                )
                if allow_demo_defaults
                else normalize_new_project_input(project_input, strict=True)
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
            current_project_id = str(
                getattr(validated_ingress, "current_project_id", None) or ""
            ).strip()
            if retrieval_access_scope is not None and current_project_id:
                retrieval_access_scope = self._bind_current_project_retrieval_scope(
                    project_root=project_root,
                    current_project_id=current_project_id,
                    retrieval_access_scope=retrieval_access_scope,
                )
        if (
            retrieval_access_scope is not None
            and "project_fact_kb" in retrieval_access_scope.authorized_kb_ids
            and not current_project_id
            and not allow_demo_defaults
        ):
            raise ValueError(
                "current_project_id is required when project_fact_kb is authorized"
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
        if type(durable_memory_required) is not bool:
            raise TypeError("durable_memory_required must be an explicit bool")
        if durable_memory_required:
            task = task.model_copy(update={"metadata": {
                **task.metadata, "durable_memory_required": True}})
        task_manager = JsonlTaskManager(output_dir=workspace.tasks_dir)
        task_manager.create_task(task)
        # 阶段 4：装配当前唯一的方案生成 Workflow；这里不是 LLM 自由规划，而是确定性工作流定义。
        workflow = build_scheme_generation_workflow(created_at=created_at)
        # 阶段 5：解析模型、RAG Profile、缓存和质量插件等运行时选项。
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=enable_agent_self_rag,
            durable_memory_required=durable_memory_required,
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
        primary_error: BaseException | None = None
        result: AgentResultSchema | None = None
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
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            shutdown_diagnostics = self._close_supervisor(
                supervisor,
                primary_error=primary_error,
                primary_result=result,
            )

        generated_draft: Dict[str, Any] = {}
        rejected_artifact: Dict[str, Any] | None = None
        deliverable_final_result: Dict[str, Any] | None = None
        release_publication: Dict[str, Any] | None = None
        target_release_publication: Dict[str, Any] | None = None
        target_review_draft: Dict[str, Any] | None = None
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
                release_publication = payload.get("release_publication")
                target_release_publication = payload.get("target_release_publication")
                target_review_draft = payload.get("target_review_draft")
                if release_publication is None and isinstance(
                    deliverable_final_result, dict
                ):
                    release_publication = deliverable_final_result.get(
                        "release_publication"
                    )
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
            "release_publication": release_publication,
            "target_release_publication": target_release_publication,
            "target_review_draft": target_review_draft,
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
            "terminal_outcome": outcome_from_result(result),
            "shutdown_diagnostics": shutdown_diagnostics,
        }

    @staticmethod
    def _close_supervisor(
        supervisor: Any,
        *,
        primary_error: BaseException | None,
        primary_result: AgentResultSchema | None = None,
    ) -> list[dict[str, str]]:
        """Keep primary failure facts; shutdown diagnostics do not imply idle."""

        try:
            with current_diagnostics().span('supervisor.close', 'DRAIN'):
                supervisor.close()
        except Exception as exc:
            structured_failure = (
                isinstance(primary_result, AgentResultSchema)
                and primary_result.status in {
                    ExecutionStatus.FAILED, ExecutionStatus.RETRYABLE_FAILED,
                }
                and primary_result.error is not None
            )
            if primary_error is None and not structured_failure:
                raise
            logger.warning(
                "supervisor shutdown failed after a primary runtime error",
                exc_info=True,
            )
            return [{
                "event_type": "supervisor_close_failed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }]
        return []

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
        supervisor_error: Exception | None = None
        try:
            supervisor.close()
        except Exception as exc:
            supervisor_error = exc
            logger.warning("supervisor shutdown failed", exc_info=True)
        if close_error is not None and primary_error is None:
            raise close_error
        if supervisor_error is not None and primary_error is None:
            raise supervisor_error

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
        durable_memory_required: bool = False,
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
        run_id = run_id or self.id_generator.new_id("run")
        input_task_id = ""
        current_project_id = ""
        if project_input is not None:
            validated_ingress = (
                ProjectInputDemoSeedV1Schema.model_validate(
                    project_input,
                    strict=True,
                )
                if allow_demo_defaults
                else normalize_new_project_input(project_input, strict=True)
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
            current_project_id = str(
                getattr(validated_ingress, "current_project_id", None) or ""
            ).strip()
            if retrieval_access_scope is not None and current_project_id:
                retrieval_access_scope = self._bind_current_project_retrieval_scope(
                    project_root=project_root,
                    current_project_id=current_project_id,
                    retrieval_access_scope=retrieval_access_scope,
                )
        if (
            retrieval_access_scope is not None
            and "project_fact_kb" in retrieval_access_scope.authorized_kb_ids
            and not current_project_id
            and not allow_demo_defaults
        ):
            raise ValueError(
                "current_project_id is required when project_fact_kb is authorized"
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
        if type(durable_memory_required) is not bool:
            raise TypeError("durable_memory_required must be an explicit bool")
        if durable_memory_required:
            task = task.model_copy(update={"metadata": {
                **task.metadata, "durable_memory_required": True}})
        task_manager = JsonlTaskManager(output_dir=workspace.tasks_dir)
        task_manager.create_task(task)
        workflow = build_scheme_generation_workflow(created_at=created_at)
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=enable_agent_self_rag,
            durable_memory_required=durable_memory_required,
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
        require_execution_compatibility: bool = False,
    ):
        """Authorize metadata/checkpoint before exposing one existing runtime."""

        settings = settings or get_settings()
        ExistingRunAuthorizer.validate_trusted_scope(retrieval_access_scope)
        workspace = RunWorkspace(
            output_root=(
                Path(output_root) if output_root is not None else settings.data_root
            ),
            task_id=task_id,
            run_id=run_id,
        )
        task_manager = JsonlTaskManager(output_dir=workspace.tasks_dir)
        try:
            authorization_metadata = task_manager.get_authorization_metadata(
                task_id
            )
        except (KeyError, OSError, ValueError) as exc:
            raise ExistingRunAuthorizationError(
                "existing run is not authorized"
            ) from exc
        authenticated_tenant = ExistingRunAuthorizer.authorize_task_metadata(
            authorization_metadata,
            requested_task_id=task_id,
            requested_run_id=run_id,
            retrieval_access_scope=retrieval_access_scope,
            allow_demo_defaults=allow_demo_defaults,
        )

        # Full Task JSONL is opened only after the immutable identity tenant is
        # authorized, then its identity must exactly match that projection.
        try:
            task_state = task_manager.get_task(task_id)
            ExistingRunAuthorizer.authorize_task_state_identity(
                task_state=task_state,
                identity=authorization_metadata,
                authenticated_tenant=authenticated_tenant,
            )
        except ExistingRunAuthorizationError:
            raise
        except (KeyError, OSError, ValueError) as exc:
            raise ExistingRunAuthorizationError(
                "existing run is not authorized"
            ) from exc
        if require_execution_compatibility:
            ExistingRunAuthorizer.authorize_task_state_execution_compatibility(
                task_state
            )
        workflow = build_scheme_generation_workflow(
            created_at=task_state.created_at
        )
        if task_state.task_type != workflow.task_type:
            raise ValueError(
                "persisted task type is not supported by this application service"
            )
        current_project_id = str(
            task_state.metadata.get("current_project_id") or ""
        ).strip()
        if (
            retrieval_access_scope is not None
            and "project_fact_kb" in retrieval_access_scope.authorized_kb_ids
            and not current_project_id
            and not allow_demo_defaults
        ):
            raise ExistingRunAuthorizationError(
                "existing run is missing current_project_id for project_fact_kb"
            )
        if retrieval_access_scope is not None and current_project_id:
            retrieval_access_scope = self._bind_current_project_retrieval_scope(
                project_root=project_root,
                current_project_id=current_project_id,
                retrieval_access_scope=retrieval_access_scope,
            )
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=enable_agent_self_rag,
            durable_memory_required=task_state.metadata.get("durable_memory_required", False),
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
        try:
            inspection = supervisor.inspect(
                task_id=task_id,
                run_id=run_id,
                task_type=task_state.task_type,
            )
            task = ExistingRunAuthorizer.authorize_checkpoint(
                inspection=inspection,
                metadata=authorization_metadata,
                authenticated_tenant=authenticated_tenant,
            )
            # Task-state metadata selects the capability before composition;
            # the immutable, hash-authorized request must confirm that choice.
            required_memory = task.metadata.get("durable_memory_required", False)
            if type(required_memory) is not bool or required_memory != options.durable_memory_required:
                from apps.enterprise_document.services.agent_rag_v1.memory_composition import DurableMemoryCapabilityError
                raise DurableMemoryCapabilityError("MEMORY_EXECUTION_DECLARATION_MISMATCH")
            # Model usage is sensitive run data and is loaded only after every
            # tenant and immutable-identity check succeeds.
            supervisor.set_prior_model_usage(workspace.read_model_usage())
        except BaseException as exc:
            self._close_supervisor(supervisor, primary_error=exc)
            raise
        return _AuthorizedExistingRuntime(
            workspace=workspace,
            task_manager=task_manager,
            task_state=task_state,
            workflow=workflow,
            supervisor=supervisor,
            settings=settings,
            options=options,
            inspection=inspection,
            task=task,
        )

    @staticmethod
    def _input_snapshot_version(inspection: Dict[str, Any]) -> str | None:
        """Extract the current human-review snapshot from a durable interrupt."""

        for interrupt_item in inspection.get("interrupts") or []:
            value = (
                interrupt_item.get("value")
                if isinstance(interrupt_item, dict)
                else None
            )
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            snapshot = str(
                value.get("input_snapshot_version")
                or metadata.get("input_snapshot_version")
                or ""
            ).strip()
            if snapshot:
                return snapshot
        return None

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

        runtime = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        primary_error: BaseException | None = None
        try:
            return {
                **runtime.inspection,
                "input_snapshot_version": self._input_snapshot_version(
                    runtime.inspection
                ),
                "paths": runtime.workspace.paths(),
            }
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(
                runtime.supervisor,
                primary_error=primary_error,
            )

    def read_terminal_result(
        self, *, project_root: Path, task_id: str, run_id: str,
        output_root: str | Path | None = None, settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None, allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Authorize and replay the committed terminal result without dispatch."""
        runtime = self._build_existing_runtime(project_root=project_root, task_id=task_id, run_id=run_id,
            output_root=output_root, settings=settings, enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults, retrieval_access_scope=retrieval_access_scope)
        primary_error = None
        try:
            raw = runtime.inspection.get("terminal_agent_result")
            if raw is None:
                raise ValueError("run has no committed terminal result")
            result = AgentResultSchema.model_validate(raw)
            if (result.task_id, result.run_id) != (task_id, run_id):
                raise ValueError("terminal result identity differs from authorized Run")
            return self._continuation_summary(workspace=runtime.workspace,
                task_manager=runtime.task_manager, result=result)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(runtime.supervisor, primary_error=primary_error)

    @staticmethod
    def public_result(summary: Dict[str, Any]) -> Dict[str, Any]:
        """Safe external result; legacy/internal summaries retain their fields."""
        return public_terminal_result(summary)

    def _continuation_summary(
        self,
        *,
        workspace: RunWorkspace,
        task_manager: JsonlTaskManager,
        result: AgentResultSchema,
    ) -> Dict[str, Any]:
        payload = terminal_payload(result)
        return {
            "task_id": result.task_id,
            "run_id": result.run_id,
            "status": result.status.value,
            "paths": workspace.paths(),
            "generated_draft": payload.get("generated_draft"),
            "rejected_artifact": payload.get("rejected_artifact"),
            "deliverable_final_result": payload.get(
                "deliverable_final_result"
            ),
            "release_publication": payload.get("release_publication"),
            "target_release_publication": payload.get("target_release_publication"),
            "target_review_draft": payload.get("target_review_draft"),
            "model_usage": result.result.get("model_usage", {}),
            "task_state": task_manager.get_task(result.task_id).model_dump(
                mode="json"
            ),
            "supervisor_result": result.model_dump(mode="json"),
            "terminal_outcome": outcome_from_result(result),
        }

    def _stream_existing_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        decision: str,
        input_snapshot_version: str | None,
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

        runtime = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            require_execution_compatibility=not cancel,
        )
        stream = None
        primary_error: BaseException | None = None
        try:
            if not cancel:
                ExistingRunAuthorizer.authorize_execution_compatibility(runtime.task)
            stream = (
                runtime.supervisor.stream_cancel(
                    runtime.task,
                    reason=feedback or "workflow_cancelled",
                    runnable_config=runnable_config,
                )
                if cancel
                else runtime.supervisor.stream_resume(
                    runtime.task,
                    decision=decision,
                    input_snapshot_version=input_snapshot_version,
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
                model_usage = runtime.supervisor.model_usage_snapshot()
                runtime.workspace.write_model_usage(
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
                    workspace=runtime.workspace,
                    task_manager=runtime.task_manager,
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
                runtime.supervisor,
                primary_error=primary_error,
            )

    def stream_resume_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        decision: str,
        input_snapshot_version: str,
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
            input_snapshot_version=input_snapshot_version,
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
            input_snapshot_version=None,
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

    def _budget_command_summary(self, runtime, acknowledgement):
        native = runtime.supervisor.inspect_budget_state(runtime.task)
        summary = {
            "task_id": runtime.task.task_id, "run_id": runtime.task.run_id,
            "status": native["run"]["status"],
            "budget_acknowledgement": acknowledgement,
            "canonical_budget_state": native,
            "paths": runtime.workspace.paths(),
            "task_state": runtime.task_manager.get_task(runtime.task.task_id).model_dump(mode="json"),
        }
        summary["canonical_run_status"] = native["run"]["status"]
        raw_result = acknowledgement.get("agent_result")
        if raw_result is not None:
            actual_result = AgentResultSchema.model_validate(raw_result)
            if acknowledgement.get("read_only"):
                # Historical receipt reading cannot replace the actual Task or
                # canonical control projection after cancellation/a newer hold.
                summary["historical_supervisor_result"] = actual_result.model_dump(mode="json")
                summary["result_disposition"] = acknowledgement["disposition"]
            else:
                summary["status"] = actual_result.status.value
                summary["supervisor_result"] = actual_result.model_dump(mode="json")
                from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RUN_TERMINAL_STATUSES, RunLifecycleStatus
                if RunLifecycleStatus(native["run"]["status"]) in RUN_TERMINAL_STATUSES:
                    from application.terminal_outcome_schema import TerminalOutcomeResultV1
                    payload = terminal_payload(actual_result)
                    owner_outcome = (TerminalOutcomeResultV1.model_validate(payload["terminal_outcome"])
                        if payload.get("terminal_outcome") is not None else None)
                    if (owner_outcome is not None
                            and owner_outcome.run_status is not None
                            and owner_outcome.run_status.value == native["run"]["status"]
                            and owner_outcome.execution_status == actual_result.status):
                        summary = {
                            **self._continuation_summary(workspace=runtime.workspace,
                                task_manager=runtime.task_manager, result=actual_result),
                            **summary,
                        }

        return summary


    def topup_budget_run(self, *, project_root: Path, task_id: str, run_id: str,
                         command: BudgetTopupCommandSchema | None = None, continue_document: bool = True,
                         output_root: str | Path | None = None, settings=None,
                         enable_agent_self_rag=None, allow_demo_defaults=False,
                         retrieval_access_scope=None) -> Dict[str, Any]:
        """Approve an explicit finite command and optionally continue its exact gate."""
        if (command is not None and not isinstance(command, BudgetTopupCommandSchema)) or type(continue_document) is not bool:
            raise ValueError("a typed explicit Budget command and Boolean continuation flag are required")
        runtime = self._build_existing_runtime(
            project_root=project_root, task_id=task_id, run_id=run_id,
            output_root=output_root, settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            require_execution_compatibility=True,
        )
        primary_error = None
        try:
            ExistingRunAuthorizer.authorize_execution_compatibility(runtime.task)
            if command is None:
                acknowledgement=runtime.supervisor.pending_budget_authorization(runtime.task)
            else:
                try:
                    validated_command=BudgetTopupCommandSchema.model_validate(command.model_dump(mode="json"))
                except (ValueError,TypeError) as structural_error:
                    acknowledgement=runtime.supervisor.reject_budget_command_shape(runtime.task,command,structural_error)
                else:
                    acknowledgement=runtime.supervisor.submit_budget_topup(runtime.task,validated_command,
                        continue_document=continue_document)
            if getattr(runtime.supervisor,"_budget_usage_projection_required",False):
                runtime.workspace.write_model_usage({
                    "schema_version": "model_usage_v1", "task_id": task_id, "run_id": run_id,
                    "created_at": self.clock.now_iso(), "finalized": True,
                    "usage": runtime.supervisor.model_usage_snapshot(),
                })
            return self._budget_command_summary(runtime, acknowledgement)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(runtime.supervisor, primary_error=primary_error)


    def resume_budget_run(self, *, project_root: Path, task_id: str, run_id: str,
                          accepted_grant_ref: str, accepted_grant_sha256: str,
                          output_root: str | Path | None = None, settings=None,
                          enable_agent_self_rag=None, allow_demo_defaults=False,
                          retrieval_access_scope=None) -> Dict[str, Any]:
        """Continue an already accepted native grant; this entry cannot add capacity."""
        if (not isinstance(accepted_grant_ref,str) or not accepted_grant_ref.strip()
                or not isinstance(accepted_grant_sha256,str) or len(accepted_grant_sha256)!=64
                or any(value not in "0123456789abcdef" for value in accepted_grant_sha256)):
            raise ValueError("accepted native Budget grant exact reference/hash is required")
        runtime = self._build_existing_runtime(
            project_root=project_root, task_id=task_id, run_id=run_id,
            output_root=output_root, settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            require_execution_compatibility=True,
        )
        primary_error = None
        try:
            ExistingRunAuthorizer.authorize_execution_compatibility(runtime.task)
            acknowledgement = runtime.supervisor.resume_budget_topup(runtime.task,
                accepted_grant_ref=accepted_grant_ref, accepted_grant_sha256=accepted_grant_sha256)
            if getattr(runtime.supervisor,"_budget_usage_projection_required",False):
                runtime.workspace.write_model_usage({
                    "schema_version": "model_usage_v1", "task_id": task_id, "run_id": run_id,
                    "created_at": self.clock.now_iso(), "finalized": True,
                    "usage": runtime.supervisor.model_usage_snapshot(),
                })
            return self._budget_command_summary(runtime, acknowledgement)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(runtime.supervisor, primary_error=primary_error)

    # PREPARED_NOT_APPLIED_NOT_RUN. Trusted application entry for a supplied exact
    # immutable positive packet. It never scans receipts or routes generic recovery.
    def recover_budget_pause_from_wait_packet(self, *, project_root: Path, task_id: str, run_id: str,
                                             wait_ref: str, wait_sha256: str,
                                             output_root: str | Path | None = None, settings=None,
                                             enable_agent_self_rag=None, allow_demo_defaults=False,
                                             retrieval_access_scope=None) -> Dict[str, Any]:
        if (not isinstance(wait_ref,str) or not wait_ref.strip()
                or not isinstance(wait_sha256,str) or len(wait_sha256)!=64
                or any(char not in "0123456789abcdef" for char in wait_sha256)):
            raise ValueError("Budget wait recovery requires exact immutable packet ref/hash")
        runtime=self._build_existing_runtime(project_root=project_root,task_id=task_id,run_id=run_id,
            output_root=output_root,settings=settings,enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,retrieval_access_scope=retrieval_access_scope,
            require_execution_compatibility=True)
        primary_error=None
        try:
            ExistingRunAuthorizer.authorize_execution_compatibility(runtime.task)
            observation=runtime.supervisor.recover_budget_pause_from_wait_packet(runtime.task,
                wait_ref=wait_ref,wait_sha256=wait_sha256)
            return {"task_id":task_id,"run_id":run_id,"status":observation["canonical_budget_state"]["run"]["status"],
                "budget_wait_recovery":observation,"paths":runtime.workspace.paths(),
                "task_state":runtime.task_manager.get_task(task_id).model_dump(mode="json")}
        except BaseException as exc:
            primary_error=exc
            raise
        finally:
            self._close_supervisor(runtime.supervisor,primary_error=primary_error)

    def resume_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        decision: str,
        input_snapshot_version: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Resume an existing paused task while preserving task/run identity."""

        runtime = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            require_execution_compatibility=True,
        )
        primary_error: BaseException | None = None
        try:
            ExistingRunAuthorizer.authorize_execution_compatibility(runtime.task)
            result = runtime.supervisor.resume(
                runtime.task,
                decision=decision,
                input_snapshot_version=input_snapshot_version,
                feedback=feedback,
                reviewer_id=reviewer_id,
            )
            runtime.workspace.write_model_usage(
                {
                    "schema_version": "model_usage_v1",
                    "task_id": task_id,
                    "run_id": run_id,
                    "created_at": self.clock.now_iso(),
                    "finalized": True,
                    "usage": runtime.supervisor.model_usage_snapshot(),
                }
            )
            return self._continuation_summary(
                workspace=runtime.workspace,
                task_manager=runtime.task_manager,
                result=result,
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(
                runtime.supervisor,
                primary_error=primary_error,
            )

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

        runtime = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
        primary_error: BaseException | None = None
        try:
            result = runtime.supervisor.cancel(runtime.task, reason=reason)
            return self._continuation_summary(
                workspace=runtime.workspace,
                task_manager=runtime.task_manager,
                result=result,
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(
                runtime.supervisor,
                primary_error=primary_error,
            )

    def _recover_existing_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        retry_only: bool,
        recovery_reason: str,
        expected_revision: int | None,
        output_root: str | Path | None,
        settings: AppSettings | None,
        enable_agent_self_rag: bool | None,
        allow_demo_defaults: bool,
        retrieval_access_scope: RetrievalAccessScopeSchema | None,
    ) -> Dict[str, Any]:
        """Authorize, CAS and continue one durable recovery attempt."""

        runtime = self._build_existing_runtime(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
            require_execution_compatibility=True,
        )
        primary_error: BaseException | None = None
        try:
            ExistingRunAuthorizer.authorize_execution_compatibility(runtime.task)
            if (
                runtime.inspection.get("interrupts")
                or runtime.task_state.status.value == "paused"
            ):
                raise ValueError(
                    "human review is waiting; inspect the run and use resume_run"
                )
            observed_revision = runtime.task_state.revision
            if (
                expected_revision is not None
                and expected_revision != observed_revision
            ):
                raise ValueError(
                    "task revision conflict: "
                    f"expected={expected_revision} actual={observed_revision}"
                )

            # This read-only checkpoint plan must succeed before the append-only
            # recovery event is committed.
            runtime.supervisor.recovery_plan(runtime.task)
            engine = runtime.supervisor.workflow_engine
            section_graph = getattr(engine, "section_execution_graph", None)
            if section_graph is not None:
                workflow = runtime.supervisor.catalog.get(runtime.task.task_type)
                config = engine.runtime_state_codec.checkpoint_config(workflow, run_id=run_id)
                native = engine.checkpointer.get_tuple(config)
                values = (native.checkpoint or {}).get("channel_values", {}) if native is not None else {}
                payload = engine.business_state_codec.restore_payload(values)
                namespace = engine.runtime_state_codec.workflow_namespace(workflow)
                if (payload.task_id != task_id or payload.run_id != run_id
                        or payload.workflow_namespace != namespace or payload.document_plan is None):
                    raise ValueError("INDETERMINATE: native recovery plan identity is unavailable")
                section_graph.assert_native_recovery_admissible(workflow_namespace=namespace,
                    task_id=task_id, run_id=run_id,
                    section_ids=[section.section_id for section in payload.document_plan.sections])
            recovery_policy = (
                "retryable_failed_checkpoint_v1"
                if retry_only
                else "durable_checkpoint_recovery_v1"
            )
            started = runtime.task_manager.mark_recovery_started(
                runtime.task,
                expected_revision=observed_revision,
                recovery_reason=recovery_reason,
                recovery_policy=recovery_policy,
                max_recovery_count=runtime.settings.max_recovery_attempts,
                retry_only=retry_only,
            )
            recovery_attempt = int(
                started.metadata.get("recovery_attempt_count", 0)
            )
            result = runtime.supervisor.recover(
                runtime.task,
                recovery_attempt=recovery_attempt,
                recovery_reason=recovery_reason,
                recovery_policy=recovery_policy,
            )
            runtime.workspace.write_model_usage(
                {
                    "schema_version": "model_usage_v1",
                    "task_id": task_id,
                    "run_id": run_id,
                    "created_at": self.clock.now_iso(),
                    "finalized": True,
                    "usage": runtime.supervisor.model_usage_snapshot(),
                }
            )
            summary = self._continuation_summary(
                workspace=runtime.workspace,
                task_manager=runtime.task_manager,
                result=result,
            )
            summary["recovery"] = {
                "attempt": recovery_attempt,
                "reason": recovery_reason,
                "policy": recovery_policy,
                "previous_revision": observed_revision,
                "recovery_revision": started.revision,
            }
            return summary
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self._close_supervisor(
                runtime.supervisor,
                primary_error=primary_error,
            )

    def recover_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        recovery_reason: str = "durable_execution_interruption",
        expected_revision: int | None = None,
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Recover an interrupted run only when checkpoint and error facts allow."""

        return self._recover_existing_run(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            retry_only=False,
            recovery_reason=recovery_reason,
            expected_revision=expected_revision,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )

    def retry_run(
        self,
        *,
        project_root: Path,
        task_id: str,
        run_id: str,
        recovery_reason: str = "retryable_execution_failure",
        expected_revision: int | None = None,
        output_root: str | Path | None = None,
        settings: AppSettings | None = None,
        enable_agent_self_rag: bool | None = None,
        allow_demo_defaults: bool = False,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> Dict[str, Any]:
        """Retry only a task explicitly persisted as RETRYABLE_FAILED."""

        return self._recover_existing_run(
            project_root=project_root,
            task_id=task_id,
            run_id=run_id,
            retry_only=True,
            recovery_reason=recovery_reason,
            expected_revision=expected_revision,
            output_root=output_root,
            settings=settings,
            enable_agent_self_rag=enable_agent_self_rag,
            allow_demo_defaults=allow_demo_defaults,
            retrieval_access_scope=retrieval_access_scope,
        )
