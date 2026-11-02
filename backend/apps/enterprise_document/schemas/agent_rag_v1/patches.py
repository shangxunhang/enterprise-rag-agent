"""State Gateway 接收的字段级 Patch 协议。"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import Field, field_validator, model_validator

from ._base import ContractSchema
from .enums import PatchOperation, StateActorRole


class StatePatchActorSchema(ContractSchema):
    agent_role: StateActorRole
    execution_id: str
    attempt_id: str


class StatePatchOperationSchema(ContractSchema):
    operation: PatchOperation
    path: str
    value: Any = None

    @field_validator("path", mode="before")
    @classmethod
    def _valid_json_pointer(cls, value: object) -> str:
        path = str(value).strip()
        if not path.startswith("/") or path == "/":
            raise ValueError("patch path must be a non-root JSON Pointer")
        if "//" in path:
            raise ValueError("patch path cannot contain empty segments")
        return path

    @model_validator(mode="after")
    def _validate_operation_value(self) -> "StatePatchOperationSchema":
        if self.operation == PatchOperation.REMOVE and self.value is not None:
            raise ValueError("remove operation cannot carry a value")
        return self


class StatePatchRequestSchema(ContractSchema):
    """Agent 只能通过该请求建议字段级状态写入。"""

    schema_version: str = "state_patch_request_v1"
    request_id: str
    task_id: str
    section_id: Optional[str] = None
    actor: StatePatchActorSchema
    input_snapshot_version: str
    expected_domain_version: int = Field(ge=0)
    target_domain: str
    operations: List[StatePatchOperationSchema] = Field(min_length=1)
    reason_code: str

    @field_validator(
        "request_id",
        "task_id",
        "input_snapshot_version",
        "target_domain",
        "reason_code",
        mode="before",
    )
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("state patch identifiers cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_section_domain(self) -> "StatePatchRequestSchema":
        if self.target_domain.startswith("section.") and not self.section_id:
            raise ValueError("section domain patch requires section_id")
        paths = [operation.path for operation in self.operations]
        if len(paths) != len(set(paths)):
            raise ValueError("one patch request cannot write the same path twice")
        return self
