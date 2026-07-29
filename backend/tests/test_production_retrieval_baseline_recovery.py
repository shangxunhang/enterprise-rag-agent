"""Regression coverage for the production retrieval import and chunk contracts."""

from __future__ import annotations

from dataclasses import fields

from rag.chunker.ChildParentChunker import (
    ParentChildChunkResult as LegacyParentChildChunkResult,
)
from rag.config.static_retrieval import ComponentConfig
from rag.judge.crag_judge import CRAGJudge
from rag.judge.rag_quality_judge import CRAGJudge as LegacyCRAGJudge
from rag.plugins.evidence_assessors import CRAGEvidenceAssessorPlugin
from rag.ports.chunking import ParentChildChunkResult
from rag.registry.default_registrations import build_default_component_registry
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2


_RECORDS = [
    {
        "unit_id": "unit-1",
        "doc_id": "doc-1",
        "tenant_id": "default",
        "kb_id": "default",
        "file_id": "file-1",
        "source_type": "offline",
        "source_uri": "fixture://doc-1",
        "source_name": "Document 1",
        "source_format": "md",
        "unit_type": "paragraph",
        "unit_order": 1,
        "text": "Enterprise RAG uses parent child retrieval and citations.",
        "language": "en",
        "cleaning_version": "cleaning_v1.0",
    }
]


def test_parent_child_result_is_owned_by_port_and_reexported() -> None:
    assert LegacyParentChildChunkResult is ParentChildChunkResult
    assert [item.name for item in fields(ParentChildChunkResult)] == [
        "parents",
        "children",
    ]

    result = ParentChildChunkResult(
        parents=[{"parent_chunk_id": "parent-1"}],
        children=[
            {
                "child_chunk_id": "child-1",
                "parent_chunk_id": "parent-1",
            }
        ],
    )
    assert result.children[0]["parent_chunk_id"] == result.parents[0][
        "parent_chunk_id"
    ]
    assert LegacyCRAGJudge is CRAGJudge


def test_default_registry_fixed_chunker_returns_real_contract() -> None:
    chunker = build_default_component_registry().build(
        category="chunker",
        config=ComponentConfig(
            name="fixed_parent_child",
            version="v1",
            params={
                "parent_chunk_size": 80,
                "parent_chunk_overlap": 10,
                "child_chunk_size": 40,
                "child_chunk_overlap": 5,
                "unit": "char",
                "deterministic_created_at": "2026-07-30T00:00:00+08:00",
            },
        ),
    )

    result = chunker.chunk_records(_RECORDS)

    assert isinstance(result, ParentChildChunkResult)
    assert result.parents and result.children
    assert result.parents[0]["schema_version"] == "parent_chunk_v1"
    assert result.children[0]["schema_version"] == "child_chunk_v1"
    assert (
        result.children[0]["parent_chunk_id"]
        == result.parents[0]["parent_chunk_id"]
    )


def test_retrieval_result_v2_preserves_child_match_and_parent_context() -> None:
    result = build_retrieval_result_v2(
        child_chunk={
            "child_chunk_id": "child-1",
            "parent_chunk_id": "parent-1",
            "doc_id": "doc-1",
            "text": "matched child",
        },
        parent_chunk={
            "parent_chunk_id": "parent-1",
            "doc_id": "doc-1",
            "text": "restored parent context",
        },
        rank=1,
        score=0.9,
    )

    assert result["schema_version"] == "retrieval_result_v2"
    assert result["chunk_id"] == "child-1"
    assert result["parent_chunk_id"] == "parent-1"
    assert result["text"] == "restored parent context"


def test_crag_assessor_returns_evidence_assessment_without_mutation() -> None:
    source = [
        {
            "parent_chunk_id": "parent-1",
            "text": "Enterprise RAG retrieval evidence",
            "score": 0.9,
        }
    ]
    assessor = CRAGEvidenceAssessorPlugin(
        use_llm=False,
        confidence_threshold=0.0,
        min_relevant_chunks=0,
    )

    assessment = assessor.assess(
        query="Enterprise RAG",
        results=source,
    )

    assert source[0]["text"] == "Enterprise RAG retrieval evidence"
    assert assessment.sufficient is True
    assert assessment.item_judgements[0].evidence_id == "parent-1"
    assert assessment.report["verification_mode"] == "deterministic_primary"
