"""Canonical SIF-04 FactSnapshot domain contracts.

Fact semantics live here. Immutable version identity/currentness continue to be owned
by SIF-03 ArtifactRuntime; these schemas never create a second head/version authority.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from ._base import ContractSchema
from .artifacts import ArtifactVersionRefSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenFactContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class FactSourceBindingSchema(_FrozenFactContract):
    schema_version: Literal["fact_source_binding_v1"] = "fact_source_binding_v1"
    source_type: NonEmptyStr
    source_ref: NonEmptyStr
    source_version_ref: NonEmptyStr | None = None


def _binding_key(binding: FactSourceBindingSchema) -> tuple[str, str, str]:
    return (
        binding.source_type,
        binding.source_ref,
        binding.source_version_ref or "",
    )


def _canonical_bindings(values: list[Any]) -> list[dict[str, Any]]:
    parsed = [FactSourceBindingSchema.model_validate(item) for item in values]
    unique: dict[tuple[str, str, str], FactSourceBindingSchema] = {}
    for item in parsed:
        unique[_binding_key(item)] = item
    return [
        unique[key].model_dump(mode="python", round_trip=True)
        for key in sorted(unique)
    ]


class CanonicalFactRecordSchema(_FrozenFactContract):
    schema_version: Literal["canonical_fact_record_v1"] = "canonical_fact_record_v1"
    fact_id: NonEmptyStr
    fact_type: NonEmptyStr
    normalized_value: Any
    unit: NonEmptyStr | None = None
    source_bindings: list[FactSourceBindingSchema] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_bindings(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        bindings = payload.get("source_bindings")
        if isinstance(bindings, list):
            payload["source_bindings"] = _canonical_bindings(bindings)
        return payload

    @field_validator("normalized_value")
    @classmethod
    def _json_compatible_value(cls, value: Any) -> Any:
        def validate_json_value(item: Any) -> None:
            if item is None or isinstance(item, (str, bool, int)):
                return
            if isinstance(item, float):
                if not math.isfinite(item):
                    raise ValueError(
                        "canonical fact normalized_value JSON numbers must be finite"
                    )
                return
            if isinstance(item, list):
                for child in item:
                    validate_json_value(child)
                return
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError(
                        "canonical fact normalized_value JSON object keys must be strings"
                    )
                for child in item.values():
                    validate_json_value(child)
                return
            raise ValueError(
                "canonical fact normalized_value must contain only JSON-native values"
            )

        validate_json_value(value)
        return value


class FactSnapshotContentSchema(_FrozenFactContract):
    schema_version: Literal["fact_snapshot_content_v1"] = "fact_snapshot_content_v1"
    normalization_contract_version: NonEmptyStr
    source_bindings: list[FactSourceBindingSchema] = Field(min_length=1)
    facts: list[CanonicalFactRecordSchema] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_unordered_collections(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        bindings = payload.get("source_bindings")
        if isinstance(bindings, list):
            payload["source_bindings"] = _canonical_bindings(bindings)
        raw_facts = payload.get("facts")
        if isinstance(raw_facts, list):
            parsed = [CanonicalFactRecordSchema.model_validate(item) for item in raw_facts]
            ids = [item.fact_id for item in parsed]
            if len(ids) != len(set(ids)):
                raise ValueError("FactSnapshot fact_id values must be unique")
            payload["facts"] = [
                item.model_dump(mode="python", round_trip=True)
                for item in sorted(parsed, key=lambda item: item.fact_id)
            ]
        return payload

    @model_validator(mode="after")
    def _fact_sources_belong_to_snapshot_source_set(self) -> "FactSnapshotContentSchema":
        snapshot_sources = {_binding_key(item) for item in self.source_bindings}
        for fact in self.facts:
            for binding in fact.source_bindings:
                if _binding_key(binding) not in snapshot_sources:
                    raise ValueError(
                        "fact source binding must be present in snapshot-level material source set"
                    )
        return self


class FactSnapshotBindingSchema(_FrozenFactContract):
    schema_version: Literal["fact_snapshot_binding_v1"] = "fact_snapshot_binding_v1"
    version_ref: ArtifactVersionRefSchema
    resolved_head_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _binds_fact_snapshot_artifact(self) -> "FactSnapshotBindingSchema":
        if self.version_ref.logical_ref.artifact_type != "fact_snapshot":
            raise ValueError("FactSnapshot binding requires artifact_type=fact_snapshot")
        if self.version_ref.content_schema_version != "fact_snapshot_content_v1":
            raise ValueError(
                "FactSnapshot binding requires fact_snapshot_content_v1 Artifact content"
            )
        return self


__all__ = [
    "CanonicalFactRecordSchema",
    "FactSnapshotBindingSchema",
    "FactSnapshotContentSchema",
    "FactSourceBindingSchema",
]
