from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path
from types import MethodType

import pytest

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.services.scheme_writer.model_service import (
    SectionModelService,
)
from apps.enterprise_document.services.scheme_writer.prompt_service import (
    SectionPromptService,
    TargetTechnicalRecoveryIntegrityError,
)
from apps.enterprise_document.services.scheme_writer.section_content_recovery import (
    SectionContentRecovery,
)
from apps.enterprise_document.services.scheme_writer.section_generation_service import (
    SectionGenerationService,
)
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from schemas.citation import CitationSchema
from schemas.claim import ClaimType
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.model import ModelResponseSchema
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RetrievedChunkSchema,
)
from schemas.evidence_assessment import (
    EvidenceAssessmentV2Schema,
    EvidenceRelevance,
)
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)


TASK_ID = "task_content_prep"
RUN_ID = "run_content_prep"
SECTION_ID = "section_content_prep_001"
E1_TEXT = "E1_CANONICAL_EVIDENCE_TEXT：统一身份认证必须记录审计日志。"
E2_TEXT = "E2_LATE_RUNTIME_EVIDENCE_TEXT：不得进入已锁定技术恢复。"
E1_MISSING_TOPIC = "E1_MISSING_TOPIC_预算边界"
E1_CONFLICT = "E1_CONFLICT_来源对成本口径不一致"
E1_QUALIFIER = "E1_REQUIRED_QUALIFIER_成本必须标注待确认"
E1_STRUCTURED_FACT = "E1_STRUCTURED_FACT_已确认部署区域"


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id=TASK_ID,
        run_id=RUN_ID,
        task_type="scheme_generation",
        user_input="生成内容",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成内容"),
            task=TaskContextSchema(
                task_id=TASK_ID,
                run_id=RUN_ID,
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-23T00:00:00+08:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema.model_validate(
        {
            "task_id": TASK_ID,
            "task_type": "scheme_generation",
            "user_query": "生成内容",
            "generation_requirements": {
                "required_sections": ["技术方案"],
                "citation_required_sections": ["技术方案"],
                "min_section_chars": 1,
                "max_section_retries": 1,
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {
                "document_title": "Target Content PREP",
                "required_sections": ["技术方案"],
            },
        }
    )


def _canonical_bundle(*, marker: str, context_text: str) -> EvidenceBundleSchema:
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=f"child_{marker}",
        context_chunk_id=f"parent_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        doc_id=f"doc_{marker}",
        matched_granularity="child",
        context_granularity="parent",
        match_text=context_text,
        context_text=context_text,
        title=f"Evidence {marker}",
    )
    citation = CitationSchema(
        citation_id=f"source_{marker}",
        source_type="document",
        source_document_id=f"doc_{marker}",
        doc_id=f"doc_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        quote_text=context_text,
        title=f"Evidence {marker}",
    )
    bundle = build_evidence_bundle(
        query=f"query_{marker}",
        rewritten_queries=[],
        selected_chunks=[chunk],
        dropped_chunks=[],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )
    if marker == "E1":
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status=EvidenceStatus.CONFLICTED,
            coverage_ratio=0.5,
            required_topics=["身份认证", E1_MISSING_TOPIC],
            covered_topics=["身份认证"],
            missing_topics=[E1_MISSING_TOPIC],
            conflicts=[{"summary": E1_CONFLICT}],
            supported_claim_types=["general_method"],
            forbidden_claim_types=["current_project_cost"],
            authoritative_evidence_present=True,
            historical_or_reference_evidence_present=True,
            required_qualifiers=[E1_QUALIFIER],
            reason_codes=["E1_CONFLICTED_AND_INCOMPLETE"],
        )
    else:
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status=EvidenceStatus.SUFFICIENT,
            coverage_ratio=1.0,
            required_topics=["late topic"],
            covered_topics=["late topic"],
            supported_claim_types=["current_project_cost"],
            authoritative_evidence_present=True,
            reason_codes=["E2_SUFFICIENT"],
        )
    item_assessment = EvidenceAssessmentV2Schema(
        evidence_id=bundle.selected_evidence_ids[0],
        document_id=chunk.doc_id,
        document_type=DocumentType.CURRENT_PROJECT_REQUIREMENT,
        evidence_role=EvidenceRole.PROJECT_FACT,
        project_relation=ProjectRelation.CURRENT_PROJECT,
        authority_level=AuthorityLevel.AUTHORITATIVE,
        eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
        semantic_relevance=1.0,
        eligibility=True,
        relevance=EvidenceRelevance.RELEVANT,
        supported_topics=(
            ["身份认证"] if marker == "E1" else ["late topic"]
        ),
        supported_claim_types=(
            [ClaimType.GENERAL_METHOD]
            if marker == "E1"
            else [ClaimType.CURRENT_PROJECT_COST]
        ),
        forbidden_claim_types=(
            [ClaimType.CURRENT_PROJECT_COST] if marker == "E1" else []
        ),
        reason_codes=[
            "E1_ITEM_ELIGIBLE" if marker == "E1" else "E2_ITEM_ELIGIBLE"
        ],
    )
    payload = bundle.model_dump(mode="python", round_trip=True)
    payload.update(
        {
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "item_assessments": [
                item_assessment.model_dump(mode="python", round_trip=True)
            ],
            "assessment": assessment.model_dump(mode="python", round_trip=True),
        }
    )
    return EvidenceBundleSchema.model_validate(payload)


def _real_target_prompt_from_evidence():
    prompt_service = SectionPromptService(
        context_manager=LLMContextManager(),
        context_policy=SectionGenerationContextPolicy(),
        prompt_manager=None,
        prompt_id="unused_target_test_prompt",
        model_gateway=None,
    )
    e1 = _canonical_bundle(marker="E1", context_text=E1_TEXT)
    structured_fact = StructuredFactSchema(
        fact_id="fact_e1",
        task_id=TASK_ID,
        run_id=RUN_ID,
        fact_type="resource_config",
        content=E1_STRUCTURED_FACT,
        source_type="user_input",
        created_at="2026-08-23T00:00:00+08:00",
    )
    target_prompt = prompt_service.render_target_initial_section_prompt(
        _shared_state(),
        _project_input(),
        section_id=SECTION_ID,
        section_title="技术方案",
        section_order=1,
        evidence_bundle=e1,
        structured_facts=[structured_fact],
        previous_sections=[],
    )
    return prompt_service, e1, target_prompt


def _run_target_case(
    *,
    primary_content: str,
    primary_finish_reason: str,
    recovery_content: str | None,
    advance_runtime_to_e2: bool,
    canonical_messages=None,
    canonical_context_package=None,
    canonical_allowed_aliases=None,
):
    prompt_service, e1, target_prompt = _real_target_prompt_from_evidence()
    state = _shared_state()
    e2 = _canonical_bundle(marker="E2", context_text=E2_TEXT)
    calls: list[dict] = []

    model_service = object.__new__(SectionModelService)
    model_service.prompt_service = prompt_service
    model_service.context_manager = prompt_service.context_manager
    model_service.model_gateway = None

    def fake_call_model(self, shared_state, **kwargs):
        del self
        calls.append(dict(kwargs))
        call_index = len(calls)
        if call_index == 1:
            if advance_runtime_to_e2:
                shared_state.extra["active_evidence_bundle"] = e2.model_dump(
                    mode="json"
                )
                shared_state.extra["active_evidence_bundle_sha256"] = (
                    evidence_bundle_sha256(e2)
                )
            content = primary_content
            finish_reason = primary_finish_reason
        else:
            content = recovery_content or ""
            finish_reason = "stop"
        return ModelResponseSchema(
            model_call_id=f"model_call_target_{call_index}",
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            model_name="same-writer-model",
            success=True,
            content=content,
            finish_reason=finish_reason,
            created_at=f"2026-08-23T00:00:0{call_index}+08:00",
        )

    model_service.call_model = MethodType(fake_call_model, model_service)
    content_recovery = SectionContentRecovery(
        model_service=model_service,
        prompt_service=prompt_service,
    )
    service = object.__new__(SectionGenerationService)
    service.model_service = model_service
    service.content_recovery = content_recovery
    result = service.generate_target_content(
        state,
        project_input=_project_input(),
        target_prompt=target_prompt,
        canonical_messages=canonical_messages,
        canonical_context_package=canonical_context_package,
        canonical_allowed_aliases=canonical_allowed_aliases,
    )
    return result, calls, state, e1, e2, target_prompt


def test_target_content_boundary_uses_real_canonical_prompt_and_same_model() -> None:
    result, calls, _, e1, _, target_prompt = _run_target_case(
        primary_content="一次模型调用即可形成完整正文。",
        primary_finish_reason="stop",
        recovery_content=None,
        advance_runtime_to_e2=False,
    )

    assert result.success is True
    assert len(calls) == 1
    assert result.evidence_bundle_sha256 == evidence_bundle_sha256(e1)
    assert calls[0]["context_package"]["context_sha256"] == (
        target_prompt.context_package.context_sha256
    )
    assert calls[0]["prompt"] == (
        target_prompt.technical_recovery_context.initial_prompt_text
    )


def test_src_rev_04_canonical_model_metadata_uses_locked_aliases() -> None:
    result, calls, _, _, _, target_prompt = _run_target_case(
        primary_content="受控正文。",
        primary_finish_reason="stop",
        recovery_content=None,
        advance_runtime_to_e2=False,
        canonical_messages=[{"role": "user", "content": "locked ContextPackage"}],
        canonical_context_package={"locked": "context"},
        canonical_allowed_aliases=(),
    )
    assert target_prompt.technical_recovery_context.citation_ids
    assert result.success
    assert len(calls) == 1
    assert calls[0]["available_citation_ids"] == []
    assert calls[0]["prompt"] == ""


def test_target_technical_retry_remains_locked_to_e1_after_runtime_evidence_advances() -> None:
    result, calls, state, e1, e2, target_prompt = _run_target_case(
        primary_content="未完整的Target正文",
        primary_finish_reason="length",
        recovery_content="E1约束下重新生成的完整短稿。",
        advance_runtime_to_e2=True,
    )

    assert result.success is True
    assert len(calls) == 2
    retry_call = calls[1]
    assert state.extra["active_evidence_bundle_sha256"] == evidence_bundle_sha256(e2)
    assert retry_call["context_package"]["lineage"][
        "evidence_bundle_sha256"
    ] == evidence_bundle_sha256(e1)
    assert retry_call["context_package"]["context_sha256"] == (
        target_prompt.context_package.context_sha256
    )
    assert E1_TEXT in retry_call["prompt"]
    assert E2_TEXT not in retry_call["prompt"]
    assert evidence_bundle_sha256(e2) not in retry_call["prompt"]


def test_target_technical_retry_preserves_locked_semantic_restrictions() -> None:
    result, calls, _, _, _, target_prompt = _run_target_case(
        primary_content="未完整的Target正文",
        primary_finish_reason="length",
        recovery_content="语义限制保持后的完整短稿。",
        advance_runtime_to_e2=True,
    )

    assert result.success is True
    retry_prompt = calls[1]["prompt"]
    retry_package = calls[1]["context_package"]
    semantics = next(
        item
        for item in retry_package["selected_items"]
        if item["item_id"] == "writer_evidence_semantics"
    )
    structured = next(
        item
        for item in retry_package["selected_items"]
        if item["item_id"] == "structured_writing_facts"
    )
    assert semantics["content"] == (
        target_prompt.technical_recovery_context.writer_evidence_semantics_json
    )
    assert structured["content"] == (
        target_prompt.technical_recovery_context.structured_writing_facts_json
    )
    for marker in (
        E1_MISSING_TOPIC,
        E1_CONFLICT,
        "current_project_cost",
        E1_QUALIFIER,
        E1_STRUCTURED_FACT,
    ):
        assert marker in retry_prompt


def test_target_overlong_compression_preserves_semantics_and_evidence_lineage() -> None:
    overlong = ("E1已生成且完整的正文内容。" * 150).strip()
    result, calls, state, e1, e2, target_prompt = _run_target_case(
        primary_content=overlong,
        primary_finish_reason="stop",
        recovery_content="压缩后的E1约束正文。",
        advance_runtime_to_e2=True,
    )

    assert result.success is True
    assert len(calls) == 2
    compression_call = calls[1]
    assert compression_call["purpose"] == "scheme_section_compression"
    assert state.extra["active_evidence_bundle_sha256"] == evidence_bundle_sha256(e2)
    assert compression_call["context_package"]["lineage"][
        "evidence_bundle_sha256"
    ] == evidence_bundle_sha256(e1)
    assert compression_call["context_package"]["lineage"][
        "locked_context_sha256"
    ] == target_prompt.context_package.context_sha256
    assert E2_TEXT not in compression_call["prompt"]
    for marker in (
        E1_MISSING_TOPIC,
        E1_CONFLICT,
        "current_project_cost",
        E1_QUALIFIER,
        E1_STRUCTURED_FACT,
    ):
        assert marker in compression_call["prompt"]


def test_invalid_target_technical_lock_fails_before_model_dispatch() -> None:
    prompt_service, _, target_prompt = _real_target_prompt_from_evidence()
    invalid_lock = replace(
        target_prompt.technical_recovery_context,
        evidence_bundle_sha256="f" * 64,
    )
    invalid_prompt = replace(
        target_prompt,
        technical_recovery_context=invalid_lock,
    )
    model_service = object.__new__(SectionModelService)
    model_service.prompt_service = prompt_service
    model_service.context_manager = prompt_service.context_manager
    model_service.model_gateway = None
    calls: list[dict] = []

    def fail_if_called(self, shared_state, **kwargs):
        del self, shared_state, kwargs
        calls.append({})
        raise AssertionError("model dispatch must not occur")

    model_service.call_model = MethodType(fail_if_called, model_service)
    service = object.__new__(SectionGenerationService)
    service.model_service = model_service
    service.content_recovery = SectionContentRecovery(
        model_service=model_service,
        prompt_service=prompt_service,
    )

    with pytest.raises(
        TargetTechnicalRecoveryIntegrityError,
        match="lineage evidence_bundle_sha256 mismatch",
    ):
        service.generate_target_content(
            _shared_state(),
            project_input=_project_input(),
            target_prompt=invalid_prompt,
        )
    assert calls == []


def test_target_content_and_recovery_methods_are_gate_free_and_do_not_invent_s11_15() -> None:
    sources = "\n".join(
        (
            inspect.getsource(SectionGenerationService.generate_target_content),
            inspect.getsource(SectionContentRecovery.recover_target),
            inspect.getsource(SectionModelService.retry_target_truncated_section),
            inspect.getsource(SectionModelService.compress_target_overlong_section),
        )
    )
    forbidden = (
        "WriterGateBinding",
        "resolve_writer_gate_enforcement",
        "writer_gate_blocks",
        "writer_gate_prompt_contract",
        "Shadow Enforcement",
        "PreWriteWriterInvocationService",
        "WriterInvocationBuilder",
        "generation_class",
        "route_decision_ref",
        "NormalWriter",
        "FallbackWriter",
        "FallbackGenerationService",
        "WriterAdmissionDecision",
        "WriterAdmissionEnforcement",
        "RecoveryControlFacts",
        "PreWriteRouter",
        "PreWriteRouteDecision",
        "NORMAL_WRITE",
        "FALLBACK_WRITE",
    )
    for marker in forbidden:
        assert marker not in sources

    signature = inspect.signature(SectionGenerationService.generate_target_content)
    assert "writer_gate_binding" not in signature.parameters
    assert "target_prompt" in signature.parameters


def test_no_normal_fallback_writer_split_or_s11_15_service_was_added() -> None:
    services_root = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
    )
    prep_sources = "\n".join(
        (services_root / name).read_text(encoding="utf-8")
        for name in (
            "prompt_service.py",
            "section_generation_service.py",
            "section_content_recovery.py",
            "model_service.py",
        )
    )
    for marker in (
        "class NormalWriter",
        "class FallbackWriter",
        "class FallbackGenerationService",
        "class PreWriteWriterInvocationService",
        "class WriterInvocationBuilder",
    ):
        assert marker not in prep_sources
