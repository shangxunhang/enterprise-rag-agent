"""把旧 RAG ToolResult/payload 升级为 canonical Evidence Contract。

新主链不会在此执行检索。若 payload 已包含结构化证据契约，本适配器只校验并重建兼容
投影；若只有旧 Chunk/引用字段，则补齐最小来源信息并通过 Builder 构造统一契约。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from contracts.evidence import (
    build_evidence_bundle,
    evidence_bundle_sha256,
    project_evidence_bundle,
)
from schemas.citation import CitationSchema
from schemas.rag import (
    EvidenceBundleSchema,
    RAGContextSchema,
    RAGEvidenceContractSchema,
    RAGTraceSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolResultSchema


SUPPORTED_LEGACY_TOOL_RESULT_VERSION = "tool_result_v1"
SUPPORTED_LEGACY_RAG_OUTPUT_VERSION = "rag_tool_output_v1"
SUPPORTED_CANONICAL_EVIDENCE_VERSION = "rag_evidence_contract_v1"


class LegacyEvidenceAdapter:
    """集中承担历史协议升级，避免兼容分支散落在生成服务中。"""

    @staticmethod
    def upgrade(
        shared_state: SharedStateSchema,
        result: Optional[ToolResultSchema],
    ) -> EvidenceBundleSchema:
        """Upgrade one legacy ToolResult into the canonical typed Evidence Bundle.

        This is the sole compatibility ingress.  After this method returns, normal
        production code must consume the typed Bundle directly and may only create
        one-way projections from it.
        """
        if result is None or not result.success:
            raise ValueError("successful legacy ToolResult is required")
        if result.schema_version != SUPPORTED_LEGACY_TOOL_RESULT_VERSION:
            raise ValueError(
                "unsupported legacy ToolResult schema_version: "
                f"{result.schema_version!r}"
            )
        payload = result.result or {}
        raw_payload_version = payload.get("schema_version")
        if not isinstance(raw_payload_version, str) or not raw_payload_version.strip():
            raise ValueError("legacy RAG payload must declare schema_version")
        if raw_payload_version != SUPPORTED_LEGACY_RAG_OUTPUT_VERSION:
            raise ValueError(
                "unsupported legacy RAG payload schema_version: "
                f"{raw_payload_version!r}"
            )
        raw_contract = payload.get("evidence")

        if raw_contract is not None:
            if not isinstance(raw_contract, dict):
                raise ValueError("legacy evidence field must be a versioned mapping")
            raw_contract_version = raw_contract.get("schema_version")
            if not isinstance(raw_contract_version, str) or not raw_contract_version.strip():
                raise ValueError("canonical Evidence payload must declare schema_version")
            if raw_contract_version != SUPPORTED_CANONICAL_EVIDENCE_VERSION:
                raise ValueError(
                    "unsupported canonical Evidence schema_version: "
                    f"{raw_contract_version!r}"
                )
            return RAGEvidenceContractSchema.model_validate(raw_contract)

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
        return build_evidence_bundle(
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
        """Return compatibility projections without exporting a full Bundle copy."""
        contract = LegacyEvidenceAdapter.upgrade(shared_state, result)
        context, chunks, citations = project_evidence_bundle(contract)
        payload = result.result if result and result.success else {}
        payload = payload or {}
        normalized = {
            key: value
            for key, value in dict(payload).items()
            if key not in {"evidence", "evidence_contract"}
        }
        normalized.update(
            {
                "schema_version": SUPPORTED_LEGACY_RAG_OUTPUT_VERSION,
                "task_id": contract.task_id or shared_state.task_id,
                "run_id": contract.run_id or shared_state.run_id,
                "status": (
                    contract.status.value
                    if hasattr(contract.status, "value")
                    else str(contract.status)
                ),
                "query": contract.query,
                "retrieval_scope": contract.retrieval_scope,
                "retrieval_trace_id": contract.retrieval_trace_id,
                "evidence_contract_sha256": evidence_bundle_sha256(contract),
                "lineage": contract.lineage.model_dump(),
                "context": context.model_dump(),
                "retrieved_chunks": [item.model_dump() for item in chunks],
                "citations": [item.model_dump() for item in citations],
                "trace": contract.trace.model_dump() if contract.trace else None,
            }
        )
        return context, chunks, citations, normalized
