"""Cooperative cancellation contract for workflow node execution.

Python threads cannot safely interrupt an in-flight native/CUDA call.  This
module therefore exposes the strongest safe in-process contract: every node
attempt owns one deadline and cancellation signal, and code running below the
node can observe the same signal through a ``ContextVar``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import hashlib
import threading
import time
from typing import Callable, Iterator

from core.runtime.side_effects import (
    InMemorySideEffectCommitStore,
    SideEffectClaimStatus,
    SideEffectCommitStore,
)


class WorkflowExecutionCancelled(BaseException):
    """Control-flow signal raised after cancellation or deadline.

    Like ``asyncio.CancelledError``, this deliberately does not inherit from
    ``Exception``. RAG/model layers contain deterministic ``except Exception``
    fallbacks for ordinary provider failures; cancellation must bypass those
    fallbacks or a timed-out workflow would continue doing expensive work.
    """


@dataclass(frozen=True)
class DeferredExternalCommit:
    """One attempt-scoped side effect waiting for canonical commit authority."""

    callback: Callable[[], None] = field(repr=False)
    side_effect_id: str
    description: str = "external_side_effect"
    requires_success: bool = True
    semantic_fingerprint: str | None = None


@dataclass
class WorkflowExecutionControl:
    """Deadline and cooperative cancellation state for one node attempt."""

    execution_id: str
    deadline_monotonic: float
    commit_scope_id: str | None = None
    side_effect_store: SideEffectCommitStore = field(
        default_factory=InMemorySideEffectCommitStore,
        repr=False,
    )
    _cancel_event: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )
    _reason: str | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _deferred_commits: list[DeferredExternalCommit] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _external_commit_state: str = field(default="open", init=False, repr=False)
    _side_effect_sequence: int = field(default=0, init=False, repr=False)

    @classmethod
    def with_timeout(
        cls,
        *,
        execution_id: str,
        timeout_seconds: float,
        commit_scope_id: str | None = None,
        side_effect_store: SideEffectCommitStore | None = None,
    ) -> "WorkflowExecutionControl":
        return cls(
            execution_id=execution_id,
            deadline_monotonic=time.monotonic() + max(0.0, float(timeout_seconds)),
            commit_scope_id=commit_scope_id or execution_id,
            side_effect_store=side_effect_store or InMemorySideEffectCommitStore(),
        )

    def reserve_side_effect_id(self, logical_key: str) -> str:
        """Allocate a deterministic retry-stable identity in registration order."""

        with self._lock:
            sequence = self._side_effect_sequence
            self._side_effect_sequence += 1
        digest = hashlib.sha256(
            (
                f"{self.commit_scope_id or self.execution_id}:"
                f"{sequence}:{logical_key}"
            ).encode("utf-8")
        ).hexdigest()
        return f"side_effect_{digest[:32]}"

    @property
    def cancel_reason(self) -> str | None:
        with self._lock:
            return self._reason

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def deadline_exceeded(self) -> bool:
        return time.monotonic() >= self.deadline_monotonic

    @property
    def should_stop(self) -> bool:
        return self.is_cancelled or self.deadline_exceeded

    def remaining_seconds(self) -> float:
        """Return the remaining provider-call budget for this attempt."""

        return max(0.0, self.deadline_monotonic - time.monotonic())

    def cancel(self, reason: str = "cancelled") -> bool:
        """Request cancellation once and revoke all pending commit authority."""

        with self._lock:
            if self._cancel_event.is_set():
                return False
            self._reason = str(reason or "cancelled")
            self._cancel_event.set()
            self._deferred_commits.clear()
            if self._external_commit_state == "open":
                self._external_commit_state = "aborted"
            return True

    def defer_external_commit(
        self,
        callback: Callable[[], None],
        *,
        description: str,
        requires_success: bool = True,
        side_effect_id: str | None = None,
        semantic_fingerprint: str | None = None,
    ) -> None:
        """Register a side effect that may commit only when this attempt is authorized."""

        with self._lock:
            if time.monotonic() >= self.deadline_monotonic and not self._cancel_event.is_set():
                self._reason = "deadline_exceeded"
                self._cancel_event.set()
                self._deferred_commits.clear()
                self._external_commit_state = "aborted"
            if self._cancel_event.is_set() or self._external_commit_state != "open":
                raise WorkflowExecutionCancelled(
                    f"workflow execution {self.execution_id} has no external commit authority: "
                    f"{self._reason or self._external_commit_state}"
                )
            if side_effect_id is None:
                sequence = self._side_effect_sequence
                self._side_effect_sequence += 1
                digest = hashlib.sha256(
                    (
                        f"{self.commit_scope_id or self.execution_id}:"
                        f"{sequence}:{description or 'external_side_effect'}"
                    ).encode("utf-8")
                ).hexdigest()
                resolved_side_effect_id = f"side_effect_{digest[:32]}"
            else:
                resolved_side_effect_id = side_effect_id
            resolved_semantic_fingerprint = (
                str(semantic_fingerprint or "").strip() or None
            )
            self._deferred_commits.append(
                DeferredExternalCommit(
                    callback=callback,
                    side_effect_id=resolved_side_effect_id,
                    description=str(description or "external_side_effect"),
                    requires_success=bool(requires_success),
                    semantic_fingerprint=resolved_semantic_fingerprint,
                )
            )

    def finalize_external_commits(self, *, attempt_succeeded: bool) -> dict[str, int]:
        """Flush callbacks only after acquiring and resolving commit authority.

        A competing in-progress claim is not a successful outcome. This method
        waits for its state to change, then reclaims a released/expired claim or
        confirms the terminal commit. A normal return therefore means no
        required side effect remains unresolved.
        """

        with self._lock:
            if self._cancel_event.is_set() or self._external_commit_state != "open":
                self._deferred_commits.clear()
                self._external_commit_state = "aborted"
                raise WorkflowExecutionCancelled(
                    f"workflow execution {self.execution_id} external commits revoked: "
                    f"{self._reason or 'cancelled'}"
                )
            pending = list(self._deferred_commits)
            self._deferred_commits.clear()
            self._external_commit_state = "committing"

        committed = 0
        discarded = 0
        already_committed = 0
        already_in_progress = 0
        try:
            for item in pending:
                if item.requires_success and not attempt_succeeded:
                    discarded += 1
                    continue

                while True:
                    self.checkpoint()
                    claim = self.side_effect_store.claim(
                        item.side_effect_id,
                        execution_id=self.execution_id,
                        description=item.description,
                        semantic_fingerprint=item.semantic_fingerprint,
                    )
                    if claim.status == SideEffectClaimStatus.ALREADY_COMMITTED:
                        already_committed += 1
                        break
                    if claim.status == SideEffectClaimStatus.ALREADY_IN_PROGRESS:
                        remaining_seconds = self.remaining_seconds()
                        if remaining_seconds <= 0:
                            self.checkpoint()
                        self.side_effect_store.wait_for_resolution(
                            item.side_effect_id,
                            timeout_seconds=remaining_seconds,
                        )
                        continue
                    break

                if claim.status == SideEffectClaimStatus.ALREADY_COMMITTED:
                    continue
                try:
                    self.checkpoint()
                    item.callback()
                    did_commit = self.side_effect_store.mark_committed(claim)
                except BaseException:
                    try:
                        self.side_effect_store.release(
                            claim,
                            reason="callback_or_acknowledgement_failed",
                        )
                    except BaseException:
                        # Preserve the original failure. A persistent orphaned
                        # claim becomes recoverable after its configured lease.
                        pass
                    try:
                        self.side_effect_store.finish_execution(claim)
                    except BaseException:
                        # Preserve the callback/acknowledgement failure while
                        # the built-in stores keep this cleanup non-throwing.
                        pass
                    raise
                self.side_effect_store.finish_execution(claim)
                if did_commit:
                    committed += 1
                else:
                    already_committed += 1
        except BaseException:
            with self._lock:
                self._external_commit_state = "commit_failed"
            raise

        with self._lock:
            self._external_commit_state = "committed"
        return {
            "committed": committed,
            "already_committed": already_committed,
            "already_in_progress": already_in_progress,
            "discarded": discarded,
        }

    def abort_external_commits(self, reason: str) -> int:
        """Discard all pending attempt-scoped side effects and revoke commit authority."""

        with self._lock:
            pending = len(self._deferred_commits)
        self.cancel(reason)
        return pending

    def checkpoint(self) -> None:
        """Fail fast when the attempt has been cancelled or exceeded deadline."""

        if self.deadline_exceeded and not self.is_cancelled:
            self.cancel("deadline_exceeded")
        if self.is_cancelled:
            raise WorkflowExecutionCancelled(
                f"workflow execution {self.execution_id} cancelled: "
                f"{self.cancel_reason or 'cancelled'}"
            )

    def metadata(self) -> dict[str, object]:
        """Return stable diagnostic data without exposing synchronization objects."""

        with self._lock:
            pending_external_commits = len(self._deferred_commits)
            external_commit_state = self._external_commit_state
            cancel_reason = self._reason
            cancel_requested = self._cancel_event.is_set()
        return {
            "execution_id": self.execution_id,
            "cooperative_cancellation": True,
            "cancel_requested": cancel_requested,
            "cancel_reason": cancel_reason,
            "remaining_seconds": self.remaining_seconds(),
            "pending_external_commits": pending_external_commits,
            "external_commit_state": external_commit_state,
        }


_ACTIVE_EXECUTION_CONTROL: ContextVar[WorkflowExecutionControl | None] = ContextVar(
    "active_workflow_execution_control",
    default=None,
)


def current_execution_control() -> WorkflowExecutionControl | None:
    """Return the node-attempt control visible to the current call context."""

    return _ACTIVE_EXECUTION_CONTROL.get()


def checkpoint_current_execution() -> None:
    """Apply the active attempt's cooperative cancellation checkpoint, if any."""

    control = current_execution_control()
    if control is not None:
        control.checkpoint()


@contextmanager
def activate_execution_control(
    control: WorkflowExecutionControl,
) -> Iterator[WorkflowExecutionControl]:
    """Propagate one attempt's deadline/cancellation signal through call layers."""

    token: Token[WorkflowExecutionControl | None] = _ACTIVE_EXECUTION_CONTROL.set(
        control
    )
    try:
        yield control
    finally:
        _ACTIVE_EXECUTION_CONTROL.reset(token)
