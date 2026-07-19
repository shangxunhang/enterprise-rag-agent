# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_FakeDenseBackend、_FakeKeywordBackend、_FakeParentStore、_FakeResourcePool、_build_context、test_profiles_declare_complete_retrieval_stack、test_registry_builds_retriever_fusion_and_enricher_plugins、test_source_rrf_and_parent_enrichment_preserve_evidence_semantics、test_parent_query_fusion_fuses_parent_level_results、test_configured_retriever_top_k_ignores_legacy_runtime_top_k等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

from pathlib import Path

import pytest

from rag.config.static_retrieval import (
    ComponentConfig,
    StaticRetrievalSpec,
    StaticRetrievalSpecLoader,
)
from rag.planning.retrieval_planner import AdaptiveRetrievalPlanner
from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.plugins.fusions import ChildRRFFusionPlugin, ParentRRFFusionPlugin
from rag.plugins.retrievers import (
    BM25ChildRetrieverPlugin,
    MilvusDenseChildRetrieverPlugin,
)
from rag.registry.default_registrations import build_default_component_registry
from rag.schema.candidate import CandidateSet, RetrievalRequest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 阅读注释（类）：封装 fake dense 后端实现，集中封装相关状态、依赖和行为。
class _FakeDenseBackend:
    """封装 fake dense 后端实现，集中封装相关状态、依赖和行为。"""
    embedding_model = "fake-embedding"
    embedding_version = "fake-v1"
    collection_name = "fake-child-index"
    vector_db = "fake-milvus"

    # 阅读注释（函数）：搜索 _FakeDenseBackend。
    def search(self, *, query: str, top_k: int, filter_expr: str | None = None):
        """搜索 _FakeDenseBackend。

        参数:
            query: 当前检索或生成查询。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            filter_expr: filter expr，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：enumerate。
        """
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


# 阅读注释（类）：封装 fake keyword 后端实现，集中封装相关状态、依赖和行为。
class _FakeKeywordBackend:
    """封装 fake keyword 后端实现，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：搜索 _FakeKeywordBackend。
    def search(self, *, query: str, top_k: int, doc_id=None, doc_ids=None):
        """搜索 _FakeKeywordBackend。

        参数:
            query: 当前检索或生成查询。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            doc_id: doc 标识，具体约束请结合类型标注和调用方确认。
            doc_ids: doc 标识集合，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：enumerate。
        """
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


# 阅读注释（类）：封装 fake 父块 store，集中封装相关状态、依赖和行为。
class _FakeParentStore:
    """封装 fake 父块 store，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：获取 _FakeParentStore。
    def get(self, parent_id: str):
        """获取 _FakeParentStore。

        参数:
            parent_id: 父块 标识，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：values.get。
        """
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


# 阅读注释（类）：封装 fake resource pool，集中封装相关状态、依赖和行为。
class _FakeResourcePool:
    """封装 fake resource pool，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 _FakeResourcePool，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 _FakeResourcePool，保存运行所需的依赖、配置或状态。

        返回:
            None

        阅读提示:
            主要直接调用：_FakeDenseBackend, _FakeKeywordBackend, _FakeParentStore。
        """
        self.dense = _FakeDenseBackend()
        self.keyword = _FakeKeywordBackend()
        self.parents = _FakeParentStore()

    # 阅读注释（函数）：获取 dense retriever。
    def get_dense_retriever(self):
        """获取 dense retriever。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return self.dense

    # 阅读注释（函数）：获取 keyword retriever。
    def get_keyword_retriever(self):
        """获取 keyword retriever。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return self.keyword

    # 阅读注释（函数）：获取 父块 store。
    def get_parent_store(self):
        """获取 父块 store。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return self.parents


# 阅读注释（函数）：构建 上下文。
def _build_context() -> dict:
    """构建 上下文。

    返回:
        dict

    阅读提示:
        主要直接调用：_FakeResourcePool。
    """
    return {"resource_pool": _FakeResourcePool()}


# 阅读注释（函数）：处理 测试 profiles declare complete 检索 stack 相关逻辑。
def test_static_spec_declares_complete_retrieval_stack() -> None:
    """处理 测试 profiles declare complete 检索 stack 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：load, PipelineConfigLoader。
    """
    profile = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
    )

    assert [item.name for item in profile.retrievers] == [
        "milvus_dense_child",
        "bm25_child",
    ]
    assert profile.source_fusion.name == "rrf_child"
    assert profile.query_fusion.name == "rrf_parent"
    assert profile.candidate_enricher.name == "parent_child"


# 阅读注释（函数）：处理 测试 注册表 builds retriever 融合 and enricher plugins 相关逻辑。
def test_registry_builds_retriever_fusion_and_enricher_plugins() -> None:
    """处理 测试 注册表 builds retriever 融合 and enricher plugins 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_component_registry, _build_context, registry.build, ComponentConfig, isinstance。
    """
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
        category="source_fusion",
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


# 阅读注释（函数）：处理 测试 source rrf and 父块 enrichment preserve 证据 semantics 相关逻辑。
def test_source_rrf_and_parent_enrichment_preserve_evidence_semantics() -> None:
    """处理 测试 source rrf and 父块 enrichment preserve 证据 semantics 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_build_context, MilvusDenseChildRetrieverPlugin, BM25ChildRetrieverPlugin, ChildRRFFusionPlugin, ParentChildCandidateEnricher, RetrievalRequest, dense.retrieve, keyword.retrieve。
    """
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


# 阅读注释（函数）：处理 测试 父块 查询 融合 fuses 父块 level 结果集合 相关逻辑。
def test_parent_query_fusion_fuses_parent_level_results() -> None:
    """处理 测试 父块 查询 融合 fuses 父块 level 结果集合 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ParentRRFFusionPlugin, CandidateSet, fusion.fuse, set。
    """
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


# 阅读注释（函数）：处理 测试 configured retriever top k ignores legacy 运行时 top k 相关逻辑。
def test_configured_retriever_top_k_ignores_legacy_runtime_top_k() -> None:
    """处理 测试 configured retriever top k ignores legacy 运行时 top k 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_build_context, MilvusDenseChildRetrieverPlugin, dense.retrieve, RetrievalRequest, len。
    """
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


# 阅读注释（函数）：处理 测试 unknown 检索 component fails during composition 相关逻辑。
def test_unknown_retrieval_component_fails_during_composition() -> None:
    """处理 测试 unknown 检索 component fails during composition 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_component_registry, pytest.raises, registry.build, ComponentConfig, _build_context。
    """
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unknown RAG component"):
        registry.build(
            category="retriever",
            config=ComponentConfig(name="missing", version="v1"),
            build_context=_build_context(),
        )


# 阅读注释（函数）：处理 测试 main pipeline does not construct hybrid retriever or call rrf directly 相关逻辑。
def test_main_pipeline_does_not_construct_hybrid_retriever_or_call_rrf_directly() -> None:
    """处理 测试 main pipeline does not construct hybrid retriever or call rrf directly 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：read_text。
    """
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
    assert "registry.build" in runtime


# 阅读注释（函数）：处理 测试 new configured stack matches legacy hybrid 父块 子块 semantics 相关逻辑。
def test_new_configured_stack_matches_legacy_hybrid_parent_child_semantics() -> None:
    """处理 测试 new configured stack matches legacy hybrid 父块 子块 semantics 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：sys.modules.setdefault, SimpleNamespace, _FakeResourcePool, HybridParentChildRetriever, legacy.retrieve, MilvusDenseChildRetrieverPlugin, BM25ChildRetrieverPlugin, enrich。
    """
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


# 阅读注释（类）：封装 no op reranker，集中封装相关状态、依赖和行为。
class _NoOpReranker:
    """封装 no op reranker，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：对 _NoOpReranker 重新排序。
    def rerank(self, *, query, results):
        """对 _NoOpReranker 重新排序。

        参数:
            query: 当前检索或生成查询。
            results: 待处理的结果集合。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：list。
        """
        del query
        return list(results)[:5]

    # 阅读注释（函数）：处理 execution 元数据 相关逻辑。
    def execution_metadata(self):
        """处理 execution 元数据 相关逻辑。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return {"top_k": 5, "text_field": "parent_text"}


# 阅读注释（类）：封装 no Adaptive 路由器，集中封装相关状态、依赖和行为。
class _NoAdaptiveRouter:
    """封装 no Adaptive 路由器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：判断 Adaptive strategy。
    @staticmethod
    def is_adaptive_strategy(strategy):
        """判断 Adaptive strategy。

        参数:
            strategy: strategy，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        del strategy
        return False


# 阅读注释（函数）：处理 测试 full configured 检索 stack runs multi 查询 without legacy hybrid 相关逻辑。
def test_full_configured_retrieval_stack_runs_multi_query_without_legacy_hybrid() -> None:
    """处理 测试 full configured 检索 stack runs multi 查询 without legacy hybrid 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：load, PipelineConfigLoader, build_default_component_registry, _build_context, registry.build, ParentChildRetrievalPipeline, _NoOpReranker, QueryTransformChain。
    """
    from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
    from rag.query.query_transform_selector import QueryTransformSelector

    profile = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
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
        source_fusion=registry.build(
            category="source_fusion",
            config=profile.source_fusion,
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
        query_transform_selector=QueryTransformSelector(transformers),
        retrieval_planner=AdaptiveRetrievalPlanner(correction_budget=0),
        evidence_assessor=registry.build(
            category="evidence_assessor",
            config=ComponentConfig(name="noop_evidence"),
        ),
        corrective_retrieval_gate=registry.build(
            category="corrective_retrieval_gate",
            config=profile.corrective_retrieval_gate,
        ),
        corrective_query_planner=registry.build(
            category="corrective_query_planner",
            config=ComponentConfig(name="section_gap", params={"use_llm": False}),
        ),
    )

    result = pipeline.run(
        "test query",
        filter_expr=None,
        keyword_doc_ids=None,
        extra_metadata=None,
    )

    assert len(result.query_expansion.retrieval_queries) == 4
    assert "legacy_retrieval_overrides" not in result.query_expansion.metadata
    stack = result.query_expansion.metadata["configured_retrieval_stack"]
    assert [item["name"] for item in stack["retrievers"]] == [
        "milvus_dense_child",
        "bm25_child",
    ]
    assert stack["source_fusion"]["name"] == "rrf_child"
    assert stack["query_fusion"]["name"] == "rrf_parent"
    assert stack["candidate_enricher"]["name"] == "parent_child"
    assert stack["query_fusion_execution"]["applied"] is True
    assert result.p2_results
    assert all(item.get("parent_chunk_id") for item in result.p2_results)


# 阅读注释（函数）：处理 测试 old pipeline Schema 版本 is rejected 相关逻辑。
def test_old_pipeline_schema_version_is_rejected() -> None:
    """处理 测试 old pipeline Schema 版本 is rejected 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, load, PipelineConfigLoader, pytest.raises, OnlineRAGPipelineConfig.model_validate。
    """
    from pydantic import ValidationError
    payload = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
    ).model_dump(mode="json")
    payload["schema_version"] = "online_rag_pipeline_config_v1"

    with pytest.raises(ValidationError):
        StaticRetrievalSpec.model_validate(payload)


# 阅读注释（函数）：处理 测试 duplicate retriever 插件 is rejected by 配置 Schema 相关逻辑。
def test_duplicate_retriever_plugin_is_rejected_by_config_schema() -> None:
    """处理 测试 duplicate retriever 插件 is rejected by 配置 Schema 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, load, PipelineConfigLoader, append, dict, pytest.raises, OnlineRAGPipelineConfig.model_validate。
    """
    from pydantic import ValidationError
    payload = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
    ).model_dump(mode="json")
    payload["retrievers"].append(dict(payload["retrievers"][0]))

    with pytest.raises(ValidationError, match="duplicate retriever"):
        StaticRetrievalSpec.model_validate(payload)
