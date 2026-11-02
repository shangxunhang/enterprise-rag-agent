"""Availability-fallback classification for ModelGateway."""

from __future__ import annotations

from model_gateway.model_contract import ModelSelection
from schemas.failure import FailureCategory, FailureOrigin
from schemas.model import ModelResponseSchema


class AvailabilityFailurePolicy:
    """Decide whether a failed provider call may try the next candidate.

    This policy intentionally does *not* inspect answer quality.  A successful
    model response is never treated as an availability failure, even when an
    upper-layer quality gate later rejects its content.
    """

    _ERROR_TYPES = {
        "CUDAOutOfMemoryError",
        "OutOfMemoryError",
        "TimeoutError",
        "ConnectionError",
        "FileNotFoundError",
        "KeyError",
        "OSError",
    }
    _NON_AVAILABILITY_MARKERS = (
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "authentication",
        "permission denied",
        "bad request",
        "invalid request",
        "unknown model",
    )
    _MESSAGE_MARKERS = (
        "out of memory",
        "cuda oom",
        "timed out",
        "timeout",
        "provider unavailable",
        "service unavailable",
        "connection",
        "failed to load",
        "model path not found",
        "api error",
        "rate limit",
        "429",
        "502",
        "503",
        "504",
        "model client not found",
    )

    def is_availability_failure(
        self,
        response: ModelResponseSchema,
        selection: ModelSelection,
    ) -> bool:
        if response.success:
            return False
        # Provider fallback is legal only for failures whose nearest source is
        # the provider/runtime boundary. Budget/storage/unknown failures must
        # never use provider fallback as an escape hatch.
        if response.failure_origin is not FailureOrigin.PROVIDER_RUNTIME:
            return False
        error_type = str(response.error.error_type if response.error else "")
        failure_category = (
            response.error.failure_category if response.error else FailureCategory.UNKNOWN
        )
        if failure_category in {
            FailureCategory.CONFIGURATION,
            FailureCategory.VALIDATION,
            FailureCategory.BUSINESS_QUALITY,
            FailureCategory.EVIDENCE_INSUFFICIENT,
            FailureCategory.CANCELLED,
        }:
            return False
        if failure_category in {
            FailureCategory.AVAILABILITY,
            FailureCategory.TIMEOUT,
            FailureCategory.RESOURCE_EXHAUSTED,
            FailureCategory.TRANSIENT,
        }:
            return True
        message = str(
            response.error_message
            or (response.error.message if response.error else "")
            or ""
        ).lower()
        # Inspect auth/config semantics before legacy exception-type fallbacks.
        # In particular, urllib.error.HTTPError is also an OSError and old
        # adapters may have persisted only the broader error type.
        if any(marker in message for marker in self._NON_AVAILABILITY_MARKERS):
            return False
        if error_type in self._ERROR_TYPES:
            return True
        if any(marker in message for marker in self._MESSAGE_MARKERS):
            return True
        # Remote residency alone is not evidence of an availability failure.
        # Authentication/configuration/protocol errors must fail fast rather than
        # silently changing the selected model.
        return False
