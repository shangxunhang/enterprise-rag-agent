# =============================================================================
# 中文阅读说明：旧 RAG ToolResult / 兼容 payload 到 canonical evidence contract 的适配器。
# 新主链不在这里执行检索；这里只承担历史契约升级。
# =============================================================================
"""Compatibility adapter from legacy RAG tool payloads to canonical evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from observability.trace_summary import canonical_sha256

from agent.runtime.shared_state_schema import SharedStateSchema
from rag.evidence.contract import RAGEvidenceContractBuilder, RAGEvidenceContractReader
from schemas.citation import CitationSchema
from schemas.rag import (
    RAGContextSchema,
    RAGEvidenceContractSchema,
    RAGTraceSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolResultSchema


class LegacyEvidenceAdapter:
    """Upgrade legacy ToolResult payloads into the Step-12 evidence contract."""

    @staticmethod
    def extract(
        shared_state: SharedStateSchema,
        result: Optional[ToolResultSchema],
    ) -> Tuple[
        RAGContextSchema,
        List[RetrievedChunkSchema],
        List[CitationSchema],
        Dict[str, Any],
    ]:
        payload = result.result if result and result.success else {}
        payload = payload or {}
        raw_contract = payload.get("evidence")

        if isinstance(raw_contract, dict):
            contract = RAGEvidenceContractSchema.model_validate(raw_contract)
            context, chunks, citations = RAGEvidenceContractReader.projections(contract)
            context.extra = {
                **dict(context.extra or {}),
                "evidence_contract": contract.model_dump(),
                "evidence_contract_sha256": canonical_sha256(contract.model_dump()),
                "lineage": contract.lineage.model_dump(),
            }
            normalized = dict(payload)
            normalized["evidence"] = contract.model_dump()
            normalized["context"] = context.model_dump()
            normalized["retrieved_chunks"] = [item.model_dump() for item in chunks]
            normalized["citations"] = [item.model_dump() for item in citations]
            normalized.setdefault("schema_version", "rag_tool_output_v1")
            normalized.setdefault("task_id", shared_state.task_id)
            normalized.setdefault("run_id", shared_state.run_id)
            normalized.setdefault("status", "success")
            return context, chunks, citations, normalized

        chunks = [
            RetrievedChunkSchema.model_validate(item)
            for item in (payload.get("retrieved_chunks") or [])
            if isinstance(item, dict)
        ]
        raw_citations: list[CitationSchema] = []
        for index, item in enumerate(payload.get("citations") or [], start=1):
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            normalized_item.setdefault(
                "citation_id", f"citation_{shared_state.run_id}_{index:03d}"
            )
            normalized_item.setdefault("source_type", "document")
            normalized_item.setdefault(
                "source_document_id", normalized_item.get("doc_id")
            )
            raw_citations.append(CitationSchema.model_validate(normalized_item))

        if not raw_citations:
            for index, chunk in enumerate(chunks, start=1):
                raw_citations.append(
                    CitationSchema(
                        citation_id=f"retrieved_chunk_{index:03d}",
                        source_type="document",
                        doc_id=chunk.doc_id,
                        source_document_id=chunk.doc_id,
                        parent_chunk_id=chunk.parent_chunk_id,
                        child_chunk_id=chunk.child_chunk_id,
                        chunk_id=chunk.matched_chunk_id or chunk.context_chunk_id,
                        title=chunk.title,
                        section=chunk.section,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        quote_text=(chunk.match_text or chunk.context_text),
                        confidence=(
                            chunk.rerank_score
                            if chunk.rerank_score is not None
                            else chunk.score
                        ),
                        extra={"rebuilt_from_retrieved_chunk": True},
                    )
                )

        raw_context = payload.get("context") or {}
        max_context_chars = int(raw_context.get("max_context_chars") or 6000)
        raw_trace = payload.get("trace")
        trace = (
            RAGTraceSchema.model_validate(raw_trace)
            if isinstance(raw_trace, dict)
            else None
        )
        contract = RAGEvidenceContractBuilder.build(
            query=str(payload.get("query") or ""),
            rewritten_queries=payload.get("rewritten_queries") or [],
            selected_chunks=chunks,
            dropped_chunks=[],
            citations=raw_citations,
            trace=trace,
            max_context_chars=max_context_chars,
            extra={
                "compatibility_upgrade": True,
                "upgraded_by": "LegacyEvidenceAdapter",
            },
        )
        context, projected_chunks, citations = RAGEvidenceContractReader.projections(
            contract
        )
        context.extra = {
            **dict(context.extra or {}),
            "evidence_contract": contract.model_dump(),
            "evidence_contract_sha256": canonical_sha256(contract.model_dump()),
            "lineage": contract.lineage.model_dump(),
        }
        normalized = dict(payload)
        normalized.setdefault("schema_version", "rag_tool_output_v1")
        normalized.setdefault("task_id", shared_state.task_id)
        normalized.setdefault("run_id", shared_state.run_id)
        normalized.setdefault(
            "status", "success" if result and result.success else "failed"
        )
        normalized["evidence"] = contract.model_dump()
        normalized["context"] = context.model_dump()
        normalized["retrieved_chunks"] = [
            item.model_dump() for item in projected_chunks
        ]
        normalized["citations"] = [item.model_dump() for item in citations]
        return context, projected_chunks, citations, normalized
