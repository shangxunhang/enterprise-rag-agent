"""Workflow/run-level Trace v2 service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from observability.trace_summary import bounded_summary
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


class WorkflowTraceService:
    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        self.sink = sink

    def start_run(self, *, task: TaskSchema, component_name: str) -> TraceSpanHandle:
        handle = new_span(
            run_id=task.run_id,
            span_name=f"run:{component_name}",
            span_kind="server",
            parent=current_span(),
        )
        if self.sink is not None:
            self.sink.record(
                task_id=task.task_id,
                run_id=task.run_id,
                event_type="run_started",
                component_type="runtime",
                component_name=component_name,
                agent_name=component_name,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                input_summary={
                    "task_id": task.task_id,
                    "run_id": task.run_id,
                    "task_type": task.task_type,
                    "user_input_chars": len(task.user_input or ""),
                    "project_input_present": bool(task.project_input),
                },
                tags=["trace_v2", "run"],
            )
        return handle

    def finish_run(
        self,
        *,
        task: TaskSchema,
        component_name: str,
        handle: TraceSpanHandle,
        result: AgentResultSchema,
    ) -> None:
        if self.sink is None:
            return
        error = result.error
        self.sink.record(
            task_id=task.task_id,
            run_id=task.run_id,
            event_type="run_finished",
            component_type="runtime",
            component_name=component_name,
            agent_name=component_name,
            status=result.status.value,
            error_message=(error.message if error else result.error_message),
            phase=("error" if error else "end"),
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            finished_at=None,
            latency_ms=handle.latency_ms(),
            output_summary={
                "status": result.status.value,
                "result_type": result.result_type,
                "need_human_review": result.need_human_review,
                "error_code": error.error_code if error else None,
                "error_type": error.error_type if error else None,
            },
            tags=["trace_v2", "run"],
        )

    def start_workflow(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        payload: Dict[str, Any],
    ) -> TraceSpanHandle:
        handle = new_span(
            run_id=task.run_id,
            span_name=f"workflow:{workflow.workflow_id}",
            span_kind="internal",
            parent=current_span(),
        )
        if self.sink is not None:
            self.sink.record(
                task_id=task.task_id,
                run_id=task.run_id,
                event_type="workflow_started",
                component_type="workflow",
                component_name=workflow.workflow_id,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                payload=payload,
                input_summary={
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.workflow_version,
                    "step_count": len(workflow.steps),
                    "task_type": task.task_type,
                    "routing": bounded_summary(payload.get("routing") or {}),
                },
                tags=["trace_v2", "workflow"],
            )
        return handle

    def finish_workflow(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        handle: TraceSpanHandle,
        result: AgentResultSchema,
    ) -> None:
        if self.sink is None:
            return
        error = result.error
        sub_results = (result.result or {}).get("sub_agent_results") or []
        self.sink.record(
            task_id=task.task_id,
            run_id=task.run_id,
            event_type="workflow_finished",
            component_type="workflow",
            component_name=workflow.workflow_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            status=result.status.value,
            error_message=(error.message if error else result.error_message),
            phase=("error" if error else "end"),
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            latency_ms=handle.latency_ms(),
            output_summary={
                "status": result.status.value,
                "workflow_complete": (result.result or {}).get("workflow_complete"),
                "sub_agent_count": len(sub_results),
                "failed_sub_agent_count": sum(
                    1
                    for item in sub_results
                    if str(item.get("status") or "").lower() not in {"success", "partial_success", "executionstatus.success", "executionstatus.partial_success"}
                ),
                "error_code": error.error_code if error else None,
            },
            tags=["trace_v2", "workflow"],
        )

    def record(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        event_type: str,
        status: ExecutionStatus,
        payload: Dict[str, Any],
    ) -> None:
        """Compatibility event API retained for callers outside Step 13."""
        if self.sink is None:
            return
        self.sink.record(
            task_id=task.task_id,
            run_id=task.run_id,
            event_type=event_type,
            component_type="workflow",
            component_name=workflow.workflow_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            payload=payload,
            status=status.value,
            tags=["trace_v2", "compatibility_event"],
        )
