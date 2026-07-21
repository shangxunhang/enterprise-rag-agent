"""Invariant parent-child retrieval followed by assessment-driven correction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag.planning.retrieval_planner import RetrievalPlannerPort
from rag.ports.pipeline import RerankerPort
from rag.ports.quality import (
    CorrectionGateDecision,
    CorrectiveQueryPlannerPort,
    CorrectiveRetrievalGatePort,
    EvidenceAssessment,
    EvidenceAssessorPort,
)
from rag.ports.retrieval_components import (
    CandidateEnricherPort,
    CandidateRetrieverPort,
    QueryFusionPort,
    SourceFusionPort,
)
from rag.query.query_transform_selector import QueryTransformSelector
from rag.schema.candidate import CandidateSet, RetrievalRequest


@dataclass
class RetrievalStageResult:
    retrieval_plan: dict[str, Any]
    query_expansion: Any
    p2_results: list[dict[str, Any]]
    reranked_results: list[dict[str, Any]]
    results: list[dict[str, Any]]
    evidence_quality: dict[str, Any]
    correction_triggered: bool


class ParentChildRetrievalPipeline:
    """Run fixed retrieval, always assess evidence, then gate bounded correction."""

    def __init__(
        self,
        *,
        retrievers: list[CandidateRetrieverPort],
        source_fusion: SourceFusionPort,
        query_fusion: QueryFusionPort,
        candidate_enricher: CandidateEnricherPort,
        reranker: RerankerPort,
        query_transform_selector: QueryTransformSelector,
        retrieval_planner: RetrievalPlannerPort,
        evidence_assessor: EvidenceAssessorPort,
        corrective_retrieval_gate: CorrectiveRetrievalGatePort,
        corrective_query_planner: CorrectiveQueryPlannerPort,
    ) -> None:
        if not retrievers:
            raise ValueError("at least one configured retriever is required")
        self.retrievers = list(retrievers)
        self.source_fusion = source_fusion
        self.query_fusion = query_fusion
        self.candidate_enricher = candidate_enricher
        self.reranker = reranker
        self.query_transform_selector = query_transform_selector
        self.retrieval_planner = retrieval_planner
        self.evidence_assessor = evidence_assessor
        self.corrective_retrieval_gate = corrective_retrieval_gate
        self.corrective_query_planner = corrective_query_planner

    @staticmethod
    def _metadata(component: Any) -> dict[str, Any]:
        metadata = getattr(component, "plugin_metadata", None)
        if metadata is not None and hasattr(metadata, "to_dict"):
            return metadata.to_dict()
        return {
            "name": component.__class__.__name__,
            "version": "unknown",
            "implementation": (
                f"{component.__class__.__module__}.{component.__class__.__qualname__}"
            ),
        }

    def _retrieve_one_query(
        self,
        *,
        retrieval_query: str,
        query_label: str,
        query_index: int,
        filter_expr: str | None,
        keyword_doc_ids: list[str] | None,
        keyword_scope: dict[str, Any] | None,
        stage: str,
    ) -> tuple[CandidateSet, dict[str, Any]]:
        scope = dict(keyword_scope or {})
        request = RetrievalRequest(
            query=retrieval_query,
            filter_expr=filter_expr,
            tenant_id=str(scope.get("tenant_id") or "").strip() or None,
            kb_ids=list(scope.get("kb_ids") or []),
            file_ids=list(scope.get("file_ids") or []),
            doc_ids=list(scope.get("doc_ids") or keyword_doc_ids or []),
            metadata={"query_index": query_index, "retrieval_stage": stage},
        )
        source_sets = [retriever.retrieve(request) for retriever in self.retrievers]
        fused_children = self.source_fusion.fuse(source_sets)
        enriched = self.candidate_enricher.enrich(fused_children)
        candidate_set = enriched.copy_with(source_name=query_label)
        return candidate_set, {
            "query_label": query_label,
            "query": retrieval_query,
            "stage": stage,
            "source_candidate_counts": {
                item.source_name: len(item.candidates) for item in source_sets
            },
            "source_metadata": {
                item.source_name: dict(item.metadata) for item in source_sets
            },
            "fused_child_count": len(fused_children.candidates),
            "enriched_parent_count": len(enriched.candidates),
        }

    def _fuse_query_sets(
        self, candidate_sets: list[CandidateSet]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if len(candidate_sets) <= 1:
            results = list(candidate_sets[0].candidates) if candidate_sets else []
            return results, {"applied": False, "query_set_count": len(candidate_sets)}
        fused = self.query_fusion.fuse(candidate_sets)
        return list(fused.candidates), {"applied": True, **dict(fused.metadata)}

    def _assess(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        correction_round: int,
        request_context: dict[str, Any] | None,
    ) -> EvidenceAssessment:
        return self.evidence_assessor.assess(
            query=query,
            results=results,
            runtime_context={
                "correction_round": correction_round,
                "request_context": dict(request_context or {}),
            },
        )

    def _gate(
        self,
        *,
        assessment: EvidenceAssessment,
        correction_budget: int,
        completed_rounds: int,
        request_context: dict[str, Any] | None,
    ) -> CorrectionGateDecision:
        return self.corrective_retrieval_gate.decide(
            assessment=assessment,
            correction_budget=correction_budget,
            completed_rounds=completed_rounds,
            runtime_context={"request_context": dict(request_context or {})},
        )

    def _run_correction(
        self,
        *,
        query: str,
        initial_reranked: list[dict[str, Any]],
        initial_assessment: EvidenceAssessment,
        correction_budget: int,
        filter_expr: str | None,
        keyword_doc_ids: list[str] | None,
        keyword_scope: dict[str, Any] | None,
        request_context: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], EvidenceAssessment, dict[str, Any]]:
        completed_rounds = 0
        assessment = initial_assessment
        current_reranked = list(initial_reranked)
        trace: dict[str, Any] = {
            "triggered": False,
            "correction_budget": max(0, int(correction_budget)),
            "rounds": [],
        }

        while True:
            decision = self._gate(
                assessment=assessment,
                correction_budget=correction_budget,
                completed_rounds=completed_rounds,
                request_context=request_context,
            )
            trace["last_gate_decision"] = decision.to_dict()
            if not decision.required:
                trace["final_reason"] = decision.reason
                break

            query_plan = self.corrective_query_planner.plan(
                query=query,
                assessment=assessment,
                runtime_context={"request_context": dict(request_context or {})},
            )
            queries = query_plan.normalized_queries(original_query=query)
            if not queries:
                trace["final_reason"] = "correction gate opened but planner returned no queries"
                break

            round_index = completed_rounds + 1
            round_sets: list[CandidateSet] = []
            query_execution: list[dict[str, Any]] = []
            for query_index, corrective_query in enumerate(queries, start=1):
                candidate_set, execution = self._retrieve_one_query(
                    retrieval_query=corrective_query,
                    query_label=f"correction_r{round_index}_q{query_index}",
                    query_index=query_index,
                    filter_expr=filter_expr,
                    keyword_doc_ids=keyword_doc_ids,
                    keyword_scope=keyword_scope,
                    stage="corrective_retrieval",
                )
                round_sets.append(candidate_set)
                query_execution.append(execution)

            merge_sets = list(round_sets)
            if query_plan.merge_original_candidates:
                merge_sets.insert(
                    0,
                    CandidateSet(
                        query=query,
                        source_name=f"correction_r{round_index}_base",
                        candidates=[dict(item) for item in current_reranked],
                        metadata={"stage": "previous_reranked"},
                    ),
                )
            merged, fusion_metadata = self._fuse_query_sets(merge_sets)
            current_reranked = self.reranker.rerank(query=query, results=merged)
            completed_rounds = round_index
            assessment = self._assess(
                query=query,
                results=current_reranked,
                correction_round=completed_rounds,
                request_context=request_context,
            )
            trace["triggered"] = True
            trace["rounds"].append(
                {
                    "round": round_index,
                    "gate_decision": decision.to_dict(),
                    "query_plan": query_plan.to_dict(),
                    "query_execution": query_execution,
                    "query_fusion_execution": fusion_metadata,
                    "reranker_output_count": len(current_reranked),
                    "assessment": assessment.to_dict(),
                }
            )

        trace["completed_rounds"] = completed_rounds
        trace["final_assessment"] = assessment.to_dict()
        return current_reranked, assessment, trace

    def run(
        self,
        query: str,
        *,
        filter_expr: str | None,
        keyword_doc_ids: list[str] | None,
        keyword_scope: dict[str, Any] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> RetrievalStageResult:
        plan = self.retrieval_planner.plan(
            query=query,
            request_context=extra_metadata,
        )
        retrieval_plan = plan.to_dict()
        expansion = self.query_transform_selector.transform(
            query,
            mode=plan.query_transform_mode,
        )
        expansion.metadata["retrieval_plan"] = retrieval_plan

        query_candidate_sets: list[CandidateSet] = []
        query_execution: list[dict[str, Any]] = []
        for index, retrieval_query in enumerate(expansion.retrieval_queries, start=1):
            candidate_set, execution = self._retrieve_one_query(
                retrieval_query=retrieval_query,
                query_label=f"q{index}",
                query_index=index,
                filter_expr=filter_expr,
                keyword_doc_ids=keyword_doc_ids,
                keyword_scope=keyword_scope,
                stage="initial_retrieval",
            )
            query_candidate_sets.append(candidate_set)
            query_execution.append(execution)

        fused_results, query_fusion_metadata = self._fuse_query_sets(
            query_candidate_sets
        )
        expansion.metadata["configured_retrieval_stack"] = {
            "retrievers": [self._metadata(item) for item in self.retrievers],
            "source_fusion": self._metadata(self.source_fusion),
            "query_fusion": self._metadata(self.query_fusion),
            "candidate_enricher": self._metadata(self.candidate_enricher),
            "reranker": self._metadata(self.reranker),
            "queries": query_execution,
            "query_fusion_execution": query_fusion_metadata,
        }

        reranked = self.reranker.rerank(query=query, results=fused_results)
        expansion.metadata["configured_reranker"] = {
            **self._metadata(self.reranker),
            **dict(self.reranker.execution_metadata() or {}),
            "input_count": len(fused_results),
            "output_count": len(reranked),
        }

        initial_assessment = self._assess(
            query=query,
            results=reranked,
            correction_round=0,
            request_context=extra_metadata,
        )
        final_reranked, final_assessment, correction_trace = self._run_correction(
            query=query,
            initial_reranked=reranked,
            initial_assessment=initial_assessment,
            correction_budget=plan.correction_budget,
            filter_expr=filter_expr,
            keyword_doc_ids=keyword_doc_ids,
            keyword_scope=keyword_scope,
            request_context=extra_metadata,
        )
        expansion.metadata["configured_evidence_assessor"] = {
            **self._metadata(self.evidence_assessor),
            **dict(self.evidence_assessor.execution_metadata() or {}),
            "initial_observed_count": len(reranked),
            "final_observed_count": len(final_reranked),
            "always_executed": True,
        }
        expansion.metadata["configured_corrective_retrieval_gate"] = {
            **self._metadata(self.corrective_retrieval_gate),
            **dict(self.corrective_retrieval_gate.execution_metadata() or {}),
        }
        expansion.metadata["configured_corrective_query_planner"] = {
            **self._metadata(self.corrective_query_planner),
            **dict(self.corrective_query_planner.execution_metadata() or {}),
        }
        quality_report = {
            **dict(final_assessment.report),
            "initial_assessment": initial_assessment.to_dict(),
            "final_assessment": final_assessment.to_dict(),
            "corrective_retrieval": correction_trace,
        }
        return RetrievalStageResult(
            retrieval_plan=retrieval_plan,
            query_expansion=expansion,
            p2_results=fused_results,
            reranked_results=list(final_reranked),
            results=list(final_reranked),
            evidence_quality=quality_report,
            correction_triggered=bool(correction_trace.get("triggered")),
        )
