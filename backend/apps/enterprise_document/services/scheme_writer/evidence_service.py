"""方案生成中的 RAG 调用与标准证据投影边界。

本服务只构造 ``RAGToolInputSchema``、调用 ``RAGServicePort`` 并返回 canonical
``EvidenceBundleSchema``。章节 Query、文档级引用注册与 remap、旧 ToolResult 升级均由
独立协作者负责，防止检索、授权和兼容逻辑重新聚合成巨型服务。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from observability.trace_summary import canonical_sha256

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from contracts.rag import RAGServicePort
from model_gateway.model_contract import ModelRole
from rag.evidence.contract import RAGEvidenceContractReader
from schemas.citation import CitationSchema
from schemas.rag import (
    EvidenceBundleSchema,
    RAGContextSchema,
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolResultSchema

from .legacy_evidence_adapter import LegacyEvidenceAdapter


class SchemeEvidenceService:
    """执行一次受授权范围约束的检索，并向调用方暴露统一证据契约。"""

    def __init__(
        self,
        *,
        rag_service: RAGServicePort | None,
        agent_name: str,
        model_gateway: Any | None = None,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
    ) -> None:
        """注入 RAG 端口、调用 Agent 名称、模型能力查询和可信检索授权范围。

        ``retrieval_access_scope`` 必须来自认证后的应用/安全边界；ProjectInput 只能提供
        缩小范围的材料和 KB 请求，不能被提升为授权事实。
        """
        self.rag_service = rag_service
        self.agent_name = agent_name
        self.model_gateway = model_gateway
        # The composition root injects this object from an authenticated
        # application/security boundary. Business input is never promoted into
        # this authorization truth.
        self.retrieval_access_scope = retrieval_access_scope

    def retrieve(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        *,
        query: Optional[str] = None,
        scope: str = "document",
        section_id: Optional[str] = None,
        section_title: Optional[str] = None,
        call_suffix: Optional[str] = None,
    ) -> Optional[EvidenceBundleSchema]:
        """构造并执行一次文档、章节或恢复范围的 RAG 请求。

        方法汇总 ProjectInput 的文件、文档和知识库定位信息，校验租户与可信授权范围一致，
        再结合目标模型安全上下文窗口计算证据预算。``model_gateway`` 缺失时使用显式保守
        默认值，而不是虚构模型能力。未配置 RAG 服务时返回 ``None``，由上层决定降级策略。
        """

        if self.rag_service is None:
            return None

        requirements = project_input.generation_requirements
        resolved_query = str(query or project_input.user_query).strip()
        source_doc_ids = list(
            dict.fromkeys(
                doc_id
                for material in project_input.source_materials
                for doc_id in material.doc_ids
                if doc_id
            )
        )
        source_file_ids = list(
            dict.fromkeys(
                file_id
                for material in project_input.source_materials
                for file_id in material.file_ids
                if file_id
            )
        )
        source_kb_ids = list(
            dict.fromkeys(
                str(kb_id)
                for material in project_input.source_materials
                for kb_id in (
                    material.metadata.get("kb_ids")
                    or (
                        [material.metadata.get("kb_id")]
                        if material.metadata.get("kb_id")
                        else []
                    )
                )
                if str(kb_id).strip()
            )
        )
        task_kb_ids = (shared_state.task or {}).get("kb_ids") or []
        requested_kb_ids = list(dict.fromkeys([*task_kb_ids, *source_kb_ids]))
        access_scope = self.retrieval_access_scope
        if (
            access_scope is not None
            and project_input.tenant_id != access_scope.tenant_id
        ):
            raise ValueError(
                "ProjectInput tenant_id conflicts with authenticated retrieval tenant"
            )
        raw_suffix = call_suffix or scope
        safe_suffix = (
            re.sub(r"[^0-9A-Za-z_\-]+", "_", raw_suffix).strip("_")
            or "document"
        )
        retrieval_trace_id = f"rag_{shared_state.run_id}_{safe_suffix}"
        capability_plan: Dict[str, Any] = {}
        resolve_capabilities = getattr(self.model_gateway, "routing_capabilities", None)
        configured_context_limit = requirements.extra.get("max_input_context_tokens")
        if callable(resolve_capabilities):
            capability_plan = dict(
                resolve_capabilities(model_role=ModelRole.SECTION_GENERATION.value)
            )
            safe_context_window = int(capability_plan["safe_context_window"])
            model_context_window = (
                min(safe_context_window, int(configured_context_limit))
                if configured_context_limit is not None
                else safe_context_window
            )
            safe_output_tokens = int(capability_plan["safe_max_output_tokens"])
        else:
            model_context_window = int(configured_context_limit or 8192)
            safe_output_tokens = int(requirements.max_tokens_per_section)
        requested_reserved_tokens = int(
            requirements.extra.get(
                "prompt_reserved_tokens",
                requirements.max_tokens_per_section + 512,
            )
        )
        prompt_reserved_tokens = min(
            requested_reserved_tokens,
            safe_output_tokens + 512,
        )
        extra_metadata = {
            "task_type": project_input.task_type,
            "retrieval_scope": scope,
            "section_id": section_id,
            "section_title": section_title,
            "context_requirements": {
                "model_context_window": model_context_window,
                "prompt_reserved_tokens": prompt_reserved_tokens,
                "section_token_budget": int(
                    requirements.extra.get("max_evidence_context_tokens", 4096)
                ),
                "max_evidence_items": int(
                    requirements.extra.get("max_evidence_items", 5)
                ),
                "max_context_chars": int(requirements.max_context_chars),
                "model_capability_plan": capability_plan,
            },
            "document_context": {
                "project_name": project_input.project_name,
                "project_type": project_input.project_type,
                "document_title": project_input.output_schema.document_title,
                "required_sections": list(
                    project_input.generation_requirements.required_sections
                    or project_input.output_schema.required_sections
                ),
                "citation_required_sections": list(
                    project_input.generation_requirements.citation_required_sections
                ),
                "target_documents": list(project_input.target_documents),
                "target_templates": list(project_input.target_templates),
            },
        }
        request = RAGToolInputSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            query=resolved_query,
            kb_ids=requested_kb_ids,
            access_scope=access_scope,
            filters={
                "tenant_id": project_input.tenant_id,
                "doc_ids": source_doc_ids,
                "file_ids": source_file_ids,
            },
            need_citation=requirements.need_citation,
            max_context_chars=requirements.max_context_chars,
            max_context_items=5,
            extra={
                "retrieval_trace_id": retrieval_trace_id,
                "retrieval_scope": scope,
                "extra_metadata": extra_metadata,
            },
        )
        return self.rag_service.retrieve(request)

    @staticmethod
    def extract_rag_output(
        shared_state: SharedStateSchema,
        result: Optional[EvidenceBundleSchema | ToolResultSchema],
    ) -> Tuple[
        RAGContextSchema,
        List[RetrievedChunkSchema],
        List[CitationSchema],
        Dict[str, Any],
    ]:
        """Project canonical evidence; delegate legacy ToolResult upgrades."""

        if isinstance(result, EvidenceBundleSchema):
            contract = result
            context, chunks, citations = RAGEvidenceContractReader.projections(contract)
            context.extra = {
                **dict(context.extra or {}),
                "evidence_contract": contract.model_dump(),
                "evidence_contract_sha256": canonical_sha256(contract.model_dump()),
                "lineage": contract.lineage.model_dump(),
            }
            normalized = {
                "schema_version": "evidence_bundle_v1",
                "task_id": contract.task_id or shared_state.task_id,
                "run_id": contract.run_id or shared_state.run_id,
                "status": contract.status.value,
                "query": contract.query,
                "rewritten_queries": list(contract.rewritten_queries),
                "evidence": contract.model_dump(),
                "context": context.model_dump(),
                "retrieved_chunks": [item.model_dump() for item in chunks],
                "citations": [item.model_dump() for item in citations],
                "trace": contract.trace.model_dump() if contract.trace else None,
                "extra": dict(contract.extra or {}),
            }
            return context, chunks, citations, normalized

        return LegacyEvidenceAdapter.extract(shared_state, result)
