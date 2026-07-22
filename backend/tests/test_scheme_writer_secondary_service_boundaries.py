from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter import (
    LegacyEvidenceAdapter,
)
from schemas.citation import CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import RAGContextSchema, RetrievedChunkSchema
from schemas.tool import ToolResultSchema


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id="task_secondary",
        run_id="run_secondary",
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id="task_secondary",
                run_id="run_secondary",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-07-22T00:00:00+00:00",
    )


def test_document_citation_registry_remaps_markers_and_global_ids() -> None:
    registry = DocumentCitationRegistry()
    context = RAGContextSchema(
        context_text="安全设计采用统一身份认证。[local_C1]",
        used_context_chars=24,
        context_item_count=1,
    )
    citation = CitationSchema(
        citation_id="local_C1",
        source_type="document",
        doc_id="doc_security",
        child_chunk_id="child_security",
        quote_text="安全设计采用统一身份认证。",
    )

    remapped_context, _, remapped_citations, normalized = registry.remap_bundle(
        context=context,
        chunks=[],
        citations=[citation],
        normalized={"query": "安全设计"},
        scope="section",
        query="安全设计",
    )

    assert remapped_context.context_text.endswith("[C1]")
    assert [item.citation_id for item in remapped_citations] == ["C1"]
    assert normalized["extra"]["citation_id_map"] == {"local_C1": "C1"}
    assert normalized["citations"][0]["citation_id"] == "C1"


def test_legacy_evidence_adapter_upgrades_chunks_into_canonical_contract() -> None:
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="child_1",
        context_chunk_id="parent_1",
        child_chunk_id="child_1",
        parent_chunk_id="parent_1",
        doc_id="doc_1",
        match_text="采用统一身份认证。",
        context_text="安全设计采用统一身份认证，并记录审计日志。",
    )
    result = ToolResultSchema(
        tool_call_id="call_legacy",
        task_id="task_secondary",
        run_id="run_secondary",
        tool_name="LegacyRAGTool",
        success=True,
        result={
            "query": "安全设计",
            "retrieved_chunks": [chunk.model_dump()],
            "citations": [],
            "context": {"max_context_chars": 6000},
        },
        created_at="2026-07-22T00:00:00+00:00",
    )

    context, chunks, citations, normalized = LegacyEvidenceAdapter.extract(
        _shared_state(), result
    )

    assert context.context_item_count == 1
    assert [item.matched_chunk_id for item in chunks] == ["child_1"]
    assert len(citations) == 1
    assert citations[0].extra["rebuilt_from_retrieved_chunk"] is True
    assert normalized["evidence"]["schema_version"] == "rag_evidence_contract_v1"
    assert normalized["evidence"]["extra"]["compatibility_upgrade"] is True
    assert normalized["evidence"]["extra"]["upgraded_by"] == "LegacyEvidenceAdapter"
