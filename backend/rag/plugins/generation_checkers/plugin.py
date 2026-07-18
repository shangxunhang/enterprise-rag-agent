"""Configuration-driven answer/section checker plugins."""

from __future__ import annotations

from typing import Any, Iterable

from rag.judge.rag_quality_judge import SelfRAGJudge
from rag.plugins.quality_support import resolve_quality_generator


def _context(build_context: Any) -> dict[str, Any]:
    return build_context if isinstance(build_context, dict) else {}


class SelfRAGLiteGenerationCheckerPlugin:
    """Check final answer or Agent section support with Self-RAG-lite."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Iterable[str] | None = None,
    ) -> None:
        self.build_context = _context(build_context)
        context_llm_enabled = bool(self.build_context.get("enable_quality_llm", False))
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
        backend = self.backend if generator is self.default_generator else self._build_backend(generator)
        report = backend.check_answer(
            query=query,
            answer=answer,
            context=context,
            citations=citations,
        ).to_dict()
        metadata = dict(report.get("metadata") or {})
        metadata.update(
            {
                "citation_count": len(citations),
                "citation_binding_count": len(citation_bindings or []),
                "agent_final_section": bool(
                    (runtime_context or {}).get("agent_final_section", False)
                ),
            }
        )
        report["metadata"] = metadata
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
            "model_gateway_available": self.build_context.get("model_gateway") is not None,
            "fallback_to_deterministic": self.fallback_to_deterministic,
            "noise_terms": list(self.noise_terms),
        }


class NoOpGenerationCheckerPlugin:
    """Explicit profile-selected disabled answer checker."""

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
        return {
            "enabled": False,
            "mode": "noop",
        }
