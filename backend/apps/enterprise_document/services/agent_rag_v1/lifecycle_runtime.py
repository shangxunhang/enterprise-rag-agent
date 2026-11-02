"""Canonical SIF-01 Run / Attempt lifecycle runtime.

The service owns lifecycle/currentness semantics. JSONL is only the physical
persistence technique; Workflow State and checkpoint data are projections/recovery inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any, Callable

from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ATTEMPT_TERMINAL_STATUSES,
    RUN_TERMINAL_STATUSES,
    ActiveExecutionLineageSchema,
    AttemptIdentitySchema,
    AttemptLifecycleRecordSchema,
    AttemptLifecycleStatus,
    RunCompletionAssessmentRecordSchema,
    RunCompletionAssessmentRefSchema,
    RunCompletionAggregateResult,
    RunIdentitySchema,
    RunFinalizationBarrierSchema,
    RunLifecycleRecordSchema,
    RunLifecycleStatus,
)


_EVENT_SCHEMA_VERSION = "run_attempt_lifecycle_event_v2"
_COMPOSITE_EVENT_SCHEMA_VERSION = "run_attempt_lifecycle_composite_event_v1"
_BUDGET_EVENT_SCHEMA_VERSION = "run_attempt_lifecycle_budget_event_v1"

_RUN_ALLOWED: dict[RunLifecycleStatus, frozenset[RunLifecycleStatus]] = {
    RunLifecycleStatus.PENDING: frozenset(
        {
            RunLifecycleStatus.RUNNING,
            RunLifecycleStatus.FAILED,
            RunLifecycleStatus.CANCELLED,
        }
    ),
    RunLifecycleStatus.RUNNING: frozenset(
        {
            RunLifecycleStatus.PAUSED,
            RunLifecycleStatus.RETRYABLE_FAILED,
            RunLifecycleStatus.FINALIZING,
            RunLifecycleStatus.PARTIAL_SUCCESS,
            RunLifecycleStatus.FAILED,
            RunLifecycleStatus.CANCELLED,
        }
    ),
    RunLifecycleStatus.FINALIZING: frozenset(
        {
            RunLifecycleStatus.RUNNING,
            RunLifecycleStatus.SUCCESS,
            RunLifecycleStatus.FAILED,
            RunLifecycleStatus.CANCELLED,
        }
    ),
    RunLifecycleStatus.PAUSED: frozenset(
        {
            RunLifecycleStatus.RUNNING,
            RunLifecycleStatus.RETRYABLE_FAILED,
            RunLifecycleStatus.FAILED,
            RunLifecycleStatus.CANCELLED,
        }
    ),
    RunLifecycleStatus.RETRYABLE_FAILED: frozenset(
        {
            RunLifecycleStatus.RUNNING,
            RunLifecycleStatus.FAILED,
            RunLifecycleStatus.CANCELLED,
        }
    ),
    RunLifecycleStatus.SUCCESS: frozenset(),
    RunLifecycleStatus.PARTIAL_SUCCESS: frozenset(),
    RunLifecycleStatus.FAILED: frozenset(),
    RunLifecycleStatus.CANCELLED: frozenset(),
}

_ATTEMPT_ALLOWED: dict[
    AttemptLifecycleStatus,
    frozenset[AttemptLifecycleStatus],
] = {
    AttemptLifecycleStatus.PENDING: frozenset(
        {
            AttemptLifecycleStatus.RUNNING,
            AttemptLifecycleStatus.FAILED,
            AttemptLifecycleStatus.CANCELLED,
            AttemptLifecycleStatus.SUPERSEDED,
        }
    ),
    AttemptLifecycleStatus.RUNNING: frozenset(
        {
            AttemptLifecycleStatus.COMPLETED,
            AttemptLifecycleStatus.PARTIAL,
            AttemptLifecycleStatus.FAILED,
            AttemptLifecycleStatus.CANCELLED,
            AttemptLifecycleStatus.SUPERSEDED,
        }
    ),
    AttemptLifecycleStatus.COMPLETED: frozenset(),
    AttemptLifecycleStatus.PARTIAL: frozenset(),
    AttemptLifecycleStatus.FAILED: frozenset(),
    AttemptLifecycleStatus.CANCELLED: frozenset(),
    AttemptLifecycleStatus.SUPERSEDED: frozenset(),
}


class LifecycleIntegrityError(ValueError):
    """Persisted lifecycle history is missing, corrupt, or contradictory."""


class LifecycleRevisionConflict(LifecycleIntegrityError):
    """A CAS lifecycle revision no longer matches the canonical record."""


class LifecycleCurrentnessError(LifecycleIntegrityError):
    """An Attempt is not the current active lineage for its scope."""


@dataclass(frozen=True)
class LifecycleCompositeTransition:
    """Ephemeral view of one committed frame in the existing lifecycle journal."""

    operation_key: str
    event_ref: str
    event_sha256: str
    resulting_run: RunLifecycleRecordSchema
    resulting_attempts: tuple[AttemptLifecycleRecordSchema, ...]


@dataclass(frozen=True)
class LifecycleBudgetPreimage:
    run: RunLifecycleRecordSchema
    current_children: tuple[AttemptLifecycleRecordSchema, ...]
    child_preimage_sha256: str


@dataclass(frozen=True)
class LifecycleBudgetTransition:
    kind: str
    operation_key: str
    event_ref: str
    event_sha256: str
    resulting_run: RunLifecycleRecordSchema
    current_children: tuple[AttemptLifecycleRecordSchema, ...]
    child_preimage_sha256: str
    wait_ref: str
    wait_sha256: str
    semantic_operation_ref: str
    semantic_input_sha256: str
    artifact_version: str
    wait_event_ref: str | None
    wait_event_sha256: str | None
    grant_ref: str | None
    grant_sha256: str | None
    resume_occurrence_id: str | None


class RunAttemptLifecycleService:
    """Single canonical owner for Run/Attempt lifecycle and active lineage."""

    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS: dict[str, threading.RLock] = {}

    def __init__(
        self,
        path: str | Path = "data/runtime/agent_rag_v1/lifecycle.jsonl",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
        run_completion_assessment_resolver: (
            Callable[
                [RunCompletionAssessmentRefSchema],
                RunCompletionAssessmentRecordSchema,
            ]
            | None
        ) = None,
    ) -> None:
        self.path = Path(path)
        self.log = CrashSafeJsonlEventLog(self.path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.run_completion_assessment_resolver = (
            run_completion_assessment_resolver
        )
        lock_key = str(self.path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._PATH_LOCKS.setdefault(lock_key, threading.RLock())

    def bind_run_completion_assessment_resolver(
        self,
        resolver: Callable[
            [RunCompletionAssessmentRefSchema],
            RunCompletionAssessmentRecordSchema,
        ],
    ) -> None:
        """Bind the Wave-13 terminalization resolver exactly once.

        Lifecycle remains the sole Run mutation owner.  This seam only installs
        the immutable assessment resolver; it never imports
        Post-write or derives completion truth.
        """

        if not callable(resolver):
            raise TypeError("run completion assessment resolver must be callable")
        current = self.run_completion_assessment_resolver
        if current is None:
            self.run_completion_assessment_resolver = resolver
            return
        if current != resolver:
            raise LifecycleIntegrityError(
                "RunCompletionAssessment resolver is already bound"
            )

    def _now(self) -> datetime:
        value = datetime.fromisoformat(self.clock.now_iso())
        if value.tzinfo is None:
            raise LifecycleIntegrityError("lifecycle Clock must return timezone-aware time")
        return value

    @staticmethod
    def _serialized_event(
        *,
        entity_type: str,
        event_type: str,
        record: RunLifecycleRecordSchema | AttemptLifecycleRecordSchema,
    ) -> str:
        return json.dumps(
            {
                "schema_version": _EVENT_SCHEMA_VERSION,
                "entity_type": entity_type,
                "event_type": event_type,
                "record": record.model_dump(mode="json", round_trip=True),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _append(
        self,
        *,
        entity_type: str,
        event_type: str,
        record: RunLifecycleRecordSchema | AttemptLifecycleRecordSchema,
    ) -> None:
        self.log.append_serialized(
            self._serialized_event(
                entity_type=entity_type,
                event_type=event_type,
                record=record,
            )
        )

    def _read_records_unlocked(
        self,
    ) -> list[RunLifecycleRecordSchema | AttemptLifecycleRecordSchema]:
        """The sole strict event/embedded-record decoder for every replay path."""
        return self._decode_history_unlocked()[0]

    def _decode_history_unlocked(self, *, budget_frames=None):
        records: list[RunLifecycleRecordSchema | AttemptLifecycleRecordSchema] = []
        composites: list[dict[str, Any]] = []
        operations: dict[str, dict[str, Any]] = {}
        request_keys: dict[str, str] = {}
        budget_events: list[dict[str, Any]] = []
        budget_operations: dict[str, dict[str, Any]] = {}
        for line_number, line in enumerate(self.log.read_complete_lines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise LifecycleIntegrityError("lifecycle event must be a JSON object")
                if payload.get("schema_version") == _BUDGET_EVENT_SCHEMA_VERSION:
                    request = payload.get("request")
                    if not isinstance(request, dict):
                        raise LifecycleIntegrityError("budget lifecycle request is required")
                    request_key = request.get("operation_request_key")
                    if not isinstance(request_key, str):
                        raise LifecycleIntegrityError("budget lifecycle operation key is required")
                    previous = budget_operations.get(request_key)
                    if previous is not None:
                        if previous != payload:
                            raise LifecycleIntegrityError("conflicting budget lifecycle operation replay")
                        continue
                    runs, attempts = self._load_all_unlocked(records)
                    resulting_run = self._validate_budget_frame(payload, runs, attempts, budget_events)
                    next_records = [*records, resulting_run]
                    self._load_all_unlocked(next_records)
                    records = next_records
                    budget_operations[request_key] = payload
                    budget_events.append(payload)
                    if budget_frames is not None:
                        budget_frames.append(payload)
                    continue
                if payload.get("schema_version") == _COMPOSITE_EVENT_SCHEMA_VERSION:
                    operation_key = payload.get("operation_key")
                    previous = operations.get(operation_key)
                    if previous is not None:
                        if previous != payload:
                            raise LifecycleIntegrityError("conflicting composite operation replay")
                        continue
                    # Validate the complete frame against the committed prefix privately.
                    # No member becomes visible until all preimages and results agree.
                    runs, attempts = self._load_all_unlocked(records)
                    resulting_run, resulting_attempts = self._validate_composite(
                        payload, runs, attempts
                    )
                    request_key = payload["request"]["operation_request_key"]
                    if request_key is not None:
                        if request_key in request_keys:
                            raise LifecycleIntegrityError("composite request key was reused with another preimage")
                        request_keys[request_key] = operation_key
                    next_records = [*records, *resulting_attempts, resulting_run]
                    self._load_all_unlocked(next_records)
                    records = next_records
                    operations[operation_key] = payload
                    composites.append(payload)
                    continue
                if payload.get("schema_version") != _EVENT_SCHEMA_VERSION:
                    raise LifecycleIntegrityError("unknown lifecycle event schema version")
                raw_record = payload.get("record")
                if not isinstance(raw_record, dict):
                    raise LifecycleIntegrityError("lifecycle event record is required")
                entity_type = payload.get("entity_type")
                if entity_type == "run":
                    schema = RunLifecycleRecordSchema
                    version = "run_lifecycle_record_v2"
                elif entity_type == "attempt":
                    schema = AttemptLifecycleRecordSchema
                    version = "attempt_lifecycle_record_v1"
                else:
                    raise LifecycleIntegrityError("unknown lifecycle entity_type")
                if raw_record.get("schema_version") != version:
                    raise LifecycleIntegrityError("unknown lifecycle record schema version")
                records.append(schema.model_validate(raw_record))
            except LifecycleIntegrityError:
                raise
            except Exception as exc:
                raise LifecycleIntegrityError(
                    f"invalid lifecycle record at {self.path}:{line_number}"
                ) from exc
        return records, composites

    def _load_all_unlocked(
        self,
        records: list[RunLifecycleRecordSchema | AttemptLifecycleRecordSchema] | None = None,
    ) -> tuple[
        dict[str, RunLifecycleRecordSchema],
        dict[str, AttemptLifecycleRecordSchema],
    ]:
        runs: dict[str, RunLifecycleRecordSchema] = {}
        attempts: dict[str, AttemptLifecycleRecordSchema] = {}

        history = self._read_records_unlocked() if records is None else records
        for record in history:
            if isinstance(record, RunLifecycleRecordSchema):
                run_id = record.identity.run_id
                previous = runs.get(run_id)
                if previous is None:
                    if record.lifecycle_revision != 0:
                        raise LifecycleIntegrityError(
                            "first Run lifecycle revision must be zero"
                        )
                else:
                    if previous.identity != record.identity:
                        raise LifecycleIntegrityError(
                            "Run identity changed within lifecycle history"
                        )
                    if record.lifecycle_revision != previous.lifecycle_revision + 1:
                        raise LifecycleIntegrityError(
                            "Run lifecycle revisions must be contiguous"
                        )
                runs[run_id] = record
            else:
                attempt_id = record.identity.attempt_id
                previous = attempts.get(attempt_id)
                if previous is None:
                    if record.lifecycle_revision != 0:
                        raise LifecycleIntegrityError(
                            "first Attempt lifecycle revision must be zero"
                        )
                else:
                    if previous.identity != record.identity:
                        raise LifecycleIntegrityError(
                            "Attempt identity changed within lifecycle history"
                        )
                    if record.lifecycle_revision != previous.lifecycle_revision + 1:
                        raise LifecycleIntegrityError(
                            "Attempt lifecycle revisions must be contiguous"
                        )
                attempts[attempt_id] = record

        for attempt in attempts.values():
            if attempt.identity.run_id not in runs:
                raise LifecycleIntegrityError(
                    "Attempt lifecycle references an unknown canonical Run"
                )
        return runs, attempts

    def _with_state(
        self,
    ) -> tuple[
        dict[str, RunLifecycleRecordSchema],
        dict[str, AttemptLifecycleRecordSchema],
    ]:
        return self._load_all_unlocked()

    @staticmethod
    def _scope_attempts(
        attempts: dict[str, AttemptLifecycleRecordSchema],
        *,
        run_id: str,
        attempt_scope_ref: str,
    ) -> list[AttemptLifecycleRecordSchema]:
        rows = [
            record
            for record in attempts.values()
            if record.identity.run_id == run_id
            and record.identity.attempt_scope_ref == attempt_scope_ref
        ]
        rows.sort(
            key=lambda record: (
                record.identity.attempt_ordinal,
                record.identity.attempt_id,
            )
        )
        ordinals = [record.identity.attempt_ordinal for record in rows]
        if len(ordinals) != len(set(ordinals)):
            raise LifecycleIntegrityError(
                "Attempt ordinals must be unique within one Run/scope"
            )
        return rows

    @classmethod
    def _current_attempt(
        cls,
        attempts: dict[str, AttemptLifecycleRecordSchema],
        *,
        run_id: str,
        attempt_scope_ref: str,
    ) -> AttemptLifecycleRecordSchema | None:
        rows = cls._scope_attempts(
            attempts,
            run_id=run_id,
            attempt_scope_ref=attempt_scope_ref,
        )
        if not rows:
            return None
        current = rows[-1]
        if current.status in ATTEMPT_TERMINAL_STATUSES:
            return None
        return current

    @classmethod
    def _current_attempts_for_run(cls, attempts, run_id):
        scopes = {
            row.identity.attempt_scope_ref for row in attempts.values()
            if row.identity.run_id == run_id
        }
        rows = [cls._current_attempt(attempts, run_id=run_id, attempt_scope_ref=scope)
                for scope in scopes]
        return sorted((row for row in rows if row is not None), key=lambda row: (
            row.identity.attempt_scope_ref, row.identity.attempt_ordinal,
            row.identity.attempt_id,
        ))

    @staticmethod
    def _composite_request(*, kind, run_id, expected_revision, reason_ref,
                           target_status, triggering_attempt_id=None,
                           expected_attempt_revisions=None, operation_key=None,
                           attempt_scope_ref=None, input_snapshot_version=None,
                           attempt_id=None):
        def optional(value):
            if value is None:
                return None
            normalized = str(value).strip()
            if not normalized:
                raise LifecycleIntegrityError("composite authority fields cannot be blank")
            return normalized

        normalized_run = optional(run_id)
        normalized_reason = optional(reason_ref)
        if normalized_run is None or normalized_reason is None:
            raise LifecycleIntegrityError("composite Run and reason authority are required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise LifecycleIntegrityError("composite expected Run revision is required")
        revisions = None
        if expected_attempt_revisions is not None:
            if not isinstance(expected_attempt_revisions, dict):
                raise LifecycleIntegrityError("expected Attempt revisions must be an exact map")
            revisions = {}
            for key, revision in expected_attempt_revisions.items():
                normalized_key = optional(key)
                if (normalized_key is None or normalized_key in revisions
                        or type(revision) is not int or revision < 0):
                    raise LifecycleIntegrityError("invalid expected Attempt revision map")
                revisions[normalized_key] = revision
        if kind not in {"NONCANCEL_FAILURE", "DOCUMENT_NONCANCEL_FAILURE", "RETRY_WITH_SUCCESSOR"}:
            raise LifecycleIntegrityError("unknown composite transition kind")
        target = RunLifecycleStatus(target_status)
        request = {
            "kind": kind, "run_id": normalized_run,
            "expected_lifecycle_revision": expected_revision,
            "reason_ref": normalized_reason, "target_status": target.value,
            "triggering_attempt_id": optional(triggering_attempt_id),
            "expected_attempt_revisions": revisions,
            "operation_request_key": optional(operation_key),
            "attempt_scope_ref": optional(attempt_scope_ref),
            "input_snapshot_version": optional(input_snapshot_version),
            "attempt_id": optional(attempt_id),
        }
        if kind == "NONCANCEL_FAILURE":
            if target not in {RunLifecycleStatus.FAILED, RunLifecycleStatus.RETRYABLE_FAILED}:
                raise LifecycleIntegrityError("noncancel failure requires a failure Run status")
            if any(request[name] is not None for name in (
                    "attempt_scope_ref", "input_snapshot_version", "attempt_id")):
                raise LifecycleIntegrityError("failure cannot carry retry successor fields")
        elif kind == "DOCUMENT_NONCANCEL_FAILURE":
            if (target is not RunLifecycleStatus.FAILED
                    or request["triggering_attempt_id"] is not None
                    or revisions is None
                    or any(request[name] is not None for name in (
                        "attempt_scope_ref", "input_snapshot_version", "attempt_id"))):
                raise LifecycleIntegrityError(
                    "document failure requires FAILED, no trigger/successor, and a complete Attempt preimage"
                )
        elif (target is not RunLifecycleStatus.RUNNING
              or request["triggering_attempt_id"] is not None
              or not request["attempt_scope_ref"] or not request["input_snapshot_version"]):
            raise LifecycleIntegrityError("retry requires an exact scope and snapshot")
        return request

    def _derive_composite(self, request, runs, attempts, now, successor_attempt_id):
        run = runs.get(request["run_id"])
        if run is None:
            raise LifecycleIntegrityError("composite canonical Run is missing")
        if run.lifecycle_revision != request["expected_lifecycle_revision"]:
            raise LifecycleRevisionConflict("composite Run preimage revision conflict")
        current = self._current_attempts_for_run(attempts, request["run_id"])
        expected = request["expected_attempt_revisions"]
        if expected is not None and expected != {
                row.identity.attempt_id: row.lifecycle_revision for row in current}:
            raise LifecycleRevisionConflict("composite complete Attempt preimage conflict")
        target = RunLifecycleStatus(request["target_status"])
        changed = []
        previous = None
        if request["kind"] == "NONCANCEL_FAILURE":
            if run.status not in {RunLifecycleStatus.PENDING, RunLifecycleStatus.RUNNING,
                                  RunLifecycleStatus.FINALIZING}:
                raise LifecycleIntegrityError("noncancel failure Run preimage is ineligible")
            if run.status is not RunLifecycleStatus.RUNNING and (
                    current or target is not RunLifecycleStatus.FAILED):
                raise LifecycleIntegrityError("PENDING/FINALIZING failure requires zero current Attempts")
            trigger = request["triggering_attempt_id"]
            if trigger is None and len(current) == 1:
                trigger = current[0].identity.attempt_id
            if ((current and trigger not in {row.identity.attempt_id for row in current})
                    or (not current and trigger is not None)
                    or successor_attempt_id is not None):
                raise LifecycleIntegrityError("failure trigger must bind one exact current Attempt")
            for row in current:
                failed = row.identity.attempt_id == trigger
                updated = row.model_copy(update={
                    "status": AttemptLifecycleStatus.FAILED if failed else AttemptLifecycleStatus.CANCELLED,
                    "lifecycle_revision": row.lifecycle_revision + 1,
                    "finished_at": now,
                    "failure_ref": request["reason_ref"] if failed else "run_terminal_failure_sibling_cancel",
                }, deep=True)
                changed.append(AttemptLifecycleRecordSchema.model_validate(updated.model_dump(mode="python")))
        elif request["kind"] == "DOCUMENT_NONCANCEL_FAILURE":
            if (run.status not in {RunLifecycleStatus.RUNNING, RunLifecycleStatus.FINALIZING}
                    or successor_attempt_id is not None):
                raise LifecycleIntegrityError("document failure Run preimage is ineligible")
            # The fixed Run revision binds the exact canonical barrier; validate
            # its embedded material again without replacing it or reopening writes.
            run = RunLifecycleRecordSchema.model_validate(
                run.model_dump(mode="python", round_trip=True)
            )
            for row in current:
                updated = row.model_copy(update={
                    "status": AttemptLifecycleStatus.CANCELLED,
                    "lifecycle_revision": row.lifecycle_revision + 1,
                    "finished_at": now,
                    "failure_ref": "run_document_failure_cancel",
                }, deep=True)
                changed.append(AttemptLifecycleRecordSchema.model_validate(updated.model_dump(mode="python")))
        else:
            if run.status is not RunLifecycleStatus.RETRYABLE_FAILED or current:
                raise LifecycleIntegrityError("retry requires RETRYABLE_FAILED and zero current Attempts")
            rows = self._scope_attempts(attempts, run_id=request["run_id"],
                                       attempt_scope_ref=request["attempt_scope_ref"])
            previous = rows[-1] if rows else None
            if previous is not None and previous.status not in ATTEMPT_TERMINAL_STATUSES:
                raise LifecycleIntegrityError("retry predecessor must be terminal")
            if (not successor_attempt_id or successor_attempt_id in attempts
                    or (request["attempt_id"] is not None
                        and successor_attempt_id != request["attempt_id"])):
                raise LifecycleIntegrityError("retry successor identity is already claimed or mismatched")
            changed.append(AttemptLifecycleRecordSchema(
                identity=AttemptIdentitySchema(
                    attempt_id=successor_attempt_id, run_id=request["run_id"],
                    attempt_scope_ref=request["attempt_scope_ref"],
                    attempt_ordinal=(previous.identity.attempt_ordinal if previous else 0) + 1,
                    input_snapshot_version=request["input_snapshot_version"],
                ), status=AttemptLifecycleStatus.PENDING, lifecycle_revision=0,
                supersedes_attempt_id=previous.identity.attempt_id if previous else None,
                created_at=now,
            ))
        updated_run = run.model_copy(update={
            "status": target, "lifecycle_revision": run.lifecycle_revision + 1,
            "updated_at": now,
            "started_at": (run.started_at or now) if target is RunLifecycleStatus.RUNNING else run.started_at,
            "finished_at": now if target in RUN_TERMINAL_STATUSES else None,
            "terminal_reason_ref": request["reason_ref"],
        }, deep=True)
        updated_run = RunLifecycleRecordSchema.model_validate(updated_run.model_dump(mode="python"))
        return run, current, previous, updated_run, tuple(changed)

    @staticmethod
    def _record_payload(record):
        return record.model_dump(mode="json", round_trip=True) if record is not None else None

    def _build_composite(self, request, runs, attempts, now, successor_attempt_id):
        run, current, previous, updated, changed = self._derive_composite(
            request, runs, attempts, now, successor_attempt_id
        )
        authority = {
            "schema_version": _COMPOSITE_EVENT_SCHEMA_VERSION,
            "request": request, "before_run": self._record_payload(run),
            "before_attempts": [self._record_payload(row) for row in current],
            "previous_scope_attempt": self._record_payload(previous),
            "successor_attempt_id": successor_attempt_id,
        }
        frame = {**authority, "operation_key": canonical_sha256(authority),
                 "resulting_run": self._record_payload(updated),
                 "resulting_attempts": [self._record_payload(row) for row in changed]}
        frame["event_sha256"] = canonical_sha256(frame)
        return frame

    def _validate_composite(self, frame, runs, attempts):
        request = frame.get("request")
        if not isinstance(request, dict):
            raise LifecycleIntegrityError("composite request is required")
        normalized = self._composite_request(
            kind=request.get("kind"), run_id=request.get("run_id"),
            expected_revision=request.get("expected_lifecycle_revision"),
            reason_ref=request.get("reason_ref"), target_status=request.get("target_status"),
            triggering_attempt_id=request.get("triggering_attempt_id"),
            expected_attempt_revisions=request.get("expected_attempt_revisions"),
            operation_key=request.get("operation_request_key"),
            attempt_scope_ref=request.get("attempt_scope_ref"),
            input_snapshot_version=request.get("input_snapshot_version"),
            attempt_id=request.get("attempt_id"),
        )
        if request != normalized:
            raise LifecycleIntegrityError("composite request must be canonical and complete")
        resulting_run = RunLifecycleRecordSchema.model_validate(frame.get("resulting_run"))
        expected_frame = self._build_composite(
            request, runs, attempts, resulting_run.updated_at, frame.get("successor_attempt_id")
        )
        if frame != expected_frame:
            raise LifecycleIntegrityError("composite hash, complete preimage, or member disposition mismatch")
        return resulting_run, tuple(AttemptLifecycleRecordSchema.model_validate(row)
                                   for row in frame["resulting_attempts"])

    @staticmethod
    def _composite_view(frame):
        return LifecycleCompositeTransition(
            operation_key=frame["operation_key"],
            event_ref=f"lifecycle_composite:{frame['operation_key']}:{frame['event_sha256']}",
            event_sha256=frame["event_sha256"],
            resulting_run=RunLifecycleRecordSchema.model_validate(frame["resulting_run"]),
            resulting_attempts=tuple(AttemptLifecycleRecordSchema.model_validate(row)
                                     for row in frame["resulting_attempts"]),
        )

    def _commit_composite(self, request):
        with self._lock, InterProcessFileLock(self.lock_path):
            records, frames = self._decode_history_unlocked()
            for frame in frames:
                prior = frame["request"]
                seed = request["operation_request_key"]
                same_slot = (prior["operation_request_key"] == seed if seed is not None else (
                    prior["operation_request_key"] is None
                    and prior["run_id"] == request["run_id"]
                    and prior["kind"] == request["kind"]
                    and prior["expected_lifecycle_revision"] == request["expected_lifecycle_revision"]
                ))
                if same_slot:
                    if prior != request:
                        raise LifecycleRevisionConflict("composite replay request changed")
                    return self._composite_view(frame)
            runs, attempts = self._load_all_unlocked(records)
            successor = None
            if request["kind"] == "RETRY_WITH_SUCCESSOR":
                successor = request["attempt_id"] or self.id_generator.new_id("attempt")
            frame = self._build_composite(request, runs, attempts, self._now(), successor)
            # Native append writes/fsyncs a single physical line. A torn line has zero members.
            self.log.append_serialized(json.dumps(frame, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")))
            _, committed = self._decode_history_unlocked()
            if not committed or committed[-1] != frame:
                raise LifecycleIntegrityError("composite durable readback mismatch")
            return self._composite_view(frame)

    def terminalize_noncancel_failure(
        self, run_id: str, expected_lifecycle_revision: int,
        target_status: RunLifecycleStatus, reason_ref: str, *,
        triggering_attempt_id: str | None = None,
        expected_attempt_revisions: dict[str, int] | None = None,
        operation_key: str | None = None,
    ) -> LifecycleCompositeTransition:
        """CAS the Run and all owner-derived current Attempts in one journal frame."""
        return self._commit_composite(self._composite_request(
            kind="NONCANCEL_FAILURE", run_id=run_id,
            expected_revision=expected_lifecycle_revision, reason_ref=reason_ref,
            target_status=target_status, triggering_attempt_id=triggering_attempt_id,
            expected_attempt_revisions=expected_attempt_revisions, operation_key=operation_key,
        ))

    def terminalize_document_failure(
        self, run_id: str, expected_lifecycle_revision: int,
        target_status: RunLifecycleStatus, reason_ref: str, *,
        expected_attempt_revisions: dict[str, int] | None = None,
        operation_key: str | None = None,
    ) -> LifecycleCompositeTransition:
        """Fail a proven document execution and cancel its complete fixed preimage."""
        return self._commit_composite(self._composite_request(
            kind="DOCUMENT_NONCANCEL_FAILURE", run_id=run_id,
            expected_revision=expected_lifecycle_revision, reason_ref=reason_ref,
            target_status=target_status,
            expected_attempt_revisions=expected_attempt_revisions, operation_key=operation_key,
        ))

    def resume_retryable_with_successor(
        self, run_id: str, expected_lifecycle_revision: int, attempt_scope_ref: str,
        input_snapshot_version: str, reason_ref: str, *, attempt_id: str | None = None,
        expected_attempt_revisions: dict[str, int] | None = None,
        operation_key: str | None = None,
    ) -> LifecycleCompositeTransition:
        """Resume only into a fresh PENDING successor; activation remains explicit."""
        return self._commit_composite(self._composite_request(
            kind="RETRY_WITH_SUCCESSOR", run_id=run_id,
            expected_revision=expected_lifecycle_revision, reason_ref=reason_ref,
            target_status=RunLifecycleStatus.RUNNING,
            expected_attempt_revisions=expected_attempt_revisions, operation_key=operation_key,
            attempt_scope_ref=attempt_scope_ref, input_snapshot_version=input_snapshot_version,
            attempt_id=attempt_id,
        ))

    @staticmethod
    def _budget_string(value, label):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise LifecycleIntegrityError(f"budget {label} must be a canonical nonempty string")
        return value

    @classmethod
    def _budget_hash(cls, value, label):
        value = cls._budget_string(value, label)
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise LifecycleIntegrityError(f"budget {label} must be a lowercase SHA256")
        return value

    @classmethod
    def _budget_children_payload(cls, children):
        if not isinstance(children, (list, tuple)):
            raise LifecycleIntegrityError("budget complete Child preimage must be a list or tuple")
        try:
            rows = [AttemptLifecycleRecordSchema.model_validate(
                row.model_dump(mode="json", round_trip=True)
                if isinstance(row, AttemptLifecycleRecordSchema) else row
            ) for row in children]
        except Exception as exc:
            raise LifecycleIntegrityError("budget Child preimage contains invalid record") from exc
        if len({row.identity.attempt_id for row in rows}) != len(rows):
            raise LifecycleIntegrityError("budget Child preimage contains duplicate identities")
        rows.sort(key=lambda row: (row.identity.attempt_scope_ref,
                                  row.identity.attempt_ordinal,
                                  row.identity.attempt_id))
        return [cls._record_payload(row) for row in rows]

    @classmethod
    def _budget_request(cls, *, kind, run_id, expected_lifecycle_revision,
                        operation_key, child_preimage_sha256, wait_ref,
                        wait_sha256, semantic_operation_ref,
                        semantic_input_sha256, artifact_version,
                        wait_event_ref=None, wait_event_sha256=None,
                        grant_ref=None, grant_sha256=None):
        if kind not in {"PAUSE", "RESUME"}:
            raise LifecycleIntegrityError("unknown budget lifecycle event kind")
        if type(expected_lifecycle_revision) is not int or expected_lifecycle_revision < 0:
            raise LifecycleIntegrityError("budget expected Run revision must be a nonnegative integer")
        request = {
            "kind": kind,
            "run_id": cls._budget_string(run_id, "run_id"),
            "expected_lifecycle_revision": expected_lifecycle_revision,
            "operation_request_key": cls._budget_string(operation_key, "operation key"),
            "child_preimage_sha256": cls._budget_hash(child_preimage_sha256, "Child preimage SHA256"),
            "wait_ref": cls._budget_string(wait_ref, "wait ref"),
            "wait_sha256": cls._budget_hash(wait_sha256, "wait SHA256"),
            "semantic_operation_ref": cls._budget_string(semantic_operation_ref, "semantic operation ref"),
            "semantic_input_sha256": cls._budget_hash(semantic_input_sha256, "semantic input SHA256"),
            "artifact_version": cls._budget_string(artifact_version, "artifact version"),
            "wait_event_ref": None,
            "wait_event_sha256": None,
            "grant_ref": None,
            "grant_sha256": None,
        }
        if kind == "PAUSE":
            if any(value is not None for value in (wait_event_ref, wait_event_sha256,
                                                  grant_ref, grant_sha256)):
                raise LifecycleIntegrityError("budget PAUSE cannot carry resume/grant authority")
        else:
            request.update({
                "wait_event_ref": cls._budget_string(wait_event_ref, "wait event ref"),
                "wait_event_sha256": cls._budget_hash(wait_event_sha256, "wait event SHA256"),
                "grant_ref": cls._budget_string(grant_ref, "grant ref"),
                "grant_sha256": cls._budget_hash(grant_sha256, "grant SHA256"),
            })
        return request

    @staticmethod
    def _budget_frame_ref(frame):
        return f"lifecycle_budget:{frame['operation_key']}:{frame['event_sha256']}"

    @classmethod
    def _find_budget_frame(cls, frames, event_ref, event_sha256=None):
        event_ref = cls._budget_string(event_ref, "event ref")
        pieces = event_ref.split(":")
        if len(pieces) != 3 or pieces[0] != "lifecycle_budget":
            raise LifecycleIntegrityError("invalid native budget lifecycle event ref")
        cls._budget_hash(pieces[1], "event operation SHA256")
        cls._budget_hash(pieces[2], "event ref SHA256")
        if event_sha256 is not None and cls._budget_hash(event_sha256, "event SHA256") != pieces[2]:
            raise LifecycleIntegrityError("native budget lifecycle event hash disagrees with ref")
        matches = [frame for frame in frames if cls._budget_frame_ref(frame) == event_ref]
        if len(matches) != 1:
            raise LifecycleIntegrityError("exact native budget lifecycle event is absent or ambiguous")
        return matches[0]

    def _build_budget_frame(self, request, runs, attempts, now, budget_frames):
        run = runs.get(request["run_id"])
        if run is None:
            raise LifecycleIntegrityError("budget canonical Run is missing")
        if run.lifecycle_revision != request["expected_lifecycle_revision"]:
            raise LifecycleRevisionConflict("budget Run preimage revision conflict")
        children = self._current_attempts_for_run(attempts, request["run_id"])
        child_payloads = self._budget_children_payload(children)
        if canonical_sha256(child_payloads) != request["child_preimage_sha256"]:
            raise LifecycleRevisionConflict("budget complete current Child preimage conflict")
        if request["kind"] == "PAUSE":
            if run.status is not RunLifecycleStatus.RUNNING:
                raise LifecycleCurrentnessError("budget pause requires RUNNING canonical Run")
            if any(frame["request"]["kind"] == "PAUSE"
                   and frame["request"]["wait_ref"] == request["wait_ref"]
                   and frame["request"]["wait_sha256"] == request["wait_sha256"]
                   for frame in budget_frames):
                raise LifecycleRevisionConflict("one immutable budget wait cannot create another pause")
            target = RunLifecycleStatus.PAUSED
        else:
            if run.status is not RunLifecycleStatus.PAUSED:
                raise LifecycleCurrentnessError("budget resume requires PAUSED canonical Run")
            wait = self._find_budget_frame(budget_frames, request["wait_event_ref"],
                                          request["wait_event_sha256"])
            if wait["request"]["kind"] != "PAUSE" or wait["resulting_run"] != self._record_payload(run):
                raise LifecycleRevisionConflict("budget resume does not bind the exact current wait Run")
            for name in ("run_id", "child_preimage_sha256", "wait_ref", "wait_sha256",
                         "semantic_operation_ref", "semantic_input_sha256", "artifact_version"):
                if wait["request"][name] != request[name]:
                    raise LifecycleRevisionConflict("budget resume changed original wait binding")
            if any(frame["request"]["kind"] == "RESUME"
                   and frame["request"]["wait_event_ref"] == request["wait_event_ref"]
                   for frame in budget_frames):
                raise LifecycleRevisionConflict("budget wait already has a resume occurrence")
            target = RunLifecycleStatus.RUNNING
        updated = RunLifecycleRecordSchema.model_validate(run.model_copy(update={
            "status": target, "lifecycle_revision": run.lifecycle_revision + 1,
            "updated_at": now,
        }, deep=True).model_dump(mode="python", round_trip=True))
        authority = {
            "schema_version": _BUDGET_EVENT_SCHEMA_VERSION,
            "request": request,
            "before_run": self._record_payload(run),
            "before_current_children": child_payloads,
        }
        operation = canonical_sha256(authority)
        frame = {**authority, "operation_key": operation,
                 "resulting_run": self._record_payload(updated),
                 "resume_occurrence_id": (f"budget-resume-occurrence://sha256/{operation}"
                                          if request["kind"] == "RESUME" else None)}
        frame["event_sha256"] = canonical_sha256(frame)
        return frame

    def _validate_budget_frame(self, frame, runs, attempts, budget_frames):
        request = frame.get("request")
        if not isinstance(request, dict):
            raise LifecycleIntegrityError("budget lifecycle request is required")
        normalized = self._budget_request(
            kind=request.get("kind"), run_id=request.get("run_id"),
            expected_lifecycle_revision=request.get("expected_lifecycle_revision"),
            operation_key=request.get("operation_request_key"),
            child_preimage_sha256=request.get("child_preimage_sha256"),
            wait_ref=request.get("wait_ref"), wait_sha256=request.get("wait_sha256"),
            semantic_operation_ref=request.get("semantic_operation_ref"),
            semantic_input_sha256=request.get("semantic_input_sha256"),
            artifact_version=request.get("artifact_version"),
            wait_event_ref=request.get("wait_event_ref"),
            wait_event_sha256=request.get("wait_event_sha256"),
            grant_ref=request.get("grant_ref"), grant_sha256=request.get("grant_sha256"),
        )
        if normalized != request:
            raise LifecycleIntegrityError("budget lifecycle request must be canonical and complete")
        result = RunLifecycleRecordSchema.model_validate(frame.get("resulting_run"))
        if self._build_budget_frame(request, runs, attempts, result.updated_at, budget_frames) != frame:
            raise LifecycleIntegrityError("budget lifecycle event hash/preimage/result mismatch")
        return result

    @classmethod
    def _budget_view(cls, frame):
        request = frame["request"]
        return LifecycleBudgetTransition(
            kind=request["kind"], operation_key=frame["operation_key"],
            event_ref=cls._budget_frame_ref(frame), event_sha256=frame["event_sha256"],
            resulting_run=RunLifecycleRecordSchema.model_validate(frame["resulting_run"]),
            current_children=tuple(AttemptLifecycleRecordSchema.model_validate(row)
                                   for row in frame["before_current_children"]),
            child_preimage_sha256=request["child_preimage_sha256"],
            wait_ref=request["wait_ref"], wait_sha256=request["wait_sha256"],
            semantic_operation_ref=request["semantic_operation_ref"],
            semantic_input_sha256=request["semantic_input_sha256"],
            artifact_version=request["artifact_version"],
            wait_event_ref=request["wait_event_ref"],
            wait_event_sha256=request["wait_event_sha256"],
            grant_ref=request["grant_ref"], grant_sha256=request["grant_sha256"],
            resume_occurrence_id=frame["resume_occurrence_id"],
        )

    def _budget_history_unlocked(self):
        frames = []
        records, _ = self._decode_history_unlocked(budget_frames=frames)
        return records, frames

    def _commit_budget_transition(self, request):
        with self._lock, InterProcessFileLock(self.lock_path):
            records, frames = self._budget_history_unlocked()
            for frame in frames:
                prior = frame["request"]
                if prior["operation_request_key"] == request["operation_request_key"]:
                    if prior != request:
                        raise LifecycleRevisionConflict("budget lifecycle replay request changed")
                    return self._budget_view(frame)
            runs, attempts = self._load_all_unlocked(records)
            frame = self._build_budget_frame(request, runs, attempts, self._now(), frames)
            self.log.append_serialized(json.dumps(frame, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")))
            _, committed = self._budget_history_unlocked()
            if not committed or committed[-1] != frame:
                raise LifecycleIntegrityError("budget lifecycle durable readback mismatch")
            return self._budget_view(frame)

    def current_budget_transition(self, run_id: str) -> LifecycleBudgetTransition | None:
        """Read native current budget authority, never infer from PAUSED status."""
        run_id = self._budget_string(run_id, "run_id")
        with self._lock, InterProcessFileLock(self.lock_path):
            records, frames = self._budget_history_unlocked()
            runs, _ = self._load_all_unlocked(records)
            run = runs.get(run_id)
            if run is None:
                raise LifecycleIntegrityError("budget canonical Run is missing")
            current_payload = self._record_payload(run)
            for frame in reversed(frames):
                if frame["request"]["run_id"] == run_id and frame["resulting_run"] == current_payload:
                    return self._budget_view(frame)
            return None

    def budget_preimage(self, run_id: str) -> LifecycleBudgetPreimage:
        run_id = self._budget_string(run_id, "run_id")
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, attempts = self._load_all_unlocked()
            run = runs.get(run_id)
            if run is None:
                raise LifecycleIntegrityError("budget canonical Run is missing")
            children = tuple(self._current_attempts_for_run(attempts, run_id))
            return LifecycleBudgetPreimage(run, children,
                canonical_sha256(self._budget_children_payload(children)))

    def pause_for_budget(self, run_id: str, expected_lifecycle_revision: int, *,
                         expected_current_children, operation_key: str,
                         wait_ref: str, wait_sha256: str,
                         semantic_operation_ref: str, semantic_input_sha256: str,
                         artifact_version: str) -> LifecycleBudgetTransition:
        return self._commit_budget_transition(self._budget_request(
            kind="PAUSE", run_id=run_id, expected_lifecycle_revision=expected_lifecycle_revision,
            operation_key=operation_key,
            child_preimage_sha256=canonical_sha256(self._budget_children_payload(expected_current_children)),
            wait_ref=wait_ref, wait_sha256=wait_sha256,
            semantic_operation_ref=semantic_operation_ref,
            semantic_input_sha256=semantic_input_sha256, artifact_version=artifact_version,
        ))

    def resume_budget_wait(self, run_id: str, expected_lifecycle_revision: int, *,
                           expected_current_children, operation_key: str,
                           wait_event_ref: str, wait_event_sha256: str,
                           grant_ref: str, grant_sha256: str) -> LifecycleBudgetTransition:
        # Read immutable wait binding first, then CAS again inside commit. This
        # exact native read is not an authorization/currentness fence on its own.
        wait = self.get_budget_transition(wait_event_ref, wait_event_sha256)
        if wait.kind != "PAUSE":
            raise LifecycleIntegrityError("budget resume requires a native PAUSE event")
        return self._commit_budget_transition(self._budget_request(
            kind="RESUME", run_id=run_id, expected_lifecycle_revision=expected_lifecycle_revision,
            operation_key=operation_key,
            child_preimage_sha256=canonical_sha256(self._budget_children_payload(expected_current_children)),
            wait_ref=wait.wait_ref, wait_sha256=wait.wait_sha256,
            semantic_operation_ref=wait.semantic_operation_ref,
            semantic_input_sha256=wait.semantic_input_sha256, artifact_version=wait.artifact_version,
            wait_event_ref=wait_event_ref, wait_event_sha256=wait_event_sha256,
            grant_ref=grant_ref, grant_sha256=grant_sha256,
        ))

    def get_budget_transition(self, event_ref: str,
                              event_sha256: str | None = None) -> LifecycleBudgetTransition:
        with self._lock, InterProcessFileLock(self.lock_path):
            _, frames = self._budget_history_unlocked()
            return self._budget_view(self._find_budget_frame(frames, event_ref, event_sha256))

    @contextmanager
    def budget_authorization_fence(self, *, task_id: str, run_id: str,
                                   expected_lifecycle_revision: int,
                                   wait_event_ref: str, wait_event_sha256: str,
                                   child_preimage_sha256: str,
                                   semantic_operation_ref: str,
                                   semantic_input_sha256: str, artifact_version: str):
        with self._lock, InterProcessFileLock(self.lock_path):
            records, frames = self._budget_history_unlocked()
            wait = self._find_budget_frame(frames, wait_event_ref, wait_event_sha256)
            runs, attempts = self._load_all_unlocked(records)
            run = runs.get(run_id)
            if run is None or run.identity.task_id != task_id or run.status is not RunLifecycleStatus.PAUSED:
                raise LifecycleCurrentnessError("budget authorization requires exact task/PAUSED Run")
            if run.lifecycle_revision != expected_lifecycle_revision:
                raise LifecycleRevisionConflict("budget authorization Run revision conflict")
            current_payloads = self._budget_children_payload(self._current_attempts_for_run(attempts, run_id))
            if (wait["request"]["kind"] != "PAUSE"
                    or wait["resulting_run"] != self._record_payload(run)
                    or wait["before_current_children"] != current_payloads):
                raise LifecycleRevisionConflict("budget authorization wait/current Child preimage conflict")
            bindings = {
                "run_id": run_id, "child_preimage_sha256": child_preimage_sha256,
                "semantic_operation_ref": semantic_operation_ref,
                "semantic_input_sha256": semantic_input_sha256,
                "artifact_version": artifact_version,
            }
            if any(wait["request"][name] != value for name, value in bindings.items()):
                raise LifecycleRevisionConflict("budget authorization changed original wait binding")
            yield self._budget_view(wait)


    def claim_new_run(self, identity: RunIdentitySchema) -> RunLifecycleRecordSchema:
        """Atomically claim a new Run identity; an existing run_id is never resume."""

        identity = RunIdentitySchema.model_validate(identity)
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, _ = self._with_state()
            if identity.run_id in runs:
                raise LifecycleIntegrityError(
                    "run_id is already claimed; explicit existing-Run operation required"
                )
            now = self._now()
            record = RunLifecycleRecordSchema(
                identity=identity,
                status=RunLifecycleStatus.PENDING,
                lifecycle_revision=0,
                created_at=now,
                updated_at=now,
            )
            self._append(entity_type="run", event_type="run_claimed", record=record)
            verified_runs, _ = self._with_state()
            if verified_runs.get(identity.run_id) != record:
                raise OSError("Run identity/lifecycle durable verification failed")
            return record

    def load_run(self, run_id: str) -> RunLifecycleRecordSchema:
        return self.load_run_lifecycle(run_id)

    def load_run_identity(self, run_id: str) -> RunIdentitySchema:
        return self.load_run_lifecycle(run_id).identity

    def load_run_lifecycle(self, run_id: str) -> RunLifecycleRecordSchema:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id is required")
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, _ = self._with_state()
            record = runs.get(normalized)
            if record is None:
                raise KeyError(f"Run lifecycle not found: {normalized}")
            return record

    def load_run_lifecycle_revision(
        self,
        run_id: str,
        lifecycle_revision: int,
    ) -> RunLifecycleRecordSchema:
        normalized = str(run_id or "").strip()
        revision = int(lifecycle_revision)
        if not normalized:
            raise ValueError("run_id is required")
        if revision < 0:
            raise ValueError("lifecycle_revision must be >= 0")
        with self._lock, InterProcessFileLock(self.lock_path):
            records = self._read_records_unlocked()
            self._load_all_unlocked(records)
            matches = [
                record for record in records
                if isinstance(record, RunLifecycleRecordSchema) and (
                    record.identity.run_id == normalized
                    and record.lifecycle_revision == revision
                )
            ]
            if len(matches) != 1:
                raise LifecycleIntegrityError(
                    "exact Run lifecycle revision is missing or ambiguous"
                )
            return matches[0]

    def transition_run(
        self,
        run_id: str,
        expected_lifecycle_revision: int,
        target_status: RunLifecycleStatus,
        reason_ref: str | None = None,
        run_completion_assessment_ref: (
            RunCompletionAssessmentRefSchema | None
        ) = None,
        *,
        run_finalization_barrier: RunFinalizationBarrierSchema | None = None,
    ) -> RunLifecycleRecordSchema:
        target_status = RunLifecycleStatus(target_status)
        assessment_ref = (
            RunCompletionAssessmentRefSchema.model_validate(
                run_completion_assessment_ref
            )
            if run_completion_assessment_ref is not None
            else None
        )
        barrier = (
            RunFinalizationBarrierSchema.model_validate(
                run_finalization_barrier.model_dump(mode="json")
                if isinstance(run_finalization_barrier, RunFinalizationBarrierSchema)
                else run_finalization_barrier
            ) if run_finalization_barrier is not None else None
        )
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, _ = self._with_state()
            current = runs.get(str(run_id or "").strip())
            if current is None:
                raise KeyError(f"Run lifecycle not found: {run_id}")
            if current.lifecycle_revision != expected_lifecycle_revision:
                raise LifecycleRevisionConflict(
                    "Run lifecycle revision conflict: "
                    f"expected={expected_lifecycle_revision} "
                    f"actual={current.lifecycle_revision}"
                )
            if target_status not in _RUN_ALLOWED[current.status]:
                raise LifecycleIntegrityError(
                    f"illegal Run transition: {current.status.value} -> {target_status.value}"
                )

            if target_status is RunLifecycleStatus.FINALIZING:
                if barrier is None or barrier.run_id != current.identity.run_id:
                    raise LifecycleIntegrityError("FINALIZING requires exact Run finalization barrier")
            elif barrier is not None:
                if target_status is not RunLifecycleStatus.SUCCESS or barrier != current.run_finalization_barrier:
                    raise LifecycleIntegrityError("transition cannot replace finalization barrier")
            else:
                barrier = current.run_finalization_barrier
            if target_status is RunLifecycleStatus.RUNNING:
                # The only FINALIZING -> RUNNING edge is explicit abort-finalization.
                barrier = None

            if target_status is RunLifecycleStatus.SUCCESS:
                if assessment_ref is None:
                    raise LifecycleIntegrityError(
                        "SUCCESS transition requires exact RunCompletionAssessmentRef"
                    )
                if self.run_completion_assessment_resolver is None:
                    raise LifecycleIntegrityError(
                        "SUCCESS transition requires committed assessment resolver"
                    )
                try:
                    resolved_assessment = (
                        self.run_completion_assessment_resolver(assessment_ref)
                    )
                    resolved_assessment = RunCompletionAssessmentRecordSchema.model_validate(
                        resolved_assessment
                    )
                except Exception as exc:
                    raise LifecycleIntegrityError(
                        "RunCompletionAssessment cannot be resolved exactly"
                    ) from exc
                if (
                    resolved_assessment.as_ref() != assessment_ref
                    or resolved_assessment.run_id != current.identity.run_id
                    or resolved_assessment.aggregate_result
                    is not RunCompletionAggregateResult.SUCCESS
                ):
                    raise LifecycleIntegrityError(
                        "SUCCESS transition assessment binding is invalid"
                    )
                expected_barrier_ref = current.run_finalization_barrier_ref
                closure_refs = [
                    item for item in resolved_assessment.post_write_mutation_closure_refs
                    if item.source_authority == "RUN_LIFECYCLE"
                    and item.source_kind == "RUN_FINALIZATION_BARRIER"
                ]
                if (
                    len(closure_refs) != 1
                    or closure_refs[0].source_scope_ref != f"run:{current.identity.run_id}"
                    or closure_refs[0].exact_ref_payload != expected_barrier_ref.model_dump(mode="json")
                    or resolved_assessment.post_write_control_decision_ref != barrier.final_control_decision_ref
                ):
                    raise LifecycleIntegrityError("SUCCESS assessment finalization barrier binding is invalid")
            elif assessment_ref is not None:
                raise LifecycleIntegrityError(
                    "non-SUCCESS transition cannot bind completion assessment"
                )

            now = self._now()
            updated = current.model_copy(
                update={
                    "status": target_status,
                    "lifecycle_revision": current.lifecycle_revision + 1,
                    "updated_at": now,
                    "started_at": (
                        current.started_at or now
                        if target_status is RunLifecycleStatus.RUNNING
                        else current.started_at
                    ),
                    "finished_at": now if target_status in RUN_TERMINAL_STATUSES else None,
                    "terminal_reason_ref": (
                        str(reason_ref).strip()
                        if reason_ref is not None and str(reason_ref).strip()
                        else None
                    ),
                    "run_completion_assessment_ref": assessment_ref,
                    "run_finalization_barrier": barrier,
                    "run_finalization_barrier_ref": barrier.as_ref() if barrier else None,
                },
                deep=True,
            )
            updated = RunLifecycleRecordSchema.model_validate(
                updated.model_dump(mode="python", round_trip=True)
            )
            self._append(
                entity_type="run",
                event_type="run_transitioned",
                record=updated,
            )
            return updated

    def assert_run_committable(
        self,
        run_id: str,
        expected_revision: int | None = None,
    ) -> RunLifecycleRecordSchema:
        record = self.load_run_lifecycle(run_id)
        if expected_revision is not None and record.lifecycle_revision != expected_revision:
            raise LifecycleRevisionConflict(
                "Run lifecycle revision changed before commit"
            )
        if record.status is not RunLifecycleStatus.RUNNING:
            raise LifecycleCurrentnessError(
                f"Run is not commit-eligible: {record.status.value}"
            )
        return record

    def open_attempt(
        self,
        run_id: str,
        attempt_scope_ref: str,
        input_snapshot_version: str,
        *,
        attempt_id: str | None = None,
    ) -> AttemptLifecycleRecordSchema:
        """Open a new PENDING Attempt and retire any prior nonterminal current Attempt."""

        normalized_run = str(run_id or "").strip()
        normalized_scope = str(attempt_scope_ref or "").strip()
        normalized_snapshot = str(input_snapshot_version or "").strip()
        if not all((normalized_run, normalized_scope, normalized_snapshot)):
            raise ValueError(
                "run_id, attempt_scope_ref, and input_snapshot_version are required"
            )
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, attempts = self._with_state()
            run = runs.get(normalized_run)
            if run is None:
                raise KeyError(f"Run lifecycle not found: {normalized_run}")
            if run.status is not RunLifecycleStatus.RUNNING:
                raise LifecycleCurrentnessError(
                    "new Attempt requires a RUNNING canonical Run"
                )
            rows = self._scope_attempts(
                attempts,
                run_id=normalized_run,
                attempt_scope_ref=normalized_scope,
            )
            previous = rows[-1] if rows else None
            current = self._current_attempt(
                attempts,
                run_id=normalized_run,
                attempt_scope_ref=normalized_scope,
            )
            now = self._now()
            if current is not None:
                retired = current.model_copy(
                    update={
                        "status": AttemptLifecycleStatus.SUPERSEDED,
                        "lifecycle_revision": current.lifecycle_revision + 1,
                        "finished_at": now,
                    },
                    deep=True,
                )
                retired = AttemptLifecycleRecordSchema.model_validate(
                    retired.model_dump(mode="python", round_trip=True)
                )
                self._append(
                    entity_type="attempt",
                    event_type="attempt_superseded",
                    record=retired,
                )
                attempts[current.identity.attempt_id] = retired

            ordinal = (rows[-1].identity.attempt_ordinal if rows else 0) + 1
            resolved_attempt_id = (
                str(attempt_id or "").strip()
                or self.id_generator.new_id("attempt")
            )
            if resolved_attempt_id in attempts:
                raise LifecycleIntegrityError("attempt_id is already claimed")
            identity = AttemptIdentitySchema(
                attempt_id=resolved_attempt_id,
                run_id=normalized_run,
                attempt_scope_ref=normalized_scope,
                attempt_ordinal=ordinal,
                input_snapshot_version=normalized_snapshot,
            )
            record = AttemptLifecycleRecordSchema(
                identity=identity,
                status=AttemptLifecycleStatus.PENDING,
                lifecycle_revision=0,
                supersedes_attempt_id=(
                    previous.identity.attempt_id if previous is not None else None
                ),
                created_at=now,
            )
            self._append(
                entity_type="attempt",
                event_type="attempt_opened",
                record=record,
            )
            return record

    def load_attempt(self, attempt_id: str) -> AttemptLifecycleRecordSchema:
        normalized = str(attempt_id or "").strip()
        if not normalized:
            raise ValueError("attempt_id is required")
        with self._lock, InterProcessFileLock(self.lock_path):
            _, attempts = self._with_state()
            record = attempts.get(normalized)
            if record is None:
                raise KeyError(f"Attempt lifecycle not found: {normalized}")
            return record

    def load_active_attempt(
        self,
        run_id: str,
        attempt_scope_ref: str,
    ) -> AttemptLifecycleRecordSchema:
        with self._lock, InterProcessFileLock(self.lock_path):
            _, attempts = self._with_state()
            current = self._current_attempt(
                attempts,
                run_id=str(run_id or "").strip(),
                attempt_scope_ref=str(attempt_scope_ref or "").strip(),
            )
            if current is None:
                raise LifecycleCurrentnessError(
                    "no active Attempt exists for Run/scope"
                )
            return current

    def activate_attempt(
        self,
        attempt_id: str,
        expected_lifecycle_revision: int,
    ) -> AttemptLifecycleRecordSchema:
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, attempts = self._with_state()
            current = attempts.get(str(attempt_id or "").strip())
            if current is None:
                raise KeyError(f"Attempt lifecycle not found: {attempt_id}")
            if current.lifecycle_revision != expected_lifecycle_revision:
                raise LifecycleRevisionConflict(
                    "Attempt lifecycle revision conflict"
                )
            if current.status is not AttemptLifecycleStatus.PENDING:
                raise LifecycleIntegrityError(
                    "only a PENDING Attempt can be activated"
                )
            run = runs.get(current.identity.run_id)
            if run is None or run.status is not RunLifecycleStatus.RUNNING:
                raise LifecycleCurrentnessError(
                    "Attempt activation requires RUNNING canonical Run"
                )
            active = self._current_attempt(
                attempts,
                run_id=current.identity.run_id,
                attempt_scope_ref=current.identity.attempt_scope_ref,
            )
            if active is None or active.identity.attempt_id != current.identity.attempt_id:
                raise LifecycleCurrentnessError(
                    "Attempt is not the current scope lineage"
                )
            now = self._now()
            updated = current.model_copy(
                update={
                    "status": AttemptLifecycleStatus.RUNNING,
                    "lifecycle_revision": current.lifecycle_revision + 1,
                    "started_at": now,
                },
                deep=True,
            )
            updated = AttemptLifecycleRecordSchema.model_validate(
                updated.model_dump(mode="python", round_trip=True)
            )
            self._append(
                entity_type="attempt",
                event_type="attempt_activated",
                record=updated,
            )
            return updated

    def transition_attempt(
        self,
        attempt_id: str,
        expected_lifecycle_revision: int,
        target_status: AttemptLifecycleStatus,
        *,
        failure_ref: str | None = None,
    ) -> AttemptLifecycleRecordSchema:
        target_status = AttemptLifecycleStatus(target_status)
        if target_status is AttemptLifecycleStatus.RUNNING:
            return self.activate_attempt(attempt_id, expected_lifecycle_revision)

        with self._lock, InterProcessFileLock(self.lock_path):
            runs, attempts = self._with_state()
            current = attempts.get(str(attempt_id or "").strip())
            if current is None:
                raise KeyError(f"Attempt lifecycle not found: {attempt_id}")
            if current.lifecycle_revision != expected_lifecycle_revision:
                raise LifecycleRevisionConflict(
                    "Attempt lifecycle revision conflict"
                )
            run = runs.get(current.identity.run_id)
            if run is None or run.status is not RunLifecycleStatus.RUNNING:
                raise LifecycleCurrentnessError(
                    "Attempt transition requires RUNNING canonical Run"
                )
            if target_status not in _ATTEMPT_ALLOWED[current.status]:
                raise LifecycleIntegrityError(
                    "illegal Attempt transition: "
                    f"{current.status.value} -> {target_status.value}"
                )
            if current.status is AttemptLifecycleStatus.RUNNING:
                active = self._current_attempt(
                    attempts,
                    run_id=current.identity.run_id,
                    attempt_scope_ref=current.identity.attempt_scope_ref,
                )
                if (
                    active is None
                    or active.identity.attempt_id != current.identity.attempt_id
                ):
                    raise LifecycleCurrentnessError(
                        "stale Attempt cannot transition canonical active truth"
                    )
            now = self._now()
            updated = current.model_copy(
                update={
                    "status": target_status,
                    "lifecycle_revision": current.lifecycle_revision + 1,
                    "finished_at": (
                        now if target_status in ATTEMPT_TERMINAL_STATUSES else None
                    ),
                    "failure_ref": (
                        str(failure_ref).strip()
                        if failure_ref is not None and str(failure_ref).strip()
                        else None
                    ),
                },
                deep=True,
            )
            updated = AttemptLifecycleRecordSchema.model_validate(
                updated.model_dump(mode="python", round_trip=True)
            )
            self._append(
                entity_type="attempt",
                event_type="attempt_transitioned",
                record=updated,
            )
            return updated

    def load_latest_attempt(self, run_id: str, attempt_scope_ref: str) -> AttemptLifecycleRecordSchema | None:
        """Read the canonical latest scope occurrence, including terminal history."""
        with self._lock, InterProcessFileLock(self.lock_path):
            _, attempts = self._with_state()
            rows = self._scope_attempts(attempts, run_id=run_id, attempt_scope_ref=attempt_scope_ref)
            return rows[-1] if rows else None

    def resolve_read_lineage(self, run_id: str, attempt_scope_ref: str, *,
                             expected_attempt_id: str) -> ActiveExecutionLineageSchema:
        """Exact current source read in RUNNING/FINALIZING/SUCCESS; no commit grant."""
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, attempts = self._with_state()
            run = runs.get(run_id)
            rows = self._scope_attempts(attempts, run_id=run_id, attempt_scope_ref=attempt_scope_ref)
            attempt = rows[-1] if rows else None
            if (run is None or run.status not in {RunLifecycleStatus.RUNNING, RunLifecycleStatus.FINALIZING, RunLifecycleStatus.SUCCESS}
                    or attempt is None or attempt.identity.attempt_id != expected_attempt_id
                    or attempt.status not in {AttemptLifecycleStatus.RUNNING, AttemptLifecycleStatus.COMPLETED}):
                raise LifecycleCurrentnessError("source Attempt is not latest/read-current for Run/scope")
            return ActiveExecutionLineageSchema(
                run_id=run_id, run_lifecycle_revision=run.lifecycle_revision, run_status=run.status,
                attempt_scope_ref=attempt_scope_ref, attempt_id=attempt.identity.attempt_id,
                attempt_lifecycle_revision=attempt.lifecycle_revision, attempt_status=attempt.status,
                input_snapshot_version=attempt.identity.input_snapshot_version,
            )

    def resolve_active_lineage(
        self,
        run_id: str,
        attempt_scope_ref: str,
    ) -> ActiveExecutionLineageSchema:
        with self._lock, InterProcessFileLock(self.lock_path):
            runs, attempts = self._with_state()
            normalized_run = str(run_id or "").strip()
            normalized_scope = str(attempt_scope_ref or "").strip()
            run = runs.get(normalized_run)
            if run is None:
                raise LifecycleIntegrityError("Run lifecycle is unresolved")
            attempt = self._current_attempt(
                attempts,
                run_id=normalized_run,
                attempt_scope_ref=normalized_scope,
            )
            if attempt is None:
                raise LifecycleCurrentnessError(
                    "active Attempt lineage is unresolved"
                )
            return ActiveExecutionLineageSchema(
                run_id=run.identity.run_id,
                run_lifecycle_revision=run.lifecycle_revision,
                run_status=run.status,
                attempt_scope_ref=attempt.identity.attempt_scope_ref,
                attempt_id=attempt.identity.attempt_id,
                attempt_lifecycle_revision=attempt.lifecycle_revision,
                attempt_status=attempt.status,
                input_snapshot_version=attempt.identity.input_snapshot_version,
            )

    def assert_attempt_committable(
        self,
        *,
        run_id: str,
        attempt_scope_ref: str,
        attempt_id: str,
        input_snapshot_version: str,
    ) -> ActiveExecutionLineageSchema:
        lineage = self.resolve_active_lineage(run_id, attempt_scope_ref)
        violations: list[str] = []
        if lineage.run_status is not RunLifecycleStatus.RUNNING:
            violations.append("run_status")
        if lineage.attempt_status is not AttemptLifecycleStatus.RUNNING:
            violations.append("attempt_status")
        if lineage.attempt_id != str(attempt_id or "").strip():
            violations.append("attempt_id")
        if lineage.input_snapshot_version != str(input_snapshot_version or "").strip():
            violations.append("input_snapshot_version")
        if violations:
            raise LifecycleCurrentnessError(
                "Attempt is not commit-eligible: " + ", ".join(violations)
            )
        return lineage


__all__ = [
    "LifecycleBudgetPreimage",
    "LifecycleBudgetTransition",
    "LifecycleCurrentnessError",
    "LifecycleIntegrityError",
    "LifecycleRevisionConflict",
    "RunAttemptLifecycleService",
]
