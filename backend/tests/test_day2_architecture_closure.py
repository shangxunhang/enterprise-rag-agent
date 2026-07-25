"""Architecture closure tests for the revised Day-2 scope."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError

import numpy as np
import pytest
from pydantic import ValidationError

import run_demo as run_demo_script

from apps.enterprise_document.quality.plugins import SelfRAGLiteGenerationCheckerPlugin
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    TruncationCheckSchema,
)
from apps.enterprise_document.services.document_gate import evaluate_scheme_draft
from apps.enterprise_document.services.scheme_writer.capture_service import SchemeCaptureService
from apps.enterprise_document.services.scheme_writer.document_assembler import DocumentAssembler
from apps.enterprise_document.services.scheme_writer.citation_service import CitationService
from apps.enterprise_document.services.scheme_writer.use_case import SchemeGenerationUseCase
from core.retry_policy import RetryDecisionPolicy, classify_failure
from data_capture.lifecycle import DataCaptureLifecycleStore, DataCaptureTransitionService
from eval.rag.capture.rag_eval_runner import RAGEvalRunner
from eval.rag.engine.eval_runner import load_eval_samples
from eval.rag.engine.rag_eval_case_schema import RagEvalCase, load_rag_eval_cases
from eval.rag.strategy.schemas import GoldenDatasetManifest, RAGEvalSample
from experiments.manifest import (
    ArtifactManifestRecord,
    ExperimentRunManifest,
    ExperimentRunManifestWriter,
)
from model_gateway.failure_policy import AvailabilityFailurePolicy
from model_gateway.model_contract import ModelProfile, ModelSelection, ResidencyPolicy
from model_gateway.usage_ledger import ModelUsageLedger
from rag.chunker.cleaned_text_unit_chunker import row_to_unit
from rag.plugins.evidence_assessors.plugin import CRAGEvidenceAssessorPlugin
from schemas.citation import CitationSchema
from rag.retriever.milvus_child_retriever import MilvusChildRetriever
from rag.runtime.resource_pool import ParentChildResourcePool
from rag.services.rag_service import RAGService
from rag.schema.cleaned_text_unit import (
    CleanedTextUnitSchema,
    migrate_legacy_cleaned_text_unit_payload,
)
from rag.schema.eval_schema import EvalSample as LegacyRAGEvalSample
from schemas.citation import CitationBindingSchema
from schemas.data_capture import DataCaptureRecordSchema
from schemas.eval_schema import RAGEvalSampleSchema
from schemas.failure import FailureCategory
from schemas.model import ModelResponseSchema
from schemas.rag import RAGToolInputSchema
from schemas.status import ExecutionStatus
from core.error_factory import ErrorFactory, FIXED_FAILURE_CATEGORY_BY_ERROR_CODE


NOW = "2026-07-23T00:00:00+00:00"


def _run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            f"git {' '.join(args)} failed in {repo}\n"
            f"stdout:\n{exc.stdout}\n"
            f"stderr:\n{exc.stderr}"
        ) from exc
    return result.stdout.strip()


def _binding(
    citation_id: str,
    binding_id: str,
    *,
    section_id: str = "s1",
    chunk_id: str | None = "chunk1",
    verified: bool = True,
) -> CitationBindingSchema:
    return CitationBindingSchema(
        binding_id=binding_id,
        citation_id=citation_id,
        target_document_id="doc_day2",
        target_section_id=section_id,
        target_paragraph_id=f"p_{binding_id}",
        target_claim_id=f"claim_{binding_id}",
        source_document_id="src",
        source_chunk_id=chunk_id,
        claim_text="有效事实",
        quote_text="有效事实",
        metadata={"grounding_verified": verified},
    )


def _draft_with_citations(
    content: str,
    bindings: list[CitationBindingSchema],
) -> SchemeDraftSchema:
    section = SchemeSectionSchema(
        section_id="s1",
        section_title="项目概述",
        section_order=1,
        content=content,
        status=ExecutionStatus.SUCCESS,
        citation_ids=[item.citation_id for item in bindings],
        citation_bindings=list(bindings),
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(passed=True),
    )
    return SchemeDraftSchema(
        draft_id="draft_day2",
        document_id="doc_day2",
        task_id="task_day2",
        run_id="run_day2",
        title="测试方案",
        full_text=f"1、项目概述\n{content}",
        sections=[section],
        required_sections=["项目概述"],
        citation_bindings=list(bindings),
        created_at=NOW,
    )


def _project_input_payload() -> dict:
    return {
        "task_id": "task_day2",
        "task_type": "scheme_generation",
        "user_query": "生成测试方案",
        "generation_requirements": {
            "required_sections": ["项目概述"],
        },
        "output_schema": {
            "required_sections": ["项目概述"],
        },
    }


def test_canonical_project_input_rejects_unknown_fields() -> None:
    payload = _project_input_payload()
    payload["generation_requirements"]["max_toknes_per_section"] = 100
    with pytest.raises(ValidationError):
        ProjectInputSchema.model_validate(payload)


def test_citation_reconciliation_removes_orphan_markers() -> None:
    verified = CitationBindingSchema(
        binding_id="b1",
        citation_id="C1",
        target_document_id="doc",
        target_section_id="s1",
        target_paragraph_id="p1",
        target_claim_id="claim1",
        source_document_id="src",
        source_chunk_id="chunk1",
        claim_text="有效事实",
        quote_text="有效事实",
        metadata={"grounding_verified": True},
    )
    content = "有效事实。[C1] 未验证事实。[C2]"
    assert CitationService.reconcile_citation_markers(content, [verified]) == (
        "有效事实。[C1] 未验证事实。"
    )


def test_document_gate_rejects_visible_marker_without_binding() -> None:
    section = SchemeSectionSchema(
        section_id="s1",
        section_title="项目概述",
        section_order=1,
        content="测试内容。[C9]",
        status=ExecutionStatus.SUCCESS,
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(passed=True),
    )
    draft = SchemeDraftSchema(
        draft_id="draft_day2",
        document_id="doc_day2",
        task_id="task_day2",
        run_id="run_day2",
        title="测试方案",
        full_text="1、项目概述\n测试内容。[C9]",
        sections=[section],
        required_sections=["项目概述"],
        created_at=NOW,
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
    )
    assert result.passed is False
    assert result.metadata["orphan_citation_marker_ids"] == ["C9"]


def test_citation_two_marker_occurrences_with_one_binding_fails() -> None:
    draft = _draft_with_citations(
        "有效事实。[C1] 再次引用。[C1]",
        [_binding("C1", "b1")],
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=True,
        retrieved_chunk_ids=["chunk1"],
    )
    assert result.passed is False
    assert result.metadata["marker_binding_count_mismatches"]["C1"] == {
        "marker_occurrences": 2,
        "binding_count": 1,
    }


def test_citation_one_marker_with_two_bindings_fails() -> None:
    draft = _draft_with_citations(
        "有效事实。[C1]",
        [_binding("C1", "b1"), _binding("C1", "b2")],
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=True,
        retrieved_chunk_ids=["chunk1"],
    )
    assert result.passed is False
    assert result.metadata["marker_binding_count_mismatches"]["C1"]["binding_count"] == 2


def test_citation_duplicate_binding_id_fails() -> None:
    draft = _draft_with_citations(
        "事实一。[C1] 事实二。[C2]",
        [_binding("C1", "dup"), _binding("C2", "dup")],
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=True,
        retrieved_chunk_ids=["chunk1"],
    )
    assert result.passed is False
    assert result.metadata["duplicate_binding_ids"] == ["dup"]


def test_citation_unverified_binding_fails() -> None:
    draft = _draft_with_citations(
        "有效事实。[C1]",
        [_binding("C1", "b1", verified=False)],
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=True,
        retrieved_chunk_ids=["chunk1"],
    )
    assert result.passed is False
    assert result.metadata["unverified_binding_ids"] == ["b1"]


def test_citation_invalid_source_or_target_fails() -> None:
    invalid = _binding("C1", "b1", section_id="missing_section", chunk_id="missing_chunk")
    draft = _draft_with_citations("有效事实。[C1]", [invalid])
    result = evaluate_scheme_draft(
        draft,
        citation_required=True,
        retrieved_chunk_ids=["chunk1"],
    )
    assert result.passed is False
    assert result.checks["citation_targets_valid"] is False


def test_document_assembly_preserves_exact_marker_binding_cardinality() -> None:
    binding = _binding("C1", "b1")
    citation = CitationSchema(
        citation_id="C1",
        source_type="rag",
        source_document_id="src",
        chunk_id="chunk1",
    )
    section = _draft_with_citations("有效事实。[C1]", [binding]).sections[0]
    assembled = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_day2",
            run_id="run_day2",
            document_id="doc_day2",
            document_title="测试方案",
            required_sections=["项目概述"],
            sections=[section],
            citations=[citation],
            document_evidence_available=True,
            document_assessment_status="sufficient",
            citation_required_sections=["项目概述"],
            created_at=NOW,
            updated_at=NOW,
        )
    ).draft
    result = evaluate_scheme_draft(
        assembled,
        citation_required=True,
        citation_required_sections=["项目概述"],
        retrieved_chunk_ids=["chunk1"],
        canonical_citations=[citation],
    )
    assert result.passed is True
    assert result.checks["citation_marker_binding_consistent"] is True


class _CaptureRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs):
        self.calls.append(dict(kwargs))


class _Dumpable:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}

    def model_dump(self) -> dict:
        return dict(self.payload)


def test_capture_schema_rejects_candidate_marked_training_ready() -> None:
    with pytest.raises(ValidationError):
        DataCaptureRecordSchema(
            record_id="capture_1",
            capture_type="sft_candidates",
            capture_stage="candidate",
            task_id="task",
            run_id="run",
            created_at=NOW,
            is_usable_for_training=True,
        )


def test_capture_schema_requires_bidirectional_readiness_invariants() -> None:
    with pytest.raises(ValidationError):
        DataCaptureRecordSchema(
            record_id="capture_training",
            capture_type="sft_candidates",
            capture_stage="training_ready",
            task_id="task",
            run_id="run",
            created_at=NOW,
            is_usable_for_training=False,
        )
    with pytest.raises(ValidationError):
        DataCaptureRecordSchema(
            record_id="capture_eval",
            capture_type="rag_eval",
            capture_stage="eval_ready",
            task_id="task",
            run_id="run",
            created_at=NOW,
            is_usable_for_eval=False,
        )


def test_capture_lifecycle_requires_external_policy_decision_before_training_ready() -> None:
    service = DataCaptureTransitionService()
    raw = DataCaptureRecordSchema(
        record_id="capture_lifecycle",
        capture_type="raw_interactions",
        capture_stage="raw",
        task_id="task",
        run_id="run",
        created_at=NOW,
    )
    candidate = service.transition(raw, "candidate")
    assert candidate.capture_stage == "candidate"
    evaluated = service.transition(candidate, "evaluated", quality_score=0.91)
    assert evaluated.metadata["quality_decision"] == "pending_policy_decision"
    with pytest.raises(ValueError):
        service.transition(evaluated, "accepted")
    accepted = service.transition(
        evaluated,
        "accepted",
        policy_decision="accepted",
    )
    training_ready = service.transition(accepted, "training_ready")
    assert training_ready.capture_stage == "training_ready"
    assert training_ready.is_usable_for_training is True
    assert training_ready.is_usable_for_eval is False


def test_runtime_rejected_capture_cannot_promote_to_readiness() -> None:
    service = DataCaptureTransitionService()
    rejected = DataCaptureRecordSchema(
        record_id="capture_runtime_rejected",
        capture_type="rejected",
        capture_stage="runtime_rejected",
        task_id="task",
        run_id="run",
        created_at=NOW,
    )
    with pytest.raises(ValueError):
        service.transition(rejected, "training_ready")


def test_success_capture_remains_unverified_candidate() -> None:
    recorder = _CaptureRecorder()
    service = SchemeCaptureService(
        data_capture_recorder=recorder,
        agent_name="SchemeWriterAgent",
    )
    draft = SimpleNamespace(
        model_dump=lambda: {},
        sections=[],
        full_text="ok",
    )
    output = SimpleNamespace(
        generated_draft=draft,
        rejected_artifact=None,
        deliverable_final_result=SimpleNamespace(draft=draft),
        rag_context=None,
        retrieved_chunks=[],
        citations=[],
        hard_gate=None,
        schema_version="scheme_writer_output_v1",
        status=ExecutionStatus.SUCCESS,
    )
    shared_state = SimpleNamespace(
        task_id="task_day2",
        run_id="run_day2",
        user_input="test",
        generated_outputs={"model_call_1": {}},
    )
    scheme_input = _Dumpable({"required_sections": ["项目概述"]})
    scheme_input.required_sections = ["项目概述"]

    service.capture(shared_state, scheme_input, output, rag_output={})

    raw = next(item for item in recorder.calls if item["capture_type"] == "raw_interactions")
    eval_sample = next(item for item in recorder.calls if item["capture_type"] == "eval_samples")
    sft = next(item for item in recorder.calls if item["capture_type"] == "sft_candidates")
    assert raw["capture_stage"] == "raw"
    assert eval_sample["capture_stage"] == "candidate"
    assert sft["capture_stage"] == "candidate"
    assert sft["quality_score"] is None
    assert "is_usable_for_training" not in sft
    assert "is_usable_for_eval" not in sft
    assert sft["metadata"]["quality_decision"] == "pending_evaluation"
    assert sft["lineage_refs"]["model_call_ids"] == ["model_call_1"]


def test_failed_capture_has_no_candidate_copy_that_can_bypass_runtime_rejection() -> None:
    recorder = _CaptureRecorder()
    service = SchemeCaptureService(
        data_capture_recorder=recorder,
        agent_name="SchemeWriterAgent",
    )
    draft = SimpleNamespace(
        model_dump=lambda: {},
        sections=[],
        full_text="failed output",
    )
    output = SimpleNamespace(
        generated_draft=draft,
        rejected_artifact=SimpleNamespace(draft=draft),
        deliverable_final_result=None,
        rag_context=None,
        retrieved_chunks=[],
        citations=[],
        hard_gate=None,
        schema_version="scheme_writer_output_v1",
        status=ExecutionStatus.FAILED,
    )
    shared_state = SimpleNamespace(
        task_id="task_day2",
        run_id="run_day2",
        user_input="test",
        generated_outputs={},
    )
    scheme_input = _Dumpable({"required_sections": ["项目概述"]})
    scheme_input.required_sections = ["项目概述"]

    service.capture(shared_state, scheme_input, output, rag_output={})

    eval_sample = next(item for item in recorder.calls if item["capture_type"] == "eval_samples")
    rejected = next(item for item in recorder.calls if item["capture_type"] == "rejected")
    assert eval_sample["capture_stage"] == "runtime_rejected"
    assert rejected["capture_stage"] == "runtime_rejected"
    assert not any(item["capture_type"] == "sft_candidates" for item in recorder.calls)


def _remote_selection() -> ModelSelection:
    return ModelSelection(
        profile=ModelProfile(
            profile_id="remote",
            model_name="remote-model",
            provider="remote-provider",
            residency_policy=ResidencyPolicy.REMOTE,
        )
    )


def test_known_fixed_failure_codes_never_resolve_to_unknown() -> None:
    factory = ErrorFactory()
    assert FIXED_FAILURE_CATEGORY_BY_ERROR_CODE
    for error_code, expected in FIXED_FAILURE_CATEGORY_BY_ERROR_CODE.items():
        error = factory.create(
            error_code=error_code,
            error_type="ContractTest",
            message="known semantic failure",
            component="test",
            failure_category=expected,
        )
        assert error.failure_category == expected
        assert error.failure_category != FailureCategory.UNKNOWN


def test_error_factory_rejects_silent_unknown_and_fixed_category_mismatch() -> None:
    factory = ErrorFactory()
    with pytest.raises(ValueError, match="failure_category must be explicit"):
        factory.create(
            error_code="NEW_UNCLASSIFIED_FAILURE",
            error_type="ContractTest",
            message="must not silently become UNKNOWN",
            component="test",
        )
    with pytest.raises(ValueError, match="requires failure_category"):
        factory.create(
            error_code="WORKFLOW_ROUTING_CYCLE",
            error_type="ContractTest",
            message="wrong category",
            component="test",
            failure_category=FailureCategory.UNKNOWN,
        )


def test_remote_provider_configuration_failure_does_not_fallback() -> None:
    error = ErrorFactory().create(
        error_code="MODEL_GATEWAY_CALL_FAILED",
        error_type="RuntimeError",
        message="401 unauthorized invalid api key",
        component="ModelGateway",
        failure_category=FailureCategory.CONFIGURATION,
    )
    response = ModelResponseSchema(
        model_call_id="call1",
        task_id="task",
        run_id="run",
        model_name="remote-model",
        success=False,
        error=error,
        error_message=error.message,
        created_at=NOW,
    )
    assert AvailabilityFailurePolicy().is_availability_failure(
        response, _remote_selection()
    ) is False


def test_retry_budget_never_makes_configuration_or_validation_retryable() -> None:
    policy = RetryDecisionPolicy()
    max_retries = 3
    assert policy.should_retry(
        FailureCategory.CONFIGURATION,
        budget_remaining=max_retries > 0,
    ) is False
    assert policy.should_retry(
        FailureCategory.VALIDATION,
        budget_remaining=max_retries > 0,
    ) is False
    assert policy.should_retry(FailureCategory.AVAILABILITY) is True
    assert policy.should_retry(FailureCategory.TRANSIENT) is True
    assert policy.should_retry(FailureCategory.CANCELLED) is False


def test_business_quality_retry_policy_is_configurable() -> None:
    assert RetryDecisionPolicy(business_quality_retryable=True).should_retry(
        FailureCategory.BUSINESS_QUALITY
    ) is True
    assert RetryDecisionPolicy(business_quality_retryable=False).should_retry(
        FailureCategory.BUSINESS_QUALITY
    ) is False


def test_timeout_with_live_worker_suppresses_concurrent_retry() -> None:
    policy = RetryDecisionPolicy()
    assert policy.should_retry(FailureCategory.TIMEOUT, worker_still_active=False) is True
    assert policy.should_retry(FailureCategory.TIMEOUT, worker_still_active=True) is False


def test_legacy_oserror_with_auth_message_does_not_fallback() -> None:
    error = ErrorFactory().create(
        error_code="MODEL_GATEWAY_CALL_FAILED",
        error_type="OSError",
        message="HTTP 401 unauthorized invalid api key",
        component="ModelGateway",
        failure_category=FailureCategory.UNKNOWN,
    )
    response = ModelResponseSchema(
        model_call_id="legacy_auth",
        task_id="task",
        run_id="run",
        model_name="remote-model",
        success=False,
        error=error,
        error_message=error.message,
        created_at=NOW,
    )
    assert AvailabilityFailurePolicy().is_availability_failure(
        response,
        _remote_selection(),
    ) is False


@pytest.mark.parametrize("status", [401, 403])
def test_actual_http_auth_error_is_configuration_and_not_fallback(status: int) -> None:
    exc = HTTPError(
        url="https://provider.example/v1/chat",
        code=status,
        msg="Unauthorized" if status == 401 else "Forbidden",
        hdrs=None,
        fp=None,
    )
    category = classify_failure(exc)
    assert category == FailureCategory.CONFIGURATION
    assert RetryDecisionPolicy().should_retry(category) is False
    error = ErrorFactory().create(
        error_code="MODEL_GATEWAY_CALL_FAILED",
        error_type=exc.__class__.__name__,
        message=str(exc),
        component="ModelGateway",
        failure_category=category,
    )
    response = ModelResponseSchema(
        model_call_id=f"call_{status}",
        task_id="task",
        run_id="run",
        model_name="remote-model",
        success=False,
        error=error,
        error_message=error.message,
        created_at=NOW,
    )
    assert AvailabilityFailurePolicy().is_availability_failure(
        response,
        _remote_selection(),
    ) is False


def test_quality_checker_exposes_deterministic_primary_without_degradation() -> None:
    checker = SelfRAGLiteGenerationCheckerPlugin(use_llm=False)
    result = checker.check(
        query="测试方案",
        answer="测试方案需要完成建设。",
        context="测试方案需要完成建设。",
        citations=[],
    )
    assert result["verification_mode"] == "deterministic_primary"
    assert result["quality_mode_degraded"] is False
    assert result["metadata"]["verification_mode"] == "deterministic_primary"


class _FailingQualityGenerator:
    def generate(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError("judge unavailable")


def test_self_rag_llm_failure_exposes_degraded_deterministic_fallback() -> None:
    checker = SelfRAGLiteGenerationCheckerPlugin(
        build_context={
            "enable_quality_llm": True,
            "quality_llm_generator": _FailingQualityGenerator(),
        },
        use_llm=True,
        fallback_to_deterministic=True,
    )
    result = checker.check(
        query="测试方案",
        answer="测试方案需要完成建设。",
        context="测试方案需要完成建设。",
        citations=[],
    )
    assert result["verification_mode"] == "degraded_deterministic_fallback"
    assert result["quality_mode_degraded"] is True
    assert result["metadata"]["fallback_used"] is True


def test_crag_item_fallback_degrades_top_level_quality_mode() -> None:
    assessor = CRAGEvidenceAssessorPlugin(
        build_context={
            "enable_quality_llm": True,
            "quality_llm_generator": _FailingQualityGenerator(),
        },
        use_llm=True,
        fallback_to_deterministic=True,
    )
    assessment = assessor.assess(
        query="测试方案",
        results=[
            {
                "chunk_id": "chunk1",
                "text": "测试方案需要完成建设。",
                "score": 0.9,
            }
        ],
    )
    assert assessment.report["verification_mode"] == "degraded_deterministic_fallback"
    assert assessment.report["quality_mode_degraded"] is True
    assert assessment.metadata["quality_mode_degraded"] is True


def test_document_level_crag_degradation_is_aggregated() -> None:
    section = SchemeSectionSchema(
        section_id="s1",
        section_title="项目概述",
        section_order=1,
        content="限定性输出。",
        status=ExecutionStatus.SUCCESS,
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(passed=True),
    )
    assembled = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_day2",
            run_id="run_day2",
            document_id="doc_day2",
            document_title="测试方案",
            required_sections=["项目概述"],
            sections=[section],
            document_evidence_available=True,
            document_assessment_status="sufficient",
            document_evidence_assessment={
                "details": {
                    "verification_mode": "degraded_deterministic_fallback",
                    "quality_mode_degraded": True,
                }
            },
            created_at=NOW,
            updated_at=NOW,
        )
    ).draft
    assert assembled.extra["quality_mode_degraded"] is True


def test_degraded_quality_is_aggregated_but_does_not_decide_delivery() -> None:
    section = SchemeSectionSchema(
        section_id="s1",
        section_title="项目概述",
        section_order=1,
        content="限定性输出。",
        status=ExecutionStatus.SUCCESS,
        truncation=TruncationCheckSchema(truncated=False),
        eval_result=SectionEvalSchema(passed=True),
        extra={
            "generation_check": {
                "verification_mode": "degraded_deterministic_fallback",
                "quality_mode_degraded": True,
            }
        },
    )
    assembled = DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task_day2",
            run_id="run_day2",
            document_id="doc_day2",
            document_title="测试方案",
            required_sections=["项目概述"],
            sections=[section],
            document_evidence_available=True,
            document_assessment_status="sufficient",
            created_at=NOW,
            updated_at=NOW,
        )
    ).draft
    assert assembled.extra["quality_mode_degraded"] is True
    result = evaluate_scheme_draft(
        assembled,
        citation_required=False,
        retrieved_chunk_ids=[],
    )
    assert result.passed is True


def test_experiment_manifest_closes_artifact_lifecycle_and_reproducibility(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "answer.md"
    existing.write_text("answer", encoding="utf-8")
    missing = tmp_path / "candidate.jsonl"
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "event_type": "model_started",
                "call_id": "call_1",
                "model_name": "model-a",
                "input_summary": {
                    "model_call_id": "call_1",
                    "model_name": "model-a",
                    "temperature": 0.2,
                    "max_tokens": 512,
                    "top_p": 0.9,
                    "do_sample": True,
                    "prompt_sha256": "prompt-sha",
                },
                "metadata": {
                    "selected_profile": "profile-a",
                    "provider": "local_huggingface",
                    "model_role": "section_generation",
                    "call_purpose": "section_generation",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    static_spec = tmp_path / "static_retrieval.yaml"
    static_spec.write_text("spec_version: v1\n", encoding="utf-8")
    writer = ExperimentRunManifestWriter(tmp_path)
    manifest = writer.build(
        summary={
            "task_id": "task",
            "run_id": "run",
            "paths": {
                "deliverable_markdown": str(existing),
                "sft_candidates": str(missing),
                "trace": str(trace),
            },
        },
        runtime_preflight={
            "model_name": "model-a",
            "static_retrieval_spec": str(static_spec),
            "active_index_version": "idx-v1",
            "active_index_collection": "chunks",
            "embedding_model": "embed-a",
            "embedding_dim": 768,
        },
        report={"schema_version": "report_v1", "metrics": {}},
        seed=42,
    )
    verified = manifest.artifacts["deliverable_markdown"]
    planned = manifest.artifacts["sft_candidates"]
    assert verified.lifecycle == "verified"
    assert verified.verification_method == "sha256"
    assert verified.exists is True
    assert verified.sha256
    assert planned.lifecycle == "planned"
    assert planned.verification_method == "none"
    assert planned.exists is False
    assert planned.sha256 is None
    assert manifest.seed == 42
    assert manifest.rag["static_retrieval_spec_identity"]["sha256"]
    assert manifest.index["index_version"] == "idx-v1"
    assert manifest.index["embedding_dim"] == 768
    assert manifest.model_calls == [
        {
            "model_call_id": "call_1",
            "selected_model": "model-a",
            "selected_profile": "profile-a",
            "provider": "local_huggingface",
            "model_role": "section_generation",
            "call_purpose": "section_generation",
            "temperature": 0.2,
            "max_tokens": 512,
            "top_p": 0.9,
            "do_sample": True,
            "prompt_sha256": "prompt-sha",
            "prompt_id": None,
            "prompt_version": None,
            "availability_fallback_from": [],
        }
    ]

    created = ArtifactManifestRecord.from_path(
        artifact_type="deliverable_markdown",
        path=existing,
        verify=False,
    )
    assert created.lifecycle == "created"
    assert created.verification_method == "exists"
    assert created.sha256 is None

    with pytest.raises(ValidationError):
        ArtifactManifestRecord(
            artifact_type="broken",
            path=str(existing),
            lifecycle="verified",
            verification_method="sha256",
            exists=True,
            sha256=None,
        )


def test_experiment_manifest_losslessly_persists_real_model_usage_ledger(tmp_path: Path) -> None:
    ledger = ModelUsageLedger()
    selections = [
        ModelSelection(
            profile=ModelProfile(
                profile_id="profile-a",
                model_name="model-a",
                provider="provider-x",
                residency_policy=ResidencyPolicy.REMOTE,
            )
        ),
        ModelSelection(
            profile=ModelProfile(
                profile_id="profile-b",
                model_name="model-b",
                provider="provider-y",
                residency_policy=ResidencyPolicy.REMOTE,
            )
        ),
    ]
    for index, selection in enumerate(selections, start=1):
        ledger.record_logical_call(f"call-{index}")
        ledger.record_attempt(
            selection=selection,
            response=ModelResponseSchema(
                model_call_id=f"call-{index}",
                task_id="task",
                run_id="run",
                model_name=selection.model_name,
                success=True,
                content="ok",
                latency_ms=index,
                created_at=NOW,
            ),
        )
    usage = ledger.snapshot()

    writer = ExperimentRunManifestWriter(tmp_path)
    manifest = writer.build(
        summary={
            "task_id": "task",
            "run_id": "run",
            "paths": {},
            "model_usage": usage,
        },
        runtime_preflight={},
        report={"schema_version": "report_v1", "metrics": {}},
    )
    path = writer.write(manifest, tmp_path / "experiment_manifest.json")
    reloaded = ExperimentRunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    assert reloaded.model_usage is not None
    assert reloaded.model_usage.model_dump() == usage
    assert reloaded.model_usage.calls_by_model == {"model-a": 1, "model-b": 1}
    assert reloaded.model_usage.calls_by_profile == {"profile-a": 1, "profile-b": 1}
    assert reloaded.model_usage.calls_by_provider == {"provider-x": 1, "provider-y": 1}

    with pytest.raises(ValidationError):
        writer.build(
            summary={
                "task_id": "task",
                "run_id": "run_future_metric",
                "paths": {},
                "model_usage": {**usage, "producer_new_metric": 1},
            },
            runtime_preflight={},
            report={"schema_version": "report_v1", "metrics": {}},
        )


def test_experiment_manifest_records_current_git_identity(tmp_path: Path) -> None:
    project_root = tmp_path / "git-project"
    project_root.mkdir()
    _run_git(project_root, "init")
    _run_git(project_root, "config", "user.name", "Test User")
    _run_git(project_root, "config", "user.email", "test@example.com")

    readme = project_root / "README.md"
    readme.write_text("hermetic manifest fixture\n", encoding="utf-8")
    _run_git(project_root, "add", "README.md")
    _run_git(project_root, "commit", "-m", "initial")
    _run_git(project_root, "branch", "-M", "main")
    _run_git(project_root, "tag", "test-manifest-v1")

    expected_sha = _run_git(project_root, "rev-parse", "HEAD")
    expected_tag = _run_git(
        project_root,
        "describe",
        "--tags",
        "--exact-match",
    )
    expected_branch = _run_git(
        project_root,
        "rev-parse",
        "--abbrev-ref",
        "HEAD",
    )

    writer = ExperimentRunManifestWriter(project_root)
    clean_manifest = writer.build(
        summary={"task_id": "task", "run_id": "run", "paths": {}},
        runtime_preflight={},
        report={"schema_version": "report_v1", "metrics": {}},
    )
    assert expected_tag == "test-manifest-v1"
    assert expected_branch == "main"
    assert clean_manifest.code["git_commit_sha"] == expected_sha
    assert clean_manifest.code["git_tag"] == expected_tag
    assert clean_manifest.code["git_branch"] == expected_branch
    assert clean_manifest.code["git_dirty"] is False

    readme.write_text(
        "hermetic manifest fixture\nuncommitted change\n",
        encoding="utf-8",
    )
    dirty_manifest = writer.build(
        summary={"task_id": "task", "run_id": "run-dirty", "paths": {}},
        runtime_preflight={},
        report={"schema_version": "report_v1", "metrics": {}},
    )
    assert dirty_manifest.code["git_commit_sha"] == expected_sha
    assert dirty_manifest.code["git_tag"] == expected_tag
    assert dirty_manifest.code["git_branch"] == expected_branch
    assert dirty_manifest.code["git_dirty"] is True


def test_golden_dataset_manifest_reuses_canonical_rag_sample_schema() -> None:
    assert RAGEvalSample is RAGEvalSampleSchema
    assert LegacyRAGEvalSample is RAGEvalSampleSchema
    manifest = GoldenDatasetManifest(
        golden_set_id="golden_v1",
        golden_set_version="v1",
        sample_ids=["sample_1"],
    )
    assert manifest.sample_schema_version == "rag_strategy_eval_sample_v1"


def test_legacy_eval_loader_validates_full_canonical_payload(tmp_path: Path) -> None:
    dataset = tmp_path / "eval.json"
    payload = [
        {
            "sample_id": "sample_1",
            "query": "测试查询",
            "expected_doc_ids": ["doc1"],
            "expected_parent_chunk_ids": ["parent1"],
            "expected_child_chunk_ids": ["child1"],
            "expected_keywords": ["关键词"],
            "answer_keywords": ["答案"],
            "filter_expr": "year >= 2025",
            "keyword_doc_ids": ["doc1", "doc2"],
            "golden_set_id": "golden_v1",
            "golden_set_version": "v1",
            "source_refs": ["source://sample_1"],
            "metadata": {"owner": "day2"},
        }
    ]
    dataset.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    sample = load_eval_samples(dataset)[0]
    assert sample.expected_parent_chunk_ids == ["parent1"]
    assert sample.expected_child_chunk_ids == ["child1"]
    assert sample.filter_expr == "year >= 2025"
    assert sample.keyword_doc_ids == ["doc1", "doc2"]
    assert sample.golden_set_id == "golden_v1"
    assert sample.golden_set_version == "v1"
    assert sample.source_refs == ["source://sample_1"]


def test_legacy_eval_loader_rejects_unknown_typo_field(tmp_path: Path) -> None:
    dataset = tmp_path / "eval_typo.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "query": "测试查询",
                    "expected_doc_ids": ["doc1"],
                    "expected_parent_chunck_ids": ["typo"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_eval_samples(dataset)


def test_no_evidence_cannot_produce_false_success_or_fake_citation() -> None:
    draft = _draft_with_citations("本项目确定建设1000个节点。", [])
    result = evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=False,
    )
    assert result.passed is False
    assert result.checks["evidence_boundary_respected"] is False
    assert draft.citation_bindings == []
    assert "[C" not in draft.full_text


def test_no_evidence_refusal_keyword_cannot_mask_unsupported_claim() -> None:
    draft = _draft_with_citations(
        "现有资料证据不足，但本项目确定建设1000个节点。",
        [],
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=False,
    )
    assert result.passed is False
    assert result.checks["evidence_boundary_respected"] is False


def test_explicit_fail_closed_no_evidence_section_respects_boundary() -> None:
    draft = _draft_with_citations("现有知识库证据不足，请补充资料后重新生成。", [])
    draft.sections[0].extra.update(
        {
            "generation_blocked": True,
            "generation_block_reason": "evidence_insufficient",
        }
    )
    result = evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=False,
    )
    assert result.checks["evidence_boundary_respected"] is True


def test_cleaned_text_unit_boundary_is_strict_and_chunker_projects_to_it() -> None:
    valid = {
        "unit_id": "u1",
        "doc_id": "doc1",
        "source_type": "pdf",
        "text": "正文",
        "unit_order": 1,
        "cleaning_version": "v1",
        "extra": {"producer_extension": "explicitly namespaced"},
    }

    invalid_payloads = [
        {k: v for k, v in valid.items() if k != "cleaning_version"},
        {k: v for k, v in valid.items() if k != "source_type"},
        {**valid, "unit_order": "1"},
        {**valid, "extra": "not-a-dict"},
        {**{k: v for k, v in valid.items() if k != "cleaning_version"}, "cleaning_verison": "typo"},
        {**valid, "producer_only_field": "must not be silently dropped"},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            row_to_unit(payload)

    projected = row_to_unit(valid)
    assert projected["doc_id"] == "doc1"
    assert projected["text_length"] == 2
    assert projected["extra"]["producer_extension"] == "explicitly namespaced"


def test_cleaned_text_unit_legacy_migration_is_explicit_and_preserves_facts() -> None:
    legacy = {
        "unit_id": "u1",
        "doc_id": "doc1",
        "source_type": "offline",
        "text": "正文",
        "unit_order": 1,
        "cleaning_version": "clean_v1",
        "cleaning_rules": ["normalize_whitespace"],
        "created_at": "2026-07-23T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        row_to_unit(legacy)

    migrated = migrate_legacy_cleaned_text_unit_payload(legacy)
    projected = row_to_unit(migrated)
    assert projected["extra"]["legacy_top_level_extensions"] == {
        "cleaning_rules": ["normalize_whitespace"],
        "created_at": "2026-07-23T00:00:00Z",
    }
    assert projected["extra"]["legacy_cleaned_text_unit_migration"]["migrated_top_level_fields"] == [
        "cleaning_rules",
        "created_at",
    ]

    with pytest.raises(TypeError):
        migrate_legacy_cleaned_text_unit_payload({**legacy, "extra": "not-a-dict"})


class _Closable:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_resource_pool_releases_owned_heavy_resources() -> None:
    pool = ParentChildResourcePool(
        runtime_config=SimpleNamespace(),
        project_root=Path("."),
    )
    dense = _Closable()
    embedding = _Closable()
    reranker = _Closable()
    pool._dense_retriever = dense
    pool._embedding_backend = embedding
    pool._parent_rerankers[("r",)] = reranker

    pool.close()

    assert dense.closed == 1
    assert embedding.closed == 1
    assert reranker.closed == 1
    assert pool._dense_retriever is None
    assert pool._embedding_backend is None
    assert pool._parent_rerankers == {}


def test_dense_retriever_can_use_runtime_owned_embedding_backend() -> None:
    class _Embedder:
        def encode_query(self, query: str) -> np.ndarray:
            assert query == "hello"
            return np.asarray([[1.0, 2.0]], dtype=np.float32)

    retriever = MilvusChildRetriever.__new__(MilvusChildRetriever)
    retriever.hash_embedding = False
    retriever.hash_dim = 2
    retriever.embedding_backend = _Embedder()
    retriever.embedding_model = "unused"
    retriever.embedding_device = "cuda"
    retriever.embedding_batch_size = 1

    vector = retriever.encode_query("hello")
    assert vector.tolist() == [1.0, 2.0]


def test_scheme_writer_preserves_evidence_insufficient_as_non_retryable() -> None:
    hard_gate = SimpleNamespace(
        checks={
            "citation_marker_binding_consistent": True,
            "citation_targets_valid": True,
            "citation_grounding_verified": True,
        }
    )
    category, retryable = SchemeGenerationUseCase._hard_gate_failure_policy(
        hard_gate,
        evidence_requirements_satisfied=False,
    )
    assert category == FailureCategory.EVIDENCE_INSUFFICIENT
    assert retryable is False


def test_scheme_writer_treats_citation_contract_failure_as_validation() -> None:
    hard_gate = SimpleNamespace(
        checks={
            "citation_marker_binding_consistent": False,
            "citation_targets_valid": True,
            "citation_grounding_verified": True,
        }
    )
    category, retryable = SchemeGenerationUseCase._hard_gate_failure_policy(
        hard_gate,
        evidence_requirements_satisfied=True,
    )
    assert category == FailureCategory.VALIDATION
    assert retryable is False


def test_rag_service_uses_canonical_failure_policy_for_validation_errors(
    tmp_path: Path,
) -> None:
    class _FailingRuntime:
        def retrieve(self, payload: dict) -> dict:
            del payload
            raise ValueError("invalid retrieval payload shape")

    service = RAGService(
        tmp_path,
        retrieval_runtime=_FailingRuntime(),
        allow_legacy_unscoped=True,
    )
    result = service.retrieve(
        RAGToolInputSchema(
            task_id="task_day2",
            run_id="run_day2",
            agent_name="SchemeWriterAgent",
            query="测试查询",
        )
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.failure_category == FailureCategory.VALIDATION
    assert result.error.retryable is False


def test_capture_eval_runner_executes_real_lifecycle_with_external_policy(
    tmp_path: Path,
) -> None:
    capture = DataCaptureRecordSchema(
        record_id="capture_prod_1",
        capture_type="eval_samples",
        capture_stage="candidate",
        task_id="task_day2",
        run_id="run_day2",
        created_at=NOW,
        user_input="测试查询",
        generated_draft_output="测试答案",
        rag_context={"context_text": "测试答案"},
    )
    path = tmp_path / "eval_samples.jsonl"
    path.write_text(
        json.dumps(capture.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = RAGEvalRunner().evaluate_file(
        path,
        policy_decisions={capture.record_id: "accepted"},
        readiness_target="eval_ready",
    )

    lifecycle = report.results[0].extra["capture_lifecycle"]
    assert lifecycle["source_stage"] == "candidate"
    assert lifecycle["final_stage"] == "eval_ready"
    assert lifecycle["policy_decision"] == "accepted"
    assert lifecycle["record"]["is_usable_for_eval"] is True
    assert lifecycle["record"]["is_usable_for_training"] is False
    assert report.metadata["capture_lifecycle"]["final_stage_counts"] == {
        "eval_ready": 1
    }


def test_capture_eval_runner_persists_canonical_promoted_state(
    tmp_path: Path,
) -> None:
    capture = DataCaptureRecordSchema(
        record_id="capture_persisted_1",
        capture_type="eval_samples",
        capture_stage="candidate",
        task_id="task_day2",
        run_id="run_day2",
        created_at=NOW,
        user_input="测试查询",
        generated_draft_output="测试答案",
        rag_context={"context_text": "测试答案"},
    )
    source = tmp_path / "eval_samples.jsonl"
    source.write_text(
        json.dumps(capture.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    store = DataCaptureLifecycleStore(tmp_path / "captures")
    runner = RAGEvalRunner(lifecycle_store=store)

    result = runner.evaluate_and_save(
        eval_samples_path=source,
        output_dir=tmp_path / "reports",
        policy_decisions={capture.record_id: "accepted"},
        readiness_target="eval_ready",
    )

    persisted = store.load_current(capture.record_id)
    assert persisted.capture_stage == "eval_ready"
    assert persisted.is_usable_for_eval is True
    assert persisted.is_usable_for_training is False
    assert [item.record_id for item in store.iter_stage("eval_ready")] == [
        capture.record_id
    ]
    assert result["output_paths"]["capture_lifecycle_current"]
    history = store.read_transition_history(capture.run_id)
    assert [(item.from_state, item.to_state) for item in history] == [
        ("candidate", "evaluated"),
        ("evaluated", "accepted"),
        ("accepted", "eval_ready"),
    ]
    assert all(item.record_id == capture.record_id for item in history)
    assert all(item.timestamp for item in history)
    assert all(item.reason for item in history)
    assert all(item.authority for item in history)


def test_capture_eval_runner_replay_cannot_overwrite_canonical_current(
    tmp_path: Path,
) -> None:
    capture = DataCaptureRecordSchema(
        record_id="capture_replay_guard_1",
        capture_type="eval_samples",
        capture_stage="candidate",
        task_id="task_day2",
        run_id="run_day2",
        created_at=NOW,
        user_input="测试查询",
        generated_draft_output="测试答案",
        rag_context={"context_text": "测试答案"},
    )
    source = tmp_path / "eval_samples.jsonl"
    source.write_text(
        json.dumps(capture.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    store = DataCaptureLifecycleStore(tmp_path / "captures")
    runner = RAGEvalRunner(lifecycle_store=store)

    runner.evaluate_and_save(
        eval_samples_path=source,
        output_dir=tmp_path / "reports_first",
        policy_decisions={capture.record_id: "accepted"},
        readiness_target="eval_ready",
    )
    assert store.load_current(capture.record_id).capture_stage == "eval_ready"

    with pytest.raises(ValueError, match="cannot replay"):
        runner.evaluate_and_save(
            eval_samples_path=source,
            output_dir=tmp_path / "reports_replay_rejected",
            policy_decisions={capture.record_id: "rejected"},
        )
    assert store.load_current(capture.record_id).capture_stage == "eval_ready"

    with pytest.raises(ValueError, match="cannot replay"):
        runner.evaluate_and_save(
            eval_samples_path=source,
            output_dir=tmp_path / "reports_replay_same",
            policy_decisions={capture.record_id: "accepted"},
            readiness_target="eval_ready",
        )
    assert store.load_current(capture.record_id).capture_stage == "eval_ready"


def test_lifecycle_store_rejects_direct_terminal_initialization(tmp_path: Path) -> None:
    store = DataCaptureLifecycleStore(tmp_path / "captures")
    terminal = DataCaptureRecordSchema(
        record_id="capture_direct_terminal",
        capture_type="eval_samples",
        capture_stage="eval_ready",
        task_id="task_day2",
        run_id="run_day2",
        created_at=NOW,
        is_usable_for_eval=True,
        metadata={"quality_decision": "accepted"},
    )
    with pytest.raises(ValueError, match="may only initialize"):
        store.initialize(terminal)


def test_capture_eval_runner_explicitly_migrates_pre_day2_auto_readiness(
    tmp_path: Path,
) -> None:
    legacy = DataCaptureRecordSchema.model_construct(
        schema_version="data_capture_record_v1",
        record_id="legacy_capture_1",
        capture_type="eval_samples",
        capture_stage="candidate",
        task_id="task_day2",
        run_id="run_day2",
        created_at=NOW,
        user_input="测试查询",
        generated_draft_output="测试答案",
        rag_context={"context_text": "测试答案"},
        quality_score=1.0,
        is_usable_for_training=True,
        is_usable_for_eval=True,
        metadata={"status": "success"},
    ).model_dump(mode="json")
    path = tmp_path / "legacy_eval_samples.jsonl"
    path.write_text(
        json.dumps(legacy, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = RAGEvalRunner().evaluate_file(path)
    lifecycle_record = report.results[0].extra["capture_lifecycle"]["record"]
    assert lifecycle_record["capture_stage"] == "evaluated"
    assert lifecycle_record["is_usable_for_training"] is False
    assert lifecycle_record["is_usable_for_eval"] is False
    migration = lifecycle_record["metadata"]["legacy_capture_migration"]
    assert migration["migration"] == "reset_pre_day2_auto_readiness_v1"
    assert migration["previous_is_usable_for_training"] is True
    assert migration["previous_is_usable_for_eval"] is True


def test_ragas_style_loader_reuses_canonical_contract_and_rejects_typos(
    tmp_path: Path,
) -> None:
    assert RagEvalCase is RAGEvalSampleSchema
    dataset = tmp_path / "ragas_cases.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "case_1",
                    "query": "测试查询",
                    "expected_doc_ids": ["doc1"],
                    "expected_parent_chunks_ids": ["typo"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_rag_eval_cases(dataset)


def test_real_runtime_preflight_preserves_active_index_reproducibility_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    static_spec = tmp_path / "static_retrieval.yaml"
    static_spec.write_text("spec_version: v1\n", encoding="utf-8")
    monkeypatch.setenv("RAG_STATIC_RETRIEVAL_SPEC_FILE", str(static_spec))
    monkeypatch.setattr(
        run_demo_script,
        "_model_routing_snapshot",
        lambda settings: {
            "model_profiles": [
                {
                    "profile_id": "p1",
                    "model_name": settings.default_model_name,
                    "provider": "local_huggingface",
                }
            ],
            "model_routing_policies": [],
        },
    )

    index_info = {
        "index_version": "idx-v1",
        "backend": "milvus_lite",
        "collection_name": "chunks",
        "manifest_path": str(tmp_path / "index_manifest.json"),
        "manifest_hash": "manifest-sha",
        "dataset_version": "dataset-v1",
        "config_hash": "config-sha",
        "reproducibility_hash": "repro-sha",
        "embedding_model": "embed-a",
        "embedding_version": "embed-v1",
        "embedding_dim": 768,
    }

    class _Resolver:
        def resolve(self, pointer_path: Path) -> dict:
            del pointer_path
            return dict(index_info)

    monkeypatch.setattr(
        run_demo_script,
        "ActiveIndexResolver",
        lambda **kwargs: _Resolver(),
    )
    settings = SimpleNamespace(
        default_model_name="model-a",
        local_qwen_model_name="model-a",
        local_qwen_model_path=model_dir,
        local_qwen_device="cpu",
        project_root=tmp_path,
    )

    preflight = run_demo_script._preflight_real_runtime(settings)
    assert preflight["active_index_manifest_path"] == index_info["manifest_path"]
    assert preflight["active_index_manifest_sha256"] == "manifest-sha"
    assert preflight["active_index_dataset_version"] == "dataset-v1"
    assert preflight["active_index_config_sha256"] == "config-sha"
    assert preflight["active_index_reproducibility_sha256"] == "repro-sha"
    assert preflight["embedding_dim"] == 768


def test_run_demo_applies_experiment_seed_to_actual_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import random

    monkeypatch.setenv("EXPERIMENT_SEED", "12345")

    def fake_run_mainline(**kwargs: object) -> dict:
        del kwargs
        return {
            "python_random": random.random(),
            "numpy_random": float(np.random.random()),
        }

    monkeypatch.setattr(run_demo_script, "run_mainline", fake_run_mainline)
    first = run_demo_script.run_demo()
    second = run_demo_script.run_demo()
    assert first == second
