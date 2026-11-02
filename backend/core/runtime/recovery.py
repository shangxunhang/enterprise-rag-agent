"""Context carried only while an authorized durable recovery is executing."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class DurableRecoveryContext:
    attempt_number: int
    reason: str
    policy: str


_CURRENT_RECOVERY: ContextVar[DurableRecoveryContext | None] = ContextVar(
    "durable_recovery_context",
    default=None,
)


def current_durable_recovery() -> DurableRecoveryContext | None:
    return _CURRENT_RECOVERY.get()


@contextmanager
def activate_durable_recovery(
    context: DurableRecoveryContext,
) -> Iterator[DurableRecoveryContext]:
    token = _CURRENT_RECOVERY.set(context)
    try:
        yield context
    finally:
        _CURRENT_RECOVERY.reset(token)


__all__ = [
    "DurableRecoveryContext",
    "activate_durable_recovery",
    "current_durable_recovery",
]
