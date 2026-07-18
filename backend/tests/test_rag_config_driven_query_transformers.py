from __future__ import annotations

from pathlib import Path

from rag.config.pipeline_config import ComponentConfig, PipelineConfigLoader
from rag.query.query_transform_chain import QueryTransformChain
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_chain(profile_name: str) -> tuple[object, QueryTransformChain]:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles" / profile_name
    )
    registry = build_default_component_registry()
    components = [
        registry.build(
            category="query_transformer",
            config=item,
            build_context={
                "query_llm_generator": None,
                "enable_query_expansion_llm": False,
                "query_expansion_generation_params": {},
            },
        )
        for item in profile.query_transformers
        if item.enabled
    ]
    return profile, QueryTransformChain(components)


def test_hybrid_profile_uses_identity_transformer() -> None:
    profile, chain = _build_chain("hybrid_v1.yaml")

    result = chain.transform("分析设备故障", strategy_label="hybrid")

    assert profile.query_transformers[0].name == "identity"
    assert result.retrieval_queries == ["分析设备故障"]
    assert result.rewritten_queries == []
    assert result.hyde_query is None
    assert result.metadata["transformers"][0]["name"] == "identity"


def test_rag_fusion_profile_generates_configured_multi_query_set() -> None:
    profile, chain = _build_chain("rag_fusion_v1.yaml")

    result = chain.transform("分析设备故障", strategy_label="hybrid")

    assert profile.query_transformers[0].name == "multi_query"
    assert len(result.rewritten_queries) == 3
    assert len(result.retrieval_queries) == 4
    assert result.retrieval_queries[0] == "分析设备故障"
    assert result.metadata["transformers"][0]["name"] == "multi_query"


def test_hyde_profile_generates_hypothetical_document() -> None:
    _, chain = _build_chain("hyde_v1.yaml")

    result = chain.transform("分析设备故障", strategy_label="hybrid")

    assert result.hyde_query
    assert len(result.retrieval_queries) == 2
    assert result.metadata["transformers"][0]["name"] == "hyde"


def test_combined_profile_executes_transformers_in_declared_order() -> None:
    profile, chain = _build_chain("rag_fusion_hyde_v1.yaml")

    result = chain.transform("分析设备故障", strategy_label="hybrid")

    assert [item.name for item in profile.query_transformers] == [
        "multi_query",
        "hyde",
    ]
    assert [item["name"] for item in result.metadata["transformers"]] == [
        "multi_query",
        "hyde",
    ]
    assert len(result.rewritten_queries) == 3
    assert result.hyde_query
    assert len(result.retrieval_queries) == 5


def test_unknown_query_transformer_fails_during_composition() -> None:
    registry = build_default_component_registry()

    try:
        registry.build(
            category="query_transformer",
            config=ComponentConfig(name="missing", version="v1"),
        )
    except ValueError as exc:
        assert "unknown RAG component" in str(exc)
    else:
        raise AssertionError("unknown query transformer must fail")


def test_retrieval_pipeline_no_longer_calls_strategy_driven_query_expander() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")

    assert "query_expander.expand" not in source
    assert "query_transform_chain.transform" in source

from rag.schema.candidate import CandidateSet


class _FakeRetriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, request):
        self.queries.append(request.query)
        return CandidateSet(
            query=request.query,
            source_name="fake",
            candidates=[
                {
                    "chunk_id": f"chunk-{len(self.queries)}",
                    "child_chunk_id": f"chunk-{len(self.queries)}",
                    "parent_chunk_id": f"parent-{len(self.queries)}",
                    "parent_text": request.query,
                    "text": request.query,
                    "score": 1.0,
                    "metadata": {},
                }
            ],
        )


class _FakeFusion:
    def fuse(self, candidate_sets):
        if not candidate_sets:
            return CandidateSet(query="", source_name="fake_fusion", candidates=[])
        candidates = []
        for item in candidate_sets:
            candidates.extend(item.candidates)
        return CandidateSet(
            query=candidate_sets[0].query,
            source_name="fake_fusion",
            candidates=candidates,
        )


class _FakeQueryFusion:
    def fuse(self, candidate_sets):
        candidates = []
        for item in candidate_sets:
            candidates.extend(item.candidates)
        return CandidateSet(
            query=candidate_sets[0].query if candidate_sets else "",
            source_name="fake_query_fusion",
            candidates=candidates,
        )


class _FakeCandidateEnricher:
    def enrich(self, candidate_set):
        return candidate_set.copy_with(source_name="fake_enriched")


class _FakeReranker:
    def rerank(self, *, query: str, results: list[dict]):
        del query
        return results[:5]

    def execution_metadata(self):
        return {"top_k": 5, "text_field": "parent_text"}


class _FakeAdaptiveRouter:
    @staticmethod
    def is_adaptive_strategy(strategy: str) -> bool:
        del strategy
        return False


class _FakeCragJudge:
    def evaluate_and_filter(self, **kwargs):
        raise AssertionError(f"CRAG should not run in this test: {kwargs}")


def test_retrieval_pipeline_uses_configured_chain_not_legacy_query_flags() -> None:
    from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline

    _, chain = _build_chain("rag_fusion_v1.yaml")
    retriever = _FakeRetriever()
    pipeline = ParentChildRetrievalPipeline(
        retrievers=[retriever],
        fusion=_FakeFusion(),
        query_fusion=_FakeQueryFusion(),
        candidate_enricher=_FakeCandidateEnricher(),
        reranker=_FakeReranker(),
        query_transform_chain=chain,
        adaptive_router=_FakeAdaptiveRouter(),
        crag_judge=_FakeCragJudge(),
    )

    result = pipeline.run(
        "分析设备故障",
        dense_top_k=10,
        keyword_top_k=10,
        candidate_top_k=10,
        rrf_k=60,
        rerank_top_k=5,
        filter_expr=None,
        keyword_doc_ids=None,
        retrieval_strategy="hybrid",
        num_rewrites=99,
        enable_hyde=True,
        enable_crag=False,
        enable_self_rag=False,
        crag_max_judge_chunks=8,
        crag_drop_irrelevant=True,
        extra_metadata=None,
    )

    assert len(retriever.queries) == 4
    assert len(result.query_expansion.rewritten_queries) == 3
    assert result.query_expansion.hyde_query is None
    assert result.query_expansion.metadata["legacy_request_overrides"] == {
        "num_rewrites": 99,
        "enable_hyde": True,
        "ignored_by_configured_chain": True,
    }
    assert result.query_expansion.metadata["legacy_retrieval_overrides"] == {
        "dense_top_k": 10,
        "keyword_top_k": 10,
        "candidate_top_k": 10,
        "rrf_k": 60,
        "ignored_by_configured_stack": True,
    }
    assert result.query_expansion.metadata["legacy_reranker_overrides"] == {
        "rerank_top_k": 5,
        "ignored_by_configured_reranker": True,
    }
