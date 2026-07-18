"""Section prompt construction through the Step 14 Context Manager."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import SchemeSectionSchema
from schemas.citation import CitationSchema
from schemas.prompt import PromptRenderResultSchema
from schemas.rag import RAGContextSchema
from .base import RuntimeBoundService


class SectionPromptService(RuntimeBoundService):
    @staticmethod
    def _citation_catalog(citations: Iterable[CitationSchema]) -> str:
        return json.dumps(
            [
                {
                    "citation_id": item.citation_id,
                    "marker": f"[{item.citation_id}]",
                    "source_document_id": item.source_document_id or item.doc_id,
                    "source_chunk_id": item.chunk_id or item.child_chunk_id or item.parent_chunk_id,
                    "title": item.title,
                    "section": item.section,
                    "quote_text": item.quote_text,
                }
                for item in citations
            ],
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _target_section_chars(project_input: ProjectInputSchema) -> int:
        """Return a conservative Chinese-character budget for one section."""

        max_tokens = max(256, int(project_input.generation_requirements.max_tokens_per_section))
        return max(600, min(1200, max_tokens))

    @staticmethod
    def _has_concrete_resource_input(project_input: ProjectInputSchema) -> bool:
        return bool(
            project_input.hardware_resources
            or project_input.total_staff is not None
            or project_input.functional_department_count is not None
            or project_input.business_department_count is not None
            or project_input.department_groups
            or project_input.extra.get("resource_configuration")
            or project_input.extra.get("capacity_requirements")
        )

    @classmethod
    def _section_generation_contract(
        cls,
        section_title: str,
        project_input: ProjectInputSchema,
    ) -> str:
        required_sections = list(
            dict.fromkeys(
                project_input.generation_requirements.required_sections
                or project_input.output_schema.required_sections
            )
        )
        sibling_titles = [
            item for item in required_sections if str(item).strip() != str(section_title).strip()
        ]
        sibling_text = "、".join(sibling_titles) if sibling_titles else "无"
        has_resource_input = cls._has_concrete_resource_input(project_input)
        resource_instruction = (
            "ProjectInput已提供部分资源事实，只能复述或推导这些已提供事实；"
            if has_resource_input
            else "ProjectInput未提供确定资源数据，涉及数量、型号、预算、工期、人员和性能时，"
                 "只能写测算维度、建议或待确认项；"
        )
        return (
            f"当前章节标题为‘{section_title}’，只围绕该标题的自然语义展开。"
            f"文档中的其他章节为：{sibling_text}；不要完整展开这些兄弟章节的内容。"
            "一般性目标、原则和技术建议可以正常表达；不得把未获ProjectInput或证据支持的"
            "数量、采购、人力、预算、工期、性能或既定事实写成确定承诺。"
            f"{resource_instruction}输入不足时明确标注待补充或需项目方确认。"
        )

    @staticmethod
    def _section_scope_violations(
        content: str,
        section_title: str,
    ) -> List[Dict[str, Any]]:
        return []

    def _render_section_prompt(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        section_id: str,
        section_title: str,
        section_order: int,
        rag_context: RAGContextSchema,
        citations: List[CitationSchema],
        previous_sections: List[SchemeSectionSchema],
    ) -> PromptRenderResultSchema:
        target_chars = self._target_section_chars(project_input)
        build_request = self.context_policy.build_request(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            section_id=section_id,
            section_title=section_title,
            section_order=section_order,
            project_input=project_input,
            section_contract=self._section_generation_contract(section_title, project_input),
            target_section_chars=target_chars,
            rag_context=rag_context,
            citations=citations,
            previous_sections=previous_sections,
        )
        context_package = self.context_manager.build(build_request)
        variables = {
            "document_title": project_input.output_schema.document_title,
            "section_title": section_title,
            "section_order": section_order,
            "target_section_chars": target_chars,
            "llm_context_text": context_package.rendered_context,
        }
        if self.prompt_manager and self.prompt_manager.exists(self.prompt_id):
            result = self.prompt_manager.render(self.prompt_id, variables, strict=True)
        else:
            text = (
                f"请只编写《{variables['document_title']}》中的“{section_title}”章节正文。\n\n"
                f"本次模型上下文：\n{context_package.rendered_context}\n\n"
                f"正文控制在 {target_chars} 个汉字以内，只输出本章节正文。"
            )
            result = PromptRenderResultSchema(
                prompt_id="fallback_section_prompt",
                prompt_name="Section generation fallback",
                prompt_version="v2",
                rendered_text=text,
                variables=variables,
                metadata={"source": "code_fallback"},
            )

        result.extra = {
            **dict(result.extra or {}),
            "llm_context_package": context_package.model_dump(),
        }
        result.metadata = {
            **dict(result.metadata or {}),
            "context_package_id": context_package.package_id,
            "context_sha256": context_package.context_sha256,
            "context_policy_id": context_package.metadata.get("policy_id"),
            "context_used_chars": context_package.budget.used_context_chars,
            "context_estimated_tokens": context_package.budget.estimated_input_tokens,
            "context_warning_count": len(context_package.warnings),
        }
        return result
