"""Regression tests for parent-context / child-evidence translation."""

from __future__ import annotations

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from rag.services.legacy_rag_service import LegacyRAGService
from schemas.citation import CitationBindingSchema, CitationSchema


def test_full_selected_record_preserves_child_and_parent_text() -> None:
    service = LegacyRAGService(rag_project_root=".")
    records = [
        {
            "rank": 1,
            "score": 0.03,
            "rerank_score": -1.2,
            "child_chunk_id": "child_security",
            "parent_chunk_id": "parent_mixed",
            "doc_id": "doc_1",
            "child_text": "八、安全设计：认证采用JWT，输入参数进行Schema验证，输出敏感字段脱敏。",
            "parent_text": "前置章节。" * 400 + "八、安全设计：认证采用JWT。",
            "title": "安全设计",
            "metadata": {"retrieval_sources": ["dense", "keyword"]},
        }
    ]

    chunks = service._convert_retrieved_chunks(records)

    assert len(chunks) == 1
    assert chunks[0].match_text.startswith("八、安全设计")
    assert "前置章节" in chunks[0].context_text
    assert chunks[0].match_text != chunks[0].context_text


def test_citations_expand_all_matched_child_evidence() -> None:
    service = LegacyRAGService(rag_project_root=".")
    records = [
        {
            "rank": 1,
            "score": 0.03,
            "rerank_score": -1.2,
            "child_chunk_id": "child_finance",
            "parent_chunk_id": "parent_mixed",
            "doc_id": "doc_1",
            "child_text": "功能点估算与费用测算。",
            "parent_text": "功能点估算。八、安全设计：JWT、权限控制、Schema验证和输出脱敏。",
            "title": "综合方案",
            "metadata": {
                "retrieval_sources": ["dense", "keyword"],
                "matched_child_chunk_ids": ["child_finance", "child_security"],
                "matched_child_chunks": [
                    {
                        "child_chunk_id": "child_finance",
                        "parent_chunk_id": "parent_mixed",
                        "doc_id": "doc_1",
                        "text": "功能点估算与费用测算。",
                        "title": "投资控制",
                    },
                    {
                        "child_chunk_id": "child_security",
                        "parent_chunk_id": "parent_mixed",
                        "doc_id": "doc_1",
                        "text": "八、安全设计：认证采用JWT；用户仅可访问自身任务；输入参数进行Schema验证；输出敏感字段脱敏。",
                        "title": "安全设计",
                    },
                ],
            },
        }
    ]
    chunks = service._convert_retrieved_chunks(records)
    citations = service._build_citations(chunks)

    by_chunk = {item.chunk_id: item for item in citations}
    assert set(by_chunk) == {"child_finance", "child_security"}
    assert "JWT" in (by_chunk["child_security"].quote_text or "")
    assert "输出敏感字段脱敏" in (by_chunk["child_security"].quote_text or "")


def test_weak_topical_overlap_is_not_grounding() -> None:
    evidence = CitationSchema(
        citation_id="C3",
        source_type="document",
        doc_id="doc_1",
        chunk_id="child_overview",
        title="方案概述",
        quote_text=(
            "本方案基于智能体系统与企业知识库，实现知识检索、知识匹配、知识更新和知识推荐。"
        ),
    )
    binding = CitationBindingSchema(
        binding_id="binding_1",
        citation_id="C3",
        target_document_id="doc_out",
        target_section_id="section_security",
        target_paragraph_id="paragraph_1",
        target_claim_id="claim_1",
        source_document_id="doc_1",
        source_chunk_id="child_overview",
        claim_text="系统采用多因素认证、漏洞扫描、防火墙和入侵检测保障安全。",
        quote_text=evidence.quote_text,
    )

    assert not SchemeWriterAgent._binding_is_supported(binding, {"C3": evidence})


def test_supported_binding_is_marked_grounding_verified() -> None:
    evidence = CitationSchema(
        citation_id="C1",
        source_type="document",
        doc_id="doc_1",
        chunk_id="child_security",
        title="安全设计",
        quote_text=(
            "安全设计采用JWT认证，用户仅可访问自身任务；用户输入和工具参数执行Schema验证，"
            "工具返回的敏感字段需要脱敏。"
        ),
    )
    binding = CitationBindingSchema(
        binding_id="binding_1",
        citation_id="C1",
        target_document_id="doc_out",
        target_section_id="section_security",
        target_paragraph_id="paragraph_1",
        target_claim_id="claim_1",
        source_document_id="doc_1",
        source_chunk_id="child_security",
        claim_text=(
            "安全设计采用JWT认证，用户仅可访问自身任务；用户输入和工具参数执行Schema验证，"
            "工具返回的敏感字段需要脱敏。"
        ),
        quote_text=evidence.quote_text,
    )

    supported = SchemeWriterAgent._supported_bindings([binding], [evidence])

    assert len(supported) == 1
    assert supported[0].metadata["grounding_verified"] is True
    assert supported[0].metadata["grounding_policy"] == "lexical_strict_v2"

class _LegacyToolStub:
    def run(self, tool_input):
        parent_text = "前置内容。" * 200 + "八、安全设计：JWT、权限控制、Schema验证和输出脱敏。"
        selected = {
            "rank": 1,
            "score": 0.03,
            "rerank_score": -1.0,
            "child_chunk_id": "child_finance",
            "parent_chunk_id": "parent_mixed",
            "doc_id": "doc_1",
            "child_text": "功能点估算。",
            "parent_text": parent_text,
            "text": parent_text,
            "title": "综合方案",
            "metadata": {
                "retrieval_sources": ["keyword"],
                "matched_child_chunk_ids": ["child_finance", "child_security"],
                "matched_child_chunks": [
                    {
                        "child_chunk_id": "child_finance",
                        "parent_chunk_id": "parent_mixed",
                        "doc_id": "doc_1",
                        "text": "功能点估算。",
                    },
                    {
                        "child_chunk_id": "child_security",
                        "parent_chunk_id": "parent_mixed",
                        "doc_id": "doc_1",
                        "text": "八、安全设计：JWT、权限控制、Schema验证和输出脱敏。",
                        "title": "安全设计",
                    },
                ],
            },
        }
        return {
            "success": True,
            "data": {
                "run_id": "rag_run_1",
                "contexts": [
                    {
                        "rank": 1,
                        "doc_id": "doc_1",
                        "parent_chunk_id": "parent_mixed",
                        "child_chunk_id": "child_finance",
                        "text_preview": "仅有前500字，安全证据已被截断",
                    }
                ],
                "retrieval_results": [selected],
                "context_pack": {
                    "context": parent_text,
                    "selected_results": [selected],
                    "selected_count": 1,
                    "dropped_count": 0,
                    "packing_strategy": "default",
                },
            },
            "metadata": {},
        }


def test_legacy_service_prefers_full_selected_records_over_compact_preview() -> None:
    from schemas.rag import RAGToolInputSchema

    service = LegacyRAGService(rag_project_root=".")
    service._rag_tool = _LegacyToolStub()
    output = service.retrieve(
        RAGToolInputSchema(
            task_id="task_1",
            run_id="run_1",
            agent_name="SchemeWriterAgent",
            query="生成方案",
            max_context_items=3,
            max_context_chars=6000,
        )
    )

    assert output.status == "success"
    assert "JWT" in (output.context.context_text if output.context else "")
    citation_by_chunk = {item.chunk_id: item for item in output.citations}
    assert "child_security" in citation_by_chunk
    assert "JWT" in (citation_by_chunk["child_security"].quote_text or "")


def test_bm25_retriever_filters_multiple_doc_ids() -> None:
    from rag.retriever.bm25_child_retriever import BM25ChildRetriever

    retriever = BM25ChildRetriever(
        [
            {"child_chunk_id": "c1", "parent_chunk_id": "p1", "doc_id": "d1", "text": "安全设计 JWT"},
            {"child_chunk_id": "c2", "parent_chunk_id": "p2", "doc_id": "d2", "text": "安全设计 JWT"},
            {"child_chunk_id": "c3", "parent_chunk_id": "p3", "doc_id": "d3", "text": "安全设计 JWT"},
        ]
    )
    hits = retriever.search("安全设计", top_k=10, doc_ids=["d1", "d3"])

    assert {item["doc_id"] for item in hits} == {"d1", "d3"}


def test_legacy_adapter_propagates_document_filters() -> None:
    from schemas.rag import RAGToolInputSchema

    class FilterCaptureTool:
        def __init__(self):
            self.last_input = None

        def run(self, tool_input):
            self.last_input = tool_input
            return {
                "success": True,
                "data": {
                    "contexts": [],
                    "retrieval_results": [],
                    "context_pack": {"context": "", "selected_results": []},
                },
                "metadata": {},
            }

    tool = FilterCaptureTool()
    service = LegacyRAGService(rag_project_root=".")
    service._rag_tool = tool
    service.retrieve(
        RAGToolInputSchema(
            task_id="task_filter",
            run_id="run_filter",
            agent_name="SchemeWriterAgent",
            query="安全设计",
            filters={"doc_ids": ["doc_a", "doc_b"]},
        )
    )

    assert tool.last_input["keyword_doc_ids"] == ["doc_a", "doc_b"]
    assert tool.last_input["filter_expr"] == 'doc_id in ["doc_a", "doc_b"]'


def test_real_project_data_keeps_security_child_as_citation() -> None:
    import json
    from pathlib import Path

    from schemas.rag import RetrievedChunkSchema

    root = Path(__file__).resolve().parents[2]
    child_file = root / "data/processed/parent_child_chunks/child_chunks.jsonl"
    if not child_file.is_file():
        import pytest

        pytest.skip(
            "external real-project child chunk fixture is not included in the share package"
        )
    target_id = "doc_002_single_column_paper_parent_000003_child_0003"
    target = None
    for line in child_file.open("r", encoding="utf-8"):
        item = json.loads(line)
        if item.get("child_chunk_id") == target_id:
            target = item
            break
    assert target is not None
    assert "JWT" in target["text"]

    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=target_id,
        context_chunk_id=target["parent_chunk_id"],
        child_chunk_id=target_id,
        parent_chunk_id=target["parent_chunk_id"],
        doc_id=target["doc_id"],
        match_text=target["text"],
        context_text=target["text"],
        metadata={"matched_child_chunks": [target]},
    )
    citations = LegacyRAGService._build_citations([chunk])
    by_chunk = {item.chunk_id: item for item in citations}

    assert target_id in by_chunk
    assert "JWT" in (by_chunk[target_id].quote_text or "")
    assert "schema验证" in (by_chunk[target_id].quote_text or "")
