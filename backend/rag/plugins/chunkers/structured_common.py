"""Shared deterministic primitives for Stage C structured chunkers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rag.chunker.cleaned_text_unit_chunker import row_to_unit, should_skip_unit
from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_CLEANING_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
    DEFAULT_SOURCE_TYPE,
)
from rag.ports.chunking import ParentChildChunkResult
from rag.schema.Chunk_Schema import build_child_chunk_v1, build_parent_chunk_v1
from rag.util.text_utils import unique_keep_order


HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千万零〇0-9]+篇\s*.*$"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千万零〇0-9]+章\s*.*$"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千万零〇0-9]+节\s*.*$"), 3),
    (re.compile(r"^第\s*[一二三四五六七八九十百千万零〇0-9]+\s*条\s*.*$"), 4),
    (re.compile(r"^[一二三四五六七八九十]+、\s*.+$"), 3),
    (re.compile(r"^（[一二三四五六七八九十]+）\s*.+$"), 4),
    (re.compile(r"^\d+(?:\.\d+)+(?:[\.、)]|\s+)\s*.+$"), 4),
    (re.compile(r"^\d+[\.、)]\s*.+$"), 3),
)


@dataclass(frozen=True)
class HeadingInfo:
    title: str
    level: int


@dataclass
class LogicalChildSpec:
    text: str
    units: list[dict[str, Any]]
    representation_type: str
    structure_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None
    fallback_strategy: str | None = None
    actual_strategy: str | None = None


@dataclass
class LogicalParentSpec:
    children: list[LogicalChildSpec]
    units: list[dict[str, Any]]
    representation_type: str
    structure_path: list[str] = field(default_factory=list)
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None
    fallback_strategy: str | None = None
    actual_strategy: str | None = None


def normalize_units(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strictly normalize, filter and order cleaned text units."""

    units: list[dict[str, Any]] = []
    for raw in records:
        unit = row_to_unit(raw)
        if unit.get("doc_id") and not should_skip_unit(unit):
            units.append(unit)
    return sorted(units, key=lambda item: int(item.get("unit_order") or 0))


def detect_heading(text: str, unit_type: str | None = None) -> HeadingInfo | None:
    """Detect a conservative document heading without using an LLM."""

    value = str(text or "").strip()
    if not value or len(value) > 180:
        return None
    if value.startswith("#"):
        match = re.match(r"^(#{1,6})\s+(.+)$", value)
        if match:
            return HeadingInfo(title=value, level=len(match.group(1)))
    for pattern, level in HEADING_PATTERNS:
        if pattern.match(value):
            return HeadingInfo(title=value, level=level)
    if unit_type == "title" and len(value) <= 120:
        return HeadingInfo(title=value, level=4)
    return None


def update_heading_stack(
    stack: list[tuple[int, str]], heading: HeadingInfo
) -> list[tuple[int, str]]:
    """Return a new heading stack with lower/equal levels replaced."""

    kept = [(level, title) for level, title in stack if level < heading.level]
    kept.append((heading.level, heading.title))
    return kept


def profile_from_units(units: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return the one canonical profile shared by all units."""

    profile: dict[str, Any] | None = None
    for unit in units:
        extra = unit.get("extra")
        candidate = extra.get("document_profile") if isinstance(extra, dict) else None
        if not isinstance(candidate, dict):
            continue
        if profile is None:
            profile = dict(candidate)
        elif profile != candidate:
            raise ValueError(
                "one structured chunk cannot combine conflicting document profiles; "
                f"doc_id={unit.get('doc_id')!r}"
            )
    if profile is None:
        raise ValueError("structured chunking requires extra.document_profile")
    return profile


def _first(
    units: Sequence[dict[str, Any]], key: str, default: Any = None
) -> Any:
    for unit in units:
        value = unit.get(key)
        if value not in (None, ""):
            return value
    return default


def _last(
    units: Sequence[dict[str, Any]], key: str, default: Any = None
) -> Any:
    for unit in reversed(units):
        value = unit.get(key)
        if value not in (None, ""):
            return value
    return default


def _bounds(
    units: Sequence[dict[str, Any]], key: str, *, maximum: bool
) -> int | None:
    values: list[int] = []
    for unit in units:
        value = unit.get(key)
        if value in (None, ""):
            continue
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return max(values) if maximum else min(values)


def parser_version_from_units(units: Sequence[dict[str, Any]]) -> str | None:
    for unit in units:
        extra = unit.get("extra")
        if not isinstance(extra, dict):
            continue
        value = extra.get("parse_version")
        if value not in (None, ""):
            return str(value)
        producer = extra.get("producer_top_level_extensions")
        if isinstance(producer, dict):
            value = producer.get("parse_version")
            if value not in (None, ""):
                return str(value)
    return None


def base_extra(
    units: Sequence[dict[str, Any]], *, text: str, chunker_name: str
) -> dict[str, Any]:
    """Build common source and profile metadata for every structured chunk."""

    profile = profile_from_units(units)
    flags: list[Any] = []
    scores: list[float] = []
    for unit in units:
        flags.extend(unit.get("quality_flags") or [])
        if unit.get("quality_score") is not None:
            scores.append(float(unit["quality_score"]))
    return {
        "chunker_name": chunker_name,
        "source_uri": _first(units, "source_uri"),
        "source_name": _first(units, "source_name"),
        "source_format": _first(units, "source_format"),
        "batch_id": _first(units, "batch_id"),
        "language": _first(units, "language", "unknown"),
        "quality_score": round(sum(scores) / len(scores), 4) if scores else None,
        "quality_flags": unique_keep_order(flags),
        "source_unit_count": len(
            unique_keep_order([unit.get("unit_id") for unit in units])
        ),
        "source_unit_types": unique_keep_order(
            [unit.get("unit_type") for unit in units]
        ),
        "text_length": len(text),
        "document_profile": profile,
        "document_type": profile["document_type"],
        "evidence_role": profile["evidence_role"],
        "project_relation": profile["project_relation"],
        "project_id": profile.get("project_id"),
        "authority_level": (profile.get("authority") or {}).get("level"),
    }


def type_aware_metadata(
    *,
    original_strategy: str,
    actual_strategy: str,
    representation_type: str,
    units: Sequence[dict[str, Any]],
    chunker_version: str,
    structure_path: Sequence[str],
    fallback_used: bool,
    fallback_reason: str | None,
    fallback_strategy: str | None,
) -> dict[str, Any]:
    """Build the auditable Stage C routing and fallback envelope."""

    return {
        "strategy": original_strategy,
        "representation_type": representation_type,
        "parser_version": parser_version_from_units(units),
        "chunker_version": chunker_version,
        "structure_path": [str(item) for item in structure_path],
        "fallback_used": bool(fallback_used),
        "fallback_reason": fallback_reason,
        "fallback_strategy": fallback_strategy,
        "original_strategy": original_strategy,
        "actual_strategy": actual_strategy,
    }


def _bounded_parent_specs(
    parents: Sequence[LogicalParentSpec], *, max_parent_chars: int
) -> list[LogicalParentSpec]:
    """Split logical parents by whole-child boundaries under a hard char budget."""

    if max_parent_chars <= 0:
        raise ValueError("max_parent_chars must be positive")

    bounded: list[LogicalParentSpec] = []
    for parent_spec in parents:
        non_empty_children = [
            child for child in parent_spec.children if str(child.text or "").strip()
        ]
        current: list[LogicalChildSpec] = []
        current_chars = 0

        def flush() -> None:
            nonlocal current, current_chars
            if not current:
                return
            seen_unit_ids: set[str] = set()
            group_units: list[dict[str, Any]] = []
            for child in current:
                for unit in child.units:
                    unit_id = str(unit.get("unit_id") or "")
                    if unit_id and unit_id in seen_unit_ids:
                        continue
                    if unit_id:
                        seen_unit_ids.add(unit_id)
                    group_units.append(unit)

            metadata = dict(parent_spec.metadata)
            if "row_count" in metadata:
                metadata["row_count"] = len(current)
            if "structured_fact_count" in metadata:
                metadata["structured_fact_count"] = sum(
                    child.representation_type == "fact_record" for child in current
                )
            if "fallback_chunk_count" in metadata:
                metadata["fallback_chunk_count"] = sum(
                    child.fallback_used for child in current
                )

            bounded.append(
                LogicalParentSpec(
                    children=list(current),
                    units=group_units,
                    representation_type=parent_spec.representation_type,
                    structure_path=list(parent_spec.structure_path),
                    title=parent_spec.title,
                    metadata=metadata,
                    fallback_used=parent_spec.fallback_used,
                    fallback_reason=parent_spec.fallback_reason,
                    fallback_strategy=parent_spec.fallback_strategy,
                    actual_strategy=parent_spec.actual_strategy,
                )
            )
            current = []
            current_chars = 0

        for child in non_empty_children:
            child_text = str(child.text or "").strip()
            child_chars = len(child_text)
            if child_chars > max_parent_chars:
                raise ValueError(
                    "structured child exceeds parent char budget; "
                    f"child_chars={child_chars}, max_parent_chars={max_parent_chars}"
                )
            addition = child_chars + (2 if current else 0)
            if current and current_chars + addition > max_parent_chars:
                flush()
                addition = child_chars
            current.append(child)
            current_chars += addition
        flush()

    return bounded


def materialize_document(
    *,
    doc_id: str,
    parents: Sequence[LogicalParentSpec],
    original_strategy: str,
    parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
    child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
    created_at: str | None = None,
    chunker_name: str,
    max_parent_chars: int | None = None,
) -> ParentChildChunkResult:
    """Materialize logical structured records into canonical parent/child chunks."""

    parent_records: list[dict[str, Any]] = []
    child_records: list[dict[str, Any]] = []
    global_child_index = 1
    materialized_parents = (
        _bounded_parent_specs(parents, max_parent_chars=max_parent_chars)
        if max_parent_chars is not None
        else list(parents)
    )

    for parent_index, parent_spec in enumerate(materialized_parents, start=1):
        if not parent_spec.children:
            continue
        parent_units = parent_spec.units or [
            unit for child in parent_spec.children for unit in child.units
        ]
        parent_text = "\n\n".join(
            child.text.strip()
            for child in parent_spec.children
            if child.text.strip()
        ).strip()
        if not parent_text:
            continue
        parent_id = f"{doc_id}_parent_{parent_index:06d}"
        built_children: list[dict[str, Any]] = []

        for child_index, child_spec in enumerate(parent_spec.children, start=1):
            child_text = str(child_spec.text or "").strip()
            if not child_text:
                continue
            child_units = child_spec.units or parent_units
            actual_strategy = child_spec.actual_strategy or original_strategy
            fallback_strategy = child_spec.fallback_strategy
            child_extra = base_extra(
                child_units,
                text=child_text,
                chunker_name=chunker_name,
            )
            child_extra.update(child_spec.metadata)
            child_extra["type_aware_chunking"] = type_aware_metadata(
                original_strategy=original_strategy,
                actual_strategy=actual_strategy,
                representation_type=child_spec.representation_type,
                units=child_units,
                chunker_version=child_chunk_version,
                structure_path=child_spec.structure_path,
                fallback_used=child_spec.fallback_used,
                fallback_reason=child_spec.fallback_reason,
                fallback_strategy=fallback_strategy,
            )
            child_id = f"{parent_id}_child_{child_index:04d}"
            child = build_child_chunk_v1(
                child_chunk_id=child_id,
                parent_chunk_id=parent_id,
                doc_id=doc_id,
                text=child_text,
                tenant_id=str(_first(child_units, "tenant_id", "default")),
                kb_id=str(_first(child_units, "kb_id", "default")),
                file_id=_first(child_units, "file_id"),
                source_type=str(
                    _first(child_units, "source_type", DEFAULT_SOURCE_TYPE)
                ),
                source_unit_ids=unique_keep_order(
                    [unit.get("unit_id") for unit in child_units]
                ),
                title=parent_spec.title or _last(child_units, "title"),
                section=(
                    child_spec.metadata.get("section_title")
                    or parent_spec.title
                    or _last(child_units, "section")
                ),
                section_level=(
                    child_spec.metadata.get("heading_level")
                    or _last(child_units, "section_level")
                ),
                page_start=_bounds(child_units, "page_start", maximum=False),
                page_end=_bounds(child_units, "page_end", maximum=True),
                child_chunk_index=global_child_index,
                child_index_in_parent=child_index,
                child_chunk_strategy=actual_strategy,
                cleaning_version=str(
                    _last(
                        child_units,
                        "cleaning_version",
                        DEFAULT_CLEANING_VERSION,
                    )
                ),
                parent_chunk_version=parent_chunk_version,
                child_chunk_version=child_chunk_version,
                created_at=created_at,
                extra=child_extra,
            )
            built_children.append(child)
            child_records.append(child)
            global_child_index += 1

        if not built_children:
            continue
        parent_actual_strategy = parent_spec.actual_strategy or original_strategy
        parent_extra = base_extra(
            parent_units,
            text=parent_text,
            chunker_name=chunker_name,
        )
        parent_extra.update(parent_spec.metadata)
        parent_extra["type_aware_chunking"] = type_aware_metadata(
            original_strategy=original_strategy,
            actual_strategy=parent_actual_strategy,
            representation_type=parent_spec.representation_type,
            units=parent_units,
            chunker_version=parent_chunk_version,
            structure_path=parent_spec.structure_path,
            fallback_used=parent_spec.fallback_used,
            fallback_reason=parent_spec.fallback_reason,
            fallback_strategy=parent_spec.fallback_strategy,
        )
        parent = build_parent_chunk_v1(
            parent_chunk_id=parent_id,
            doc_id=doc_id,
            text=parent_text,
            tenant_id=str(_first(parent_units, "tenant_id", "default")),
            kb_id=str(_first(parent_units, "kb_id", "default")),
            file_id=_first(parent_units, "file_id"),
            source_type=str(
                _first(parent_units, "source_type", DEFAULT_SOURCE_TYPE)
            ),
            source_unit_ids=unique_keep_order(
                [unit.get("unit_id") for unit in parent_units]
            ),
            child_chunk_ids=[child["child_chunk_id"] for child in built_children],
            title=parent_spec.title or _last(parent_units, "title"),
            section=parent_spec.title or _last(parent_units, "section"),
            section_level=parent_spec.metadata.get("heading_level")
            or _last(parent_units, "section_level"),
            page_start=_bounds(parent_units, "page_start", maximum=False),
            page_end=_bounds(parent_units, "page_end", maximum=True),
            parent_chunk_index=parent_index,
            parent_chunk_strategy=parent_actual_strategy,
            cleaning_version=str(
                _last(
                    parent_units,
                    "cleaning_version",
                    DEFAULT_CLEANING_VERSION,
                )
            ),
            parent_chunk_version=parent_chunk_version,
            created_at=created_at,
            extra=parent_extra,
        )
        parent_records.append(parent)

    return ParentChildChunkResult(parents=parent_records, children=child_records)


def chunk_text_units(
    units: Sequence[dict[str, Any]], *, max_chars: int
) -> list[list[dict[str, Any]]]:
    """Group whole source units under a character budget without splitting units."""

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0
    for unit in units:
        text = str(unit.get("text") or "").strip()
        if not text:
            continue
        addition = len(text) + (2 if current else 0)
        if current and current_len + addition > max_chars:
            groups.append(current)
            current = []
            current_len = 0
        current.append(unit)
        current_len += len(text) + (2 if len(current) > 1 else 0)
    if current:
        groups.append(current)
    return groups


def join_unit_text(units: Sequence[dict[str, Any]]) -> str:
    return "\n\n".join(
        str(unit.get("text") or "").strip()
        for unit in units
        if str(unit.get("text") or "").strip()
    ).strip()


__all__ = [
    "HeadingInfo",
    "LogicalChildSpec",
    "LogicalParentSpec",
    "base_extra",
    "chunk_text_units",
    "detect_heading",
    "join_unit_text",
    "materialize_document",
    "normalize_units",
    "profile_from_units",
    "type_aware_metadata",
    "update_heading_stack",
]
