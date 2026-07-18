"""Configuration-driven generation repair strategies."""

from __future__ import annotations

from typing import Any

from rag.plugins.quality_support import resolve_quality_generator
from rag.ports.quality import RepairOutput


def _context(build_context: Any) -> dict[str, Any]:
    return build_context if isinstance(build_context, dict) else {}


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit]


class LocalRewriteRepairStrategyPlugin:
    """Rewrite one unsupported section using only current evidence.

    The plugin does not decide whether the rewritten text is acceptable.  The
    caller must rebuild CitationBinding objects and run the configured checker
    again before accepting it.
    """

    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        max_new_tokens: int = 768,
        fallback_to_original: bool = True,
    ) -> None:
        self.build_context = _context(build_context)
        context_llm_enabled = bool(self.build_context.get("enable_quality_llm", False))
        self.use_llm = context_llm_enabled if use_llm is None else bool(use_llm)
        self.max_new_tokens = max(128, int(max_new_tokens))
        self.fallback_to_original = bool(fallback_to_original)

    def repair(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]],
        check_result: dict[str, Any] | None,
        runtime_context: dict[str, Any] | None = None,
    ) -> RepairOutput:
        original = str(answer or "").strip()
        check = dict(check_result or {})
        need_rewrite = bool(check.get("need_rewrite"))
        if not need_rewrite:
            return RepairOutput(
                answer=original,
                repaired=False,
                report={
                    "enabled": True,
                    "action": "skip",
                    "reason": "generation checker did not request rewrite",
                    "need_retrieve_more": bool(check.get("need_retrieve_more")),
                },
            )

        generator = resolve_quality_generator(
            build_context=self.build_context,
            runtime_context=runtime_context,
            purpose="agent_section_local_rewrite",
            call_suffix=str(
                (runtime_context or {}).get("call_suffix") or "local_rewrite"
            ),
        )
        if not self.use_llm or generator is None:
            return RepairOutput(
                answer=original,
                repaired=False,
                report={
                    "enabled": True,
                    "action": "unavailable",
                    "reason": "repair LLM is not available",
                    "fallback_to_original": self.fallback_to_original,
                    "need_retrieve_more": bool(check.get("need_retrieve_more")),
                },
            )

        citation_ids = [
            str(item.get("citation_id"))
            for item in citations
            if isinstance(item, dict) and item.get("citation_id")
        ]
        problems = check.get("problems") or []
        unsupported = check.get("unsupported_claims") or []
        prompt = (
            "请对下面章节执行一次局部证据约束改写。只修复检查器指出的问题，"
            "不要扩展章节范围，不得增加资料中不存在的事实、数量、产品型号、"
            "预算、工期或人员承诺。只输出修订后的完整正文。\n"
            "引用标记不可信，系统会在改写后重新绑定，因此不要为了形式强行插入引用。\n\n"
            f"章节任务：{query}\n\n"
            f"检查问题：{problems}\n"
            f"疑似无支撑内容：{unsupported}\n"
            f"当前有效绑定数量：{len(citation_bindings)}\n"
            f"可用引用ID：{citation_ids}\n\n"
            f"证据上下文：\n{_clip(context, 5000)}\n\n"
            f"原章节：\n{_clip(original, 3000)}"
        )
        try:
            candidate = str(
                generator.generate(
                    prompt,
                    system_prompt=(
                        "你是企业级文档局部修复器。严格依据证据修订章节，"
                        "只输出正文。"
                    ),
                    max_new_tokens=self.max_new_tokens,
                    temperature=0.0,
                    top_p=0.9,
                    do_sample=False,
                )
                or ""
            ).strip()
        except Exception as exc:
            if not self.fallback_to_original:
                raise
            return RepairOutput(
                answer=original,
                repaired=False,
                report={
                    "enabled": True,
                    "action": "fallback_original",
                    "reason": f"{exc.__class__.__name__}: {exc}",
                    "fallback_to_original": True,
                    "need_retrieve_more": bool(check.get("need_retrieve_more")),
                },
            )

        repaired = bool(candidate and candidate != original)
        return RepairOutput(
            answer=candidate if repaired else original,
            repaired=repaired,
            report={
                "enabled": True,
                "action": "local_rewrite" if repaired else "no_change",
                "original_chars": len(original),
                "candidate_chars": len(candidate),
                "need_retrieve_more": bool(check.get("need_retrieve_more")),
                "fallback_to_original": self.fallback_to_original,
            },
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "local_rewrite",
            "use_llm": self.use_llm,
            "max_new_tokens": self.max_new_tokens,
            "fallback_to_original": self.fallback_to_original,
            "llm_available": bool(
                self.build_context.get("quality_llm_generator") is not None
                or self.build_context.get("model_gateway") is not None
            ),
        }


class NoOpRepairStrategyPlugin:
    """Explicit no-repair strategy selected by normal profiles."""

    def __init__(self, *, build_context: Any = None) -> None:
        del build_context

    def repair(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]],
        check_result: dict[str, Any] | None,
        runtime_context: dict[str, Any] | None = None,
    ) -> RepairOutput:
        del query, context, citations, citation_bindings, check_result, runtime_context
        return RepairOutput(
            answer=str(answer or ""),
            repaired=False,
            report={"enabled": False, "action": "noop"},
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {"enabled": False, "mode": "noop"}
