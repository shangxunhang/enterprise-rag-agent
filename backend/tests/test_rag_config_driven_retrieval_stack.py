from __future__ import annotations

from pathlib import Path

import pytest

from rag.config.pipeline_config import ComponentConfig, PipelineConfigLoader
from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.plugins.fusions import ChildRRFFusionPlugin, ParentRRFFusionPlugin
from rag.plugins.retrievers import (
    BM25ChildRetrieverPlugin,
    MilvusDenseChildRetrieverPlugin,
)
from rag.registry.default_registrations import build_default_component_registry
from rag.schema.candidate import CandidateSet, RetrievalRequest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _FakeDenseBackend:
    embedding_model = "fake-embedding"
    embedding_version = "fake-v1"
    collection_name = "fake-child-index"
    vector_db = "fake-milvus"

    def search(self, *, query: str, top_k: int, filter_expr: str | None = None):
        del query, filter_expr
        values = [
            ("child-1", "parent-1", 0.9),
            ("child-2", "parent-1", 0.8),
            ("child-3", "parent-2", 0.7),
        ]
        return [
            {
                "rank": rank,
                "score": score,
                "retrieval_source": "dense",
                "chunk_id": child_id,
                "child_chunk_id": child_id,
                "parent_chunk_id": parent_id,
                "doc_id": "doc-1",
                "child_chunk": {
                    "chunk_id": child_id,
                    "child_chunk_id": child_id,
                    "parent_chunk_id": parent_id,
                    "doc_id": "doc-1",
                    "text": f"dense evidence {child_id}",
                    "title": "title",
                    "source_unit_ids": [f"unit-{child_id}"],
                },
            }
            for rank, (child_id, parent_id, score) in enumerate(values[:top_k], 1)
        ]


class _FakeKeywordBackend:
    def search(self, *, query: str, top_k: int, doc_id=None, doc_ids=None):
        del query, doc_id, doc_ids
        values = [
            ("child-2", "parent-1", 12.0),
            ("child-3", "parent-2", 10.0),
        ]
        return [
            {
                "rank": rank,
                "score": score,
                "retrieval_source": "keyword",
                "chunk_id": child_id,
                "child_chunk_id": child_id,
                "parent_chunk_id": parent_id,
                "doc_id": "doc-1",
                "child_chunk": {
                    "chunk_id": child_id,
                    "child_chunk_id": child_id,
                    "parent_chunk_id": parent_id,
                    "doc_id": "doc-1",
                    "text": f"keyword evidence {child_id}",
                    "title": "title",
                    "source_unit_ids": [f"unit-{child_id}"],
                },
            }
            for rank, (child_id, parent_id, score) in enumerate(values[:top_k], 1)
        ]


class _FakeParentStore:
    def get(self, parent_id: str):
        values = {
            "parent-1": {
                "parent_chunk_id": "parent-1",
                "doc_id": "doc-1",
                "text": "parent context one",
                "title": "parent one",
            },
            "parent-2": {
                "parent_chunk_id": "parent-2",
                "doc_id": "doc-1",
                "text": "parent context two",
                "title": "parent two",
            },
        }
        return values.get(parent_id)


class _FakeResourcePool:
    def __init__(self) -> None:
        self.dense = _FakeDenseBackend()
        self.keyword = _FakeKeywordBackend()
        self.parents = _FakeParentStore()

    def get_dense_retriever(self):
        return self.dense

    def get_keyword_retriever(self):
        return self.keyword

    def get_parent_store(self):
        return self.parents


def _build_context() -> dict:
    return {"resource_pool": _FakeResourcePool()}


def test_profiles_declare_complete_retrieval_stack() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    )

    assert [item.name for item in profile.retrievers] == [
        "milvus_dense_child",
        "bm25_child",
    ]
    assert profile.fusion.name == "rrf_child"
    assert profile.query_fusion.name == "rrf_parent"
    assert profile.candidate_enricher.name == "parent_child"


def test_registry_builds_retriever_fusion_and_enricher_plugins() -> None:
    registry = build_default_component_registry()
    context = _build_context()

    dense = registry.build(
        category="retriever",
        config=ComponentConfig(name="milvus_dense_child", params={"top_k": 3}),
        build_context=context,
    )
    keyword = registry.build(
        category="retriever",
        config=ComponentConfig(name="bm25_child", params={"top_k": 2}),
        build_context=context,
    )
    fusion = registry.build(
        category="fusion",
        config=ComponentConfig(name="rrf_child", params={"rrf_k": 60}),
        build_context=context,
    )
    query_fusion = registry.build(
        category="query_fusion",
        config=ComponentConfig(
            name="rrf_parent", params={"rrf_k": 60, "top_k": 10}
        ),
        build_context=context,
    )
    enricher = registry.build(
        category="candidate_enricher",
        config=ComponentConfig(
            name="parent_child",
            params={"top_k": 10, "dedup_parent": True},
        ),
        build_context=context,
    )

    assert isinstance(dense, MilvusDenseChildRetrieverPlugin)
    assert isinstance(keyword, BM25ChildRetrieverPlugin)
    assert isinstance(fusion, ChildRRFFusionPlugin)
    assert isinstance(query_fusion, ParentRRFFusionPlugin)
    assert isinstance(enricher, ParentChildCandidateEnricher)
    assert dense.plugin_metadata.name == "milvus_dense_child"
    assert keyword.plugin_metadata.name == "bm25_child"


def test_source_rrf_and_parent_enrichment_preserve_evidence_semantics() -> None:
    context = _build_context()
    dense = MilvusDenseChildRetrieverPlugin(build_context=context, top_k=3)
    keyword = BM25ChildRetrieverPlugin(build_context=context, top_k=2)
    fusion = ChildRRFFusionPlugin(rrf_k=60)
    enricher = ParentChildCandidateEnricher(
        build_context=context,
        top_k=10,
        dedup_parent=True,
    )

    request = RetrievalRequest(query="test query")
    dense_set = dense.retrieve(request)
    keyword_set = keyword.retrieve(request)
    fused = fusion.fuse([dense_set, keyword_set])
    enriched = enricher.enrich(fused)

    assert len(fused.candidates) == 3
    assert len(enriched.candidates) == 2

    first = enriched.candidates[0]
    assert first["parent_chunk_id"] == "parent-1"
    assert first["child_chunk_id"] in {"child-1", "child-2"}
    assert first["parent_text"] == "parent context one"
    assert first["text"] == "parent context one"
    assert first["metadata"]["parent_found"] is True
    assert first["metadata"]["dedup_parent"] is True
    assert first["metadata"]["matched_child_count"] == 2
    assert set(first["metadata"]["matched_child_chunk_ids"]) == {
        "child-1",
        "child-2",
    }
    matched = first["metadata"]["matched_child_chunks"]
    assert {item["child_chunk_id"] for item in matched} == {
        "child-1",
        "child-2",
    }
    assert first["metadata"]["source_ranks"]
    assert first["metadata"]["source_scores"]
    assert first["metadata"]["rrf_contributions"]
    assert first["embedding_model"] == "fake-embedding"
    assert first["index_name"] == "fake-child-index"


def test_parent_query_fusion_fuses_parent_level_results() -> None:
    fusion = ParentRRFFusionPlugin(rrf_k=60, top_k=3)
    first = CandidateSet(
        query="original",
        source_name="q1",
        candidates=[
            {
                "rank": 1,
                "parent_chunk_id": "parent-1",
                "chunk_id": "child-1",
                "score": 0.9,
                "metadata": {},
            },
            {
                "rank": 2,
                "parent_chunk_id": "parent-2",
                "chunk_id": "child-2",
                "score": 0.8,
                "metadata": {},
            },
        ],
    )
    second = CandidateSet(
        query="rewrite",
        source_name="q2",
        candidates=[
            {
                "rank": 1,
                "parent_chunk_id": "parent-2",
                "chunk_id": "child-2",
                "score": 0.7,
                "metadata": {},
            }
        ],
    )

    result = fusion.fuse([first, second])

    assert result.candidates[0]["parent_chunk_id"] == "parent-2"
    metadata = result.candidates[0]["metadata"]
    assert set(metadata["query_fusion_queries"]) == {"q1", "q2"}
    assert metadata["query_fusion_stage"] == "rag_fusion_multi_query_rrf"


def test_configured_retriever_top_k_ignores_legacy_runtime_top_k() -> None:
    context = _build_context()
    dense = MilvusDenseChildRetrieverPlugin(build_context=context, top_k=2)

    result = dense.retrieve(
        RetrievalRequest(
            query="test query",
            metadata={"legacy_dense_top_k": 999},
        )
    )

    assert len(result.candidates) == 2
    assert result.metadata["top_k"] == 2


def test_unknown_retrieval_component_fails_during_composition() -> None:
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unknown RAG component"):
        registry.build(
            category="retriever",
            config=ComponentConfig(name="missing", version="v1"),
            build_context=_build_context(),
        )


def test_main_pipeline_does_not_construct_hybrid_retriever_or_call_rrf_directly() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")
    runtime = (
        PROJECT_ROOT / "backend/rag/runtime/parent_child_runtime_factory.py"
    ).read_text(encoding="utf-8")

    assert "HybridParentChildRetriever" not in source
    assert "HybridParentChildRetriever" not in runtime
    assert "rrf_fuse(" not in source
    assert "MultiQueryFusion(" not in source
    assert "component_registry.build" in runtime


def test_new_configured_stack_matches_legacy_hybrid_parent_child_semantics() -> None:
    import sys
    from types import SimpleNamespace

    sys.modules.setdefault("pymilvus", SimpleNamespace(MilvusClient=object))
    from rag.retriever.hybrid_parent_child_retriever import (
        HybridParentChildRetriever,
    )

    pool = _FakeResourcePool()
    legacy = HybridParentChildRetriever(
        dense_retriever=pool.dense,
        keyword_retriever=pool.keyword,
        parent_store=pool.parents,
        rrf_k=60,
        dedup_parent=True,
    )
    legacy_results = legacy.retrieve(
        "test query",
        dense_top_k=3,
        keyword_top_k=2,
        final_top_k=5,
    )

    context = {"resource_pool": pool}
    dense = MilvusDenseChildRetrieverPlugin(build_context=context, top_k=3)
    keyword = BM25ChildRetrieverPlugin(build_context=context, top_k=2)
    configured_results = ParentChildCandidateEnricher(
        build_context=context,
        top_k=5,
        dedup_parent=True,
    ).enrich(
        ChildRRFFusionPlugin(rrf_k=60).fuse(
            [
                dense.retrieve(RetrievalRequest(query="test query")),
                keyword.retrieve(RetrievalRequest(query="test query")),
            ]
        )
    ).candidates

    assert [item["parent_chunk_id"] for item in configured_results] == [
        item["parent_chunk_id"] for item in legacy_results
    ]
    assert [item["child_chunk_id"] for item in configured_results] == [
        item["child_chunk_id"] for item in legacy_results
    ]
    assert [item["text"] for item in configured_results] == [
        item["text"] for item in legacy_results
    ]
    for configured, old in zip(configured_results, legacy_results, strict=True):
        for key in (
            "retrieval_sources",
            "source_ranks",
            "source_scores",
            "rrf_contributions",
            "matched_child_chunk_ids",
            "matched_child_count",
            "dense_hits",
            "keyword_hits",
        ):
            assert configured["metadata"][key] == old["metadata"][key]


class _NoOpReranker:
    def rerank(self, *, query, results):
        del query
        return list(results)[:5]

    def execution_metadata(self):
        return {"top_k": 5, "text_field": "parent_text"}


class _NoAdaptiveRouter:
    @staticmethod
    def is_adaptive_strategy(strategy):
        del strategy
        return False


class _NoCragJudge:
    def evaluate_and_filter(self, **kwargs):
        raise AssertionError(f"CRAG should not run: {kwargs}")


def test_full_configured_retrieval_stack_runs_multi_query_without_legacy_hybrid() -> None:
    from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
    from rag.query.query_transform_chain import QueryTransformChain

    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/rag_fusion_v1.yaml"
    )
    registry = build_default_component_registry()
    context = _build_context()
    transformers = [
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
    retrievers = [
        registry.build(
            category="retriever",
            config=item,
            build_context=context,
        )
        for item in profile.retrievers
        if item.enabled
    ]
    pipeline = ParentChildRetrievalPipeline(
        retrievers=retrievers,
        fusion=registry.build(
            category="fusion",
            config=profile.fusion,
            build_context=context,
        ),
        query_fusion=registry.build(
            category="query_fusion",
            config=profile.query_fusion,
            build_context=context,
        ),
        candidate_enricher=registry.build(
            category="candidate_enricher",
            config=profile.candidate_enricher,
            build_context=context,
        ),
        reranker=_NoOpReranker(),
        query_transform_chain=QueryTransformChain(transformers),
        adaptive_router=_NoAdaptiveRouter(),
        crag_judge=_NoCragJudge(),
    )

    result = pipeline.run(
        "test query",
        dense_top_k=999,
        keyword_top_k=999,
        candidate_top_k=999,
        rrf_k=999,
        rerank_top_k=5,
        filter_expr=None,
        keyword_doc_ids=None,
        retrieval_strategy="hybrid",
        num_rewrites=999,
        enable_hyde=True,
        enable_crag=False,
        enable_self_rag=False,
        crag_max_judge_chunks=8,
        crag_drop_irrelevant=True,
        extra_metadata=None,
    )

    assert len(result.query_expansion.retrieval_queries) == 4
    assert result.query_expansion.metadata["legacy_retrieval_overrides"][
        "ignored_by_configured_stack"
    ] is True
    stack = result.query_expansion.metadata["configured_retrieval_stack"]
    assert [item["name"] for item in stack["retrievers"]] == [
        "milvus_dense_child",
        "bm25_child",
    ]
    assert stack["fusion"]["name"] == "rrf_child"
    assert stack["query_fusion"]["name"] == "rrf_parent"
    assert stack["candidate_enricher"]["name"] == "parent_child"
    assert stack["query_fusion_execution"]["applied"] is True
    assert result.p2_results
    assert all(item.get("parent_chunk_id") for item in result.p2_results)


def test_old_pipeline_schema_version_is_rejected() -> None:
    from pydantic import ValidationError
    from rag.config.pipeline_config import OnlineRAGPipelineConfig

    payload = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    payload["schema_version"] = "online_rag_pipeline_config_v1"

    with pytest.raises(ValidationError):
        OnlineRAGPipelineConfig.model_validate(payload)


def test_duplicate_retriever_plugin_is_rejected_by_config_schema() -> None:
    from pydantic import ValidationError
    from rag.config.pipeline_config import OnlineRAGPipelineConfig

    payload = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    payload["retrievers"].append(dict(payload["retrievers"][0]))

    with pytest.raises(ValidationError, match="duplicate retriever"):
        OnlineRAGPipelineConfig.model_validate(payload)
