"""Contracts for independently configured retrieval and generation quality plugins."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from pathlib import Path

import pytest

from apps.enterprise_document.config.grounded_generation import (
    GenerationPluginConfig,
    GroundedGenerationPolicyLoader,
)
from apps.enterprise_document.quality.registry import build_generation_plugin_registry
from rag.config.static_retrieval import ComponentConfig, StaticRetrievalSpecLoader
from rag.plugins.correction_gates import EvidenceSufficiencyCorrectionGate
from rag.plugins.corrective_query_planners import SectionGapCorrectiveQueryPlanner
from rag.plugins.evidence_assessors import (
    CRAGEvidenceAssessorPlugin,
    NoOpEvidenceAssessorPlugin,
)
from rag.ports.quality import EvidenceAssessment
from rag.registry.default_registrations import build_default_component_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_SPEC = PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
GENERATION_POLICY = (
    PROJECT_ROOT
    / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
)


def _candidate(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "parent_chunk_id": f"parent-{chunk_id}",
        "text": text,
        "parent_text": text,
        "score": 0.8,
        "rank": 1,
        "metadata": {},
    }


def test_static_spec_declares_three_independent_quality_components() -> None:
    spec = StaticRetrievalSpecLoader().load(STATIC_SPEC)

    assert spec.evidence_assessor.name == "crag"
    assert spec.corrective_retrieval_gate.name == "evidence_sufficiency"
    assert spec.corrective_query_planner.name == "section_gap"
    assert {
        "drop_irrelevant",
        "keep_at_least",
        "ranking_policy",
    }.isdisjoint(spec.evidence_assessor.params)
    assert {item.name for item in spec.query_transformers} == {
        "identity",
        "multi_query",
        "hyde",
    }


def test_registry_builds_independent_retrieval_quality_plugins() -> None:
    registry = build_default_component_registry()

    assessor = registry.build(
        category="evidence_assessor",
        config=ComponentConfig(
            name="crag",
            params={"use_llm": False, "confidence_threshold": 0.5},
        ),
    )
    gate = registry.build(
        category="corrective_retrieval_gate",
        config=ComponentConfig(name="evidence_sufficiency"),
    )
    planner = registry.build(
        category="corrective_query_planner",
        config=ComponentConfig(
            name="section_gap",
            params={"use_llm": False, "max_queries": 2},
        ),
    )

    assert isinstance(assessor, CRAGEvidenceAssessorPlugin)
    assert isinstance(gate, EvidenceSufficiencyCorrectionGate)
    assert isinstance(planner, SectionGapCorrectiveQueryPlanner)
    assert assessor.plugin_metadata.category == "evidence_assessor"
    assert gate.plugin_metadata.category == "corrective_retrieval_gate"
    assert planner.plugin_metadata.category == "corrective_query_planner"


def test_assessor_only_observes_and_cannot_carry_or_mutate_evidence() -> None:
    assessor = CRAGEvidenceAssessorPlugin(
        use_llm=False,
        confidence_threshold=0.0,
        min_relevant_chunks=0,
    )
    source = [
        _candidate("a", "enterprise RAG architecture evidence"),
        _candidate("b", "partially related deployment notes"),
        _candidate("c", "unrelated cooking recipe"),
    ]
    for rank, item in enumerate(source, start=1):
        item["rank"] = rank
        item["metadata"] = {"nested": {"original_rank": rank}}
    before = deepcopy(source)

    assessment = assessor.assess(query="enterprise RAG", results=source)

    assert assessment.sufficient is True
    assert source == before
    assert [item["parent_chunk_id"] for item in source] == [
        "parent-a",
        "parent-b",
        "parent-c",
    ]
    assert [item.evidence_id for item in assessment.item_judgements] == [
        "parent-a",
        "parent-b",
        "parent-c",
    ]
    assert "results" not in {item.name for item in fields(EvidenceAssessment)}
    assert not hasattr(assessment, "correction")
    assert not hasattr(assessment, "queries")


def test_noop_assessor_is_an_explicit_nonempty_evidence_baseline() -> None:
    assessor = NoOpEvidenceAssessorPlugin()

    nonempty = assessor.assess(query="q", results=[_candidate("c1", "evidence")])
    empty = assessor.assess(query="q", results=[])

    assert nonempty.sufficient is True
    assert empty.sufficient is False


def test_generation_quality_policy_remains_outside_rag_static_spec() -> None:
    policy = GroundedGenerationPolicyLoader().load(GENERATION_POLICY)
    registry = build_generation_plugin_registry()
    checker = registry.build(
        category="generation_checker",
        config=policy.generation_checker,
        build_context={"enable_quality_llm": False},
    )
    repair = registry.build(
        category="repair_strategy",
        config=policy.repair_strategy,
        build_context={"enable_quality_llm": False},
    )

    assert checker.plugin_metadata.category == "generation_checker"
    assert repair.plugin_metadata.category == "repair_strategy"
    assert policy.budget_scope == "section"


def test_noop_generation_checker_returns_none() -> None:
    checker = build_generation_plugin_registry().build(
        category="generation_checker",
        config=GenerationPluginConfig(name="noop_generation"),
    )

    assert checker.check(
        query="q",
        answer="answer",
        context="context",
        citations=[],
    ) is None


@pytest.mark.parametrize(
    ("category", "name"),
    [
        ("evidence_assessor", "missing_assessor"),
        ("corrective_retrieval_gate", "missing_gate"),
        ("corrective_query_planner", "missing_planner"),
    ],
)
def test_unknown_retrieval_quality_plugin_fails_at_composition(
    category: str,
    name: str,
) -> None:
    registry = build_default_component_registry()

    with pytest.raises(ValueError, match="unknown RAG component"):
        registry.build(category=category, config=ComponentConfig(name=name))
