from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer import (
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionRequestSchema,
    SectionExecutionResultSchema,
    SectionPlanSchema,
)
from schemas.citation import CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id="task_contract",
        run_id="run_contract",
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id="task_contract",
                run_id="run_contract",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-07-22T00:00:00+00:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": "task_contract",
            "task_type": "scheme_generation",
            "user_query": "生成建设方案",
            "generation_requirements": {
                "required_sections": ["技术方案"],
                "citation_required_sections": ["技术方案"],
            },
        }
    )


def _chunk(chunk_id: str, rank: int) -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=rank,
        matched_chunk_id=chunk_id,
        context_chunk_id=f"parent_{chunk_id}",
        child_chunk_id=chunk_id,
        parent_chunk_id=f"parent_{chunk_id}",
        doc_id="doc_1",
        match_text=f"match {chunk_id}",
        context_text=f"context {chunk_id}",
    )


def _context(text: str = "document context") -> RAGContextSchema:
    return RAGContextSchema(
        context_text=text,
        used_context_chunk_ids=["parent_c1"],
        matched_chunk_ids=["c1"],
        used_doc_ids=["doc_1"],
        used_context_chars=len(text),
        context_item_count=1,
    )


def _citation(citation_id: str = "C1") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        doc_id="doc_1",
        child_chunk_id="c1",
        parent_chunk_id="parent_c1",
        quote_text="evidence quote",
    )


def test_section_execution_request_carries_current_section_inputs() -> None:
    first = _chunk("c1", 1)
    second = _chunk("c2", 2)
    previous = SchemeSectionSchema(
        section_id="section_001",
        section_title="项目概述",
        section_order=1,
        content="已有章节",
        status=ExecutionStatus.SUCCESS,
    )

    request = SectionExecutionRequestSchema(
        shared_state=_shared_state(),
        document_id="document_run_contract",
        project_input=_project_input(),
        section_plan=SectionPlanSchema(
            section_id="section_002",
            section_title="技术方案",
            section_order=2,
            citation_required=True,
        ),
        previous_sections=[previous],
        document_rag_context=_context(),
        document_retrieved_chunks=[first, second],
        document_citations=[_citation()],
        document_evidence_assessment={"status": "sufficient"},
        document_tool_call_ids=["rag_document"],
        section_retrieval_enabled=True,
        corrective_retrieval_enabled=True,
    )

    assert request.schema_version == "section_execution_request_v1"
    assert request.section_plan.section_title == "技术方案"
    assert request.previous_sections[0].section_title == "项目概述"
    assert [item.matched_chunk_id for item in request.document_retrieved_chunks] == [
        "c1",
        "c2",
    ]
    assert request.document_evidence_assessment["status"] == "sufficient"
    assert request.document_tool_call_ids == ["rag_document"]


def test_section_execution_result_separates_active_evidence_from_all_retrieval_artifacts() -> None:
    initial_chunk = _chunk("c1", 1)
    recovery_chunk = _chunk("c2", 1)
    final_context = RAGContextSchema(
        context_text="recovery context",
        used_context_chunk_ids=["parent_c2"],
        matched_chunk_ids=["c2"],
        used_doc_ids=["doc_1"],
        used_context_chars=16,
        context_item_count=1,
    )
    evidence = SectionEvidenceBundleSchema(
        section_id="section_002",
        section_title="技术方案",
        retrieval_scope="self_rag_recovery",
        query="技术方案恢复检索",
        tool_call_ids=["rag_section", "rag_self_rag"],
        rag_context=final_context,
        retrieved_chunks=[recovery_chunk],
        citations=[_citation("C2")],
        recovery_count=1,
    )
    section = SchemeSectionSchema(
        section_id="section_002",
        section_title="技术方案",
        section_order=2,
        content="最终章节",
        status=ExecutionStatus.SUCCESS,
    )

    result = SectionExecutionResultSchema(
        section=section,
        evidence=evidence,
        retrieved_chunks=[initial_chunk, recovery_chunk],
        rag_outputs=[
            {"scope": "section", "trace_id": "rag_section"},
            {"scope": "self_rag_recovery", "trace_id": "rag_self_rag"},
        ],
        budget_usage={
            "retrieval_rounds": 1,
            "llm_calls": 2,
        },
    )

    assert result.schema_version == "section_execution_result_v1"
    assert [item.matched_chunk_id for item in result.retrieved_chunks] == ["c1", "c2"]
    assert [item["scope"] for item in result.rag_outputs] == [
        "section",
        "self_rag_recovery",
    ]
    assert [item.matched_chunk_id for item in result.evidence.retrieved_chunks] == ["c2"]
    assert result.evidence.retrieval_scope == "self_rag_recovery"
    assert result.budget_usage["retrieval_rounds"] == 1


def test_section_execution_contract_avoids_duplicate_runtime_ownership() -> None:
    request_fields = set(SectionExecutionRequestSchema.model_fields)
    result_fields = set(SectionExecutionResultSchema.model_fields)

    assert "citation_registry" not in request_fields
    assert "generated_model_outputs" not in result_fields
    assert "shared_state" in request_fields
    assert "retrieved_chunks" in result_fields
    assert "rag_outputs" in result_fields


def test_section_execution_contracts_remain_available_from_compatibility_exports() -> None:
    from apps.enterprise_document.schemas.scheme_writer_schema import (
        SectionExecutionRequestSchema as CompatibilityRequest,
        SectionExecutionResultSchema as CompatibilityResult,
    )

    assert CompatibilityRequest is SectionExecutionRequestSchema
    assert CompatibilityResult is SectionExecutionResultSchema
