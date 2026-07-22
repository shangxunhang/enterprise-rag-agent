# =============================================================================
# 中文阅读说明：方案生成中的证据服务。
# 只负责构造 RAG 请求、调用 RAGService，并把当前结果投影为标准 Evidence 视图。
# Query 构造、文档级引用注册/remap、旧 ToolResult 兼容分别由独立协作者负责。
# =============================================================================
"""Evidence retrieval and canonical evidence projection for scheme generation."""

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
    """Invoke RAG and expose one canonical evidence projection to callers."""

    def __init__(
        self,
        *,
        rag_service: RAGServicePort | None,
        agent_name: str,
        model_gateway: Any | None = None,
    ) -> None:
        self.rag_service = rag_service
        self.agent_name = agent_name
        self.model_gateway = model_gateway

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
        """Build one RAG request and execute it through the configured service."""

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
        effective_kb_ids = list(dict.fromkeys([*task_kb_ids, *source_kb_ids]))
        access_scope = (
            RetrievalAccessScopeSchema(
                tenant_id=project_input.tenant_id,
                authorized_kb_ids=effective_kb_ids,
                allowed_file_ids=source_file_ids,
                allowed_doc_ids=source_doc_ids,
            )
            if effective_kb_ids
            else None
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
            kb_ids=effective_kb_ids,
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
