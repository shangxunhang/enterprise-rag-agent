"""Failure classification and retry decision policy.

Failure classification answers *what happened*. Retry policy answers *whether a
retry is allowed*. Retry budgets are checked separately from both decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError

from schemas.failure import FailureCategory


_AUTH_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "authentication",
    "permission denied",
    "invalid credential",
    "configuration",
)


def classify_failure(exc: Exception) -> FailureCategory:
    """Classify an exception without making retry or fallback decisions."""

    message = str(exc or "").lower()

    # HTTPError is also an OSError. Handle protocol status first so auth/config
    # failures can never fall through to the generic availability branch.
    if isinstance(exc, HTTPError):
        status = int(getattr(exc, "code", 0) or 0)
        if status in {401, 403}:
            return FailureCategory.CONFIGURATION
        if status in {400, 404, 405, 422}:
            return FailureCategory.VALIDATION
        if status == 408:
            return FailureCategory.TIMEOUT
        if status in {409, 425, 429}:
            return FailureCategory.TRANSIENT
        if status >= 500:
            return FailureCategory.AVAILABILITY

    if any(marker in message for marker in _AUTH_MARKERS):
        return FailureCategory.CONFIGURATION
    if isinstance(exc, PermissionError):
        return FailureCategory.CONFIGURATION
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return FailureCategory.CONFIGURATION
    if isinstance(exc, TimeoutError):
        return FailureCategory.TIMEOUT
    if isinstance(exc, MemoryError) or "out of memory" in message:
        return FailureCategory.RESOURCE_EXHAUSTED
    if isinstance(exc, (TypeError, ValueError)):
        return FailureCategory.VALIDATION
    if isinstance(exc, ConnectionError):
        return FailureCategory.AVAILABILITY
    if isinstance(exc, OSError):
        return FailureCategory.AVAILABILITY
    if any(
        marker in message
        for marker in (
            "temporarily unavailable",
            "rate limit",
            "connection reset",
            "connection refused",
            "service unavailable",
        )
    ):
        return FailureCategory.TRANSIENT
    return FailureCategory.UNKNOWN


@dataclass(frozen=True)
class RetryDecisionPolicy:
    """Policy-only mapping from failure category to retry permission.

    ``business_quality_retryable`` is deliberately configurable because the
    contract must not hard-code product policy. ``budget_remaining`` only limits
    attempts; it never changes the failure category.
    """

    business_quality_retryable: bool = True
    unknown_retryable: bool = False

    def should_retry(
        self,
        category: FailureCategory | str,
        *,
        budget_remaining: bool = True,
        worker_still_active: bool = False,
        legacy_retryable: bool | None = None,
    ) -> bool:
        if not budget_remaining:
            return False
        try:
            normalized = FailureCategory(category)
        except ValueError:
            normalized = FailureCategory.UNKNOWN

        if normalized in {
            FailureCategory.CONFIGURATION,
            FailureCategory.VALIDATION,
            FailureCategory.EVIDENCE_INSUFFICIENT,
            FailureCategory.CANCELLED,
        }:
            return False
        if normalized == FailureCategory.BUSINESS_QUALITY:
            return self.business_quality_retryable
        if normalized == FailureCategory.TIMEOUT:
            return not worker_still_active
        if normalized in {
            FailureCategory.AVAILABILITY,
            FailureCategory.TRANSIENT,
            FailureCategory.RESOURCE_EXHAUSTED,
        }:
            return True
        if legacy_retryable is not None:
            return bool(legacy_retryable)
        return self.unknown_retryable
