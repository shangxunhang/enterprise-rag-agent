"""LangChain BaseRetriever projection over the canonical enterprise RAG service."""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

from contracts.rag import RAGServicePort
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceDisposition,
    RAGToolInputSchema,
)
from schemas.status import ExecutionStatus


def evidence_bundle_to_documents(
    bundle: EvidenceBundleSchema,
    *,
    include_dropped: bool = False,
) -> list[Document]:
    """Project a canonical EvidenceBundle into LangChain Documents.

    This is intentionally a projection, not the canonical contract.
    EvidenceBundleSchema remains the source of truth for lineage, assessment,
    correction trace, citations, warnings and failure semantics.
    """
    selected_ids = set(bundle.selected_evidence_ids)
    dropped_ids = set(bundle.dropped_evidence_ids)

    documents: list[Document] = []
    for item in bundle.items:
        if not include_dropped and item.evidence_id not in selected_ids:
            continue
        if include_dropped and item.evidence_id not in selected_ids | dropped_ids:
            continue

        page_content = item.context_text or item.match_text
        metadata: dict[str, Any] = {
            "evidence_id": item.evidence_id,
            "disposition": item.disposition.value,
            "rank": item.rank,
            "pre_context_rank": item.pre_context_rank,
            "matched_chunk_id": item.matched_chunk_id,
            "context_chunk_id": item.context_chunk_id,
            "child_chunk_id": item.child_chunk_id,
            "parent_chunk_id": item.parent_chunk_id,
            "doc_id": item.doc_id,
            "title": item.title,
            "section": item.section,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "score": item.score,
            "score_type": item.score_type,
            "rerank_score": item.rerank_score,
            "rerank_score_type": item.rerank_score_type,
            "retrieval_sources": list(item.retrieval_sources),
            "citation_ids": list(item.citation_ids),
            "drop_reason": item.drop_reason,
            "match_text": item.match_text,
            "retrieval_trace_id": bundle.retrieval_trace_id,
            "query": bundle.query,
            "task_id": bundle.task_id,
            "run_id": bundle.run_id,
            "lineage": bundle.lineage.model_dump(mode="json"),
            "source_metadata": dict(item.metadata),
            "extra": dict(item.extra),
        }
        documents.append(Document(page_content=page_content, metadata=metadata))

    return documents


class LangChainRAGRetriever(BaseRetriever):
    """Expose RAGServicePort through LangChain's standard Retriever interface.

    `BaseRetriever` is intentionally a lossy interoperability view:
        str -> list[Document]

    For full enterprise semantics use `build_rag_runnable`, which preserves:
        RAGToolInputSchema -> EvidenceBundleSchema
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    rag_service: Any = Field(exclude=True)
    request_template: RAGToolInputSchema
    include_dropped: bool = False

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Any | None = None,
    ) -> list[Document]:
        del run_manager  # LangChain callback lifecycle is handled by BaseRetriever.

        request = self.request_template.model_copy(
            update={
                "query": str(query).strip(),
                # Query expansion belongs to the enterprise RAG runtime.
                # Do not leak a previous request's rewritten queries.
                "rewritten_queries": [],
            }
        )
        if not request.query:
            raise ValueError("query cannot be empty")

        service: RAGServicePort = self.rag_service
        bundle = service.retrieve(request)

        if bundle.status == ExecutionStatus.FAILED:
            message = (
                bundle.error.message
                if bundle.error is not None
                else "enterprise RAG retrieval failed"
            )
            raise RuntimeError(message)

        return evidence_bundle_to_documents(
            bundle,
            include_dropped=self.include_dropped,
        )
