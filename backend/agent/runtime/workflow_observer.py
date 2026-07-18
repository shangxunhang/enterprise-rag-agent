"""Node-level workflow Trace v2 observer."""

from __future__ import annotations

from typing import Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from schemas.agent import AgentResultSchema
from schemas.graph import GraphNodeInputSchema, GraphNodeOutputSchema
from schemas.status import ExecutionStatus


class WorkflowObserver:
    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        self.sink = sink

    def step_started(
        self,
        state: SharedStateSchema,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> TraceSpanHandle:
        handle = new_span(
            run_id=state.run_id,
            span_name=f"agent:{step.target_name}",
            span_kind="internal",
            parent=current_span(),
        )
        if self.sink is not None:
            self.sink.record(
                task_id=state.task_id,
                run_id=state.run_id,
                event_type="agent_started",
                component_type="agent",
                component_name=step.target_name,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                step_id=step.step_id,
                step_name=step.step_name,
                step_order=step.order,
                agent_name=step.target_name,
                caller=workflow.workflow_id,
                callee=step.target_name,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                payload={
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.workflow_version,
                },
                input_summary={
                    "task_type": state.task_type,
                    "step_id": step.step_id,
                    "step_name": step.step_name,
                    "step_order": step.order,
                    "target_name": step.target_name,
                    "current_state_status": getattr(state.status, "value", state.status),
                    "has_evidence_contract": bool(
                        getattr(getattr(state.context_bundle, "evidence", None), "contract", None)
                    ),
                    "graph_state_schema": getattr(state, "schema_version", None),
                    "graph_revision": getattr(state, "graph_revision", None),
                    "node_input_sha256": (
                        node_input.input_sha256 if node_input is not None else None
                    ),
                    "declared_read_keys": (
                        list(node_input.declared_read_keys) if node_input is not None else []
                    ),
                    "missing_read_keys": (
                        list(node_input.missing_keys) if node_input is not None else []
                    ),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
                tags=["trace_v2", "agent", "workflow_step"],
            )
        return handle

    def step_finished(
        self,
        state: SharedStateSchema,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        result: AgentResultSchema,
        handle: TraceSpanHandle,
        node_output: GraphNodeOutputSchema | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
        will_retry: bool = False,
    ) -> None:
        if self.sink is None:
            return
        error = result.error
        self.sink.record(
            task_id=state.task_id,
            run_id=state.run_id,
            event_type="agent_finished",
            component_type="agent",
            component_name=result.agent_name,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            step_id=step.step_id,
            step_name=step.step_name,
            step_order=step.order,
            agent_name=result.agent_name,
            caller=workflow.workflow_id,
            callee=result.agent_name,
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
            payload={"agent_result": result.model_dump(mode="json")},
            output_summary={
                "status": result.status.value,
                "result_type": result.result_type,
                "need_human_review": result.need_human_review,
                "warning_count": len(result.warnings or []),
                "citation_count": len(result.citations or []),
                "error_code": error.error_code if error else None,
                "error_type": error.error_type if error else None,
                "graph_revision": getattr(state, "graph_revision", None),
                "state_delta_sha256": (
                    node_output.state_delta.delta_sha256
                    if node_output is not None
                    else None
                ),
                "changed_path_count": (
                    len(node_output.state_delta.changed_paths)
                    if node_output is not None
                    else 0
                ),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "will_retry": will_retry,
                "commit_decision": (
                    (node_output.metadata or {}).get("commit_decision")
                    if node_output is not None
                    else None
                ),
            },
            tags=["trace_v2", "agent", "workflow_step"],
        )
