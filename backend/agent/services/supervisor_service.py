"""Supervisor application service independent from the agent protocol shell."""

from __future__ import annotations

import traceback
from typing import Optional

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from contracts.workflow_engine import WorkflowEnginePort
from core.error_factory import ErrorFactory
from observability.trace_context import TraceSpanHandle, activate_span
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus, is_failure
from schemas.task import TaskSchema
from .context_factory import ContextBundleFactory
from .task_lifecycle import TaskLifecycleService
from .workflow_router import WorkflowCatalog, WorkflowRouter
from .workflow_trace import WorkflowTraceService


class SupervisorService:
    def __init__(
        self,
        *,
        catalog: WorkflowCatalog,
        router: WorkflowRouter,
        workflow_engine: WorkflowEnginePort,
        context_factory: ContextBundleFactory | None = None,
        lifecycle: TaskLifecycleService | None = None,
        trace: WorkflowTraceService | None = None,
        error_factory: ErrorFactory | None = None,
        agent_name: str = "SupervisorAgent",
        agent_type: str = "supervisor",
    ) -> None:
        self.catalog = catalog
        self.router = router
        self.workflow_engine = workflow_engine
        self.context_factory = context_factory or ContextBundleFactory()
        self.lifecycle = lifecycle or TaskLifecycleService()
        self.trace = trace or WorkflowTraceService()
        self.error_factory = error_factory or ErrorFactory()
        self.agent_name = agent_name
        self.agent_type = agent_type

    def execute(self, task: TaskSchema) -> AgentResultSchema:
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
                    workflow_execution = self.workflow_engine.execute(workflow, state)
                    sub_results = workflow_execution.node_results

                step_states = state.workflow_step_states
                workflow_complete = bool(
                    workflow_execution.metadata.get("workflow_complete")
                )
                failed_results = [
                    item for item in sub_results if is_failure(item.status)
                ]

                # The workflow engine is the source of truth because it owns
                # retries and explicit failure-recovery routes. A recovered
                # node failure may legitimately produce PARTIAL_SUCCESS even
                # though one terminal node result is failed.
                final_status = workflow_execution.status

                final_error = next(
                    (
                        item.error
                        for item in failed_results
                        if item.error is not None
                    ),
                    None,
                )
                if is_failure(final_status) and not workflow_complete and final_error is None:
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
                            item.model_dump() for item in sub_results
                        ],
                        "shared_state": state.model_dump(),
                        "workflow_execution": workflow_execution.model_dump(),
                        "final_output": state.final_result,
                    },
                    error=final_error,
                    error_message=(
                        final_error.message if final_error else None
                    ),
                    need_human_review=True,
                    metadata={
                        "routing_mode": routing.metadata.get("routing_mode"),
                        "selected_task_type": routing.metadata.get(
                            "selected_task_type"
                        ),
                        "workflow_id": workflow.workflow_id,
                        "workflow_version": workflow.workflow_version,
                        "workflow_complete": workflow_complete,
                        "workflow_engine": workflow_execution.engine_name,
                        "graph_state_schema": state.schema_version,
                        "graph_revision": state.graph_revision,
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
                    result={},
                    error=error,
                    error_message=error.message,
                    need_human_review=True,
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
