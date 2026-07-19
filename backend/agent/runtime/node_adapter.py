"""Isolate Agent execution and project state changes into graph deltas."""

from __future__ import annotations

import traceback

from agent.agent_registry import AgentRegistry
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.graph_state_ops import GraphStateDiffer, StateWriteContractViolation
from agent.runtime.workflow_schema import WorkflowStepSchema
from core.error_factory import ErrorFactory
from core.runtime.clock import Clock, SystemClock
from core.runtime.timing import MonotonicTimer, elapsed_ms
from schemas.agent import AgentResultSchema
from schemas.graph import GraphNodeInputSchema, GraphNodeOutputSchema
from schemas.status import ExecutionStatus


class AgentNodeAdapter:
    """Run an existing Agent on an isolated state copy and emit a delta.

    The canonical graph state is never handed to an Agent. This gives
    the workflow engine an explicit commit boundary and makes late timeout
    results or failed writes unable to mutate canonical state directly.
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        *,
        differ: GraphStateDiffer | None = None,
        clock: Clock | None = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.differ = differ or GraphStateDiffer()
        self.clock = clock or SystemClock()
        self.error_factory = error_factory or ErrorFactory()

    def execute(
        self,
        *,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema,
        state: GraphStateSchema,
    ) -> GraphNodeOutputSchema:
        """Execute one workflow step and return its validated state delta."""
        started_at = self.clock.now_iso()
        timer = MonotonicTimer()
        started = timer.now()
        working_state = state.model_copy(deep=True)
        contract_violation: StateWriteContractViolation | None = None

        try:
            if step.step_type != "agent":
                raise ValueError(
                    f"unsupported workflow step_type={step.step_type!r}; "
                    "the fixed workflow accepts agent steps only"
                )
            result = self.agent_registry.get(step.target_name).run(working_state)
        except Exception as exc:
            error = self.error_factory.create(
                error_code="WORKFLOW_NODE_ADAPTER_EXCEPTION",
                error_type=exc.__class__.__name__,
                message=str(exc),
                user_visible_message=f"工作流节点 {step.step_name} 执行失败。",
                recoverable=True,
                retryable=step.max_retries > 0,
                failed_node=step.step_id,
                component="AgentNodeAdapter",
                agent_name=step.target_name,
                step_name=step.step_name,
                stack_trace=traceback.format_exc(),
            )
            result = AgentResultSchema(
                result_id=f"result_{state.run_id}_{step.step_id}_adapter_failed",
                task_id=state.task_id,
                run_id=state.run_id,
                agent_name=step.target_name,
                agent_type="sub_agent",
                status=(
                    ExecutionStatus.RETRYABLE_FAILED
                    if error.retryable
                    else ExecutionStatus.FAILED
                ),
                result_type="workflow_node_error",
                result={},
                error=error,
                error_message=error.message,
                need_human_review=True,
            )

        try:
            delta = self.differ.diff(
                node_id=step.step_id,
                before=state,
                after=working_state,
                declared_write_keys=step.output_keys,
                declared_write_paths=step.write_paths,
            )
        except StateWriteContractViolation as exc:
            contract_violation = exc
            error = self.error_factory.create(
                error_code="STATE_WRITE_CONTRACT_VIOLATION",
                error_type=exc.__class__.__name__,
                message=str(exc),
                user_visible_message=(
                    f"工作流节点 {step.step_name} 修改了未声明的状态字段。"
                ),
                recoverable=False,
                retryable=False,
                failed_node=step.step_id,
                component="AgentNodeAdapter",
                agent_name=step.target_name,
                step_name=step.step_name,
                details={
                    "violating_paths": list(exc.changed_paths),
                    "allowed_paths": list(exc.allowed_paths),
                },
            )
            result = AgentResultSchema(
                result_id=f"result_{state.run_id}_{step.step_id}_contract_failed",
                task_id=state.task_id,
                run_id=state.run_id,
                agent_name=step.target_name,
                agent_type="sub_agent",
                status=ExecutionStatus.FAILED,
                result_type="state_write_contract_error",
                result={},
                error=error,
                error_message=error.message,
                need_human_review=True,
            )
            # The violating proposal is never committed. Emit a validated
            # empty delta so the engine can record a terminal node revision.
            delta = self.differ.diff(
                node_id=step.step_id,
                before=state,
                after=state.model_copy(deep=True),
                declared_write_keys=step.output_keys,
                declared_write_paths=step.write_paths,
            )

        latency = elapsed_ms(timer, started)
        finished_at = self.clock.now_iso()
        return GraphNodeOutputSchema(
            node_id=step.step_id,
            node_name=step.step_name,
            node_type=step.step_type,
            target_name=step.target_name,
            status=result.status,
            result=result,
            state_delta=delta,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=latency,
            warnings=list(result.warnings or []),
            error=result.error,
            metadata={
                "adapter_id": "agent_node_adapter_v3",
                "node_input_sha256": node_input.input_sha256,
                "declared_read_keys": list(step.input_keys),
                "declared_write_keys": list(step.output_keys),
                "declared_write_paths": list(delta.declared_write_paths),
                "missing_read_keys": list(node_input.missing_keys),
                "isolated_state_execution": True,
                "write_contract_passed": contract_violation is None,
                "write_contract_violations": (
                    list(contract_violation.changed_paths)
                    if contract_violation is not None
                    else []
                ),
            },
        )
