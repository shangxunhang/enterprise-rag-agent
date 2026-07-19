"""Self-RAG checking and local repair implementations for document generation."""

from __future__ import annotations

from typing import Any, Iterable

from apps.enterprise_document.quality.model_adapter import (
    resolve_quality_generator,
)
from apps.enterprise_document.quality.ports import RepairOutput
from rag.judge.rag_quality_judge import SelfRAGJudge


def _context(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit]


class SelfRAGLiteGenerationCheckerPlugin:
    """Check answer relevance and evidence support after generation."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Iterable[str] | None = None,
    ) -> None:
        self.build_context = _context(build_context)
        context_llm_enabled = bool(
            self.build_context.get("enable_quality_llm", False)
        )
        self.use_llm = context_llm_enabled if use_llm is None else bool(use_llm)
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.noise_terms = tuple(str(item) for item in (noise_terms or ()))
        self.generation_params = dict(
            self.build_context.get("quality_generation_params") or {}
        )
        self.default_generator = self.build_context.get("quality_llm_generator")
        self.backend = self._build_backend(self.default_generator)

    def _build_backend(self, generator: Any | None) -> SelfRAGJudge:
        return SelfRAGJudge(
            llm_generator=generator,
            use_llm=self.use_llm,
            generation_params=self.generation_params,
            fallback_to_deterministic=self.fallback_to_deterministic,
            noise_terms=self.noise_terms,
        )

    def check(
        self,
        *,
        query: str,
        answer: str | None,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        generator = resolve_quality_generator(
            build_context=self.build_context,
            runtime_context=runtime_context,
            purpose="agent_section_self_rag_check",
            call_suffix=str(
                (runtime_context or {}).get("call_suffix") or "self_rag_check"
            ),
        )
        backend = (
            self.backend
            if generator is self.default_generator
            else self._build_backend(generator)
        )
        report = backend.check_answer(
            query=query,
            answer=answer,
            context=context,
            citations=citations,
        ).to_dict()
        report["metadata"] = {
            **dict(report.get("metadata") or {}),
            "citation_count": len(citations),
            "citation_binding_count": len(citation_bindings or []),
            "agent_final_section": bool(
                (runtime_context or {}).get("agent_final_section", False)
            ),
        }
        return report

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "self_rag_lite",
            "use_llm": self.use_llm,
            "llm_available": bool(
                self.default_generator is not None
                or self.build_context.get("model_gateway") is not None
            ),
            "model_gateway_available": (
                self.build_context.get("model_gateway") is not None
            ),
            "fallback_to_deterministic": self.fallback_to_deterministic,
            "noise_terms": list(self.noise_terms),
        }


class NoOpGenerationCheckerPlugin:
    def __init__(self, *, build_context: Any = None) -> None:
        del build_context

    def check(
        self,
        *,
        query: str,
        answer: str | None,
        context: str,
        citations: list[dict[str, Any]],
        citation_bindings: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> None:
        del query, answer, context, citations, citation_bindings, runtime_context
        return None

    def execution_metadata(self) -> dict[str, Any]:
        return {"enabled": False, "mode": "noop"}


class LocalRewriteRepairStrategyPlugin:
    """Rewrite an unsupported section using only the current evidence."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        max_new_tokens: int = 768,
        fallback_to_original: bool = True,
    ) -> None:
        self.build_context = _context(build_context)
        context_llm_enabled = bool(
            self.build_context.get("enable_quality_llm", False)
        )
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
        if not bool(check.get("need_rewrite")):
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
        prompt = (
            "请对下面章节执行一次局部证据约束改写。只修复检查器指出的问题，"
            "不要扩展章节范围，不得增加资料中不存在的事实、数量、产品型号、"
            "预算、工期或人员承诺。只输出修订后的完整正文。\n"
            "引用将在改写后重新绑定，不要为了形式强行插入引用。\n\n"
            f"章节任务：{query}\n\n"
            f"检查问题：{check.get('problems') or []}\n"
            f"疑似无支撑内容：{check.get('unsupported_claims') or []}\n"
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
