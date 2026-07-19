"""Mutually exclusive query-transform plugin contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag.config.static_retrieval import ComponentConfig, StaticRetrievalSpecLoader
from rag.query.query_transform_selector import QueryTransformSelector
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_SPEC = PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"


def _selector() -> QueryTransformSelector:
    spec = StaticRetrievalSpecLoader().load(STATIC_SPEC)
    registry = build_default_component_registry()
    build_context = {
        "query_llm_generator": None,
        "enable_query_expansion_llm": False,
        "query_expansion_generation_params": {},
    }
    transformers = [
        registry.build(
            category="query_transformer",
            config=config,
            build_context=build_context,
        )
        for config in spec.query_transformers
    ]
    return QueryTransformSelector(
        transformers,
        spec_id=spec.spec_id,
        spec_version=spec.spec_version,
    )


def test_identity_mode_runs_only_identity_transformer() -> None:
    output = _selector().transform("enterprise RAG", mode="identity")

    assert output.retrieval_queries == ["enterprise RAG"]
    assert output.metadata["query_transform_mode"] == "identity"
    assert len(output.metadata["transformers"]) == 1
    assert output.metadata["transformers"][0]["name"] == "identity"


def test_multi_query_mode_runs_only_multi_query_transformer() -> None:
    output = _selector().transform("enterprise RAG", mode="multi_query")

    assert len(output.retrieval_queries) == 4
    assert output.retrieval_queries[0] == "enterprise RAG"
    assert len(output.metadata["transformers"]) == 1
    assert output.metadata["transformers"][0]["name"] == "multi_query"
    assert output.hyde_query is None


def test_hyde_mode_runs_only_hyde_transformer() -> None:
    output = _selector().transform("enterprise RAG", mode="hyde")

    assert output.hyde_query
    assert output.hyde_query in output.retrieval_queries
    assert output.rewritten_queries == []
    assert len(output.metadata["transformers"]) == 1
    assert output.metadata["transformers"][0]["name"] == "hyde"


def test_selector_requires_all_static_implementations() -> None:
    registry = build_default_component_registry()
    identity = registry.build(
        category="query_transformer",
        config=ComponentConfig(name="identity"),
    )

    with pytest.raises(ValueError, match="must provide query transformers"):
        QueryTransformSelector([identity])


def test_unknown_query_transformer_fails_during_composition() -> None:
    with pytest.raises(ValueError, match="unknown RAG component"):
        build_default_component_registry().build(
            category="query_transformer",
            config=ComponentConfig(name="missing"),
        )


def test_retrieval_pipeline_uses_selector_and_has_no_strategy_chain() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")

    assert "query_transform_selector.transform" in source
    assert "query_transform_chain" not in source
    assert "query_expander.expand" not in source
