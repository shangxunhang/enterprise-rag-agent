"""关键 Claim、来源绑定和生命周期协议。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from ._base import ContractSchema
from .enums import (
    ClaimLifecycleStatus,
    ClaimSupportType,
    ClaimVerificationStatus,
)


class ClaimSourceBindingSchema(ContractSchema):
    source_type: str
    source_ref: str
    support_type: ClaimSupportType


class ClaimSchema(ContractSchema):
    schema_version: str = "claim_v1"
    claim_id: str
    section_id: str
    paragraph_id: str
    claim_type: str
    text_span_ref: str
    normalized_value: Any
    unit: Optional[str] = None
    source_bindings: List[ClaimSourceBindingSchema] = Field(default_factory=list)
    verification_status: ClaimVerificationStatus = ClaimVerificationStatus.PENDING
    lifecycle_status: ClaimLifecycleStatus = ClaimLifecycleStatus.ACTIVE
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "claim_id",
        "section_id",
        "paragraph_id",
        "claim_type",
        "text_span_ref",
        mode="before",
    )
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("claim identifiers cannot be empty")
        return normalized


class ExcelDeviceFactSchema(ContractSchema):
    schema_version: str = "excel_device_fact_v1"
    fact_id: str
    device_name: str
    aliases: List[str] = Field(default_factory=list)
    device_type: Optional[str] = None
    model: Optional[str] = None
    specification: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    formula: Optional[str] = None
    calculation_result: Optional[Decimal] = None
    source_ref: str

    @field_validator("aliases")
    @classmethod
    def _unique_aliases(cls, value: List[str]) -> List[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("device aliases cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("device aliases must be unique")
        return normalized


class FactSnapshotSchema(ContractSchema):
    schema_version: str = "fact_snapshot_v1"
    snapshot_version: str
    artifact_ref: str
    devices: List[ExcelDeviceFactSchema] = Field(default_factory=list)
    construction_scope: List[str] = Field(default_factory=list)
    calculation_parameters: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_fact_ids(self) -> "FactSnapshotSchema":
        fact_ids = [item.fact_id for item in self.devices]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("FactSnapshot device fact ids must be unique")
        return self
