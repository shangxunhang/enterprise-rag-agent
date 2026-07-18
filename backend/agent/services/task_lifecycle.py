"""Task-state lifecycle port adapter."""

from __future__ import annotations

from typing import Optional

from contracts.task_state import TaskStateManager
from core.error_factory import ErrorFactory
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


class TaskLifecycleService:
    def __init__(
        self,
        task_manager: Optional[TaskStateManager] = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        self.task_manager = task_manager
        self.error_factory = error_factory or ErrorFactory()

    def mark_running(self, task: TaskSchema) -> None:
        if self.task_manager is not None:
            self.task_manager.mark_running(task, current_step="workflow_started")

    def mark_terminal(self, task: TaskSchema, result: AgentResultSchema) -> None:
        if self.task_manager is None:
            return
        if result.status == ExecutionStatus.SUCCESS:
            self.task_manager.mark_success(task, result)
        elif result.status == ExecutionStatus.PARTIAL_SUCCESS:
            self.task_manager.mark_partial_success(task, result)
        else:
            error = result.error or self.error_factory.create(
                error_code="WORKFLOW_FAILED",
                error_type="WorkflowFailure",
                message=result.error_message or "workflow failed",
                component="TaskLifecycleService",
                failed_node="SupervisorAgent",
            )
            self.task_manager.mark_failed(
                task,
                error=error,
                retryable=result.status == ExecutionStatus.RETRYABLE_FAILED,
                result=result,
            )
