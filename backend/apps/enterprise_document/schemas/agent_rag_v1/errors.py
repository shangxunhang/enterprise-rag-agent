"""Agent-RAG V1 的统一工作流错误协议。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field, field_validator

from schemas.failure import FailureOrigin

from ._base import ContractSchema
from .enums import (
    WorkflowErrorCategory,
    WorkflowErrorScope,
    WorkflowErrorSource,
    WorkflowRetryability,
)


class WorkflowErrorSchema(ContractSchema):
    """跨 Agent、工具、状态网关和装配边界传递的唯一错误外壳。"""

    schema_version: str = "workflow_error_v1"
    error_id: str
    source: WorkflowErrorSource
    category: WorkflowErrorCategory
    scope: WorkflowErrorScope
    retryability: WorkflowRetryability
    affected_section_ids: List[str] = Field(default_factory=list)
    error_code: str
    message: str
    # Shared FP1 failure-source fact. UNKNOWN is the compatibility default and
    # grants no fallback/retry/Gate authority.
    failure_origin: FailureOrigin = FailureOrigin.UNKNOWN
    details_ref: Optional[str] = None

    @field_validator(
        "error_id",
        "error_code",
        "message",
        mode="before",
    )
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("workflow error text fields cannot be empty")
        return normalized

    @field_validator("affected_section_ids")
    @classmethod
    def _unique_sections(cls, value: List[str]) -> List[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("affected section ids cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("affected section ids must be unique")
        return normalized
