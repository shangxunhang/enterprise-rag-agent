"""Durable SQLite Trace sink for required Gate V2 pre-write control events.

This module is observability infrastructure only.  It allocates a transactional
per-run presentation sequence and persists the existing ``run_trace_event_v2``
envelope.  Storage failure degrades observability; it never creates Gate
business truth or execution authority.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.persistence.safe_paths import SafeExternalIdentifier
from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from observability.trace_context import current_span
from observability.trace_summary import bounded_summary
from schemas.common import ErrorSchema
from schemas.failure import FailureOrigin
from schemas.trace import RunTraceEventSchema, TraceCorrelationSchema


LOGGER = logging.getLogger(__name__)
TRACE_DATABASE_FILENAME = "run_trace.sqlite3"
TRACE_STORE_SCHEMA_VERSION = "gate_v2_run_trace_store_v1"
TRACE_PERSISTENCE_RECOVERED_EVENT = "trace_persistence_recovered_v1"


class TraceSinkHealth(str, Enum):
    """Best-effort operational health of the Trace persistence path."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class TracePersistenceFailureDiagnostic:
    """Sanitized non-authoritative diagnosis for one failed Trace write."""

    run_id: str
    task_id: str
    write_instance_id: str | None
    event_id: str
    event_type: str
    phase: str
    failure_kind: str
    sqlite_error_code: int | None
    sqlite_error_name: str | None
    exception_class: str
    sanitized_message: str
    failed_at: str
    write_attempt_count: int


@dataclass(frozen=True)
class _PendingTraceWrite:
    event_id: str
    created_at: str
    record_kwargs: dict[str, Any]


class SQLiteTraceSink:
    """Primary durable Target Trace sink backed by a dedicated SQLite file.

    ``record`` preserves the existing ``TraceSink`` calling convention.  A
    failed SQLite write is retried a bounded number of times and then retained
    in a bounded in-process pending queue.  Programming/schema formation
    defects are deliberately *not* converted into storage degradation.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
        max_write_attempts: int = 3,
        retry_backoff_seconds: float = 0.01,
        pending_capacity: int = 256,
        connect_timeout_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_write_attempts < 1:
            raise ValueError("max_write_attempts must be >= 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        if pending_capacity < 1:
            raise ValueError("pending_capacity must be >= 1")
        if connect_timeout_seconds < 0:
            raise ValueError("connect_timeout_seconds must be >= 0")

        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.max_write_attempts = int(max_write_attempts)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.pending_capacity = int(pending_capacity)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self._sleep = sleep
        self._pending: deque[_PendingTraceWrite] = deque()
        self._lock = threading.RLock()
        self._flush_lock = threading.Lock()
        self._health = TraceSinkHealth.HEALTHY
        self._latest_failure: TracePersistenceFailureDiagnostic | None = None
        self._dropped_event_count = 0
        self._initialize_schema()

    @classmethod
    def for_data_root(
        cls,
        data_root: str | Path,
        **kwargs: Any,
    ) -> "SQLiteTraceSink":
        """Construct the non-activating Target sink at ``DATA_ROOT/runs``."""

        return cls(Path(data_root) / "runs" / TRACE_DATABASE_FILENAME, **kwargs)

    @property
    def health(self) -> TraceSinkHealth:
        with self._lock:
            return self._health

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def dropped_event_count(self) -> int:
        with self._lock:
            return self._dropped_event_count

    @property
    def latest_failure(self) -> TracePersistenceFailureDiagnostic | None:
        with self._lock:
            return self._latest_failure

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=self.connect_timeout_seconds,
            check_same_thread=False,
        )
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trace_store_metadata ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            existing = connection.execute(
                "SELECT value FROM trace_store_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if existing is not None and existing[0] != TRACE_STORE_SCHEMA_VERSION:
                raise ValueError(
                    "unsupported run_trace.sqlite3 schema version: "
                    f"{existing[0]!r}"
                )
            connection.execute(
                "INSERT OR IGNORE INTO trace_store_metadata(key, value) VALUES (?, ?)",
                ("schema_version", TRACE_STORE_SCHEMA_VERSION),
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trace_run_sequences ("
                "run_id TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL CHECK(last_sequence >= 0))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trace_events ("
                "event_id TEXT PRIMARY KEY, "
                "run_id TEXT NOT NULL, "
                "task_id TEXT NOT NULL, "
                "write_instance_id TEXT, "
                "event_type TEXT NOT NULL, "
                "event_sequence INTEGER NOT NULL CHECK(event_sequence >= 1), "
                "created_at TEXT NOT NULL, "
                "event_json TEXT NOT NULL, "
                "UNIQUE(run_id, event_sequence))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_events_run_type "
                "ON trace_events(run_id, event_type)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_events_task "
                "ON trace_events(task_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_events_write_instance "
                "ON trace_events(write_instance_id) WHERE write_instance_id IS NOT NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_events_created_at "
                "ON trace_events(created_at)"
            )
            connection.commit()

    def _now_iso(self) -> str:
        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        return self.id_generator.new_id(prefix)

    @staticmethod
    def _sanitize_message(exc: BaseException) -> str:
        return " ".join(str(exc).split())[:512]

    @staticmethod
    def _failure_kind(exc: sqlite3.Error) -> str:
        error_name = str(getattr(exc, "sqlite_errorname", "") or "").upper()
        message = str(exc).lower()
        if "BUSY" in error_name or "LOCKED" in error_name or "locked" in message:
            return "SQLITE_BUSY"
        if "READONLY" in error_name or "readonly" in message:
            return "SQLITE_READONLY"
        if "FULL" in error_name or "full" in message:
            return "SQLITE_FULL"
        if "IOERR" in error_name or "i/o" in message or "io error" in message:
            return "SQLITE_IOERR"
        if "CORRUPT" in error_name or "corrupt" in message:
            return "SQLITE_CORRUPT"
        return "SQLITE_PERSISTENCE_FAILURE"

    @staticmethod
    def _is_retryable(exc: sqlite3.Error) -> bool:
        return SQLiteTraceSink._failure_kind(exc) == "SQLITE_BUSY"

    def _build_event(
        self,
        pending: _PendingTraceWrite,
        *,
        event_sequence: int,
    ) -> RunTraceEventSchema:
        kwargs = pending.record_kwargs
        active = current_span()
        correlation = kwargs.get("correlation") or TraceCorrelationSchema()
        explicit_trace_id = kwargs.get("trace_id")
        if active is not None and active.run_id != kwargs["run_id"]:
            if (
                kwargs.get("parent_span_id") is not None
                or explicit_trace_id is None
                or not correlation.external_trace_refs
            ):
                raise ValueError(
                    "cross-run Trace record requires detached root + explicit external ref"
                )
        elif (
            active is not None
            and explicit_trace_id is not None
            and explicit_trace_id != active.trace_id
        ):
            raise ValueError(
                "explicit trace_id does not match the active same-run trace_id"
            )
        trace_id = explicit_trace_id or (
            active.trace_id if active else f"trace_{kwargs['run_id']}"
        )
        span_id = kwargs.get("span_id") or (
            active.span_id if active else self._new_id("span")
        )
        parent_span_id = kwargs.get("parent_span_id")
        if parent_span_id is None and active is not None and kwargs.get("span_id") is None:
            parent_span_id = active.parent_span_id
        phase = kwargs.get("phase") or "event"
        raw_input = kwargs.get("input_payload") or {}
        raw_output = kwargs.get("output_payload") or {}

        event = RunTraceEventSchema(
            event_id=pending.event_id,
            event_sequence=event_sequence,
            trace_id=trace_id,
            task_id=kwargs["task_id"],
            run_id=kwargs["run_id"],
            span_id=span_id,
            parent_span_id=parent_span_id,
            span_name=kwargs.get("span_name") or kwargs["component_name"],
            span_kind=kwargs.get("span_kind") or "internal",
            phase=phase,
            event_type=kwargs["event_type"],
            component_type=kwargs["component_type"],
            component_name=kwargs["component_name"],
            workflow_id=kwargs.get("workflow_id"),
            workflow_version=kwargs.get("workflow_version"),
            step_id=kwargs.get("step_id"),
            step_name=kwargs.get("step_name"),
            step_order=kwargs.get("step_order"),
            call_id=kwargs.get("call_id"),
            caller=kwargs.get("caller"),
            callee=kwargs.get("callee"),
            status=kwargs.get("status"),
            correlation=correlation,
            error=kwargs.get("error"),
            failure_origin=kwargs.get("failure_origin") or FailureOrigin.UNKNOWN,
            error_message=kwargs.get("error_message"),
            input_payload=raw_input,
            output_payload=raw_output,
            input_summary=kwargs.get("input_summary") or bounded_summary(raw_input),
            output_summary=kwargs.get("output_summary") or bounded_summary(raw_output),
            lineage=kwargs.get("lineage") or {},
            payload=kwargs.get("payload") or {},
            latency_ms=kwargs.get("latency_ms"),
            token_usage=kwargs.get("token_usage") or {},
            cost=kwargs.get("cost") or {},
            metrics=kwargs.get("metrics") or {},
            started_at=kwargs.get("started_at") or (
                pending.created_at if phase == "start" else None
            ),
            finished_at=kwargs.get("finished_at") or (
                pending.created_at if phase in {"end", "error"} else None
            ),
            created_at=pending.created_at,
            model_name=kwargs.get("model_name"),
            tool_name=kwargs.get("tool_name"),
            agent_name=kwargs.get("agent_name"),
            tags=kwargs.get("tags") or [],
            metadata=kwargs.get("metadata") or {},
            extra=kwargs.get("extra") or {},
        )
        # Deliberately surface non-JSON programming/schema defects before a DB
        # write can be misclassified as ordinary persistence degradation.
        json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        return event

    @staticmethod
    def _write_instance_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("write_instance_id")
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _persist_pending(self, pending: _PendingTraceWrite) -> RunTraceEventSchema:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT event_json FROM trace_events WHERE event_id = ?",
                (pending.event_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return RunTraceEventSchema.model_validate_json(existing[0])

            row = connection.execute(
                "SELECT last_sequence FROM trace_run_sequences WHERE run_id = ?",
                (pending.record_kwargs["run_id"],),
            ).fetchone()
            next_sequence = (int(row[0]) if row is not None else 0) + 1
            if row is None:
                connection.execute(
                    "INSERT INTO trace_run_sequences(run_id, last_sequence) VALUES (?, ?)",
                    (pending.record_kwargs["run_id"], next_sequence),
                )
            else:
                connection.execute(
                    "UPDATE trace_run_sequences SET last_sequence = ? WHERE run_id = ?",
                    (next_sequence, pending.record_kwargs["run_id"]),
                )

            event = self._build_event(pending, event_sequence=next_sequence)
            serialized = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            connection.execute(
                "INSERT INTO trace_events("
                "event_id, run_id, task_id, write_instance_id, event_type, "
                "event_sequence, created_at, event_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.run_id,
                    event.task_id,
                    self._write_instance_id(event.payload),
                    event.event_type,
                    event.event_sequence,
                    event.created_at,
                    serialized,
                ),
            )
            connection.commit()
            return event
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _diagnostic(
        self,
        pending: _PendingTraceWrite,
        exc: sqlite3.Error,
        attempts: int,
    ) -> TracePersistenceFailureDiagnostic:
        kwargs = pending.record_kwargs
        payload = kwargs.get("payload") or {}
        return TracePersistenceFailureDiagnostic(
            run_id=kwargs["run_id"],
            task_id=kwargs["task_id"],
            write_instance_id=self._write_instance_id(payload),
            event_id=pending.event_id,
            event_type=kwargs["event_type"],
            phase=kwargs.get("phase") or "event",
            failure_kind=self._failure_kind(exc),
            sqlite_error_code=getattr(exc, "sqlite_errorcode", None),
            sqlite_error_name=getattr(exc, "sqlite_errorname", None),
            exception_class=type(exc).__name__,
            sanitized_message=self._sanitize_message(exc),
            failed_at=self._now_iso(),
            write_attempt_count=attempts,
        )

    def _persist_with_retry(
        self,
        pending: _PendingTraceWrite,
    ) -> RunTraceEventSchema | TracePersistenceFailureDiagnostic:
        last_error: sqlite3.Error | None = None
        attempts = 0
        for attempts in range(1, self.max_write_attempts + 1):
            try:
                return self._persist_pending(pending)
            except sqlite3.Error as exc:
                last_error = exc
                if attempts >= self.max_write_attempts or not self._is_retryable(exc):
                    break
                if self.retry_backoff_seconds:
                    self._sleep(self.retry_backoff_seconds)
        assert last_error is not None
        return self._diagnostic(pending, last_error, attempts)

    def _append_pending(
        self,
        pending: _PendingTraceWrite,
        diagnostic: TracePersistenceFailureDiagnostic,
    ) -> None:
        with self._lock:
            if len(self._pending) >= self.pending_capacity:
                self._pending.popleft()
                self._dropped_event_count += 1
            self._pending.append(pending)
            self._health = TraceSinkHealth.DEGRADED
            self._latest_failure = diagnostic
        LOGGER.error(
            "Gate Trace persistence degraded: kind=%s run_id=%s event_type=%s "
            "event_id=%s attempts=%s dropped=%s error=%s",
            diagnostic.failure_kind,
            diagnostic.run_id,
            diagnostic.event_type,
            diagnostic.event_id,
            diagnostic.write_attempt_count,
            self.dropped_event_count,
            diagnostic.sanitized_message,
        )

    def _recovery_diagnostic_pending(
        self,
        failure: TracePersistenceFailureDiagnostic,
    ) -> _PendingTraceWrite:
        """Build the compact non-authoritative C6 persistence-gap recovery event."""

        return _PendingTraceWrite(
            event_id=self._new_id("event"),
            created_at=self._now_iso(),
            record_kwargs={
                "task_id": failure.task_id,
                "run_id": failure.run_id,
                "event_type": TRACE_PERSISTENCE_RECOVERED_EVENT,
                "component_type": "observability",
                "component_name": "sqlite_trace_sink",
                "payload": {
                    "write_instance_id": failure.write_instance_id,
                    "recovered_failed_event_id": failure.event_id,
                    "recovered_failed_event_type": failure.event_type,
                    "failure_kind": failure.failure_kind,
                    "failed_at": failure.failed_at,
                    "failed_write_attempt_count": failure.write_attempt_count,
                    "dropped_event_count": self.dropped_event_count,
                },
                "input_payload": {},
                "output_payload": {},
                "status": "recovered",
                "tags": ["trace_persistence", "operational_diagnostic"],
                "metadata": {},
                "extra": {},
                "phase": "event",
            },
        )

    def flush_pending(self) -> int:
        """Best-effort drain using original IDs, then persist one recovery diagnostic."""

        # Multiple business threads may discover a recovered Trace store at the
        # same time. Serialize drain/diagnostic work so the same in-memory gap is
        # not converted into duplicate operational recovery observations.
        with self._flush_lock:
            flushed = 0
            recovered_required_gap = False
            recovery_basis: TracePersistenceFailureDiagnostic | None = None
            while True:
                with self._lock:
                    if not self._pending:
                        if not recovered_required_gap or recovery_basis is None:
                            self._health = TraceSinkHealth.HEALTHY
                            return flushed
                        diagnostic_pending = self._recovery_diagnostic_pending(
                            recovery_basis
                        )
                        # The gap diagnostic is itself observability only. It is
                        # attempted regardless TRACE_ENABLED, but a failure to
                        # persist it still must not escape into Gate control flow.
                        outcome = self._persist_with_retry(diagnostic_pending)
                        if isinstance(outcome, TracePersistenceFailureDiagnostic):
                            self._append_pending(diagnostic_pending, outcome)
                            return flushed
                        self._health = TraceSinkHealth.HEALTHY
                        return flushed
                    pending = self._pending[0]
                    current_failure = self._latest_failure
                outcome = self._persist_with_retry(pending)
                if isinstance(outcome, TracePersistenceFailureDiagnostic):
                    with self._lock:
                        self._health = TraceSinkHealth.DEGRADED
                        self._latest_failure = outcome
                    LOGGER.error(
                        "Gate Trace pending flush still degraded: kind=%s event_id=%s error=%s",
                        outcome.failure_kind,
                        outcome.event_id,
                        outcome.sanitized_message,
                    )
                    return flushed
                with self._lock:
                    if self._pending and self._pending[0].event_id == pending.event_id:
                        self._pending.popleft()
                flushed += 1
                if pending.record_kwargs.get("event_type") != TRACE_PERSISTENCE_RECOVERED_EVENT:
                    recovered_required_gap = True
                    recovery_basis = current_failure or recovery_basis

    def record(
        self,
        task_id: str,
        run_id: str,
        event_type: str,
        component_type: str,
        component_name: str,
        payload: Optional[Dict[str, Any]] = None,
        input_payload: Optional[Dict[str, Any]] = None,
        output_payload: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[str] = None,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
        step_order: Optional[int] = None,
        call_id: Optional[str] = None,
        caller: Optional[str] = None,
        callee: Optional[str] = None,
        status: Optional[str] = None,
        correlation: Optional[TraceCorrelationSchema] = None,
        error: Optional[ErrorSchema] = None,
        failure_origin: FailureOrigin = FailureOrigin.UNKNOWN,
        error_message: Optional[str] = None,
        latency_ms: Optional[int] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        cost: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        span_name: Optional[str] = None,
        span_kind: str = "internal",
        phase: str = "event",
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        input_summary: Optional[Dict[str, Any]] = None,
        output_summary: Optional[Dict[str, Any]] = None,
        lineage: Optional[Dict[str, Any]] = None,
    ) -> RunTraceEventSchema | TracePersistenceFailureDiagnostic:
        SafeExternalIdentifier.validate(task_id, field_name="task_id")
        SafeExternalIdentifier.validate(run_id, field_name="run_id")
        record_kwargs: dict[str, Any] = {
            "task_id": task_id,
            "run_id": run_id,
            "event_type": event_type,
            "component_type": component_type,
            "component_name": component_name,
            "payload": payload or {},
            "input_payload": input_payload or {},
            "output_payload": output_payload or {},
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "step_id": step_id,
            "step_name": step_name,
            "step_order": step_order,
            "call_id": call_id,
            "caller": caller,
            "callee": callee,
            "status": status,
            "correlation": correlation or TraceCorrelationSchema(),
            "error": error,
            "failure_origin": failure_origin,
            "error_message": error_message,
            "latency_ms": latency_ms,
            "token_usage": token_usage or {},
            "cost": cost or {},
            "metrics": metrics or {},
            "model_name": model_name,
            "tool_name": tool_name,
            "agent_name": agent_name,
            "tags": tags or [],
            "metadata": metadata or {},
            "extra": extra or {},
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "span_name": span_name,
            "span_kind": span_kind,
            "phase": phase,
            "started_at": started_at,
            "finished_at": finished_at,
            "input_summary": input_summary or {},
            "output_summary": output_summary or {},
            "lineage": lineage or {},
        }
        pending = _PendingTraceWrite(
            event_id=self._new_id("event"),
            created_at=self._now_iso(),
            record_kwargs=record_kwargs,
        )

        # A recovered store drains older failed observations before the new one;
        # failure to drain remains non-blocking and does not redirect to JSONL.
        if self.health is TraceSinkHealth.DEGRADED:
            self.flush_pending()

        outcome = self._persist_with_retry(pending)
        if isinstance(outcome, TracePersistenceFailureDiagnostic):
            self._append_pending(pending, outcome)
            return outcome

        with self._lock:
            if not self._pending:
                self._health = TraceSinkHealth.HEALTHY
        return outcome


__all__ = [
    "SQLiteTraceSink",
    "TRACE_DATABASE_FILENAME",
    "TRACE_STORE_SCHEMA_VERSION",
    "TRACE_PERSISTENCE_RECOVERED_EVENT",
    "TracePersistenceFailureDiagnostic",
    "TraceSinkHealth",
]
