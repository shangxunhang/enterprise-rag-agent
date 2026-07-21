"""Closure tests for the single-spec plugin architecture."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from rag.config.static_retrieval import StaticRetrievalSpecLoader
from rag.mapping.evidence_mapper import EvidenceMapper
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.plugins.context_packers import DefaultContextPacker
from rag.retriever.bm25_child_retriever import BM25ChildRetriever
from rag.registry.component_registry import ComponentRegistry
from rag.registry.default_registrations import build_default_component_registry
from schemas.rag import (
    EvidenceAssessmentStatus,
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
)


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

    invocation = RAGRequestMapper(allow_legacy_unscoped=True).map(request)

    assert "enable_hyde" not in invocation.payload
    assert "enable_crag" not in invocation.payload
    assert "enable_self_rag" not in invocation.payload


def test_request_mapper_enforces_tenant_and_kb_scope_before_optional_filters() -> None:
    request = RAGToolInputSchema(
        task_id="task-scope",
        run_id="run-scope",
        agent_name="SchemeWriterAgent",
        query="政务云",
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="tenant-a",
            authorized_kb_ids=["kb-design", "kb-policy"],
            allowed_file_ids=["file-1"],
            allowed_doc_ids=["doc-1", "doc-2"],
        ),
        filters={"doc_ids": ["doc-2"]},
    )

    invocation = RAGRequestMapper().map(request)

    assert 'tenant_id == "tenant-a"' in invocation.payload["filter_expr"]
    assert 'kb_id in ["kb-design", "kb-policy"]' in invocation.payload["filter_expr"]
    assert 'file_id in ["file-1"]' in invocation.payload["filter_expr"]
    assert 'doc_id in ["doc-2"]' in invocation.payload["filter_expr"]
    assert invocation.payload["keyword_scope"] == {
        "tenant_id": "tenant-a",
        "kb_ids": ["kb-design", "kb-policy"],
        "file_ids": ["file-1"],
        "doc_ids": ["doc-2"],
    }
    assert invocation.payload["access_scope_enforced"] is True


def test_request_mapper_fails_closed_when_requested_scope_is_disjoint() -> None:
    request = RAGToolInputSchema(
        task_id="task-scope-denied",
        run_id="run-scope-denied",
        agent_name="SchemeWriterAgent",
        query="test",
        kb_ids=["kb-forbidden"],
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="tenant-a",
            authorized_kb_ids=["kb-design"],
            allowed_doc_ids=["doc-allowed"],
        ),
    )

    with pytest.raises(ValueError, match="outside the authorized retrieval scope"):
        RAGRequestMapper().map(request)


def test_request_mapper_rejects_tenant_without_kb_scope_outside_demo_mode() -> None:
    request = RAGToolInputSchema(
        task_id="task-missing-kb",
        run_id="run-missing-kb",
        agent_name="SchemeWriterAgent",
        query="test",
        filters={"tenant_id": "tenant-a"},
    )

    with pytest.raises(ValueError, match="retrieval access scope is required"):
        RAGRequestMapper().map(request)


def test_bm25_retriever_blocks_cross_tenant_and_cross_kb_hits() -> None:
    retriever = BM25ChildRetriever(
        [
            {
                "chunk_id": "a-design",
                "child_chunk_id": "a-design",
                "parent_chunk_id": "p-a-design",
                "tenant_id": "tenant-a",
                "kb_id": "kb-design",
                "file_id": "file-a",
                "doc_id": "doc-a",
                "text": "政务云 建设 方案",
            },
            {
                "chunk_id": "a-finance",
                "child_chunk_id": "a-finance",
                "parent_chunk_id": "p-a-finance",
                "tenant_id": "tenant-a",
                "kb_id": "kb-finance",
                "file_id": "file-finance",
                "doc_id": "doc-finance",
                "text": "政务云 建设 方案",
            },
            {
                "chunk_id": "b-design",
                "child_chunk_id": "b-design",
                "parent_chunk_id": "p-b-design",
                "tenant_id": "tenant-b",
                "kb_id": "kb-design",
                "file_id": "file-b",
                "doc_id": "doc-b",
                "text": "政务云 建设 方案",
            },
        ]
    )

    hits = retriever.search(
        "政务云 建设",
        tenant_id="tenant-a",
        kb_ids=["kb-design"],
    )

    assert [item["child_chunk_id"] for item in hits] == ["a-design"]
    assert all(item["tenant_id"] == "tenant-a" for item in hits)
    assert all(item["kb_id"] == "kb-design" for item in hits)


def test_result_mapper_projects_final_evidence_assessment_into_contract() -> None:
    assessment = RAGResultMapper._assessment_from_quality(
        {
            "metadata": {"judge": "CRAGJudge"},
            "corrective_retrieval": {
                "triggered": True,
                "rounds": [{"round": 1}],
                "final_assessment": {
                    "sufficient": False,
                    "confidence": 0.12,
                    "reason": "relevant evidence below threshold",
                    "relevant_chunk_count": 0,
                },
            },
        }
    )

    assert assessment.status == EvidenceAssessmentStatus.INSUFFICIENT
    assert assessment.judge_name == "CRAGJudge"
    assert assessment.score == pytest.approx(0.12)
    assert assessment.reason_codes == ["retrieval_evidence_insufficient"]
    assert assessment.details["corrective_retrieval_triggered"] is True
    assert assessment.details["correction_rounds"] == 1


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
