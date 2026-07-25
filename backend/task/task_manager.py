"""Event-sourced JSONL task lifecycle manager."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from schemas.task_state import (
    TaskStateEventSchema,
    TaskStateRecordSchema,
    TaskStateSnapshotSchema,
)


class JsonlTaskManager:
    """Own task truth as append-only events and replayable projections."""

    _LOCKS_GUARD = threading.Lock()
    _DIRECTORY_LOCKS: dict[str, threading.RLock] = {}

    _ALLOWED: dict[ExecutionStatus, set[ExecutionStatus]] = {
        ExecutionStatus.PENDING: {
            ExecutionStatus.RUNNING,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        },
        ExecutionStatus.RUNNING: {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        },
        ExecutionStatus.CREATED: {ExecutionStatus.PENDING},
        ExecutionStatus.QUEUED: {ExecutionStatus.PENDING, ExecutionStatus.RUNNING},
        ExecutionStatus.SUCCESS: set(),
        ExecutionStatus.PARTIAL_SUCCESS: set(),
        ExecutionStatus.FAILED: set(),
        ExecutionStatus.RETRYABLE_FAILED: set(),
        ExecutionStatus.CANCELLED: set(),
        ExecutionStatus.SKIPPED: set(),
    }

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
        lock_key = str(self.output_dir.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._DIRECTORY_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )
        self._records: Dict[str, TaskStateRecordSchema] = {}

    def _now_iso(self) -> str:
        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        return self.id_generator.new_id(prefix)

    def _task_path(self, task_id: str) -> Path:
        if not task_id or Path(task_id).name != task_id or any(
            separator in task_id for separator in ("/", "\\")
        ):
            raise ValueError("task_id must be a non-empty path-safe identifier")
        return self.output_dir / f"{task_id}_state.jsonl"

    def _write_event(self, event: TaskStateEventSchema) -> None:
        serialized = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        CrashSafeJsonlEventLog(
            self._task_path(event.task_id)
        ).append_serialized(serialized)

    @staticmethod
    def _record_from_events(
        events: list[TaskStateEventSchema],
    ) -> TaskStateRecordSchema:
        if not events:
            raise ValueError("task event log cannot be empty")
        snapshot = events[-1].snapshot
        return TaskStateRecordSchema(
            **snapshot.model_dump(mode="python"),
            events=events,
        )

    def _read_task_events(self, path: Path) -> list[TaskStateEventSchema]:
        events: list[TaskStateEventSchema] = []
        for line_number, line in enumerate(
            CrashSafeJsonlEventLog(path).read_complete_lines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                event = TaskStateEventSchema.model_validate_json(line, strict=True)
            except Exception as exc:
                raise ValueError(
                    f"invalid task event at {path}:{line_number}"
                ) from exc
            expected_revision = len(events)
            if event.revision != expected_revision:
                raise ValueError(
                    "task event revision is not contiguous: "
                    f"expected={expected_revision} actual={event.revision}"
                )
            if event.snapshot.revision != event.revision:
                raise ValueError("task event snapshot revision mismatch")
            if event.snapshot.status != event.current_status:
                raise ValueError("task event snapshot status mismatch")
            if events:
                previous = events[-1]
                if (
                    event.task_id != previous.task_id
                    or event.run_id != previous.run_id
                    or event.task_type != previous.task_type
                    or (
                        event.snapshot.canonical_task_request_sha256
                        != previous.snapshot.canonical_task_request_sha256
                    )
                    or event.previous_status != previous.current_status
                ):
                    raise ValueError("task event identity/previous status mismatch")
                if event.current_status not in self._ALLOWED.get(
                    previous.current_status,
                    set(),
                ):
                    raise ValueError(
                        "illegal persisted task transition: "
                        f"{previous.current_status.value} -> {event.current_status.value}"
                    )
            elif (
                event.revision != 0
                or event.previous_status is not None
                or event.current_status != ExecutionStatus.PENDING
            ):
                raise ValueError("task event log must start at revision 0 pending")
            events.append(event)
        if not events:
            raise ValueError(f"task event log is empty: {path}")
        return events

    def _load_existing_task(self, task_id: str) -> TaskStateRecordSchema | None:
        """Replay only the requested aggregate, not every historical task file."""

        path = self._task_path(task_id)
        if not path.is_file():
            return None
        committed_lines = CrashSafeJsonlEventLog(path).read_complete_lines()
        if not any(line.strip() for line in committed_lines):
            return None
        record = self._record_from_events(self._read_task_events(path))
        if record.task_id != task_id:
            raise ValueError(
                f"task event log identity mismatch: expected={task_id} "
                f"actual={record.task_id}"
            )
        self._records[task_id] = record
        return record

    @staticmethod
    def _snapshot_from_record(
        record: TaskStateRecordSchema,
    ) -> TaskStateSnapshotSchema:
        payload = record.model_dump(mode="python", exclude={"events"})
        return TaskStateSnapshotSchema.model_validate(payload, strict=True)

    def _commit_record(
        self,
        record: TaskStateRecordSchema,
        *,
        event_type: str,
        previous_status: ExecutionStatus | None,
    ) -> TaskStateRecordSchema:
        snapshot = self._snapshot_from_record(record)
        event = TaskStateEventSchema(
            event_id=self._new_id("task_event"),
            task_id=record.task_id,
            run_id=record.run_id,
            task_type=record.task_type,
            revision=record.revision,
            event_type=event_type,
            previous_status=previous_status,
            current_status=record.status,
            current_step=record.current_step,
            result_id=record.result_id,
            error=record.error,
            error_message=record.error_message,
            created_at=record.updated_at,
            snapshot=snapshot,
        )
        # Event append is the commit point. Memory is updated only afterward.
        self._write_event(event)
        committed = record.model_copy(
            update={"events": [*record.events, event]},
            deep=True,
        )
        self._records[record.task_id] = committed
        return committed

    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema:
        with self._lock:
            request_sha256 = task.canonical_request_sha256()
            existing = self._load_existing_task(task.task_id)
            if existing is not None:
                if not existing.canonical_task_request_sha256:
                    raise ValueError(
                        "duplicate task_id has no canonical immutable request "
                        f"identity: task_id={task.task_id}"
                    )
                if existing.canonical_task_request_sha256 != request_sha256:
                    raise ValueError(
                        "duplicate task_id immutable request mismatch: "
                        f"task_id={task.task_id}"
                    )
                return existing
            now = self._now_iso()
            record = TaskStateRecordSchema(
                task_id=task.task_id,
                run_id=task.run_id,
                task_type=task.task_type,
                tenant_id=task.tenant_id,
                task_name=task.task_name,
                project_name=task.project_name,
                user_input=task.user_input,
                canonical_task_request_sha256=request_sha256,
                status=ExecutionStatus.PENDING,
                current_step="created",
                revision=0,
                created_at=now,
                updated_at=now,
                metadata=task.metadata,
            )
            return self._commit_record(
                record,
                event_type="task_created",
                previous_status=None,
            )

    def _mark(
        self,
        task: TaskSchema,
        *,
        status: ExecutionStatus,
        event_type: str,
        current_step: str,
        result: Optional[AgentResultSchema] = None,
        error: Optional[ErrorSchema] = None,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        with self._lock:
            current = self.create_task(task)
            if current.status == status:
                return current
            if expected_revision is not None and current.revision != expected_revision:
                raise ValueError(
                    "task revision conflict: "
                    f"expected={expected_revision} actual={current.revision}"
                )
            if status not in self._ALLOWED.get(current.status, set()):
                raise ValueError(
                    f"illegal task transition: {current.status.value} -> {status.value}"
                )
            now = self._now_iso()
            updates = {
                "status": status,
                "current_step": current_step,
                "updated_at": now,
                "revision": current.revision + 1,
            }
            if status == ExecutionStatus.RUNNING:
                updates["started_at"] = current.started_at or now
            if status not in {
                ExecutionStatus.CREATED,
                ExecutionStatus.QUEUED,
                ExecutionStatus.PENDING,
                ExecutionStatus.RUNNING,
            }:
                updates["finished_at"] = now
            if result is not None:
                updates["result_id"] = result.result_id
            if error is not None:
                updates["error"] = error
                updates["error_message"] = error.message
            updated = current.model_copy(update=updates, deep=True)
            return self._commit_record(
                updated,
                event_type=event_type,
                previous_status=current.status,
            )

    def mark_running(
        self,
        task: TaskSchema,
        current_step: str = "workflow_started",
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.RUNNING,
            event_type="task_started",
            current_step=current_step,
            expected_revision=expected_revision,
        )

    def mark_success(
        self,
        task: TaskSchema,
        result: AgentResultSchema,
        current_step: str = "workflow_finished",
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.SUCCESS,
            event_type="task_succeeded",
            current_step=current_step,
            result=result,
            expected_revision=expected_revision,
        )

    def mark_partial_success(
        self,
        task: TaskSchema,
        result: AgentResultSchema,
        current_step: str = "workflow_finished",
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.PARTIAL_SUCCESS,
            event_type="task_partial_succeeded",
            current_step=current_step,
            result=result,
            error=result.error,
            expected_revision=expected_revision,
        )

    def mark_failed(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_failed",
        retryable: bool = False,
        result: Optional[AgentResultSchema] = None,
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        status = (
            ExecutionStatus.RETRYABLE_FAILED
            if retryable
            else ExecutionStatus.FAILED
        )
        return self._mark(
            task,
            status=status,
            event_type=(
                "task_retryable_failed" if retryable else "task_failed"
            ),
            current_step=current_step,
            result=result,
            error=error,
            expected_revision=expected_revision,
        )

    def mark_cancelled(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_cancelled",
        result: Optional[AgentResultSchema] = None,
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.CANCELLED,
            event_type="task_cancelled",
            current_step=current_step,
            result=result,
            error=error,
            expected_revision=expected_revision,
        )

    def get_task(self, task_id: str) -> TaskStateRecordSchema:
        with self._lock:
            record = self._load_existing_task(task_id)
            if record is None:
                raise KeyError(f"Task state not found: {task_id}")
            return record.model_copy(deep=True)

    def exists(self, task_id: str) -> bool:
        with self._lock:
            return self._load_existing_task(task_id) is not None

    def get_task_path(self, task_id: str) -> Path:
        return self._task_path(task_id)
