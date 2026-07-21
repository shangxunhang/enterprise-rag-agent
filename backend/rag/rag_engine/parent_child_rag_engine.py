"""Retrieval-only engine for the configured parent-child RAG pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, TimestampedUuidIdGenerator
from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.application.run_record import RAGRunRecordBuilder
from rag.context.context_gate import ContextGate, ContextRequirements
from rag.evaluation.retrieval_metrics import evaluate_retrieval_results_v2
from rag.planning.retrieval_planner import (
    AdaptiveRetrievalPlanner,
    RetrievalPlannerPort,
)
from rag.ports.capture import RAGRunCapturePort
from rag.ports.generation import TextGenerator
from rag.ports.pipeline import RerankerPort
from rag.ports.quality import (
    CorrectiveQueryPlannerPort,
    CorrectiveRetrievalGatePort,
    EvidenceAssessorPort,
)
from rag.ports.retrieval_components import (
    CandidateEnricherPort,
    CandidateRetrieverPort,
    QueryFusionPort,
    SourceFusionPort,
)
from rag.query.query_transform_selector import QueryTransformSelector


class ParentChildRAGEngine:
    """Run the stable retrieval skeleton and return packed evidence.

    Business prompting, answer generation, generation checking and repair are
    intentionally outside this engine.  The enterprise use case owns them.
    """

    def __init__(
        self,
        *,
        retrievers: list[CandidateRetrieverPort],
        source_fusion: SourceFusionPort,
        query_fusion: QueryFusionPort,
        candidate_enricher: CandidateEnricherPort,
        reranker: RerankerPort,
        context_gate: ContextGate,
        evidence_assessor: EvidenceAssessorPort,
        corrective_retrieval_gate: CorrectiveRetrievalGatePort,
        corrective_query_planner: CorrectiveQueryPlannerPort,
        run_capture: Optional[RAGRunCapturePort] = None,
        query_llm_generator: Optional[TextGenerator] = None,
        query_transform_selector: QueryTransformSelector | None = None,
        retrieval_planner: RetrievalPlannerPort | None = None,
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
        if query_transform_selector is None:
            raise ValueError(
                "query_transform_selector is required; query transformation must "
                "be constructed from external retrieval configuration"
            )

        self.retrievers = list(retrievers)
        self.source_fusion = source_fusion
        self.query_fusion = query_fusion
        self.candidate_enricher = candidate_enricher
        self.reranker = reranker
        self.context_gate = context_gate
        self.evidence_assessor = evidence_assessor
        self.corrective_retrieval_gate = corrective_retrieval_gate
        self.corrective_query_planner = corrective_query_planner
        self.run_capture = run_capture
        self.query_llm_generator = query_llm_generator
        self.query_transform_selector = query_transform_selector
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

        self.retrieval_planner = retrieval_planner or AdaptiveRetrievalPlanner()
        self.retrieval_pipeline = ParentChildRetrievalPipeline(
            retrievers=self.retrievers,
            source_fusion=self.source_fusion,
            query_fusion=self.query_fusion,
            candidate_enricher=self.candidate_enricher,
            reranker=self.reranker,
            query_transform_selector=self.query_transform_selector,
            retrieval_planner=self.retrieval_planner,
            evidence_assessor=self.evidence_assessor,
            corrective_retrieval_gate=self.corrective_retrieval_gate,
            corrective_query_planner=self.corrective_query_planner,
        )

    def close(self) -> None:
        """Release resources owned by configured retrieval components."""
        seen: set[int] = set()
        components = [
            *self.retrievers,
            self.source_fusion,
            self.query_fusion,
            self.candidate_enricher,
            self.reranker,
            self.context_gate.default_packer,
            self.context_gate.lost_in_middle_packer,
            self.evidence_assessor,
            self.corrective_retrieval_gate,
            self.corrective_query_planner,
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

    def run(
        self,
        query: str,
        *,
        eval_top_k: int = 5,
        expected_doc_ids: Optional[List[str]] = None,
        expected_parent_chunk_ids: Optional[List[str]] = None,
        expected_child_chunk_ids: Optional[List[str]] = None,
        expected_keywords: Optional[List[str]] = None,
        filter_expr: Optional[str] = None,
        keyword_doc_ids: Optional[List[str]] = None,
        keyword_scope: Optional[Dict[str, Any]] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")

        run_id = self.run_id_generator.new_id("rag_run")
        started_at = self.clock.now_iso()
        retrieval = self.retrieval_pipeline.run(
            query=query,
            filter_expr=filter_expr,
            keyword_doc_ids=keyword_doc_ids,
            keyword_scope=keyword_scope,
            extra_metadata=extra_metadata,
        )
        request_context = dict(extra_metadata or {})
        context_requirements = ContextRequirements.from_mapping(
            request_context.get("context_requirements"),
            defaults=self.context_gate.default_requirements,
        )
        context_pack = self.context_gate.pack(
            retrieval.results,
            requirements=context_requirements,
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
        metadata = self.record_builder.build_metadata(
            pipeline_name=self.pipeline_name,
            pipeline_version=self.pipeline_version,
            retrievers=self.retrievers,
            source_fusion=self.source_fusion,
            query_fusion=self.query_fusion,
            candidate_enricher=self.candidate_enricher,
            reranker=self.reranker,
            evidence_assessor=self.evidence_assessor,
            corrective_retrieval_gate=self.corrective_retrieval_gate,
            corrective_query_planner=self.corrective_query_planner,
            context_packer=self.context_gate,
            eval_top_k=eval_top_k,
            filter_expr=filter_expr,
            keyword_doc_ids=keyword_doc_ids or [],
            retrieval=retrieval,
            enable_query_expansion_llm=self.enable_query_expansion_llm,
            query_llm_generator=self.query_llm_generator,
            query_expansion_generation_params=self.query_expansion_generation_params,
            extra_metadata=extra_metadata,
        )
        run_record = self.record_builder.build_record(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            query=query,
            retrieval=retrieval,
            context_pack=context_pack,
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
            "context_requirements": context_requirements.to_dict(),
            "packed_context": context_pack.context,
            "citations": context_pack.citations,
            "eval_result": eval_result,
            "run_record": run_record,
            "capture_result": capture_result,
        }
