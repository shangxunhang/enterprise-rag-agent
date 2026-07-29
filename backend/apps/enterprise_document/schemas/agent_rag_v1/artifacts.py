"""不可变 Artifact 引用协议。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, field_validator

from ._base import ContractSchema


class ArtifactReferenceSchema(ContractSchema):
    """引用 Artifact Store 中一个不可变、可校验的版本。"""

    reference_schema_version: Literal["artifact_reference_v1"] = (
        "artifact_reference_v1"
    )
    artifact_ref: str
    artifact_type: str
    content_hash: str
    schema_version: str
    size_bytes: int = Field(ge=0)
    created_by: str
    task_id: str
    section_id: Optional[str] = None
    attempt_id: str
    input_snapshot_version: str
    immutable: Literal[True] = True

    @field_validator(
        "artifact_ref",
        "artifact_type",
        "content_hash",
        "schema_version",
        "created_by",
        "task_id",
        "attempt_id",
        "input_snapshot_version",
        mode="before",
    )
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("artifact reference text fields cannot be empty")
        return normalized
