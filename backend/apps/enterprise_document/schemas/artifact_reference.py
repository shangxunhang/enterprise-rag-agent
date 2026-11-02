"""Immutable artifact reference shared across enterprise-document domains."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, field_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema


class ArtifactReferenceSchema(ContractSchema):
    """Reference one immutable, verifiable artifact-store object."""

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
    run_id: Optional[str] = None
    section_id: Optional[str] = None
    attempt_id: Optional[str] = None
    input_snapshot_version: Optional[str] = None
    immutable: Literal[True] = True

    @field_validator(
        "artifact_ref",
        "artifact_type",
        "content_hash",
        "schema_version",
        "created_by",
        "task_id",
        mode="before",
    )
    @classmethod
    def _non_empty_text(cls, value: object) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("artifact reference text fields cannot be empty")
        return normalized

    @field_validator(
        "run_id",
        "section_id",
        "attempt_id",
        "input_snapshot_version",
        mode="before",
    )
    @classmethod
    def _optional_non_empty_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(
                "optional artifact reference text fields cannot be blank"
            )
        return normalized


__all__ = ["ArtifactReferenceSchema"]
