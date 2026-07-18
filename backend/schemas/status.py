"""Canonical execution statuses used across Task, Workflow, Agent and Tool."""

from __future__ import annotations

from enum import Enum


class ExecutionStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    RETRYABLE_FAILED = "retryable_failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


TERMINAL_STATUSES = {
    ExecutionStatus.SUCCESS,
    ExecutionStatus.PARTIAL_SUCCESS,
    ExecutionStatus.FAILED,
    ExecutionStatus.RETRYABLE_FAILED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.SKIPPED,
}

FAILURE_STATUSES = {
    ExecutionStatus.FAILED,
    ExecutionStatus.RETRYABLE_FAILED,
    ExecutionStatus.CANCELLED,
}


def is_failure(status: str | ExecutionStatus) -> bool:
    try:
        return ExecutionStatus(status) in FAILURE_STATUSES
    except ValueError:
        return True
