"""Step 12 Evidence / RAG Context Contract v1 acceptance tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from rag.context.context_packer import ContextPacker
from rag.evidence.contract import RAGEvidenceContractBuilder
from rag.services.legacy_rag_service import LegacyRAGService
from schemas.citation import CitationSchema
from schemas.rag import (
    EvidenceAssessmentStatus,
    EvidenceDisposition,
    RAGContextSchema,
    RAGEvidenceContractSchema,
    RAGTraceSchema,
    RAGToolInputSchema,
    RAGToolOutputSchema,
    RetrievedChunkSchema,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.tool import ToolCallSchema, ToolResultSchema
from tools.fake_rag_tool import FakeRAGTool


def _chunk(
    *,
    rank: int,
    child: str,
    parent: str,
    doc: str = "doc_1",
    match: str = "JWT认证和Schema验证。",
    context: str = "安全设计采用JWT认证，并对输入参数执行Schema验证。",
    drop_reason: str | None = None,
) -> RetrievedChunkSchema:
    metadata = {"pre_context_rank": rank}
    if drop_reason:
        metadata["context_drop_reason"] = drop_reason
    return RetrievedChunkSchema(
        rank=rank,
        score=0.9 - rank * 0.01,
        score_type="rrf_score",
        rerank_score=1.0 - rank * 0.1,
        rerank_score_type="cross_encoder_logit",
        matched_chunk_id=child,
        context_chunk_id=parent,
        child_chunk_id=child,
        parent_chunk_id=parent,
        doc_id=doc,
        match_text=match,
        context_text=context,
        title="安全设计",
        retrieval_sources=["dense", "keyword"],
        metadata=metadata,
    )


def _citation(chunk: RetrievedChunkSchema, citation_id: str = "long_source_id") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        doc_id=chunk.doc_id,
        source_document_id=chunk.doc_id,
        parent_chunk_id=chunk.parent_chunk_id,
        child_chunk_id=chunk.child_chunk_id,
        chunk_id=chunk.child_chunk_id,
        title=chunk.title,
        quote_text=chunk.match_text,
        confidence=chunk.rerank_score,
    )


def _trace() -> RAGTraceSchema:
    return RAGTraceSchema(
        retrieval_mode="hybrid",
        query="安全设计",
        embedding_model="m3e-base",
        embedding_version="local_m3e_base_v1",
        reranker_model="bge-reranker-v2-m3",
        reranker_version="local_v1",
        index_name="rag_child_chunks",
        index_version="idx_v1",
        vector_db="milvus_lite",
        extra={
            "rag_result_metadata": {
                "offline_index": {
                    "dataset_version": "dataset_v1",
                    "embedding_dim": 768,
                },
                "pipeline_config": {
                    "profile_id": "hybrid_v1",
                    "profile_version": "v1",
                    "hash": "pipeline_hash",
                },
            }
        },
    )


def test_contract_is_source_of_truth_and_context_is_projection() -> None:
    selected = _chunk(rank=1, child="child_1", parent="parent_1")
    dropped = _chunk(
        rank=2,
        child="child_2",
        parent="parent_2",
        context="这段内容不应该进入最终Prompt。",
        drop_reason="context_budget_overflow",
    )
    contract = RAGEvidenceContractBuilder.build(
        query="安全设计",
        rewritten_queries=["认证与输入校验"],
        selected_chunks=[selected],
        dropped_chunks=[dropped],
        citations=[_citation(selected)],
        trace=_trace(),
        max_context_chars=6000,
    )

    assert contract.schema_version == "rag_evidence_contract_v1"
    assert contract.selected_evidence_ids == ["E1"]
    assert contract.dropped_evidence_ids == ["E2"]
    assert contract.items[0].disposition == EvidenceDisposition.SELECTED
    assert contract.items[1].disposition == EvidenceDisposition.DROPPED
    assert contract.items[1].drop_reason == "context_budget_overflow"
    assert contract.items[1].citation_ids == []
    assert contract.citations[0].citation_id == "C1"
    assert contract.citations[0].extra["source_citation_id"] == "long_source_id"
    assert "[C1]" in contract.context.context_text
    assert selected.context_text in contract.context.context_text
    assert dropped.context_text not in contract.context.context_text
    assert contract.context.extra["derived_from"] == "rag_evidence_contract_v1"


def test_presence_does_not_claim_semantic_sufficiency() -> None:
    selected = _chunk(rank=1, child="child_1", parent="parent_1")
    contract = RAGEvidenceContractBuilder.build(
        query="安全设计",
        rewritten_queries=[],
        selected_chunks=[selected],
        dropped_chunks=[],
        citations=[_citation(selected)],
        trace=None,
        max_context_chars=6000,
    )

    assert contract.assessment.evidence_available is True
    assert contract.assessment.status == EvidenceAssessmentStatus.NOT_ASSESSED
    assert contract.assessment.details["presence_is_not_semantic_sufficiency"] is True


def test_lineage_carries_index_model_and_pipeline_versions() -> None:
    selected = _chunk(rank=1, child="child_1", parent="parent_1")
    contract = RAGEvidenceContractBuilder.build(
        query="安全设计",
        rewritten_queries=[],
        selected_chunks=[selected],
        dropped_chunks=[],
        citations=[_citation(selected)],
        trace=_trace(),
        max_context_chars=6000,
    )

    assert contract.lineage.index_version == "idx_v1"
    assert contract.lineage.dataset_version == "dataset_v1"
    assert contract.lineage.embedding_model == "m3e-base"
    assert contract.lineage.embedding_dim == 768
    assert contract.lineage.pipeline_profile_id == "hybrid_v1"
    assert contract.lineage.pipeline_config_hash == "pipeline_hash"


def test_contract_rejects_unknown_selected_evidence_id() -> None:
    with pytest.raises(ValidationError):
        RAGEvidenceContractSchema(
            query="q",
            selected_evidence_ids=["E404"],
            context=RAGContextSchema(
                context_text="",
                max_context_chars=100,
                used_context_chars=0,
                context_item_count=1,
            ),
        )


def test_context_packer_records_why_evidence_was_dropped() -> None:
    packer = ContextPacker(max_context_chars=2000, max_items=1, dedup_parent=True)
    packed = packer.pack(
        [
            {
                "rank": 1,
                "child_chunk_id": "c1",
                "parent_chunk_id": "p1",
                "doc_id": "d1",
                "text": "第一条证据",
            },
            {
                "rank": 2,
                "child_chunk_id": "c2",
                "parent_chunk_id": "p2",
                "doc_id": "d2",
                "text": "第二条证据",
            },
        ]
    )

    assert len(packed.selected_results) == 1
    assert len(packed.dropped_results) == 1
    assert packed.dropped_results[0]["metadata"]["context_drop_reason"] == "max_items"


class _LegacyContractToolStub:
    def run(self, tool_input):
        selected = {
            "rank": 1,
            "score": 0.8,
            "rerank_score": 1.2,
            "child_chunk_id": "child_selected",
            "parent_chunk_id": "parent_selected",
            "doc_id": "doc_1",
            "child_text": "JWT认证。",
            "parent_text": "安全设计采用JWT认证。",
            "title": "安全设计",
            "metadata": {"retrieval_sources": ["dense"]},
        }
        dropped = {
            "rank": 2,
            "score": 0.7,
            "rerank_score": 0.9,
            "child_chunk_id": "child_dropped",
            "parent_chunk_id": "parent_dropped",
            "doc_id": "doc_2",
            "child_text": "无关候选。",
            "parent_text": "无关候选不进入Prompt。",
            "metadata": {"context_drop_reason": "max_items"},
        }
        return {
            "success": True,
            "data": {
                "run_id": "rag_run_1",
                "retrieval_results": [selected, dropped],
                "context_pack": {
                    "context": "旧的任意字符串，不应成为最终事实源",
                    "selected_results": [selected],
                    "dropped_results": [dropped],
                    "selected_count": 1,
                    "dropped_count": 1,
                    "packing_strategy": "default",
                },
            },
            "metadata": {
                "offline_index": {
                    "index_version": "idx_real_v1",
                    "dataset_version": "dataset_real_v1",
                    "collection_name": "rag_child_chunks",
                    "backend": "milvus_lite",
                    "embedding_model": "m3e-base",
                    "embedding_version": "v1",
                    "embedding_dim": 768,
                }
            },
        }


def test_legacy_service_publishes_step_12_contract() -> None:
    service = LegacyRAGService(rag_project_root=".")
    service._rag_tool = _LegacyContractToolStub()
    output = service.retrieve(
        RAGToolInputSchema(
            task_id="task_1",
            run_id="run_1",
            agent_name="SchemeWriterAgent",
            query="安全设计",
            max_context_items=1,
            max_context_chars=6000,
        )
    )

    assert output.status == "success"
    assert output.evidence is not None
    assert len(output.evidence.items) == 2
    assert output.evidence.selected_evidence_ids == ["E1"]
    assert output.evidence.dropped_evidence_ids == ["E2"]
    assert output.evidence.items[1].drop_reason == "max_items"
    assert output.citations[0].citation_id == "C1"
    assert "旧的任意字符串" not in output.context.context_text
    assert "安全设计采用JWT认证" in output.context.context_text
    assert output.evidence.lineage.index_version == "idx_real_v1"


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id="task_1",
        run_id="run_1",
        task_type="scheme_generation",
        user_input="安全设计",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="安全设计"),
            task=TaskContextSchema(
                task_id="task_1",
                run_id="run_1",
                task_type="scheme_generation",
            ),
        ),
        status="running",
        created_at="2026-07-17T00:00:00+00:00",
        updated_at="2026-07-17T00:00:00+00:00",
    )


def test_scheme_layer_trusts_contract_not_poisoned_legacy_fields() -> None:
    selected = _chunk(rank=1, child="child_1", parent="parent_1")
    contract = RAGEvidenceContractBuilder.build(
        query="安全设计",
        rewritten_queries=[],
        selected_chunks=[selected],
        dropped_chunks=[],
        citations=[_citation(selected)],
        trace=_trace(),
        max_context_chars=6000,
    )
    payload = RAGToolOutputSchema(
        task_id="task_1",
        run_id="run_1",
        status="success",
        query="安全设计",
        evidence=contract,
        # Deliberately inconsistent compatibility fields. The contract wins.
        retrieved_chunks=[],
        context=RAGContextSchema(
            context_text="POISONED_CONTEXT",
            max_context_chars=6000,
            used_context_chars=len("POISONED_CONTEXT"),
            context_item_count=0,
        ),
        citations=[],
    ).model_dump()
    result = ToolResultSchema(
        tool_call_id="call_1",
        task_id="task_1",
        run_id="run_1",
        tool_name="RealRAGTool",
        success=True,
        result=payload,
        created_at="2026-07-17T00:00:00+00:00",
    )

    context, chunks, citations, normalized = SchemeEvidenceService._extract_rag_output(
        _shared_state(), result
    )

    assert "POISONED_CONTEXT" not in context.context_text
    assert "JWT认证" in context.context_text
    assert len(chunks) == 1
    assert [item.citation_id for item in citations] == ["C1"]
    assert normalized["evidence"]["schema_version"] == "rag_evidence_contract_v1"



def test_shared_state_stores_contract_without_claiming_sufficiency() -> None:
    selected = _chunk(rank=1, child="child_1", parent="parent_1")
    contract = RAGEvidenceContractBuilder.build(
        query="安全设计",
        rewritten_queries=[],
        selected_chunks=[selected],
        dropped_chunks=[],
        citations=[_citation(selected)],
        trace=_trace(),
        max_context_chars=6000,
    )
    state = _shared_state()
    SharedStateWriter().set_evidence_context(
        state,
        query=contract.query,
        evidence_contract=contract.model_dump(),
        context_text=contract.context.context_text,
        retrieved_chunks=[],
        citations=[item.model_dump() for item in contract.citations],
        used_doc_ids=contract.context.used_doc_ids,
        evidence_available=True,
        assessment_status="not_assessed",
        evidence_sufficient=None,
    )

    assert state.context_bundle.evidence.contract["schema_version"] == "rag_evidence_contract_v1"
    assert state.context_bundle.evidence.evidence_available is True
    assert state.context_bundle.evidence.assessment_status == "not_assessed"
    assert state.context_bundle.evidence.evidence_sufficient is None
    assert state.context_bundle.evidence.metadata["context_is_projection"] is True

def test_fake_rag_tool_also_uses_step_12_contract() -> None:
    result = FakeRAGTool().run(
        ToolCallSchema(
            tool_call_id="call_fake",
            task_id="task_fake",
            run_id="run_fake",
            tool_name="FakeRAGTool",
            tool_input={"query": "企业RAG架构"},
            created_at="2026-07-17T00:00:00+00:00",
        )
    )

    output = RAGToolOutputSchema.model_validate(result.result)
    assert output.evidence is not None
    assert output.evidence.selected_evidence_ids == ["E1"]
    assert output.citations[0].citation_id == "C1"
    assert "[C1]" in output.context.context_text
