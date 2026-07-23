"""Build retrieval-only RAG run records."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _component_name(component: Any) -> str | None:
    if component is None:
        return None
    return component.__class__.__name__


class RAGRunRecordBuilder:
    def build_metadata(
        self,
        *,
        pipeline_name: str,
        pipeline_version: str,
        retrievers: list[Any],
        source_fusion: Any,
        query_fusion: Any,
        candidate_enricher: Any,
        reranker: Any,
        evidence_assessor: Any,
        corrective_retrieval_gate: Any,
        corrective_query_planner: Any,
        context_packer: Any,
        eval_top_k: int,
        filter_expr: Optional[str],
        keyword_doc_ids: list[str],
        retrieval: Any,
        enable_query_expansion_llm: bool,
        query_llm_generator: Any,
        model_calls: list[Dict[str, Any]],
        query_expansion_generation_params: Dict[str, Any],
        extra_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        query_expansion_calls = [
            dict(item)
            for item in model_calls
            if str(item.get("call_purpose") or "")
            in {"rag_query_rewrite", "rag_hyde"}
        ]
        selected_query_models = list(
            dict.fromkeys(
                str(item.get("selected_model") or "").strip()
                for item in query_expansion_calls
                if str(item.get("selected_model") or "").strip()
            )
        )
        selected_query_profiles = list(
            dict.fromkeys(
                str(item.get("selected_profile") or "").strip()
                for item in query_expansion_calls
                if str(item.get("selected_profile") or "").strip()
            )
        )
        metadata: Dict[str, Any] = {
            # Caller metadata is additive. Canonical execution and model
            # lineage below is written last so it cannot be forged or replaced.
            **dict(extra_metadata or {}),
            "pipeline_stage": "retrieval_evidence",
            "pipeline_name": pipeline_name,
            "pipeline_version": pipeline_version,
            "retrievers": [_component_name(item) for item in retrievers],
            "source_fusion": _component_name(source_fusion),
            "query_fusion": _component_name(query_fusion),
            "candidate_enricher": _component_name(candidate_enricher),
            "reranker": _component_name(reranker),
            "evidence_assessor": _component_name(evidence_assessor),
            "corrective_retrieval_gate": _component_name(
                corrective_retrieval_gate
            ),
            "corrective_query_planner": _component_name(
                corrective_query_planner
            ),
            "context_packer": _component_name(context_packer),
            "configured_reranker": retrieval.query_expansion.metadata.get(
                "configured_reranker"
            ),
            "configured_evidence_assessor": retrieval.query_expansion.metadata.get(
                "configured_evidence_assessor"
            ),
            "eval_top_k": eval_top_k,
            "filter_expr": filter_expr,
            "keyword_doc_ids": keyword_doc_ids,
            "query_transform_mode": retrieval.query_expansion.strategy,
            "retrieval_plan": retrieval.retrieval_plan,
            "correction_triggered": bool(retrieval.correction_triggered),
            "evidence_quality": retrieval.evidence_quality,
            "initial_reranked_result_count": (
                len(retrieval.reranked_results)
                if retrieval.correction_triggered
                else None
            ),
            "final_assessed_result_count": (
                len(retrieval.results) if retrieval.correction_triggered else None
            ),
            "rewritten_queries": retrieval.query_expansion.rewritten_queries,
            "hyde_query": retrieval.query_expansion.hyde_query,
            "retrieval_queries": retrieval.query_expansion.retrieval_queries,
            "query_expansion": retrieval.query_expansion.to_dict(),
            "query_expansion_llm_enabled": bool(
                enable_query_expansion_llm and query_llm_generator is not None
            ),
            # Concrete provider lineage, never the adapter class name.
            "query_expansion_model_name": (
                selected_query_models[-1] if selected_query_models else None
            ),
            "query_expansion_models": selected_query_models,
            "query_expansion_model_profiles": selected_query_profiles,
            "query_expansion_model_call_ids": [
                item.get("model_call_id") for item in query_expansion_calls
            ],
            "model_calls": [dict(item) for item in model_calls],
            "model_call_ids": [item.get("model_call_id") for item in model_calls],
            "query_expansion_generation_params": dict(
                query_expansion_generation_params
            ),
        }
        return metadata

    def build_record(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        query: str,
        retrieval: Any,
        context_pack: Any,
        eval_result: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "schema_version": "rag_retrieval_run_v1",
            "run_id": run_id,
            "created_at": started_at,
            "finished_at": finished_at,
            "query": query,
            "query_expansion": retrieval.query_expansion.to_dict(),
            "retrieval_plan": retrieval.retrieval_plan,
            "evidence_quality": retrieval.evidence_quality,
            "p2_results": retrieval.p2_results,
            "initial_reranked_results": (
                retrieval.reranked_results
                if retrieval.correction_triggered
                else None
            ),
            "retrieval_results": retrieval.results,
            "context_pack": context_pack.to_dict(),
            "packed_context": context_pack.context,
            "citations": context_pack.citations,
            "eval_result": eval_result,
            "model_calls": list(metadata.get("model_calls") or []),
            "metadata": metadata,
        }
