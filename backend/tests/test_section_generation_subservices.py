from __future__ import annotations

from types import SimpleNamespace

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SemanticGateResultSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.scheme_writer.citation_service import CitationService
from apps.enterprise_document.services.scheme_writer.grounding_repair_service import (
    GroundingRepairService,
)
from apps.enterprise_document.services.scheme_writer.model_service import SectionModelService
from apps.enterprise_document.services.scheme_writer.runtime_support import (
    SchemeWriterRuntimeSupport,
)
from apps.enterprise_document.services.scheme_writer.section_content_recovery import (
    SectionContentRecovery,
)
from apps.enterprise_document.services.scheme_writer.section_quality_evaluator import (
    SectionQualityEvaluator,
)
from context_manager.manager import ContextBudgetExceededError, LLMContextManager
from schemas.citation import CitationSchema
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


def test_section_content_recovery_falls_back_when_compression_context_cannot_fit() -> None:
    class _BudgetFailingCompressionModelService(_ModelService):
        def compress_overlong_section(self, *args, **kwargs) -> ModelResponseSchema:
            self.compression_calls += 1
            raise ContextBudgetExceededError("compression required context cannot fit")

    model_service = _BudgetFailingCompressionModelService()
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
    assert result.compression_response is None
    assert len(result.content) <= result.max_section_chars
    assert result.overlong is False
    assert (
        result.compression_fallback_strategy
        == "compression_context_budget_complete_sentence_prefix"
    )


def test_compression_uses_managed_context_before_model_boundary() -> None:
    recorded_requests = []

    class _Gateway:
        @staticmethod
        def routing_capabilities(*, model_role: str) -> dict:
            assert model_role == "repair"
            return {
                "safe_context_window": 8192,
                "safe_max_output_tokens": 1024,
            }

        def generate(self, request):
            recorded_requests.append(request)
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name="fake_repair",
                success=True,
                content="压缩后的网络资源建设方案。",
                finish_reason="stop",
                created_at=request.created_at,
            )

    class _CompressionPromptService:
        @staticmethod
        def target_section_chars(project_input) -> int:
            return 1024

        @staticmethod
        def section_generation_contract(section_title, project_input) -> str:
            return f"只压缩‘{section_title}’本章，不新增事实。"

    service = SectionModelService(
        model_gateway=_Gateway(),
        model_name="fake_repair",
        agent_name="SchemeWriterAgent",
        context_manager=LLMContextManager(),
        prompt_service=_CompressionPromptService(),
        runtime_support=SchemeWriterRuntimeSupport(),
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_compression_context",
            "task_type": "scheme_generation",
            "user_query": "生成网络资源建设方案",
            "generation_requirements": {
                "required_sections": ["网络资源建设方案"],
                "citation_required_sections": ["网络资源建设方案"],
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {"required_sections": ["网络资源建设方案"]},
        }
    )
    citations = [
        CitationSchema(
            citation_id=f"C{index}",
            source_type="document",
            doc_id=f"doc_{index}",
            chunk_id=f"chunk_{index}",
            quote_text=("政务云网络资源建设应遵循高可用、安全隔离和可扩展原则。" * 20),
            extra={"prompt_visible_text": "X" * 800},
        )
        for index in range(1, 25)
    ]
    state = SimpleNamespace(
        task_id="task_compression_context",
        run_id="run_compression_context",
        generated_outputs={},
    )

    response = service.compress_overlong_section(
        state,
        original_content=("网络资源建设方案正文。" * 150) + "。",
        section_id="section_006",
        section_title="网络资源建设方案",
        project_input=project_input,
        citations=citations,
    )

    assert response.success is True
    assert len(recorded_requests) == 1
    request = recorded_requests[0]
    summary = request.extra["llm_context_summary"]
    assert request.model_role == "repair"
    assert summary["managed"] is True
    assert summary["max_context_chars"] == 6000
    assert summary["used_context_chars"] <= 6000
    assert (
        summary["decision_counts"]["truncated"]
        + summary["decision_counts"]["dropped"]
        >= 1
    )
    assert "请执行压缩改写" in request.prompt
    assert len(request.prompt) > summary["used_context_chars"]


def test_citation_repair_uses_managed_context_before_model_boundary() -> None:
    recorded_requests = []

    class _Gateway:
        @staticmethod
        def routing_capabilities(*, model_role: str) -> dict:
            assert model_role == "repair"
            return {
                "safe_context_window": 8192,
                "safe_max_output_tokens": 1024,
            }

        def generate(self, request):
            recorded_requests.append(request)
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name="fake_repair",
                success=True,
                content="未形成有效引用绑定。",
                finish_reason="stop",
                created_at=request.created_at,
            )

    model_service = SectionModelService(
        model_gateway=_Gateway(),
        model_name="fake_repair",
        agent_name="SchemeWriterAgent",
        context_manager=LLMContextManager(),
        prompt_service=SimpleNamespace(),
        runtime_support=SchemeWriterRuntimeSupport(),
    )
    service = GroundingRepairService(
        model_service=model_service,
        prompt_service=SimpleNamespace(),
        citation_service=CitationService(),
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_citation_repair_context",
            "task_type": "scheme_generation",
            "user_query": "修复网络资源建设方案引用",
            "generation_requirements": {
                "required_sections": ["网络资源建设方案"],
                "citation_required_sections": ["网络资源建设方案"],
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {"required_sections": ["网络资源建设方案"]},
        }
    )
    citations = [
        CitationSchema(
            citation_id=f"C{index}",
            source_type="document",
            doc_id=f"doc_{index}",
            chunk_id=f"chunk_{index}",
            quote_text=("政务云网络资源建设应遵循高可用、安全隔离和可扩展原则。" * 20),
            extra={"prompt_visible_text": "X" * 800},
        )
        for index in range(1, 25)
    ]
    original_content = ("网络资源建设方案正文必须保持原样。" * 80) + "。"
    state = SimpleNamespace(
        task_id="task_citation_repair_context",
        run_id="run_citation_repair_context",
        generated_outputs={},
    )

    repaired, response = service.repair_section_citations(
        state,
        content=original_content,
        section_id="section_006",
        section_title="网络资源建设方案",
        project_input=project_input,
        citations=citations,
    )

    assert repaired == original_content
    assert response is not None and response.success is True
    assert len(recorded_requests) == 1
    request = recorded_requests[0]
    summary = request.extra["llm_context_summary"]
    assert request.model_role == "repair"
    assert summary["managed"] is True
    assert summary["max_context_chars"] == 6000
    assert summary["used_context_chars"] <= 6000
    assert (
        summary["decision_counts"]["truncated"]
        + summary["decision_counts"]["dropped"]
        >= 1
    )
    assert "本次修复材料" in request.prompt
    assert len(request.prompt) > summary["used_context_chars"]


def test_grounded_regeneration_uses_managed_context_before_model_boundary() -> None:
    recorded_requests = []

    class _Gateway:
        @staticmethod
        def routing_capabilities(*, model_role: str) -> dict:
            assert model_role == "repair"
            return {
                "safe_context_window": 8192,
                "safe_max_output_tokens": 1024,
            }

        def generate(self, request):
            recorded_requests.append(request)
            return ModelResponseSchema(
                model_call_id=request.model_call_id,
                task_id=request.task_id,
                run_id=request.run_id,
                model_name="fake_repair",
                success=True,
                content="依据证据重写后的章节。",
                finish_reason="stop",
                created_at=request.created_at,
            )

    model_service = SectionModelService(
        model_gateway=_Gateway(),
        model_name="fake_repair",
        agent_name="SchemeWriterAgent",
        context_manager=LLMContextManager(),
        prompt_service=SimpleNamespace(),
        runtime_support=SchemeWriterRuntimeSupport(),
    )
    service = GroundingRepairService(
        model_service=model_service,
        prompt_service=SimpleNamespace(),
        citation_service=CitationService(),
    )
    project_input = ProjectInputSchema.model_validate(
        {
            "task_id": "task_grounded_regeneration_context",
            "task_type": "scheme_generation",
            "user_query": "重写网络资源建设方案",
            "project_name": "政务云建设项目",
            "missing_information": ["网络带宽", "设备品牌型号"],
            "generation_requirements": {
                "required_sections": ["网络资源建设方案"],
                "citation_required_sections": ["网络资源建设方案"],
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {"required_sections": ["网络资源建设方案"]},
        }
    )
    citations = [
        CitationSchema(
            citation_id=f"C{index}",
            source_type="document",
            doc_id=f"doc_{index}",
            chunk_id=f"chunk_{index}",
            quote_text=("政务云网络资源建设应遵循高可用、安全隔离和可扩展原则。" * 20),
            extra={"prompt_visible_text": "Y" * 800},
        )
        for index in range(1, 25)
    ]
    state = SimpleNamespace(
        task_id="task_grounded_regeneration_context",
        run_id="run_grounded_regeneration_context",
        generated_outputs={},
    )

    response = service.regenerate_section_from_evidence(
        state,
        original_content=("原始无依据网络方案。" * 200) + "。",
        section_id="section_006",
        section_title="网络资源建设方案",
        project_input=project_input,
        citations=citations,
    )

    assert response.success is True
    assert len(recorded_requests) == 1
    request = recorded_requests[0]
    summary = request.extra["llm_context_summary"]
    assert request.model_role == "repair"
    assert summary["managed"] is True
    assert summary["max_context_chars"] == 6000
    assert summary["used_context_chars"] <= 6000
    assert (
        summary["decision_counts"]["truncated"]
        + summary["decision_counts"]["dropped"]
        >= 1
    )
    assert "本次修复材料" in request.prompt
    assert len(request.prompt) > summary["used_context_chars"]


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
