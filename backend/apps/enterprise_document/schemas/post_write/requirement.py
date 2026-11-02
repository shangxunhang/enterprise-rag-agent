"""Canonical Post-write Requirement authority schemas."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class _FrozenRequirement(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class PostWriteRequirementEntrySchema(_FrozenRequirement):
    schema_version: Literal["post_write_requirement_entry_v1"] = (
        "post_write_requirement_entry_v1"
    )
    requirement_id: NonEmptyStr
    requirement_kind: NonEmptyStr
    statement: NonEmptyStr
    mandatory: bool = True
    constraints: dict[str, Any] = Field(default_factory=dict)


class PostWriteRequirementSnapshotSchema(_FrozenRequirement):
    schema_version: Literal["post_write_requirement_snapshot_v1"] = (
        "post_write_requirement_snapshot_v1"
    )
    semantic_contract_version: NonEmptyStr
    requirement_snapshot_id: NonEmptyStr
    source_business_ref: NonEmptyStr
    source_task_ref: NonEmptyStr
    requirements: tuple[PostWriteRequirementEntrySchema, ...] = Field(min_length=1)
    requirement_hash: Sha256Hex

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_requirements(cls, value):
        if isinstance(value, cls) or not isinstance(value, dict):
            return value
        payload = dict(value)
        raw = payload.get("requirements")
        if isinstance(raw, (list, tuple)):
            parsed = [
                PostWriteRequirementEntrySchema.model_validate(item)
                for item in raw
            ]
            ids = [item.requirement_id for item in parsed]
            if len(ids) != len(set(ids)):
                raise ValueError("Post-write requirement_id values must be unique")
            payload["requirements"] = [
                item.model_dump(mode="python", round_trip=True)
                for item in sorted(parsed, key=lambda item: item.requirement_id)
            ]
        return payload


class RequirementRefSchema(_FrozenRequirement):
    schema_version: Literal["post_write_requirement_ref_v1"] = (
        "post_write_requirement_ref_v1"
    )
    requirement_snapshot_id: NonEmptyStr
    requirement_schema_version: Literal["post_write_requirement_snapshot_v1"] = (
        "post_write_requirement_snapshot_v1"
    )
    requirement_semantic_contract_version: NonEmptyStr
    requirement_hash: Sha256Hex


__all__ = [
    "PostWriteRequirementEntrySchema",
    "PostWriteRequirementSnapshotSchema",
    "RequirementRefSchema",
]
