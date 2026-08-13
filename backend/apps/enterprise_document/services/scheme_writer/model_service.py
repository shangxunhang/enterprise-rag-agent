"""章节生成及修复操作的统一模型调用服务。

所有正常生成、续写、压缩、截断重试和定向改写都经过同一 ``ModelCallBoundary``，从而
共享模型角色路由、能力上限、预算预留、Trace 和用量记录。该服务不判断内容是否合格；
它只构造受约束调用并返回标准模型响应。
"""


import json
import re
from typing import Any, Dict, List, Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.quality.model_adapter import (
    reserve_current_workflow_budget,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema
from schemas.context import ContextBuildRequestSchema, ContextItemSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema
from rag.evidence.citation_sources import prompt_visible_citation_text
from context_manager import LLMContextManager
from model_gateway.call_boundary import ModelCallBoundary, infer_model_role
from model_gateway.model_gateway import ModelGateway
from .prompt_service import SectionPromptService
from .runtime_support import SchemeWriterRuntimeSupport


class SectionModelService:
    """集中管理章节相关模型调用及输出长度恢复策略。"""

    def __init__(
        self,
        *,
        model_gateway: ModelGateway | None,
        model_name: str,
        agent_name: str,
        context_manager: LLMContextManager,
        prompt_service: SectionPromptService,
        runtime_support: SchemeWriterRuntimeSupport,
    ) -> None:
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.agent_name = agent_name
        self.context_manager = context_manager
        self.prompt_service = prompt_service
        self.runtime_support = runtime_support
    def call_model(
        self,
        shared_state: SharedStateSchema,
        *,
        prompt: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        available_citation_ids: List[str],
        purpose: str = "scheme_section_generation",
        suffix: str = "",
        max_tokens_override: Optional[int] = None,
        context_package: Optional[Dict[str, Any]] = None,
        prompt_id: Optional[str] = None,
        prompt_version: Optional[str] = None,
    ) -> ModelResponseSchema:
        """通过角色感知的统一边界执行一次模型调用。

        ``purpose`` 决定模型角色；输出上限先受 ProjectInput 控制，再受目标模型安全能力
        收紧。调用方未提供结构化上下文包时，现有辅助 Prompt 会被包装为 passthrough 包，
        确保所有模型调用都有一致的上下文血缘。响应同时写入 ``generated_outputs``，供
        Checkpoint、Trace 和 DataCapture 引用。
        """
        if self.model_gateway is None:
            raise RuntimeError("ModelGateway is not configured")
        resolved_role = infer_model_role(purpose)
        requested_max_tokens = int(
            max_tokens_override
            if max_tokens_override is not None
            else project_input.generation_requirements.max_tokens_per_section
        )
        capability_plan: Dict[str, Any] = {}
        resolve_capabilities = getattr(self.model_gateway, "routing_capabilities", None)
        if callable(resolve_capabilities):
            capability_plan = dict(
                resolve_capabilities(model_role=resolved_role.value)
            )
            requested_max_tokens = min(
                requested_max_tokens,
                int(capability_plan["safe_max_output_tokens"]),
            )
        if not context_package:
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
            passthrough = self.context_manager.build_passthrough(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                call_purpose=purpose,
                content=prompt,
                section_id=section_id,
                section_title=section_title,
                max_context_chars=int(
                    project_input.generation_requirements.max_context_chars
                ),
                max_input_tokens=max_input_tokens,
                reserved_output_tokens=requested_max_tokens,
                lineage={
                    "context_mode": "auxiliary_passthrough",
                    "model_capability_plan": capability_plan,
                },
            )
            context_package = passthrough.model_dump()
        call_id = f"model_call_{shared_state.run_id}_{section_id}{suffix}"
        boundary = ModelCallBoundary(
            model_gateway=self.model_gateway,
            model_role=resolved_role.value,
            runtime_context={
                "task_id": shared_state.task_id,
                "workflow_run_id": shared_state.run_id,
                "section_id": section_id,
                "section_title": section_title,
                "caller_agent": self.agent_name,
            },
            default_purpose=purpose,
            call_suffix=(suffix or section_id),
            budget_hook=reserve_current_workflow_budget,
        )
        print(
            f"[Model] START purpose={purpose} section={section_title} max_tokens={requested_max_tokens}",
            flush=True,
        )
        response = boundary.generate_response(
            prompt,
            temperature=0.2,
            max_new_tokens=requested_max_tokens,
            created_at=self.runtime_support.now_iso(),
            model_call_id=call_id,
            model_extra={
                "document_title": project_input.output_schema.document_title,
                "available_citation_ids": available_citation_ids,
                "prompt_id": prompt_id,
                "prompt_version": prompt_version,
                "llm_context_summary": self._context_package_summary(context_package),
                "model_capability_plan": capability_plan,
            },
        )
        print(
            f"[Model] END   purpose={purpose} section={section_title} "
            f"success={response.success} finish_reason={response.finish_reason} "
            f"latency_ms={response.latency_ms}",
            flush=True,
        )
        shared_state.generated_outputs[call_id] = response.model_dump()
        return response

    @staticmethod
    def _context_package_summary(
        package: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """提取适合 Trace 的有界上下文摘要，不复制正文内容。"""
        if not package:
            return {"managed": False}
        budget = dict(package.get("budget") or {})
        selected = list(package.get("selected_items") or [])
        decisions = list(package.get("decisions") or [])
        return {
            "managed": True,
            "schema_version": package.get("schema_version"),
            "package_id": package.get("package_id"),
            "context_sha256": package.get("context_sha256"),
            "call_purpose": package.get("call_purpose"),
            "section_id": package.get("section_id"),
            "section_title": package.get("section_title"),
            "selected_item_count": len(selected),
            "selected_item_ids": [item.get("item_id") for item in selected],
            "selected_source_types": [item.get("source_type") for item in selected],
            "decision_counts": {
                action: sum(1 for item in decisions if item.get("action") == action)
                for action in ("selected", "truncated", "dropped")
            },
            "used_context_chars": budget.get("used_context_chars"),
            "estimated_input_tokens": budget.get("estimated_input_tokens"),
            "max_context_chars": budget.get("max_context_chars"),
            "max_input_tokens": budget.get("max_input_tokens"),
            "reserved_output_tokens": budget.get("reserved_output_tokens"),
            "warning_count": len(package.get("warnings") or []),
            "lineage": dict(package.get("lineage") or {}),
        }

    def _continue_truncated_section(
        self,
        shared_state: SharedStateSchema,
        *,
        original_content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
        rag_context: RAGContextSchema,
    ) -> ModelResponseSchema:
        """请求模型只续写未完成尾部，并使用缩短证据上下文控制再次膨胀。

        此方法保留为兼容恢复策略；当前主恢复路径更偏向从头生成紧凑短稿，因为小模型
        续写常再次触达 Token 上限。
        """
        reduced_context = rag_context.context_text[: max(1000, rag_context.max_context_chars // 2)]
        remaining_chars = max(
            160,
            self.prompt_service.target_section_chars(project_input)
            - len(original_content),
        )
        prompt = (
            f"继续完成“{section_title}”章节。不要重复已完成内容，只输出续写部分。\n"
            f"续写最多 {remaining_chars} 个汉字，只补全未完成句子和必要结论；"
            "不得新增其他章节、标题、分隔线或大段扩展。\n\n"
            f"已完成内容：\n{original_content}\n\n"
            f"缩短后的证据上下文：\n{reduced_context}\n\n"
            f"可用引用：\n{self.prompt_service.citation_catalog(citations)}"
        )
        return self.call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_section_continuation",
            suffix="_continue",
            max_tokens_override=min(256, project_input.generation_requirements.max_tokens_per_section),
        )

    def retry_truncated_section(
        self,
        shared_state: SharedStateSchema,
        *,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
        rag_context: RAGContextSchema,
        retry_index: int,
    ) -> ModelResponseSchema:
        """输出达到 Token 上限后，从头生成一版完整且显著缩短的章节。

        小型本地模型续写容易继续扩展并再次截断，因此恢复时按重试次数缩小证据上下文，
        将目标压到 4–6 个要点，并明确禁止接续旧稿和跨章节展开。
        """

        divisor = max(2, retry_index + 1)
        reduced_chars = max(800, rag_context.max_context_chars // divisor)
        target_chars = self.prompt_service.target_section_chars(project_input)
        compact_target_chars = min(800, max(480, int(target_chars * 0.68)))
        contract = self.prompt_service.section_generation_contract(
            section_title, project_input
        )

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
        compact_project_facts = json.dumps(
            project_facts,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(compact_project_facts) > 1200:
            compact_project_facts = compact_project_facts[:1199].rstrip() + "…"

        citation_catalog = []
        for item in citations:
            quote_text = prompt_visible_citation_text(item)
            if len(quote_text) > 240:
                quote_text = quote_text[:239].rstrip() + "…"
            rendered = {
                "marker": f"[{item.citation_id}]",
                "quote_text": quote_text,
            }
            if item.title:
                rendered["title"] = item.title
            if item.section:
                rendered["section"] = item.section
            citation_catalog.append(rendered)
        compact_citation_catalog = json.dumps(
            citation_catalog,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(compact_citation_catalog) > 1800:
            compact_citation_catalog = compact_citation_catalog[:1799].rstrip() + "…"

        prompt_prefix = (
            f"“{section_title}”章节此前因达到输出上限而未完整结束。请从头生成一版完整短稿。\n"
            f"强制要求：正文控制在 {compact_target_chars} 个汉字以内，最多使用 4 至 6 个要点；"
            "每个要点只保留核心结论，不得续接旧稿，不得重复其他章节，"
            "不得输出章节标题、Markdown 分隔线或无关扩展；最后必须以完整句子结束；"
            "输入缺失时写待补充或需项目方确认。\n"
            f"章节边界：{contract}\n\n"
            f"项目事实：\n{compact_project_facts}\n\n"
            "缩短后的证据上下文：\n"
        )
        prompt_suffix = f"\n\n可用引用：\n{compact_citation_catalog}"
        max_prompt_chars = int(
            project_input.generation_requirements.max_context_chars
        )
        available_context_chars = max(
            0,
            max_prompt_chars - len(prompt_prefix) - len(prompt_suffix),
        )
        reduced_context = rag_context.context_text[
            : min(reduced_chars, available_context_chars)
        ]
        prompt = f"{prompt_prefix}{reduced_context}{prompt_suffix}"
        return self.call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_section_retry",
            suffix=f"_retry_{retry_index}",
            max_tokens_override=min(
                768, project_input.generation_requirements.max_tokens_per_section
            ),
        )

    @staticmethod
    def recover_complete_prefix(
        content: str,
        *,
        min_chars: int,
        max_chars: int,
    ) -> Optional[str]:
        """从被截断输出中恢复满足最小长度且结束于完整句界的安全前缀。

        这是显式确定性恢复，不是静默按字符硬切；找不到完整结束点或有效内容过短时返回
        ``None``，由调用方继续重试或判定失败。
        """

        normalized = (content or "").strip()
        if not normalized:
            return None
        capped = normalized[: max_chars].rstrip()
        endpoints = [
            match.end()
            for match in re.finditer(
                r"[。！？!?；;](?:\s*\[[A-Za-z0-9_.:\-]+\])?",
                capped,
            )
        ]
        if not endpoints:
            return None
        candidate = capped[: endpoints[-1]].strip()
        if len(candidate) < max(1, min_chars):
            return None
        return candidate

    def compress_overlong_section(
        self,
        shared_state: SharedStateSchema,
        *,
        original_content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> ModelResponseSchema:
        """压缩内容完整但超长的章节，禁止自由重生成或增加新事实。

        固定压缩指令作为 Prompt wrapper，不占 ``max_context_chars``；原始正文、章节边界
        和引用目录作为结构化候选上下文交给 ``LLMContextManager``，与主 Writer 保持
        ``managed context <= max_context_chars`` 的同一预算语义。
        """

        target_chars = self.prompt_service.target_section_chars(project_input)
        hard_limit = int(target_chars * 1.5)
        contract = self.prompt_service.section_generation_contract(
            section_title, project_input
        )
        compression_max_tokens = min(
            640,
            max(384, int(target_chars * 0.65)),
            project_input.generation_requirements.max_tokens_per_section,
        )

        citation_lines: list[str] = []
        for item in citations:
            quote_text = prompt_visible_citation_text(item)
            if len(quote_text) > 240:
                quote_text = quote_text[:239].rstrip() + "…"
            citation_lines.append(
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

        context_items = [
            ContextItemSchema(
                item_id="compression_original_content",
                source_type="generated_content",
                title="待压缩原始正文",
                content=original_content,
                priority=110,
                required=True,
                truncate_allowed=False,
            ),
            ContextItemSchema(
                item_id="compression_section_contract",
                source_type="instruction",
                title="章节边界",
                content=contract,
                priority=105,
                required=True,
                truncate_allowed=False,
            ),
        ]
        if citation_lines:
            context_items.append(
                ContextItemSchema(
                    item_id="compression_citation_catalog",
                    source_type="citation",
                    title="可用引用目录",
                    content="\n".join(citation_lines),
                    priority=70,
                    required=False,
                    truncate_allowed=True,
                    citation_ids=[item.citation_id for item in citations],
                    metadata={
                        "compaction_strategy": "line_blocks",
                        "min_blocks": 0,
                    },
                )
            )

        capability_plan: Dict[str, Any] = {}
        resolved_role = infer_model_role("scheme_section_compression")
        resolve_capabilities = getattr(self.model_gateway, "routing_capabilities", None)
        if callable(resolve_capabilities):
            capability_plan = dict(
                resolve_capabilities(model_role=resolved_role.value)
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
            reserved_output_tokens = min(
                compression_max_tokens,
                int(capability_plan["safe_max_output_tokens"]),
            )
        else:
            max_input_tokens = int(configured_input_limit or 8192)
            reserved_output_tokens = compression_max_tokens

        context_package = self.context_manager.build(
            ContextBuildRequestSchema(
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                call_purpose="scheme_section_compression",
                section_id=section_id,
                section_title=section_title,
                items=context_items,
                max_context_chars=int(
                    project_input.generation_requirements.max_context_chars
                ),
                max_input_tokens=max_input_tokens,
                reserved_output_tokens=reserved_output_tokens,
                safety_margin_tokens=int(
                    project_input.generation_requirements.extra.get(
                        "context_safety_margin_tokens"
                    )
                    or 256
                ),
                lineage={
                    "context_mode": "managed_compression",
                    "model_capability_plan": capability_plan,
                },
                metadata={"policy_id": "section_compression_context_policy_v1"},
            )
        )
        prompt = (
            f"下面的‘{section_title}’章节内容完整，但长度超过限制。请执行压缩改写，"
            f"不要重新自由扩写。目标不超过 {target_chars} 个汉字，绝对不得超过 "
            f"{hard_limit} 个字符。\n"
            "保留核心逻辑和有证据支撑的事实，删除重复说明、空泛形容、跨章节内容和"
            "不必要的小标题；不得增加新事实、资源数量、产品型号或人员承诺；"
            "只输出压缩后的完整正文，并以完整句子结束。\n\n"
            f"本次压缩上下文：\n{context_package.rendered_context}"
        )
        return self.call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_section_compression",
            suffix="_compress",
            max_tokens_override=compression_max_tokens,
            context_package=context_package.model_dump(),
        )

    def _rewrite_invalid_section(
        self,
        shared_state: SharedStateSchema,
        *,
        original_content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
        semantic_issues: List[Dict[str, Any]],
    ) -> ModelResponseSchema:
        """依据语义门禁问题清单做一次定向修订，不允许无约束扩写。"""

        target_chars = self.prompt_service.target_section_chars(project_input)
        contract = self.prompt_service.section_generation_contract(
            section_title, project_input
        )
        prompt = (
            f"‘{section_title}’章节存在语义质量问题。请只按问题清单做定向修订，"
            "不要重新自由扩写，也不要增加新事实。\n"
            f"正文建议不超过 {target_chars} 个汉字，只输出修订后的完整正文。\n"
            "对于无依据的确定性数量、采购、人力、预算、工期、性能和项目现状，"
            "应删除或改成建议、待测算、需项目方确认；对于轻微章节跑题，应删除"
            "跨章节展开内容，但保留与当前标题直接相关的核心表述。\n\n"
            f"动态章节边界：\n{contract}\n\n"
            f"语义评审问题：\n{json.dumps(semantic_issues, ensure_ascii=False, indent=2)}\n\n"
            f"项目输入：\n{json.dumps(project_input.model_dump(), ensure_ascii=False, indent=2)}\n\n"
            f"原始正文：\n{original_content}\n\n"
            f"可用引用目录：\n{self.prompt_service.citation_catalog(citations)}"
        )
        return self.call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_section_validation_rewrite",
            suffix="_validation_rewrite",
            max_tokens_override=min(
                768, project_input.generation_requirements.max_tokens_per_section
            ),
        )
