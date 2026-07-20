"""Contract tests for LangChain interoperability over the canonical RAG boundary."""

from __future__ import annotations

from rag.integrations.langchain import (
    LangChainRAGRetriever,
    build_rag_runnable,
    evidence_bundle_to_documents,
)
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceDisposition,
    RAGContextSchema,
    RAGEvidenceItemSchema,
    RAGToolInputSchema,
)
from schemas.status import ExecutionStatus


class _StubRAGService:
    def __init__(self) -> None:
        self.requests: list[RAGToolInputSchema] = []

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        self.requests.append(request)
        selected = RAGEvidenceItemSchema(
            evidence_id="E1",
            disposition=EvidenceDisposition.SELECTED,
            rank=1,
            matched_chunk_id="child_1",
            context_chunk_id="parent_1",
            child_chunk_id="child_1",
            parent_chunk_id="parent_1",
            doc_id="doc_1",
            match_text="精确命中证据",
            context_text="父块上下文正文",
            title="测试文档",
            section="第一章",
            score=0.81,
            rerank_score=0.93,
            retrieval_sources=["dense", "bm25"],
            metadata={"source": "fixture"},
        )
        dropped = RAGEvidenceItemSchema(
            evidence_id="E2",
            disposition=EvidenceDisposition.DROPPED,
            rank=2,
            matched_chunk_id="child_2",
            context_chunk_id="parent_2",
            child_chunk_id="child_2",
            parent_chunk_id="parent_2",
            doc_id="doc_2",
            match_text="低相关证据",
            context_text="未进入最终上下文",
            drop_reason="context_budget",
        )
        return EvidenceBundleSchema(
            task_id=request.task_id,
            run_id=request.run_id,
            status=ExecutionStatus.SUCCESS,
            query=request.query,
            retrieval_trace_id="trace_fixture",
            items=[selected, dropped],
            selected_evidence_ids=["E1"],
            dropped_evidence_ids=["E2"],
            context=RAGContextSchema(
                context_text="父块上下文正文",
                used_context_chunk_ids=["parent_1"],
                matched_chunk_ids=["child_1"],
                used_doc_ids=["doc_1"],
                used_context_chars=7,
                context_item_count=1,
            ),
        )


def _request(query: str = "政务云建设方案") -> RAGToolInputSchema:
    return RAGToolInputSchema(
        task_id="task_langchain_test",
        run_id="run_langchain_test",
        agent_name="LangChainRAGTest",
        query=query,
        need_citation=True,
        max_context_chars=6000,
        max_context_items=3,
    )


def test_langchain_runnable_preserves_evidence_bundle_contract() -> None:
    service = _StubRAGService()
    runnable = build_rag_runnable(service)

    bundle = runnable.invoke(_request())

    assert isinstance(bundle, EvidenceBundleSchema)
    assert bundle.query == "政务云建设方案"
    assert bundle.selected_evidence_ids == ["E1"]
    assert bundle.dropped_evidence_ids == ["E2"]
    assert bundle.retrieval_trace_id == "trace_fixture"
    assert service.requests[0].task_id == "task_langchain_test"


def test_langchain_runnable_accepts_dict_input() -> None:
    service = _StubRAGService()
    runnable = build_rag_runnable(service)

    bundle = runnable.invoke(_request("测试查询").model_dump())

    assert bundle.query == "测试查询"
    assert service.requests[0].query == "测试查询"


def test_langchain_retriever_projects_only_selected_evidence_by_default() -> None:
    service = _StubRAGService()
    retriever = LangChainRAGRetriever(
        rag_service=service,
        request_template=_request("template query"),
    )

    docs = retriever.invoke("新的查询")

    assert len(docs) == 1
    assert docs[0].page_content == "父块上下文正文"
    assert docs[0].metadata["evidence_id"] == "E1"
    assert docs[0].metadata["matched_chunk_id"] == "child_1"
    assert docs[0].metadata["context_chunk_id"] == "parent_1"
    assert docs[0].metadata["match_text"] == "精确命中证据"
    assert docs[0].metadata["retrieval_trace_id"] == "trace_fixture"
    assert service.requests[0].query == "新的查询"
    assert service.requests[0].rewritten_queries == []


def test_langchain_retriever_can_include_dropped_evidence_for_diagnostics() -> None:
    service = _StubRAGService()
    retriever = LangChainRAGRetriever(
        rag_service=service,
        request_template=_request(),
        include_dropped=True,
    )

    docs = retriever.invoke("诊断查询")

    assert [doc.metadata["evidence_id"] for doc in docs] == ["E1", "E2"]
    assert docs[1].metadata["disposition"] == "dropped"
    assert docs[1].metadata["drop_reason"] == "context_budget"


def test_projection_keeps_enterprise_metadata_without_replacing_canonical_bundle() -> None:
    service = _StubRAGService()
    bundle = service.retrieve(_request())

    docs = evidence_bundle_to_documents(bundle)

    assert len(docs) == 1
    assert docs[0].metadata["source_metadata"] == {"source": "fixture"}
    assert docs[0].metadata["lineage"]["schema_version"] == "rag_evidence_lineage_v1"
