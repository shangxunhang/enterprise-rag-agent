# -*- coding: utf-8 -*-
"""Thin facade for the parent-child RAG application pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, TimestampedUuidIdGenerator

from rag.application.parent_child_generation import ParentChildGenerationPipeline
from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.application.run_record import RAGRunRecordBuilder
from rag.evaluation.retrieval_metrics import evaluate_retrieval_results_v2
from rag.judge.adaptive_rag_router import AdaptiveRAGRouter
from rag.ports.capture import RAGRunCapturePort
from rag.ports.generation import TextGenerator
from rag.ports.pipeline import ContextPackerPort, PromptBuilderPort, RerankerPort
from rag.query.query_transform_chain import QueryTransformChain
from rag.retrieval.query_fusion import MultiQueryFusion


class ParentChildRAGEngine:
    """Facade coordinating configured retrieval, generation and capture services."""

    def __init__(
        self,
        *,
        retrievers: list[Any],
        fusion: Any,
        query_fusion: Any,
        candidate_enricher: Any,
        reranker: RerankerPort,
        context_packer: ContextPackerPort,
        prompt_builder: PromptBuilderPort,
        evidence_grader: Any,
        generation_checker: Any,
        repair_strategy: Any,
        run_capture: Optional[RAGRunCapturePort] = None,
        llm_generator: Optional[TextGenerator] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        query_llm_generator: Optional[TextGenerator] = None,
        query_transform_chain: QueryTransformChain | None = None,
        enable_query_expansion_llm: bool = True,
        query_expansion_generation_params: Optional[Dict[str, Any]] = None,
        pipeline_name: str = "parent_child_hybrid_rag",
        pipeline_version: str = "v1.0",
        retrieval_evaluator: Any = evaluate_retrieval_results_v2,
        record_builder: RAGRunRecordBuilder | None = None,
        clock: Clock | None = None,
        run_id_generator: IdGenerator | None = None,
    ) -> None:
        if not retrievers:
            raise ValueError("at least one configured retriever is required")
        self.retrievers = list(retrievers)
        self.fusion = fusion
        self.query_fusion = query_fusion
        self.candidate_enricher = candidate_enricher
        self.reranker = reranker
        self.context_packer = context_packer
        self.prompt_builder = prompt_builder
        self.evidence_grader = evidence_grader
        self.generation_checker = generation_checker
        self.repair_strategy = repair_strategy
        self.run_capture = run_capture
        self.llm_generator = llm_generator
        self.model_name = model_name
        self.model_provider = model_provider
        self.query_llm_generator = query_llm_generator or llm_generator
        self.enable_query_expansion_llm = bool(enable_query_expansion_llm)
        self.query_expansion_generation_params = dict(
            query_expansion_generation_params or {}
        )
        self.pipeline_name = pipeline_name
        self.pipeline_version = pipeline_version
        self.retrieval_evaluator = retrieval_evaluator
        self.record_builder = record_builder or RAGRunRecordBuilder()
        self.clock = clock or SystemClock()
        self.run_id_generator = run_id_generator or TimestampedUuidIdGenerator(
            self.clock
        )

        if query_transform_chain is None:
            raise ValueError(
                "query_transform_chain is required; query expansion must be "
                "constructed from external pipeline configuration"
            )
        self.query_transform_chain = query_transform_chain
        self.adaptive_rag_router = AdaptiveRAGRouter(
            llm_generator=self.query_llm_generator,
            use_llm=self.enable_query_expansion_llm,
            generation_params=self.query_expansion_generation_params,
        )
        self.retrieval_pipeline = ParentChildRetrievalPipeline(
            retrievers=self.retrievers,
            fusion=self.fusion,
            query_fusion=self.query_fusion,
            candidate_enricher=self.candidate_enricher,
            reranker=self.reranker,
            query_transform_chain=self.query_transform_chain,
            adaptive_router=self.adaptive_rag_router,
            evidence_grader=self.evidence_grader,
            generation_checker_enabled=bool(
                self.generation_checker.execution_metadata().get("enabled", False)
            ),
        )
        self.generation_pipeline = ParentChildGenerationPipeline(
            context_packer=self.context_packer,
            prompt_builder=self.prompt_builder,
            llm_generator=self.llm_generator,
            model_name=self.model_name,
            model_provider=self.model_provider,
            generation_checker=self.generation_checker,
            repair_strategy=self.repair_strategy,
        )

    def close(self) -> None:
        """Release runtime-owned resources, especially Milvus Lite handles."""
        seen: set[int] = set()
        components = [
            *self.retrievers,
            self.fusion,
            self.query_fusion,
            self.candidate_enricher,
            self.reranker,
            self.context_packer,
            self.evidence_grader,
            self.generation_checker,
            self.repair_strategy,
            self.llm_generator,
            self.query_llm_generator,
        ]
        for component in components:
            if component is None or id(component) in seen:
                continue
            seen.add(id(component))
            close = getattr(component, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    # Compatibility surface for callers that used the previous private helpers.
    @staticmethod
    def _result_key(result: Dict[str, Any]) -> str:
        return MultiQueryFusion.result_key(result)

    @staticmethod
    def _safe_rank(value: Any, fallback: int) -> int:
        return MultiQueryFusion.safe_rank(value, fallback)

    @staticmethod
    def _safe_score(value: Any, default: float = 0.0) -> float:
        return MultiQueryFusion.safe_score(value, default)

    def _fuse_multi_query_results(
        self,
        query_results: Dict[str, List[Dict[str, Any]]],
        *,
        rrf_k: int,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        return MultiQueryFusion().fuse(
            query_results,
            rrf_k=rrf_k,
            top_k=top_k,
        )

    def run(
        self,
        query: str,
        *,
        dense_top_k: int = 10,
        keyword_top_k: int = 10,
        candidate_top_k: int = 10,
        rrf_k: Optional[int] = None,
        rerank_top_k: int = 5,
        eval_top_k: int = 5,
        expected_doc_ids: Optional[List[str]] = None,
        expected_parent_chunk_ids: Optional[List[str]] = None,
        expected_child_chunk_ids: Optional[List[str]] = None,
        expected_keywords: Optional[List[str]] = None,
        filter_expr: Optional[str] = None,
        keyword_doc_ids: Optional[List[str]] = None,
        generate_answer: Optional[bool] = None,
        generation_params: Optional[Dict[str, Any]] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        retrieval_strategy: str = "hybrid",
        num_rewrites: int = 3,
        enable_hyde: Optional[bool] = None,
        enable_crag: Optional[bool] = None,
        enable_self_rag: Optional[bool] = None,
        crag_max_judge_chunks: int = 8,
        crag_drop_irrelevant: bool = True,
    ) -> Dict[str, Any]:
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        if generate_answer is None:
            generate_answer = self.llm_generator is not None
        if generate_answer and self.llm_generator is None:
            raise ValueError("generate_answer=True requires llm_generator")

        run_id = self.run_id_generator.new_id("rag_run")
        started_at = self.clock.now_iso()
        retrieval = self.retrieval_pipeline.run(
            query=query,
            dense_top_k=dense_top_k,
            keyword_top_k=keyword_top_k,
            candidate_top_k=candidate_top_k,
            rrf_k=rrf_k,
            rerank_top_k=rerank_top_k,
            filter_expr=filter_expr,
            keyword_doc_ids=keyword_doc_ids,
            retrieval_strategy=retrieval_strategy,
            num_rewrites=num_rewrites,
            enable_hyde=enable_hyde,
            enable_crag=enable_crag,
            enable_self_rag=enable_self_rag,
            crag_max_judge_chunks=crag_max_judge_chunks,
            crag_drop_irrelevant=crag_drop_irrelevant,
            extra_metadata=extra_metadata,
        )
        generation = self.generation_pipeline.run(
            query,
            retrieval.results,
            generate_answer=bool(generate_answer),
            generation_params=generation_params,
            self_rag_enabled=bool(enable_self_rag),
        )
        eval_result = self.retrieval_evaluator(
            retrieval.results,
            top_k=eval_top_k,
            expected_doc_ids=expected_doc_ids or [],
            expected_parent_chunk_ids=expected_parent_chunk_ids or [],
            expected_child_chunk_ids=expected_child_chunk_ids or [],
            expected_keywords=expected_keywords or [],
        )
        finished_at = self.clock.now_iso()
        resolved_rrf_k = getattr(self.fusion, "rrf_k", rrf_k)
        metadata = self.record_builder.build_metadata(
            pipeline_name=self.pipeline_name,
            pipeline_version=self.pipeline_version,
            retrievers=self.retrievers,
            fusion=self.fusion,
            query_fusion=self.query_fusion,
            candidate_enricher=self.candidate_enricher,
            reranker=self.reranker,
            evidence_grader=self.evidence_grader,
            context_packer=self.context_packer,
            generation_checker=self.generation_checker,
            repair_strategy=self.repair_strategy,
            prompt_builder=self.prompt_builder,
            dense_top_k=dense_top_k,
            keyword_top_k=keyword_top_k,
            candidate_top_k=candidate_top_k,
            rerank_top_k=rerank_top_k,
            eval_top_k=eval_top_k,
            filter_expr=filter_expr,
            keyword_doc_ids=keyword_doc_ids or [],
            rrf_k=resolved_rrf_k,
            retrieval=retrieval,
            generation=generation,
            enable_query_expansion_llm=self.enable_query_expansion_llm,
            query_llm_generator=self.query_llm_generator,
            query_expansion_generation_params=self.query_expansion_generation_params,
            generate_answer=bool(generate_answer),
            extra_metadata=extra_metadata,
        )
        run_record = self.record_builder.build_record(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            query=query,
            retrieval=retrieval,
            generation=generation,
            eval_result=eval_result,
            metadata=metadata,
        )
        capture_result = (
            self.run_capture.capture(run_record)
            if self.run_capture is not None
            else None
        )
        return {
            "run_id": run_id,
            "query": query,
            "query_expansion": retrieval.query_expansion.to_dict(),
            "adaptive_rag": retrieval.adaptive_rag,
            "c_rag": retrieval.c_rag,
            "self_rag": generation.self_rag,
            "repair": generation.repair,
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
            "eval_result": eval_result,
            "run_record": run_record,
            "capture_result": capture_result,
        }
