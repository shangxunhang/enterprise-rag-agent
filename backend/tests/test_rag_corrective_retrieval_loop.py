from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.config.pipeline_config import ComponentConfig, PipelineConfigLoader
from rag.plugins.evidence_graders import CRAGCorrectiveEvidenceGraderPlugin
from rag.plugins.evidence_graders.plugin import _extract_query_list
from rag.ports.quality import EvidenceCorrectionPlan
from rag.query.query_transform_chain import QueryTransformChain
from rag.registry.default_registrations import build_default_component_registry
from rag.schema.candidate import CandidateSet


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _quality_context() -> dict:
    return {
        "quality_llm_generator": None,
        "enable_quality_llm": False,
        "quality_generation_params": {},
    }


def _candidate(*, cid: str, text: str, rank: int = 1, score: float = 0.1) -> dict:
    return {
        "chunk_id": cid,
        "child_chunk_id": f"{cid}-child",
        "parent_chunk_id": cid,
        "matched_chunk_id": f"{cid}-child",
        "context_chunk_id": cid,
        "parent_text": text,
        "text": text,
        "score": score,
        "rank": rank,
        "metadata": {"matched_child_count": 1},
    }



def test_corrective_query_parser_extracts_strings_from_object_array() -> None:
    raw = '''{
      "queries": [
        {"query": "企业级 RAG-Agent 系统建设方案"},
        {"query": "如何根据资料生成企业级 RAG-Agent 系统建设方案"}
      ],
      "reason": "evidence quality is low"
    }'''

    assert _extract_query_list(raw) == [
        "企业级 RAG-Agent 系统建设方案",
        "如何根据资料生成企业级 RAG-Agent 系统建设方案",
    ]


def test_correction_plan_normalizes_structured_queries_without_dict_syntax() -> None:
    plan = EvidenceCorrectionPlan(
        required=True,
        queries=[
            {"query": "企业级 RAG-Agent 系统建设方案"},
            {"text": "如何根据资料生成企业级 RAG-Agent 系统建设方案"},
        ],
    )

    assert plan.normalized_queries(original_query="原始问题") == [
        "企业级 RAG-Agent 系统建设方案",
        "如何根据资料生成企业级 RAG-Agent 系统建设方案",
    ]
    assert all("{" not in item for item in plan.normalized_queries())

def test_corrective_profiles_are_registered_and_valid() -> None:
    registry = build_default_component_registry()
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/c_rag_corrective_v1.yaml"
    )
    combined = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/c_rag_corrective_self_rag_v1.yaml"
    )

    assert profile.evidence_grader.name == "crag_corrective"
    assert combined.evidence_grader.name == "crag_corrective"
    assert combined.generation_checker.name == "self_rag_lite"
    plugin = registry.build(
        category="evidence_grader",
        config=ComponentConfig(
            name="crag_corrective",
            params={
                "use_llm": False,
                "confidence_threshold": 0.9,
                "max_correction_queries": 2,
            },
        ),
        build_context=_quality_context(),
    )
    assert isinstance(plugin, CRAGCorrectiveEvidenceGraderPlugin)
    assert plugin.plugin_metadata.name == "crag_corrective"
    assert plugin.execution_metadata()["corrective_retrieval_enabled"] is True


def test_corrective_grader_emits_bounded_plan_for_low_confidence_evidence() -> None:
    plugin = CRAGCorrectiveEvidenceGraderPlugin(
        build_context=_quality_context(),
        use_llm=False,
        confidence_threshold=0.9,
        min_relevant_chunks=1,
        max_correction_queries=2,
        max_correction_rounds=1,
    )

    output = plugin.grade(
        query="enterprise rag architecture",
        results=[_candidate(cid="noise", text="tomato soup kitchen recipe")],
        runtime_context={"correction_round": 0, "allow_correction": True},
    )

    assert output.correction is not None
    assert output.correction.required is True
    assert len(output.correction.queries) == 2
    assert output.correction.max_rounds == 1
    assert output.report["correction_decision"]["quality_insufficient"] is True
    assert output.report["correction_decision"]["required"] is True


def test_corrective_grader_does_not_recurse_after_final_round() -> None:
    plugin = CRAGCorrectiveEvidenceGraderPlugin(
        build_context=_quality_context(),
        use_llm=False,
        confidence_threshold=0.9,
        max_correction_rounds=1,
    )

    output = plugin.grade(
        query="enterprise rag architecture",
        results=[_candidate(cid="noise", text="tomato soup kitchen recipe")],
        runtime_context={"correction_round": 1, "allow_correction": False},
    )

    assert output.correction is not None
    assert output.correction.required is False
    assert output.correction.queries == []
    assert output.report["correction_decision"]["quality_insufficient"] is True


class _ConditionalRetriever:
    source_name = "conditional"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, request):
        self.queries.append(request.query)
        if "关键事实" in request.query or "技术细节" in request.query:
            rows = [
                _candidate(
                    cid=f"relevant-{len(self.queries)}",
                    text=(
                        "enterprise rag architecture retrieves documents, "
                        "reranks evidence and augments generation"
                    ),
                    score=0.9,
                )
            ]
        else:
            rows = [_candidate(cid="noise", text="tomato soup kitchen recipe")]
        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=deepcopy(rows),
        )


class _PassFusion:
    def fuse(self, candidate_sets):
        return candidate_sets[0]


class _MergeQueryFusion:
    def fuse(self, candidate_sets):
        rows = []
        for candidate_set in candidate_sets:
            rows.extend(deepcopy(candidate_set.candidates))
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return CandidateSet(
            query=candidate_sets[0].query,
            source_name="merged",
            candidates=rows,
            metadata={
                "query_set_count": len(candidate_sets),
                "fused_count": len(rows),
            },
        )


class _PassEnricher:
    def enrich(self, candidate_set):
        return candidate_set


class _PassReranker:
    def rerank(self, *, query, results):
        del query
        rows = [dict(item) for item in results]
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def execution_metadata(self):
        return {"top_k": 5, "text_field": "parent_text"}


class _NoAdaptiveRouter:
    @staticmethod
    def is_adaptive_strategy(strategy):
        del strategy
        return False


def _identity_chain():
    registry = build_default_component_registry()
    identity = registry.build(
        category="query_transformer",
        config=ComponentConfig(name="identity"),
    )
    return QueryTransformChain([identity], profile_id="corrective_test")


def test_pipeline_executes_full_corrective_retrieval_loop() -> None:
    retriever = _ConditionalRetriever()
    grader = CRAGCorrectiveEvidenceGraderPlugin(
        build_context=_quality_context(),
        use_llm=False,
        confidence_threshold=0.9,
        min_relevant_chunks=1,
        max_correction_queries=2,
        max_correction_rounds=1,
        merge_original_candidates=True,
    )
    pipeline = ParentChildRetrievalPipeline(
        retrievers=[retriever],
        fusion=_PassFusion(),
        query_fusion=_MergeQueryFusion(),
        candidate_enricher=_PassEnricher(),
        reranker=_PassReranker(),
        query_transform_chain=_identity_chain(),
        adaptive_router=_NoAdaptiveRouter(),
        evidence_grader=grader,
        generation_checker_enabled=False,
    )

    output = pipeline.run(
        "enterprise rag architecture",
        dense_top_k=10,
        keyword_top_k=10,
        candidate_top_k=10,
        rrf_k=60,
        rerank_top_k=5,
        filter_expr=None,
        keyword_doc_ids=None,
        retrieval_strategy="hybrid",
        num_rewrites=3,
        enable_hyde=False,
        enable_crag=False,
        enable_self_rag=False,
        crag_max_judge_chunks=8,
        crag_drop_irrelevant=True,
        extra_metadata=None,
    )

    assert len(retriever.queries) == 3
    assert retriever.queries[0] == "enterprise rag architecture"
    assert any("关键事实" in item for item in retriever.queries[1:])
    assert any(row["parent_chunk_id"].startswith("relevant-") for row in output.results)
    assert output.c_rag is not None
    correction = output.c_rag["corrective_retrieval"]
    assert correction["triggered"] is True
    assert len(correction["rounds"]) == 1
    assert correction["rounds"][0]["reranker_output_count"] == 3
    configured = output.query_expansion.metadata["configured_evidence_grader"]
    assert configured["mode"] == "crag_corrective"
    assert configured["corrective_retrieval"]["triggered"] is True


def test_crag_demotion_only_never_reorders_partial_candidates_by_llm_score() -> None:
    from rag.judge.rag_quality_judge import CRAGJudge

    judge = CRAGJudge(use_llm=False, ranking_policy="demotion_only")
    score_by_id = {"strong": 0.2, "middle": 0.1, "noise": 0.9}

    def fake_judge_chunk(*, query, result, rank):
        del query
        return {
            "chunk_id": result["chunk_id"],
            "rank": rank,
            "relevance_label": "partial",
            "decision": "downrank",
            "score": score_by_id[result["chunk_id"]],
            "reason": "test judgement",
            "judge_method": "fake",
        }

    judge.judge_chunk = fake_judge_chunk  # type: ignore[method-assign]
    rows = [
        _candidate(cid="strong", text="strong bge evidence", rank=1),
        _candidate(cid="middle", text="medium bge evidence", rank=2),
        _candidate(cid="noise", text="unrelated correction study", rank=3),
    ]
    rows[0]["rerank_score"] = 8.0
    rows[1]["rerank_score"] = 1.0
    rows[2]["rerank_score"] = -8.2

    filtered, report = judge.evaluate_and_filter(
        query="enterprise rag architecture",
        results=rows,
        drop_irrelevant=True,
    )

    assert [item["chunk_id"] for item in filtered] == [
        "strong",
        "middle",
        "noise",
    ]
    assert report.metadata["ranking_policy"] == "demotion_only"
    assert report.metadata["judge_score_used_for_promotion"] is False
    assert all(
        item["metadata"]["c_rag_judge_score_used_for_promotion"] is False
        for item in filtered
    )


class _PlannerAwareGenerator:
    def generate(self, prompt, **kwargs):
        del kwargs
        if "请判断检索片段" in prompt:
            return (
                '{"relevance_label":"partial","decision":"downrank",'
                '"score":0.2,"reason":"证据覆盖不足"}'
            )
        return (
            '{"queries":["企业级 RAG-Agent 系统建设方案"],'
            '"reason":"原检索证据不足"}'
        )


def test_section_gap_planner_rejects_trivial_rewrite_and_fills_section_queries() -> None:
    plugin = CRAGCorrectiveEvidenceGraderPlugin(
        build_context={
            "quality_llm_generator": _PlannerAwareGenerator(),
            "enable_quality_llm": True,
            "quality_generation_params": {},
        },
        use_llm=True,
        confidence_threshold=0.9,
        min_relevant_chunks=1,
        max_correction_queries=2,
        max_correction_rounds=1,
        query_planner="section_gap_aware_v1",
        reject_trivial_rewrites=True,
    )

    output = plugin.grade(
        query="根据资料生成企业级 RAG-Agent 系统建设方案",
        results=[_candidate(cid="noise", text="纠错学习方法与训练数据")],
        runtime_context={
            "correction_round": 0,
            "allow_correction": True,
            "request_context": {
                "task_type": "scheme_generation",
                "document_context": {
                    "document_title": "企业级 RAG-Agent 系统建设方案",
                    "required_sections": [
                        "项目概述",
                        "建设内容",
                        "技术方案",
                        "安全设计",
                        "实施与验收",
                    ],
                    "citation_required_sections": [
                        "建设内容",
                        "技术方案",
                        "安全设计",
                    ],
                },
            },
        },
    )

    assert output.correction is not None
    assert output.correction.required is True
    assert len(output.correction.queries) == 2
    assert "企业级 RAG-Agent 系统建设方案" not in output.correction.queries
    combined = " ".join(output.correction.queries)
    assert "建设内容" in combined
    assert "技术方案" in combined
    assert "安全设计" in combined
    generation = output.correction.metadata["query_generation"]
    assert generation["method"] == "llm_with_gap_completion"
    assert generation["rejected_trivial_queries"] == [
        "企业级 RAG-Agent 系统建设方案"
    ]
    assert generation["fallback_used"] is True


def test_corrective_profiles_explicitly_enable_safe_ranking_and_section_planner() -> None:
    for filename in (
        "c_rag_corrective_v1.yaml",
        "c_rag_corrective_self_rag_v1.yaml",
    ):
        profile = PipelineConfigLoader().load(
            PROJECT_ROOT / "backend/rag/profiles" / filename
        )
        params = profile.evidence_grader.params
        assert params["ranking_policy"] == "demotion_only"
        assert params["query_planner"] == "section_gap_aware_v1"
        assert params["reject_trivial_rewrites"] is True


def test_crag_demotion_only_preserves_reranker_order_across_labels() -> None:
    from rag.judge.rag_quality_judge import CRAGJudge

    judge = CRAGJudge(use_llm=False, ranking_policy="demotion_only")
    labels = {
        "rank1": ("partial", "downrank", 0.2),
        "rank2": ("relevant", "keep", 0.99),
        "rank3": ("partial", "downrank", 0.8),
    }

    def fake_judge_chunk(*, query, result, rank):
        del query
        label, decision, score = labels[result["chunk_id"]]
        return {
            "chunk_id": result["chunk_id"],
            "rank": rank,
            "relevance_label": label,
            "decision": decision,
            "score": score,
            "reason": "test judgement",
            "judge_method": "fake",
        }

    judge.judge_chunk = fake_judge_chunk  # type: ignore[method-assign]
    rows = [
        _candidate(cid="rank1", text="first", rank=1),
        _candidate(cid="rank2", text="second", rank=2),
        _candidate(cid="rank3", text="third", rank=3),
    ]

    filtered, _ = judge.evaluate_and_filter(
        query="query",
        results=rows,
        drop_irrelevant=True,
    )

    assert [item["chunk_id"] for item in filtered] == ["rank1", "rank2", "rank3"]


def test_legacy_request_mapper_preserves_document_context_for_crag_planner() -> None:
    from rag.adapters.legacy.request_mapper import LegacyRAGRequestMapper
    from schemas.rag import RAGToolInputSchema

    document_context = {
        "document_title": "企业级 RAG-Agent 系统建设方案",
        "required_sections": ["建设内容", "技术方案", "安全设计"],
        "citation_required_sections": ["建设内容", "技术方案", "安全设计"],
    }
    original_tool_payload = {
        "query": "根据资料生成企业级 RAG-Agent 系统建设方案",
        "extra_metadata": {
            "task_type": "scheme_generation",
            "document_context": document_context,
        },
    }
    request = RAGToolInputSchema(
        task_id="task-context-pass",
        run_id="run-context-pass",
        agent_name="SchemeWriterAgent",
        query=original_tool_payload["query"],
        retrieval_mode="hybrid",
        extra=original_tool_payload,
    )

    invocation = LegacyRAGRequestMapper().map(request)

    assert invocation.payload["extra_metadata"] == {
        "task_type": "scheme_generation",
        "document_context": document_context,
    }


def test_realistic_nested_runtime_context_reaches_section_gap_planner() -> None:
    plugin = CRAGCorrectiveEvidenceGraderPlugin(
        build_context=_quality_context(),
        use_llm=False,
        confidence_threshold=0.9,
        min_relevant_chunks=1,
        max_correction_queries=2,
        max_correction_rounds=1,
        query_planner="section_gap_aware_v1",
    )
    forwarded_metadata = {
        "task_type": "scheme_generation",
        "document_context": {
            "document_title": "企业级 RAG-Agent 系统建设方案",
            "required_sections": [
                "项目概述",
                "建设内容",
                "技术方案",
                "安全设计",
            ],
            "citation_required_sections": [
                "建设内容",
                "技术方案",
                "安全设计",
            ],
        },
    }
    # This mirrors the real RAGToolRunner -> ParentChildRetrievalPipeline nesting.
    engine_extra_metadata = {
        **forwarded_metadata,
        "request_context": forwarded_metadata,
        "tool_name": "LegacyParentChildRAGTool",
    }

    output = plugin.grade(
        query="根据资料生成企业级 RAG-Agent 系统建设方案",
        results=[_candidate(cid="noise", text="纠错学习方法与训练数据")],
        runtime_context={
            "correction_round": 0,
            "allow_correction": True,
            "request_context": engine_extra_metadata,
        },
    )

    assert output.correction is not None
    planner = output.correction.metadata["planner_context"]
    assert planner["document_title"] == "企业级 RAG-Agent 系统建设方案"
    assert planner["citation_required_sections"] == [
        "建设内容",
        "技术方案",
        "安全设计",
    ]
    combined = " ".join(output.correction.queries)
    assert "建设内容" in combined
    assert "技术方案" in combined
    assert "安全设计" in combined
