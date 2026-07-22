from __future__ import annotations

from types import SimpleNamespace

from apps.enterprise_document.schemas.scheme_writer_schema import (
    SemanticGateResultSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_content_recovery import (
    SectionContentRecovery,
)
from apps.enterprise_document.services.scheme_writer.section_quality_evaluator import (
    SectionQualityEvaluator,
)
from schemas.model import ModelResponseSchema
from schemas.status import ExecutionStatus


def _response(
    content: str,
    *,
    finish_reason: str = "stop",
    model_call_id: str = "model_call_1",
) -> ModelResponseSchema:
    return ModelResponseSchema(
        model_call_id=model_call_id,
        task_id="task_1",
        run_id="run_1",
        model_name="fake_llm",
        success=True,
        content=content,
        finish_reason=finish_reason,
        created_at="2026-07-22T00:00:00+00:00",
    )


class _PromptService:
    @staticmethod
    def target_section_chars(project_input) -> int:
        return 100


class _ModelService:
    def __init__(self) -> None:
        self.retry_calls = 0
        self.compression_calls = 0
        self.retry_response = _response(
            "修复后的完整章节内容。",
            model_call_id="model_retry",
        )
        self.compression_response = _response(
            "压缩后的完整章节内容。",
            model_call_id="model_compress",
        )

    def retry_truncated_section(self, *args, **kwargs) -> ModelResponseSchema:
        self.retry_calls += 1
        return self.retry_response

    @staticmethod
    def recover_complete_prefix(content: str, *, min_chars: int, max_chars: int):
        capped = content[:max_chars]
        index = capped.rfind("。")
        if index < 0:
            return None
        candidate = capped[: index + 1]
        return candidate if len(candidate) >= min_chars else None

    def compress_overlong_section(self, *args, **kwargs) -> ModelResponseSchema:
        self.compression_calls += 1
        return self.compression_response


def _project_input(*, min_chars: int = 1, retries: int = 1):
    return SimpleNamespace(
        generation_requirements=SimpleNamespace(
            min_section_chars=min_chars,
            max_section_retries=retries,
        )
    )


def test_section_content_recovery_retries_truncated_output() -> None:
    model_service = _ModelService()
    recovery = SectionContentRecovery(
        model_service=model_service,
        prompt_service=_PromptService(),
    )

    result = recovery.recover(
        SimpleNamespace(),
        response=_response("未完成的章节", finish_reason="length"),
        model_section_id="section_001",
        section_title="技术方案",
        project_input=_project_input(),
        citations=[],
        rag_context=SimpleNamespace(),
    )

    assert model_service.retry_calls == 1
    assert result.content == "修复后的完整章节内容。"
    assert result.truncation.truncated is False
    assert [item.model_call_id for item in result.truncation_retry_responses] == [
        "model_retry"
    ]


def test_section_content_recovery_compresses_complete_overlong_output() -> None:
    model_service = _ModelService()
    recovery = SectionContentRecovery(
        model_service=model_service,
        prompt_service=_PromptService(),
    )
    content = "完整句子。" * 40

    result = recovery.recover(
        SimpleNamespace(),
        response=_response(content),
        model_section_id="section_001",
        section_title="技术方案",
        project_input=_project_input(retries=0),
        citations=[],
        rag_context=SimpleNamespace(),
    )

    assert model_service.compression_calls == 1
    assert result.content == "压缩后的完整章节内容。"
    assert result.compression_response.model_call_id == "model_compress"
    assert result.overlong is False


def _quality_evaluator() -> SectionQualityEvaluator:
    return SectionQualityEvaluator(runtime_support=SchemeWriterRuntimeSupport())


def test_section_quality_evaluator_returns_success_for_clean_section() -> None:
    result = _quality_evaluator().evaluate(
        section_id="section_001",
        section_title="技术方案",
        content="这是有依据且完整的章节。",
        truncation=TruncationCheckSchema(truncated=False),
        max_section_chars=1000,
        citation_ok=True,
        semantic_gate=SemanticGateResultSchema(decision="pass"),
        generation_check_result=None,
        repair_result=None,
        repair_accepted=False,
        truncation_recovery_strategy=None,
        compression_fallback_strategy=None,
    )

    assert result.status == ExecutionStatus.SUCCESS
    assert result.error is None
    assert result.warnings == []
    assert result.eval_result.passed is True


def test_section_quality_evaluator_maps_advisory_failure_to_partial_success() -> None:
    result = _quality_evaluator().evaluate(
        section_id="section_001",
        section_title="技术方案",
        content="这是完整章节。",
        truncation=TruncationCheckSchema(truncated=False),
        max_section_chars=1000,
        citation_ok=True,
        semantic_gate=SemanticGateResultSchema(decision="pass"),
        generation_check_result={
            "is_supported": False,
            "need_rewrite": True,
            "need_retrieve_more": False,
        },
        repair_result=None,
        repair_accepted=False,
        truncation_recovery_strategy=None,
        compression_fallback_strategy=None,
    )

    assert result.status == ExecutionStatus.PARTIAL_SUCCESS
    assert result.error is None
    assert "self_rag:generation_check_failed" in result.eval_result.warnings
    assert result.warnings[0].warning_code == "SECTION_SELF_RAG_WARNING"


def test_section_quality_evaluator_keeps_missing_citation_as_hard_failure() -> None:
    result = _quality_evaluator().evaluate(
        section_id="section_001",
        section_title="技术方案",
        content="这是完整章节。",
        truncation=TruncationCheckSchema(truncated=False),
        max_section_chars=1000,
        citation_ok=False,
        semantic_gate=SemanticGateResultSchema(decision="pass"),
        generation_check_result=None,
        repair_result=None,
        repair_accepted=False,
        truncation_recovery_strategy=None,
        compression_fallback_strategy=None,
    )

    assert result.status == ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.error_code == "SECTION_HARD_GATE_FAILED"
    assert result.eval_result.failures == ["citation_bound"]
