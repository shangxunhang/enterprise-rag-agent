"""Explicit unsupported-step handler used until concrete handlers are registered."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowStepSchema
from core.error_factory import ErrorFactory
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


class UnsupportedStepHandler:
    step_type = "*"

    def __init__(self, error_factory: ErrorFactory | None = None) -> None:
        self.error_factory = error_factory or ErrorFactory()

    def execute(
        self,
        step: WorkflowStepSchema,
        state: SharedStateSchema,
    ) -> AgentResultSchema:
        error = self.error_factory.create(
            error_code="UNSUPPORTED_WORKFLOW_STEP_TYPE",
            error_type="ValueError",
            message=f"Unsupported step_type: {step.step_type}",
            recoverable=False,
            retryable=False,
            failed_node=step.step_id,
            component="WorkflowStepDispatcher",
            step_name=step.step_name,
        )
        return AgentResultSchema(
            result_id=f"result_{state.run_id}_{step.step_id}_unsupported",
            task_id=state.task_id,
            run_id=state.run_id,
            agent_name=step.target_name,
            agent_type="workflow_step",
            status=ExecutionStatus.FAILED,
            result_type="workflow_step_error",
            result={},
            error=error,
            error_message=error.message,
            need_human_review=True,
        )
