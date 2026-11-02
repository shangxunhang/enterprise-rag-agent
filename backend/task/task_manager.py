"""Event-sourced JSONL task lifecycle manager."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional
from uuid import uuid4

from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from core.persistence.interprocess_lock import InterProcessFileLock
from core.persistence.safe_paths import SafePathResolver
from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from schemas.task_state import (
    TaskAuthorizationIdentitySchema,
    TaskStateEventSchema,
    TaskStateRecordSchema,
    TaskStateSnapshotSchema,
)


class TaskAuthorizationIdentityMigrationStatus(str, Enum):
    """Closed set of privileged offline identity migration outcomes."""

    WOULD_CREATE = "would_create"
    CREATED = "created"
    ALREADY_CURRENT = "already_current"
    CONFLICT = "conflict"
    INVALID_TASK_LOG = "invalid_task_log"
    MISSING_TASK_LOG = "missing_task_log"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskAuthorizationIdentityMigrationResult:
    """One task outcome from the privileged offline migration entry point."""

    task_id: str
    status: TaskAuthorizationIdentityMigrationStatus
    detail: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"task_id": self.task_id, "status": self.status.value}
        if self.detail:
            payload["detail"] = self.detail
        return payload


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
            ExecutionStatus.RUNNING,
            ExecutionStatus.PAUSED,
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        },
        ExecutionStatus.PAUSED: {
            ExecutionStatus.RUNNING,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        },
        ExecutionStatus.CREATED: {ExecutionStatus.PENDING},
        ExecutionStatus.QUEUED: {ExecutionStatus.PENDING, ExecutionStatus.RUNNING},
        ExecutionStatus.SUCCESS: set(),
        ExecutionStatus.PARTIAL_SUCCESS: set(),
        ExecutionStatus.FAILED: {ExecutionStatus.RUNNING},
        ExecutionStatus.RETRYABLE_FAILED: {ExecutionStatus.RUNNING},
        ExecutionStatus.CANCELLED: set(),
        ExecutionStatus.SKIPPED: set(),
    }
    _RECOVERY_EVENT_TYPES = {"task_recovery_started", "task_retry_started"}

    def __init__(
        self,
        output_dir: str | Path = "data/tasks",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._path_resolver = SafePathResolver(output_dir)
        self.output_dir = self._path_resolver.root
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
        return self._path_resolver.path_for_identifier(
            task_id,
            field_name="task_id",
            suffix="_state.jsonl",
        )

    def _task_identity_path(self, task_id: str) -> Path:
        return self._path_resolver.path_for_identifier(
            task_id,
            field_name="task_id",
            suffix="_identity.json",
        )

    def _run_identity_path(self, run_id: str) -> Path:
        """Return the durable run-id uniqueness projection in the task-state domain."""

        return self._path_resolver.path_for_identifier(
            run_id,
            field_name="run_id",
            suffix="_run_identity.json",
        )

    def _task_lock_path(self, task_id: str) -> Path:
        return self._path_resolver.path_for_identifier(
            task_id,
            field_name="task_id",
            suffix="_state.lock",
        )

    def _task_process_lock(self, task_id: str) -> InterProcessFileLock:
        return InterProcessFileLock(self._task_lock_path(task_id))

    @staticmethod
    def _identity_from_task(task: TaskSchema) -> TaskAuthorizationIdentitySchema:
        return TaskAuthorizationIdentitySchema(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
            tenant_id=task.tenant_id,
            canonical_task_request_sha256=task.canonical_request_sha256(),
        )

    @staticmethod
    def _identity_from_record(
        record: TaskStateRecordSchema,
    ) -> TaskAuthorizationIdentitySchema:
        request_hash = str(record.canonical_task_request_sha256 or "").strip()
        if not request_hash:
            raise ValueError("task state immutable identity is incomplete")
        return TaskAuthorizationIdentitySchema(
            task_id=record.task_id,
            run_id=record.run_id,
            task_type=record.task_type,
            tenant_id=record.tenant_id,
            canonical_task_request_sha256=request_hash,
        )

    def _read_authorization_identity(
        self,
        task_id: str,
    ) -> TaskAuthorizationIdentitySchema:
        """Read only the immutable identity file, never the Task event log."""

        path = self._task_identity_path(task_id)
        if not path.is_file():
            raise KeyError("task authorization identity is unavailable")
        try:
            identity = TaskAuthorizationIdentitySchema.model_validate_json(
                path.read_text(encoding="utf-8"),
                strict=True,
            )
        except Exception as exc:
            raise ValueError("task authorization identity is invalid") from exc
        if identity.task_id != task_id:
            raise ValueError("task authorization identity path mismatch")
        return identity

    def _read_run_identity(
        self,
        run_id: str,
    ) -> TaskAuthorizationIdentitySchema:
        path = self._run_identity_path(run_id)
        if not path.is_file():
            raise KeyError("run authorization identity is unavailable")
        try:
            identity = TaskAuthorizationIdentitySchema.model_validate_json(
                path.read_text(encoding="utf-8"),
                strict=True,
            )
        except Exception as exc:
            raise ValueError("run authorization identity is invalid") from exc
        if identity.run_id != run_id:
            raise ValueError("run authorization identity path mismatch")
        return identity

    def _ensure_run_identity(
        self,
        identity: TaskAuthorizationIdentitySchema,
    ) -> TaskAuthorizationIdentitySchema:
        """Atomically claim one immutable task/request identity for ``run_id``."""

        target = self._run_identity_path(identity.run_id)
        if target.exists():
            persisted = self._read_run_identity(identity.run_id)
            if persisted != identity:
                raise ValueError("run_id immutable task/request identity conflict")
            return persisted

        serialized = (
            json.dumps(
                identity.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink()

        persisted = self._read_run_identity(identity.run_id)
        if persisted != identity:
            raise ValueError("run_id immutable task/request identity conflict")
        return persisted

    def _ensure_authorization_identity(
        self,
        identity: TaskAuthorizationIdentitySchema,
    ) -> TaskAuthorizationIdentitySchema:
        """Atomically create an immutable projection or verify exact equality."""

        target = self._task_identity_path(identity.task_id)
        if target.exists():
            persisted = self._read_authorization_identity(identity.task_id)
            if persisted != identity:
                raise ValueError("task authorization identity conflict")
            return persisted

        serialized = (
            json.dumps(
                identity.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # A same-filesystem hard link publishes the already-fsynced
                # bytes atomically and fails rather than replacing a winner.
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink()

        persisted = self._read_authorization_identity(identity.task_id)
        if persisted != identity:
            raise ValueError("task authorization identity conflict")
        return persisted

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

    @staticmethod
    def _read_task_event_lines(
        path: Path,
        *,
        recover_torn_write: bool,
    ) -> list[str]:
        if recover_torn_write:
            return CrashSafeJsonlEventLog(path).read_complete_lines()

        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError(
                f"task event log has an incomplete trailing record: {path}"
            )
        try:
            return data.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"invalid UTF-8 in committed task history: {path}"
            ) from exc

    def _read_task_events(
        self,
        path: Path,
        *,
        recover_torn_write: bool = True,
    ) -> list[TaskStateEventSchema]:
        events: list[TaskStateEventSchema] = []
        for line_number, line in enumerate(
            self._read_task_event_lines(
                path,
                recover_torn_write=recover_torn_write,
            ),
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
                    or event.snapshot.tenant_id != previous.snapshot.tenant_id
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
                recovery_transition = (
                    event.current_status == ExecutionStatus.RUNNING
                    and previous.current_status
                    in {
                        ExecutionStatus.RUNNING,
                        ExecutionStatus.FAILED,
                        ExecutionStatus.RETRYABLE_FAILED,
                    }
                )
                if recovery_transition:
                    if event.event_type not in self._RECOVERY_EVENT_TYPES:
                        raise ValueError(
                            "persisted transition into recovery requires a "
                            "dedicated recovery event"
                        )
                    required = {
                        "recovery_attempt_number",
                        "recovery_reason",
                        "previous_status",
                        "previous_revision",
                        "new_revision",
                        "canonical_immutable_request_hash",
                        "recovery_policy",
                        "max_recovery_count",
                        "request_timestamp",
                    }
                    if not required <= set(event.metadata):
                        raise ValueError("recovery event metadata is incomplete")
                    previous_attempt = int(
                        previous.snapshot.metadata.get(
                            "recovery_attempt_count",
                            0,
                        )
                    )
                    if (
                        event.metadata["previous_status"]
                        != previous.current_status.value
                        or event.metadata["previous_revision"]
                        != previous.revision
                        or event.metadata["new_revision"] != event.revision
                        or event.metadata["recovery_attempt_number"]
                        != previous_attempt + 1
                        or event.snapshot.metadata.get(
                            "recovery_attempt_count"
                        )
                        != previous_attempt + 1
                        or event.metadata["canonical_immutable_request_hash"]
                        != previous.snapshot.canonical_task_request_sha256
                    ):
                        raise ValueError("recovery event continuity mismatch")
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

    @staticmethod
    def _validate_migration_event_identity(
        task_id: str,
        events: list[TaskStateEventSchema],
    ) -> None:
        """Require one complete immutable identity across every event."""

        expected: tuple[str, str, str, str, str] | None = None
        for event in events:
            snapshot = event.snapshot
            if (
                event.task_id != snapshot.task_id
                or event.run_id != snapshot.run_id
                or event.task_type != snapshot.task_type
            ):
                raise ValueError(
                    "task event top-level identity does not match its snapshot"
                )
            request_hash = str(
                snapshot.canonical_task_request_sha256 or ""
            )
            if not request_hash.strip():
                raise ValueError(
                    "task event canonical request hash is missing"
                )
            current = (
                snapshot.task_id,
                snapshot.run_id,
                snapshot.task_type,
                snapshot.tenant_id,
                request_hash,
            )
            if current[0] != task_id:
                raise ValueError("task event identity does not match its path")
            if expected is None:
                expected = current
            elif current != expected:
                raise ValueError(
                    "task event immutable identity is not continuous"
                )

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
        event_metadata: dict | None = None,
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
            metadata=dict(event_metadata or {}),
        )
        # Event append is the commit point. Memory is updated only afterward.
        self._write_event(event)
        committed = record.model_copy(
            update={"events": [*record.events, event]},
            deep=True,
        )
        self._records[record.task_id] = committed
        return committed

    def _create_task_locked(self, task: TaskSchema) -> TaskStateRecordSchema:
        """Create/idempotently load while thread and process locks are held."""

        requested_identity = self._identity_from_task(task)
        existing = self._load_existing_task(task.task_id)
        if existing is not None:
            persisted_identity = self._identity_from_record(existing)
            if persisted_identity != requested_identity:
                raise ValueError(
                    "duplicate task_id immutable request mismatch: "
                    "identity projection differs; "
                    f"task_id={task.task_id}"
                )
            self._ensure_authorization_identity(persisted_identity)
            self._ensure_run_identity(persisted_identity)
            return existing

        self._ensure_run_identity(requested_identity)
        self._ensure_authorization_identity(requested_identity)
        now = self._now_iso()
        record = TaskStateRecordSchema(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
            tenant_id=task.tenant_id,
            task_name=task.task_name,
            project_name=task.project_name,
            user_input=task.user_input,
            canonical_task_request_sha256=(
                requested_identity.canonical_task_request_sha256
            ),
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

    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema:
        with self._lock:
            with self._task_process_lock(task.task_id):
                return self._create_task_locked(task)

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
            with self._task_process_lock(task.task_id):
                current = self._create_task_locked(task)
                return self._mark_locked(
                    current=current,
                    status=status,
                    event_type=event_type,
                    current_step=current_step,
                    result=result,
                    error=error,
                    expected_revision=expected_revision,
                )

    def _mark_locked(
        self,
        *,
        current: TaskStateRecordSchema,
        status: ExecutionStatus,
        event_type: str,
        current_step: str,
        result: Optional[AgentResultSchema],
        error: Optional[ErrorSchema],
        expected_revision: int | None,
    ) -> TaskStateRecordSchema:
        """Apply a normal transition while both persistence locks are held."""

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
        if (
            status == ExecutionStatus.RUNNING
            and current.status
            in {ExecutionStatus.FAILED, ExecutionStatus.RETRYABLE_FAILED}
        ):
            raise ValueError(
                "failed tasks require the dedicated recovery entry point"
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
            ExecutionStatus.PAUSED,
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

    def mark_recovery_started(
        self,
        task: TaskSchema,
        *,
        expected_revision: int,
        recovery_reason: str,
        recovery_policy: str,
        max_recovery_count: int,
        retry_only: bool = False,
    ) -> TaskStateRecordSchema:
        """CAS one eligible durable run into RUNNING with an audit event."""

        reason = str(recovery_reason or "").strip()
        policy = str(recovery_policy or "").strip()
        if not reason or not policy:
            raise ValueError("recovery reason and policy are required")
        if max_recovery_count < 1:
            raise ValueError("max recovery count must be positive")

        with self._lock:
            with self._task_process_lock(task.task_id):
                current = self._create_task_locked(task)
                return self._mark_recovery_started_locked(
                    task,
                    current=current,
                    expected_revision=expected_revision,
                    reason=reason,
                    policy=policy,
                    max_recovery_count=max_recovery_count,
                    retry_only=retry_only,
                )

    def _mark_recovery_started_locked(
        self,
        task: TaskSchema,
        *,
        current: TaskStateRecordSchema,
        expected_revision: int,
        reason: str,
        policy: str,
        max_recovery_count: int,
        retry_only: bool,
    ) -> TaskStateRecordSchema:
        """Perform the complete recovery CAS while the OS lock is held."""

        if current.revision != expected_revision:
            raise ValueError(
                "task revision conflict: "
                f"expected={expected_revision} actual={current.revision}"
            )
        if retry_only and current.status != ExecutionStatus.RETRYABLE_FAILED:
            raise ValueError("retry_run requires RETRYABLE_FAILED status")
        if current.status not in {
            ExecutionStatus.RUNNING,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
        }:
            raise ValueError(
                f"task status is not recoverable: {current.status.value}"
            )
        if current.status == ExecutionStatus.FAILED:
            non_recoverable_categories = {
                "business_quality",
                "evidence_insufficient",
                "validation",
                "configuration",
                "cancelled",
            }
            if (
                current.error is None
                or not current.error.recoverable
                or current.error.failure_category.value
                in non_recoverable_categories
            ):
                raise ValueError("failed task is not classified as recoverable")

        previous_attempt = int(
            current.metadata.get("recovery_attempt_count", 0)
        )
        recovery_attempt = previous_attempt + 1
        if recovery_attempt > max_recovery_count:
            raise ValueError("maximum recovery count exceeded")

        now = self._now_iso()
        next_revision = current.revision + 1
        request_hash = str(current.canonical_task_request_sha256 or "")
        if not request_hash or request_hash != task.canonical_request_sha256():
            raise ValueError("canonical immutable request hash mismatch")
        event_metadata = {
            "recovery_attempt_number": recovery_attempt,
            "recovery_reason": reason,
            "previous_status": current.status.value,
            "previous_revision": current.revision,
            "new_revision": next_revision,
            "canonical_immutable_request_hash": request_hash,
            "recovery_policy": policy,
            "max_recovery_count": max_recovery_count,
            "request_timestamp": now,
        }
        updated = current.model_copy(
            update={
                "status": ExecutionStatus.RUNNING,
                "current_step": "workflow_recovery_started",
                "updated_at": now,
                "finished_at": None,
                "result_id": None,
                "error": None,
                "error_message": None,
                "revision": next_revision,
                "metadata": {
                    **dict(current.metadata),
                    "recovery_attempt_count": recovery_attempt,
                    "last_recovery": dict(event_metadata),
                },
            },
            deep=True,
        )
        event_type = (
            "task_retry_started" if retry_only else "task_recovery_started"
        )
        committed = self._commit_record(
            updated,
            event_type=event_type,
            previous_status=current.status,
            event_metadata=event_metadata,
        )

        # Re-read and fully replay before releasing the cross-process lock.
        # This proves the fsynced append is parseable, contiguous and unique.
        verified = self._load_existing_task(task.task_id)
        if (
            verified is None
            or verified.revision != next_revision
            or not verified.events
            or verified.events[-1].event_id != committed.events[-1].event_id
            or verified.events[-1].event_type != event_type
        ):
            raise OSError("recovery event commit verification failed")
        return verified.model_copy(deep=True)

    def mark_paused(
        self,
        task: TaskSchema,
        result: AgentResultSchema,
        current_step: str = "workflow_paused",
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecordSchema:
        return self._mark(
            task,
            status=ExecutionStatus.PAUSED,
            event_type="task_paused",
            current_step=current_step,
            result=result,
            error=result.error,
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
            with self._task_process_lock(task_id):
                record = self._load_existing_task(task_id)
                if record is None:
                    raise KeyError(f"Task state not found: {task_id}")
                return record.model_copy(deep=True)

    def get_authorization_metadata(
        self,
        task_id: str,
    ) -> TaskAuthorizationIdentitySchema:
        """Read the immutable identity file without opening the Task JSONL."""

        with self._lock:
            return self._read_authorization_identity(task_id)

    def get_authorization_identity_path(self, task_id: str) -> Path:
        return self._task_identity_path(task_id)

    def migrate_legacy_authorization_identity(
        self,
        task_id: str,
        *,
        apply: bool = False,
    ) -> TaskAuthorizationIdentityMigrationResult:
        """PRIVILEGED/OFFLINE ONLY: project a legacy log's identity.

        This method must be called only by an explicit administrator migration.
        Online application and authorization paths must never invoke it because
        it deliberately reads the full Task JSONL before an identity exists.
        """

        try:
            task_path = self._task_path(task_id)
            identity_path = self._task_identity_path(task_id)
            self._task_lock_path(task_id)
        except Exception as exc:
            return TaskAuthorizationIdentityMigrationResult(
                task_id=task_id,
                status=TaskAuthorizationIdentityMigrationStatus.FAILED,
                detail=f"unsafe task path: {type(exc).__name__}: {exc}",
            )

        try:
            with self._lock:
                with self._task_process_lock(task_id):
                    if not task_path.is_file():
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=(
                                TaskAuthorizationIdentityMigrationStatus
                                .MISSING_TASK_LOG
                            ),
                        )

                    try:
                        events = self._read_task_events(
                            task_path,
                            recover_torn_write=False,
                        )
                        self._validate_migration_event_identity(task_id, events)
                        record = self._record_from_events(events)
                        expected_identity = self._identity_from_record(record)
                    except (TypeError, ValueError) as exc:
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=(
                                TaskAuthorizationIdentityMigrationStatus
                                .INVALID_TASK_LOG
                            ),
                            detail=str(exc),
                        )

                    if identity_path.exists():
                        try:
                            persisted = self._read_authorization_identity(task_id)
                        except (KeyError, TypeError, ValueError) as exc:
                            return TaskAuthorizationIdentityMigrationResult(
                                task_id=task_id,
                                status=(
                                    TaskAuthorizationIdentityMigrationStatus
                                    .CONFLICT
                                ),
                                detail=str(exc),
                            )
                        status = (
                            TaskAuthorizationIdentityMigrationStatus
                            .ALREADY_CURRENT
                            if persisted == expected_identity
                            else TaskAuthorizationIdentityMigrationStatus.CONFLICT
                        )
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=status,
                        )

                    if not apply:
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=(
                                TaskAuthorizationIdentityMigrationStatus
                                .WOULD_CREATE
                            ),
                        )

                    try:
                        persisted = self._ensure_authorization_identity(
                            expected_identity
                        )
                    except (TypeError, ValueError) as exc:
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=(
                                TaskAuthorizationIdentityMigrationStatus
                                .CONFLICT
                            ),
                            detail=str(exc),
                        )
                    if persisted != expected_identity:
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=(
                                TaskAuthorizationIdentityMigrationStatus
                                .CONFLICT
                            ),
                            detail="published identity verification mismatch",
                        )
                    verified = self._read_authorization_identity(task_id)
                    if verified != expected_identity:
                        return TaskAuthorizationIdentityMigrationResult(
                            task_id=task_id,
                            status=(
                                TaskAuthorizationIdentityMigrationStatus
                                .CONFLICT
                            ),
                            detail="identity re-read verification mismatch",
                        )
                    return TaskAuthorizationIdentityMigrationResult(
                        task_id=task_id,
                        status=TaskAuthorizationIdentityMigrationStatus.CREATED,
                    )
        except Exception as exc:
            return TaskAuthorizationIdentityMigrationResult(
                task_id=task_id,
                status=TaskAuthorizationIdentityMigrationStatus.FAILED,
                detail=f"{type(exc).__name__}: {exc}",
            )

    def exists(self, task_id: str) -> bool:
        with self._lock:
            with self._task_process_lock(task_id):
                return self._load_existing_task(task_id) is not None

    def get_task_path(self, task_id: str) -> Path:
        return self._task_path(task_id)
