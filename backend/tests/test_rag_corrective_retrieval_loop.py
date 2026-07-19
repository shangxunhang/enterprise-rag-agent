"""Assessment-driven and budget-bounded corrective retrieval contracts."""

from __future__ import annotations

from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.planning.retrieval_planner import RetrievalPlan
from rag.plugins.correction_gates import EvidenceSufficiencyCorrectionGate
from rag.plugins.corrective_query_planners import SectionGapCorrectiveQueryPlanner
from rag.ports.quality import EvidenceAssessment
from rag.query.query_expander import QueryExpansionResult
from rag.schema.candidate import CandidateSet


def _candidate(chunk_id: str, text: str, *, rank: int = 1) -> dict:
    return {
        "chunk_id": chunk_id,
        "parent_chunk_id": f"parent-{chunk_id}",
        "text": text,
        "parent_text": text,
        "score": 1.0,
        "rank": rank,
        "metadata": {},
    }


class _Planner:
    def __init__(self, correction_budget: int) -> None:
        self.correction_budget = correction_budget

    def plan(self, *, query: str, request_context=None) -> RetrievalPlan:
        del query, request_context
        return RetrievalPlan(
            plan_id="test-plan",
            query_transform_mode="identity",
            correction_budget=self.correction_budget,
            reason="test",
        )


class _Selector:
    def transform(self, query: str, *, mode: str) -> QueryExpansionResult:
        return QueryExpansionResult(
            original_query=query,
            retrieval_queries=[query],
            metadata={"selected_mode": mode},
        )


class _Retriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, request) -> CandidateSet:
        self.queries.append(request.query)
        is_correction = len(self.queries) > 1
        chunk_id = "relevant" if is_correction else "insufficient"
        return CandidateSet(
            query=request.query,
            source_name="fake",
            candidates=[_candidate(chunk_id, f"{chunk_id} evidence")],
        )


class _SourceFusion:
    def fuse(self, candidate_sets) -> CandidateSet:
        source = candidate_sets[0]
        return source.copy_with(source_name="source-fused")


class _QueryFusion:
    def fuse(self, candidate_sets) -> CandidateSet:
        candidates = []
        for item in candidate_sets:
            candidates.extend(dict(row) for row in item.candidates)
        return CandidateSet(
            query=candidate_sets[0].query,
            source_name="query-fused",
            candidates=candidates,
        )


class _Enricher:
    def enrich(self, candidate_set) -> CandidateSet:
        return candidate_set.copy_with(source_name="enriched")


class _Reranker:
    def rerank(self, *, query, results):
        del query
        rows = [dict(item) for item in results]
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def execution_metadata(self):
        return {"top_k": 5}


class _CountingAssessor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def assess(self, *, query, results, runtime_context=None) -> EvidenceAssessment:
        del query, runtime_context
        chunk_ids = [str(item["chunk_id"]) for item in results]
        self.calls.append(chunk_ids)
        sufficient = "relevant" in chunk_ids
        return EvidenceAssessment(
            sufficient=sufficient,
            confidence=1.0 if sufficient else 0.1,
            reason="enough evidence" if sufficient else "evidence gap",
            report={"assessment_call": len(self.calls)},
        )

    def execution_metadata(self):
        return {"mode": "counting"}


class _CountingQueryPlanner:
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = SectionGapCorrectiveQueryPlanner(
            use_llm=False,
            max_queries=1,
        )

    def plan(self, **kwargs):
        self.calls += 1
        return self.delegate.plan(**kwargs)

    def execution_metadata(self):
        return self.delegate.execution_metadata()


def _pipeline(*, correction_budget: int):
    retriever = _Retriever()
    assessor = _CountingAssessor()
    query_planner = _CountingQueryPlanner()
    pipeline = ParentChildRetrievalPipeline(
        retrievers=[retriever],
        source_fusion=_SourceFusion(),
        query_fusion=_QueryFusion(),
        candidate_enricher=_Enricher(),
        reranker=_Reranker(),
        query_transform_selector=_Selector(),
        retrieval_planner=_Planner(correction_budget),
        evidence_assessor=assessor,
        corrective_retrieval_gate=EvidenceSufficiencyCorrectionGate(),
        corrective_query_planner=query_planner,
    )
    return pipeline, retriever, assessor, query_planner


def test_gate_opens_only_for_insufficient_evidence_with_budget() -> None:
    gate = EvidenceSufficiencyCorrectionGate()
    insufficient = EvidenceAssessment(
        sufficient=False,
        confidence=0.1,
        reason="gap",
    )
    sufficient = EvidenceAssessment(
        sufficient=True,
        confidence=0.9,
        reason="enough",
    )

    assert gate.decide(
        assessment=insufficient,
        correction_budget=1,
        completed_rounds=0,
    ).required is True
    assert gate.decide(
        assessment=insufficient,
        correction_budget=1,
        completed_rounds=1,
    ).required is False
    assert gate.decide(
        assessment=sufficient,
        correction_budget=1,
        completed_rounds=0,
    ).required is False


def test_corrective_query_planner_runs_only_after_gate_opens() -> None:
    pipeline, retriever, assessor, query_planner = _pipeline(correction_budget=0)

    output = pipeline.run(
        "enterprise RAG architecture",
        filter_expr=None,
        keyword_doc_ids=None,
        extra_metadata={},
    )

    assert len(retriever.queries) == 1
    assert len(assessor.calls) == 1
    assert query_planner.calls == 0
    assert output.correction_triggered is False
    assert output.evidence_quality["corrective_retrieval"]["final_reason"].endswith(
        "budget is exhausted"
    )


def test_pipeline_always_reassesses_after_full_corrective_retrieval() -> None:
    pipeline, retriever, assessor, query_planner = _pipeline(correction_budget=1)

    output = pipeline.run(
        "enterprise RAG architecture",
        filter_expr=None,
        keyword_doc_ids=None,
        extra_metadata={
            "document_context": {
                "document_title": "enterprise RAG architecture",
                "required_sections": ["retrieval", "generation"],
            }
        },
    )

    assert len(retriever.queries) == 2
    assert len(assessor.calls) == 2
    assert query_planner.calls == 1
    assert output.correction_triggered is True
    assert output.evidence_quality["corrective_retrieval"]["completed_rounds"] == 1
    assert output.evidence_quality["final_assessment"]["sufficient"] is True
    assert [item["chunk_id"] for item in output.results] == assessor.calls[-1]
    assert [item["chunk_id"] for item in output.reranked_results] == assessor.calls[-1]
    assert output.query_expansion.metadata["configured_evidence_assessor"][
        "always_executed"
    ] is True


def test_query_plan_normalizes_duplicates_and_original_query() -> None:
    from rag.ports.quality import CorrectiveQueryPlan

    plan = CorrectiveQueryPlan(
        queries=("original", "new query", "new query", "  second query  ")
    )

    assert plan.normalized_queries(original_query="original") == [
        "new query",
        "second query",
    ]
