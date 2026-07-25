"""Claim-based commit ledger for workflow-owned external side effects."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog


class SideEffectClaimStatus(str, Enum):
    """Outcome of atomically requesting execution authority."""

    ACQUIRED = "acquired"
    ALREADY_COMMITTED = "already_committed"
    ALREADY_IN_PROGRESS = "already_in_progress"


class SideEffectIdentityConflict(ValueError):
    """The same logical ID was presented with a different semantic payload."""


@dataclass(frozen=True)
class SideEffectClaim:
    """Execution authority for one logical side effect."""

    status: SideEffectClaimStatus
    side_effect_id: str
    execution_id: str
    description: str
    claim_token: str | None = None
    semantic_fingerprint: str | None = None

    @property
    def acquired(self) -> bool:
        return self.status == SideEffectClaimStatus.ACQUIRED


class SideEffectCommitStore(Protocol):
    """Atomic authority and acknowledgement port for logical side effects."""

    def claim(
        self,
        side_effect_id: str,
        *,
        execution_id: str,
        description: str,
        semantic_fingerprint: str | None = None,
    ) -> SideEffectClaim: ...

    def mark_committed(self, claim: SideEffectClaim) -> bool: ...

    def release(self, claim: SideEffectClaim, *, reason: str) -> bool: ...

    def wait_for_resolution(
        self,
        side_effect_id: str,
        *,
        timeout_seconds: float,
    ) -> None: ...

    def finish_execution(self, claim: SideEffectClaim) -> None: ...


@dataclass(frozen=True)
class _SideEffectState:
    state: str
    claim_token: str | None
    execution_id: str | None
    lease_expires_at: float | None
    semantic_fingerprint: str | None


@dataclass
class _LocalExecutionOwner:
    """Live process-local executor, separate from persistent claim state."""

    thread: threading.Thread
    active: bool = True

    @property
    def is_live(self) -> bool:
        return self.active and self.thread.is_alive()


def _validate_identity(
    side_effect_id: str,
    execution_id: str,
    description: str,
) -> tuple[str, str, str]:
    values = (
        str(side_effect_id or "").strip(),
        str(execution_id or "").strip(),
        str(description or "").strip(),
    )
    if not all(values):
        raise ValueError(
            "side_effect_id, execution_id, and description must be non-empty"
        )
    return values


def _normalize_semantic_fingerprint(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _assert_semantic_identity(
    *,
    side_effect_id: str,
    existing: str | None,
    incoming: str | None,
) -> None:
    if existing == incoming:
        return
    raise SideEffectIdentityConflict(
        "side-effect logical identity collision with different or unverifiable "
        f"semantic payload: {side_effect_id}"
    )


class InMemorySideEffectCommitStore:
    """Process-local atomic claim state used by isolated/custom engines."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._states: dict[str, _SideEffectState] = {}

    def claim(
        self,
        side_effect_id: str,
        *,
        execution_id: str,
        description: str,
        semantic_fingerprint: str | None = None,
    ) -> SideEffectClaim:
        side_effect_id, execution_id, description = _validate_identity(
            side_effect_id,
            execution_id,
            description,
        )
        semantic_fingerprint = _normalize_semantic_fingerprint(
            semantic_fingerprint
        )
        with self._lock:
            current = self._states.get(side_effect_id)
            if current is not None:
                _assert_semantic_identity(
                    side_effect_id=side_effect_id,
                    existing=current.semantic_fingerprint,
                    incoming=semantic_fingerprint,
                )
            if current is not None and current.state == "committed":
                return SideEffectClaim(
                    SideEffectClaimStatus.ALREADY_COMMITTED,
                    side_effect_id,
                    execution_id,
                    description,
                    semantic_fingerprint=semantic_fingerprint,
                )
            if current is not None and current.state == "in_progress":
                return SideEffectClaim(
                    SideEffectClaimStatus.ALREADY_IN_PROGRESS,
                    side_effect_id,
                    execution_id,
                    description,
                    semantic_fingerprint=semantic_fingerprint,
                )
            token = uuid.uuid4().hex
            self._states[side_effect_id] = _SideEffectState(
                state="in_progress",
                claim_token=token,
                execution_id=execution_id,
                lease_expires_at=None,
                semantic_fingerprint=semantic_fingerprint,
            )
            return SideEffectClaim(
                SideEffectClaimStatus.ACQUIRED,
                side_effect_id,
                execution_id,
                description,
                token,
                semantic_fingerprint,
            )

    def mark_committed(self, claim: SideEffectClaim) -> bool:
        if not claim.acquired or not claim.claim_token:
            raise ValueError("only an acquired claim can be committed")
        with self._lock:
            current = self._states.get(claim.side_effect_id)
            if current is not None and current.state == "committed":
                return False
            if (
                current is None
                or current.state != "in_progress"
                or current.claim_token != claim.claim_token
                or current.semantic_fingerprint != claim.semantic_fingerprint
            ):
                raise ValueError("side-effect claim is not the active owner")
            self._states[claim.side_effect_id] = _SideEffectState(
                state="committed",
                claim_token=claim.claim_token,
                execution_id=claim.execution_id,
                lease_expires_at=None,
                semantic_fingerprint=claim.semantic_fingerprint,
            )
            self._condition.notify_all()
            return True

    def release(self, claim: SideEffectClaim, *, reason: str) -> bool:
        del reason
        if not claim.acquired or not claim.claim_token:
            return False
        with self._lock:
            current = self._states.get(claim.side_effect_id)
            if (
                current is None
                or current.state != "in_progress"
                or current.claim_token != claim.claim_token
            ):
                return False
            del self._states[claim.side_effect_id]
            self._condition.notify_all()
            return True

    def wait_for_resolution(
        self,
        side_effect_id: str,
        *,
        timeout_seconds: float,
    ) -> None:
        if timeout_seconds <= 0:
            return
        with self._condition:
            current = self._states.get(side_effect_id)
            if current is not None and current.state == "in_progress":
                self._condition.wait(timeout=float(timeout_seconds))

    def finish_execution(self, claim: SideEffectClaim) -> None:
        del claim
        with self._condition:
            self._condition.notify_all()


class JsonlSideEffectCommitStore:
    """Persistent claim/commit state under the local single-process model.

    The shared path lock makes claim acquisition atomic across threads and
    store instances in this process. Claim events make ownership explicit and
    reloadable. A lease permits eventual recovery of a claim orphaned by a
    process crash. This is not a distributed lock: multi-process deployments
    must use a transactional store with a unique-key claim operation.

    Production adapters must also apply ``side_effect_id`` as their own
    idempotency key. That closes the unavoidable window between an external
    write succeeding and its local commit acknowledgement.
    """

    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS: dict[str, threading.RLock] = {}
    _PATH_CONDITIONS: dict[str, threading.Condition] = {}
    _ACTIVE_CLAIMS: dict[str, dict[str, _LocalExecutionOwner]] = {}

    def __init__(
        self,
        path: str | Path,
        *,
        claim_lease_seconds: float = 300.0,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("claim_lease_seconds must be positive")
        self.path = Path(path)
        self.log = CrashSafeJsonlEventLog(self.path)
        self.claim_lease_seconds = float(claim_lease_seconds)
        lock_key = str(self.path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._PATH_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )
            self._condition = self._PATH_CONDITIONS.setdefault(
                lock_key,
                threading.Condition(self._lock),
            )
            self._local_execution_owners = self._ACTIVE_CLAIMS.setdefault(
                lock_key,
                {},
            )
        self._states: dict[str, _SideEffectState] = {}
        with self._lock:
            self._reload_unlocked()

    def _reload_unlocked(self) -> None:
        states: dict[str, _SideEffectState] = {}
        for line_number, line in enumerate(
            self.log.read_complete_lines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                schema_version = payload["schema_version"]
                side_effect_id = payload["side_effect_id"]
            except Exception as exc:
                raise ValueError(
                    f"invalid side-effect ledger event at {self.path}:{line_number}"
                ) from exc
            if not isinstance(side_effect_id, str) or not side_effect_id:
                raise ValueError("side-effect ledger id must be a non-empty string")

            # Internal compatibility for already-committed v1 ledgers. It does
            # not manufacture an in-progress claim or rerun an old callback.
            if schema_version == "side_effect_commit_v1":
                if payload.get("state") != "committed":
                    raise ValueError("invalid legacy side-effect commit state")
                states[side_effect_id] = _SideEffectState(
                    state="committed",
                    claim_token=None,
                    execution_id=str(payload.get("execution_id") or ""),
                    lease_expires_at=None,
                    semantic_fingerprint=None,
                )
                continue
            if schema_version != "side_effect_ledger_event_v2":
                raise ValueError("unknown side-effect ledger schema version")

            event_type = payload.get("event_type")
            token = payload.get("claim_token")
            if not isinstance(token, str) or not token:
                raise ValueError("side-effect ledger claim_token is required")
            current = states.get(side_effect_id)
            if event_type == "claimed":
                if current is not None and current.state in {
                    "in_progress",
                    "committed",
                }:
                    raise ValueError("invalid duplicate side-effect claim history")
                lease_expires_at = payload.get("lease_expires_at")
                if not isinstance(lease_expires_at, (int, float)):
                    raise ValueError("side-effect claim lease is invalid")
                semantic_fingerprint = _normalize_semantic_fingerprint(
                    payload.get("semantic_fingerprint")
                )
                states[side_effect_id] = _SideEffectState(
                    state="in_progress",
                    claim_token=token,
                    execution_id=str(payload.get("execution_id") or ""),
                    lease_expires_at=float(lease_expires_at),
                    semantic_fingerprint=semantic_fingerprint,
                )
            elif event_type == "released":
                if (
                    current is None
                    or current.state != "in_progress"
                    or current.claim_token != token
                ):
                    raise ValueError("side-effect release has no matching claim")
                states.pop(side_effect_id, None)
            elif event_type == "committed":
                if (
                    current is None
                    or current.state != "in_progress"
                    or current.claim_token != token
                ):
                    raise ValueError("side-effect commit has no matching claim")
                _assert_semantic_identity(
                    side_effect_id=side_effect_id,
                    existing=current.semantic_fingerprint,
                    incoming=_normalize_semantic_fingerprint(
                        payload.get("semantic_fingerprint")
                    ),
                )
                states[side_effect_id] = _SideEffectState(
                    state="committed",
                    claim_token=token,
                    execution_id=current.execution_id,
                    lease_expires_at=None,
                    semantic_fingerprint=current.semantic_fingerprint,
                )
            else:
                raise ValueError("unknown side-effect ledger event_type")
        self._states = states

    def _append_unlocked(self, payload: dict[str, Any]) -> None:
        self.log.append_serialized(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _release_stale_claim_unlocked(
        self,
        side_effect_id: str,
        current: _SideEffectState,
    ) -> None:
        self._append_unlocked(
            {
                "schema_version": "side_effect_ledger_event_v2",
                "event_type": "released",
                "side_effect_id": side_effect_id,
                "claim_token": current.claim_token,
                "execution_id": current.execution_id,
                "semantic_fingerprint": current.semantic_fingerprint,
                "reason": "claim_lease_expired",
                "timestamp_epoch": time.time(),
            }
        )
        self._states.pop(side_effect_id, None)
        if current.claim_token:
            self._local_execution_owners.pop(current.claim_token, None)

    def _has_live_local_owner_unlocked(self, claim_token: str | None) -> bool:
        if not claim_token:
            return False
        owner = self._local_execution_owners.get(claim_token)
        if owner is None:
            return False
        if owner.is_live:
            return True
        owner.active = False
        self._local_execution_owners.pop(claim_token, None)
        return False

    def claim(
        self,
        side_effect_id: str,
        *,
        execution_id: str,
        description: str,
        semantic_fingerprint: str | None = None,
    ) -> SideEffectClaim:
        side_effect_id, execution_id, description = _validate_identity(
            side_effect_id,
            execution_id,
            description,
        )
        semantic_fingerprint = _normalize_semantic_fingerprint(
            semantic_fingerprint
        )
        with self._lock:
            self._reload_unlocked()
            current = self._states.get(side_effect_id)
            if current is not None:
                _assert_semantic_identity(
                    side_effect_id=side_effect_id,
                    existing=current.semantic_fingerprint,
                    incoming=semantic_fingerprint,
                )
            if current is not None and current.state == "committed":
                return SideEffectClaim(
                    SideEffectClaimStatus.ALREADY_COMMITTED,
                    side_effect_id,
                    execution_id,
                    description,
                    semantic_fingerprint=semantic_fingerprint,
                )
            now = time.time()
            if current is not None and current.state == "in_progress":
                lease_expired = (
                    current.lease_expires_at is not None
                    and current.lease_expires_at <= now
                )
                local_owner_live = self._has_live_local_owner_unlocked(
                    current.claim_token
                )
                if lease_expired and not local_owner_live:
                    self._release_stale_claim_unlocked(side_effect_id, current)
                else:
                    return SideEffectClaim(
                        SideEffectClaimStatus.ALREADY_IN_PROGRESS,
                        side_effect_id,
                        execution_id,
                        description,
                        semantic_fingerprint=semantic_fingerprint,
                    )

            token = uuid.uuid4().hex
            lease_expires_at = now + self.claim_lease_seconds
            self._append_unlocked(
                {
                    "schema_version": "side_effect_ledger_event_v2",
                    "event_type": "claimed",
                    "side_effect_id": side_effect_id,
                    "claim_token": token,
                    "execution_id": execution_id,
                    "description": description,
                    "semantic_fingerprint": semantic_fingerprint,
                    "claimed_at_epoch": now,
                    "lease_expires_at": lease_expires_at,
                }
            )
            self._states[side_effect_id] = _SideEffectState(
                state="in_progress",
                claim_token=token,
                execution_id=execution_id,
                lease_expires_at=lease_expires_at,
                semantic_fingerprint=semantic_fingerprint,
            )
            self._local_execution_owners[token] = _LocalExecutionOwner(
                thread=threading.current_thread()
            )
            return SideEffectClaim(
                SideEffectClaimStatus.ACQUIRED,
                side_effect_id,
                execution_id,
                description,
                token,
                semantic_fingerprint,
            )

    def mark_committed(self, claim: SideEffectClaim) -> bool:
        if not claim.acquired or not claim.claim_token:
            raise ValueError("only an acquired claim can be committed")
        with self._lock:
            self._reload_unlocked()
            current = self._states.get(claim.side_effect_id)
            if current is not None and current.state == "committed":
                return False
            if (
                current is None
                or current.state != "in_progress"
                or current.claim_token != claim.claim_token
                or current.semantic_fingerprint != claim.semantic_fingerprint
            ):
                raise ValueError("side-effect claim is not the active owner")
            self._append_unlocked(
                {
                    "schema_version": "side_effect_ledger_event_v2",
                    "event_type": "committed",
                    "side_effect_id": claim.side_effect_id,
                    "claim_token": claim.claim_token,
                    "execution_id": claim.execution_id,
                    "description": claim.description,
                    "semantic_fingerprint": claim.semantic_fingerprint,
                    "timestamp_epoch": time.time(),
                }
            )
            self._states[claim.side_effect_id] = _SideEffectState(
                state="committed",
                claim_token=claim.claim_token,
                execution_id=claim.execution_id,
                lease_expires_at=None,
                semantic_fingerprint=claim.semantic_fingerprint,
            )
            self._local_execution_owners.pop(claim.claim_token, None)
            self._condition.notify_all()
            return True

    def release(self, claim: SideEffectClaim, *, reason: str) -> bool:
        if not claim.acquired or not claim.claim_token:
            return False
        with self._lock:
            self._reload_unlocked()
            current = self._states.get(claim.side_effect_id)
            if (
                current is None
                or current.state != "in_progress"
                or current.claim_token != claim.claim_token
            ):
                return False
            self._append_unlocked(
                {
                    "schema_version": "side_effect_ledger_event_v2",
                    "event_type": "released",
                    "side_effect_id": claim.side_effect_id,
                    "claim_token": claim.claim_token,
                    "execution_id": claim.execution_id,
                    "semantic_fingerprint": claim.semantic_fingerprint,
                    "reason": str(reason or "execution_failed"),
                    "timestamp_epoch": time.time(),
                }
            )
            self._states.pop(claim.side_effect_id, None)
            self._local_execution_owners.pop(claim.claim_token, None)
            self._condition.notify_all()
            return True

    def wait_for_resolution(
        self,
        side_effect_id: str,
        *,
        timeout_seconds: float,
    ) -> None:
        """Wait until completion, release, lease recovery, or caller deadline.

        The condition handles normal same-process completion. The short live
        owner recheck also detects a thread that exited without running its
        cleanup path. An orphaned persistent claim waits only until its lease,
        after which the caller retries ``claim`` and performs recovery.
        """

        timeout_seconds = max(0.0, float(timeout_seconds))
        if timeout_seconds <= 0:
            return
        with self._condition:
            self._reload_unlocked()
            current = self._states.get(side_effect_id)
            if current is None or current.state != "in_progress":
                return

            live_owner = self._has_live_local_owner_unlocked(
                current.claim_token
            )
            wait_seconds = timeout_seconds
            if live_owner:
                wait_seconds = min(wait_seconds, 0.05)
            elif current.lease_expires_at is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.0, current.lease_expires_at - time.time()),
                )
            if wait_seconds > 0:
                self._condition.wait(timeout=wait_seconds)

    def finish_execution(self, claim: SideEffectClaim) -> None:
        """Clear local liveness even when persistent acknowledgement failed."""

        if not claim.claim_token:
            return
        with self._condition:
            owner = self._local_execution_owners.get(claim.claim_token)
            if owner is not None:
                owner.active = False
                self._local_execution_owners.pop(claim.claim_token, None)
            self._condition.notify_all()
