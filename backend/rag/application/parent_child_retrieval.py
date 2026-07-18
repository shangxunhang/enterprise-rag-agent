"""Configuration-driven retrieval, evidence grading and compatibility routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from rag.ports.quality import EvidenceCorrectionPlan, EvidenceGradeOutput
from rag.schema.candidate import CandidateSet, RetrievalRequest


@dataclass
class RetrievalStageResult:
    original_strategy: str
    effective_strategy: str
    adaptive_rag: Optional[Dict[str, Any]]
    query_expansion: Any
    p2_results: List[Dict[str, Any]]
    pre_crag_results: List[Dict[str, Any]]
    results: List[Dict[str, Any]]
    c_rag: Optional[Dict[str, Any]]
    crag_enabled: bool
    self_rag_enabled: bool


class ParentChildRetrievalPipeline:
    """Stable pipeline shell composed from configured retrieval components."""

    def __init__(
        self,
        *,
        retrievers: list[Any],
        fusion: Any,
        query_fusion: Any,
        candidate_enricher: Any,
        reranker: Any,
        query_transform_chain: Any,
        adaptive_router: Any,
        evidence_grader: Any | None = None,
        generation_checker_enabled: bool = False,
        crag_judge: Any = None,
    ) -> None:
        if not retrievers:
            raise ValueError("at least one configured retriever is required")
        self.retrievers = list(retrievers)
        self.fusion = fusion
        self.query_fusion = query_fusion
        self.candidate_enricher = candidate_enricher
        self.reranker = reranker
        self.query_transform_chain = query_transform_chain
        self.adaptive_router = adaptive_router
        self.evidence_grader = evidence_grader
        self.generation_checker_enabled = bool(generation_checker_enabled)
        # Compatibility-only fallback for tests and callers predating Step 5.
        self.crag_judge = crag_judge

    @staticmethod
    def _metadata(component: Any) -> dict[str, Any]:
        metadata = getattr(component, "plugin_metadata", None)
        if metadata is not None and hasattr(metadata, "to_dict"):
            return metadata.to_dict()
        return {
            "name": component.__class__.__name__,
            "version": "unknown",
            "implementation": (
                f"{component.__class__.__module__}."
                f"{component.__class__.__qualname__}"
            ),
        }

    def _retrieve_one_query(
        self,
        *,
        retrieval_query: str,
        query_label: str,
        query_index: int,
        filter_expr: Optional[str],
        keyword_doc_ids: Optional[List[str]],
        stage: str,
    ) -> tuple[CandidateSet, dict[str, Any]]:
        request = RetrievalRequest(
            query=retrieval_query,
            filter_expr=filter_expr,
            doc_ids=list(keyword_doc_ids or []),
            metadata={
                "query_index": int(query_index),
                "retrieval_stage": stage,
            },
        )
        source_sets = [item.retrieve(request) for item in self.retrievers]
        fused_children = self.fusion.fuse(source_sets)
        enriched = self.candidate_enricher.enrich(fused_children)
        candidate_set = enriched.copy_with(source_name=query_label)
        execution = {
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
        return candidate_set, execution

    def _fuse_query_sets(
        self,
        candidate_sets: list[CandidateSet],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if len(candidate_sets) <= 1:
            results = list(candidate_sets[0].candidates) if candidate_sets else []
            return results, {
                "applied": False,
                "query_set_count": len(candidate_sets),
            }
        fused = self.query_fusion.fuse(candidate_sets)
        return list(fused.candidates), {
            "applied": True,
            **dict(fused.metadata),
        }

    def _grade(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        correction_round: int,
        allow_correction: bool,
        request_context: dict[str, Any] | None = None,
    ) -> EvidenceGradeOutput:
        if self.evidence_grader is None:
            return EvidenceGradeOutput(results=list(results), report=None)
        try:
            return self.evidence_grader.grade(
                query=query,
                results=results,
                runtime_context={
                    "correction_round": int(correction_round),
                    "allow_correction": bool(allow_correction),
                    "request_context": dict(request_context or {}),
                },
            )
        except TypeError:
            # Compatibility for external graders implementing the pre-Step-9
            # signature. Configured built-ins use the runtime_context form.
            return self.evidence_grader.grade(query=query, results=results)

    def _run_corrective_loop(
        self,
        *,
        query: str,
        initial_reranked: list[dict[str, Any]],
        initial_grade: EvidenceGradeOutput,
        filter_expr: Optional[str],
        keyword_doc_ids: Optional[List[str]],
        request_context: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any]]:
        correction = initial_grade.correction or EvidenceCorrectionPlan(required=False)
        queries = correction.normalized_queries(original_query=query)
        trace: dict[str, Any] = {
            "triggered": False,
            "reason": correction.reason,
            "requested_queries": list(correction.queries),
            "normalized_queries": queries,
            "max_rounds": max(0, int(correction.max_rounds)),
            "merge_original_candidates": bool(correction.merge_original_candidates),
            "plan_metadata": dict(correction.metadata or {}),
            "rounds": [],
        }
        if not correction.required or not queries or correction.max_rounds <= 0:
            trace["final_reason"] = "correction_not_required_or_no_queries"
            return list(initial_grade.results), initial_grade.report, trace

        current_results = list(initial_grade.results)
        current_report = initial_grade.report
        original_set = CandidateSet(
            query=query,
            source_name="crag_initial",
            candidates=[dict(item) for item in initial_reranked],
            metadata={"stage": "initial_reranked"},
        )
        trace["triggered"] = True
        print(
            "[CorrectiveRetrieval] START "
            f"queries={len(queries)} max_rounds={correction.max_rounds} "
            f"reason={correction.reason}"
        )

        for round_index in range(1, max(0, int(correction.max_rounds)) + 1):
            round_sets: list[CandidateSet] = []
            round_execution: list[dict[str, Any]] = []
            for query_index, corrective_query in enumerate(queries, start=1):
                label = f"crag_r{round_index}_q{query_index}"
                candidate_set, execution = self._retrieve_one_query(
                    retrieval_query=corrective_query,
                    query_label=label,
                    query_index=query_index,
                    filter_expr=filter_expr,
                    keyword_doc_ids=keyword_doc_ids,
                    stage="corrective_retrieval",
                )
                round_sets.append(candidate_set)
                round_execution.append(execution)

            merge_sets: list[CandidateSet] = []
            if correction.merge_original_candidates:
                merge_sets.append(original_set)
            merge_sets.extend(round_sets)
            merged, fusion_meta = self._fuse_query_sets(merge_sets)
            reranked = self.reranker.rerank(query=query, results=merged)
            final_grade = self._grade(
                query=query,
                results=reranked,
                correction_round=round_index,
                allow_correction=False,
                request_context=request_context,
            )
            current_results = list(final_grade.results)
            current_report = final_grade.report
            round_trace = {
                "round": round_index,
                "queries": list(queries),
                "query_execution": round_execution,
                "merge_original_candidates": bool(
                    correction.merge_original_candidates
                ),
                "merged_input_count": len(merged),
                "reranker_output_count": len(reranked),
                "query_fusion_execution": fusion_meta,
                "grade_report": final_grade.report,
                "output_count": len(current_results),
            }
            trace["rounds"].append(round_trace)
            print(
                "[CorrectiveRetrieval] ROUND "
                f"round={round_index} merged={len(merged)} "
                f"reranked={len(reranked)} output={len(current_results)} "
                f"confidence={(current_report or {}).get('retrieval_confidence')}"
            )
            # Current implementation is deliberately bounded. Future graders
            # can emit a new plan, but this loop will not recursively mutate
            # itself beyond the configured maximum.
            if round_index >= correction.max_rounds:
                break

        trace["final_output_count"] = len(current_results)
        trace["final_retrieval_confidence"] = (
            (current_report or {}).get("retrieval_confidence")
        )
        final_decision = dict((current_report or {}).get("correction_decision") or {})
        final_quality_insufficient = bool(final_decision.get("quality_insufficient"))
        trace["final_quality_insufficient"] = final_quality_insufficient
        trace["final_reason"] = (
            "corrective_retrieval_completed_but_quality_insufficient"
            if final_quality_insufficient
            else "corrective_retrieval_completed"
        )
        print(
            "[CorrectiveRetrieval] END "
            f"rounds={len(trace['rounds'])} output={len(current_results)} "
            f"confidence={trace['final_retrieval_confidence']}"
        )
        return current_results, current_report, trace

    def run(
        self,
        query: str,
        *,
        dense_top_k: int,
        keyword_top_k: int,
        candidate_top_k: int,
        rrf_k: Optional[int],
        rerank_top_k: int,
        filter_expr: Optional[str],
        keyword_doc_ids: Optional[List[str]],
        retrieval_strategy: str,
        num_rewrites: int,
        enable_hyde: Optional[bool],
        enable_crag: Optional[bool],
        enable_self_rag: Optional[bool],
        crag_max_judge_chunks: int,
        crag_drop_irrelevant: bool,
        extra_metadata: Optional[Dict[str, Any]],
    ) -> RetrievalStageResult:
        original = str(retrieval_strategy or "hybrid")
        configured_profile = (
            str(getattr(self.query_transform_chain, "profile_id", "") or "").strip()
            or "configured_profile"
        )
        adaptive = None
        effective = configured_profile
        effective_hyde = enable_hyde
        effective_crag = enable_crag
        effective_self_rag = enable_self_rag
        if self.adaptive_router.is_adaptive_strategy(original):
            decision = self.adaptive_router.route(
                query=query,
                task_type=(extra_metadata or {}).get("task_type")
                if isinstance(extra_metadata, dict)
                else None,
            )
            adaptive = {
                **decision.to_dict(),
                "advisory_only": True,
                "configured_profile_id": configured_profile,
            }

        expansion = self.query_transform_chain.transform(
            query=query,
            strategy_label=configured_profile,
        )
        expansion.metadata["legacy_request_overrides"] = {
            "num_rewrites": int(num_rewrites),
            "enable_hyde": bool(effective_hyde),
            "ignored_by_configured_chain": True,
        }
        expansion.metadata["legacy_retrieval_overrides"] = {
            "dense_top_k": int(dense_top_k),
            "keyword_top_k": int(keyword_top_k),
            "candidate_top_k": int(candidate_top_k),
            "rrf_k": None if rrf_k is None else int(rrf_k),
            "ignored_by_configured_stack": True,
        }
        expansion.metadata["legacy_reranker_overrides"] = {
            "rerank_top_k": int(rerank_top_k),
            "ignored_by_configured_reranker": True,
        }
        expansion.metadata["legacy_quality_overrides"] = {
            "enable_crag": bool(effective_crag),
            "enable_self_rag": bool(effective_self_rag),
            "crag_max_judge_chunks": int(crag_max_judge_chunks),
            "crag_drop_irrelevant": bool(crag_drop_irrelevant),
            "ignored_by_configured_quality_plugins": self.evidence_grader
            is not None,
        }
        expansion.metadata["legacy_strategy_control"] = {
            "requested_strategy": original,
            "configured_profile_id": configured_profile,
            "ignored_for_non_router_plugins": True,
        }
        if adaptive is not None:
            expansion.metadata["adaptive_rag"] = adaptive
        expansion.metadata["original_strategy"] = original
        expansion.metadata["effective_strategy"] = effective

        query_candidate_sets: list[CandidateSet] = []
        execution_queries: list[dict[str, Any]] = []
        for index, retrieval_query in enumerate(expansion.retrieval_queries, start=1):
            candidate_set, execution = self._retrieve_one_query(
                retrieval_query=retrieval_query,
                query_label=f"q{index}",
                query_index=index,
                filter_expr=filter_expr,
                keyword_doc_ids=keyword_doc_ids,
                stage="initial_retrieval",
            )
            query_candidate_sets.append(candidate_set)
            execution_queries.append(execution)

        p2_results, query_fusion_metadata = self._fuse_query_sets(query_candidate_sets)
        expansion.metadata["configured_retrieval_stack"] = {
            "retrievers": [self._metadata(item) for item in self.retrievers],
            "fusion": self._metadata(self.fusion),
            "query_fusion": self._metadata(self.query_fusion),
            "candidate_enricher": self._metadata(self.candidate_enricher),
            "reranker": self._metadata(self.reranker),
            "queries": execution_queries,
            "query_fusion_execution": query_fusion_metadata,
        }

        results = self.reranker.rerank(query=query, results=p2_results)
        reranker_execution = self.reranker.execution_metadata()
        expansion.metadata["configured_reranker"] = {
            **self._metadata(self.reranker),
            **dict(reranker_execution or {}),
            "input_count": len(p2_results),
            "output_count": len(results),
        }

        pre_grade = list(results)
        c_rag: Optional[Dict[str, Any]] = None
        crag_enabled = False
        if self.evidence_grader is not None:
            initial_grade = self._grade(
                query=query,
                results=results,
                correction_round=0,
                allow_correction=True,
                request_context=extra_metadata,
            )
            results, final_report, correction_trace = self._run_corrective_loop(
                query=query,
                initial_reranked=pre_grade,
                initial_grade=initial_grade,
                filter_expr=filter_expr,
                keyword_doc_ids=keyword_doc_ids,
                request_context=extra_metadata,
            )
            if correction_trace.get("triggered"):
                c_rag = {
                    **dict(final_report or {}),
                    "initial_grade": initial_grade.report,
                    "final_grade": final_report,
                    "corrective_retrieval": correction_trace,
                }
            else:
                c_rag = initial_grade.report
                if c_rag is not None:
                    c_rag = {
                        **dict(c_rag),
                        "corrective_retrieval": correction_trace,
                    }
            execution = self.evidence_grader.execution_metadata()
            crag_enabled = bool(execution.get("enabled", False))
            expansion.metadata["configured_evidence_grader"] = {
                **self._metadata(self.evidence_grader),
                **dict(execution or {}),
                "input_count": len(pre_grade),
                "output_count": len(results),
                "report_method": (c_rag or {}).get("method"),
                "corrective_retrieval": correction_trace,
            }
        else:
            crag_enabled = bool(effective_crag)
            if crag_enabled and self.crag_judge is not None:
                results, crag_result = self.crag_judge.evaluate_and_filter(
                    query=query,
                    results=results,
                    max_judge_chunks=crag_max_judge_chunks,
                    drop_irrelevant=crag_drop_irrelevant,
                )
                c_rag = crag_result.to_dict()

        return RetrievalStageResult(
            original_strategy=original,
            effective_strategy=effective,
            adaptive_rag=adaptive,
            query_expansion=expansion,
            p2_results=p2_results,
            pre_crag_results=pre_grade,
            results=results,
            c_rag=c_rag,
            crag_enabled=crag_enabled,
            self_rag_enabled=self.generation_checker_enabled,
        )
