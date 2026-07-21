# =============================================================================
# 中文阅读说明：方案生成中的证据服务：构造 RAGTool 请求、调用检索工具并把结果转换为章节可用证据。
# 主要定义：DocumentCitationRegistry、SchemeEvidenceService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Evidence retrieval and section-aware citation normalization."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from observability.trace_summary import canonical_sha256

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from contracts.rag import RAGServicePort
from schemas.citation import CitationSchema
from rag.evidence.contract import RAGEvidenceContractBuilder, RAGEvidenceContractReader
from schemas.rag import (
    RAGContextSchema,
    EvidenceBundleSchema,
    RAGEvidenceContractSchema,
    RAGToolInputSchema,
    RAGTraceSchema,
    RetrievalAccessScopeSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolResultSchema


_MARKER_PATTERN = re.compile(r"\[([^\[\]]+)\]")


# 阅读注释（类）：封装 文档 引用 注册表，集中封装相关状态、依赖和行为。
class DocumentCitationRegistry:
    """Allocate stable document-wide citation ids across multiple RAG calls."""

    # 阅读注释（函数）：初始化 DocumentCitationRegistry，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 DocumentCitationRegistry，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self._by_key: Dict[str, CitationSchema] = {}
        self._ordered: List[CitationSchema] = []

    # 阅读注释（函数）：处理 identity 相关逻辑。
    @staticmethod
    def _identity(citation: CitationSchema) -> str:
        """处理 identity 相关逻辑。

        参数:
            citation: 引用，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：list, join, split, any, payload.values, canonical_sha256。
        """
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

    # 阅读注释（函数）：注册 DocumentCitationRegistry。
    def register(
        self,
        citations: Iterable[CitationSchema],
        *,
        scope: str,
        query: str,
    ) -> Tuple[List[CitationSchema], Dict[str, str]]:
        """注册 DocumentCitationRegistry。

        参数:
            citations: 引用信息集合。
            scope: scope，具体约束请结合类型标注和调用方确认。
            query: 当前检索或生成查询。

        返回:
            Tuple[List[CitationSchema], Dict[str, str]]

        阅读提示:
            主要直接调用：self._identity, self._by_key.get, len, dict, extra.update, citation.model_copy, self._ordered.append, list。
        """
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

    # 阅读注释（函数）：处理 all 相关逻辑。
    def all(self) -> List[CitationSchema]:
        """处理 all 相关逻辑。

        返回:
            List[CitationSchema]

        阅读提示:
            主要直接调用：list。
        """
        return list(self._ordered)


# 阅读注释（类）：封装 scheme 证据 服务，封装一组可复用的业务能力。
class SchemeEvidenceService:
    def __init__(
        self,
        *,
        rag_service: RAGServicePort | None,
        agent_name: str,
    ) -> None:
        self.rag_service = rag_service
        self.agent_name = agent_name
    """封装 scheme 证据 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：处理 call RAG 工具 相关逻辑。
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
    ) -> Optional[EvidenceBundleSchema]:
        """处理 call RAG 工具 相关逻辑。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。
            project_input: 规范化后的项目输入。
            query: 当前检索或生成查询。
            scope: scope，具体约束请结合类型标注和调用方确认。
            section_id: 章节 标识，具体约束请结合类型标注和调用方确认。
            section_title: 章节 title，具体约束请结合类型标注和调用方确认。
            call_suffix: call suffix，具体约束请结合类型标注和调用方确认。

        返回:
            Optional[ToolResultSchema]

        阅读提示:
            主要直接调用：strip, str, list, dict.fromkeys, material.metadata.get, get, re.sub, ToolCallSchema。
        """
        if self.rag_service is None:
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
        extra_metadata = {
            "task_type": project_input.task_type,
            "retrieval_scope": scope,
            "section_id": section_id,
            "section_title": section_title,
            "context_requirements": {
                "model_context_window": int(
                    requirements.extra.get("max_input_context_tokens", 8192)
                ),
                "prompt_reserved_tokens": int(
                    requirements.extra.get(
                        "prompt_reserved_tokens",
                        requirements.max_tokens_per_section + 512,
                    )
                ),
                "section_token_budget": int(
                    requirements.extra.get("max_evidence_context_tokens", 4096)
                ),
                "max_evidence_items": int(
                    requirements.extra.get("max_evidence_items", 5)
                ),
                "max_context_chars": int(requirements.max_context_chars),
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

    # 阅读注释（函数）：构建 章节 查询。
    @staticmethod
    def _build_section_query(
        project_input: ProjectInputSchema,
        section_title: str,
        *,
        recovery: bool = False,
    ) -> str:
        """构建 章节 查询。

        参数:
            project_input: 规范化后的项目输入。
            section_title: 章节 title，具体约束请结合类型标注和调用方确认。
            recovery: recovery，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：next, usable_label, strip, str, re.search, match.group, re.sub, domain_hints.get。
        """
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

        # 阅读注释（函数）：处理 usable label 相关逻辑。
        def usable_label(value: Any) -> Optional[str]:
            """处理 usable label 相关逻辑。

            参数:
                value: value，具体约束请结合类型标注和调用方确认。

            返回:
                Optional[str]

            阅读提示:
                主要直接调用：strip, str, text.lower。
            """
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

    # 阅读注释（函数）：处理 replace markers 相关逻辑。
    @staticmethod
    def _replace_markers(text: str, id_map: Dict[str, str]) -> str:
        """处理 replace markers 相关逻辑。

        参数:
            text: 待处理文本。
            id_map: 标识 map，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：_MARKER_PATTERN.sub。
        """
        if not text or not id_map:
            return text

        # 阅读注释（函数）：处理 replace 相关逻辑。
        def replace(match: re.Match[str]) -> str:
            """处理 replace 相关逻辑。

            参数:
                match: match，具体约束请结合类型标注和调用方确认。

            返回:
                str

            阅读提示:
                主要直接调用：match.group, id_map.get。
            """
            old = match.group(1)
            return f"[{id_map.get(old, old)}]"

        return _MARKER_PATTERN.sub(replace, text)

    # 阅读注释（函数）：处理 remap bundle citations 相关逻辑。
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
        """处理 remap bundle citations 相关逻辑。

        参数:
            context: 当前执行上下文。
            chunks: chunks，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。
            normalized: normalized，具体约束请结合类型标注和调用方确认。
            registry: 注册表，具体约束请结合类型标注和调用方确认。
            scope: scope，具体约束请结合类型标注和调用方确认。
            query: 当前检索或生成查询。

        返回:
            Tuple[RAGContextSchema, List[RetrievedChunkSchema], List[CitationSchema], Dict[str, Any]]

        阅读提示:
            主要直接调用：registry.register, list, values, cls._replace_markers, context.model_copy, len, get, isinstance。
        """
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

    # 阅读注释（函数）：提取 RAG 输出。
    @staticmethod
    def _extract_rag_output(
        shared_state: SharedStateSchema,
        result: Optional[EvidenceBundleSchema | ToolResultSchema],
    ) -> Tuple[RAGContextSchema, List[RetrievedChunkSchema], List[CitationSchema], Dict[str, Any]]:
        """Consume the canonical Step 12 evidence contract."""

        if isinstance(result, EvidenceBundleSchema):
            contract = result
            context, chunks, citations = RAGEvidenceContractReader.projections(
                contract
            )
            context.extra = {
                **dict(context.extra or {}),
                "evidence_contract": contract.model_dump(),
                "evidence_contract_sha256": canonical_sha256(
                    contract.model_dump()
                ),
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
