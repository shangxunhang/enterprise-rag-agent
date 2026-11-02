"""Start-HEAD technical length oracle and soft-target orthogonality."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.scheme_writer.model_service import (
    SectionModelService,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_content_recovery import (
    SectionContentRecovery,
)
from apps.enterprise_document.services.scheme_writer.section_technical_length_policy import (
    build_section_technical_length_policy,
)
from context_manager.manager import LLMContextManager
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema


@pytest.mark.parametrize(
    (
        "max_tokens",
        "base",
        "technical_max",
        "compact",
        "compress",
        "continue_tokens",
        "retry_tokens",
    ),
    [
        (256, 600, 900, 480, 256, 256, 256),
        (512, 600, 900, 480, 390, 256, 512),
        (1024, 1024, 1536, 696, 640, 256, 768),
        (2048, 1200, 1800, 800, 640, 256, 768),
    ],
)
def test_start_head_technical_values_are_preserved(
    max_tokens: int,
    base: int,
    technical_max: int,
    compact: int,
    compress: int,
    continue_tokens: int,
    retry_tokens: int,
) -> None:
    # These literal expectations were frozen from the 7a6a865 start-HEAD
    # formulas before implementing V3. They are not produced by the helper.
    policy = build_section_technical_length_policy(max_tokens)
    assert policy.technical_recovery_target_chars == base
    assert policy.technical_max_section_chars == technical_max
    assert policy.prefix_recovery_max_chars == technical_max
    assert policy.compression_trigger_chars == technical_max
    assert policy.compact_retry_target_chars == compact
    assert policy.compression_max_tokens == compress
    assert policy.continuation_max_tokens == continue_tokens
    assert policy.retry_max_tokens == retry_tokens
    assert policy.validation_rewrite_max_tokens == retry_tokens
    assert policy.continuation_remaining_chars("甲" * 100) == max(160, base - 100)
    assert policy.continuation_remaining_chars("甲" * 5000) == 160
    with pytest.raises(FrozenInstanceError):
        policy.technical_recovery_target_chars = 1


def _response(content: str, *, finish_reason: str = "stop") -> ModelResponseSchema:
    return ModelResponseSchema(
        model_call_id="technical_recovery_fake",
        task_id="technical_task",
        run_id="technical_run",
        model_name="fake",
        success=True,
        content=content,
        finish_reason=finish_reason,
        created_at="2026-09-28T00:00:00+00:00",
    )


class _NoPromptLength:
    @staticmethod
    def target_section_chars(project_input: object) -> int:
        raise AssertionError("technical recovery must not use the initial target")


class _RecoveryModel:
    def __init__(self) -> None:
        self.compression_calls = 0
        self.retry_calls = 0
        self.prefix_bounds: list[int] = []

    def retry_truncated_section(
        self, *args: object, **kwargs: object
    ) -> ModelResponseSchema:
        self.retry_calls += 1
        return _response("完整句子。" * 320, finish_reason="length")

    def retry_target_truncated_section(
        self, *args: object, **kwargs: object
    ) -> ModelResponseSchema:
        self.retry_calls += 1
        return _response("完整句子。" * 320, finish_reason="length")

    def recover_complete_prefix(
        self, content: str, *, min_chars: int, max_chars: int
    ) -> str | None:
        self.prefix_bounds.append(max_chars)
        capped = content[:max_chars]
        end = capped.rfind("。")
        candidate = capped[: end + 1]
        return candidate if len(candidate) >= min_chars else None

    def compress_overlong_section(
        self, *args: object, **kwargs: object
    ) -> ModelResponseSchema:
        self.compression_calls += 1
        return _response("压缩完成。" * 100)

    def compress_target_overlong_section(
        self, *args: object, **kwargs: object
    ) -> ModelResponseSchema:
        self.compression_calls += 1
        return _response("压缩完成。" * 100)


def _input(target: int | None, max_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        generation_requirements=SimpleNamespace(
            target_section_chars=target,
            max_tokens_per_section=max_tokens,
            min_section_chars=80,
            max_section_retries=1,
        )
    )


@pytest.mark.parametrize("target", [None, 1000, 3000])
@pytest.mark.parametrize("target_path", [False, True])
def test_recovery_threshold_and_legacy_dto_meaning_ignore_soft_target(
    target: int | None, target_path: bool
) -> None:
    model = _RecoveryModel()
    recovery = SectionContentRecovery(
        model_service=model, prompt_service=_NoPromptLength()
    )
    # 1600 characters exceeds the old 1024-token technical bound of 1536.
    response = _response("完整句子。" * 320)
    project_input = _input(target, 1024)
    if target_path:
        result = recovery.recover_target(
            SimpleNamespace(),
            response=response,
            project_input=project_input,
            recovery_context=SimpleNamespace(),
        )
    else:
        result = recovery.recover(
            SimpleNamespace(),
            response=response,
            model_section_id="section_001",
            section_title="技术方案",
            project_input=project_input,
            citations=[],
            rag_context=SimpleNamespace(),
        )
    assert model.compression_calls == 1
    assert model.retry_calls == 0
    assert result.technical_recovery_target_chars == 1024
    assert result.technical_max_section_chars == 1536
    assert result.target_section_chars == 1024  # explicit legacy technical alias
    assert result.max_section_chars == 1536  # explicit legacy technical alias
    assert result.requested_target_section_chars == target


@pytest.mark.parametrize("target", [None, 1000, 3000])
def test_prefix_bound_and_retry_count_ignore_soft_target(target: int | None) -> None:
    model = _RecoveryModel()
    recovery = SectionContentRecovery(
        model_service=model, prompt_service=_NoPromptLength()
    )
    result = recovery.recover(
        SimpleNamespace(),
        response=_response("未完句子", finish_reason="length"),
        model_section_id="section_001",
        section_title="技术方案",
        project_input=_input(target, 1024),
        citations=[],
        rag_context=SimpleNamespace(),
    )
    assert model.retry_calls == 1
    assert model.prefix_bounds == [1536]
    assert result.truncation_recovery_strategy == "complete_sentence_prefix"
    assert result.technical_max_section_chars == 1536


def test_token_budget_changes_technical_bound_but_keeps_requested_target() -> None:
    model = _RecoveryModel()
    recovery = SectionContentRecovery(
        model_service=model, prompt_service=_NoPromptLength()
    )
    result = recovery.recover(
        SimpleNamespace(),
        response=_response("完整句子。" * 320),
        model_section_id="section_001",
        section_title="技术方案",
        project_input=_input(3000, 2048),
        citations=[],
        rag_context=SimpleNamespace(),
    )
    assert model.compression_calls == 0
    assert result.technical_recovery_target_chars == 1200
    assert result.technical_max_section_chars == 1800
    assert result.requested_target_section_chars == 3000


class _TechnicalPrompt:
    @staticmethod
    def target_section_chars(project_input: object) -> int:
        raise AssertionError("no technical model call may read an initial target")

    @staticmethod
    def section_generation_contract(section_title: str, project_input: object) -> str:
        return "仅编写当前章节。"

    @staticmethod
    def citation_catalog(citations: object) -> str:
        return "[]"


class _CaptureModelService(SectionModelService):
    def __init__(self) -> None:
        self.prompt_service = _TechnicalPrompt()
        self.calls: list[dict] = []

    def call_model(self, shared_state: object, **kwargs: object) -> ModelResponseSchema:
        self.calls.append(dict(kwargs))
        return _response("完整句子。")

    @staticmethod
    def _load_target_recovery_package(recovery_context: object) -> SimpleNamespace:
        return SimpleNamespace(model_dump=lambda **kwargs: {})


def _v3_input(target: int | None, max_tokens: int) -> ProjectInputSchema:
    generation_requirements: dict[str, object] = {
        "required_sections": ["正文"],
        "citation_required_sections": [],
        "max_tokens_per_section": max_tokens,
    }
    if target is not None:
        generation_requirements["target_section_chars"] = target
    return ProjectInputSchema.model_validate(
        {
            "schema_version": "project_input_v3",
            "task_id": "technical_task",
            "task_type": "scheme_generation",
            "user_query": "撰写正文",
            "generation_requirements": generation_requirements,
            "output_schema": {"required_sections": ["正文"]},
        }
    )


@pytest.mark.parametrize("version", ["project_input_v1", "project_input_v2"])
def test_rewrite_projection_keeps_old_project_input_bytes(version: str) -> None:
    project_input = ProjectInputSchema.model_validate(
        {
            "schema_version": version,
            "task_id": "technical_task",
            "task_type": "scheme_generation",
            "user_query": "撰写正文",
            "generation_requirements": {"required_sections": ["正文"]},
            "output_schema": {"required_sections": ["正文"]},
        }
    )
    assert project_input.model_dump(
        exclude={"generation_requirements": {"target_section_chars"}}
    ) == project_input.model_dump()


@pytest.mark.parametrize("target", [None, 1000, 3000])
def test_real_model_service_technical_prompts_ignore_soft_target(
    target: int | None,
) -> None:
    service = _CaptureModelService()
    project_input = _v3_input(target, 1024)
    state = SimpleNamespace(task_id="technical_task", run_id="technical_run")
    rag_context = RAGContextSchema(context_text="依据", max_context_chars=6000)

    service._continue_truncated_section(
        state,
        original_content="甲" * 100,
        section_id="section_001",
        section_title="正文",
        project_input=project_input,
        citations=[],
        rag_context=rag_context,
    )
    continuation = service.calls.pop()
    assert "续写最多 924 个汉字" in continuation["prompt"]
    assert continuation["max_tokens_override"] == 256

    service.retry_truncated_section(
        state,
        section_id="section_001",
        section_title="正文",
        project_input=project_input,
        citations=[],
        rag_context=rag_context,
        retry_index=1,
    )
    retry = service.calls.pop()
    assert "正文控制在 696 个汉字以内" in retry["prompt"]
    assert retry["max_tokens_override"] == 768

    recovery_context = SimpleNamespace(
        task_id="technical_task",
        run_id="technical_run",
        section_id="section_001",
        section_title="正文",
        citation_ids=(),
        prompt_id="locked_prompt",
        prompt_version="v1",
        initial_prompt_text=f"锁定的初始软目标：{target}",
    )
    service.retry_target_truncated_section(
        state,
        project_input=project_input,
        recovery_context=recovery_context,
        retry_index=1,
    )
    target_retry = service.calls.pop()
    assert "正文不超过696个汉字" in target_retry["prompt"]
    assert target_retry["max_tokens_override"] == 768
    # The locked initial prompt remains visible as immutable semantics, while
    # the technical retry wrapper and token limit stay target-independent.
    assert recovery_context.initial_prompt_text in target_retry["prompt"]

    service._rewrite_invalid_section(
        state,
        original_content="原始正文。",
        section_id="section_001",
        section_title="正文",
        project_input=project_input,
        citations=[],
        semantic_issues=[],
    )
    rewrite = service.calls.pop()
    assert "正文建议不超过 1024 个汉字" in rewrite["prompt"]
    assert '"target_section_chars"' not in rewrite["prompt"]
    assert rewrite["max_tokens_override"] == 768


@pytest.mark.parametrize("target", [None, 1000, 3000])
def test_real_compression_request_keeps_technical_tokens(target: int | None) -> None:
    requests = []

    class _Gateway:
        @staticmethod
        def routing_capabilities(*, model_role: str) -> dict:
            assert model_role == "repair"
            return {"safe_context_window": 8192, "safe_max_output_tokens": 1024}

        def generate(self, request):
            requests.append(request)
            return _response("压缩完成。")

    service = SectionModelService(
        model_gateway=_Gateway(),
        model_name="fake",
        agent_name="SchemeWriterAgent",
        context_manager=LLMContextManager(),
        prompt_service=_TechnicalPrompt(),
        runtime_support=SchemeWriterRuntimeSupport(),
    )
    state = SimpleNamespace(
        task_id="technical_task", run_id="technical_run", generated_outputs={}
    )
    service.compress_overlong_section(
        state,
        original_content="完整句子。" * 320,
        section_id="section_001",
        section_title="正文",
        project_input=_v3_input(target, 1024),
        citations=[],
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.max_tokens == 640
    assert "目标不超过 1024 个汉字" in request.prompt
    assert "1536 个字符" in request.prompt
    assert request.extra["llm_context_summary"]["reserved_output_tokens"] == 640


@pytest.mark.parametrize("target", [1000, 3000])
def test_target_compression_keeps_locked_reserve_and_technical_limit(
    target: int,
) -> None:
    requests = []

    class _Gateway:
        @staticmethod
        def routing_capabilities(*, model_role: str) -> dict:
            assert model_role == "repair"
            return {"safe_context_window": 8192, "safe_max_output_tokens": 1024}

        def generate(self, request):
            requests.append(request)
            return _response("压缩完成。")

    service = SectionModelService(
        model_gateway=_Gateway(),
        model_name="fake",
        agent_name="SchemeWriterAgent",
        context_manager=LLMContextManager(),
        prompt_service=_TechnicalPrompt(),
        runtime_support=SchemeWriterRuntimeSupport(),
    )
    # Scope the seam to the already locked package. The actual context manager
    # and model call boundary remain active for the compression request.
    initial_package = SimpleNamespace(
        selected_items=[],
        budget=SimpleNamespace(
            max_context_chars=6000,
            max_input_tokens=8192,
            reserved_output_tokens=500,
            safety_margin_tokens=256,
        ),
        lineage={},
    )
    service._load_target_recovery_package = lambda recovery_context: initial_package
    service._assert_target_semantic_items = lambda package, recovery_context: None
    recovery_context = SimpleNamespace(
        task_id="technical_task",
        run_id="technical_run",
        section_id="section_001",
        section_title="正文",
        citation_ids=(),
        context_package_id="locked_package",
        context_sha256="locked_hash",
        evidence_bundle_sha256="evidence_hash",
        prompt_id="locked_prompt",
        prompt_version="v1",
    )
    state = SimpleNamespace(
        task_id="technical_task", run_id="technical_run", generated_outputs={}
    )
    service.compress_target_overlong_section(
        state,
        original_content="完整句子。" * 320,
        project_input=_v3_input(target, 1024),
        recovery_context=recovery_context,
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.max_tokens == 640
    assert "压缩至不超过1024个汉字" in request.prompt
    assert "1536个字符" in request.prompt
    assert request.extra["llm_context_summary"]["reserved_output_tokens"] == 500
