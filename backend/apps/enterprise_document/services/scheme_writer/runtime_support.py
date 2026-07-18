"""Runtime support shared by SchemeWriter services."""

import traceback
from typing import Optional

from core.error_factory import ErrorFactory
from schemas.common import ErrorSchema
from .base import RuntimeBoundService


class SchemeWriterRuntimeSupport(RuntimeBoundService):
    """Compatibility helpers backed by the canonical runtime error factory."""

    _runtime_error_factory = ErrorFactory()

    @classmethod
    def _now_iso(cls) -> str:
        return cls._runtime_error_factory.clock.now_iso()

    @classmethod
    def _error(
        cls,
        code: str,
        exc_or_message: Exception | str,
        *,
        node: str,
        retryable: bool = False,
        user_message: Optional[str] = None,
    ) -> ErrorSchema:
        if isinstance(exc_or_message, Exception):
            message = str(exc_or_message)
            error_type = exc_or_message.__class__.__name__
            stack = traceback.format_exc()
        else:
            message = str(exc_or_message)
            error_type = "RuntimeValidationError"
            stack = None
        return cls._runtime_error_factory.create(
            error_code=code,
            error_type=error_type,
            message=message,
            user_visible_message=user_message or message,
            recoverable=True,
            retryable=retryable,
            failed_node=node,
            component="SchemeWriterAgent",
            agent_name="SchemeWriterAgent",
            step_name=node,
            stack_trace=stack,
        )
