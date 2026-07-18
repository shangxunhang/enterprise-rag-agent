"""Build and consume the Step 12 canonical RAG evidence contract."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

from schemas.citation import CitationSchema
from schemas.rag import (
    EvidenceAssessmentStatus,
    EvidenceDisposition,
    RAGContextSchema,
    RAGEvidenceAssessmentSchema,
    RAGEvidenceContractSchema,
    RAGEvidenceItemSchema,
    RAGEvidenceLineageSchema,
    RAGTraceSchema,
    RetrievedChunkSchema,
)


class RAGEvidenceContractBuilder:
    """Normalize retrieval evidence and render its prompt projection."""

    @staticmethod
    def _stable_unique(values: Iterable[str | None]) -> List[str]:
        return list(dict.fromkeys(str(value) for value in values if str(value or "").strip()))

    @staticmethod
    def _normalize_citations(citations: Sequence[CitationSchema]) -> List[CitationSchema]:
        normalized: List[CitationSchema] = []
        seen_sources: set[tuple[str, str, str]] = set()
        for citation in citations:
            source_key = (
                str(citation.doc_id or citation.source_document_id or ""),
                str(citation.child_chunk_id or citation.chunk_id or ""),
                str(citation.quote_text or ""),
            )
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            data = citation.model_dump()
            original_id = str(citation.citation_id or "").strip()
            extra = dict(data.get("extra") or {})
            if original_id:
                extra.setdefault("source_citation_id", original_id)
            data["citation_id"] = f"C{len(normalized) + 1}"
            data["extra"] = extra
            normalized.append(CitationSchema.model_validate(data))
        return normalized

    @staticmethod
    def _citation_ids_for_chunk(
        chunk: RetrievedChunkSchema,
        citations: Sequence[CitationSchema],
    ) -> List[str]:
        ids: List[str] = []
        for citation in citations:
            same_parent = bool(
                citation.parent_chunk_id
                and citation.parent_chunk_id
                in {chunk.parent_chunk_id, chunk.context_chunk_id}
            )
            same_child = bool(
                (citation.child_chunk_id or citation.chunk_id)
                and (citation.child_chunk_id or citation.chunk_id)
                in {chunk.child_chunk_id, chunk.matched_chunk_id}
            )
            if same_parent or same_child:
                ids.append(citation.citation_id)
        return list(dict.fromkeys(ids))

    @staticmethod
    def _lineage(trace: RAGTraceSchema | None) -> RAGEvidenceLineageSchema:
        if trace is None:
            return RAGEvidenceLineageSchema()
        extra = dict(trace.extra or {})
        result_metadata = extra.get("rag_result_metadata")
        result_metadata = result_metadata if isinstance(result_metadata, dict) else {}
        offline_index = result_metadata.get("offline_index")
        offline_index = offline_index if isinstance(offline_index, dict) else {}
        pipeline_config = result_metadata.get("pipeline_config")
        pipeline_config = pipeline_config if isinstance(pipeline_config, dict) else {}
        return RAGEvidenceLineageSchema(
            index_name=trace.index_name,
            index_version=trace.index_version,
            dataset_version=offline_index.get("dataset_version"),
            vector_db=trace.vector_db,
            embedding_model=trace.embedding_model,
            embedding_version=trace.embedding_version,
            embedding_dim=offline_index.get("embedding_dim"),
            reranker_model=trace.reranker_model,
            reranker_version=trace.reranker_version,
            retrieval_strategy=trace.retrieval_mode,
            pipeline_profile_id=pipeline_config.get("profile_id"),
            pipeline_profile_version=pipeline_config.get("profile_version"),
            pipeline_config_hash=pipeline_config.get("hash"),
            extra={
                "trace_schema_version": trace.schema_version,
                "offline_index": offline_index,
            },
        )

    @classmethod
    def _item(
        cls,
        *,
        evidence_id: str,
        chunk: RetrievedChunkSchema,
        disposition: EvidenceDisposition,
        citations: Sequence[CitationSchema],
    ) -> RAGEvidenceItemSchema:
        metadata = dict(chunk.metadata or {})
        pre_context_rank = metadata.get("pre_output_rank", metadata.get("pre_context_rank"))
        try:
            pre_context_rank = int(pre_context_rank) if pre_context_rank is not None else None
        except (TypeError, ValueError):
            pre_context_rank = None
        drop_reason = metadata.get("context_drop_reason") if disposition == EvidenceDisposition.DROPPED else None
        citation_ids = (
            cls._citation_ids_for_chunk(chunk, citations)
            if disposition == EvidenceDisposition.SELECTED
            else []
        )
        return RAGEvidenceItemSchema(
            evidence_id=evidence_id,
            disposition=disposition,
            rank=chunk.rank,
            pre_context_rank=pre_context_rank,
            matched_chunk_id=chunk.matched_chunk_id,
            context_chunk_id=chunk.context_chunk_id,
            child_chunk_id=chunk.child_chunk_id,
            parent_chunk_id=chunk.parent_chunk_id,
            doc_id=chunk.doc_id,
            match_text=chunk.match_text,
            context_text=chunk.context_text,
            title=chunk.title,
            section=chunk.section,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            score=chunk.score,
            score_type=chunk.score_type,
            rerank_score=chunk.rerank_score,
            rerank_score_type=chunk.rerank_score_type,
            retrieval_sources=list(chunk.retrieval_sources),
            citation_ids=citation_ids,
            drop_reason=str(drop_reason) if drop_reason else None,
            metadata=metadata,
            extra={
                **dict(chunk.extra or {}),
                "matched_granularity": chunk.matched_granularity,
                "context_granularity": chunk.context_granularity,
            },
        )

    @staticmethod
    def _render_context(
        items: Sequence[RAGEvidenceItemSchema],
        *,
        max_context_chars: int,
    ) -> RAGContextSchema:
        parts: List[str] = []
        for item in items:
            marker = " ".join(f"[{citation_id}]" for citation_id in item.citation_ids)
            if not marker:
                marker = f"[{item.evidence_id}]"
            title = item.title or item.doc_id
            parts.append(f"{marker} {title}\n{item.context_text}".strip())
        text = "\n\n".join(parts)[:max_context_chars]
        return RAGContextSchema(
            context_text=text,
            used_context_chunk_ids=[item.context_chunk_id for item in items],
            matched_chunk_ids=[item.matched_chunk_id for item in items],
            used_doc_ids=RAGEvidenceContractBuilder._stable_unique(item.doc_id for item in items),
            max_context_chars=max_context_chars,
            used_context_chars=len(text),
            context_item_count=len(items),
            context_format="markdown",
            extra={
                "derived_from": "rag_evidence_contract_v1",
                "citation_ids_normalized": True,
            },
        )

    @classmethod
    def build(
        cls,
        *,
        query: str,
        rewritten_queries: Sequence[str] | None,
        selected_chunks: Sequence[RetrievedChunkSchema],
        dropped_chunks: Sequence[RetrievedChunkSchema] | None,
        citations: Sequence[CitationSchema],
        trace: RAGTraceSchema | None,
        max_context_chars: int,
        extra: Dict[str, Any] | None = None,
    ) -> RAGEvidenceContractSchema:
        normalized_citations = cls._normalize_citations(citations)
        items: List[RAGEvidenceItemSchema] = []
        selected_ids: List[str] = []
        dropped_ids: List[str] = []

        for chunk in selected_chunks:
            evidence_id = f"E{len(items) + 1}"
            items.append(
                cls._item(
                    evidence_id=evidence_id,
                    chunk=chunk,
                    disposition=EvidenceDisposition.SELECTED,
                    citations=normalized_citations,
                )
            )
            selected_ids.append(evidence_id)

        selected_keys = {
            (item.matched_chunk_id, item.context_chunk_id)
            for item in items
            if item.disposition == EvidenceDisposition.SELECTED
        }
        for chunk in dropped_chunks or []:
            key = (chunk.matched_chunk_id, chunk.context_chunk_id)
            if key in selected_keys:
                continue
            evidence_id = f"E{len(items) + 1}"
            items.append(
                cls._item(
                    evidence_id=evidence_id,
                    chunk=chunk,
                    disposition=EvidenceDisposition.DROPPED,
                    citations=normalized_citations,
                )
            )
            dropped_ids.append(evidence_id)

        selected_items = [item for item in items if item.evidence_id in set(selected_ids)]
        context = cls._render_context(selected_items, max_context_chars=max_context_chars)
        reason_codes: List[str] = []
        if not selected_items:
            reason_codes.append("NO_SELECTED_EVIDENCE")
        if selected_items and not normalized_citations:
            reason_codes.append("NO_GROUNDING_CITATIONS")

        return RAGEvidenceContractSchema(
            query=query,
            rewritten_queries=list(rewritten_queries or []),
            items=items,
            selected_evidence_ids=selected_ids,
            dropped_evidence_ids=dropped_ids,
            citations=normalized_citations,
            context=context,
            lineage=cls._lineage(trace),
            assessment=RAGEvidenceAssessmentSchema(
                status=EvidenceAssessmentStatus.NOT_ASSESSED,
                evidence_available=bool(selected_items),
                selected_evidence_count=len(selected_items),
                dropped_evidence_count=len(dropped_ids),
                citation_count=len(normalized_citations),
                reason_codes=reason_codes,
                details={
                    "presence_is_not_semantic_sufficiency": True,
                },
            ),
            extra={
                "contract_owner": "rag_service_boundary",
                "context_is_projection": True,
                **dict(extra or {}),
            },
        )


class RAGEvidenceContractReader:
    """Read Step 12 contracts, with a compatibility fallback for old payloads."""

    @staticmethod
    def projections(
        contract: RAGEvidenceContractSchema,
    ) -> Tuple[RAGContextSchema, List[RetrievedChunkSchema], List[CitationSchema]]:
        item_by_id = {item.evidence_id: item for item in contract.items}
        chunks: List[RetrievedChunkSchema] = []
        for evidence_id in contract.selected_evidence_ids:
            item = item_by_id[evidence_id]
            chunks.append(
                RetrievedChunkSchema(
                    rank=item.rank,
                    score=item.score,
                    score_type=item.score_type,
                    rerank_score=item.rerank_score,
                    rerank_score_type=item.rerank_score_type,
                    matched_chunk_id=item.matched_chunk_id,
                    context_chunk_id=item.context_chunk_id,
                    child_chunk_id=item.child_chunk_id,
                    parent_chunk_id=item.parent_chunk_id,
                    doc_id=item.doc_id,
                    matched_granularity=str(item.extra.get("matched_granularity") or "child"),
                    context_granularity=str(item.extra.get("context_granularity") or "parent"),
                    match_text=item.match_text,
                    context_text=item.context_text,
                    title=item.title,
                    section=item.section,
                    page_start=item.page_start,
                    page_end=item.page_end,
                    retrieval_sources=list(item.retrieval_sources),
                    metadata=dict(item.metadata),
                    extra={**dict(item.extra), "evidence_id": item.evidence_id},
                )
            )
        return contract.context, chunks, list(contract.citations)
