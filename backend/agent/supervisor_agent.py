"""Supervisor orchestration boundary for task routing and workflow execution."""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable, Dict, Iterable, Optional

from agent.agent_registry import AgentRegistry
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_workflow_engine import LangGraphWorkflowEngine
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from agent.services.context_factory import ContextBundleFactory
from agent.services.task_lifecycle import TaskLifecycleService
from agent.services.workflow_router import WorkflowCatalog, WorkflowRouter
from agent.services.workflow_trace import WorkflowTraceService
from contracts.observability import TraceSink
from contracts.task_state import TaskStateManager
from contracts.workflow_engine import WorkflowEnginePort
from core.error_factory import ErrorFactory
from core.retry_policy import RetryDecisionPolicy, classify_failure
from model_gateway.model_gateway import ModelGateway
from model_gateway.usage_ledger import merge_model_usage_snapshots
from observability.trace_context import TraceSpanHandle, activate_span
from schemas.agent import AgentResultSchema
from schemas.failure import FailureCategory
from schemas.graph import WorkflowEngineResultSchema
from schemas.human_review import (
    HumanReviewDecisionSchema,
    HumanReviewRequestSchema,
    HumanReviewStatus,
)
from schemas.status import ExecutionStatus, is_failure
from schemas.task import TaskSchema
from schemas.workflow_stream import WorkflowStreamEventSchema


logger = logging.getLogger(__name__)


class SupervisorAgent:
    """Route one task, execute its workflow, and publish one terminal result."""

    agent_name = "SupervisorAgent"
    agent_type = "supervisor"

    def __init__(
        self,
        agent_registry: AgentRegistry,
        workflows: Dict[str, WorkflowDefinitionSchema],
        run_trace_recorder: Optional[TraceSink] = None,
        task_manager: Optional[TaskStateManager] = None,
        model_gateway: Optional[ModelGateway] = None,
        supervisor_model_name: str = "fake_llm",
        enable_llm_routing: bool = True,
        workflow_engine: WorkflowEnginePort | None = None,
        owned_resources: Iterable[Any] | None = None,
        retry_policy: RetryDecisionPolicy | None = None,
    ) -> None:
        # 工作流目录 它只是已注册工作流的索引
        self.catalog = WorkflowCatalog(workflows)
        # 统一模型调用网关
        self.model_gateway = model_gateway
        # 工作流路由器，决定当前 Task 应该走哪个已注册 Workflow。
        self.router = WorkflowRouter(
            self.catalog,
            model_gateway=model_gateway,
            model_name=supervisor_model_name,
            enable_llm_routing=enable_llm_routing,
            caller_agent=self.agent_name,
        )
        # 执行 Workflow 的引擎
        self.workflow_engine = workflow_engine or LangGraphWorkflowEngine(
            agent_registry=agent_registry,
            run_trace_recorder=run_trace_recorder,
        )
        # 把 TaskSchema 转换成统一的 ContextBundleSchema
        self.context_factory = ContextBundleFactory()
        # 任务生命周期管理服务
        self.lifecycle = TaskLifecycleService(task_manager)
        #  运行级和工作流级 Trace 服务
        self.trace = WorkflowTraceService(run_trace_recorder)
        self.error_factory = ErrorFactory()
        self.retry_policy = retry_policy or RetryDecisionPolicy()
        self._owned_resources = tuple(owned_resources or ())
        self._resource_close_lock = threading.Lock()
        self._resource_close_requested = False
        self._resources_closed = False
        self._shutdown_diagnostics: list[dict[str, Any]] = []
        self._prior_model_usage: Dict[str, Any] = {}

    def model_usage_snapshot(self) -> Dict[str, Any]:
        """Return provider-attempt and logical-call usage for this run."""

        if self.model_gateway is None:
            return dict(self._prior_model_usage)
        snapshot = getattr(self.model_gateway, "usage_snapshot", None)
        current = dict(snapshot()) if callable(snapshot) else {}
        return merge_model_usage_snapshots(self._prior_model_usage, current)

    def set_prior_model_usage(self, snapshot: Dict[str, Any]) -> None:
        """Seed a resumed runtime with its already-persisted usage baseline."""

        self._prior_model_usage = dict(snapshot or {})

    def defer_until_idle(self, callback: Callable[[], None]) -> bool:
        """Run a finalization callback after any timed-out worker exits."""

        defer = getattr(self.workflow_engine, "defer_until_idle", None)
        if callable(defer):
            return bool(defer(callback))
        callback()
        return False

    def close(self) -> None:
        """Release resources only after timed-out node workers physically exit.

        A workflow timeout can return while a native model/RAG call is still
        unwinding.  Closing those dependencies immediately creates a shutdown
        race.  Runtimes that expose ``defer_until_idle`` own that boundary;
        other WorkflowEnginePort implementations retain synchronous shutdown.
        """

        with self._resource_close_lock:
            if self._resource_close_requested:
                return
            self._resource_close_requested = True

        cancel_active = getattr(self.workflow_engine, "cancel_active_workers", None)
        if callable(cancel_active):
            cancel_active("supervisor_close")

        defer_until_idle = getattr(self.workflow_engine, "defer_until_idle", None)
        if callable(defer_until_idle):
            defer_until_idle(self._close_owned_resources)
            return
        self._close_owned_resources()

    @property
    def shutdown_diagnostics(self) -> tuple[dict[str, Any], ...]:
        """Return structured best-effort shutdown failures for observability."""

        with self._resource_close_lock:
            return tuple(dict(item) for item in self._shutdown_diagnostics)

    def _close_owned_resources(self) -> None:
        """Idempotently close resources after the workflow engine is idle."""

        with self._resource_close_lock:
            if self._resources_closed:
                return

        seen: set[int] = set()
        failures: list[dict[str, Any]] = []
        for resource in reversed(self._owned_resources):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    diagnostic = {
                        "event_type": "owned_resource_close_failed",
                        "resource_type": type(resource).__name__,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                    failures.append(diagnostic)
                    logger.warning(
                        "owned resource close failed: resource_type=%s",
                        type(resource).__name__,
                        exc_info=True,
                    )

        with self._resource_close_lock:
            self._shutdown_diagnostics.extend(failures)
            # A failed resource remains owned and may still be recovered by its
            # own finalizer.  Successful shutdown is published only when every
            # unique resource accepted close without an exception.
            self._resources_closed = not failures

    def inspect(
        self,
        *,
        task_id: str,
        run_id: str,
        task_type: str,
    ) -> dict[str, Any]:
        """Inspect an existing durable run without creating or routing a task."""

        if not self.workflow_engine.capabilities.inspect:
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support inspect"
            )
        workflow = self.catalog.get(task_type)
        inspection = self.workflow_engine.inspect(workflow, run_id=run_id)
        graph_state = inspection.get("graph_state") or {}
        if graph_state and (
            graph_state.get("task_id") != task_id
            or graph_state.get("run_id") != run_id
        ):
            raise ValueError("checkpoint task/run identity mismatch")
        task_state = None
        if self.lifecycle.task_manager is not None:
            task_state = self.lifecycle.task_manager.get_task(task_id)
            if task_state.run_id != run_id or task_state.task_type != task_type:
                raise ValueError("task state identity does not match checkpoint")
        return {
            **inspection,
            "task_id": task_id,
            "task_state": (
                task_state.model_dump(mode="json")
                if task_state is not None
                else None
            ),
        }

    def _result_from_continuation(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        state: GraphStateSchema,
        execution: Any,
        resumed: bool = True,
        routing_metadata: dict[str, Any] | None = None,
    ) -> AgentResultSchema:
        workflow_complete = bool(execution.metadata.get("workflow_complete"))
        failed_results = [
            item for item in execution.node_results if is_failure(item.status)
        ]
        final_error = next(
            (
                item.error
                for item in failed_results
                if item.error is not None
            ),
            execution.error,
        )
        state.status = execution.status
        state.context_bundle.runtime.status = execution.status
        model_usage = self.model_usage_snapshot()
        review_status = HumanReviewStatus(
            execution.metadata.get(
                "human_review_status",
                HumanReviewStatus.NOT_REQUIRED.value,
            )
        )
        raw_request = execution.metadata.get("human_review_request")
        raw_decision = execution.metadata.get("human_review_decision")
        return AgentResultSchema(
            result_id=(
                (
                    f"result_{task.run_id}_supervisor_resume_"
                    f"{execution.metadata.get('resume_count', 0)}"
                )
                if resumed
                else f"result_{task.run_id}_supervisor"
            ),
            task_id=task.task_id,
            run_id=task.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=execution.status,
            result_type="workflow_result",
            result={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "workflow_complete": workflow_complete,
                "routing": dict(routing_metadata or {}),
                "sub_agent_results": [
                    item.model_dump() for item in execution.node_results
                ],
                "shared_state": state.model_dump(),
                "workflow_execution": execution.model_dump(),
                "generated_draft": state.generated_draft,
                "rejected_artifact": state.rejected_artifact,
                "deliverable_final_result": state.deliverable_final_result,
                "model_usage": model_usage,
            },
            error=final_error,
            error_message=final_error.message if final_error else None,
            human_review_required=bool(
                execution.metadata.get("human_review_required", False)
            ),
            human_review_recommended=bool(
                execution.metadata.get("human_review_recommended", False)
            ),
            human_review_status=review_status,
            human_review_request=(
                HumanReviewRequestSchema.model_validate(raw_request)
                if raw_request
                else None
            ),
            human_review_decision=(
                HumanReviewDecisionSchema.model_validate(raw_decision)
                if raw_decision
                else None
            ),
            metadata={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "workflow_complete": workflow_complete,
                "workflow_engine": execution.engine_name,
                "resumed": resumed,
                "resume_count": execution.metadata.get("resume_count", 0),
                "review_decision": (
                    raw_decision.get("decision")
                    if isinstance(raw_decision, dict)
                    else None
                ),
                "model_usage": model_usage,
                "human_review_status": review_status.value,
            },
        )

    def _continue_paused(
        self,
        task: TaskSchema,
        *,
        resume_value: dict[str, Any],
        cancel: bool,
    ) -> AgentResultSchema:
        workflow, inspection, state = self._prepare_paused_continuation(task)

        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "resume": True,
                "decision": resume_value.get("decision"),
                "resume_count": inspection.get("resume_count", 0),
            },
        )
        with activate_span(run_span):
            self.lifecycle.mark_running(task)
            self.trace.record(
                task=task,
                workflow=workflow,
                event_type="workflow_resumed",
                status=ExecutionStatus.RUNNING,
                payload={
                    "decision": resume_value.get("decision"),
                    "feedback_present": bool(resume_value.get("feedback")),
                },
            )
            with activate_span(workflow_span):
                execution = (
                    self.workflow_engine.cancel(
                        workflow,
                        state,
                        reason=str(
                            resume_value.get("feedback")
                            or "workflow_cancelled"
                        ),
                    )
                    if cancel
                    else self.workflow_engine.resume(
                        workflow,
                        state,
                        resume_value,
                    )
                )
            result = self._result_from_continuation(
                task=task,
                workflow=workflow,
                state=state,
                execution=execution,
                resumed=True,
            )
            self.lifecycle.mark_terminal(task, result)
            self.trace.finish_workflow(
                task=task,
                workflow=workflow,
                handle=workflow_span,
                result=result,
            )
            self.trace.finish_run(
                task=task,
                component_name=self.agent_name,
                handle=run_span,
                result=result,
            )
            return result

    def _prepare_paused_continuation(
        self,
        task: TaskSchema,
    ) -> tuple[
        WorkflowDefinitionSchema,
        dict[str, Any],
        GraphStateSchema,
    ]:
        """Validate durable identity and return the original paused graph state."""

        workflow = self.catalog.get(task.task_type)
        inspection = self.inspect(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
        )
        task_state = inspection.get("task_state") or {}
        if task_state.get("status") != ExecutionStatus.PAUSED.value:
            raise ValueError("only a paused task can be resumed or cancelled")
        if not inspection.get("interrupts"):
            raise ValueError("paused task has no durable interrupt to continue")
        if not inspection.get("checkpoint_config"):
            raise RuntimeError("paused task has no durable checkpoint to continue")
        raw_state = inspection.get("graph_state")
        if not raw_state:
            raise RuntimeError("checkpoint inspection did not return graph state")
        state = GraphStateSchema.model_validate(raw_state)
        if (
            state.task_id != task.task_id
            or state.run_id != task.run_id
            or state.task_type != task.task_type
        ):
            raise ValueError("resume graph-state identity mismatch")
        return workflow, inspection, state

    def _stream_reconciliation_result(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        cause: BaseException,
        consumer_disconnected: bool,
        cancel_requested: bool,
    ) -> AgentResultSchema:
        """Reconcile a non-terminal stream with durable checkpoint truth."""

        inspection: dict[str, Any] = {}
        inspection_error: Exception | None = None
        try:
            inspection = self.workflow_engine.inspect(
                workflow,
                run_id=task.run_id,
            )
        except Exception as exc:
            inspection_error = exc
            logger.warning(
                "stream checkpoint reconciliation inspect failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )

        interrupts = list(inspection.get("interrupts") or [])
        raw_graph_state = inspection.get("graph_state") or {}
        checkpoint_status: ExecutionStatus | None = None
        if isinstance(raw_graph_state, dict) and raw_graph_state.get("status"):
            try:
                checkpoint_status = ExecutionStatus(raw_graph_state["status"])
            except ValueError:
                checkpoint_status = None

        manager = self.lifecycle.task_manager
        current_status = ExecutionStatus.RUNNING
        if manager is not None:
            current_status = manager.get_task(task.task_id).status

        if current_status != ExecutionStatus.RUNNING:
            reconciled_status = current_status
        elif interrupts:
            reconciled_status = ExecutionStatus.PAUSED
        elif checkpoint_status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            reconciled_status = checkpoint_status
        else:
            category = (
                FailureCategory.TRANSIENT
                if consumer_disconnected
                else classify_failure(cause)
            )
            if category == FailureCategory.CANCELLED:
                reconciled_status = ExecutionStatus.CANCELLED
            else:
                retryable = self.retry_policy.should_retry(category)
                reconciled_status = (
                    ExecutionStatus.RETRYABLE_FAILED
                    if retryable
                    else ExecutionStatus.FAILED
                )

        category = (
            FailureCategory.TRANSIENT
            if consumer_disconnected
            else classify_failure(cause)
        )
        retryable = reconciled_status == ExecutionStatus.RETRYABLE_FAILED
        error = None
        if reconciled_status in {
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            error_category = (
                FailureCategory.CANCELLED
                if reconciled_status == ExecutionStatus.CANCELLED
                else category
            )
            error = self.error_factory.create(
                error_code=(
                    "STREAM_CONSUMER_DISCONNECTED"
                    if consumer_disconnected
                    else "WORKFLOW_STREAM_INTERRUPTED"
                ),
                error_type=type(cause).__name__,
                message=(
                    "workflow stream consumer disconnected before terminal delivery"
                    if consumer_disconnected
                    else str(cause) or "workflow stream interrupted"
                ),
                component=self.agent_name,
                recoverable=(
                    retryable or reconciled_status == ExecutionStatus.PAUSED
                ),
                retryable=retryable,
                failure_category=error_category,
                failed_node="workflow_stream",
                agent_name=self.agent_name,
                stack_trace=(
                    None
                    if consumer_disconnected
                    else "".join(
                        traceback.format_exception(
                            type(cause),
                            cause,
                            cause.__traceback__,
                        )
                    )
                ),
                details={
                    "consumer_disconnected": consumer_disconnected,
                    "cancel_requested": cancel_requested,
                    "checkpoint_status": (
                        checkpoint_status.value
                        if checkpoint_status is not None
                        else None
                    ),
                    "durable_interrupt_count": len(interrupts),
                    "inspection_error": (
                        type(inspection_error).__name__
                        if inspection_error is not None
                        else None
                    ),
                },
            )

        review_request = None
        if reconciled_status == ExecutionStatus.PAUSED and interrupts:
            raw_request = interrupts[0]
            if isinstance(raw_request, dict) and "value" in raw_request:
                raw_request = raw_request["value"]
            if isinstance(raw_request, dict):
                try:
                    review_request = HumanReviewRequestSchema.model_validate(
                        raw_request
                    )
                except ValueError:
                    review_request = None

        review_status = (
            HumanReviewStatus.PENDING
            if reconciled_status == ExecutionStatus.PAUSED
            else (
                HumanReviewStatus.CANCELLED
                if reconciled_status == ExecutionStatus.CANCELLED
                else HumanReviewStatus.NOT_REQUIRED
            )
        )
        review_decision = (
            HumanReviewDecisionSchema(
                decision=HumanReviewStatus.CANCELLED,
                feedback=str(cause) or "workflow_cancelled",
                metadata={"stream_reconciled": True},
            )
            if review_status == HumanReviewStatus.CANCELLED
            else None
        )
        result = AgentResultSchema(
            result_id=f"result_{task.run_id}_supervisor_stream_reconciled",
            task_id=task.task_id,
            run_id=task.run_id,
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            status=reconciled_status,
            result_type="workflow_stream_reconciliation",
            result={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "workflow_complete": reconciled_status
                in {ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS},
                "sub_agent_results": [],
                "model_usage": self.model_usage_snapshot(),
                "stream_reconciliation": {
                    "consumer_disconnected": consumer_disconnected,
                    "cancel_requested": cancel_requested,
                    "checkpoint_status": (
                        checkpoint_status.value
                        if checkpoint_status is not None
                        else None
                    ),
                    "durable_interrupt_count": len(interrupts),
                },
            },
            error=error,
            error_message=error.message if error is not None else None,
            human_review_required=(
                review_status == HumanReviewStatus.PENDING
            ),
            human_review_status=review_status,
            human_review_request=review_request,
            human_review_decision=review_decision,
            metadata={
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "stream_reconciled": True,
                "consumer_disconnected": consumer_disconnected,
                "cancel_requested": cancel_requested,
            },
        )
        if current_status == ExecutionStatus.RUNNING:
            self.lifecycle.mark_terminal(task, result)
        return result

    def _record_stream_diagnostic(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        result: AgentResultSchema,
        consumer_disconnected: bool,
        cause: BaseException,
    ) -> None:
        try:
            self.trace.record(
                task=task,
                workflow=workflow,
                event_type=(
                    "stream_abandoned"
                    if consumer_disconnected
                    else "stream_interrupted"
                ),
                status=result.status,
                payload={
                    "consumer_disconnected": consumer_disconnected,
                    "error_type": type(cause).__name__,
                    "error_message": str(cause),
                    "reconciled_status": result.status.value,
                },
            )
        except Exception:
            logger.warning(
                "stream diagnostic trace write failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )

    def _finish_stream_traces(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        workflow_span: TraceSpanHandle,
        run_span: TraceSpanHandle,
        result: AgentResultSchema,
        primary_error: BaseException | None,
    ) -> None:
        finish_error: Exception | None = None
        try:
            self.trace.finish_workflow(
                task=task,
                workflow=workflow,
                handle=workflow_span,
                result=result,
            )
        except Exception as exc:
            finish_error = exc
            logger.warning(
                "workflow trace finalization failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )
        try:
            self.trace.finish_run(
                task=task,
                component_name=self.agent_name,
                handle=run_span,
                result=result,
            )
        except Exception as exc:
            if finish_error is None:
                finish_error = exc
            logger.warning(
                "run trace finalization failed: task_id=%s run_id=%s",
                task.task_id,
                task.run_id,
                exc_info=True,
            )
        if finish_error is not None and primary_error is None:
            raise finish_error

    def _stream_with_lifecycle(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        state: GraphStateSchema,
        stream_factory: Callable[[], Iterable[WorkflowStreamEventSchema]],
        run_span: TraceSpanHandle,
        workflow_span: TraceSpanHandle,
        resumed: bool,
        routing_metadata: dict[str, Any] | None = None,
        continuation_payload: dict[str, Any] | None = None,
        cancel_requested: bool = False,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Own task, checkpoint reconciliation and trace closure for a stream."""

        final_result: AgentResultSchema | None = None
        trace_result: AgentResultSchema | None = None
        primary_error: BaseException | None = None
        with activate_span(run_span):
            try:
                self.lifecycle.mark_running(task)
                if resumed:
                    self.trace.record(
                        task=task,
                        workflow=workflow,
                        event_type="workflow_resumed",
                        status=ExecutionStatus.RUNNING,
                        payload=dict(continuation_payload or {}),
                    )
                with activate_span(workflow_span):
                    for stream_event in stream_factory():
                        raw_execution = stream_event.payload.get("result")
                        if raw_execution is None:
                            yield stream_event
                            continue
                        if final_result is not None:
                            raise RuntimeError(
                                "workflow stream emitted multiple terminal results"
                            )
                        execution = WorkflowEngineResultSchema.model_validate(
                            raw_execution
                        )
                        final_result = self._result_from_continuation(
                            task=task,
                            workflow=workflow,
                            state=state,
                            execution=execution,
                            resumed=resumed,
                            routing_metadata=routing_metadata,
                        )
                        self.lifecycle.mark_terminal(task, final_result)
                        trace_result = final_result
                        yield stream_event.model_copy(
                            update={
                                "payload": {
                                    **stream_event.payload,
                                    "workflow_engine_result": raw_execution,
                                    "result": final_result.model_dump(mode="json"),
                                }
                            }
                        )
                if final_result is None:
                    raise RuntimeError(
                        "workflow stream ended without a terminal result"
                    )
            except GeneratorExit as exc:
                primary_error = exc
                if final_result is None:
                    trace_result = self._stream_reconciliation_result(
                        task=task,
                        workflow=workflow,
                        cause=exc,
                        consumer_disconnected=True,
                        cancel_requested=cancel_requested,
                    )
                    self._record_stream_diagnostic(
                        task=task,
                        workflow=workflow,
                        result=trace_result,
                        consumer_disconnected=True,
                        cause=exc,
                    )
                raise
            except BaseException as exc:
                primary_error = exc
                if final_result is None:
                    trace_result = self._stream_reconciliation_result(
                        task=task,
                        workflow=workflow,
                        cause=exc,
                        consumer_disconnected=False,
                        cancel_requested=cancel_requested,
                    )
                    self._record_stream_diagnostic(
                        task=task,
                        workflow=workflow,
                        result=trace_result,
                        consumer_disconnected=False,
                        cause=exc,
                    )
                raise
            finally:
                if trace_result is not None:
                    self._finish_stream_traces(
                        task=task,
                        workflow=workflow,
                        workflow_span=workflow_span,
                        run_span=run_span,
                        result=trace_result,
                        primary_error=primary_error,
                    )

    def _stream_continue_paused(
        self,
        task: TaskSchema,
        *,
        resume_value: dict[str, Any],
        cancel: bool,
        runnable_config: dict[str, Any] | None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream one validated durable continuation through the stable protocol."""

        if not self.workflow_engine.capabilities.stream_events:
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support streaming"
            )
        workflow, inspection, state = self._prepare_paused_continuation(task)
        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "resume": True,
                "decision": resume_value.get("decision"),
                "resume_count": inspection.get("resume_count", 0),
                "stream": True,
            },
        )
        def stream_factory() -> Iterable[WorkflowStreamEventSchema]:
            if cancel:
                cancel_active = getattr(
                    self.workflow_engine,
                    "cancel_active_workers",
                    None,
                )
                if callable(cancel_active):
                    cancel_active(
                        str(
                            resume_value.get("feedback")
                            or "workflow_cancelled"
                        )
                    )
            return self.workflow_engine.stream_events(
                workflow,
                state,
                resume_value=resume_value,
                runnable_config=runnable_config,
            )

        yield from self._stream_with_lifecycle(
            task=task,
            workflow=workflow,
            state=state,
            stream_factory=stream_factory,
            run_span=run_span,
            workflow_span=workflow_span,
            resumed=True,
            continuation_payload={
                "decision": resume_value.get("decision"),
                "feedback_present": bool(resume_value.get("feedback")),
            },
            cancel_requested=cancel,
        )

    def resume(
        self,
        task: TaskSchema,
        *,
        decision: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
    ) -> AgentResultSchema:
        """Resume the original task/run from its durable human-review interrupt."""

        normalized = str(decision).strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError("resume decision must be approved or rejected")
        return self._continue_paused(
            task,
            resume_value={
                "decision": normalized,
                "feedback": feedback,
                "reviewer_id": reviewer_id,
            },
            cancel=False,
        )

    def cancel(
        self,
        task: TaskSchema,
        *,
        reason: str = "workflow_cancelled",
    ) -> AgentResultSchema:
        """Cancel a paused durable run through the rejected-artifact path."""

        return self._continue_paused(
            task,
            resume_value={
                "decision": HumanReviewStatus.CANCELLED.value,
                "feedback": reason,
            },
            cancel=True,
        )

    def stream_resume(
        self,
        task: TaskSchema,
        *,
        decision: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream approval or rejection of the original paused task/run."""

        normalized = str(decision).strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise ValueError("resume decision must be approved or rejected")
        return self._stream_continue_paused(
            task,
            resume_value={
                "decision": normalized,
                "feedback": feedback,
                "reviewer_id": reviewer_id,
            },
            cancel=False,
            runnable_config=runnable_config,
        )

    def stream_cancel(
        self,
        task: TaskSchema,
        *,
        reason: str = "workflow_cancelled",
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream cancellation through the original durable interrupt."""

        return self._stream_continue_paused(
            task,
            resume_value={
                "decision": HumanReviewStatus.CANCELLED.value,
                "feedback": reason,
            },
            cancel=True,
            runnable_config=runnable_config,
        )

    def stream(
        self,
        task: TaskSchema,
        *,
        runnable_config: dict[str, Any] | None = None,
    ) -> Iterable[WorkflowStreamEventSchema]:
        """Stream one initial run through the application-owned event protocol.

        LangGraph's raw stream records remain inside the workflow engine.  The
        final event carries the same ``AgentResultSchema`` boundary returned by
        :meth:`run`, so callers never have to infer completion from log text.
        """

        if not self.workflow_engine.capabilities.stream_events:
            raise RuntimeError(
                f"{self.workflow_engine.engine_name} does not support streaming"
            )
        routing = self.router.route(task)
        workflow = self.catalog.get(routing.task_type)
        context_bundle = self.context_factory.build(task)
        state = GraphStateSchema(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
            user_input=task.user_input,
            tenant_id=task.tenant_id,
            task=task.model_dump(),
            workflow=workflow.model_dump(),
            requirements={
                **task.generation_requirements,
                "project_input": task.project_input,
            },
            context_bundle=context_bundle,
            created_at=task.created_at,
            updated_at=task.updated_at or task.created_at,
            extra={
                "routing": routing.metadata,
                "supervisor_model_response": (
                    routing.model_response.model_dump()
                    if routing.model_response
                    else None
                ),
            },
        )
        run_span = self.trace.start_run(task=task, component_name=self.agent_name)
        workflow_span = self.trace.start_workflow(
            task=task,
            workflow=workflow,
            payload={
                "task": task.model_dump(),
                "workflow": workflow.model_dump(),
                "routing": routing.metadata,
                "stream": True,
            },
        )
        yield from self._stream_with_lifecycle(
            task=task,
            workflow=workflow,
            state=state,
            stream_factory=lambda: self.workflow_engine.stream_events(
                workflow,
                state,
                runnable_config=runnable_config,
            ),
            run_span=run_span,
            workflow_span=workflow_span,
            resumed=False,
            routing_metadata=routing.metadata,
        )

    def run(self, task: TaskSchema) -> AgentResultSchema:
        """Execute the selected workflow and own its lifecycle and trace."""

        workflow: Optional[WorkflowDefinitionSchema] = None
        workflow_span: Optional[TraceSpanHandle] = None
        run_span = self.trace.start_run(task=task, component_name=self.agent_name)

        with activate_span(run_span):
            try:
                routing = self.router.route(task)
                workflow = self.catalog.get(routing.task_type)
                self.lifecycle.mark_running(task)
                context_bundle = self.context_factory.build(task)
                state = GraphStateSchema(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    task_type=task.task_type,
                    user_input=task.user_input,
                    tenant_id=task.tenant_id,
                    task=task.model_dump(),
                    workflow=workflow.model_dump(),
                    requirements={
                        **task.generation_requirements,
                        "project_input": task.project_input,
                    },
                    context_bundle=context_bundle,
                    created_at=task.created_at,
                    updated_at=task.updated_at or task.created_at,
                    extra={
                        "routing": routing.metadata,
                        "supervisor_model_response": (
                            routing.model_response.model_dump()
                            if routing.model_response
                            else None
                        ),
                    },
                )
                workflow_span = self.trace.start_workflow(
                    task=task,
                    workflow=workflow,
                    payload={
                        "task": task.model_dump(),
                        "workflow": workflow.model_dump(),
                        "routing": routing.metadata,
                    },
                )

                with activate_span(workflow_span):
                    execution = self.workflow_engine.execute(workflow, state)

                workflow_complete = bool(
                    execution.metadata.get("workflow_complete")
                )
                failed_results = [
                    item for item in execution.node_results if is_failure(item.status)
                ]
                final_status = execution.status
                final_error = next(
                    (
                        item.error
                        for item in failed_results
                        if item.error is not None
                    ),
                    None,
                )
                if (
                    is_failure(final_status)
                    and not workflow_complete
                    and final_error is None
                ):
                    final_error = self.error_factory.create(
                        error_code="WORKFLOW_INCOMPLETE",
                        error_type="WorkflowStateError",
                        message="Workflow 节点未完整结束。",
                        recoverable=False,
                        retryable=False,
                        failure_category=FailureCategory.VALIDATION,
                        failed_node=state.current_step,
                        component=self.agent_name,
                        agent_name=self.agent_name,
                        step_name=state.current_step,
                    )

                state.status = final_status
                state.context_bundle.runtime.status = final_status
                model_usage = self.model_usage_snapshot()
                review_required = bool(
                    execution.metadata.get("human_review_required", False)
                )
                review_recommended = bool(
                    execution.metadata.get("human_review_recommended", False)
                )
                review_status = HumanReviewStatus(
                    execution.metadata.get(
                        "human_review_status",
                        (
                            HumanReviewStatus.PENDING.value
                            if final_status == ExecutionStatus.PAUSED
                            else HumanReviewStatus.NOT_REQUIRED.value
                        ),
                    )
                )
                raw_review_request = execution.metadata.get(
                    "human_review_request"
                )
                review_request = (
                    HumanReviewRequestSchema.model_validate(raw_review_request)
                    if raw_review_request
                    else None
                )
                raw_review_decision = execution.metadata.get(
                    "human_review_decision"
                )
                review_decision = (
                    HumanReviewDecisionSchema.model_validate(raw_review_decision)
                    if raw_review_decision
                    else None
                )
                result = AgentResultSchema(
                    result_id=f"result_{task.run_id}_supervisor",
                    task_id=task.task_id,
                    run_id=task.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=final_status,
                    result_type="workflow_result",
                    result={
                        "workflow_id": workflow.workflow_id,
                        "workflow_version": workflow.workflow_version,
                        "workflow_complete": workflow_complete,
                        "routing": routing.metadata,
                        "sub_agent_results": [
                            item.model_dump() for item in execution.node_results
                        ],
                        "shared_state": state.model_dump(),
                        "workflow_execution": execution.model_dump(),
                        "generated_draft": state.generated_draft,
                        "rejected_artifact": state.rejected_artifact,
                        "deliverable_final_result": state.deliverable_final_result,
                        "model_usage": model_usage,
                    },
                    error=final_error,
                    error_message=final_error.message if final_error else None,
                    human_review_required=review_required,
                    human_review_recommended=review_recommended,
                    human_review_status=review_status,
                    human_review_request=review_request,
                    human_review_decision=review_decision,
                    metadata={
                        "routing_mode": routing.metadata.get("routing_mode"),
                        "selected_task_type": routing.metadata.get(
                            "selected_task_type"
                        ),
                        "workflow_id": workflow.workflow_id,
                        "workflow_version": workflow.workflow_version,
                        "workflow_complete": workflow_complete,
                        "workflow_engine": execution.engine_name,
                        "graph_state_schema": state.schema_version,
                        "graph_revision": state.graph_revision,
                        "model_usage": model_usage,
                        "human_review_required": review_required,
                        "human_review_recommended": review_recommended,
                        "human_review_status": review_status.value,
                    },
                )
                self.lifecycle.mark_terminal(task, result)
                self.trace.finish_workflow(
                    task=task,
                    workflow=workflow,
                    handle=workflow_span,
                    result=result,
                )
                self.trace.finish_run(
                    task=task,
                    component_name=self.agent_name,
                    handle=run_span,
                    result=result,
                )
                return result
            except Exception as exc:
                model_usage = self.model_usage_snapshot()
                failure_category = classify_failure(exc)
                retryable = self.retry_policy.should_retry(failure_category)
                error = self.error_factory.create(
                    error_code="SUPERVISOR_EXECUTION_FAILED",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    user_visible_message="任务主流程执行失败。",
                    recoverable=retryable,
                    retryable=retryable,
                    failure_category=failure_category,
                    failed_node=self.agent_name,
                    component=self.agent_name,
                    agent_name=self.agent_name,
                    stack_trace=traceback.format_exc(),
                )
                result = AgentResultSchema(
                    result_id=f"result_{task.run_id}_supervisor_failed",
                    task_id=task.task_id,
                    run_id=task.run_id,
                    agent_name=self.agent_name,
                    agent_type=self.agent_type,
                    status=(
                        ExecutionStatus.RETRYABLE_FAILED
                        if retryable
                        else ExecutionStatus.FAILED
                    ),
                    result_type="workflow_result",
                    result={"model_usage": model_usage},
                    error=error,
                    error_message=error.message,
                    human_review_required=False,
                    human_review_recommended=bool(error.requires_human),
                    human_review_status=HumanReviewStatus.NOT_REQUIRED,
                    metadata={"model_usage": model_usage},
                )
                self.lifecycle.mark_terminal(task, result)
                if workflow is not None and workflow_span is not None:
                    self.trace.finish_workflow(
                        task=task,
                        workflow=workflow,
                        handle=workflow_span,
                        result=result,
                    )
                self.trace.finish_run(
                    task=task,
                    component_name=self.agent_name,
                    handle=run_span,
                    result=result,
                )
                return result
