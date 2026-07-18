"""Map legacy retrieval records to canonical evidence schemas."""

from __future__ import annotations

from typing import Any, Dict, List

from schemas.citation import CitationSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema


class LegacyEvidenceMapper:
    @staticmethod
    def context_text(item: Dict[str, Any]) -> str:
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
    def match_text(item: Dict[str, Any]) -> str:
        return str(
            item.get("match_text")
            or item.get("child_text")
            or item.get("match_text_preview")
            or item.get("text")
            or item.get("text_preview")
            or ""
        )

    def chunks(self, contexts: List[Dict[str, Any]]) -> List[RetrievedChunkSchema]:
        chunks: List[RetrievedChunkSchema] = []
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
            item_metadata = dict(item.get("metadata") or {})
            item_metadata.setdefault(
                "pre_output_rank",
                item.get("pre_context_rank", item.get("rank")),
            )
            chunks.append(
                RetrievedChunkSchema(
                    # Agent-facing ranks are always contiguous after packing.
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
                    retrieval_sources=list(
                        dict.fromkeys(
                            (item.get("metadata") or {}).get("retrieval_sources")
                            or item.get("retrieval_sources")
                            or []
                        )
                    ),
                    metadata=item_metadata,
                    extra={"source": "rag-template"},
                )
            )
        return chunks

    @staticmethod
    def context(
        chunks: List[RetrievedChunkSchema],
        max_context_chars: int,
    ) -> RAGContextSchema:
        parts: List[str] = []
        for chunk in chunks:
            parts.append(f"[{chunk.rank}] {chunk.title or chunk.doc_id}\n{chunk.context_text}")
        text = "\n\n".join(parts)[:max_context_chars]
        return RAGContextSchema(
            context_text=text,
            used_context_chunk_ids=[chunk.context_chunk_id for chunk in chunks],
            matched_chunk_ids=[chunk.matched_chunk_id for chunk in chunks],
            used_doc_ids=list(dict.fromkeys(chunk.doc_id for chunk in chunks)),
            max_context_chars=max_context_chars,
            used_context_chars=len(text),
            context_item_count=len(chunks),
            context_format="markdown",
            extra={"source": "rag-template"},
        )

    @staticmethod
    def citations(chunks: List[RetrievedChunkSchema]) -> List[CitationSchema]:
        citations: List[CitationSchema] = []
        seen_child_ids: set[str] = set()

        def append(
            *,
            chunk: RetrievedChunkSchema,
            child_id: str,
            quote_text: str,
            title: Any = None,
            section: Any = None,
            page_start: Any = None,
            page_end: Any = None,
            extra: Dict[str, Any] | None = None,
        ) -> None:
            child_id = str(child_id or "").strip()
            quote_text = str(quote_text or "").strip()
            if not child_id or not quote_text or child_id in seen_child_ids:
                return
            seen_child_ids.add(child_id)
            citations.append(
                CitationSchema(
                    citation_id=f"citation_real_rag_{len(citations)+1:03d}",
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
                    summary="来自 rag-template 的匹配子块证据。",
                    confidence=(
                        chunk.rerank_score
                        if chunk.rerank_score is not None
                        else chunk.score
                    ),
                    extra={"source": "rag-template", **(extra or {})},
                )
            )

        for chunk in chunks:
            for child in (chunk.metadata or {}).get("matched_child_chunks") or []:
                if not isinstance(child, dict):
                    continue
                append(
                    chunk=chunk,
                    child_id=child.get("child_chunk_id") or child.get("chunk_id") or "",
                    quote_text=child.get("text") or child.get("child_text") or "",
                    title=child.get("title"),
                    section=child.get("section"),
                    page_start=child.get("page_start"),
                    page_end=child.get("page_end"),
                    extra={"expanded_from_parent_match": True},
                )
            append(
                chunk=chunk,
                child_id=(
                    chunk.child_chunk_id
                    or chunk.matched_chunk_id
                    or chunk.context_chunk_id
                ),
                quote_text=chunk.match_text,
                extra={"expanded_from_parent_match": False},
            )
        return citations
