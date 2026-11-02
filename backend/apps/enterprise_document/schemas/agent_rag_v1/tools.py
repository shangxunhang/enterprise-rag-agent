"""Tool Gateway 的标准化结果协议。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from schemas.failure import FailureCategory, FailureOrigin

from ._base import ContractSchema
from .budgets import BudgetReservationRefSchema, BudgetSettlementSchema
from .enums import ToolResultStatus


class ToolProvenanceSchema(ContractSchema):
    source_refs: List[str] = Field(default_factory=list)
    raw_artifact_ref: Optional[str] = None
    input_snapshot_version: str
    tool_name: Optional[str] = None
    tool_version: Optional[str] = None
    input_fingerprint: Optional[str] = None

    @field_validator("source_refs")
    @classmethod
    def _unique_source_refs(cls, value: List[str]) -> List[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("tool source refs cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("tool source refs must be unique")
        return normalized


class ToolQualitySchema(ContractSchema):
    warnings: List[str] = Field(default_factory=list)


class ToolResultErrorSchema(ContractSchema):
    error_code: Optional[str] = None
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    failure_origin: FailureOrigin = FailureOrigin.UNKNOWN
    retryable_hint: bool = False
    source_component: Optional[str] = None


class NormalizedToolResultSchema(ContractSchema):
    """屏蔽底层工具差异，并只通过 Artifact 引用保留原始结果。"""

    schema_version: str = "normalized_tool_result_v1"
    call_id: str
    # Target semantic identity.  Optional only for historical compatibility rows.
    tool_operation_id: Optional[str] = None
    tool_semantic_fingerprint: Optional[str] = None
    physical_attempt_refs: List[str] = Field(default_factory=list)
    budget_reservation_ref: Optional[BudgetReservationRefSchema] = None
    budget_settlement: Optional[BudgetSettlementSchema] = None
    status: ToolResultStatus
    data_type: str
    data: Dict[str, Any] = Field(default_factory=dict)
    provenance: ToolProvenanceSchema
    quality: ToolQualitySchema = Field(default_factory=ToolQualitySchema)
    error: ToolResultErrorSchema = Field(default_factory=ToolResultErrorSchema)

    @field_validator("call_id", "data_type", mode="before")
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("tool result identifiers cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_target_identity(self) -> "NormalizedToolResultSchema":
        if bool(self.tool_operation_id) != bool(self.tool_semantic_fingerprint):
            raise ValueError(
                "tool_operation_id and tool_semantic_fingerprint must be supplied together"
            )
        if self.tool_semantic_fingerprint is not None:
            fingerprint = self.tool_semantic_fingerprint
            if len(fingerprint) != 64 or any(
                ch not in "0123456789abcdef" for ch in fingerprint
            ):
                raise ValueError("tool_semantic_fingerprint must be lowercase SHA-256 hex")
        if len(self.physical_attempt_refs) != len(set(self.physical_attempt_refs)):
            raise ValueError("physical_attempt_refs must be unique")
        return self

    @model_validator(mode="after")
    def _validate_error_status(self) -> "NormalizedToolResultSchema":
        error_statuses = {
            ToolResultStatus.RETRYABLE_ERROR,
            ToolResultStatus.PERMANENT_ERROR,
            ToolResultStatus.REJECTED,
            ToolResultStatus.STALE,
        }
        if self.status in error_statuses and not self.error.error_code:
            raise ValueError("non-success tool result requires error_code")
        if self.status in {
            ToolResultStatus.SUCCESS,
            ToolResultStatus.EMPTY,
        } and self.error.error_code:
            raise ValueError("successful tool result cannot carry error_code")
        return self
