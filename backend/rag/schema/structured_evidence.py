"""Canonical online projection of Stage C structured child evidence.

Stage C stores the real fact/table/policy/template fields in ``child.extra``.
This module freezes one stable projection that is shared by Dense, BM25,
RetrievalResultV2, RetrievalCandidateV2 and prompt-visible evidence.
"""

from __future__ import annotations

from enum import Enum
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactStatus(str, Enum):
    UNKNOWN = "unknown"
    ESTIMATED = "estimated"
    PROPOSED = "proposed"
    PLANNED = "planned"
    CONFIRMED = "confirmed"
    DECIDED = "decided"


class FactEvidenceSchema(_FrozenContract):
    record_id: str | None = None
    fact_category: str | None = None
    subject: str | None = None
    predicate: str | None = None
    value: Any | None = None
    unit: str | None = None
    fact_status: FactStatus = FactStatus.UNKNOWN
    source_span: dict[str, Any] | None = None


class TableEvidenceSchema(_FrozenContract):
    table_id: str | None = None
    table_title: str | None = None
    header: list[Any] = Field(default_factory=list)
    row_index: int | None = None
    cells: list[Any] = Field(default_factory=list)
    normalized_fields: dict[str, Any] = Field(default_factory=dict)
    inherited_cells: dict[str, Any] = Field(default_factory=dict)
    merged_cell_relations: list[Any] = Field(default_factory=list)
    calculation_record: bool | dict[str, Any] | None = None


class PolicyEvidenceSchema(_FrozenContract):
    policy_name: str | None = None
    chapter_id: str | None = None
    chapter_title: str | None = None
    article_id: str | None = None
    article_title: str | None = None
    clause_id: str | None = None
    clause_path: list[str] = Field(default_factory=list)
    is_table_of_contents: bool = False


class TemplateEvidenceSchema(_FrozenContract):
    section_path: list[str] = Field(default_factory=list)
    section_title: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    placeholder_fields: list[str] = Field(default_factory=list)
    style_hints: list[str] = Field(default_factory=list)
    is_example_content: bool = False


class FallbackEvidenceSchema(_FrozenContract):
    used: bool = False
    reason: str | None = None
    strategy: str | None = None


class StructuredEvidenceSchema(_FrozenContract):
    schema_version: Literal["structured_evidence_v1"] = "structured_evidence_v1"
    representation_type: str
    original_strategy: str | None = None
    actual_strategy: str | None = None
    fact: FactEvidenceSchema | None = None
    table: TableEvidenceSchema | None = None
    policy: PolicyEvidenceSchema | None = None
    template: TemplateEvidenceSchema | None = None
    fallback: FallbackEvidenceSchema = Field(default_factory=FallbackEvidenceSchema)

    @model_validator(mode="after")
    def _representation_has_stable_name(self) -> "StructuredEvidenceSchema":
        if not str(self.representation_type or "").strip():
            raise ValueError("representation_type cannot be empty")
        return self


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_and_extra(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    child = record.get("child_chunk")
    child = dict(child) if isinstance(child, dict) else dict(record)
    extra = _as_dict(child.get("extra"))
    if not extra:
        extra = _as_dict(record.get("extra"))
    if not extra:
        extra = _as_dict(child.get("extra_json"))
    if not extra:
        extra = _as_dict(record.get("extra_json"))
    return child, extra


def build_structured_evidence(record: dict[str, Any]) -> StructuredEvidenceSchema:
    """Project the real Stage C child-extra shape into one frozen contract."""
    for container in (
        record,
        record.get("child_chunk") if isinstance(record.get("child_chunk"), dict) else {},
        record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
        record.get("extra") if isinstance(record.get("extra"), dict) else {},
    ):
        candidate = container.get("structured_evidence") if isinstance(container, dict) else None
        if isinstance(candidate, StructuredEvidenceSchema):
            return candidate
        if isinstance(candidate, dict):
            return StructuredEvidenceSchema.model_validate(candidate)

    child, extra = _record_and_extra(record)
    type_aware = _as_dict(child.get("type_aware_chunking"))
    if not type_aware:
        type_aware = _as_dict(record.get("type_aware_chunking"))
    if not type_aware:
        type_aware = _as_dict(extra.get("type_aware_chunking"))

    raw_representation = _optional_str(type_aware.get("representation_type"))
    raw_actual = _optional_str(type_aware.get("actual_strategy"))
    representation_type = raw_representation or raw_actual or "paragraph_text"
    if representation_type == "paragraph":
        representation_type = "paragraph_text"

    fact_keys = {
        "record_id",
        "fact_category",
        "subject",
        "predicate",
        "value",
        "unit",
        "fact_status",
        "source_span",
    }
    fact_present = bool(fact_keys & set(extra))
    fact = None
    if fact_present:
        raw_status = _optional_str(extra.get("fact_status")) or FactStatus.UNKNOWN.value
        try:
            fact_status = FactStatus(raw_status)
        except ValueError as exc:
            raise ValueError(f"unsupported fact_status: {raw_status}") from exc
        fact = FactEvidenceSchema(
            record_id=_optional_str(extra.get("record_id")),
            fact_category=_optional_str(extra.get("fact_category")),
            subject=_optional_str(extra.get("subject")),
            predicate=_optional_str(extra.get("predicate")),
            value=extra.get("value"),
            unit=_optional_str(extra.get("unit")),
            fact_status=fact_status,
            source_span=(
                dict(extra.get("source_span"))
                if isinstance(extra.get("source_span"), dict)
                else None
            ),
        )

    table_keys = {
        "table_id",
        "table_title",
        "header",
        "row_index",
        "cells",
        "normalized_fields",
        "inherited_cells",
        "merged_cell_relations",
        "calculation_record",
    }
    table_present = bool(table_keys & set(extra))
    table = None
    if table_present:
        table = TableEvidenceSchema(
            table_id=_optional_str(extra.get("table_id")),
            table_title=_optional_str(extra.get("table_title")),
            header=_as_list(extra.get("header")),
            row_index=_optional_int(extra.get("row_index")),
            cells=_as_list(extra.get("cells")),
            normalized_fields=_as_dict(extra.get("normalized_fields")),
            inherited_cells=_as_dict(extra.get("inherited_cells")),
            merged_cell_relations=_as_list(extra.get("merged_cell_relations")),
            calculation_record=(
                dict(extra.get("calculation_record"))
                if isinstance(extra.get("calculation_record"), dict)
                else (
                    bool(extra.get("calculation_record"))
                    if isinstance(extra.get("calculation_record"), bool)
                    else None
                )
            ),
        )

    policy_keys = {
        "policy_name",
        "chapter_id",
        "chapter_title",
        "article_id",
        "article_title",
        "clause_id",
        "clause_path",
        "is_table_of_contents",
    }
    policy_present = bool(policy_keys & set(extra))
    policy = None
    if policy_present:
        policy = PolicyEvidenceSchema(
            policy_name=_optional_str(extra.get("policy_name")),
            chapter_id=_optional_str(extra.get("chapter_id")),
            chapter_title=_optional_str(extra.get("chapter_title")),
            article_id=_optional_str(extra.get("article_id")),
            article_title=_optional_str(extra.get("article_title")),
            clause_id=_optional_str(extra.get("clause_id")),
            clause_path=[str(item) for item in _as_list(extra.get("clause_path"))],
            is_table_of_contents=bool(extra.get("is_table_of_contents", False)),
        )

    template_keys = {
        "section_path",
        "section_title",
        "required_fields",
        "placeholder_fields",
        "style_hints",
        "is_example_content",
    }
    template_present = bool(template_keys & set(extra)) and (
        representation_type == "template_section"
        or _optional_str(type_aware.get("original_strategy")) == "template_section"
    )
    template = None
    if template_present:
        template = TemplateEvidenceSchema(
            section_path=[str(item) for item in _as_list(extra.get("section_path"))],
            section_title=_optional_str(extra.get("section_title")),
            required_fields=[str(item) for item in _as_list(extra.get("required_fields"))],
            placeholder_fields=[
                str(item) for item in _as_list(extra.get("placeholder_fields"))
            ],
            style_hints=[str(item) for item in _as_list(extra.get("style_hints"))],
            is_example_content=bool(extra.get("is_example_content", False)),
        )

    fallback = FallbackEvidenceSchema(
        used=bool(type_aware.get("fallback_used", False)),
        reason=_optional_str(type_aware.get("fallback_reason")),
        strategy=_optional_str(type_aware.get("fallback_strategy")),
    )
    return StructuredEvidenceSchema(
        representation_type=representation_type,
        original_strategy=_optional_str(
            type_aware.get("original_strategy") or type_aware.get("strategy")
        ),
        actual_strategy=_optional_str(
            type_aware.get("actual_strategy") or representation_type
        ),
        fact=fact,
        table=table,
        policy=policy,
        template=template,
        fallback=fallback,
    )


__all__ = [
    "FactEvidenceSchema",
    "FactStatus",
    "FallbackEvidenceSchema",
    "PolicyEvidenceSchema",
    "StructuredEvidenceSchema",
    "TableEvidenceSchema",
    "TemplateEvidenceSchema",
    "build_structured_evidence",
]
