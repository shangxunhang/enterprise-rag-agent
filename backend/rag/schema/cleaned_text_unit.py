"""Canonical Data Platform -> RAG cleaned text unit boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


LEGACY_TOP_LEVEL_EXTENSION_FIELDS = frozenset({"cleaning_rules", "created_at"})
DATA_PLATFORM_TOP_LEVEL_EXTENSION_FIELDS = frozenset(
    {
        "can_enter_spark_cleaning",
        "document_role",
        "image_uri",
        "is_gold_reference",
        "parser_name",
        "project_id",
        "should_index",
        "source_backend",
        "source_content_type",
        "source_quality_level",
        "source_sha256",
        "structure_quality",
        "table_uri",
    }
)
KNOWN_TOP_LEVEL_EXTENSION_FIELDS = (
    LEGACY_TOP_LEVEL_EXTENSION_FIELDS
    | DATA_PLATFORM_TOP_LEVEL_EXTENSION_FIELDS
)


class CleanedTextUnitSchema(BaseModel):
    """Minimum contract consumed by the current RAG chunking layer.

    The schema intentionally mirrors fields that ``cleaned_text_unit_chunker``
    actually consumes.  Future data-platform metadata must travel through
    ``extra`` until a real RAG consumer requires a first-class field.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["cleaned_text_unit_v1"] = "cleaned_text_unit_v1"
    unit_id: str | None = None
    doc_id: str | None = None
    tenant_id: str = "default"
    kb_id: str = "default"
    file_id: str | None = None
    source_type: str
    source_uri: str | None = None
    source_name: str | None = None
    source_format: str | None = None
    batch_id: str | None = None
    title: str | None = None
    section: str | None = None
    section_level: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    unit_type: str = "other"
    unit_order: int = 0
    text: str = ""
    text_length: int = Field(default=0, ge=0)
    language: str = "unknown"
    quality_score: float | None = None
    quality_flags: list[str] = Field(default_factory=list)
    cleaning_version: str
    extra: dict[str, Any] = Field(default_factory=dict)


def migrate_legacy_cleaned_text_unit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Explicitly migrate known producer extensions into ``extra``.

    The adapter accepts the two legacy extensions plus the metadata emitted by
    ``spark_document_clean_v2.0``.  It never defaults required fields, coerces
    known-field types, or drops unknown producer fields, so typos and unapproved
    additions still fail at ``CleanedTextUnitSchema(extra='forbid')``.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("legacy CleanedTextUnit migration requires an object payload")
    migrated = dict(payload)
    producer_extensions = {
        field: migrated.pop(field)
        for field in KNOWN_TOP_LEVEL_EXTENSION_FIELDS
        if field in migrated
    }
    if not producer_extensions:
        return migrated

    raw_extra = migrated.get("extra")
    if raw_extra is None:
        extra: dict[str, Any] = {}
    elif isinstance(raw_extra, dict):
        extra = dict(raw_extra)
    else:
        # Do not convert an invalid canonical ``extra`` value into a valid one.
        raise TypeError("legacy CleanedTextUnit migration requires extra to be a dict or null")

    migration_meta = dict(extra.get("cleaned_text_unit_migration") or {})
    migration_meta["migrated_top_level_fields"] = sorted(producer_extensions)
    extra["cleaned_text_unit_migration"] = migration_meta
    extra["producer_top_level_extensions"] = producer_extensions

    legacy_extensions = {
        field: value
        for field, value in producer_extensions.items()
        if field in LEGACY_TOP_LEVEL_EXTENSION_FIELDS
    }
    if legacy_extensions:
        legacy_meta = dict(
            extra.get("legacy_cleaned_text_unit_migration") or {}
        )
        legacy_meta["migrated_top_level_fields"] = sorted(legacy_extensions)
        extra["legacy_cleaned_text_unit_migration"] = legacy_meta
        extra["legacy_top_level_extensions"] = legacy_extensions

    migrated["extra"] = extra
    return migrated
