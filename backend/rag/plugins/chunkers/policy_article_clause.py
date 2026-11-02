"""Deterministic policy chapter/article/clause chunking."""

from __future__ import annotations

import re
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
    join_unit_text,
    materialize_document,
    normalize_units,
)


_CN_NUM = r"[一二三四五六七八九十百千万零〇0-9]+"
_CHAPTER_RE = re.compile(rf"^第\s*({_CN_NUM})\s*(章|篇|部分)\s*(.*)$")
_SECTION_RE = re.compile(rf"^第\s*({_CN_NUM})\s*节\s*(.*)$")
_ARTICLE_RE = re.compile(rf"^第\s*({_CN_NUM})\s*条\s*(.*)$")
_CLAUSE_RE = re.compile(
    rf"^(（({_CN_NUM})）|\((\d+)\)|({_CN_NUM})、|(\d+)[\.、)])\s*(.*)$"
)
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)[\.、)]?\s*(.+)$")
_TOC_TRAILER_RE = re.compile(r"(?:\.{2,}|…{2,}|\s)(?:\d+|[IVXLCDM]+)\s*$")


def _normalize_id(prefix: str, value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return f"{prefix}_{str(value).strip()}"


class PolicyArticleClauseChunker:
    """Build policy parents from articles and atomic children from clauses."""

    STRATEGY = "policy_article_clause"

    def __init__(
        self,
        *,
        parent_chunk_size: int = 2400,
        parent_chunk_overlap: int = 0,
        child_chunk_size: int = 900,
        child_chunk_overlap: int = 0,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "policy_article_clause",
        build_context: Any = None,
    ) -> None:
        self.parent_chunk_size = int(parent_chunk_size)
        self.child_chunk_size = int(child_chunk_size)
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name
        self._stats: dict[str, int] = {}

    @staticmethod
    def _toc_marker(text: str) -> bool:
        return "".join(str(text or "").split()) in {"目录", "目次"}

    @staticmethod
    def _toc_entry(text: str) -> bool:
        return bool(_TOC_TRAILER_RE.search(str(text or "").strip()))

    def _chunk_doc(
        self, doc_id: str, records: list[dict[str, Any]]
    ) -> ParentChildChunkResult:
        units = normalize_units(records)
        if not units:
            return ParentChildChunkResult(parents=[], children=[])
        policy_name = str(units[0].get("source_name") or units[0].get("title") or doc_id)

        chapter_id: str | None = None
        chapter_title: str | None = None
        section_id: str | None = None
        section_title: str | None = None
        article_id: str | None = None
        article_title: str | None = None
        clause_id: str | None = None
        clause_units: list[dict[str, Any]] = []
        clause_counter = 0
        structured_records: list[dict[str, Any]] = []
        toc_mode = False
        toc_filtered = 0
        structure_markers = 0

        def current_path() -> list[str]:
            return [
                value
                for value in (chapter_title, section_title, article_title, clause_id)
                if value
            ]

        def flush_clause() -> None:
            nonlocal clause_units, clause_counter
            if not clause_units:
                return
            clause_counter += 1
            effective_clause_id = clause_id or f"clause_{clause_counter:04d}"
            structured_records.append(
                {
                    "units": clause_units,
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "section_id": section_id,
                    "section_title": section_title,
                    "article_id": article_id,
                    "article_title": article_title,
                    "clause_id": effective_clause_id,
                    "clause_path": current_path() or [effective_clause_id],
                }
            )
            clause_units = []

        for unit in units:
            text = str(unit.get("text") or "").strip()
            if self._toc_marker(text):
                flush_clause()
                toc_mode = True
                toc_filtered += 1
                continue
            chapter_match = _CHAPTER_RE.match(text)
            section_match = _SECTION_RE.match(text)
            article_match = _ARTICLE_RE.match(text)
            clause_match = _CLAUSE_RE.match(text)
            numbered_match = _NUMBERED_HEADING_RE.match(text)

            if toc_mode:
                if self._toc_entry(text):
                    toc_filtered += 1
                    continue
                if not any((chapter_match, section_match, article_match, numbered_match)):
                    toc_filtered += 1
                    continue
                toc_mode = False

            if chapter_match:
                flush_clause()
                structure_markers += 1
                chapter_id = _normalize_id("chapter", chapter_match.group(1))
                chapter_title = text
                section_id = section_title = None
                article_id = article_title = None
                clause_id = None
                continue
            if section_match:
                flush_clause()
                structure_markers += 1
                section_id = _normalize_id("section", section_match.group(1))
                section_title = text
                article_id = article_title = None
                clause_id = None
                continue
            if article_match:
                flush_clause()
                structure_markers += 1
                article_id = _normalize_id("article", article_match.group(1))
                article_title = text
                clause_id = None
                clause_units = [unit]
                continue
            if numbered_match and unit.get("unit_type") in {"title", "list", "paragraph"}:
                flush_clause()
                structure_markers += 1
                article_id = _normalize_id("article", numbered_match.group(1))
                article_title = text
                clause_id = None
                clause_units = [unit]
                continue
            if clause_match:
                flush_clause()
                structure_markers += 1
                clause_id = str(clause_match.group(1)).strip()
                clause_units = [unit]
                continue
            clause_units.append(unit)
        flush_clause()

        logical_parents: list[LogicalParentSpec] = []
        fallback_chunks = 0
        if structured_records and structure_markers > 0:
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in structured_records:
                parent_key = str(
                    item.get("article_id")
                    or item.get("section_id")
                    or item.get("chapter_id")
                    or "policy_preamble"
                )
                grouped[parent_key].append(item)
            for parent_key, items in grouped.items():
                children: list[LogicalChildSpec] = []
                for item in items:
                    child_units = item["units"]
                    # A clause can contain several source paragraphs, but it is
                    # never merged with another article/clause boundary.
                    pieces = chunk_text_units(
                        child_units, max_chars=self.child_chunk_size
                    )
                    for piece_index, piece_units in enumerate(pieces, start=1):
                        path = list(item["clause_path"])
                        if len(pieces) > 1:
                            path = [*path, f"part_{piece_index}"]
                        metadata = {
                            "policy_name": policy_name,
                            "chapter_id": item.get("chapter_id"),
                            "chapter_title": item.get("chapter_title"),
                            "section_id": item.get("section_id"),
                            "section_title": item.get("section_title"),
                            "article_id": item.get("article_id"),
                            "article_title": item.get("article_title"),
                            "clause_id": item.get("clause_id"),
                            "clause_path": path,
                            "source_page": piece_units[0].get("page_start"),
                            "is_table_of_contents": False,
                        }
                        children.append(
                            LogicalChildSpec(
                                text=join_unit_text(piece_units),
                                units=list(piece_units),
                                representation_type="policy_clause",
                                structure_path=path,
                                metadata=metadata,
                                actual_strategy=self.STRATEGY,
                            )
                        )
                parent_units = [unit for child in children for unit in child.units]
                first = items[0]
                parent_path = [
                    value
                    for value in (
                        first.get("chapter_title"),
                        first.get("section_title"),
                        first.get("article_title"),
                    )
                    if value
                ]
                logical_parents.append(
                    LogicalParentSpec(
                        children=children,
                        units=parent_units,
                        representation_type="policy_article",
                        structure_path=parent_path or [parent_key],
                        title=(
                            first.get("article_title")
                            or first.get("section_title")
                            or first.get("chapter_title")
                            or policy_name
                        ),
                        metadata={
                            "policy_name": policy_name,
                            "chapter_id": first.get("chapter_id"),
                            "chapter_title": first.get("chapter_title"),
                            "section_id": first.get("section_id"),
                            "section_title": first.get("section_title"),
                            "article_id": first.get("article_id"),
                            "article_title": first.get("article_title"),
                            "is_table_of_contents": False,
                        },
                        actual_strategy=self.STRATEGY,
                    )
                )
        else:
            # Explicit fallback is allowed only when no reliable policy marker
            # survives TOC filtering.
            usable_units = [
                unit
                for unit in units
                if not self._toc_marker(str(unit.get("text") or ""))
                and not self._toc_entry(str(unit.get("text") or ""))
            ]
            groups = chunk_text_units(usable_units, max_chars=self.child_chunk_size)
            children: list[LogicalChildSpec] = []
            for index, group in enumerate(groups, start=1):
                fallback_chunks += 1
                children.append(
                    LogicalChildSpec(
                        text=join_unit_text(group),
                        units=list(group),
                        representation_type="text_evidence",
                        structure_path=[f"fallback_{index}"],
                        metadata={
                            "policy_name": policy_name,
                            "chapter_id": None,
                            "chapter_title": None,
                            "article_id": None,
                            "article_title": None,
                            "clause_id": None,
                            "clause_path": [f"fallback_{index}"],
                            "source_page": group[0].get("page_start"),
                            "is_table_of_contents": False,
                        },
                        fallback_used=True,
                        fallback_reason="no_policy_clause_structure",
                        fallback_strategy="paragraph_text_evidence",
                        actual_strategy="paragraph_text_evidence",
                    )
                )
            logical_parents = [
                LogicalParentSpec(
                    children=children,
                    units=usable_units,
                    representation_type="policy_document",
                    structure_path=[policy_name],
                    title=policy_name,
                    fallback_used=True,
                    fallback_reason="no_policy_clause_structure",
                    fallback_strategy="paragraph_text_evidence",
                    actual_strategy="paragraph_text_evidence",
                )
            ]

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
            "structure_marker_count": structure_markers,
            "toc_units_filtered": toc_filtered,
            "policy_clause_count": sum(
                1
                for child in result.children
                if (child.get("extra") or {})
                .get("type_aware_chunking", {})
                .get("representation_type")
                == "policy_clause"
            ),
            "fallback_chunk_count": fallback_chunks,
        }
        return result

    def chunk_records(
        self, records: Iterable[dict[str, Any]]
    ) -> ParentChildChunkResult:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise ValueError("policy chunking requires doc_id")
            groups[doc_id].append(dict(record))
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        for doc_id in sorted(groups):
            result = self._chunk_doc(doc_id, groups[doc_id])
            parents.extend(result.parents)
            children.extend(result.children)
        return ParentChildChunkResult(parents=parents, children=children)

    def execution_metadata(self) -> dict[str, Any]:
        return {"strategy": self.STRATEGY, **self._stats}


__all__ = ["PolicyArticleClauseChunker"]
