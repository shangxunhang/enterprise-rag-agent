"""Agent workflow-step execution."""

from __future__ import annotations

import traceback

from agent.agent_registry import AgentRegistry
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowStepSchema
from core.error_factory import ErrorFactory
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


class AgentStepHandler:
    step_type = "agent"

    def __init__(
        self,
        agent_registry: AgentRegistry,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.error_factory = error_factory or ErrorFactory()

    def execute(
        self,
        step: WorkflowStepSchema,
        state: SharedStateSchema,
    ) -> AgentResultSchema:
        try:
            return self.agent_registry.get(step.target_name).run(state)
        except Exception as exc:
            error = self.error_factory.create(
                error_code="WORKFLOW_AGENT_EXCEPTION",
                error_type=exc.__class__.__name__,
                message=str(exc),
                user_visible_message=f"工作流节点 {step.step_name} 执行失败。",
                recoverable=True,
                retryable=step.max_retries > 0,
                failed_node=step.step_id,
                component="AgentStepHandler",
                agent_name=step.target_name,
                step_name=step.step_name,
                stack_trace=traceback.format_exc(),
            )
            return AgentResultSchema(
                result_id=f"result_{state.run_id}_{step.step_id}_failed",
                task_id=state.task_id,
                run_id=state.run_id,
                agent_name=step.target_name,
                agent_type="sub_agent",
                status=(
                    ExecutionStatus.RETRYABLE_FAILED
                    if error.retryable
                    else ExecutionStatus.FAILED
                ),
                result_type="workflow_step_error",
                result={},
                error=error,
                error_message=error.message,
                need_human_review=True,
            )
