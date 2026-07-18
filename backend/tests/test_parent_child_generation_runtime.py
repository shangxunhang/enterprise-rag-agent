"""Runtime regression tests for the decoupled parent-child generation stage."""

from __future__ import annotations

from dataclasses import dataclass

from rag.application.parent_child_generation import ParentChildGenerationPipeline


@dataclass
class _ContextPack:
    context: str = "evidence context"
    citations: list[dict] = None

    def __post_init__(self) -> None:
        if self.citations is None:
            self.citations = [{"citation_id": "C1"}]


@dataclass
class _PromptResult:
    prompt: str = "prompt"


class _ContextPacker:
    def pack(self, results):
        return _ContextPack()


class _PromptBuilder:
    def build(self, *, query, packed_context, citations):
        return _PromptResult()


class _SelfRAGResult:
    def to_dict(self):
        return {"decision": "pass"}


class _SelfRAGJudge:
    def __init__(self) -> None:
        self.calls = 0

    def check_answer(self, *, query, answer, context, citations):
        self.calls += 1
        assert query == "q"
        assert context == "evidence context"
        assert citations == [{"citation_id": "C1"}]
        return _SelfRAGResult()


def test_generation_pipeline_uses_injected_self_rag_judge() -> None:
    judge = _SelfRAGJudge()
    pipeline = ParentChildGenerationPipeline(
        context_packer=_ContextPacker(),
        prompt_builder=_PromptBuilder(),
        self_rag_judge=judge,
    )

    result = pipeline.run(
        query="q",
        results=[],
        generate_answer=False,
        generation_params=None,
        self_rag_enabled=True,
    )

    assert judge.calls == 1
    assert result.self_rag == {"decision": "pass"}
