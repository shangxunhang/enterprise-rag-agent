"""Derive citation-level sources from one parent/child retrieval record."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CitationSource:
    """One child-level source that can ground a parent-level context item."""

    child_id: str
    quote_text: str
    title: Any = None
    section: Any = None
    page_start: Any = None
    page_end: Any = None
    expanded_from_parent_match: bool = False


def citation_sources(
    *,
    metadata: Mapping[str, Any] | None,
    fallback_child_id: Any,
    fallback_quote_text: Any,
    fallback_title: Any = None,
    fallback_section: Any = None,
    fallback_page_start: Any = None,
    fallback_page_end: Any = None,
) -> list[CitationSource]:
    """Return stable, child-level citation sources for a retrieval item.

    Parent enrichment may attach several matched child chunks. They are the
    preferred grounding spans; the primary matched child is retained as a
    fallback when the enrichment metadata is absent or incomplete.
    """

    sources: list[CitationSource] = []
    seen_child_ids: set[str] = set()

    def append(
        *,
        child_id: Any,
        quote_text: Any,
        title: Any,
        section: Any,
        page_start: Any,
        page_end: Any,
        expanded: bool,
    ) -> None:
        normalized_child_id = str(child_id or "").strip()
        normalized_quote = str(quote_text or "").strip()
        if (
            not normalized_child_id
            or not normalized_quote
            or normalized_child_id in seen_child_ids
        ):
            return
        seen_child_ids.add(normalized_child_id)
        sources.append(
            CitationSource(
                child_id=normalized_child_id,
                quote_text=normalized_quote,
                title=title,
                section=section,
                page_start=page_start,
                page_end=page_end,
                expanded_from_parent_match=expanded,
            )
        )

    raw_metadata = dict(metadata or {})
    for child in raw_metadata.get("matched_child_chunks") or []:
        if not isinstance(child, Mapping):
            continue
        append(
            child_id=child.get("child_chunk_id") or child.get("chunk_id"),
            quote_text=child.get("text") or child.get("child_text"),
            title=child.get("title"),
            section=child.get("section"),
            page_start=child.get("page_start"),
            page_end=child.get("page_end"),
            expanded=True,
        )

    append(
        child_id=fallback_child_id,
        quote_text=fallback_quote_text,
        title=fallback_title,
        section=fallback_section,
        page_start=fallback_page_start,
        page_end=fallback_page_end,
        expanded=False,
    )
    return sources
