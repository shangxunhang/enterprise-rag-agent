"""Evidence retrieval and section-aware citation normalization."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from observability.trace_summary import canonical_sha256

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema
from rag.evidence.contract import RAGEvidenceContractBuilder, RAGEvidenceContractReader
from schemas.rag import (
    RAGContextSchema,
    RAGEvidenceContractSchema,
    RAGTraceSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolCallSchema, ToolResultSchema
from .base import RuntimeBoundService


_MARKER_PATTERN = re.compile(r"\[([^\[\]]+)\]")


class DocumentCitationRegistry:
    """Allocate stable document-wide citation ids across multiple RAG calls."""

    def __init__(self) -> None:
        self._by_key: Dict[str, CitationSchema] = {}
        self._ordered: List[CitationSchema] = []

    @staticmethod
    def _identity(citation: CitationSchema) -> str:
        payload = {
            "source_document_id": citation.source_document_id or citation.doc_id,
            "file_id": citation.file_id,
            "parent_chunk_id": citation.parent_chunk_id,
            "child_chunk_id": citation.child_chunk_id,
            "chunk_id": citation.chunk_id,
            "table_id": citation.table_id,
            "row_ids": list(citation.row_ids),
            "page_start": citation.page_start,
            "page_end": citation.page_end,
            "quote_text": " ".join((citation.quote_text or "").split()),
            "title": citation.title,
            "section": citation.section,
        }
        if not any(value for value in payload.values()):
            payload["original_citation_id"] = citation.citation_id
        return canonical_sha256(payload)

    def register(
        self,
        citations: Iterable[CitationSchema],
        *,
        scope: str,
        query: str,
    ) -> Tuple[List[CitationSchema], Dict[str, str]]:
        remapped: List[CitationSchema] = []
        id_map: Dict[str, str] = {}
        for citation in citations:
            key = self._identity(citation)
            existing = self._by_key.get(key)
            if existing is None:
                citation_id = f"C{len(self._ordered) + 1}"
                extra = dict(citation.extra or {})
                extra.update(
                    {
                        "original_citation_id": citation.citation_id,
                        "retrieval_scopes": [scope],
                        "retrieval_queries": [query],
                        "citation_identity_sha256": key,
                    }
                )
                existing = citation.model_copy(
                    update={"citation_id": citation_id, "extra": extra}
                )
                self._by_key[key] = existing
                self._ordered.append(existing)
            else:
                extra = dict(existing.extra or {})
                scopes = list(extra.get("retrieval_scopes") or [])
                queries = list(extra.get("retrieval_queries") or [])
                if scope not in scopes:
                    scopes.append(scope)
                if query not in queries:
                    queries.append(query)
                updated = existing.model_copy(
                    update={
                        "extra": {
                            **extra,
                            "retrieval_scopes": scopes,
                            "retrieval_queries": queries,
                        }
                    }
                )
                self._by_key[key] = updated
                index = next(
                    i for i, item in enumerate(self._ordered)
                    if item.citation_id == existing.citation_id
                )
                self._ordered[index] = updated
                existing = updated
            id_map[citation.citation_id] = existing.citation_id
            remapped.append(existing)
        return remapped, id_map

    def all(self) -> List[CitationSchema]:
        return list(self._ordered)


class SchemeEvidenceService(RuntimeBoundService):
    def _call_rag_tool(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        *,
        query: Optional[str] = None,
        scope: str = "document",
        section_id: Optional[str] = None,
        section_title: Optional[str] = None,
        call_suffix: Optional[str] = None,
    ) -> Optional[ToolResultSchema]:
        if self.tool_executor is None:
            return None

        requirements = project_input.generation_requirements
        resolved_query = str(query or project_input.user_query).strip()
        source_doc_ids = list(dict.fromkeys(
            doc_id
            for material in project_input.source_materials
            for doc_id in material.doc_ids
            if doc_id
        ))
        source_file_ids = list(dict.fromkeys(
            file_id
            for material in project_input.source_materials
            for file_id in material.file_ids
            if file_id
        ))
        source_kb_ids = list(dict.fromkeys(
            str(kb_id)
            for material in project_input.source_materials
            for kb_id in (
                material.metadata.get("kb_ids")
                or ([material.metadata.get("kb_id")] if material.metadata.get("kb_id") else [])
            )
            if str(kb_id).strip()
        ))
        task_kb_ids = (shared_state.task or {}).get("kb_ids") or []
        raw_suffix = call_suffix or scope
        safe_suffix = re.sub(r"[^0-9A-Za-z_\-]+", "_", raw_suffix).strip("_") or "document"
        tool_call = ToolCallSchema(
            tool_call_id=f"tool_call_{shared_state.run_id}_rag_{safe_suffix}",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            tool_name=self.rag_tool_name,
            tool_input={
                "query": resolved_query,
                "kb_ids": list(dict.fromkeys([*task_kb_ids, *source_kb_ids])),
                "retrieval_mode": self.rag_retrieval_mode,
                "retrieval_strategy": self.rag_retrieval_mode,
                "mode": "retrieve_only",
                "generate_answer": False,
                "need_citation": requirements.need_citation,
                "max_context_chars": requirements.max_context_chars,
                "max_context_items": 5,
                "dense_top_k": 10,
                "keyword_top_k": 10,
                "candidate_top_k": 10,
                "rerank_top_k": 5,
                "filters": {
                    "tenant_id": project_input.tenant_id,
                    "doc_ids": source_doc_ids,
                    "file_ids": source_file_ids,
                },
                "extra_metadata": {
                    "task_type": project_input.task_type,
                    "retrieval_scope": scope,
                    "section_id": section_id,
                    "section_title": section_title,
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
                },
            },
            caller_agent=self.agent_name,
            step_name=f"evidence_retrieval:{scope}",
            created_at=shared_state.updated_at or shared_state.created_at,
            metadata={
                "expected_output_schema": "RAGToolOutputSchema",
                "retrieval_scope": scope,
                "section_id": section_id,
                "section_title": section_title,
            },
        )
        result = self.tool_executor.execute(tool_call)
        SharedStateWriter().set_tool_result(
            shared_state, tool_call.tool_call_id, result.model_dump()
        )
        return result

    @staticmethod
    def _build_section_query(
        project_input: ProjectInputSchema,
        section_title: str,
        *,
        recovery: bool = False,
    ) -> str:
        placeholder_values = {
            "",
            "unspecified",
            "unknown",
            "none",
            "n/a",
            "未指定",
            "待定",
            "默认项目",
        }

        def usable_label(value: Any) -> Optional[str]:
            text = str(value or "").strip()
            if text.lower() in placeholder_values:
                return None
            return text or None

        project_label = next(
            (
                label
                for label in (
                    usable_label(project_input.project_type),
                    usable_label(project_input.project_name),
                )
                if label
            ),
            None,
        )
        if not project_label:
            user_query = str(project_input.user_query or "").strip()
            match = re.search(
                r"(?:生成|编制|撰写|制作|输出)?(?:一个|一份)?(.{2,40}?)(?:的)?建设方案",
                user_query,
            )
            if match:
                project_label = match.group(1).strip(" ：:，,。的")
        if not project_label:
            project_label = usable_label(project_input.output_schema.document_title)
        project_label = re.sub(r"(?:建设)?方案$", "", project_label or "").strip()
        project_label = project_label or "政企项目"
        domain_hints = {
            "项目概述": "建设背景、现状、建设范围、服务对象和总体依据",
            "建设目标": "总体目标、业务目标、能力目标、预期效果和建设原则",
            "建设内容": "建设任务、平台能力、功能模块、数据治理、运维和服务内容",
            "技术方案": "总体架构、系统架构、网络架构、数据架构、接口和部署方式",
            "资源配置": "计算、存储、网络、安全、容灾、容量规划和资源配置原则",
            "安全设计": "身份认证、访问控制、最小权限、数据加密、敏感数据保护、日志审计、等级保护、输入校验和接口安全",
            "实施与验收": "实施步骤、里程碑、迁移、联调、试运行、验收指标和交付物",
            "待补充事项": "项目输入缺口、待确认参数、边界条件、风险和人工补充材料",
        }
        hints = domain_hints.get(section_title, f"与“{section_title}”直接相关的要求、措施和依据")
        recovery_clause = (
            "请优先返回能够直接支撑章节确定性陈述、可绑定引用的原文证据。"
            if recovery
            else "请返回与本章节直接相关、可用于方案编写和引用的依据。"
        )
        return (
            f"{project_label}建设方案的“{section_title}”章节：{hints}。"
            f"{recovery_clause}"
        )

    @staticmethod
    def _replace_markers(text: str, id_map: Dict[str, str]) -> str:
        if not text or not id_map:
            return text

        def replace(match: re.Match[str]) -> str:
            old = match.group(1)
            return f"[{id_map.get(old, old)}]"

        return _MARKER_PATTERN.sub(replace, text)

    @classmethod
    def _remap_bundle_citations(
        cls,
        *,
        context: RAGContextSchema,
        chunks: List[RetrievedChunkSchema],
        citations: List[CitationSchema],
        normalized: Dict[str, Any],
        registry: DocumentCitationRegistry,
        scope: str,
        query: str,
    ) -> Tuple[RAGContextSchema, List[RetrievedChunkSchema], List[CitationSchema], Dict[str, Any]]:
        remapped_citations, id_map = registry.register(
            citations,
            scope=scope,
            query=query,
        )
        remapped_citations = list(
            {item.citation_id: item for item in remapped_citations}.values()
        )
        remapped_context_text = cls._replace_markers(context.context_text, id_map)
        remapped_context = context.model_copy(
            update={
                "context_text": remapped_context_text,
                "used_context_chars": len(remapped_context_text),
            }
        )
        raw_contract = (context.extra or {}).get("evidence_contract")
        if isinstance(raw_contract, dict):
            contract_payload = dict(raw_contract)
            contract_payload["citations"] = [item.model_dump() for item in remapped_citations]
            items = []
            for raw_item in contract_payload.get("items") or []:
                item = dict(raw_item)
                item["citation_ids"] = list(dict.fromkeys(
                    id_map.get(value, value) for value in item.get("citation_ids") or []
                ))
                items.append(item)
            contract_payload["items"] = items
            raw_context = dict(contract_payload.get("context") or {})
            raw_context["context_text"] = cls._replace_markers(
                str(raw_context.get("context_text") or ""), id_map
            )
            raw_context["used_context_chars"] = len(raw_context["context_text"])
            contract_payload["context"] = raw_context
            contract_payload.setdefault("extra", {})
            contract_payload["extra"] = {
                **dict(contract_payload.get("extra") or {}),
                "citation_registry_scope": scope,
                "citation_id_map": id_map,
            }
            contract = RAGEvidenceContractSchema.model_validate(contract_payload)
            remapped_context = contract.context.model_copy(
                update={
                    "extra": {
                        **dict(remapped_context.extra or {}),
                        "evidence_contract": contract.model_dump(),
                        "evidence_contract_sha256": canonical_sha256(contract.model_dump()),
                        "lineage": contract.lineage.model_dump(),
                        "retrieval_scope": scope,
                    }
                }
            )
            normalized = dict(normalized)
            normalized["evidence"] = contract.model_dump()
        else:
            remapped_context.extra = {
                **dict(remapped_context.extra or {}),
                "retrieval_scope": scope,
                "citation_id_map": id_map,
            }
        normalized = dict(normalized)
        normalized["context"] = remapped_context.model_dump()
        normalized["citations"] = [item.model_dump() for item in remapped_citations]
        normalized["retrieved_chunks"] = [item.model_dump() for item in chunks]
        normalized.setdefault("extra", {})
        normalized["extra"] = {
            **dict(normalized.get("extra") or {}),
            "retrieval_scope": scope,
            "citation_id_map": id_map,
        }
        return remapped_context, chunks, remapped_citations, normalized

    @staticmethod
    def _extract_rag_output(
        shared_state: SharedStateSchema,
        result: Optional[ToolResultSchema],
    ) -> Tuple[RAGContextSchema, List[RetrievedChunkSchema], List[CitationSchema], Dict[str, Any]]:
        """Consume the canonical Step 12 evidence contract."""

        payload = result.result if result and result.success else {}
        payload = payload or {}
        raw_contract = payload.get("evidence")
        if isinstance(raw_contract, dict):
            contract = RAGEvidenceContractSchema.model_validate(raw_contract)
            context, chunks, citations = RAGEvidenceContractReader.projections(contract)
            context.extra = {
                **dict(context.extra or {}),
                "evidence_contract": contract.model_dump(),
                "evidence_contract_sha256": canonical_sha256(contract.model_dump()),
                "lineage": contract.lineage.model_dump(),
            }
            normalized = dict(payload)
            normalized["evidence"] = contract.model_dump()
            normalized["context"] = context.model_dump()
            normalized["retrieved_chunks"] = [item.model_dump() for item in chunks]
            normalized["citations"] = [item.model_dump() for item in citations]
            normalized.setdefault("schema_version", "rag_tool_output_v1")
            normalized.setdefault("task_id", shared_state.task_id)
            normalized.setdefault("run_id", shared_state.run_id)
            normalized.setdefault("status", "success")
            return context, chunks, citations, normalized

        chunks = [
            RetrievedChunkSchema.model_validate(item)
            for item in (payload.get("retrieved_chunks") or [])
            if isinstance(item, dict)
        ]
        raw_citations: list[CitationSchema] = []
        for index, item in enumerate(payload.get("citations") or [], start=1):
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            normalized_item.setdefault(
                "citation_id", f"citation_{shared_state.run_id}_{index:03d}"
            )
            normalized_item.setdefault("source_type", "document")
            normalized_item.setdefault(
                "source_document_id", normalized_item.get("doc_id")
            )
            raw_citations.append(CitationSchema.model_validate(normalized_item))

        if not raw_citations:
            for index, chunk in enumerate(chunks, start=1):
                raw_citations.append(
                    CitationSchema(
                        citation_id=f"retrieved_chunk_{index:03d}",
                        source_type="document",
                        doc_id=chunk.doc_id,
                        source_document_id=chunk.doc_id,
                        parent_chunk_id=chunk.parent_chunk_id,
                        child_chunk_id=chunk.child_chunk_id,
                        chunk_id=chunk.matched_chunk_id or chunk.context_chunk_id,
                        title=chunk.title,
                        section=chunk.section,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        quote_text=(chunk.match_text or chunk.context_text),
                        confidence=(
                            chunk.rerank_score
                            if chunk.rerank_score is not None
                            else chunk.score
                        ),
                        extra={"rebuilt_from_retrieved_chunk": True},
                    )
                )

        raw_context = payload.get("context") or {}
        max_context_chars = int(raw_context.get("max_context_chars") or 6000)
        raw_trace = payload.get("trace")
        trace = (
            RAGTraceSchema.model_validate(raw_trace)
            if isinstance(raw_trace, dict)
            else None
        )
        contract = RAGEvidenceContractBuilder.build(
            query=str(payload.get("query") or ""),
            rewritten_queries=payload.get("rewritten_queries") or [],
            selected_chunks=chunks,
            dropped_chunks=[],
            citations=raw_citations,
            trace=trace,
            max_context_chars=max_context_chars,
            extra={
                "compatibility_upgrade": True,
                "upgraded_by": "SchemeEvidenceService",
            },
        )
        context, projected_chunks, citations = RAGEvidenceContractReader.projections(contract)
        context.extra = {
            **dict(context.extra or {}),
            "evidence_contract": contract.model_dump(),
            "evidence_contract_sha256": canonical_sha256(contract.model_dump()),
            "lineage": contract.lineage.model_dump(),
        }
        normalized = dict(payload)
        normalized.setdefault("schema_version", "rag_tool_output_v1")
        normalized.setdefault("task_id", shared_state.task_id)
        normalized.setdefault("run_id", shared_state.run_id)
        normalized.setdefault(
            "status", "success" if result and result.success else "failed"
        )
        normalized["evidence"] = contract.model_dump()
        normalized["context"] = context.model_dump()
        normalized["retrieved_chunks"] = [item.model_dump() for item in projected_chunks]
        normalized["citations"] = [item.model_dump() for item in citations]
        return context, projected_chunks, citations, normalized
