"""Common schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class SchemaBase(BaseModel):
    """Base schema for all protocol schemas."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ErrorSourceSchema(SchemaBase):
    schema_version: str = "error_source_v1"

    component: Optional[str] = None
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    step_name: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class ErrorSchema(SchemaBase):
    schema_version: str = "error_v2"

    error_id: Optional[str] = None
    error_code: str
    error_type: str
    message: str
    error_message: Optional[str] = None
    user_visible_message: Optional[str] = None

    recoverable: bool = True
    retryable: bool = False
    failed_node: Optional[str] = None

    source: Optional[ErrorSourceSchema] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    debug_info: Dict[str, Any] = Field(default_factory=dict)
    stack_trace: Optional[str] = None

    created_at: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.error_message is None:
            self.error_message = self.message
        if self.user_visible_message is None:
            self.user_visible_message = self.message


class WarningSchema(SchemaBase):
    schema_version: str = "warning_v1"

    warning_code: str
    message: str

    source: Optional[ErrorSourceSchema] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    created_at: Optional[str] = None

    extra: Dict[str, Any] = Field(default_factory=dict)