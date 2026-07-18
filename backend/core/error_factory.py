"""Central construction of structured runtime errors."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.runtime.clock import Clock, SystemClock
from schemas.common import ErrorSchema, ErrorSourceSchema


class ErrorFactory:
    def __init__(self, clock: Optional[Clock] = None) -> None:
        self.clock = clock or SystemClock()

    def create(
        self,
        *,
        error_code: str,
        error_type: str,
        message: str,
        component: str,
        user_visible_message: Optional[str] = None,
        recoverable: bool = False,
        retryable: bool = False,
        failed_node: Optional[str] = None,
        agent_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        step_name: Optional[str] = None,
        stack_trace: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        debug_info: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ErrorSchema:
        return ErrorSchema(
            error_code=error_code,
            error_type=error_type,
            message=message,
            user_visible_message=user_visible_message,
            recoverable=recoverable,
            retryable=retryable,
            failed_node=failed_node,
            source=ErrorSourceSchema(
                component=component,
                agent_name=agent_name,
                tool_name=tool_name,
                step_name=step_name,
            ),
            details=dict(details or {}),
            debug_info=dict(debug_info or {}),
            stack_trace=stack_trace,
            created_at=self.clock.now_iso(),
            extra=dict(extra or {}),
        )
