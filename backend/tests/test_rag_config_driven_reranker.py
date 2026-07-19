# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_FakeCrossEncoderBackend、_FakeResourcePool、_context、_candidates、test_profiles_declare_configured_reranker、test_registry_builds_bge_reranker_plugin、test_bge_plugin_applies_profile_top_k_and_text_field、test_bge_plugin_can_override_runtime_resource_fields、test_noop_reranker_is_explicit_plugin_and_preserves_parent_metadata、test_invalid_rerank_text_field_fails_during_composition等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rag.config.static_retrieval import (
    ComponentConfig,
    StaticRetrievalSpec,
    StaticRetrievalSpecLoader,
)
from rag.plugins.rerankers import (
    BGEParentCrossEncoderRerankerPlugin,
    NoOpParentRerankerPlugin,
)
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 阅读注释（类）：封装 fake cross encoder 后端实现，集中封装相关状态、依赖和行为。
class _FakeCrossEncoderBackend:
    """封装 fake cross encoder 后端实现，集中封装相关状态、依赖和行为。"""
    model_name = "fake-bge-reranker"
    device = "cpu"
    batch_size = 4
    max_length = 256
    local_files_only = True

    # 阅读注释（函数）：初始化 _FakeCrossEncoderBackend，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 _FakeCrossEncoderBackend，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self.calls: list[dict] = []

    # 阅读注释（函数）：对 _FakeCrossEncoderBackend 重新排序。
    def rerank(self, query, results, *, top_k=None, text_field="parent_text"):
        """对 _FakeCrossEncoderBackend 重新排序。

        参数:
            query: 当前检索或生成查询。
            results: 待处理的结果集合。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            text_field: 文本 field，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self.calls.append, len, deepcopy, items.sort, int, enumerate, float, item.get。
        """
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "text_field": text_field,
                "input_count": len(results),
            }
        )
        items = [deepcopy(item) for item in results]
        items.sort(key=lambda item: float(item.get("fake_score", 0.0)), reverse=True)
        items = items[: int(top_k)] if top_k is not None else items
        for rank, item in enumerate(items, 1):
            item["rank"] = rank
            item["rerank_score"] = float(item.get("fake_score", 0.0))
            metadata = dict(item.get("metadata") or {})
            metadata["rerank_text_field"] = text_field
            item["metadata"] = metadata
        return items


# 阅读注释（类）：封装 fake resource pool，集中封装相关状态、依赖和行为。
class _FakeResourcePool:
    """封装 fake resource pool，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 _FakeResourcePool，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 _FakeResourcePool，保存运行所需的依赖、配置或状态。

        返回:
            None

        阅读提示:
            主要直接调用：_FakeCrossEncoderBackend。
        """
        self.backend = _FakeCrossEncoderBackend()
        self.calls: list[dict] = []

    # 阅读注释（函数）：获取 父块 reranker。
    def get_parent_reranker(self, **kwargs):
        """获取 父块 reranker。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：self.calls.append, dict。
        """
        self.calls.append(dict(kwargs))
        return self.backend


# 阅读注释（函数）：处理 上下文 相关逻辑。
def _context() -> dict:
    """处理 上下文 相关逻辑。

    返回:
        dict

    阅读提示:
        主要直接调用：_FakeResourcePool。
    """
    return {"resource_pool": _FakeResourcePool()}


# 阅读注释（函数）：处理 candidates 相关逻辑。
def _candidates() -> list[dict]:
    """处理 candidates 相关逻辑。

    返回:
        list[dict]
    """
    return [
        {
            "chunk_id": "child-1",
            "child_chunk_id": "child-1",
            "parent_chunk_id": "parent-1",
            "parent_text": "parent one",
            "text": "parent one",
            "score": 0.5,
            "fake_score": 0.2,
            "metadata": {"matched_child_count": 2},
        },
        {
            "chunk_id": "child-2",
            "child_chunk_id": "child-2",
            "parent_chunk_id": "parent-2",
            "parent_text": "parent two",
            "text": "parent two",
            "score": 0.4,
            "fake_score": 0.9,
            "metadata": {"matched_child_count": 3},
        },
        {
            "chunk_id": "child-3",
            "child_chunk_id": "child-3",
            "parent_chunk_id": "parent-3",
            "parent_text": "parent three",
            "text": "parent three",
            "score": 0.3,
            "fake_score": 0.6,
            "metadata": {"matched_child_count": 1},
        },
    ]


# 阅读注释（函数）：处理 测试 profiles declare configured reranker 相关逻辑。
def test_static_spec_declares_configured_reranker() -> None:
    """处理 测试 profiles declare configured reranker 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：load, PipelineConfigLoader。
    """
    profile = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
    )

    assert profile.schema_version == "static_retrieval_spec_v1"
    assert profile.reranker.name == "bge_parent_cross_encoder"
    assert profile.reranker.version == "v1"
    assert profile.reranker.params == {
        "top_k": 5,
        "text_field": "parent_text",
    }


# 阅读注释（函数）：处理 测试 注册表 builds bge reranker 插件 相关逻辑。
def test_registry_builds_bge_reranker_plugin() -> None:
    """处理 测试 注册表 builds bge reranker 插件 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_component_registry, _context, registry.build, ComponentConfig, isinstance。
    """
    registry = build_default_component_registry()
    context = _context()

    reranker = registry.build(
        category="reranker",
        config=ComponentConfig(
            name="bge_parent_cross_encoder",
            params={"top_k": 2, "text_field": "parent_text"},
        ),
        build_context=context,
    )

    assert isinstance(reranker, BGEParentCrossEncoderRerankerPlugin)
    assert reranker.plugin_metadata.name == "bge_parent_cross_encoder"
    assert reranker.top_k == 2
    assert reranker.text_field == "parent_text"


# 阅读注释（函数）：处理 测试 bge 插件 applies 策略配置 top k and 文本 field 相关逻辑。
def test_bge_plugin_applies_profile_top_k_and_text_field() -> None:
    """处理 测试 bge 插件 applies 策略配置 top k and 文本 field 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_context, BGEParentCrossEncoderRerankerPlugin, reranker.rerank, _candidates。
    """
    context = _context()
    reranker = BGEParentCrossEncoderRerankerPlugin(
        build_context=context,
        top_k=2,
        text_field="parent_text",
    )

    output = reranker.rerank(query="query", results=_candidates())

    assert [item["parent_chunk_id"] for item in output] == [
        "parent-2",
        "parent-3",
    ]
    assert [item["rank"] for item in output] == [1, 2]
    assert [item["metadata"]["matched_child_count"] for item in output] == [3, 1]
    assert context["resource_pool"].backend.calls == [
        {
            "query": "query",
            "top_k": 2,
            "text_field": "parent_text",
            "input_count": 3,
        }
    ]


# 阅读注释（函数）：处理 测试 bge 插件 can override 运行时 resource fields 相关逻辑。
def test_bge_plugin_can_override_runtime_resource_fields() -> None:
    """处理 测试 bge 插件 can override 运行时 resource fields 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_context, BGEParentCrossEncoderRerankerPlugin。
    """
    context = _context()
    BGEParentCrossEncoderRerankerPlugin(
        build_context=context,
        model_name="override-model",
        device="cpu",
        batch_size=8,
        max_length=384,
        local_files_only=False,
    )

    assert context["resource_pool"].calls == [
        {
            "model_name": "override-model",
            "device": "cpu",
            "batch_size": 8,
            "max_length": 384,
            "local_files_only": False,
        }
    ]


# 阅读注释（函数）：处理 测试 noop reranker is explicit 插件 and preserves 父块 元数据 相关逻辑。
def test_noop_reranker_is_explicit_plugin_and_preserves_parent_metadata() -> None:
    """处理 测试 noop reranker is explicit 插件 and preserves 父块 元数据 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_component_registry, registry.build, ComponentConfig, reranker.rerank, _candidates, isinstance。
    """
    registry = build_default_component_registry()
    reranker = registry.build(
        category="reranker",
        config=ComponentConfig(
            name="noop_parent",
            params={"top_k": 2, "text_field": "parent_text"},
        ),
    )

    output = reranker.rerank(query="query", results=_candidates())

    assert isinstance(reranker, NoOpParentRerankerPlugin)
    assert [item["parent_chunk_id"] for item in output] == [
        "parent-1",
        "parent-2",
    ]
    assert [item["rank"] for item in output] == [1, 2]
    assert output[0]["metadata"]["matched_child_count"] == 2
    assert output[0]["metadata"]["reranker"] == "noop"


# 阅读注释（函数）：处理 测试 invalid 重排 文本 field fails during composition 相关逻辑。
def test_invalid_rerank_text_field_fails_during_composition() -> None:
    """处理 测试 invalid 重排 文本 field fails during composition 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_component_registry, pytest.raises, registry.build, ComponentConfig。
    """
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unsupported rerank text_field"):
        registry.build(
            category="reranker",
            config=ComponentConfig(
                name="noop_parent",
                params={"top_k": 5, "text_field": "made_up_field"},
            ),
        )


# 阅读注释（函数）：处理 测试 pipeline Schema requires enabled reranker 相关逻辑。
def test_pipeline_schema_requires_enabled_reranker() -> None:
    """处理 测试 pipeline Schema requires enabled reranker 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, load, PipelineConfigLoader, pytest.raises, OnlineRAGPipelineConfig.model_validate。
    """
    profile = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
    ).model_dump(mode="json")
    profile["reranker"]["enabled"] = False

    with pytest.raises(ValidationError, match="requires enabled reranker"):
        StaticRetrievalSpec.model_validate(profile)


# 阅读注释（函数）：处理 测试 v3 策略配置 is rejected after 质量 migration 相关逻辑。
def test_old_profile_schema_is_rejected_by_static_spec() -> None:
    """处理 测试 v3 策略配置 is rejected after 质量 migration 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, load, PipelineConfigLoader, pytest.raises, OnlineRAGPipelineConfig.model_validate。
    """
    profile = StaticRetrievalSpecLoader().load(
        PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
    ).model_dump(mode="json")
    profile["schema_version"] = "online_retrieval_pipeline_config_v2"

    with pytest.raises(ValidationError):
        StaticRetrievalSpec.model_validate(profile)


# 阅读注释（函数）：处理 测试 运行时 工厂 builds reranker from 注册表 only 相关逻辑。
def test_runtime_factory_builds_reranker_from_registry_only() -> None:
    """处理 测试 运行时 工厂 builds reranker from 注册表 only 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：read_text。
    """
    source = (
        PROJECT_ROOT / "backend/rag/runtime/parent_child_runtime_factory.py"
    ).read_text(encoding="utf-8")

    assert 'category="reranker"' in source
    assert "ParentChildReranker(" not in source
    assert "NoOpParentChildReranker(" not in source
    assert "if cfg.skip_rerank" not in source


# 阅读注释（函数）：处理 测试 检索 pipeline does not pass legacy 重排 controls 相关逻辑。
def test_retrieval_pipeline_does_not_pass_legacy_rerank_controls() -> None:
    """处理 测试 检索 pipeline does not pass legacy 重排 controls 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：read_text, split, source.split。
    """
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")
    call = source.split("reranked = self.reranker.rerank(", 1)[1].split(")", 1)[0]

    assert "rerank_top_k" not in call
    assert "text_field" not in call
    assert "legacy_reranker_overrides" not in source
    assert 'expansion.metadata["configured_reranker"]' in source


# 阅读注释（函数）：处理 测试 reranker execution 元数据 exposes effective configuration 相关逻辑。
def test_reranker_execution_metadata_exposes_effective_configuration() -> None:
    """处理 测试 reranker execution 元数据 exposes effective configuration 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_context, BGEParentCrossEncoderRerankerPlugin, reranker.execution_metadata。
    """
    context = _context()
    reranker = BGEParentCrossEncoderRerankerPlugin(
        build_context=context,
        top_k=2,
        text_field="parent_text",
    )

    assert reranker.execution_metadata() == {
        "top_k": 2,
        "text_field": "parent_text",
        "model_name": "fake-bge-reranker",
        "device": "cpu",
        "batch_size": 4,
        "max_length": 256,
        "local_files_only": True,
    }


# 阅读注释（类）：封装 static retriever，集中封装相关状态、依赖和行为。
class _StaticRetriever:
    """封装 static retriever，集中封装相关状态、依赖和行为。"""
    source_name = "static"

    # 阅读注释（函数）：检索 _StaticRetriever。
    def retrieve(self, request):
        """检索 _StaticRetriever。

        参数:
            request: 当前请求对象。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：CandidateSet, _candidates。
        """
        from rag.schema.candidate import CandidateSet

        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=_candidates(),
        )


# 阅读注释（类）：封装 pass 融合，集中封装相关状态、依赖和行为。
class _PassFusion:
    """封装 pass 融合，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：融合 _PassFusion。
    def fuse(self, candidate_sets):
        """融合 _PassFusion。

        参数:
            candidate_sets: candidate sets，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return candidate_sets[0]


# 阅读注释（类）：封装 pass enricher，集中封装相关状态、依赖和行为。
class _PassEnricher:
    """封装 pass enricher，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：补充并丰富 _PassEnricher。
    def enrich(self, candidate_set):
        """补充并丰富 _PassEnricher。

        参数:
            candidate_set: candidate set，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        return candidate_set


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


# 阅读注释（函数）：处理 测试 full pipeline ignores legacy 重排 top k 相关逻辑。
