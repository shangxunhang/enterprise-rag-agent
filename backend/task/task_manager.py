"""JSONL task lifecycle manager with canonical statuses and structured errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from schemas.task_state import TaskStateEventSchema, TaskStateRecordSchema


class JsonlTaskManager:
    VALID_STATUSES = {status.value for status in ExecutionStatus}

    def __init__(
        self,
        output_dir: str | Path = "data/tasks",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, TaskStateRecordSchema] = {}

    def _now_iso(self) -> str:
        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        return self.id_generator.new_id(prefix)

    def _task_path(self, task_id: str) -> Path:
        return self.output_dir / f"{task_id}_state.jsonl"

    def _write_event(self, event: TaskStateEventSchema) -> None:
        with self._task_path(event.task_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")

    def _append_event(
        self,
        record: TaskStateRecordSchema,
        event_type: str,
        current_status: ExecutionStatus,
        previous_status: Optional[ExecutionStatus] = None,
        current_step: Optional[str] = None,
        result_id: Optional[str] = None,
        error: Optional[ErrorSchema] = None,
    ) -> TaskStateEventSchema:
        event = TaskStateEventSchema(
            event_id=self._new_id("task_event"),
            task_id=record.task_id,
            run_id=record.run_id,
            task_type=record.task_type,
            event_type=event_type,
            previous_status=previous_status,
            current_status=current_status,
            current_step=current_step,
            result_id=result_id,
            error=error,
            error_message=error.message if error else None,
            created_at=self._now_iso(),
        )
        record.events.append(event)
        self._write_event(event)
        return event

    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema:
        if task.task_id in self._records:
            return self._records[task.task_id]
        now = self._now_iso()
        record = TaskStateRecordSchema(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
            tenant_id=task.tenant_id,
            task_name=task.task_name,
            project_name=task.project_name,
            user_input=task.user_input,
            status=ExecutionStatus.PENDING,
            current_step="created",
            created_at=now,
            updated_at=now,
            metadata=task.metadata,
        )
        self._records[task.task_id] = record
        self._append_event(
            record,
            "task_created",
            ExecutionStatus.PENDING,
            current_step="created",
        )
        return record

    def _mark(
        self,
        task: TaskSchema,
        *,
        status: ExecutionStatus,
        event_type: str,
        current_step: str,
        result: Optional[AgentResultSchema] = None,
        error: Optional[ErrorSchema] = None,
    ) -> TaskStateRecordSchema:
        record = self.create_task(task)
        previous = record.status
        now = self._now_iso()
        record.status = status
        record.current_step = current_step
        record.updated_at = now
        if status == ExecutionStatus.RUNNING:
            record.started_at = record.started_at or now
        if status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            record.finished_at = now
        if result:
            record.result_id = result.result_id
        if error:
            record.error = error
            record.error_message = error.message
        self._append_event(
            record,
            event_type,
            status,
            previous_status=previous,
            current_step=current_step,
            result_id=result.result_id if result else None,
            error=error,
        )
        return record

    def mark_running(self, task: TaskSchema, current_step: str = "workflow_started") -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.RUNNING,
            event_type="task_started",
            current_step=current_step,
        )

    def mark_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.SUCCESS,
            event_type="task_succeeded",
            current_step=current_step,
            result=result,
        )

    def mark_partial_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.PARTIAL_SUCCESS,
            event_type="task_partial_succeeded",
            current_step=current_step,
            result=result,
            error=result.error,
        )

    def mark_failed(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_failed",
        retryable: bool = False,
        result: Optional[AgentResultSchema] = None,
    ) -> TaskStateRecordSchema:
        status = ExecutionStatus.RETRYABLE_FAILED if retryable else ExecutionStatus.FAILED
        return self._mark(
            task,
            status=status,
            event_type="task_retryable_failed" if retryable else "task_failed",
            current_step=current_step,
            result=result,
            error=error,
        )

    def get_task(self, task_id: str) -> TaskStateRecordSchema:
        if task_id not in self._records:
            raise KeyError(f"Task state not found: {task_id}")
        return self._records[task_id]

    def exists(self, task_id: str) -> bool:
        return task_id in self._records

    def get_task_path(self, task_id: str) -> Path:
        return self._task_path(task_id)
