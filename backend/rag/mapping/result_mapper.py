"""Map retrieval runtime output directly to the public evidence bundle."""

from __future__ import annotations

from typing import Any

from rag.common.coercion import as_str_list
from rag.evidence.contract import RAGEvidenceContractBuilder
from rag.mapping.evidence_mapper import EvidenceMapper
from rag.mapping.request_mapper import RAGInvocation
from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema, RAGTraceSchema
from schemas.status import ExecutionStatus


class RAGResultMapper:
    """Own the single dictionary-to-schema conversion at the runtime boundary."""

    def __init__(self, evidence: EvidenceMapper | None = None) -> None:
        self.evidence = evidence or EvidenceMapper()

    @staticmethod
    def _query_expansion(
        result: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
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
    def _runtime_metadata(
        result: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        run_record = data.get("run_record") or {}
        metadata = result.get("metadata") or run_record.get("metadata") or {}
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _record_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(
                item.get("child_chunk_id")
                or item.get("matched_chunk_id")
                or item.get("chunk_id")
                or ""
            ),
            str(
                item.get("parent_chunk_id")
                or item.get("context_chunk_id")
                or item.get("chunk_id")
                or ""
            ),
        )

    def _selected_and_dropped_records(
        self,
        *,
        data: dict[str, Any],
        context_pack: dict[str, Any],
        max_context_items: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected = context_pack.get("selected_results") or []
        full = data.get("retrieval_results") or []
        compact = data.get("contexts") or []
        dropped = context_pack.get("dropped_results") or []

        if isinstance(selected, list) and selected:
            selected_records = [item for item in selected if isinstance(item, dict)]
        elif isinstance(full, list) and full:
            selected_records = [
                item for item in full[:max_context_items] if isinstance(item, dict)
            ]
        elif isinstance(compact, list):
            selected_records = [
                item for item in compact[:max_context_items] if isinstance(item, dict)
            ]
        else:
            selected_records = []

        dropped_records = (
            [item for item in dropped if isinstance(item, dict)]
            if isinstance(dropped, list)
            else []
        )
        selected_keys = {self._record_key(item) for item in selected_records}
        dropped_keys = {self._record_key(item) for item in dropped_records}

        # Some older context packers omitted dropped_results. Preserve those
        # candidates so the evidence contract remains auditable.
        if isinstance(full, list):
            for item in full:
                if not isinstance(item, dict):
                    continue
                key = self._record_key(item)
                if key in selected_keys or key in dropped_keys:
                    continue
                normalized = dict(item)
                metadata = dict(normalized.get("metadata") or {})
                metadata.setdefault(
                    "context_drop_reason", "not_selected_by_context_packer"
                )
                normalized["metadata"] = metadata
                dropped_records.append(normalized)
                dropped_keys.add(key)
        return selected_records, dropped_records

    @staticmethod
    def _correction_rounds(
        evidence_quality: dict[str, Any]
    ) -> list[dict[str, Any]]:
        corrective = evidence_quality.get("corrective_retrieval")
        if not isinstance(corrective, dict):
            return []
        rounds = corrective.get("rounds")
        if not isinstance(rounds, list):
            return []
        return [dict(item) for item in rounds if isinstance(item, dict)]

    def map(
        self,
        *,
        request: RAGToolInputSchema,
        invocation: RAGInvocation,
        raw_result: dict[str, Any],
        latency_ms: int,
        rag_project_root: str,
        service_name: str,
    ) -> EvidenceBundleSchema:
        if raw_result.get("success") is False:
            raise RuntimeError(raw_result.get("error") or "RAG retrieval failed")

        data = raw_result.get("data") or raw_result
        metadata = self._runtime_metadata(raw_result, data)
        query_expansion = self._query_expansion(raw_result, data)
        rewritten_queries = as_str_list(query_expansion.get("rewritten_queries"))
        if not rewritten_queries:
            rewritten_queries = as_str_list(
                invocation.request_data.get("rewritten_queries")
            )

        retrieval_plan = data.get("retrieval_plan") or metadata.get("retrieval_plan") or {}
        retrieval_plan = (
            dict(retrieval_plan) if isinstance(retrieval_plan, dict) else {}
        )
        evidence_quality = (
            data.get("evidence_quality") or metadata.get("evidence_quality") or {}
        )
        evidence_quality = (
            dict(evidence_quality) if isinstance(evidence_quality, dict) else {}
        )
        context_pack = data.get("context_pack") or {}
        context_pack = dict(context_pack) if isinstance(context_pack, dict) else {}
        selected_records, dropped_records = self._selected_and_dropped_records(
            data=data,
            context_pack=context_pack,
            max_context_items=invocation.max_context_items,
        )
        selected_chunks = self.evidence.chunks(selected_records)
        dropped_chunks = self.evidence.chunks(dropped_records)
        citations = self.evidence.citations(selected_chunks)

        offline_index = metadata.get("offline_index")
        offline_index = offline_index if isinstance(offline_index, dict) else {}
        plan_id = str(retrieval_plan.get("plan_id") or "adaptive")
        trace = RAGTraceSchema(
            retrieval_mode=plan_id,
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            embedding_model=str(
                offline_index.get("embedding_model") or "runtime-config"
            ),
            embedding_version=str(offline_index.get("embedding_version") or "unknown"),
            reranker_model="runtime-config",
            reranker_version="unknown",
            index_name=str(
                offline_index.get("collection_name") or "rag_child_chunks"
            ),
            index_version=str(
                offline_index.get("index_version") or "unversioned_index"
            ),
            vector_db=str(offline_index.get("backend") or "milvus-lite"),
            max_context_chars=invocation.max_context_chars,
            retrieved_count=len(selected_chunks) + len(dropped_chunks),
            reranked_count=len(selected_chunks) + len(dropped_chunks),
            context_item_count=len(selected_chunks),
            latency_ms=latency_ms,
            extra={
                "rag_project_root": rag_project_root,
                "rag_result_metadata": metadata,
                "query_expansion": query_expansion,
                "retrieval_plan": retrieval_plan,
                "evidence_quality": evidence_quality,
                "rag_run_id": data.get("run_id"),
                "context_source": "rag_evidence_contract_v1",
                "packing_strategy": context_pack.get("packing_strategy"),
                "context_pack_selected_count": context_pack.get("selected_count"),
                "context_pack_dropped_count": context_pack.get("dropped_count"),
                "context_token_budget": context_pack.get("token_budget"),
                "context_tokens_used": context_pack.get("tokens_used"),
                "context_truncated_item_ids": context_pack.get(
                    "truncated_item_ids"
                ),
            },
        )
        bundle = RAGEvidenceContractBuilder.build(
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            selected_chunks=selected_chunks,
            dropped_chunks=dropped_chunks,
            citations=citations,
            trace=trace,
            max_context_chars=invocation.max_context_chars,
            context_pack=context_pack,
            extra={
                "source": "parent_child_retrieval",
                "packing_strategy": context_pack.get("packing_strategy"),
                "selected_count": context_pack.get("selected_count"),
                "dropped_count": context_pack.get("dropped_count"),
                "token_budget": context_pack.get("token_budget"),
                "tokens_used": context_pack.get("tokens_used"),
            },
        )
        correction_rounds = self._correction_rounds(evidence_quality)
        trace_id = str(
            request.extra.get("retrieval_trace_id")
            or f"rag_{request.run_id}_{request.extra.get('retrieval_scope') or 'document'}"
        )
        return bundle.model_copy(
            update={
                "task_id": request.task_id,
                "run_id": request.run_id,
                "status": ExecutionStatus.SUCCESS,
                "retrieval_trace_id": trace_id,
                "correction_trace": correction_rounds,
                "budget_usage": {
                    "retrieval_rounds": 1 + len(correction_rounds),
                    "queries_executed": 1 + len(rewritten_queries),
                    "rerank_calls": 1 + len(correction_rounds),
                },
                "trace": trace,
                "extra": {
                    **dict(bundle.extra),
                    "rag_service": service_name,
                    "retrieval_plan": retrieval_plan,
                    "evidence_quality": evidence_quality,
                },
            }
        )
