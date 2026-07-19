"""Normalize retrieval records into the public evidence schemas."""

from __future__ import annotations

from typing import Any

from rag.evidence.citation_sources import citation_sources
from schemas.citation import CitationSchema
from schemas.rag import RetrievedChunkSchema


class EvidenceMapper:
    """Convert child/parent retrieval records without losing source detail."""

    @staticmethod
    def context_text(item: dict[str, Any]) -> str:
        return str(
            item.get("context_text")
            or item.get("parent_text")
            or item.get("text")
            or item.get("context_text_preview")
            or item.get("text_preview")
            or item.get("child_text")
            or ""
        )

    @staticmethod
    def match_text(item: dict[str, Any]) -> str:
        return str(
            item.get("match_text")
            or item.get("child_text")
            or item.get("match_text_preview")
            or item.get("text")
            or item.get("text_preview")
            or ""
        )

    def chunks(self, contexts: list[dict[str, Any]]) -> list[RetrievedChunkSchema]:
        chunks: list[RetrievedChunkSchema] = []
        for index, item in enumerate(contexts, start=1):
            if not isinstance(item, dict):
                continue
            doc_id = str(item.get("doc_id") or "unknown_doc")
            parent_chunk_id = str(
                item.get("parent_chunk_id")
                or item.get("chunk_id")
                or f"parent_{index}"
            )
            child_chunk_id = str(
                item.get("child_chunk_id")
                or item.get("matched_chunk_id")
                or f"child_{index}"
            )
            metadata = dict(item.get("metadata") or {})
            metadata.setdefault(
                "pre_output_rank",
                item.get("pre_context_rank", item.get("rank")),
            )
            sources = (
                metadata.get("retrieval_sources")
                or item.get("retrieval_sources")
                or []
            )
            chunks.append(
                RetrievedChunkSchema(
                    rank=index,
                    score=item.get("score"),
                    score_type="hybrid_score",
                    rerank_score=item.get("rerank_score"),
                    rerank_score_type="cross_encoder_or_noop",
                    matched_chunk_id=child_chunk_id,
                    context_chunk_id=parent_chunk_id,
                    child_chunk_id=child_chunk_id,
                    parent_chunk_id=parent_chunk_id,
                    doc_id=doc_id,
                    matched_granularity="child",
                    context_granularity="parent",
                    match_text=self.match_text(item),
                    context_text=self.context_text(item),
                    title=item.get("title"),
                    section=item.get("section"),
                    page_start=item.get("page_start"),
                    page_end=item.get("page_end"),
                    retrieval_sources=list(dict.fromkeys(sources)),
                    metadata=metadata,
                    extra={"source": "parent_child_retrieval"},
                )
            )
        return chunks

    @staticmethod
    def citations(chunks: list[RetrievedChunkSchema]) -> list[CitationSchema]:
        citations: list[CitationSchema] = []
        seen_child_ids: set[str] = set()

        def append_citation(
            *,
            chunk: RetrievedChunkSchema,
            child_id: str,
            quote_text: str,
            title: Any = None,
            section: Any = None,
            page_start: Any = None,
            page_end: Any = None,
            extra: dict[str, Any] | None = None,
        ) -> None:
            child_id = str(child_id or "").strip()
            quote_text = str(quote_text or "").strip()
            if not child_id or not quote_text or child_id in seen_child_ids:
                return
            seen_child_ids.add(child_id)
            citations.append(
                CitationSchema(
                    citation_id=f"citation_rag_{len(citations) + 1:03d}",
                    source_type="document",
                    doc_id=chunk.doc_id,
                    source_document_id=chunk.doc_id,
                    parent_chunk_id=chunk.parent_chunk_id,
                    child_chunk_id=child_id,
                    chunk_id=child_id,
                    title=title or chunk.title,
                    section=section or chunk.section,
                    page_start=(
                        page_start if page_start is not None else chunk.page_start
                    ),
                    page_end=page_end if page_end is not None else chunk.page_end,
                    quote_text=quote_text,
                    summary="来自匹配子块的检索证据。",
                    confidence=(
                        chunk.rerank_score
                        if chunk.rerank_score is not None
                        else chunk.score
                    ),
                    extra={"source": "parent_child_retrieval", **(extra or {})},
                )
            )

        for chunk in chunks:
            sources = citation_sources(
                metadata=chunk.metadata,
                fallback_child_id=(
                    chunk.child_chunk_id
                    or chunk.matched_chunk_id
                    or chunk.context_chunk_id
                ),
                fallback_quote_text=chunk.match_text,
                fallback_title=chunk.title,
                fallback_section=chunk.section,
                fallback_page_start=chunk.page_start,
                fallback_page_end=chunk.page_end,
            )
            for source in sources:
                append_citation(
                    chunk=chunk,
                    child_id=source.child_id,
                    quote_text=source.quote_text,
                    title=source.title,
                    section=source.section,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    extra={
                        "expanded_from_parent_match": (
                            source.expanded_from_parent_match
                        )
                    },
                )
        return citations
