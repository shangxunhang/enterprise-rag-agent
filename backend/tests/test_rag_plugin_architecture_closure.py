"""Closure tests for the single-spec plugin architecture."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from rag.config.static_retrieval import StaticRetrievalSpecLoader
from rag.mapping.evidence_mapper import EvidenceMapper
from rag.mapping.request_mapper import RAGRequestMapper
from rag.plugins.context_packers import DefaultContextPacker
from rag.registry.component_registry import ComponentRegistry
from rag.registry.default_registrations import build_default_component_registry
from schemas.rag import RAGToolInputSchema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_RELATIVE = "backend/rag/config/static_retrieval_v1.yaml"
SPEC_PATH = PROJECT_ROOT / SPEC_RELATIVE


def test_relative_static_spec_path_is_independent_of_process_cwd(monkeypatch) -> None:
    monkeypatch.chdir(PROJECT_ROOT / "scripts")

    spec = StaticRetrievalSpecLoader().load(SPEC_RELATIVE)

    assert spec.spec_id == "enterprise_parent_child_hybrid_v1"


def test_cli_static_spec_path_resolves_against_project_root(monkeypatch) -> None:
    from mainline_runtime import resolve_project_path

    monkeypatch.chdir(PROJECT_ROOT / "scripts")
    resolved = resolve_project_path(SPEC_RELATIVE, project_root=PROJECT_ROOT)

    assert resolved == SPEC_PATH.resolve()


def test_explicit_project_root_resolves_relative_static_spec(tmp_path) -> None:
    root = tmp_path / "project"
    target = root / SPEC_RELATIVE
    target.parent.mkdir(parents=True)
    shutil.copy2(SPEC_PATH, target)

    spec = StaticRetrievalSpecLoader().load(SPEC_RELATIVE, project_root=root)

    assert spec.spec_id == "enterprise_parent_child_hybrid_v1"


def test_static_spec_references_only_registered_components() -> None:
    spec = StaticRetrievalSpecLoader().load(SPEC_PATH)
    registry = build_default_component_registry()
    references = [
        *(("query_transformer", item) for item in spec.query_transformers),
        *(("retriever", item) for item in spec.retrievers),
        ("source_fusion", spec.source_fusion),
        ("query_fusion", spec.query_fusion),
        ("candidate_enricher", spec.candidate_enricher),
        ("reranker", spec.reranker),
        ("evidence_assessor", spec.evidence_assessor),
        ("corrective_retrieval_gate", spec.corrective_retrieval_gate),
        ("corrective_query_planner", spec.corrective_query_planner),
        *(("context_packer", item) for item in spec.context_packers),
    ]

    assert all(
        registry.contains(
            category=category,
            name=config.name,
            version=config.version,
        )
        for category, config in references
    )


def test_registry_rejects_plugin_with_incompatible_category_contract() -> None:
    class InvalidSourceFusion:
        def __init__(self, *, build_context=None) -> None:
            del build_context

        def merge(self, candidate_sets):
            return candidate_sets

    registry: ComponentRegistry[object] = ComponentRegistry()
    with pytest.raises(TypeError, match="missing method fuse"):
        registry.register(
            category="source_fusion",
            name="invalid",
            version="v1",
            builder=InvalidSourceFusion,
        )


def test_unknown_static_component_fails_during_composition(tmp_path) -> None:
    path = tmp_path / "static.yaml"
    payload = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    payload["evidence_assessor"]["name"] = "missing_assessor"
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    spec = StaticRetrievalSpecLoader().load(path)

    with pytest.raises(ValueError, match="unknown RAG component"):
        build_default_component_registry().build(
            category="evidence_assessor",
            config=spec.evidence_assessor,
        )


def test_context_packer_reassigns_contiguous_agent_facing_ranks() -> None:
    packer = DefaultContextPacker(
        max_context_chars=6000,
        max_items=3,
        text_field="text",
    )
    packed = packer.pack(
        [
            {
                "rank": rank,
                "doc_id": f"doc-{rank}",
                "chunk_id": f"child-{rank}",
                "child_chunk_id": f"child-{rank}",
                "parent_chunk_id": f"parent-{rank}",
                "text": f"evidence-{rank}",
                "metadata": {},
            }
            for rank in (1, 3, 7)
        ],
        token_budget=1000,
        max_items=3,
    )

    assert [item["rank"] for item in packed.selected_results] == [1, 2, 3]
    assert [item["context_rank"] for item in packed.selected_results] == [1, 2, 3]
    assert [item["pre_context_rank"] for item in packed.selected_results] == [1, 3, 7]


def test_evidence_mapper_guarantees_contiguous_output_ranks() -> None:
    chunks = EvidenceMapper().chunks(
        [
            {
                "rank": 1,
                "doc_id": "doc-a",
                "child_chunk_id": "child-a",
                "parent_chunk_id": "parent-a",
                "child_text": "a",
                "parent_text": "A",
            },
            {
                "rank": 3,
                "doc_id": "doc-b",
                "child_chunk_id": "child-b",
                "parent_chunk_id": "parent-b",
                "child_text": "b",
                "parent_text": "B",
            },
        ]
    )

    assert [item.rank for item in chunks] == [1, 2]
    assert [item.metadata["pre_output_rank"] for item in chunks] == [1, 3]


def test_legacy_strategy_name_no_longer_enables_online_plugins() -> None:
    request = RAGToolInputSchema(
        task_id="task-1",
        run_id="run-1",
        agent_name="SchemeWriterAgent",
        query="test query",
        extra={"retrieval_strategy": "c_rag_self_rag_hyde"},
    )

    invocation = RAGRequestMapper().map(request)

    assert "enable_hyde" not in invocation.payload
    assert "enable_crag" not in invocation.payload
    assert "enable_self_rag" not in invocation.payload


def test_online_pipeline_has_assessment_driven_correction_only() -> None:
    source = (
        PROJECT_ROOT / "backend/rag/application/parent_child_retrieval.py"
    ).read_text(encoding="utf-8")
    request_mapper_source = (
        PROJECT_ROOT / "backend/rag/mapping/request_mapper.py"
    ).read_text(encoding="utf-8")

    assert "strategy_lower" not in request_mapper_source
    assert "retrieval_planner.plan" in source
    assert "corrective_retrieval_gate.decide" in source
    assert "enable_corrective_retrieval" not in source
