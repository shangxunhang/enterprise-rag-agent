"""Context policies for enterprise document generation."""

from __future__ import annotations

import json
from typing import Iterable, List

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeSectionSchema
from schemas.citation import CitationSchema
from schemas.context import ContextBuildRequestSchema, ContextItemSchema
from schemas.rag import RAGContextSchema, RAGEvidenceContractSchema


class SectionGenerationContextPolicy:
    """Build candidates for the initial generation of one document section."""

    policy_id = "section_generation_context_policy_v2"

    SYSTEM_CONSTRAINTS = (
        "你是政企项目建设方案编制助手。禁止把未由ProjectInput或知识库证据支持的"
        "数量、采购、人力、预算、工期、性能或客户事实写成确定结论。输入不足时必须"
        "标注‘待补充’或‘需项目方确认’。引用知识库事实时只能使用提供的citation_id。"
    )

    def build_request(
        self,
        *,
        task_id: str,
        run_id: str,
        section_id: str,
        section_title: str,
        section_order: int,
        project_input: ProjectInputSchema,
        section_contract: str,
        target_section_chars: int,
        rag_context: RAGContextSchema,
        citations: List[CitationSchema],
        previous_sections: List[SchemeSectionSchema],
    ) -> ContextBuildRequestSchema:
        contract = self._evidence_contract(rag_context)
        lineage = (
            contract.lineage.model_dump()
            if contract is not None
            else dict((rag_context.extra or {}).get("lineage") or {})
        )
        if contract is not None:
            lineage["evidence_contract_schema"] = contract.schema_version
            lineage["evidence_contract_query"] = contract.query

        citation_required_titles = {
            str(item).strip()
            for item in project_input.generation_requirements.citation_required_sections
            if str(item).strip()
        }
        citation_required = bool(
            project_input.generation_requirements.need_citation
            and citations
            and str(section_title).strip() in citation_required_titles
        )
        context_options = dict(project_input.generation_requirements.extra or {})
        required_catalog_limit = max(
            1, min(12, int(context_options.get("context_required_citation_limit") or 6))
        )
        optional_catalog_limit = max(
            0, min(8, int(context_options.get("context_optional_citation_limit") or 3))
        )
        required_quote_chars = max(
            80, min(320, int(context_options.get("context_required_quote_chars") or 180))
        )
        optional_quote_chars = max(
            60, min(240, int(context_options.get("context_optional_quote_chars") or 120))
        )
        citation_catalog, catalog_citation_ids, catalog_metadata = self._citation_catalog(
            citations,
            max_items=(
                required_catalog_limit if citation_required else optional_catalog_limit
            ),
            max_quote_chars=(
                required_quote_chars if citation_required else optional_quote_chars
            ),
        )

        items: List[ContextItemSchema] = [
            ContextItemSchema(
                item_id="system_constraints",
                source_type="system",
                title="系统约束",
                content=self.SYSTEM_CONSTRAINTS,
                priority=110,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id=f"section_{section_order:03d}",
                source_type="current_section",
                title="当前章节任务",
                content=(
                    f"文档标题：{project_input.output_schema.document_title}\n"
                    f"章节序号：{section_order}\n"
                    f"章节标题：{section_title}\n"
                    f"目标篇幅：不超过{target_section_chars}个汉字\n"
                    f"章节边界：{section_contract}"
                ),
                priority=105,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id="project_core",
                source_type="task",
                title="项目核心输入",
                content=json.dumps(
                    self._project_core(project_input),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                priority=100,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id="citation_catalog",
                source_type="citation",
                title="可用引用目录",
                content=citation_catalog,
                priority=95,
                required=citation_required,
                truncate_allowed=True,
                citation_ids=catalog_citation_ids,
                metadata={
                    **catalog_metadata,
                    "compaction_strategy": "line_blocks",
                    "min_blocks": 1 if citation_required else 0,
                    "section_citation_required": citation_required,
                },
            ),
        ]

        items.extend(self._evidence_items(contract, rag_context))

        history = self._history_summary(previous_sections)
        if history:
            items.append(
                ContextItemSchema(
                    item_id="previous_sections",
                    source_type="history",
                    title="已生成章节摘要",
                    content=history,
                    priority=60,
                    required=False,
                    metadata={"section_count": len(previous_sections)},
                )
            )

        supplemental = self._supplemental_project_context(project_input)
        if supplemental:
            items.append(
                ContextItemSchema(
                    item_id="project_supplemental",
                    source_type="task_detail",
                    title="项目补充输入",
                    content=json.dumps(supplemental, ensure_ascii=False, indent=2),
                    priority=70,
                    required=False,
                )
            )

        max_context_tokens = int(
            project_input.generation_requirements.extra.get("max_input_context_tokens")
            or 8192
        )
        safety_margin = int(
            project_input.generation_requirements.extra.get("context_safety_margin_tokens")
            or 256
        )
        return ContextBuildRequestSchema(
            task_id=task_id,
            run_id=run_id,
            call_purpose="scheme_section_generation",
            section_id=section_id,
            section_title=section_title,
            items=items,
            max_context_chars=int(project_input.generation_requirements.max_context_chars),
            max_input_tokens=max_context_tokens,
            reserved_output_tokens=int(
                project_input.generation_requirements.max_tokens_per_section
            ),
            safety_margin_tokens=safety_margin,
            lineage=lineage,
            metadata={
                "policy_id": self.policy_id,
                "document_title": project_input.output_schema.document_title,
                "section_order": section_order,
            },
        )

    @staticmethod
    def _project_core(project_input: ProjectInputSchema) -> dict:
        return {
            "task_id": project_input.task_id,
            "tenant_id": project_input.tenant_id,
            "project_name": project_input.project_name,
            "project_type": project_input.project_type,
            "customer_type": project_input.customer_type,
            "task_type": project_input.task_type,
            "user_query": project_input.user_query,
            "business_goal": project_input.business_goal,
            "total_staff": project_input.total_staff,
            "functional_department_count": project_input.functional_department_count,
            "business_department_count": project_input.business_department_count,
            "policy_requirements": list(project_input.policy_requirements),
            "manual_boundaries": [
                item.model_dump() for item in project_input.manual_boundaries
            ],
            "missing_information": list(project_input.missing_information),
            "conflicting_information": list(project_input.conflicting_information),
            "output_schema": project_input.output_schema.model_dump(),
            "generation_requirements": {
                "need_citation": project_input.generation_requirements.need_citation,
                "citation_required_sections": list(
                    project_input.generation_requirements.citation_required_sections
                ),
                "language": project_input.generation_requirements.language,
                "tone": project_input.generation_requirements.tone,
            },
        }

    @staticmethod
    def _supplemental_project_context(project_input: ProjectInputSchema) -> dict:
        result = {
            "department_groups": [item.model_dump() for item in project_input.department_groups],
            "hardware_resources": [item.model_dump() for item in project_input.hardware_resources],
            "source_materials": [item.model_dump() for item in project_input.source_materials],
            "target_documents": list(project_input.target_documents),
            "target_templates": list(project_input.target_templates),
            "extra": dict(project_input.extra or {}),
        }
        return {key: value for key, value in result.items() if value}

    @staticmethod
    def _compact_text(value: object, *, max_chars: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_chars:
            return text
        prefix = text[:max_chars].rstrip()
        cut = max(prefix.rfind(mark) for mark in ("。", "；", "！", "？", ".", ";"))
        if cut >= max(24, max_chars // 2):
            prefix = prefix[: cut + 1].rstrip()
        return prefix.rstrip("，,；;。.") + "…"

    @classmethod
    def _citation_catalog(
        cls,
        citations: Iterable[CitationSchema],
        *,
        max_items: int,
        max_quote_chars: int,
    ) -> tuple[str, list[str], dict]:
        source_items = list(citations)
        selected_items = source_items[: max(0, int(max_items))]
        rows: list[str] = []
        citation_ids: list[str] = []
        for item in selected_items:
            citation_id = str(item.citation_id or "").strip()
            if not citation_id:
                continue
            title = cls._compact_text(
                item.title or item.source_document_id or item.doc_id or "未命名来源",
                max_chars=80,
            )
            section = cls._compact_text(item.section or "", max_chars=48)
            quote = cls._compact_text(item.quote_text or "", max_chars=max_quote_chars)
            parts = [f"[{citation_id}]", f"来源：{title}"]
            if section:
                parts.append(f"位置：{section}")
            if quote:
                parts.append(f"摘要：{quote}")
            rows.append(" | ".join(parts))
            citation_ids.append(citation_id)
        return (
            "\n".join(rows),
            citation_ids,
            {
                "catalog_format": "compact_lines_v1",
                "original_citation_count": len(source_items),
                "catalog_citation_count": len(citation_ids),
                "omitted_citation_count": max(0, len(source_items) - len(citation_ids)),
                "max_quote_chars": int(max_quote_chars),
            },
        )

    @staticmethod
    def _history_summary(previous_sections: List[SchemeSectionSchema]) -> str:
        rows: list[str] = []
        for item in previous_sections:
            summary = str((item.extra or {}).get("context_summary") or "").strip()
            if not summary:
                summary = item.content.strip()[:240]
            if summary:
                rows.append(f"- {item.section_title}: {summary}")
        return "\n".join(rows)

    @staticmethod
    def _evidence_contract(rag_context: RAGContextSchema) -> RAGEvidenceContractSchema | None:
        raw = (rag_context.extra or {}).get("evidence_contract")
        if not isinstance(raw, dict):
            return None
        try:
            return RAGEvidenceContractSchema.model_validate(raw)
        except Exception:
            return None

    @classmethod
    def _evidence_items(
        cls,
        contract: RAGEvidenceContractSchema | None,
        rag_context: RAGContextSchema,
    ) -> list[ContextItemSchema]:
        if contract is None:
            if not rag_context.context_text.strip():
                return []
            return [
                ContextItemSchema(
                    item_id="rag_context_projection",
                    source_type="evidence",
                    title="知识库证据",
                    content=rag_context.context_text,
                    priority=85,
                    required=False,
                    metadata={"compatibility_projection": True},
                )
            ]

        item_by_id = {item.evidence_id: item for item in contract.items}
        result: list[ContextItemSchema] = []
        for rank, evidence_id in enumerate(contract.selected_evidence_ids, start=1):
            evidence = item_by_id[evidence_id]
            title = evidence.title or evidence.section or evidence.doc_id
            result.append(
                ContextItemSchema(
                    item_id=evidence.evidence_id,
                    source_type="evidence",
                    title=f"知识库证据{rank}：{title}",
                    content=evidence.context_text,
                    priority=max(75, 90 - rank),
                    required=False,
                    citation_ids=list(evidence.citation_ids),
                    metadata={
                        "doc_id": evidence.doc_id,
                        "matched_chunk_id": evidence.matched_chunk_id,
                        "context_chunk_id": evidence.context_chunk_id,
                        "rank": evidence.rank,
                        "score": evidence.score,
                        "rerank_score": evidence.rerank_score,
                    },
                )
            )
        return result
