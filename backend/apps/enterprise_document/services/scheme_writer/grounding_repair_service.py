# =============================================================================
# 中文阅读说明：Grounding 的 LLM 修复边界。
# 只负责引用标记修复和 evidence-only 重生成；确定性绑定与支持性判断由 CitationService 负责。
# =============================================================================
"""LLM-backed grounding repair for generated scheme sections."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema
from schemas.model import ModelResponseSchema

from .citation_service import CitationService
from .model_service import SectionModelService
from .prompt_service import SectionPromptService


class GroundingRepairService:
    """Own only LLM-backed citation repair and evidence-grounded regeneration."""

    def __init__(
        self,
        *,
        model_service: SectionModelService,
        prompt_service: SectionPromptService,
        citation_service: CitationService,
    ) -> None:
        self.model_service = model_service
        self.prompt_service = prompt_service
        self.citation_service = citation_service

    def repair_section_citations(
        self,
        shared_state: SharedStateSchema,
        *,
        content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> Tuple[str, Optional[ModelResponseSchema]]:
        """Insert citation markers without allowing the repair call to rewrite prose."""

        if not content.strip() or not citations:
            return content, None

        citation_ids = [item.citation_id for item in citations]
        prompt = (
            f"请为下面的‘{section_title}’章节补充知识库引用标记。\n"
            "严格要求：\n"
            "1. 只能在被证据直接支持的句子末尾插入引用标记；\n"
            "2. 只能使用引用目录中存在的标记，例如[C1]；\n"
            "3. 不得增加、删除、改写、重排任何正文文字；\n"
            "4. 不得给没有证据支持的句子强行添加引用；\n"
            "5. 只输出插入引用后的完整原文，不要解释。\n\n"
            f"引用目录：\n{self.prompt_service.citation_catalog(citations)}\n\n"
            f"原始正文：\n{content}"
        )
        response = self.model_service.call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=citation_ids,
            purpose="scheme_citation_repair",
            suffix="_citation_repair",
        )
        if not response.success or not response.content.strip():
            return content, response

        candidate = response.content.strip()
        candidate_bindings = self.citation_service.supported_bindings(
            self.citation_service.bind_citations(
                document_id="citation_repair_validation",
                section_id=section_id,
                content=candidate,
                citations=citations,
            ),
            citations,
        )
        if not candidate_bindings:
            print(
                f"[CitationRepair] REJECT section={section_title} "
                "reason=unsupported_binding",
                flush=True,
            )
            return content, response

        original_plain = self.citation_service.strip_known_citation_markers(
            content, citation_ids
        )
        candidate_plain = self.citation_service.strip_known_citation_markers(
            candidate, citation_ids
        )
        similarity = SequenceMatcher(None, original_plain, candidate_plain).ratio()
        if similarity < 0.97:
            print(
                f"[CitationRepair] REJECT section={section_title} reason=text_changed "
                f"similarity={similarity:.4f}",
                flush=True,
            )
            return content, response

        print(
            f"[CitationRepair] ACCEPT section={section_title} "
            f"bindings={len(candidate_bindings)} similarity={similarity:.4f}",
            flush=True,
        )
        return candidate, response

    def regenerate_section_from_evidence(
        self,
        shared_state: SharedStateSchema,
        *,
        original_content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> ModelResponseSchema:
        """Rewrite one failed section using only explicit evidence and project input."""

        prompt = (
            f"“{section_title}”章节未通过 Claim-Evidence 校验，请重写该章节。\n"
            "强制要求：\n"
            "1. 只能使用项目输入和下列引用目录中直接出现的信息；\n"
            "2. 每个来自知识库的确定性事实必须在句末标注对应引用；\n"
            "3. 不得把常识、经验或推测包装成项目事实；\n"
            "4. 证据未覆盖的内容必须写为‘待补充’或‘需项目方确认’；\n"
            "5. 不得保留原文中没有证据支持的安全措施、资源数量、指标或承诺；\n"
            "6. 只输出重写后的完整章节正文。\n\n"
            f"项目输入：\n{json.dumps(project_input.model_dump(), ensure_ascii=False, indent=2)}\n\n"
            f"引用目录：\n{self.prompt_service.citation_catalog(citations)}\n\n"
            f"未通过校验的原始正文（仅供识别需要修正的范围，不得照抄无依据内容）：\n"
            f"{original_content}"
        )
        return self.model_service.call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_grounded_regeneration",
            suffix="_grounded_regeneration",
        )
