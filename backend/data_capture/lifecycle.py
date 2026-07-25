"""Event-sourced lifecycle authority for captured runtime samples."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict

from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from schemas.data_capture import DataCaptureRecordSchema


CaptureStage = Literal[
    "raw",
    "candidate",
    "evaluated",
    "accepted",
    "rejected",
    "runtime_rejected",
    "training_ready",
    "eval_ready",
]
RuntimeCaptureStage = Literal["raw", "candidate", "runtime_rejected"]
PolicyDecision = Literal["accepted", "rejected"]


class CaptureTransitionEvent(BaseModel):
    """Canonical event for one initialization or lifecycle transition."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["capture_lifecycle_event_v2"] = (
        "capture_lifecycle_event_v2"
    )
    event_id: str
    record_id: str
    task_id: str
    run_id: str
    revision: int
    event_type: Literal["initialized", "transitioned"]
    from_state: CaptureStage | None
    to_state: CaptureStage
    timestamp: str
    reason: str
    authority: str
    policy_decision: PolicyDecision | None = None
    quality_score: float | None = None
    snapshot: DataCaptureRecordSchema


# Historical public name retained as a direct alias to the one canonical event
# contract; it does not create a second persistence format.
CaptureTransitionAuditRecord = CaptureTransitionEvent


class DataCaptureLifecycleStore:
    """Own lifecycle truth as an append-only event log.

    ``events`` is canonical. ``current`` is only a rebuildable projection, so
    a projection write failure can never split lifecycle truth from history.
    All promoted states are produced by ``transition``; the runtime recorder
    can only create raw, candidate, or runtime_rejected source records.
    """

    _LOCKS_GUARD = threading.Lock()
    _ROOT_LOCKS: dict[str, threading.RLock] = {}

    def __init__(self, capture_root: str | Path) -> None:
        self.capture_root = Path(capture_root).expanduser().resolve()
        self.root = self.capture_root / "lifecycle"
        self.current_dir = self.root / "current"
        self.events_dir = self.root / "events"
        self.current_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        lock_key = str(self.root)
        with self._LOCKS_GUARD:
            self._lock = self._ROOT_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )

    def _current_path(self, record_id: str) -> Path:
        return self.current_dir / f"{record_id}.json"

    def _event_path(self, record_id: str) -> Path:
        return self.events_dir / f"{record_id}.jsonl"

    @staticmethod
    def _event_id(record_id: str, revision: int, to_state: str) -> str:
        digest = hashlib.sha256(
            f"{record_id}:{revision}:{to_state}".encode("utf-8")
        ).hexdigest()[:24]
        return f"capture_event_{digest}"

    def _read_events_unlocked(self, record_id: str) -> list[CaptureTransitionEvent]:
        path = self._event_path(record_id)
        if not path.is_file():
            raise FileNotFoundError(f"capture lifecycle event log not found: {path}")
        events: list[CaptureTransitionEvent] = []
        for line_number, line in enumerate(
            CrashSafeJsonlEventLog(path).read_complete_lines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                event = CaptureTransitionEvent.model_validate_json(line, strict=True)
            except Exception as exc:
                raise ValueError(
                    f"invalid capture lifecycle event at {path}:{line_number}"
                ) from exc
            expected_revision = len(events)
            if event.record_id != record_id or event.revision != expected_revision:
                raise ValueError(
                    "capture lifecycle event sequence is not contiguous: "
                    f"record_id={record_id} expected_revision={expected_revision} "
                    f"actual_revision={event.revision}"
                )
            if event.snapshot.lifecycle_revision != event.revision:
                raise ValueError("capture lifecycle snapshot revision mismatch")
            if events:
                previous = events[-1]
                if (
                    event.from_state != previous.to_state
                    or event.task_id != previous.task_id
                    or event.run_id != previous.run_id
                ):
                    raise ValueError("capture lifecycle event identity/state mismatch")
            elif event.event_type != "initialized" or event.from_state is not None:
                raise ValueError("capture lifecycle log must start with initialization")
            events.append(event)
        if not events:
            raise ValueError(f"capture lifecycle event log is empty: {path}")
        return events

    def _append_event(self, event: CaptureTransitionEvent) -> None:
        path = self._event_path(event.record_id)
        serialized = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        CrashSafeJsonlEventLog(path).append_serialized(serialized)

    def _write_current_atomic(self, record: DataCaptureRecordSchema) -> Path:
        payload = record.model_dump(mode="json")
        current_path = self._current_path(record.record_id)
        temp_path = current_path.with_name(
            f".{current_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, current_path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        return current_path

    def _repair_projection_best_effort(
        self,
        record: DataCaptureRecordSchema,
    ) -> None:
        try:
            self._write_current_atomic(record)
        except OSError:
            # The canonical event has already been durably appended. Readers
            # replay it and a later load/recovery will rebuild this projection.
            return

    def _load_current_unlocked(self, record_id: str) -> DataCaptureRecordSchema:
        current = self._read_events_unlocked(record_id)[-1].snapshot
        projection_path = self._current_path(record_id)
        projection_matches = False
        if projection_path.is_file():
            try:
                projection = DataCaptureRecordSchema.model_validate_json(
                    projection_path.read_text(encoding="utf-8"),
                    strict=True,
                )
                projection_matches = (
                    projection.model_dump(mode="json")
                    == current.model_dump(mode="json")
                )
            except Exception:
                projection_matches = False
        if not projection_matches:
            self._repair_projection_best_effort(current)
        return current

    def initialize(self, record: DataCaptureRecordSchema) -> DataCaptureRecordSchema:
        """Initialize canonical truth exactly once from a runtime-owned stage."""

        if record.capture_stage not in {"raw", "candidate", "runtime_rejected"}:
            raise ValueError(
                "canonical lifecycle may only initialize from raw, candidate, "
                "or runtime_rejected"
            )
        with self._lock:
            event_path = self._event_path(record.record_id)
            if event_path.is_file():
                committed_lines = CrashSafeJsonlEventLog(
                    event_path
                ).read_complete_lines()
                if any(line.strip() for line in committed_lines):
                    current = self._load_current_unlocked(record.record_id)
                    for field in ("record_id", "task_id", "run_id", "capture_type"):
                        if getattr(current, field) != getattr(record, field):
                            raise ValueError(
                                "capture lifecycle identity mismatch for existing truth: "
                                f"record_id={record.record_id} field={field}"
                            )
                    return current

            initialized = record.model_copy(update={"lifecycle_revision": 0})
            event = CaptureTransitionEvent(
                event_id=self._event_id(
                    initialized.record_id,
                    0,
                    initialized.capture_stage,
                ),
                record_id=initialized.record_id,
                task_id=initialized.task_id,
                run_id=initialized.run_id,
                revision=0,
                event_type="initialized",
                from_state=None,
                to_state=initialized.capture_stage,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reason="runtime_capture_initialized",
                authority="DataCaptureLifecycleStore",
                snapshot=initialized,
            )
            self._append_event(event)
            self._repair_projection_best_effort(initialized)
            return initialized

    def transition(
        self,
        record_id: str,
        target_stage: CaptureStage,
        *,
        transition_service: "DataCaptureTransitionService",
        reason: str,
        authority: str,
        policy_decision: PolicyDecision | None = None,
        quality_score: float | None = None,
        expected_revision: int | None = None,
    ) -> DataCaptureRecordSchema:
        """Validate and append one canonical transition with optimistic CAS."""

        if not reason.strip() or not authority.strip():
            raise ValueError("capture transition requires non-empty reason and authority")
        with self._lock:
            current = self._load_current_unlocked(record_id)
            if current.capture_stage == target_stage:
                # Deterministic retry after a committed transition is idempotent.
                return current
            if (
                expected_revision is not None
                and current.lifecycle_revision != expected_revision
            ):
                raise ValueError(
                    "capture lifecycle revision conflict: "
                    f"expected={expected_revision} actual={current.lifecycle_revision}"
                )
            updated = transition_service.transition(
                current,
                target_stage,
                policy_decision=policy_decision,
                quality_score=quality_score,
            ).model_copy(
                update={"lifecycle_revision": current.lifecycle_revision + 1}
            )
            event = CaptureTransitionEvent(
                event_id=self._event_id(
                    updated.record_id,
                    updated.lifecycle_revision,
                    updated.capture_stage,
                ),
                record_id=updated.record_id,
                task_id=updated.task_id,
                run_id=updated.run_id,
                revision=updated.lifecycle_revision,
                event_type="transitioned",
                from_state=current.capture_stage,
                to_state=updated.capture_stage,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reason=reason,
                authority=authority,
                policy_decision=policy_decision,
                quality_score=quality_score,
                snapshot=updated,
            )
            self._append_event(event)
            self._repair_projection_best_effort(updated)
            return updated

    def read_transition_history(self, run_id: str) -> list[CaptureTransitionEvent]:
        records: list[CaptureTransitionEvent] = []
        with self._lock:
            for path in sorted(self.events_dir.glob("*.jsonl")):
                events = self._read_events_unlocked(path.stem)
                records.extend(
                    event
                    for event in events
                    if event.run_id == run_id and event.event_type == "transitioned"
                )
        return sorted(records, key=lambda item: (item.timestamp, item.record_id, item.revision))

    def load_current(self, record_id: str) -> DataCaptureRecordSchema:
        with self._lock:
            return self._load_current_unlocked(record_id)

    def iter_stage(self, stage: CaptureStage) -> Iterable[DataCaptureRecordSchema]:
        with self._lock:
            record_ids = [path.stem for path in sorted(self.events_dir.glob("*.jsonl"))]
            records = [self._load_current_unlocked(record_id) for record_id in record_ids]
        yield from (record for record in records if record.capture_stage == stage)


class DataCaptureTransitionService:
    """Apply the only allowed promotion graph; it owns no quality policy."""

    _ALLOWED: dict[str, set[str]] = {
        "raw": {"candidate"},
        "candidate": {"evaluated"},
        "evaluated": {"accepted", "rejected"},
        "accepted": {"training_ready", "eval_ready"},
        "rejected": set(),
        "runtime_rejected": set(),
        "training_ready": set(),
        "eval_ready": set(),
    }

    def transition(
        self,
        record: DataCaptureRecordSchema,
        target_stage: CaptureStage,
        *,
        policy_decision: PolicyDecision | None = None,
        quality_score: float | None = None,
    ) -> DataCaptureRecordSchema:
        current = str(record.capture_stage)
        target = str(target_stage)
        if target not in self._ALLOWED.get(current, set()):
            raise ValueError(f"invalid capture transition: {current} -> {target}")

        if current == "evaluated":
            if policy_decision is None:
                raise ValueError("evaluated -> policy outcome requires policy_decision")
            if target != policy_decision:
                raise ValueError(
                    "target stage must match external policy_decision for evaluated sample"
                )
        elif policy_decision is not None:
            raise ValueError("policy_decision is only accepted for evaluated policy outcome")

        metadata = dict(record.metadata or {})
        if target == "evaluated":
            metadata["quality_decision"] = "pending_policy_decision"
        elif target in {"accepted", "rejected"}:
            metadata["quality_decision"] = target
        elif target in {"training_ready", "eval_ready"}:
            if metadata.get("quality_decision") != "accepted":
                raise ValueError("readiness promotion requires prior accepted policy decision")
            metadata["promotion_target"] = target

        payload = record.model_dump()
        payload.update(
            {
                "capture_stage": target,
                "metadata": metadata,
                "is_usable_for_training": target == "training_ready",
                "is_usable_for_eval": target == "eval_ready",
            }
        )
        if quality_score is not None:
            payload["quality_score"] = quality_score
        return DataCaptureRecordSchema.model_validate(payload, strict=True)
