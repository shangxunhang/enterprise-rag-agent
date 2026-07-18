"""Generated from the stable v7.5.1 SchemeWriter behavior."""


import json
import re
from typing import Any, Dict, List, Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.rag import RAGContextSchema
from .base import RuntimeBoundService


class SectionModelService(RuntimeBoundService):
    def _call_model(
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
        if self.model_gateway is None:
            raise RuntimeError("ModelGateway is not configured")
        if not context_package:
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
                max_input_tokens=int(
                    project_input.generation_requirements.extra.get(
                        "max_input_context_tokens", 8192
                    )
                ),
                reserved_output_tokens=(
                    max_tokens_override
                    if max_tokens_override is not None
                    else project_input.generation_requirements.max_tokens_per_section
                ),
                lineage={"context_mode": "auxiliary_passthrough"},
            )
            context_package = passthrough.model_dump()
        request = ModelRequestSchema(
            model_call_id=f"model_call_{shared_state.run_id}_{section_id}{suffix}",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            model_name=self.model_name,
            caller_agent=self.agent_name,
            prompt=prompt,
            temperature=0.2,
            max_tokens=(
                max_tokens_override
                if max_tokens_override is not None
                else project_input.generation_requirements.max_tokens_per_section
            ),
            created_at=self._now_iso(),
            extra={
                "call_purpose": purpose,
                "section_id": section_id,
                "section_title": section_title,
                "document_title": project_input.output_schema.document_title,
                "available_citation_ids": available_citation_ids,
                "prompt_id": prompt_id,
                "prompt_version": prompt_version,
                "llm_context_summary": self._context_package_summary(context_package),
            },
        )
        print(
            f"[Model] START purpose={purpose} section={section_title} max_tokens={request.max_tokens}",
            flush=True,
        )
        response = self.model_gateway.generate(request)
        print(
            f"[Model] END   purpose={purpose} section={section_title} "
            f"success={response.success} finish_reason={response.finish_reason} "
            f"latency_ms={response.latency_ms}",
            flush=True,
        )
        shared_state.generated_outputs[request.model_call_id] = response.model_dump()
        return response

    @staticmethod
    def _context_package_summary(
        package: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
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
        reduced_context = rag_context.context_text[: max(1000, rag_context.max_context_chars // 2)]
        remaining_chars = max(160, self._target_section_chars(project_input) - len(original_content))
        prompt = (
            f"继续完成“{section_title}”章节。不要重复已完成内容，只输出续写部分。\n"
            f"续写最多 {remaining_chars} 个汉字，只补全未完成句子和必要结论；"
            "不得新增其他章节、标题、分隔线或大段扩展。\n\n"
            f"已完成内容：\n{original_content}\n\n"
            f"缩短后的证据上下文：\n{reduced_context}\n\n"
            f"可用引用：\n{self._citation_catalog(citations)}"
        )
        return self._runtime._call_model(
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

    def _retry_truncated_section(
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
        """Regenerate one complete compact section after a token-limit stop.

        Continuation proved unstable with small local models: the model often
        keeps expanding, consumes another token budget and is truncated again.
        Recovery therefore starts a fresh generation with a much smaller
        writing target and a reduced evidence context.
        """

        divisor = max(2, retry_index + 1)
        reduced_chars = max(800, rag_context.max_context_chars // divisor)
        reduced_context = rag_context.context_text[:reduced_chars]
        target_chars = self._target_section_chars(project_input)
        compact_target_chars = min(800, max(480, int(target_chars * 0.68)))
        contract = self._section_generation_contract(section_title, project_input)
        prompt = (
            f"“{section_title}”章节此前因达到输出上限而未完整结束。请从头生成一版完整短稿。\n"
            f"强制要求：正文控制在 {compact_target_chars} 个汉字以内，最多使用 4 至 6 个要点；"
            "每个要点只保留核心结论，不得续接旧稿，不得重复其他章节，"
            "不得输出章节标题、Markdown 分隔线或无关扩展；最后必须以完整句子结束；"
            "输入缺失时写待补充或需项目方确认。\n"
            f"章节边界：{contract}\n\n"
            f"项目输入：\n{json.dumps(project_input.model_dump(), ensure_ascii=False, indent=2)}\n\n"
            f"缩短后的证据上下文：\n{reduced_context}\n\n"
            f"可用引用：\n{self._citation_catalog(citations)}"
        )
        return self._runtime._call_model(
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
    def _recover_complete_prefix(
        content: str,
        *,
        min_chars: int,
        max_chars: int,
    ) -> Optional[str]:
        """Return a safe complete prefix from a token-limited response.

        This is a deterministic recovery, not silent raw truncation.  The
        method only accepts text ending at a sentence/list-item boundary and
        only when enough complete content remains to form a usable section.
        The caller records the recovery strategy in section metadata.
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

    def _compress_overlong_section(
        self,
        shared_state: SharedStateSchema,
        *,
        original_content: str,
        section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> ModelResponseSchema:
        """Compress a complete but overlong section instead of free regeneration."""

        target_chars = self._target_section_chars(project_input)
        hard_limit = int(target_chars * 1.5)
        contract = self._section_generation_contract(section_title, project_input)
        prompt = (
            f"下面的‘{section_title}’章节内容完整，但长度超过限制。请执行压缩改写，"
            f"不要重新自由扩写。目标不超过 {target_chars} 个汉字，绝对不得超过 "
            f"{hard_limit} 个字符。\n"
            "保留核心逻辑和有证据支撑的事实，删除重复说明、空泛形容、跨章节内容和"
            "不必要的小标题；不得增加新事实、资源数量、产品型号或人员承诺；"
            "只输出压缩后的完整正文，并以完整句子结束。\n\n"
            f"章节边界：\n{contract}\n\n"
            f"原始正文：\n{original_content}\n\n"
            f"可用引用目录：\n{self._citation_catalog(citations)}"
        )
        return self._runtime._call_model(
            shared_state,
            prompt=prompt,
            section_id=section_id,
            section_title=section_title,
            project_input=project_input,
            available_citation_ids=[item.citation_id for item in citations],
            purpose="scheme_section_compression",
            suffix="_compress",
            max_tokens_override=min(
                640,
                max(384, int(target_chars * 0.65)),
                project_input.generation_requirements.max_tokens_per_section,
            ),
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
        """Apply one issue-directed rewrite based on semantic gate output."""

        target_chars = self._target_section_chars(project_input)
        contract = self._section_generation_contract(section_title, project_input)
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
            f"可用引用目录：\n{self._citation_catalog(citations)}"
        )
        return self._runtime._call_model(
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
