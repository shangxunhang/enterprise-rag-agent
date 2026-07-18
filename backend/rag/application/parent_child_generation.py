"""Context packing, prompt building, answer generation and configured checking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from rag.ports.generation import TextGenerator
from rag.ports.pipeline import ContextPackerPort, PromptBuilderPort


@dataclass
class GenerationStageResult:
    context_pack: Any
    prompt_result: Any
    answer: Optional[str]
    llm_latency_ms: Optional[int]
    model_name: Optional[str]
    model_provider: Optional[str]
    generation_params: Optional[Dict[str, Any]]
    self_rag: Optional[Dict[str, Any]]
    repair: Optional[Dict[str, Any]]
    generation_checker_metadata: Dict[str, Any]
    repair_strategy_metadata: Dict[str, Any]


def infer_model_name(
    generator: TextGenerator | None,
    fallback: Optional[str] = None,
) -> Optional[str]:
    if fallback:
        return fallback
    if generator is None:
        return None
    for attr in ("model_name", "model_path", "model", "name"):
        value = getattr(generator, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return generator.__class__.__name__


class ParentChildGenerationPipeline:
    def __init__(
        self,
        *,
        context_packer: ContextPackerPort,
        prompt_builder: PromptBuilderPort,
        llm_generator: TextGenerator | None = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        generation_checker: Any = None,
        repair_strategy: Any = None,
        self_rag_judge: Any = None,
        timer: Timer | None = None,
    ) -> None:
        self.context_packer = context_packer
        self.prompt_builder = prompt_builder
        self.llm_generator = llm_generator
        self.model_name = model_name
        self.model_provider = model_provider
        # ``self_rag_judge`` is a compatibility-only injection path. The
        # configured runtime always supplies ``generation_checker``.
        self.generation_checker = generation_checker or self_rag_judge
        self.repair_strategy = repair_strategy
        self.timer = timer or MonotonicTimer()

    @staticmethod
    def _component_metadata(component: Any, *, missing_mode: str) -> dict[str, Any]:
        if component is None:
            return {"enabled": False, "mode": missing_mode}
        method = getattr(component, "execution_metadata", None)
        if callable(method):
            return dict(method() or {})
        return {
            "enabled": True,
            "mode": "legacy_component",
            "implementation": (
                f"{component.__class__.__module__}."
                f"{component.__class__.__qualname__}"
            ),
        }

    def run(
        self,
        query: str,
        results: list[dict[str, Any]],
        *,
        generate_answer: bool,
        generation_params: Optional[Dict[str, Any]],
        self_rag_enabled: bool = False,
    ) -> GenerationStageResult:
        context_pack = self.context_packer.pack(results)
        prompt_result = self.prompt_builder.build(
            query=query,
            packed_context=context_pack.context,
            citations=context_pack.citations,
        )
        answer = None
        latency = None
        final_params = None
        model_name = (
            infer_model_name(self.llm_generator, self.model_name)
            if generate_answer
            else None
        )
        provider = self.model_provider if generate_answer else None
        if generate_answer:
            final_params = dict(generation_params or {})
            started = self.timer.now()
            answer = self.llm_generator.generate(prompt_result.prompt, **final_params)
            latency = elapsed_ms(self.timer, started)
            answer = str(answer).strip()

        checker_metadata = self._component_metadata(
            self.generation_checker,
            missing_mode="missing",
        )
        checker_metadata["legacy_enable_self_rag"] = bool(self_rag_enabled)
        checker_metadata["legacy_flag_ignored"] = self.generation_checker is not None

        repair_metadata = self._component_metadata(
            self.repair_strategy,
            missing_mode="missing",
        )

        self_rag = None
        if self.generation_checker is not None:
            check = getattr(self.generation_checker, "check", None)
            if callable(check):
                self_rag = check(
                    query=query,
                    answer=answer,
                    context=context_pack.context,
                    citations=context_pack.citations,
                    citation_bindings=[],
                    runtime_context={"agent_final_section": False},
                )
            elif self_rag_enabled and hasattr(
                self.generation_checker, "check_answer"
            ):
                # Compatibility path for older injected SelfRAGJudge objects.
                self_rag = self.generation_checker.check_answer(
                    query=query,
                    answer=answer,
                    context=context_pack.context,
                    citations=context_pack.citations,
                ).to_dict()

        repair = None
        if self.repair_strategy is not None and answer is not None:
            repair_output = self.repair_strategy.repair(
                query=query,
                answer=answer,
                context=context_pack.context,
                citations=context_pack.citations,
                citation_bindings=[],
                check_result=self_rag,
                runtime_context={"agent_final_section": False},
            )
            answer = repair_output.answer
            repair = dict(repair_output.report)
            repair["repaired"] = bool(repair_output.repaired)

        return GenerationStageResult(
            context_pack=context_pack,
            prompt_result=prompt_result,
            answer=answer,
            llm_latency_ms=latency,
            model_name=model_name,
            model_provider=provider,
            generation_params=final_params,
            self_rag=self_rag,
            repair=repair,
            generation_checker_metadata=checker_metadata,
            repair_strategy_metadata=repair_metadata,
        )
