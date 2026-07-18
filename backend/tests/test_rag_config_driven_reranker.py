from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rag.config.pipeline_config import (
    ComponentConfig,
    OnlineRAGPipelineConfig,
    PipelineConfigLoader,
)
from rag.plugins.rerankers import (
    BGEParentCrossEncoderRerankerPlugin,
    NoOpParentRerankerPlugin,
)
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _FakeCrossEncoderBackend:
    model_name = "fake-bge-reranker"
    device = "cpu"
    batch_size = 4
    max_length = 256
    local_files_only = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def rerank(self, query, results, *, top_k=None, text_field="parent_text"):
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


class _FakeResourcePool:
    def __init__(self) -> None:
        self.backend = _FakeCrossEncoderBackend()
        self.calls: list[dict] = []

    def get_parent_reranker(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.backend


def _context() -> dict:
    return {"resource_pool": _FakeResourcePool()}


def _candidates() -> list[dict]:
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


def test_profiles_declare_configured_reranker() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/rag_fusion_v1.yaml"
    )

    assert profile.schema_version == "online_rag_pipeline_config_v5"
    assert profile.reranker.name == "bge_parent_cross_encoder"
    assert profile.reranker.version == "v1"
    assert profile.reranker.params == {
        "top_k": 5,
        "text_field": "parent_text",
    }


def test_registry_builds_bge_reranker_plugin() -> None:
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


def test_bge_plugin_applies_profile_top_k_and_text_field() -> None:
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


def test_bge_plugin_can_override_runtime_resource_fields() -> None:
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


def test_noop_reranker_is_explicit_plugin_and_preserves_parent_metadata() -> None:
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


def test_invalid_rerank_text_field_fails_during_composition() -> None:
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unsupported rerank text_field"):
        registry.build(
            category="reranker",
            config=ComponentConfig(
                name="noop_parent",
                params={"top_k": 5, "text_field": "made_up_field"},
            ),
        )


def test_pipeline_schema_requires_enabled_reranker() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    profile["reranker"]["enabled"] = False

    with pytest.raises(ValidationError, match="requires enabled reranker"):
        OnlineRAGPipelineConfig.model_validate(profile)


def test_v3_profile_is_rejected_after_quality_migration() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    profile["schema_version"] = "online_rag_pipeline_config_v4"

    with pytest.raises(ValidationError):
        OnlineRAGPipelineConfig.model_validate(profile)


def test_runtime_factory_builds_reranker_from_registry_only() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/runtime/parent_child_runtime_factory.py"
    ).read_text(encoding="utf-8")

    assert 'category="reranker"' in source
    assert "ParentChildReranker(" not in source
    assert "NoOpParentChildReranker(" not in source
    assert "if cfg.skip_rerank" not in source


def test_retrieval_pipeline_does_not_pass_legacy_rerank_controls() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")
    call = source.split("results = self.reranker.rerank(", 1)[1].split(")", 1)[0]

    assert "rerank_top_k" not in call
    assert "text_field" not in call
    assert "ignored_by_configured_reranker" in source
    assert 'expansion.metadata["configured_reranker"]' in source


def test_reranker_execution_metadata_exposes_effective_configuration() -> None:
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


class _StaticRetriever:
    source_name = "static"

    def retrieve(self, request):
        from rag.schema.candidate import CandidateSet

        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=_candidates(),
        )


class _PassFusion:
    def fuse(self, candidate_sets):
        return candidate_sets[0]


class _PassEnricher:
    def enrich(self, candidate_set):
        return candidate_set


class _NoAdaptiveRouter:
    @staticmethod
    def is_adaptive_strategy(strategy):
        del strategy
        return False


class _NoCragJudge:
    def evaluate_and_filter(self, **kwargs):
        raise AssertionError(f"CRAG should not run: {kwargs}")


def test_full_pipeline_ignores_legacy_rerank_top_k() -> None:
    from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
    from rag.query.query_transform_chain import QueryTransformChain

    registry = build_default_component_registry()
    identity = registry.build(
        category="query_transformer",
        config=ComponentConfig(name="identity"),
    )
    reranker = NoOpParentRerankerPlugin(top_k=2, text_field="parent_text")
    pipeline = ParentChildRetrievalPipeline(
        retrievers=[_StaticRetriever()],
        fusion=_PassFusion(),
        query_fusion=_PassFusion(),
        candidate_enricher=_PassEnricher(),
        reranker=reranker,
        query_transform_chain=QueryTransformChain([identity]),
        adaptive_router=_NoAdaptiveRouter(),
        crag_judge=_NoCragJudge(),
    )

    result = pipeline.run(
        "query",
        dense_top_k=999,
        keyword_top_k=999,
        candidate_top_k=999,
        rrf_k=999,
        rerank_top_k=99,
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

    assert len(result.results) == 2
    assert result.query_expansion.metadata["legacy_reranker_overrides"] == {
        "rerank_top_k": 99,
        "ignored_by_configured_reranker": True,
    }
    assert result.query_expansion.metadata["configured_reranker"]["top_k"] == 2
    assert result.query_expansion.metadata["configured_reranker"]["output_count"] == 2
