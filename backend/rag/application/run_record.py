"""Build and persist RAG run records."""

from __future__ import annotations

from typing import Any, Dict, Optional

from rag.application.parent_child_generation import infer_model_name


class RAGRunRecordBuilder:
    def build_metadata(
        self,
        *,
        pipeline_name: str,
        pipeline_version: str,
        retrievers: list[Any],
        fusion: Any,
        query_fusion: Any,
        candidate_enricher: Any,
        reranker: Any,
        evidence_grader: Any,
        context_packer: Any,
        generation_checker: Any,
        repair_strategy: Any,
        prompt_builder: Any,
        dense_top_k: int,
        keyword_top_k: int,
        candidate_top_k: int,
        rerank_top_k: int,
        eval_top_k: int,
        filter_expr: Optional[str],
        keyword_doc_ids: list[str],
        rrf_k: Any,
        retrieval: Any,
        generation: Any,
        enable_query_expansion_llm: bool,
        query_llm_generator: Any,
        query_expansion_generation_params: Dict[str, Any],
        generate_answer: bool,
        extra_metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "pipeline_stage": (
                "p4_full_answer_capture" if generate_answer else "p4_lite_prompt_capture"
            ),
            "pipeline_name": pipeline_name,
            "pipeline_version": pipeline_version,
            "retrievers": [item.__class__.__name__ for item in retrievers],
            "fusion": fusion.__class__.__name__,
            "query_fusion": query_fusion.__class__.__name__,
            "candidate_enricher": candidate_enricher.__class__.__name__,
            "reranker": reranker.__class__.__name__,
            "evidence_grader": evidence_grader.__class__.__name__,
            "context_packer": context_packer.__class__.__name__,
            "generation_checker": generation_checker.__class__.__name__,
            "repair_strategy": repair_strategy.__class__.__name__,
            "prompt_builder": prompt_builder.__class__.__name__,
            "dense_top_k": dense_top_k,
            "keyword_top_k": keyword_top_k,
            "candidate_top_k": candidate_top_k,
            "rerank_top_k": rerank_top_k,
            "configured_reranker": retrieval.query_expansion.metadata.get(
                "configured_reranker"
            ),
            "legacy_reranker_overrides": retrieval.query_expansion.metadata.get(
                "legacy_reranker_overrides"
            ),
            "configured_evidence_grader": retrieval.query_expansion.metadata.get(
                "configured_evidence_grader"
            ),
            "legacy_quality_overrides": retrieval.query_expansion.metadata.get(
                "legacy_quality_overrides"
            ),
            "configured_generation_checker": generation.generation_checker_metadata,
            "configured_repair_strategy": generation.repair_strategy_metadata,
            "repair": generation.repair,
            "eval_top_k": eval_top_k,
            "filter_expr": filter_expr,
            "keyword_doc_ids": keyword_doc_ids,
            "rrf_k": rrf_k,
            "retrieval_strategy": retrieval.query_expansion.strategy,
            "original_retrieval_strategy": retrieval.original_strategy,
            "effective_retrieval_strategy": retrieval.effective_strategy,
            "adaptive_rag_enabled": retrieval.adaptive_rag is not None,
            "adaptive_rag": retrieval.adaptive_rag,
            "c_rag_enabled": bool(retrieval.crag_enabled),
            "self_rag_enabled": bool(
                generation.generation_checker_metadata.get("enabled", False)
            ),
            "c_rag": retrieval.c_rag,
            "self_rag": generation.self_rag,
            "pre_crag_result_count": (
                len(retrieval.pre_crag_results) if retrieval.crag_enabled else None
            ),
            "post_crag_result_count": (
                len(retrieval.results) if retrieval.crag_enabled else None
            ),
            "rewritten_queries": retrieval.query_expansion.rewritten_queries,
            "hyde_query": retrieval.query_expansion.hyde_query,
            "retrieval_queries": retrieval.query_expansion.retrieval_queries,
            "query_expansion": retrieval.query_expansion.to_dict(),
            "query_expansion_llm_enabled": bool(
                enable_query_expansion_llm and query_llm_generator is not None
            ),
            "query_expansion_model_name": infer_model_name(query_llm_generator),
            "query_expansion_generation_params": dict(
                query_expansion_generation_params
            ),
            "llm_enabled": bool(generate_answer),
            "llm_latency_ms": generation.llm_latency_ms,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        return metadata

    def build_record(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        query: str,
        retrieval: Any,
        generation: Any,
        eval_result: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "schema_version": "rag_run_v1",
            "run_id": run_id,
            "created_at": started_at,
            "finished_at": finished_at,
            "query": query,
            "query_expansion": retrieval.query_expansion.to_dict(),
            "adaptive_rag": retrieval.adaptive_rag,
            "c_rag": retrieval.c_rag,
            "self_rag": generation.self_rag,
            "answer": generation.answer,
            "model_name": generation.model_name,
            "model_provider": generation.model_provider,
            "generation_params": generation.generation_params,
            "p2_results": retrieval.p2_results,
            "pre_crag_retrieval_results": (
                retrieval.pre_crag_results if retrieval.crag_enabled else None
            ),
            "retrieval_results": retrieval.results,
            "context_pack": generation.context_pack.to_dict(),
            "packed_context": generation.context_pack.context,
            "citations": generation.context_pack.citations,
            "prompt": generation.prompt_result.prompt,
            "prompt_id": generation.prompt_result.prompt_id,
            "prompt_version": generation.prompt_result.prompt_version,
            "prompt_build": generation.prompt_result.to_dict(),
            "eval_result": eval_result,
            "metadata": metadata,
        }
