from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from rag.application.parent_child_generation import ParentChildGenerationPipeline
from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.config.pipeline_config import (
    ComponentConfig,
    OnlineRAGPipelineConfig,
    PipelineConfigLoader,
)
from rag.plugins.evidence_graders import (
    CRAGLiteEvidenceGraderPlugin,
    NoOpEvidenceGraderPlugin,
)
from rag.plugins.generation_checkers import (
    NoOpGenerationCheckerPlugin,
    SelfRAGLiteGenerationCheckerPlugin,
)
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


def _results() -> list[dict]:
    return [
        {
            "chunk_id": "relevant-child",
            "child_chunk_id": "relevant-child",
            "parent_chunk_id": "relevant-parent",
            "parent_text": "enterprise rag architecture retrieves documents for generation",
            "text": "enterprise rag architecture retrieves documents for generation",
            "score": 0.9,
            "rank": 1,
            "metadata": {"matched_child_count": 2},
        },
        {
            "chunk_id": "irrelevant-child",
            "child_chunk_id": "irrelevant-child",
            "parent_chunk_id": "irrelevant-parent",
            "parent_text": "tomato soup cooking recipe and kitchen utensils",
            "text": "tomato soup cooking recipe and kitchen utensils",
            "score": 0.0,
            "rank": 2,
            "metadata": {"matched_child_count": 1},
        },
    ]


def test_profiles_explicitly_declare_quality_plugins() -> None:
    hybrid = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/rag_fusion_v1.yaml"
    )
    combined = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/c_rag_self_rag_v1.yaml"
    )

    assert hybrid.schema_version == "online_rag_pipeline_config_v5"
    assert hybrid.evidence_grader.name == "noop_evidence"
    assert hybrid.generation_checker.name == "noop_generation"
    assert combined.evidence_grader.name == "crag_lite"
    assert combined.generation_checker.name == "self_rag_lite"


def test_pipeline_schema_requires_enabled_quality_plugins() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")

    profile["evidence_grader"]["enabled"] = False
    with pytest.raises(ValidationError, match="requires enabled evidence_grader"):
        OnlineRAGPipelineConfig.model_validate(profile)

    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    profile["generation_checker"]["enabled"] = False
    with pytest.raises(ValidationError, match="requires enabled generation_checker"):
        OnlineRAGPipelineConfig.model_validate(profile)


def test_v3_profile_is_rejected_after_quality_migration() -> None:
    profile = PipelineConfigLoader().load(
        PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml"
    ).model_dump(mode="json")
    profile["schema_version"] = "online_rag_pipeline_config_v4"

    with pytest.raises(ValidationError):
        OnlineRAGPipelineConfig.model_validate(profile)


def test_registry_builds_quality_plugins() -> None:
    registry = build_default_component_registry()

    evidence = registry.build(
        category="evidence_grader",
        config=ComponentConfig(
            name="crag_lite",
            params={"use_llm": False, "max_judge_chunks": 3},
        ),
        build_context=_quality_context(),
    )
    checker = registry.build(
        category="generation_checker",
        config=ComponentConfig(
            name="self_rag_lite",
            params={"use_llm": False},
        ),
        build_context=_quality_context(),
    )

    assert isinstance(evidence, CRAGLiteEvidenceGraderPlugin)
    assert evidence.plugin_metadata.name == "crag_lite"
    assert evidence.max_judge_chunks == 3
    assert isinstance(checker, SelfRAGLiteGenerationCheckerPlugin)
    assert checker.plugin_metadata.name == "self_rag_lite"


def test_crag_plugin_filters_irrelevant_candidate_and_preserves_parent_fields() -> None:
    plugin = CRAGLiteEvidenceGraderPlugin(
        build_context=_quality_context(),
        use_llm=False,
        max_judge_chunks=8,
        drop_irrelevant=True,
    )

    output = plugin.grade(
        query="enterprise rag architecture",
        results=_results(),
    )

    assert [item["parent_chunk_id"] for item in output.results] == [
        "relevant-parent"
    ]
    assert output.results[0]["metadata"]["matched_child_count"] == 2
    assert output.results[0]["metadata"]["c_rag_judgement"]["decision"] in {
        "keep",
        "downrank",
    }
    assert output.report["enabled"] is True
    assert output.report["original_count"] == 2
    assert output.report["filtered_count"] == 1


def test_noop_evidence_grader_is_explicit_pass_through() -> None:
    plugin = NoOpEvidenceGraderPlugin()
    source = _results()

    output = plugin.grade(query="ignored", results=source)

    assert output.results == source
    assert output.results is not source
    assert output.report is None
    assert plugin.execution_metadata() == {"enabled": False, "mode": "noop"}


def test_self_rag_checker_uses_configured_checker_independent_of_legacy_flag() -> None:
    checker = SelfRAGLiteGenerationCheckerPlugin(
        build_context=_quality_context(),
        use_llm=False,
    )

    report = checker.check(
        query="what does rag do",
        answer="rag retrieves documents and augments generation",
        context="rag retrieves documents and augments generation with evidence",
        citations=[{"citation_id": "C1"}],
    )

    assert report["enabled"] is True
    assert report["method"] == "deterministic_fallback"
    assert report["metadata"]["checker"] == "SelfRAGJudge"
    assert checker.execution_metadata()["mode"] == "self_rag_lite"


def test_noop_generation_checker_returns_none() -> None:
    checker = NoOpGenerationCheckerPlugin()

    assert checker.check(
        query="q",
        answer="a",
        context="c",
        citations=[],
    ) is None
    assert checker.execution_metadata() == {"enabled": False, "mode": "noop"}


class _StaticRetriever:
    source_name = "static"

    def retrieve(self, request):
        return CandidateSet(
            query=request.query,
            source_name=self.source_name,
            candidates=deepcopy(_results()),
        )


class _PassFusion:
    def fuse(self, candidate_sets):
        return candidate_sets[0]


class _PassEnricher:
    def enrich(self, candidate_set):
        return candidate_set


class _PassReranker:
    def rerank(self, *, query, results):
        del query
        return list(results)

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
    return QueryTransformChain([identity])


def test_retrieval_pipeline_ignores_legacy_crag_flags_when_profile_uses_noop() -> None:
    registry = build_default_component_registry()
    evidence = registry.build(
        category="evidence_grader",
        config=ComponentConfig(name="noop_evidence"),
    )
    pipeline = ParentChildRetrievalPipeline(
        retrievers=[_StaticRetriever()],
        fusion=_PassFusion(),
        query_fusion=_PassFusion(),
        candidate_enricher=_PassEnricher(),
        reranker=_PassReranker(),
        query_transform_chain=_identity_chain(),
        adaptive_router=_NoAdaptiveRouter(),
        evidence_grader=evidence,
        generation_checker_enabled=False,
    )

    output = pipeline.run(
        "enterprise rag architecture",
        dense_top_k=999,
        keyword_top_k=999,
        candidate_top_k=999,
        rrf_k=999,
        rerank_top_k=999,
        filter_expr=None,
        keyword_doc_ids=None,
        retrieval_strategy="c_rag_self_rag",
        num_rewrites=999,
        enable_hyde=True,
        enable_crag=True,
        enable_self_rag=True,
        crag_max_judge_chunks=1,
        crag_drop_irrelevant=True,
        extra_metadata=None,
    )

    assert len(output.results) == 2
    assert output.crag_enabled is False
    assert output.self_rag_enabled is False
    assert output.query_expansion.metadata["legacy_quality_overrides"] == {
        "enable_crag": True,
        "enable_self_rag": True,
        "crag_max_judge_chunks": 1,
        "crag_drop_irrelevant": True,
        "ignored_by_configured_quality_plugins": True,
    }
    configured = output.query_expansion.metadata["configured_evidence_grader"]
    assert configured["name"] == "noop_evidence"
    assert configured["input_count"] == 2
    assert configured["output_count"] == 2


class _ContextPacker:
    def pack(self, results):
        del results
        return SimpleNamespace(
            context="rag retrieves documents and augments generation with evidence",
            citations=[{"citation_id": "C1"}],
            to_dict=lambda: {},
        )


class _PromptBuilder:
    def build(self, *, query, packed_context, citations):
        del query, packed_context, citations
        return SimpleNamespace(
            prompt="prompt",
            prompt_id="p",
            prompt_version="v1",
            to_dict=lambda: {},
        )


class _Generator:
    model_name = "fake"

    def generate(self, prompt, **kwargs):
        del prompt, kwargs
        return "rag retrieves documents and augments generation"


def test_generation_pipeline_runs_configured_checker_when_legacy_flag_is_false() -> None:
    checker = SelfRAGLiteGenerationCheckerPlugin(
        build_context=_quality_context(),
        use_llm=False,
    )
    pipeline = ParentChildGenerationPipeline(
        context_packer=_ContextPacker(),
        prompt_builder=_PromptBuilder(),
        llm_generator=_Generator(),
        generation_checker=checker,
    )

    output = pipeline.run(
        "what does rag do",
        [],
        generate_answer=True,
        generation_params={},
        self_rag_enabled=False,
    )

    assert output.self_rag is not None
    assert output.self_rag["enabled"] is True
    assert output.generation_checker_metadata["mode"] == "self_rag_lite"
    assert output.generation_checker_metadata["legacy_enable_self_rag"] is False
    assert output.generation_checker_metadata["legacy_flag_ignored"] is True


def test_runtime_factory_resolves_quality_plugins_from_registry() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/runtime/parent_child_runtime_factory.py"
    ).read_text(encoding="utf-8")
    engine_source = (
        PROJECT_ROOT / "backend/rag/rag_engine/parent_child_rag_engine.py"
    ).read_text(encoding="utf-8")

    assert 'category="evidence_grader"' in source
    assert 'category="generation_checker"' in source
    assert "CRAGJudge(" not in source
    assert "SelfRAGJudge(" not in source
    assert "CRAGJudge(" not in engine_source
    assert "SelfRAGJudge(" not in engine_source


def test_unknown_quality_plugin_fails_during_composition() -> None:
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unknown RAG component"):
        registry.build(
            category="evidence_grader",
            config=ComponentConfig(name="missing"),
        )
    with pytest.raises(ValueError, match="unknown RAG component"):
        registry.build(
            category="generation_checker",
            config=ComponentConfig(name="missing"),
        )
