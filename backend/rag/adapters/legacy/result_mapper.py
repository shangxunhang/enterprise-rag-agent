"""Map a successful legacy tool result to the canonical RAG response."""

from __future__ import annotations

from typing import Any, Dict, List

from rag.adapters.legacy.coercion import as_int, as_str_list
from rag.adapters.legacy.evidence_mapper import LegacyEvidenceMapper
from rag.adapters.legacy.request_mapper import LegacyInvocation
from rag.evidence.contract import RAGEvidenceContractBuilder, RAGEvidenceContractReader
from schemas.rag import RAGToolOutputSchema, RAGTraceSchema


class LegacyRAGResultMapper:
    def __init__(self, evidence: LegacyEvidenceMapper | None = None) -> None:
        self.evidence = evidence or LegacyEvidenceMapper()

    @staticmethod
    def query_expansion(result: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        metadata = result.get("metadata") or {}
        nested = metadata.get("metadata")
        for candidate in (
            data.get("query_expansion"),
            metadata.get("query_expansion"),
            nested.get("query_expansion") if isinstance(nested, dict) else None,
        ):
            if isinstance(candidate, dict):
                return candidate
        return {}

    @staticmethod
    def strategy_payload(result: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        metadata = result.get("metadata") or {}
        return {
            "original_retrieval_strategy": (
                data.get("original_retrieval_strategy")
                or metadata.get("original_retrieval_strategy")
                or metadata.get("retrieval_strategy")
            ),
            "effective_retrieval_strategy": (
                data.get("effective_retrieval_strategy")
                or metadata.get("effective_retrieval_strategy")
                or metadata.get("retrieval_strategy")
            ),
            "adaptive_rag": data.get("adaptive_rag") or metadata.get("adaptive_rag") or {},
            "adaptive_profile_router": (
                data.get("adaptive_profile_router")
                or metadata.get("adaptive_profile_router")
                or {}
            ),
            "c_rag": data.get("c_rag") or metadata.get("c_rag") or {},
            "self_rag": data.get("self_rag") or metadata.get("self_rag") or {},
            "repair": data.get("repair") or metadata.get("repair") or {},
        }

    @staticmethod
    def _record_key(item: Dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("child_chunk_id") or item.get("matched_chunk_id") or item.get("chunk_id") or ""),
            str(item.get("parent_chunk_id") or item.get("context_chunk_id") or item.get("chunk_id") or ""),
        )

    def _selected_and_dropped_records(
        self,
        *,
        data: Dict[str, Any],
        context_pack: Dict[str, Any],
        max_context_items: int,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        selected = context_pack.get("selected_results") or []
        full = data.get("retrieval_results") or []
        compact = data.get("contexts") or []
        dropped = context_pack.get("dropped_results") or []

        selected_records: List[Dict[str, Any]]
        if isinstance(selected, list) and selected:
            selected_records = [item for item in selected if isinstance(item, dict)]
        elif isinstance(full, list) and full:
            selected_records = [item for item in full[:max_context_items] if isinstance(item, dict)]
        elif isinstance(compact, list):
            selected_records = [item for item in compact[:max_context_items] if isinstance(item, dict)]
        else:
            selected_records = []

        dropped_records = [item for item in dropped if isinstance(item, dict)] if isinstance(dropped, list) else []
        selected_keys = {self._record_key(item) for item in selected_records}
        dropped_keys = {self._record_key(item) for item in dropped_records}

        # Older context packers did not return dropped_results. Preserve all
        # candidates and label those omitted from the final context.
        if isinstance(full, list):
            for item in full:
                if not isinstance(item, dict):
                    continue
                key = self._record_key(item)
                if key in selected_keys or key in dropped_keys:
                    continue
                normalized = dict(item)
                metadata = dict(normalized.get("metadata") or {})
                metadata.setdefault("context_drop_reason", "not_selected_by_context_packer")
                normalized["metadata"] = metadata
                dropped_records.append(normalized)
                dropped_keys.add(key)

        return selected_records, dropped_records

    def map(
        self,
        *,
        request,
        invocation: LegacyInvocation,
        raw_result: Dict[str, Any],
        latency_ms: int,
        rag_project_root: str,
        skip_rerank: bool,
        service_name: str,
    ) -> RAGToolOutputSchema:
        if not raw_result.get("success"):
            raise RuntimeError(raw_result.get("error") or "rag-template RAGTool failed.")
        data = raw_result.get("data") or {}
        metadata = raw_result.get("metadata") or {}
        query_expansion = self.query_expansion(raw_result, data)
        rewritten_queries = as_str_list(query_expansion.get("rewritten_queries"))
        if not rewritten_queries:
            rewritten_queries = as_str_list(invocation.tool_input.get("rewritten_queries"))
        strategy = self.strategy_payload(raw_result, data)
        context_pack = data.get("context_pack") or {}
        selected_records, dropped_records = self._selected_and_dropped_records(
            data=data,
            context_pack=context_pack,
            max_context_items=invocation.max_context_items,
        )
        selected_chunks = self.evidence.chunks(selected_records)
        dropped_chunks = self.evidence.chunks(dropped_records)
        raw_citations = self.evidence.citations(selected_chunks)

        effective_strategy = str(
            strategy.get("effective_retrieval_strategy")
            or invocation.retrieval_strategy
        )
        offline_index = metadata.get("offline_index") if isinstance(metadata.get("offline_index"), dict) else {}
        trace = RAGTraceSchema(
            retrieval_mode=effective_strategy,
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            embedding_model=str(offline_index.get("embedding_model") or "rag-template-config"),
            embedding_version=str(offline_index.get("embedding_version") or "unknown"),
            reranker_model="rag-template-config",
            reranker_version="unknown",
            index_name=str(offline_index.get("collection_name") or "rag_child_chunks"),
            index_version=str(offline_index.get("index_version") or "legacy_unversioned_index"),
            vector_db=str(offline_index.get("backend") or "milvus-lite"),
            dense_top_k=as_int(invocation.tool_input.get("dense_top_k"), 10),
            keyword_top_k=as_int(invocation.tool_input.get("keyword_top_k"), 10),
            candidate_top_k=as_int(invocation.tool_input.get("candidate_top_k"), 10),
            rerank_top_k=as_int(invocation.tool_input.get("rerank_top_k"), 5),
            max_context_chars=invocation.max_context_chars,
            retrieved_count=len(selected_chunks) + len(dropped_chunks),
            reranked_count=len(selected_chunks) + len(dropped_chunks),
            context_item_count=len(selected_chunks),
            latency_ms=latency_ms,
            extra={
                "rag_project_root": rag_project_root,
                "rag_result_metadata": metadata,
                "query_expansion": query_expansion,
                "query_expansion_rewritten_query_count": len(rewritten_queries),
                "original_retrieval_strategy": (
                    strategy.get("original_retrieval_strategy")
                    or invocation.retrieval_strategy
                ),
                "effective_retrieval_strategy": effective_strategy,
                "adaptive_rag": strategy.get("adaptive_rag"),
                "adaptive_profile_router": strategy.get("adaptive_profile_router"),
                "c_rag": strategy.get("c_rag"),
                "self_rag": strategy.get("self_rag"),
                "repair": strategy.get("repair"),
                "rag_run_id": data.get("run_id"),
                "generate_answer": invocation.generate_answer,
                "skip_rerank": skip_rerank,
                "context_source": "rag_evidence_contract_v1",
                "legacy_context_source": (
                    "rag_template_context_pack"
                    if str(context_pack.get("context") or data.get("packed_context") or "")
                    else "agent_rebuilt_context"
                ),
                "packing_strategy": context_pack.get("packing_strategy"),
                "context_pack_selected_count": context_pack.get("selected_count"),
                "context_pack_dropped_count": context_pack.get("dropped_count"),
            },
        )
        evidence_contract = RAGEvidenceContractBuilder.build(
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            selected_chunks=selected_chunks,
            dropped_chunks=dropped_chunks,
            citations=raw_citations,
            trace=trace,
            max_context_chars=invocation.max_context_chars,
            extra={
                "source": "rag-template",
                "packing_strategy": context_pack.get("packing_strategy"),
                "legacy_selected_count": context_pack.get("selected_count"),
                "legacy_dropped_count": context_pack.get("dropped_count"),
            },
        )
        context, projected_chunks, citations = RAGEvidenceContractReader.projections(
            evidence_contract
        )
        return RAGToolOutputSchema(
            task_id=request.task_id,
            run_id=request.run_id,
            status="success",
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            evidence=evidence_contract,
            retrieved_chunks=projected_chunks,
            context=context,
            citations=citations,
            trace=trace,
            answer=data.get("answer"),
            extra={
                "source": "rag-template",
                "rag_service": service_name,
                "rag_tool_raw_success": True,
                "evidence_contract": "rag_evidence_contract_v1",
                "context_source": "rag_evidence_contract_v1",
                "packing_strategy": context_pack.get("packing_strategy"),
                "query_expansion": query_expansion,
                "query_expansion_rewritten_query_count": len(rewritten_queries),
                "original_retrieval_strategy": (
                    strategy.get("original_retrieval_strategy")
                    or invocation.retrieval_strategy
                ),
                "effective_retrieval_strategy": effective_strategy,
                "adaptive_rag": strategy.get("adaptive_rag"),
                "adaptive_profile_router": strategy.get("adaptive_profile_router"),
                "c_rag": strategy.get("c_rag"),
                "self_rag": strategy.get("self_rag"),
                "repair": strategy.get("repair"),
            },
        )
