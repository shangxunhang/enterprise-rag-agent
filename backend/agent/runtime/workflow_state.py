"""Workflow state transitions isolated from execution logic."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from agent.runtime.workflow_schema import WorkflowStepSchema
from core.runtime.clock import Clock, SystemClock
from schemas.agent import AgentResultSchema
from schemas.context import WorkflowStepStateSchema
from schemas.status import ExecutionStatus


class WorkflowStateController:
    def __init__(
        self,
        *,
        writer: SharedStateWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.writer = writer or SharedStateWriter()
        self.clock = clock or SystemClock()

    def start_workflow(self, state: SharedStateSchema) -> None:
        state.status = ExecutionStatus.RUNNING
        state.context_bundle.runtime.status = ExecutionStatus.RUNNING

    def start_step(
        self,
        state: SharedStateSchema,
        step: WorkflowStepSchema,
    ) -> WorkflowStepStateSchema:
        step_state = WorkflowStepStateSchema(
            step_id=step.step_id,
            step_name=step.step_name,
            target_name=step.target_name,
            status=ExecutionStatus.RUNNING,
            started_at=self.clock.now_iso(),
            attempt=1,
        )
        self.writer.set_step_state(state, step_state)
        self.writer.set_current_step(state, step.step_name)
        return step_state

    def finish_step(
        self,
        state: SharedStateSchema,
        step_state: WorkflowStepStateSchema,
        result: AgentResultSchema,
    ) -> None:
        self.writer.add_agent_result(state, result)
        step_state.status = result.status
        step_state.finished_at = self.clock.now_iso()
        step_state.error = result.error
        self.writer.set_step_state(state, step_state)
        if result.error is not None:
            self.writer.add_error(state, result.error)

    def finish_workflow(
        self,
        state: SharedStateSchema,
        status: ExecutionStatus,
    ) -> None:
        state.status = status
        state.context_bundle.runtime.status = status
