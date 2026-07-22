# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_chunk、_citation、_trace、test_contract_is_source_of_truth_and_context_is_projection、test_presence_does_not_claim_semantic_sufficiency、test_lineage_carries_index_model_and_pipeline_versions、test_contract_rejects_unknown_selected_evidence_id、test_context_packer_records_why_evidence_was_dropped、_LegacyContractToolStub、test_legacy_service_publishes_step_12_contract等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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
from rag.services.rag_service import RAGService
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


# 阅读注释（函数）：处理 文本块 相关逻辑。
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
    """处理 文本块 相关逻辑。

    参数:
        rank: rank，具体约束请结合类型标注和调用方确认。
        child: 子块，具体约束请结合类型标注和调用方确认。
        parent: 父块，具体约束请结合类型标注和调用方确认。
        doc: doc，具体约束请结合类型标注和调用方确认。
        match: match，具体约束请结合类型标注和调用方确认。
        context: 当前执行上下文。
        drop_reason: drop reason，具体约束请结合类型标注和调用方确认。

    返回:
        RetrievedChunkSchema

    阅读提示:
        主要直接调用：RetrievedChunkSchema。
    """
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


# 阅读注释（函数）：处理 引用 相关逻辑。
def _citation(chunk: RetrievedChunkSchema, citation_id: str = "long_source_id") -> CitationSchema:
    """处理 引用 相关逻辑。

    参数:
        chunk: 文本块，具体约束请结合类型标注和调用方确认。
        citation_id: 引用 标识，具体约束请结合类型标注和调用方确认。

    返回:
        CitationSchema

    阅读提示:
        主要直接调用：CitationSchema。
    """
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


# 阅读注释（函数）：处理 Trace 相关逻辑。
def _trace() -> RAGTraceSchema:
    """处理 Trace 相关逻辑。

    返回:
        RAGTraceSchema

    阅读提示:
        主要直接调用：RAGTraceSchema。
    """
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
                "static_retrieval_spec": {
                    "spec_id": "enterprise_parent_child_hybrid_v1",
                    "spec_version": "v1",
                    "hash": "pipeline_hash",
                },
            }
        },
    )


# 阅读注释（函数）：处理 测试 contract is source of truth and 上下文 is projection 相关逻辑。
def test_contract_is_source_of_truth_and_context_is_projection() -> None:
    """处理 测试 contract is source of truth and 上下文 is projection 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_chunk, RAGEvidenceContractBuilder.build, _citation, _trace。
    """
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


# 阅读注释（函数）：处理 测试 presence does not claim semantic sufficiency 相关逻辑。
def test_presence_does_not_claim_semantic_sufficiency() -> None:
    """处理 测试 presence does not claim semantic sufficiency 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_chunk, RAGEvidenceContractBuilder.build, _citation。
    """
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


# 阅读注释（函数）：处理 测试 lineage carries 索引 模型 and pipeline versions 相关逻辑。
def test_lineage_carries_index_model_and_pipeline_versions() -> None:
    """处理 测试 lineage carries 索引 模型 and pipeline versions 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_chunk, RAGEvidenceContractBuilder.build, _citation, _trace。
    """
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
    assert contract.lineage.static_retrieval_spec_id == (
        "enterprise_parent_child_hybrid_v1"
    )
    assert contract.lineage.static_retrieval_spec_hash == "pipeline_hash"


# 阅读注释（函数）：处理 测试 contract rejects unknown selected 证据 标识 相关逻辑。
def test_contract_rejects_unknown_selected_evidence_id() -> None:
    """处理 测试 contract rejects unknown selected 证据 标识 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：pytest.raises, RAGEvidenceContractSchema, RAGContextSchema。
    """
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


# 阅读注释（函数）：处理 测试 上下文 packer 记录集合 why 证据 was dropped 相关逻辑。
def test_context_packer_records_why_evidence_was_dropped() -> None:
    """处理 测试 上下文 packer 记录集合 why 证据 was dropped 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：ContextPacker, packer.pack, len。
    """
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


# 阅读注释（类）：封装 legacy contract 工具 stub，集中封装相关状态、依赖和行为。
class _LegacyContractToolStub:
    """封装 legacy contract 工具 stub，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：执行 _LegacyContractToolStub 的主流程。
    def retrieve(self, tool_input):
        """执行 _LegacyContractToolStub 的主流程。

        参数:
            tool_input: 工具调用输入。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
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
                    "context": "[C1] 安全设计\n安全设计采用JWT认证。",
                    "rendered_text": "[C1] 安全设计\n安全设计采用JWT认证。",
                    "max_context_chars": 6000,
                    "token_budget": 1000,
                    "tokens_used": 24,
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


# 阅读注释（函数）：处理 测试 legacy 服务 publishes step 12 contract 相关逻辑。
def test_legacy_service_publishes_step_12_contract() -> None:
    """处理 测试 legacy 服务 publishes step 12 contract 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：LegacyRAGService, _LegacyContractToolStub, service.retrieve, RAGToolInputSchema, len。
    """
    service = RAGService(
        rag_project_root=".",
        retrieval_runtime=_LegacyContractToolStub(),
        allow_legacy_unscoped=True,
    )
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
    assert len(output.items) == 2
    assert output.selected_evidence_ids == ["E1"]
    assert output.dropped_evidence_ids == ["E2"]
    assert output.items[1].drop_reason == "max_items"
    assert output.citations[0].citation_id == "C1"
    assert output.context.context_text == "[C1] 安全设计\n安全设计采用JWT认证。"
    assert output.context.token_budget == 1000
    assert "安全设计采用JWT认证" in output.context.context_text
    assert output.lineage.index_version == "idx_real_v1"


# 阅读注释（函数）：处理 shared 状态 相关逻辑。
def _shared_state() -> SharedStateSchema:
    """处理 shared 状态 相关逻辑。

    返回:
        SharedStateSchema

    阅读提示:
        主要直接调用：SharedStateSchema, ContextBundleSchema, UserContextSchema, TaskContextSchema。
    """
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


# 阅读注释（函数）：处理 测试 scheme layer trusts contract not poisoned legacy fields 相关逻辑。
def test_scheme_layer_trusts_contract_not_poisoned_legacy_fields() -> None:
    """处理 测试 scheme layer trusts contract not poisoned legacy fields 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_chunk, RAGEvidenceContractBuilder.build, _citation, _trace, model_dump, RAGToolOutputSchema, RAGContextSchema, len。
    """
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

    context, chunks, citations, normalized = SchemeEvidenceService.extract_rag_output(
        _shared_state(), result
    )

    assert "POISONED_CONTEXT" not in context.context_text
    assert "JWT认证" in context.context_text
    assert len(chunks) == 1
    assert [item.citation_id for item in citations] == ["C1"]
    assert normalized["evidence"]["schema_version"] == "rag_evidence_contract_v1"



# 阅读注释（函数）：处理 测试 shared 状态 stores contract without claiming sufficiency 相关逻辑。
def test_shared_state_stores_contract_without_claiming_sufficiency() -> None:
    """处理 测试 shared 状态 stores contract without claiming sufficiency 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：_chunk, RAGEvidenceContractBuilder.build, _citation, _trace, _shared_state, set_evidence_context, SharedStateWriter, contract.model_dump。
    """
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

# 阅读注释（函数）：处理 测试 fake RAG 工具 also uses step 12 contract 相关逻辑。
def test_fake_rag_tool_also_uses_step_12_contract() -> None:
    """处理 测试 fake RAG 工具 also uses step 12 contract 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：run, FakeRAGTool, ToolCallSchema, RAGToolOutputSchema.model_validate。
    """
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
