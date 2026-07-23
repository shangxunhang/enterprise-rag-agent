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
import threading
import time
from typing import Iterator


class WorkflowExecutionCancelled(BaseException):
    """Control-flow signal raised after cancellation or deadline.

    Like ``asyncio.CancelledError``, this deliberately does not inherit from
    ``Exception``. RAG/model layers contain deterministic ``except Exception``
    fallbacks for ordinary provider failures; cancellation must bypass those
    fallbacks or a timed-out workflow would continue doing expensive work.
    """


@dataclass
class WorkflowExecutionControl:
    """Deadline and cooperative cancellation state for one node attempt."""

    execution_id: str
    deadline_monotonic: float
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

    @classmethod
    def with_timeout(
        cls,
        *,
        execution_id: str,
        timeout_seconds: float,
    ) -> "WorkflowExecutionControl":
        return cls(
            execution_id=execution_id,
            deadline_monotonic=time.monotonic() + max(0.0, float(timeout_seconds)),
        )

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
        """Request cancellation once; return whether this call set it."""

        with self._lock:
            if self._cancel_event.is_set():
                return False
            self._reason = str(reason or "cancelled")
            self._cancel_event.set()
            return True

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

        return {
            "execution_id": self.execution_id,
            "cooperative_cancellation": True,
            "cancel_requested": self.is_cancelled,
            "cancel_reason": self.cancel_reason,
            "remaining_seconds": self.remaining_seconds(),
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
