"""章节 Grounding 的受约束 LLM 修复边界。

模型仅允许执行两类操作：不改正文地补充引用标记，或只依据 ProjectInput 与显式证据重写
失败章节。引用绑定、支持性评分和是否接受候选文本仍由确定性 ``CitationService`` 决定。
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema
from schemas.context import ContextBuildRequestSchema, ContextItemSchema, LLMContextPackageSchema
from schemas.model import ModelResponseSchema
from rag.evidence.citation_sources import prompt_visible_citation_text

from .citation_service import CitationService
from .model_service import SectionModelService
from .prompt_service import SectionPromptService


class GroundingRepairService:
    """隔离所有需要模型参与的引用修复和基于证据重生成。"""

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

    @staticmethod
    def _compact_citation_lines(citations: List[CitationSchema]) -> list[str]:
        """Render citation candidates as independently truncatable line blocks."""

        lines: list[str] = []
        for item in citations:
            quote_text = prompt_visible_citation_text(item)
            if len(quote_text) > 240:
                quote_text = quote_text[:239].rstrip() + "…"
            lines.append(
                json.dumps(
                    {
                        "citation_id": item.citation_id,
                        "marker": f"[{item.citation_id}]",
                        "title": item.title,
                        "section": item.section,
                        "quote_text": quote_text,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return lines

    def _build_managed_repair_context(
        self,
        shared_state: SharedStateSchema,
        *,
        purpose: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        items: list[ContextItemSchema],
    ) -> LLMContextPackageSchema:
        """Build grounding-repair material under the real managed-context budget.

        Operation instructions stay outside this package as a fixed prompt wrapper.
        Only repair material counts against ``max_context_chars`` so the budget has
        the same semantics as the primary writer and compression paths.
        """

        requested_output_tokens = int(
            project_input.generation_requirements.max_tokens_per_section
        )
        capability_plan: dict = {}
        resolve_capabilities = getattr(
            self.model_service.model_gateway,
            "routing_capabilities",
            None,
        )
        if callable(resolve_capabilities):
            capability_plan = dict(resolve_capabilities(model_role="repair"))
            requested_output_tokens = min(
                requested_output_tokens,
                int(capability_plan["safe_max_output_tokens"]),
            )

        configured_input_limit = project_input.generation_requirements.extra.get(
            "max_input_context_tokens"
        )
        if capability_plan:
            safe_context_window = int(capability_plan["safe_context_window"])
            max_input_tokens = (
                min(safe_context_window, int(configured_input_limit))
                if configured_input_limit is not None
                else safe_context_window
            )
        else:
            max_input_tokens = int(configured_input_limit or 8192)

        return self.model_service.context_manager.build(
            ContextBuildRequestSchema(
                task_id=str(
                    getattr(shared_state, "task_id", None)
                    or project_input.task_id
                ),
                run_id=shared_state.run_id,
                call_purpose=purpose,
                section_id=section_id,
                section_title=section_title,
                items=items,
                max_context_chars=int(
                    project_input.generation_requirements.max_context_chars
                ),
                max_input_tokens=max_input_tokens,
                reserved_output_tokens=requested_output_tokens,
                safety_margin_tokens=int(
                    project_input.generation_requirements.extra.get(
                        "context_safety_margin_tokens"
                    )
                    or 256
                ),
                lineage={
                    "context_mode": "managed_grounding_repair",
                    "model_capability_plan": capability_plan,
                },
                metadata={"policy_id": "grounding_repair_context_policy_v1"},
            )
        )

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
        """请求模型只插入引用标记，并用确定性规则拒绝正文改写。

        候选必须形成至少一个受支持绑定；移除已知标记后与原文相似度低于 0.97 时视为
        模型越权改写，返回原文而不接受候选。
        """

        if not content.strip() or not citations:
            return content, None

        citation_ids = [item.citation_id for item in citations]
        context_package = self._build_managed_repair_context(
            shared_state,
            purpose="scheme_citation_repair",
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            items=[
                ContextItemSchema(
                    item_id="citation_repair_original_content",
                    source_type="generated_content",
                    title="原始正文",
                    content=content,
                    priority=110,
                    required=True,
                    truncate_allowed=False,
                ),
                ContextItemSchema(
                    item_id="citation_repair_catalog",
                    source_type="citation",
                    title="引用目录",
                    content="\n".join(self._compact_citation_lines(citations)),
                    priority=100,
                    required=True,
                    truncate_allowed=True,
                    citation_ids=citation_ids,
                    metadata={
                        "compaction_strategy": "line_blocks",
                        "min_blocks": 1,
                    },
                ),
            ],
        )
        prompt = (
            f"请为‘{section_title}’章节补充知识库引用标记。\n"
            "严格要求：\n"
            "1. 只能在被证据直接支持的句子末尾插入引用标记；\n"
            "2. 只能使用下方修复材料中存在的引用标记，例如[C1]；\n"
            "3. 不得增加、删除、改写、重排任何正文文字；\n"
            "4. 不得给没有证据支持的句子强行添加引用；\n"
            "5. 只输出插入引用后的完整原文，不要解释。\n\n"
            f"本次修复材料：\n{context_package.rendered_context}"
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
            context_package=context_package.model_dump(),
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
        """只使用显式证据和 ProjectInput 重写未通过 Claim-Evidence 校验的章节。

        未被证据覆盖的内容必须降级为待补充或需项目方确认，不能继承原文中的无依据资源、
        指标、安全措施或承诺。
        """

        project_payload = project_input.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
        project_fact_fields = (
            "project_name",
            "project_type",
            "customer_type",
            "business_goal",
            "total_staff",
            "functional_department_count",
            "business_department_count",
            "department_groups",
            "hardware_resources",
            "policy_requirements",
            "manual_boundaries",
            "missing_information",
            "conflicting_information",
            "extra",
        )
        project_facts = {
            field_name: project_payload[field_name]
            for field_name in project_fact_fields
            if field_name in project_payload
            and project_payload[field_name] not in (None, "", [], {})
        }
        citation_catalog = []
        for item in citations:
            rendered = {
                "marker": f"[{item.citation_id}]",
                "quote_text": prompt_visible_citation_text(item),
            }
            if item.title:
                rendered["title"] = item.title
            if item.section:
                rendered["section"] = item.section
            citation_catalog.append(rendered)

        compact_project_facts = json.dumps(
            project_facts,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        compact_citation_catalog = [
            json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for item in citation_catalog
        ]
        context_package = self._build_managed_repair_context(
            shared_state,
            purpose="scheme_grounded_regeneration",
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            items=[
                ContextItemSchema(
                    item_id="grounded_regeneration_project_facts",
                    source_type="task",
                    title="项目事实",
                    content=compact_project_facts,
                    priority=120,
                    required=True,
                    truncate_allowed=False,
                ),
                ContextItemSchema(
                    item_id="grounded_regeneration_citation_catalog",
                    source_type="citation",
                    title="引用目录",
                    content="\n".join(compact_citation_catalog),
                    priority=110,
                    required=True,
                    truncate_allowed=True,
                    citation_ids=[item.citation_id for item in citations],
                    metadata={
                        "compaction_strategy": "line_blocks",
                        "min_blocks": 1,
                    },
                ),
                ContextItemSchema(
                    item_id="grounded_regeneration_original_content",
                    source_type="generated_content",
                    title="未通过校验的原始正文",
                    content=original_content,
                    priority=60,
                    required=False,
                    truncate_allowed=True,
                ),
            ],
        )
        prompt = (
            f"“{section_title}”章节未通过 Claim-Evidence 校验，请重写该章节。\n"
            "强制要求：\n"
            "1. 只能使用下方修复材料中的项目事实和引用证据；\n"
            "2. 知识库事实必须在句末标注对应引用，未覆盖内容改为‘待补充’或‘需项目方确认’；\n"
            "3. 删除原文中无依据的数量、配置、指标、安全措施和承诺，只输出完整章节正文；\n"
            "4. 原始正文仅用于识别修正范围，不得照抄其中无依据内容。\n\n"
            f"本次修复材料：\n{context_package.rendered_context}"
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
            context_package=context_package.model_dump(),
        )
