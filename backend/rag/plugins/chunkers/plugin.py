"""Configuration-driven parent/child chunker plugins.

The online RAG stack consumes parent_chunk_v1 and child_chunk_v1.  This module
adapts several parent splitting strategies to that stable contract while child
chunks remain small fixed windows suitable for dense indexing.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from rag.chunker.ChildParentChunker import ChildParentChunker, ParentChildChunkResult
from rag.chunker.FixedSizeChunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.ParagraphChunker import ParagraphChunker
from rag.chunker.RecursiveChunker import RecursiveChunker
from rag.chunker.cleaned_text_unit_chunker import row_to_unit, should_skip_unit
from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_CLEANING_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
    DEFAULT_SOURCE_TYPE,
)
from rag.legacy.schema.Chunk_Schema import build_child_chunk_v1, build_parent_chunk_v1
from rag.util.text_utils import unique_keep_order


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value) if value not in (None, "") else default
    except Exception:
        return default


def _first(items: Sequence[dict[str, Any]], key: str, default: Any = None) -> Any:
    for item in items:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _last(items: Sequence[dict[str, Any]], key: str, default: Any = None) -> Any:
    for item in reversed(items):
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _bounds(items: Sequence[dict[str, Any]], key: str, *, maximum: bool) -> int | None:
    values = [_safe_int(item.get(key)) for item in items]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values) if maximum else min(values)


def _unit_extra(units: Sequence[dict[str, Any]], *, text: str, chunker_name: str) -> dict[str, Any]:
    flags: list[Any] = []
    for unit in units:
        flags.extend(unit.get("quality_flags") or [])
    scores = [unit.get("quality_score") for unit in units if unit.get("quality_score") is not None]
    return {
        "chunker_name": chunker_name,
        "source_uri": _first(units, "source_uri"),
        "source_name": _first(units, "source_name"),
        "source_format": _first(units, "source_format"),
        "batch_id": _first(units, "batch_id"),
        "language": _first(units, "language", "unknown"),
        "quality_score": round(sum(scores) / len(scores), 4) if scores else None,
        "quality_flags": unique_keep_order(flags),
        "source_unit_count": len(unique_keep_order([u.get("unit_id") for u in units])),
        "source_unit_types": unique_keep_order([u.get("unit_type") for u in units]),
        "text_length": len(text),
    }


class FixedParentChildChunkerPlugin:
    """Registry adapter over the existing production parent/child chunker."""

    def __init__(
        self,
        *,
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 150,
        child_chunk_size: int = 500,
        child_chunk_overlap: int = 50,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "fixed_parent_child",
        tokenizer_model_name: str | None = None,
        tokenizer_local_files_only: bool = True,
        build_context: Any = None,
    ) -> None:
        self.created_at = deterministic_created_at
        self.delegate = ChildParentChunker(
            parent_chunk_size=parent_chunk_size,
            parent_chunk_overlap=parent_chunk_overlap,
            child_chunk_size=child_chunk_size,
            child_chunk_overlap=child_chunk_overlap,
            unit=unit,
            parent_chunk_strategy="fixed",
            child_chunk_strategy="fixed",
            parent_chunk_version=parent_chunk_version,
            child_chunk_version=child_chunk_version,
            chunker_name=chunker_name,
            tokenizer_model_name=tokenizer_model_name,
            tokenizer_local_files_only=tokenizer_local_files_only,
        )
        self._metadata = {
            "parent_strategy": "fixed",
            "child_strategy": "fixed",
            "parent_chunk_size": parent_chunk_size,
            "parent_chunk_overlap": parent_chunk_overlap,
            "child_chunk_size": child_chunk_size,
            "child_chunk_overlap": child_chunk_overlap,
            "unit": unit,
            "tokenizer_model_name": tokenizer_model_name,
            "tokenizer_local_files_only": bool(tokenizer_local_files_only),
        }

    def chunk_records(self, records: Iterable[dict[str, Any]]) -> ParentChildChunkResult:
        result = self.delegate.chunk_records(records)
        self._normalize_output(result)
        return result

    def _normalize_output(self, result: ParentChildChunkResult) -> None:
        plugin = getattr(self, "plugin_metadata", None)
        plugin_dict = plugin.to_dict() if plugin is not None else {}
        for record in [*result.parents, *result.children]:
            if self.created_at:
                record["created_at"] = self.created_at
            extra = dict(record.get("extra") or {})
            extra["chunker_plugin"] = plugin_dict
            record["extra"] = extra

    def execution_metadata(self) -> dict[str, Any]:
        return dict(self._metadata)


class StructuredParentChildChunkerPlugin:
    """Use a structural parent splitter and fixed child windows."""

    STRATEGY = "recursive"
    FLAT_CHUNKER = RecursiveChunker

    def __init__(
        self,
        *,
        parent_chunk_size: int = 1500,
        parent_chunk_overlap: int = 150,
        child_chunk_size: int = 500,
        child_chunk_overlap: int = 50,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str | None = None,
        build_context: Any = None,
    ) -> None:
        if parent_chunk_size <= 0 or child_chunk_size <= 0:
            raise ValueError("parent/child chunk sizes must be positive")
        if not 0 <= parent_chunk_overlap < parent_chunk_size:
            raise ValueError("invalid parent_chunk_overlap")
        if not 0 <= child_chunk_overlap < child_chunk_size:
            raise ValueError("invalid child_chunk_overlap")
        self.parent_chunk_size = int(parent_chunk_size)
        self.parent_chunk_overlap = int(parent_chunk_overlap)
        self.child_chunk_size = int(child_chunk_size)
        self.child_chunk_overlap = int(child_chunk_overlap)
        self.unit = str(unit or "char").lower()
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name or f"{self.STRATEGY}_parent_child"
        self.parent_splitter = self.FLAT_CHUNKER(
            chunk_size=self.parent_chunk_size,
            chunk_overlap=self.parent_chunk_overlap,
        )
        # Reuse its offset-aware child window implementation.
        self.child_helper = ChildParentChunker(
            parent_chunk_size=max(self.parent_chunk_size, self.child_chunk_size),
            parent_chunk_overlap=min(self.parent_chunk_overlap, max(self.parent_chunk_size, self.child_chunk_size) - 1),
            child_chunk_size=self.child_chunk_size,
            child_chunk_overlap=self.child_chunk_overlap,
            unit=self.unit,
            parent_chunk_strategy=self.STRATEGY,
            child_chunk_strategy="fixed",
            parent_chunk_version=self.parent_chunk_version,
            child_chunk_version=self.child_chunk_version,
            chunker_name=self.chunker_name,
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "parent_strategy": self.STRATEGY,
            "child_strategy": "fixed",
            "parent_chunk_size": self.parent_chunk_size,
            "parent_chunk_overlap": self.parent_chunk_overlap,
            "child_chunk_size": self.child_chunk_size,
            "child_chunk_overlap": self.child_chunk_overlap,
            "unit": self.unit,
        }

    def chunk_records(self, records: Iterable[dict[str, Any]]) -> ParentChildChunkResult:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            unit = row_to_unit(record)
            if unit.get("doc_id") and not should_skip_unit(unit):
                groups[str(unit["doc_id"])].append(unit)

        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        global_child_index = 1
        for doc_id in sorted(groups):
            doc_parents, doc_children, global_child_index = self._chunk_doc(
                doc_id,
                sorted(groups[doc_id], key=lambda item: int(item.get("unit_order") or 0)),
                global_child_index,
            )
            parents.extend(doc_parents)
            children.extend(doc_children)
        return ParentChildChunkResult(parents=parents, children=children)

    def _chunk_doc(
        self,
        doc_id: str,
        units: list[dict[str, Any]],
        global_child_index: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        text, spans = self._join_units(units)
        if not text:
            return [], [], global_child_index
        document = {
            "doc_id": doc_id,
            "text": text,
            "metadata": {
                "title": _last(units, "title"),
                "source": _first(units, "source_uri"),
                "source_path": _first(units, "source_uri"),
                "doc_type": _first(units, "source_format"),
            },
        }
        flat_chunks = self.parent_splitter.chunk_document(document)
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []

        for parent_index, flat in enumerate(flat_chunks, start=1):
            parent_text = str(flat.get("text") or "").strip()
            if not parent_text:
                continue
            metadata = flat.get("metadata") if isinstance(flat.get("metadata"), dict) else {}
            start = _safe_int(metadata.get("start_char"))
            end = _safe_int(metadata.get("end_char"))
            if start is None:
                start = text.find(parent_text)
            if end is None and start is not None and start >= 0:
                end = start + len(parent_text)
            parent_units = self._units_for_span(units, spans, start, end)
            if not parent_units:
                parent_units = units
            parent_id = f"{doc_id}_parent_{parent_index:06d}"
            child_windows = self.child_helper._split_text_windows_with_offsets(  # noqa: SLF001
                parent_text,
                chunk_size=self.child_chunk_size,
                overlap=self.child_chunk_overlap,
            )
            child_records: list[dict[str, Any]] = []
            for child_index, (child_text, child_start, child_end) in enumerate(child_windows, start=1):
                child_id = f"{parent_id}_child_{child_index:04d}"
                child = build_child_chunk_v1(
                    child_chunk_id=child_id,
                    parent_chunk_id=parent_id,
                    doc_id=doc_id,
                    text=child_text,
                    source_type=_first(parent_units, "source_type", DEFAULT_SOURCE_TYPE),
                    source_unit_ids=unique_keep_order([u.get("unit_id") for u in parent_units]),
                    title=_last(parent_units, "title"),
                    section=metadata.get("section") or _last(parent_units, "section"),
                    section_level=metadata.get("heading_level") or _last(parent_units, "section_level"),
                    page_start=_bounds(parent_units, "page_start", maximum=False),
                    page_end=_bounds(parent_units, "page_end", maximum=True),
                    child_chunk_index=global_child_index,
                    child_index_in_parent=child_index,
                    child_chunk_strategy="fixed",
                    char_start_in_parent=child_start,
                    char_end_in_parent=child_end,
                    cleaning_version=_last(parent_units, "cleaning_version", DEFAULT_CLEANING_VERSION),
                    parent_chunk_version=self.parent_chunk_version,
                    child_chunk_version=self.child_chunk_version,
                    created_at=self.created_at,
                    extra={
                        **_unit_extra(parent_units, text=child_text, chunker_name=self.chunker_name),
                        "parent_strategy": self.STRATEGY,
                    },
                )
                child_records.append(child)
                global_child_index += 1

            parent = build_parent_chunk_v1(
                parent_chunk_id=parent_id,
                doc_id=doc_id,
                text=parent_text,
                source_type=_first(parent_units, "source_type", DEFAULT_SOURCE_TYPE),
                source_unit_ids=unique_keep_order([u.get("unit_id") for u in parent_units]),
                child_chunk_ids=[item["child_chunk_id"] for item in child_records],
                title=_last(parent_units, "title"),
                section=metadata.get("section") or _last(parent_units, "section"),
                section_level=metadata.get("heading_level") or _last(parent_units, "section_level"),
                page_start=_bounds(parent_units, "page_start", maximum=False),
                page_end=_bounds(parent_units, "page_end", maximum=True),
                parent_chunk_index=parent_index,
                parent_chunk_strategy=self.STRATEGY,
                cleaning_version=_last(parent_units, "cleaning_version", DEFAULT_CLEANING_VERSION),
                parent_chunk_version=self.parent_chunk_version,
                created_at=self.created_at,
                extra={
                    **_unit_extra(parent_units, text=parent_text, chunker_name=self.chunker_name),
                    "parent_strategy": self.STRATEGY,
                    "source_char_start": start,
                    "source_char_end": end,
                },
            )
            plugin = getattr(self, "plugin_metadata", None)
            plugin_dict = plugin.to_dict() if plugin is not None else {}
            parent["extra"]["chunker_plugin"] = plugin_dict
            for child in child_records:
                child["extra"]["chunker_plugin"] = plugin_dict
            parents.append(parent)
            children.extend(child_records)
        return parents, children, global_child_index

    @staticmethod
    def _join_units(units: Sequence[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
        pieces: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0
        for unit in units:
            value = str(unit.get("text") or "")
            if not value:
                spans.append((cursor, cursor))
                continue
            if pieces:
                pieces.append("\n")
                cursor += 1
            start = cursor
            pieces.append(value)
            cursor += len(value)
            spans.append((start, cursor))
        return "".join(pieces), spans

    @staticmethod
    def _units_for_span(
        units: Sequence[dict[str, Any]],
        spans: Sequence[tuple[int, int]],
        start: int | None,
        end: int | None,
    ) -> list[dict[str, Any]]:
        if start is None or end is None or start < 0:
            return []
        return [unit for unit, (u_start, u_end) in zip(units, spans) if u_start < end and u_end > start]


class RecursiveParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    STRATEGY = "recursive"
    FLAT_CHUNKER = RecursiveChunker


class HeadingParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    STRATEGY = "heading"
    FLAT_CHUNKER = HeadingChunker


class ParagraphParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    STRATEGY = "paragraph"
    FLAT_CHUNKER = ParagraphChunker


class FixedStructuredParentChildChunkerPlugin(StructuredParentChildChunkerPlugin):
    """Alternative fixed implementation used for registry parity tests."""

    STRATEGY = "fixed"
    FLAT_CHUNKER = FixedSizeChunker
