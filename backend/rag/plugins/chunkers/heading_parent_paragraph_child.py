"""True heading-parent / paragraph-child structured chunking."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
)
from rag.ports.chunking import ParentChildChunkResult
from rag.plugins.chunkers.structured_common import (
    LogicalChildSpec,
    LogicalParentSpec,
    chunk_text_units,
    detect_heading,
    join_unit_text,
    materialize_document,
    normalize_units,
    update_heading_stack,
)


class HeadingParagraphChunker:
    """Build heading-bounded parents and paragraph/list children."""

    STRATEGY = "heading_parent_paragraph_child"

    def __init__(
        self,
        *,
        parent_chunk_size: int = 1800,
        parent_chunk_overlap: int = 0,
        child_chunk_size: int = 520,
        child_chunk_overlap: int = 0,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "heading_parent_paragraph_child",
        build_context: Any = None,
    ) -> None:
        if parent_chunk_size <= 0 or child_chunk_size <= 0:
            raise ValueError("parent/child chunk sizes must be positive")
        self.parent_chunk_size = int(parent_chunk_size)
        self.child_chunk_size = int(child_chunk_size)
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name
        self._stats: dict[str, int] = {}

    @staticmethod
    def _is_toc_marker(text: str) -> bool:
        return "".join(str(text or "").split()) in {"目录", "目次"}

    @staticmethod
    def _is_toc_entry(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        import re

        return bool(
            re.search(r"(?:\.{2,}|…{2,}|\s)(?:\d+|[IVXLCDM]+)\s*$", value)
        )

    def _chunk_doc(
        self, doc_id: str, records: list[dict[str, Any]]
    ) -> ParentChildChunkResult:
        units = normalize_units(records)
        if not units:
            return ParentChildChunkResult(parents=[], children=[])

        sections: list[tuple[list[str], int | None, str | None, list[dict[str, Any]]]] = []
        heading_stack: list[tuple[int, str]] = []
        current_units: list[dict[str, Any]] = []
        current_path: list[str] = []
        current_level: int | None = None
        current_title: str | None = None
        toc_mode = False
        toc_filtered = 0
        heading_count = 0

        def flush() -> None:
            nonlocal current_units
            if current_units:
                sections.append(
                    (
                        list(current_path),
                        current_level,
                        current_title,
                        current_units,
                    )
                )
                current_units = []

        for unit in units:
            text = str(unit.get("text") or "").strip()
            if self._is_toc_marker(text):
                toc_mode = True
                toc_filtered += 1
                continue
            heading = detect_heading(text, str(unit.get("unit_type") or ""))
            if toc_mode:
                if self._is_toc_entry(text):
                    toc_filtered += 1
                    continue
                if heading is None:
                    toc_filtered += 1
                    continue
                toc_mode = False
            if heading is not None:
                flush()
                heading_count += 1
                heading_stack = update_heading_stack(heading_stack, heading)
                current_path = [title for _, title in heading_stack]
                current_level = heading.level
                current_title = heading.title
                current_units = [unit]
            else:
                current_units.append(unit)
        flush()

        if not sections:
            sections = [([], None, None, units)]

        logical_parents: list[LogicalParentSpec] = []
        fallback_document = heading_count == 0
        for section_index, (path, level, title, section_units) in enumerate(
            sections, start=1
        ):
            child_groups = chunk_text_units(
                section_units, max_chars=self.child_chunk_size
            )
            child_specs: list[LogicalChildSpec] = []
            for group_index, group in enumerate(child_groups, start=1):
                text = join_unit_text(group)
                child_specs.append(
                    LogicalChildSpec(
                        text=text,
                        units=list(group),
                        representation_type="paragraph",
                        structure_path=path,
                        metadata={
                            "heading_path": list(path),
                            "heading_level": level,
                            "section_title": title,
                            "source_page_start": min(
                                [
                                    int(unit["page_start"])
                                    for unit in group
                                    if unit.get("page_start") is not None
                                ],
                                default=None,
                            ),
                            "source_page_end": max(
                                [
                                    int(unit["page_end"])
                                    for unit in group
                                    if unit.get("page_end") is not None
                                ],
                                default=None,
                            ),
                            "paragraph_group_index": group_index,
                        },
                        fallback_used=fallback_document,
                        fallback_reason=(
                            "no_heading_structure"
                            if fallback_document
                            else None
                        ),
                        fallback_strategy=(
                            "document_paragraph_grouping"
                            if fallback_document
                            else None
                        ),
                        actual_strategy=(
                            "document_paragraph_grouping"
                            if fallback_document
                            else self.STRATEGY
                        ),
                    )
                )

            # A very long section may span several parents, but no parent ever
            # crosses into another heading path.
            parent_children: list[LogicalChildSpec] = []
            parent_chars = 0
            part_index = 1
            for child in child_specs:
                addition = len(child.text) + (2 if parent_children else 0)
                if (
                    parent_children
                    and parent_chars + addition > self.parent_chunk_size
                ):
                    logical_parents.append(
                        LogicalParentSpec(
                            children=parent_children,
                            units=[
                                unit
                                for item in parent_children
                                for unit in item.units
                            ],
                            representation_type="heading_section",
                            structure_path=path,
                            title=title,
                            metadata={
                                "heading_path": list(path),
                                "heading_level": level,
                                "section_title": title,
                                "section_index": section_index,
                                "section_part_index": part_index,
                                "toc_units_filtered": toc_filtered,
                            },
                            fallback_used=fallback_document,
                            fallback_reason=(
                                "no_heading_structure"
                                if fallback_document
                                else None
                            ),
                            fallback_strategy=(
                                "document_paragraph_grouping"
                                if fallback_document
                                else None
                            ),
                            actual_strategy=(
                                "document_paragraph_grouping"
                                if fallback_document
                                else self.STRATEGY
                            ),
                        )
                    )
                    parent_children = []
                    parent_chars = 0
                    part_index += 1
                parent_children.append(child)
                parent_chars += addition
            if parent_children:
                logical_parents.append(
                    LogicalParentSpec(
                        children=parent_children,
                        units=[
                            unit
                            for item in parent_children
                            for unit in item.units
                        ],
                        representation_type="heading_section",
                        structure_path=path,
                        title=title,
                        metadata={
                            "heading_path": list(path),
                            "heading_level": level,
                            "section_title": title,
                            "section_index": section_index,
                            "section_part_index": part_index,
                            "toc_units_filtered": toc_filtered,
                        },
                        fallback_used=fallback_document,
                        fallback_reason=(
                            "no_heading_structure" if fallback_document else None
                        ),
                        fallback_strategy=(
                            "document_paragraph_grouping"
                            if fallback_document
                            else None
                        ),
                        actual_strategy=(
                            "document_paragraph_grouping"
                            if fallback_document
                            else self.STRATEGY
                        ),
                    )
                )

        result = materialize_document(
            doc_id=doc_id,
            parents=logical_parents,
            original_strategy=self.STRATEGY,
            parent_chunk_version=self.parent_chunk_version,
            child_chunk_version=self.child_chunk_version,
            created_at=self.created_at,
            chunker_name=self.chunker_name,
        )
        self._stats = {
            "heading_count": heading_count,
            "toc_units_filtered": toc_filtered,
            "fallback_document_count": int(fallback_document),
        }
        return result

    def chunk_records(
        self, records: Iterable[dict[str, Any]]
    ) -> ParentChildChunkResult:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise ValueError("heading chunking requires doc_id")
            groups[doc_id].append(dict(record))
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        for doc_id in sorted(groups):
            result = self._chunk_doc(doc_id, groups[doc_id])
            parents.extend(result.parents)
            children.extend(result.children)
        return ParentChildChunkResult(parents=parents, children=children)

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "strategy": self.STRATEGY,
            "parent_boundary": "heading_path",
            "child_boundary": "paragraph_or_list_group",
            "parent_chunk_size": self.parent_chunk_size,
            "child_chunk_size": self.child_chunk_size,
            **self._stats,
        }


__all__ = ["HeadingParagraphChunker"]
