"""Task lifecycle port used by application services."""
from __future__ import annotations

from typing import Protocol

from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.task import TaskSchema
from schemas.task_state import TaskStateRecordSchema


class TaskStateManager(Protocol):
    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema: ...
    def mark_running(self, task: TaskSchema, current_step: str = "workflow_started") -> TaskStateRecordSchema: ...
    def mark_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema: ...
    def mark_partial_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema: ...
    def mark_failed(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_failed",
        retryable: bool = False,
        result: AgentResultSchema | None = None,
    ) -> TaskStateRecordSchema: ...
    def get_task(self, task_id: str) -> TaskStateRecordSchema: ...
