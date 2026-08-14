"""用于主链、补充检索和引用冲突测试的确定性 RAG 工具。

它构造真实 ``EvidenceBundleSchema``，而不是只返回一段字符串，因此测试仍经过生产环境
相同的证据、引用、上下文投影和血缘契约。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

from rag.evidence.contract import RAGEvidenceContractBuilder, RAGEvidenceContractReader
from schemas.citation import CitationSchema
from schemas.claim import ClaimType
from schemas.rag import (
    EvidenceStatus,
    RAGToolOutputSchema,
    RAGTraceSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolCallSchema, ToolResultSchema
from contracts.base_tool import BaseTool


class FakeRAGTool(BaseTool):
    """Deterministic RAG tool with opt-in test scenarios.

    The default behavior remains backward-compatible.  Test-only scenarios are
    activated with ``FAKE_RAG_SCENARIO``:

    ``citation_collision``
        Every retrieval returns the local citation id ``C1`` but a different
        source document/chunk.  The document citation registry must remap them
        to globally unique ids.

    ``corrective_retrieval`` / ``business_gate_failure``
        Return scope-specific evidence so recovery lineage can be asserted.
        The corresponding model behavior is controlled by
        ``FAKE_LLM_SCENARIO``.
    """

    tool_name = "FakeRAGTool"
    description = "Mock RAG retrieval tool for testing."

    @staticmethod
    def _safe_key(value: str) -> str:
        """把场景和章节信息转换为可用于模拟文档/Chunk 标识的稳定片段。"""
        text = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value).strip("_")
        return text or "document"

    def run(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """根据测试场景构造一条可追溯证据并返回标准工具结果。

        默认场景返回固定企业 RAG 架构材料；碰撞与恢复场景按检索范围、章节和调用 ID
        生成不同来源，但故意复用局部 citation_id ``C1``，用于验证文档级引用注册表是否
        正确重映射。测试语料由本工具明确声明语义充分，避免通用 Builder 对充分性做推断。
        """
        tool_input: Dict[str, Any] = tool_call.tool_input
        query = str(tool_input.get("query", ""))
        retrieval_mode = tool_input.get("retrieval_mode", "mock")
        scope = str(tool_input.get("retrieval_scope") or "document")
        evidence_requirements = dict(tool_input.get("evidence_requirements") or {})
        section_title = str(
            evidence_requirements.get("section_title") or ""
        ).strip()
        scenario = str(os.getenv("FAKE_RAG_SCENARIO", "default")).strip().lower()

        source_key = "default"
        local_citation_id = "citation_mock_001"
        title = "Mock 企业级 RAG-Agent 架构说明"
        section = "总体架构"
        mock_evidence = (
            "建设内容包括企业知识库构建、RAG 检索、Agent 工作流、引用追溯和运行数据沉淀。"
            "典型方案可采用分层架构，组件可包括 SupervisorAgent、SubAgent、ToolRegistry、"
            "RAGTool、ModelGateway、RunTrace 和 DataCapture 等模块。"
            "安全设计应满足法律法规和合规要求，并包含访问控制、操作审计及敏感数据保护。"
        )

        if scenario in {
            "citation_collision",
            "corrective_retrieval",
            "business_gate_failure",
        }:
            source_key = self._safe_key(
                f"{scope}_{section_title or 'document'}_{tool_call.tool_call_id}"
            )
            # Deliberately reuse a local id across independent retrieval calls.
            local_citation_id = "C1"
            title = f"Mock {section_title or '政务云'}证据-{scope}"
            section = section_title or "总体架构"
            if scope == "recovery":
                mock_evidence = (
                    "政务云安全设计应满足等级保护和合规要求，并采用统一身份认证、最小权限访问控制、"
                    "传输与存储加密、敏感数据脱敏、完整操作审计、输入校验和接口鉴权，并保留可追溯记录。"
                )
            elif section_title == "安全设计":
                mock_evidence = (
                    "安全设计应满足等级保护要求，并覆盖身份认证、访问控制、最小权限、数据加密、"
                    "敏感数据保护、日志审计、输入校验和接口安全。"
                )
            elif section_title == "技术方案":
                mock_evidence = (
                    "技术方案应明确总体架构、网络架构、数据架构、接口边界和部署方式。"
                )
            elif section_title == "建设内容":
                mock_evidence = (
                    "建设内容应覆盖平台能力、功能模块、数据治理、运维体系和服务内容。"
                )
            else:
                mock_evidence = (
                    "政务云建设方案应明确项目目标、建设范围、实施边界和验收要求。"
                )

        doc_id = f"mock_doc_{source_key}"
        child_chunk_id = f"mock_chunk_{source_key}"
        parent_chunk_id = f"mock_parent_{source_key}"

        retrieved_chunk = RetrievedChunkSchema(
            rank=1,
            score=0.99,
            score_type="mock_score",
            rerank_score=0.98,
            rerank_score_type="mock_rerank_score",
            matched_chunk_id=child_chunk_id,
            context_chunk_id=parent_chunk_id,
            child_chunk_id=child_chunk_id,
            parent_chunk_id=parent_chunk_id,
            doc_id=doc_id,
            matched_granularity="chunk",
            context_granularity="parent",
            match_text=mock_evidence,
            context_text=mock_evidence,
            title=title,
            section=section,
            page_start=1,
            page_end=1,
            retrieval_sources=["mock_vector", "mock_keyword"],
            metadata={
                "source_type": "mock",
                "kb_id": "mock_kb_001",
                "retrieval_scope": scope,
                "section_title": section_title,
                "scenario": scenario,
            },
            extra={"is_mock": True, "scenario": scenario},
        )

        citation = CitationSchema(
            citation_id=local_citation_id,
            source_type="document",
            doc_id=doc_id,
            source_document_id=doc_id,
            parent_chunk_id=parent_chunk_id,
            child_chunk_id=child_chunk_id,
            chunk_id=child_chunk_id,
            title=title,
            section=section,
            page_start=1,
            page_end=1,
            quote_text=retrieved_chunk.match_text,
            summary=f"{title}引用。",
            confidence=0.99,
            extra={
                "is_mock": True,
                "scenario": scenario,
                "retrieval_scope": scope,
                "prompt_visible_text": retrieved_chunk.context_text,
            },
        )

        rag_trace = RAGTraceSchema(
            retrieval_mode=retrieval_mode,
            query=query,
            rewritten_queries=[],
            embedding_model="mock_embedding",
            embedding_version="v1.0",
            reranker_model="mock_reranker",
            reranker_version="v1.0",
            index_name="mock_index",
            index_version="v1.0",
            vector_db="mock",
            dense_top_k=tool_input.get("dense_top_k", 10),
            keyword_top_k=tool_input.get("keyword_top_k", 10),
            candidate_top_k=tool_input.get("candidate_top_k", 10),
            rerank_top_k=tool_input.get("rerank_top_k", 5),
            max_context_chars=tool_input.get("max_context_chars", 6000),
            retrieved_count=1,
            reranked_count=1,
            context_item_count=1,
            latency_ms=0,
            extra={
                "tool_name": self.tool_name,
                "is_mock": True,
                "scenario": scenario,
                "retrieval_scope": scope,
                "section_title": section_title,
            },
        )

        evidence = RAGEvidenceContractBuilder.build(
            query=query,
            rewritten_queries=[],
            selected_chunks=[retrieved_chunk],
            dropped_chunks=[],
            citations=[citation],
            trace=rag_trace,
            max_context_chars=int(tool_input.get("max_context_chars", 6000)),
            extra={
                "is_mock": True,
                "scenario": scenario,
                "retrieval_scope": scope,
                "section_title": section_title,
            },
        )
        # The fake corpus is intentionally authored to answer its fake query.
        # Unlike the generic contract builder, this deterministic test adapter
        # can make that semantic assertion explicitly.
        evidence.assessment = evidence.assessment.model_copy(
            update={
                "assessment_state": "completed",
                "status": EvidenceStatus.SUFFICIENT,
                "coverage_ratio": 1.0,
                "required_topics": [
                    section_title or "企业级 RAG-Agent 架构"
                ],
                "covered_topics": [
                    section_title or "企业级 RAG-Agent 架构"
                ],
                "supported_claim_types": [
                    ClaimType.GENERAL_METHOD,
                    ClaimType.TYPICAL_ARCHITECTURE,
                    ClaimType.REFERENCE_CASE,
                    ClaimType.CURRENT_PROJECT_FACT,
                    ClaimType.CURRENT_PROJECT_COST,
                    ClaimType.CURRENT_PROJECT_QUANTITY,
                    ClaimType.CURRENT_PROJECT_SCHEDULE,
                    ClaimType.CURRENT_PROJECT_CONFIGURATION,
                    ClaimType.POLICY_REQUIREMENT,
                    ClaimType.TECHNICAL_SPECIFICATION,
                    ClaimType.PROCUREMENT_REQUIREMENT,
                    ClaimType.TEMPLATE_STRUCTURE,
                    ClaimType.STYLE_GUIDANCE,
                ],
                "authoritative_evidence_present": True,
                "prompt_evidence_source": "fake_fixture_selected_evidence",
            }
        )
        evidence.assessment.reason_codes = ["fake_fixture_semantically_sufficient"]
        evidence.extra = {
            **dict(evidence.extra or {}),
            "prompt_evidence_source": "fake_fixture_selected_evidence",
            "prompt_evidence_contract": {
                "schema_version": "prompt_evidence_contract_v1",
                "source": "fake_fixture_selected_evidence",
                "contract_missing": False,
                "candidate_count": 1,
                "candidate_errors": [],
            },
        }
        rag_context, projected_chunks, projected_citations = (
            RAGEvidenceContractReader.projections(evidence)
        )
        rag_output = RAGToolOutputSchema(
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            status="success",
            query=query,
            rewritten_queries=[],
            evidence=evidence,
            retrieved_chunks=projected_chunks,
            context=rag_context,
            citations=projected_citations,
            trace=rag_trace,
            answer=None,
            extra={
                "is_mock": True,
                "scenario": scenario,
                "retrieval_scope": scope,
                "section_title": section_title,
                "contract": "RAGToolOutputSchema",
                "evidence_contract": "rag_evidence_contract_v1",
            },
        )

        return ToolResultSchema(
            tool_call_id=tool_call.tool_call_id,
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            tool_name=self.tool_name,
            success=True,
            result=rag_output.model_dump(),
            created_at=tool_call.created_at,
            metadata={
                "output_schema": "RAGToolOutputSchema",
                "output_schema_version": rag_output.schema_version,
                "scenario": scenario,
                "retrieval_scope": scope,
            },
            extra={"is_mock": True, "scenario": scenario},
        )
