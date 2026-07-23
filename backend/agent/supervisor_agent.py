"""Supervisor orchestration boundary for task routing and workflow execution."""

from __future__ import annotations

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
from model_gateway.model_gateway import ModelGateway
from observability.trace_context import TraceSpanHandle, activate_span
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus, is_failure
from schemas.task import TaskSchema


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
    ) -> None:
        self.catalog = WorkflowCatalog(workflows)
        self.model_gateway = model_gateway
        self.router = WorkflowRouter(
            self.catalog,
            model_gateway=model_gateway,
            model_name=supervisor_model_name,
            enable_llm_routing=enable_llm_routing,
            caller_agent=self.agent_name,
        )
        self.workflow_engine = workflow_engine or LangGraphWorkflowEngine(
            agent_registry=agent_registry,
            run_trace_recorder=run_trace_recorder,
        )
        self.context_factory = ContextBundleFactory()
        self.lifecycle = TaskLifecycleService(task_manager)
        self.trace = WorkflowTraceService(run_trace_recorder)
        self.error_factory = ErrorFactory()
        self._owned_resources = tuple(owned_resources or ())
        self._resource_close_lock = threading.Lock()
        self._resource_close_requested = False
        self._resources_closed = False

    def model_usage_snapshot(self) -> Dict[str, Any]:
        """Return provider-attempt and logical-call usage for this run."""

        if self.model_gateway is None:
            return {}
        snapshot = getattr(self.model_gateway, "usage_snapshot", None)
        return dict(snapshot()) if callable(snapshot) else {}

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

    def _close_owned_resources(self) -> None:
        """Idempotently close resources after the workflow engine is idle."""

        with self._resource_close_lock:
            if self._resources_closed:
                return
            self._resources_closed = True

        seen: set[int] = set()
        for resource in reversed(self._owned_resources):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # Shutdown is best-effort and must not mask the task result.
                    pass

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
                        recoverable=True,
                        retryable=True,
                        failed_node=state.current_step,
                        component=self.agent_name,
                        agent_name=self.agent_name,
                        step_name=state.current_step,
                    )

                state.status = final_status
                state.context_bundle.runtime.status = final_status
                model_usage = self.model_usage_snapshot()
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
                        "final_output": state.final_result,
                        "model_usage": model_usage,
                    },
                    error=final_error,
                    error_message=final_error.message if final_error else None,
                    need_human_review=True,
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
                error = self.error_factory.create(
                    error_code="SUPERVISOR_EXECUTION_FAILED",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    user_visible_message="任务主流程执行失败。",
                    recoverable=True,
                    retryable=False,
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
                    status=ExecutionStatus.FAILED,
                    result_type="workflow_result",
                    result={"model_usage": model_usage},
                    error=error,
                    error_message=error.message,
                    need_human_review=True,
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
