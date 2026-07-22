"""Availability-fallback classification for ModelGateway."""

from __future__ import annotations

from model_gateway.model_contract import ModelSelection
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
        error_type = str(response.error.error_type if response.error else "")
        if error_type in self._ERROR_TYPES:
            return True
        message = str(
            response.error_message
            or (response.error.message if response.error else "")
            or ""
        ).lower()
        if any(marker in message for marker in self._MESSAGE_MARKERS):
            return True
        # Remote provider failures are availability failures when the provider
        # did not produce a valid model response. Semantic/quality rejection is
        # represented above the gateway and therefore never enters this branch.
        return selection.profile.residency_policy.value == "remote"
