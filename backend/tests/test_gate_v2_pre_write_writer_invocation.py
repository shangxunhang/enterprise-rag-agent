"""T12-06 focused acceptance for P3-B S11-15 canonical Writer Invocation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import inspect
import sqlite3
from pathlib import Path
from types import MethodType
import time

import pytest

from backend.tests.test_rag_interface_v2_phase4b_canonical_carrier import (
    _item_assessment,
)
from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.quality.budget import (
    WorkflowBudget,
    WorkflowBudgetExceeded,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceFactSchema,
    CriterionEvidenceSupportState,
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    RequirementEvidenceFactSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionContractSchema,
    WriterAdmissionFactAssessmentSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    WriterGateBindingRefSchema,
    WriterGateBindingSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementResultSchema,
    PreWriteRouteDecisionSchema,
    RecoveryControlFactsSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionFailureKind,
    WriterExecutionStatus,
    WriterGenerationInvocationRefSchema,
    WriterGenerationClass,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
    RecoveryChainSnapshot,
)
from apps.enterprise_document.services.scheme_writer.pre_write_router import (
    PreWriteRouter,
    PreWriteRoutingIntegrityError,
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
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    WriterAdmissionPolicy,
)
from apps.enterprise_document.services.scheme_writer.writer_admission_enforcement import (
    WriterAdmissionEnforcement,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    WriterGateApplicabilityValidator,
    WriterGateBinder,
)
from apps.enterprise_document.services.scheme_writer.writer_invocation import (
    PreWriteWriterInvocationService,
    WriterIndeterminateExternalExecutionError,
    WriterInvocationBuilder,
    WriterInvocationIntegrityError,
    WriterInvocationIntegrityValidator,
)
from context_manager import LLMContextManager, SectionGenerationContextPolicy
from contracts.evidence import build_evidence_bundle, evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import (
    SQLitePreWriteExecutionStore,
    WriterExecutionState,
)
from core.runtime.pre_write_route_decision_store import (
    PreWriteRouteDecisionPersistenceIntegrityError,
    SQLitePreWriteRouteDecisionStore,
)
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.failure import FailureCategory
from schemas.model import ModelResponseSchema
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RetrievedChunkSchema,
)


TASK_ID = "task_s11_15"
RUN_ID = "run_s11_15"
DOCUMENT_ID = "document_s11_15"
SECTION_ID = "section_s11_15_001"
WRITE_INSTANCE_ID = "write_instance_s11_15"
E1_TEXT = "E1_CANONICAL_WRITER_EVIDENCE：统一身份认证必须记录审计日志。"
E2_TEXT = "E2_LATE_ACTIVE_EVIDENCE：不得进入已锁定 Writer invocation。"
E1_QUALIFIER = "E1_QUALIFIER：预算数字必须标注待确认"
E1_FACT = "E1_STRUCTURED_FACT：部署区域已确认"


@dataclass(frozen=True)
class CurrentRouteChain:
    evidence_bundle: EvidenceBundleSchema
    decision: object
    binding: WriterGateBindingSchema
    applicability: BindingApplicabilityResult
    enforcement: EnforcementResultSchema
    recovery_control: RecoveryControlFactsSchema
    route_decision: PreWriteRouteDecisionSchema


def _shared_state() -> SharedStateSchema:
    return SharedStateSchema(
        task_id=TASK_ID,
        run_id=RUN_ID,
        task_type="scheme_generation",
        user_input="生成技术方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成技术方案"),
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
            "user_query": "生成技术方案",
            "generation_requirements": {
                "required_sections": ["技术方案"],
                "citation_required_sections": ["技术方案"],
                "min_section_chars": 1,
                "max_section_retries": 1,
                "max_context_chars": 6000,
                "max_tokens_per_section": 1024,
            },
            "output_schema": {
                "document_title": "S11-15 Target",
                "required_sections": ["技术方案"],
            },
        }
    )


def _structured_facts() -> list[StructuredFactSchema]:
    return [
        StructuredFactSchema(
            fact_id="fact_s11_15",
            task_id=TASK_ID,
            run_id=RUN_ID,
            fact_type="resource_config",
            content=E1_FACT,
            source_type="user_input",
            created_at="2026-08-23T00:00:00+08:00",
        )
    ]


def _bundle(*, marker: str, allow: bool) -> EvidenceBundleSchema:
    text = E1_TEXT if marker == "E1" else E2_TEXT
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=f"child_{marker}",
        context_chunk_id=f"parent_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        doc_id=f"doc_{marker}",
        matched_granularity="child",
        context_granularity="parent",
        match_text=text,
        context_text=text,
        title=f"Evidence {marker}",
    )
    citation = CitationSchema(
        citation_id=f"source_{marker}",
        source_type="document",
        source_document_id=f"doc_{marker}",
        doc_id=f"doc_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        quote_text=text,
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
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=EvidenceStatus.SUFFICIENT if allow else EvidenceStatus.INSUFFICIENT,
        coverage_ratio=1.0 if allow else 0.5,
        required_topics=["身份认证", "预算边界"],
        covered_topics=["身份认证", "预算边界"] if allow else ["身份认证"],
        missing_topics=[] if allow else ["预算边界"],
        supported_claim_types=["general_method"],
        forbidden_claim_types=[] if allow else ["current_project_cost"],
        authoritative_evidence_present=True,
        historical_or_reference_evidence_present=not allow,
        required_qualifiers=[] if allow else [E1_QUALIFIER],
        reason_codes=["SUFFICIENT"] if allow else ["PARTIAL_TOPIC_COVERAGE"],
    )
    payload = bundle.model_dump(mode="python", round_trip=True)
    payload.update(
        {
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "item_assessments": [
                _item_assessment(
                    evidence_id=bundle.selected_evidence_ids[0],
                    document_id=chunk.doc_id,
                    status=(
                        EvidenceStatus.SUFFICIENT
                        if allow
                        else EvidenceStatus.INSUFFICIENT
                    ),
                ).model_dump(mode="python", round_trip=True)
            ],
            "assessment": assessment.model_dump(mode="python", round_trip=True),
        }
    )
    return EvidenceBundleSchema.model_validate(payload)


def _current_chain(*, allow: bool) -> CurrentRouteChain:
    bundle = _bundle(marker="E1", allow=allow)
    requirements = EvidenceRequirementsMaterializer.materialize(
        source_profile_ref="profile_s11_15",
        source_task_ref=TASK_ID,
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="r1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="c1",
                        criterion="必须满足判据 C1",
                    ),
                ),
            ),
        ),
    )
    criterion = CriterionEvidenceFactSchema(
        fact_id="criterion_s11_15",
        requirement_id="r1",
        criterion_id="c1",
        support_state=(
            CriterionEvidenceSupportState.SUPPORTED
            if allow
            else CriterionEvidenceSupportState.NOT_SUPPORTED
        ),
        evidence_ids=("evidence-c1",) if allow else (),
    )
    requirement_fact = RequirementEvidenceFactSchema(
        fact_id="requirement_s11_15",
        requirement_id="r1",
        support_state=(
            RequirementEvidenceFactSchema.SupportState.SUPPORTED
            if allow
            else RequirementEvidenceFactSchema.SupportState.NOT_SUPPORTED
        ),
        criterion_fact_ids=(criterion.fact_id,),
    )
    assessment = WriterAdmissionFactAssessmentSchema(
        assessment_id="assessment_s11_15",
        assessment_state=WriterAdmissionAssessmentState.COMPLETED,
        requirements_schema_version=requirements.schema_version,
        requirements_contract_version=requirements.contract_version,
        requirements_sha256=requirements.requirements_sha256,
        evidence_bundle_schema_version=bundle.schema_version,
        evidence_bundle_sha256=evidence_bundle_sha256(bundle),
        criterion_facts=(criterion,),
        requirement_facts=(requirement_fact,),
    )
    decision = WriterAdmissionPolicy(
        decision_id_factory=lambda: (
            "decision_allow_s11_15" if allow else "decision_deny_s11_15"
        )
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )
    subject = WriterSubjectRefSchema(
        run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        section_id=SECTION_ID,
    )
    binding = WriterGateBinder.bind(
        decision=decision,
        writer_subject_ref=subject,
        write_instance_id=WRITE_INSTANCE_ID,
    )
    applicability = WriterGateApplicabilityValidator().validate(
        binding=binding,
        current_writer_subject_ref=subject,
        current_write_instance_id=WRITE_INSTANCE_ID,
        current_decision=decision,
        current_active_evidence_bundle_sha256=evidence_bundle_sha256(bundle),
        current_active_requirements_sha256=requirements.requirements_sha256,
        current_active_binding_ref=WriterGateBindingRefSchema(
            binding_id=binding.binding_id,
            binding_schema_version=binding.schema_version,
            binding_contract_version=binding.contract_version,
        ),
    )
    enforcement = WriterAdmissionEnforcement().evaluate(
        applicability=applicability,
        decision=decision,
        mode="strict",
    )
    recovery_controller = PreWriteRecoveryController()
    runtime_state = recovery_controller.initialize_state(
        writer_subject_ref=subject,
        write_instance_id=WRITE_INSTANCE_ID,
        recovery_rounds_limit=2 if allow else 0,
        no_progress_rounds_limit=1,
    )
    recovery_chain = RecoveryChainSnapshot(
        evidence_bundle=bundle,
        requirements=requirements,
        assessment=assessment,
        decision=decision,
        binding=binding,
    )
    recovery_control = recovery_controller.evaluate_control_facts(
        chain=recovery_chain,
        applicability=applicability,
        runtime_state=runtime_state,
    )
    route_decision = PreWriteRouter.evaluate(
        writer_subject_ref=subject,
        write_instance_id=WRITE_INSTANCE_ID,
        decision=decision,
        binding=binding,
        applicability=applicability,
        enforcement=enforcement,
        recovery_control=recovery_control,
    )
    return CurrentRouteChain(
        evidence_bundle=bundle,
        decision=decision,
        binding=binding,
        applicability=applicability,
        enforcement=enforcement,
        recovery_control=recovery_control,
        route_decision=route_decision,
    )


def _prompt_service() -> SectionPromptService:
    return SectionPromptService(
        context_manager=LLMContextManager(),
        context_policy=SectionGenerationContextPolicy(),
        prompt_manager=None,
        prompt_id="unused_s11_15_prompt",
        model_gateway=None,
    )


def _section_service(
    calls: list[dict[str, object]],
    *,
    responses: list[ModelResponseSchema],
    store: SQLitePreWriteExecutionStore | None = None,
    expected_key: WriterLogicalExecutionKey | None = None,
    advance_to_e2: bool = False,
    delay_seconds: float = 0.0,
) -> SectionGenerationService:
    prompt_service = _prompt_service()
    model_service = object.__new__(
        __import__(
            "apps.enterprise_document.services.scheme_writer.model_service",
            fromlist=["SectionModelService"],
        ).SectionModelService
    )
    model_service.prompt_service = prompt_service
    model_service.context_manager = prompt_service.context_manager
    model_service.model_gateway = None

    def fake_call_model(self, shared_state, **kwargs):
        del self
        call_index = len(calls)
        calls.append(dict(kwargs))
        if store is not None and expected_key is not None:
            record = store.get_writer_execution(expected_key)
            assert record is not None
            assert record.state is WriterExecutionState.DISPATCHED
        if advance_to_e2 and call_index == 0:
            e2 = _bundle(marker="E2", allow=True)
            shared_state.extra["active_evidence_bundle"] = e2.model_dump(mode="json")
            shared_state.extra["active_evidence_bundle_sha256"] = (
                evidence_bundle_sha256(e2)
            )
        if delay_seconds:
            time.sleep(delay_seconds)
        return responses[min(call_index, len(responses) - 1)]

    model_service.call_model = MethodType(fake_call_model, model_service)
    service = object.__new__(SectionGenerationService)
    service.model_service = model_service
    service.content_recovery = SectionContentRecovery(
        model_service=model_service,
        prompt_service=prompt_service,
    )
    service.prompt_service = prompt_service
    return service


def _success_response(
    *,
    call_id: str = "model_call_s11_15",
    content: str = "Target Writer 生成的完整章节正文。",
    finish_reason: str = "stop",
) -> ModelResponseSchema:
    return ModelResponseSchema(
        model_call_id=call_id,
        task_id=TASK_ID,
        run_id=RUN_ID,
        model_name="same-target-writer-model",
        success=True,
        content=content,
        finish_reason=finish_reason,
        created_at="2026-08-23T00:00:01+08:00",
    )


def _failure_response() -> ModelResponseSchema:
    return ModelResponseSchema(
        model_call_id="model_call_s11_15_timeout",
        task_id=TASK_ID,
        run_id=RUN_ID,
        model_name="same-target-writer-model",
        success=False,
        error=ErrorSchema(
            error_code="MODEL_TIMEOUT",
            error_type="TimeoutError",
            message="provider timed out",
            retryable=False,
            failure_category=FailureCategory.TIMEOUT,
        ),
        error_message="provider timed out",
        created_at="2026-08-23T00:00:01+08:00",
    )


def _builder(prompt_service: SectionPromptService) -> WriterInvocationBuilder:
    return WriterInvocationBuilder(prompt_service=prompt_service)


def _build(
    builder: WriterInvocationBuilder,
    chain: CurrentRouteChain,
):
    return builder.build(
        route_decision=chain.route_decision,
        decision=chain.decision,
        shared_state=_shared_state(),
        project_input=_project_input(),
        evidence_bundle=chain.evidence_bundle,
        section_title="技术方案",
        section_order=1,
        structured_facts=_structured_facts(),
        previous_sections=[],
    )


def _service(
    *,
    store: SQLitePreWriteExecutionStore,
    section_service: SectionGenerationService,
    duplicate_wait_seconds: float = 2.0,
) -> PreWriteWriterInvocationService:
    return PreWriteWriterInvocationService(
        builder=_builder(section_service.prompt_service),
        integrity_validator=WriterInvocationIntegrityValidator(),
        execution_store=store,
        route_decision_store=SQLitePreWriteRouteDecisionStore(store.database_path),
        section_generation_service=section_service,
        duplicate_wait_seconds=duplicate_wait_seconds,
        duplicate_poll_seconds=0.005,
    )


def _execute(
    service: PreWriteWriterInvocationService,
    chain: CurrentRouteChain,
    *,
    evidence_bundle: EvidenceBundleSchema | None = None,
    writer_subject_ref: WriterSubjectRefSchema | None = None,
    structured_facts: list[StructuredFactSchema] | None = None,
    technical_execution_constraints: dict[str, object] | None = None,
):
    authorization_snapshot_sha256 = PreWriteRouter.authorization_snapshot_sha256(
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
    )
    service.route_decision_store.put_if_absent_or_same(
        chain.route_decision,
        authorization_snapshot_sha256=authorization_snapshot_sha256,
    )
    prepared = _build(service.builder, chain)
    service.execution_store.reserve_writer_execution(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )
    return service.execute_initial(
        chain.route_decision,
        writer_subject_ref=(
            writer_subject_ref or chain.route_decision.writer_subject_ref
        ),
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
        shared_state=_shared_state(),
        project_input=_project_input(),
        evidence_bundle=evidence_bundle or chain.evidence_bundle,
        section_title="技术方案",
        section_order=1,
        structured_facts=(
            _structured_facts()
            if structured_facts is None
            else structured_facts
        ),
        previous_sections=[],
        technical_execution_constraints=technical_execution_constraints,
    )


def test_writer_logical_key_and_locked_snapshot_are_deterministic() -> None:
    chain = _current_chain(allow=True)
    builder = _builder(_prompt_service())
    first = _build(builder, chain)
    second = _build(builder, chain)

    assert first.logical_execution_key == second.logical_execution_key
    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.invocation == second.invocation
    assert first.logical_execution_key == WriterLogicalExecutionKey(
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        route_decision_ref=first.invocation.route_decision_ref,
    )
    changed_route = chain.route_decision.model_copy(
        update={"route_decision_id": f"{chain.route_decision.route_decision_id}_new"}
    )
    changed = builder.build(
        route_decision=changed_route,
        decision=chain.decision,
        shared_state=_shared_state(),
        project_input=_project_input(),
        evidence_bundle=chain.evidence_bundle,
        section_title="技术方案",
        section_order=1,
        structured_facts=_structured_facts(),
        previous_sections=[],
    )
    assert changed.logical_execution_key != first.logical_execution_key


def test_stale_route_and_section_mismatch_remain_upstream_handoff_failures(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
        duplicate_wait_seconds=0,
    )

    stale = chain.route_decision.model_copy(
        update={"route_decision_id": "stale_route_decision"}
    )
    stale_chain = replace(chain, route_decision=stale)
    with pytest.raises(PreWriteRoutingIntegrityError):
        _execute(service, stale_chain)

    mismatched_subject = chain.route_decision.writer_subject_ref.model_copy(
        update={"section_id": "different_section"}
    )
    with pytest.raises(PreWriteRoutingIntegrityError):
        _execute(service, chain, writer_subject_ref=mismatched_subject)
    assert calls == []


def test_option_d_direct_execute_without_reserved_fails_closed_provider_zero(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
    )
    auth_hash = PreWriteRouter.authorization_snapshot_sha256(
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
    )
    service.route_decision_store.put_if_absent_or_same(
        chain.route_decision,
        authorization_snapshot_sha256=auth_hash,
    )

    result = service.execute_initial(
        chain.route_decision,
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
        shared_state=_shared_state(),
        project_input=_project_input(),
        evidence_bundle=chain.evidence_bundle,
        section_title="技术方案",
        section_order=1,
        structured_facts=_structured_facts(),
        previous_sections=[],
    )

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert result.failure_kind is WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE
    assert calls == []
    assert store.get_writer_execution(result.logical_execution_key) is None


def test_invocation_evidence_mismatch_returns_invocation_integrity_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
    )

    substituted_evidence = _bundle(marker="E2", allow=True)
    assert evidence_bundle_sha256(substituted_evidence) != (
        chain.decision.fact_assessment_ref.evidence_bundle_sha256
    )
    original_route_ref = chain.route_decision.route_decision_id
    original_decision_ref = chain.decision.decision_id

    result = _execute(
        service,
        chain,
        evidence_bundle=substituted_evidence,
    )

    assert chain.route_decision.route_decision_id == original_route_ref
    assert chain.decision.decision_id == original_decision_ref

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE
    )
    assert result.artifact is None
    assert result.error is not None
    assert result.technical_execution_metadata.model_call_ids == []
    assert calls == []
    reserved = store.get_writer_execution(result.logical_execution_key)
    assert reserved is not None
    assert reserved.state is WriterExecutionState.RESERVED


def test_integrity_validator_rejects_tampered_key_and_context_lock() -> None:
    chain = _current_chain(allow=True)
    builder = _builder(_prompt_service())
    prepared = _build(builder, chain)
    validator = WriterInvocationIntegrityValidator()
    kwargs = {
        "route_decision": chain.route_decision,
        "decision": chain.decision,
        "shared_state": _shared_state(),
        "project_input": _project_input(),
        "evidence_bundle": chain.evidence_bundle,
        "section_title": "技术方案",
        "section_order": 1,
        "structured_facts": _structured_facts(),
        "previous_sections": [],
    }

    tampered_key = prepared.logical_execution_key.model_copy(
        update={"write_instance_id": "tampered_write_instance"}
    )
    with pytest.raises(WriterInvocationIntegrityError, match="LogicalExecutionKey"):
        validator.validate(replace(prepared, logical_execution_key=tampered_key), **kwargs)

    tampered_recovery = replace(
        prepared.target_prompt.technical_recovery_context,
        evidence_bundle_sha256="f" * 64,
    )
    tampered_prompt = replace(
        prepared.target_prompt,
        technical_recovery_context=tampered_recovery,
    )
    with pytest.raises(WriterInvocationIntegrityError, match="evidence_bundle_sha256"):
        validator.validate(replace(prepared, target_prompt=tampered_prompt), **kwargs)


def test_route_class_mismatch_returns_invocation_integrity_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    prepared = _build(_builder(_prompt_service()), chain)
    mismatched_invocation = prepared.invocation.model_copy(
        update={"generation_class": WriterGenerationClass.FALLBACK_WRITE}
    )
    mismatched = replace(
        prepared,
        invocation=mismatched_invocation,
        semantic_fingerprint=canonical_sha256(
            mismatched_invocation.model_dump(mode="json")
        ),
    )
    calls: list[dict[str, object]] = []
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=_section_service(calls, responses=[_success_response()]),
    )

    def return_mismatched(self, **kwargs):
        del self, kwargs
        return mismatched

    service.builder.build = MethodType(return_mismatched, service.builder)
    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE
    )
    assert "generation_class" in result.error.message
    assert calls == []


def test_tampered_writer_logical_key_returns_invocation_integrity_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    prepared = _build(_builder(_prompt_service()), chain)
    tampered = replace(
        prepared,
        logical_execution_key=prepared.logical_execution_key.model_copy(
            update={"write_instance_id": "tampered_write_instance"}
        ),
    )
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
    )

    def return_tampered(self, **kwargs):
        del self, kwargs
        return tampered

    service.builder.build = MethodType(return_tampered, service.builder)
    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE
    )
    assert result.logical_execution_key == prepared.logical_execution_key
    assert result.artifact is None
    assert calls == []
    assert store.get_writer_execution(prepared.logical_execution_key) is None


def test_normal_and_fallback_use_same_writer_and_preserve_route_class(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    section_service = _section_service(
        calls,
        responses=[
            _success_response(call_id="normal_call"),
            _success_response(call_id="fallback_call"),
        ],
    )
    service = _service(
        store=SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3"),
        section_service=section_service,
    )

    normal = _execute(service, _current_chain(allow=True))
    fallback = _execute(service, _current_chain(allow=False))

    assert normal.status is WriterExecutionStatus.SUCCESS
    assert fallback.status is WriterExecutionStatus.SUCCESS
    assert normal.generation_provenance.generation_class == (
        WriterGenerationClass.NORMAL_WRITE.value
    )
    assert fallback.generation_provenance.generation_class == (
        WriterGenerationClass.FALLBACK_WRITE.value
    )
    # AV-S12-048: fallback disclosure/provenance is system-owned.  The
    # deliberately generic model body contains no fallback warning/marker.
    assert "fallback" not in fallback.artifact.content.lower()
    assert fallback.generation_provenance.generation_class == (
        WriterGenerationClass.FALLBACK_WRITE.value
    )
    assert len(calls) == 2
    assert normal.artifact.extra["target_initial_writer"] is True
    assert fallback.artifact.extra["target_initial_writer"] is True


def test_same_key_concurrent_duplicate_dispatches_model_once_and_converges(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    prepared = _build(_builder(_prompt_service()), chain)
    section_service = _section_service(
        calls,
        responses=[_success_response()],
        store=store,
        expected_key=prepared.logical_execution_key,
        delay_seconds=0.1,
    )
    first_service = _service(store=store, section_service=section_service)
    second_service = _service(store=store, section_service=section_service)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda service: _execute(service, chain),
                (first_service, second_service),
            )
        )

    assert results[0] == results[1]
    assert results[0].status is WriterExecutionStatus.SUCCESS
    assert len(calls) == 1
    records = store.get_writer_execution(prepared.logical_execution_key)
    assert records is not None
    assert records.state is WriterExecutionState.SUCCEEDED


def test_dispatched_is_durable_before_model_and_terminal_result_is_reused(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    prepared = _build(_builder(_prompt_service()), chain)
    calls: list[dict[str, object]] = []
    section_service = _section_service(
        calls,
        responses=[_success_response()],
        store=store,
        expected_key=prepared.logical_execution_key,
    )
    service = _service(store=store, section_service=section_service)

    first = _execute(service, chain)
    second = _execute(service, chain)

    assert first == second
    assert len(calls) == 1
    record = store.get_writer_execution(prepared.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.SUCCEEDED
    assert record.result == first


def test_reserved_never_dispatched_replay_can_safely_dispatch_once(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    prepared = _build(_builder(_prompt_service()), chain)
    reserved = store.reserve_writer_execution(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )
    assert reserved.state is WriterExecutionState.RESERVED
    calls: list[dict[str, object]] = []
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
    )

    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.SUCCESS
    assert len(calls) == 1
    invocation_ref = WriterGenerationInvocationRefSchema(
        logical_execution_key=prepared.logical_execution_key,
        invocation_sha256=prepared.semantic_fingerprint,
    )
    assert store.resolve_writer_generation_invocation(invocation_ref) == prepared.invocation


def test_dispatch_persistence_failure_prevents_model_call(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
    )

    def fail_mark_dispatched(self, logical_execution_key, *, semantic_fingerprint):
        del self, logical_execution_key, semantic_fingerprint
        raise sqlite3.OperationalError("simulated DISPATCHED persistence failure")

    store.mark_writer_dispatched = MethodType(fail_mark_dispatched, store)
    with pytest.raises(sqlite3.OperationalError):
        _execute(service, chain)
    assert calls == []


def test_same_writer_key_semantic_drift_converges_to_invocation_integrity_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
    )
    first = _execute(service, chain)
    changed_fact = _structured_facts()[0].model_copy(
        update={"content": "CHANGED_LOCKED_STRUCTURED_FACT"}
    )

    second = _execute(service, chain, structured_facts=[changed_fact])

    assert first.status is WriterExecutionStatus.SUCCESS
    assert second.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        second.failure_kind
        is WriterExecutionFailureKind.INVOCATION_INTEGRITY_FAILURE
    )
    assert second.artifact is None
    assert len(calls) == 1
    stored = store.get_writer_execution(first.logical_execution_key)
    assert stored is not None
    assert stored.state is WriterExecutionState.SUCCEEDED
    assert stored.result == first


def test_ambiguous_dispatched_restore_never_redispatches(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    prepared = _build(_builder(_prompt_service()), chain)
    store.reserve_writer_execution(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )
    assert store.mark_writer_dispatched(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )

    calls: list[dict[str, object]] = []
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_success_response()]),
        duplicate_wait_seconds=0,
    )
    with pytest.raises(WriterIndeterminateExternalExecutionError):
        _execute(service, chain)
    assert calls == []
    record = store.get_writer_execution(prepared.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.DISPATCHED


def test_technical_failure_is_durable_and_not_business_fallback(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    service = _service(
        store=store,
        section_service=_section_service(calls, responses=[_failure_response()]),
    )

    first = _execute(service, chain)
    second = _execute(service, chain)

    assert first == second
    assert first.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert first.failure_kind is WriterExecutionFailureKind.MODEL_TIMEOUT
    assert first.artifact is None
    assert first.generation_provenance.generation_class == "normal_write"
    assert len(calls) == 1


def test_unrelated_value_error_is_model_invocation_failure_not_context_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    calls: list[dict[str, object]] = []
    section_service = _section_service(calls, responses=[_success_response()])

    def raise_provider_value_error(self, invocation, shared_state, **kwargs):
        del self, invocation, shared_state, kwargs
        raise ValueError("provider response decoder defect")

    section_service.generate_from_pre_write_invocation = MethodType(
        raise_provider_value_error,
        section_service,
    )
    service = _service(store=store, section_service=section_service)

    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert result.failure_kind is WriterExecutionFailureKind.MODEL_INVOCATION_FAILURE
    assert result.error.error_type == "ValueError"


def test_workflow_budget_exceeded_before_artifact_is_runtime_resource_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    calls: list[dict[str, object]] = []
    section_service = _section_service(calls, responses=[_success_response()])
    budget = WorkflowBudget(
        max_retrieval_rounds=1,
        max_rewrite_rounds=1,
        max_total_llm_calls=1,
        max_total_tokens=1024,
    )
    budget.llm_calls = 1
    budget_checks: list[str] = []

    def exhaust_before_provider(self, shared_state, **kwargs):
        del self, shared_state, kwargs
        budget_checks.append("checked_before_provider")
        budget.reserve_llm_call(max_tokens=1)
        raise AssertionError("unreachable after WorkflowBudgetExceeded")

    section_service.model_service.call_model = MethodType(
        exhaust_before_provider,
        section_service.model_service,
    )
    service = _service(store=store, section_service=section_service)

    result = _execute(service, chain)

    assert budget_checks == ["checked_before_provider"]
    assert calls == []
    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.RUNTIME_RESOURCE_FAILURE
    )
    assert result.artifact is None
    assert result.error.error_type == WorkflowBudgetExceeded.__name__
    assert (
        result.error.failure_category
        == FailureCategory.RESOURCE_EXHAUSTED.value
    )
    assert result.technical_execution_metadata.model_call_ids == []
    record = store.get_writer_execution(result.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.CONFIRMED_FAILED
    assert record.result == result


def test_truncation_recovery_preserves_all_model_call_ids(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=_section_service(
            calls,
            responses=[
                _success_response(
                    call_id="primary_truncated",
                    content="尚未完成的正文",
                    finish_reason="length",
                ),
                _success_response(
                    call_id="retry_complete",
                    content="恢复后的完整正文。",
                ),
            ],
        ),
    )

    result = _execute(service, chain)

    expected = ["primary_truncated", "retry_complete"]
    assert result.status is WriterExecutionStatus.SUCCESS
    assert result.technical_execution_metadata.model_call_id == expected[0]
    assert result.technical_execution_metadata.model_call_ids == expected
    assert result.generation_provenance.model_call_ids == expected
    assert result.artifact.extra["model_call_id"] == expected[0]
    assert result.artifact.extra["model_call_ids"] == expected
    assert len(calls) == 2


def test_compression_preserves_all_model_call_ids(tmp_path: Path) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=_section_service(
            calls,
            responses=[
                _success_response(
                    call_id="primary_overlong",
                    content="超长正文。" * 400,
                ),
                _success_response(
                    call_id="compression_call",
                    content="压缩后的完整正文。",
                ),
            ],
        ),
    )

    result = _execute(service, chain)

    expected = ["primary_overlong", "compression_call"]
    assert result.status is WriterExecutionStatus.SUCCESS
    assert result.technical_execution_metadata.model_call_ids == expected
    assert result.generation_provenance.model_call_ids == expected
    assert result.artifact.extra["model_call_ids"] == expected
    assert len(calls) == 2


def test_unrecoverable_truncation_preserves_all_actual_model_call_ids(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=_section_service(
            calls,
            responses=[
                _success_response(
                    call_id="primary_still_truncated",
                    content="未结束正文",
                    finish_reason="length",
                ),
                _success_response(
                    call_id="retry_still_truncated",
                    content="仍未结束正文",
                    finish_reason="length",
                ),
            ],
        ),
    )

    result = _execute(service, chain)

    expected = ["primary_still_truncated", "retry_still_truncated"]
    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.UNRECOVERABLE_TRUNCATION
    )
    assert result.artifact is None
    assert result.technical_execution_metadata.model_call_ids == expected
    assert result.generation_provenance.model_call_ids == expected
    assert len(calls) == 2


def test_context_lock_failure_during_dispatch_is_context_integrity_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    section_service = _section_service(calls, responses=[_success_response()])

    def reject_context_lock(self, invocation, shared_state, **kwargs):
        del self, invocation, shared_state, kwargs
        raise TargetTechnicalRecoveryIntegrityError("locked context drift")

    section_service.generate_from_pre_write_invocation = MethodType(
        reject_context_lock,
        section_service,
    )
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=section_service,
    )

    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.CONTEXT_INTEGRITY_FAILURE
    )
    assert result.artifact is None
    assert calls == []


def test_provider_unavailable_remains_distinct_from_generic_invocation_failure(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    unavailable = ModelResponseSchema(
        model_call_id="provider_unavailable",
        task_id=TASK_ID,
        run_id=RUN_ID,
        model_name="same-target-writer-model",
        success=False,
        error=ErrorSchema(
            error_code="MODEL_UNAVAILABLE",
            error_type="ProviderUnavailable",
            message="provider unavailable",
            retryable=False,
            failure_category=FailureCategory.AVAILABILITY,
        ),
        error_message="provider unavailable",
        created_at="2026-08-23T00:00:01+08:00",
    )
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=_section_service(calls, responses=[unavailable]),
    )

    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.MODEL_OR_PROVIDER_UNAVAILABLE
    )
    assert result.artifact is None
    assert result.technical_execution_metadata.model_call_ids == [
        "provider_unavailable"
    ]
    assert len(calls) == 1


def test_empty_model_output_is_empty_or_unusable_output(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=True)
    calls: list[dict[str, object]] = []
    service = _service(
        store=SQLitePreWriteExecutionStore(
            tmp_path / "execution_control.sqlite3"
        ),
        section_service=_section_service(
            calls,
            responses=[
                _success_response(call_id="empty_primary", content=""),
                _success_response(call_id="empty_retry", content=""),
            ],
        ),
    )

    result = _execute(service, chain)

    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert (
        result.failure_kind
        is WriterExecutionFailureKind.EMPTY_OR_UNUSABLE_OUTPUT
    )
    assert result.artifact is None
    assert result.technical_execution_metadata.model_call_ids == [
        "empty_primary",
        "empty_retry",
    ]
    assert len(calls) == 2


def test_e1_remains_locked_when_runtime_advances_to_e2_during_recovery(
    tmp_path: Path,
) -> None:
    chain = _current_chain(allow=False)
    state = _shared_state()
    calls: list[dict[str, object]] = []
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    section_service = _section_service(
        calls,
        responses=[
            _success_response(
                call_id="primary_length",
                content="未完成的 E1 正文",
                finish_reason="length",
            ),
            _success_response(
                call_id="recovered_e1",
                content="E1 约束下恢复完成的正文。",
            ),
        ],
        advance_to_e2=True,
    )
    service = _service(store=store, section_service=section_service)
    authorization_hash = PreWriteRouter.authorization_snapshot_sha256(
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
    )
    service.route_decision_store.put_if_absent_or_same(
        chain.route_decision,
        authorization_snapshot_sha256=authorization_hash,
    )
    prepared = service.builder.build(
        route_decision=chain.route_decision,
        decision=chain.decision,
        shared_state=state,
        project_input=_project_input(),
        evidence_bundle=chain.evidence_bundle,
        section_title="技术方案",
        section_order=1,
        structured_facts=_structured_facts(),
        previous_sections=[],
    )
    service.execution_store.reserve_writer_execution(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )

    result = service.execute_initial(
        chain.route_decision,
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
        shared_state=state,
        project_input=_project_input(),
        evidence_bundle=chain.evidence_bundle,
        section_title="技术方案",
        section_order=1,
        structured_facts=_structured_facts(),
        previous_sections=[],
    )

    assert result.status is WriterExecutionStatus.SUCCESS
    assert len(calls) == 2
    assert state.extra["active_evidence_bundle_sha256"] == evidence_bundle_sha256(
        _bundle(marker="E2", allow=True)
    )
    for call in calls:
        assert call["context_package"]["lineage"]["evidence_bundle_sha256"] == (
            evidence_bundle_sha256(chain.evidence_bundle)
        )
        assert E1_TEXT in call["prompt"]
        assert E2_TEXT not in call["prompt"]
        assert E1_QUALIFIER in call["prompt"]
        assert E1_FACT in call["prompt"]


def test_full_s11_17_entry_rejects_tampered_context_before_model() -> None:
    chain = _current_chain(allow=True)
    prepared = _build(_builder(_prompt_service()), chain)
    calls: list[dict[str, object]] = []
    section_service = _section_service(calls, responses=[_success_response()])
    tampered_recovery = replace(
        prepared.target_prompt.technical_recovery_context,
        context_sha256="f" * 64,
    )
    tampered_prompt = replace(
        prepared.target_prompt,
        technical_recovery_context=tampered_recovery,
    )

    with pytest.raises(TargetTechnicalRecoveryIntegrityError):
        section_service.generate_from_pre_write_invocation(
            prepared.invocation,
            _shared_state(),
            project_input=_project_input(),
            target_prompt=tampered_prompt,
        )
    assert calls == []


def test_static_owner_and_negative_architecture_fences() -> None:
    services_root = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
    )
    writer_source = (services_root / "writer_invocation.py").read_text(
        encoding="utf-8"
    )
    generation_source = inspect.getsource(
        SectionGenerationService.generate_from_pre_write_invocation
    )
    for marker in (
        "class NormalWriter",
        "class FallbackWriter",
        "class FallbackGenerationService",
        "class RecoveryEvidenceAuthority",
        "class WriterEvidenceAuthority",
        "WriterAdmissionPolicy(",
        "WriterGateBinder(",
        "WriterAdmissionEnforcement(",
        "evidence_assessment",
        "active_evidence_bundle",
    ):
        assert marker not in writer_source
    for marker in (
        "PreWriteRouter",
        "WriterAdmissionPolicy",
        "WriterGateBinding",
        "EnforcementResult",
        "RecoveryControlFacts",
        "selected_route",
        "NORMAL_WRITE",
        "FALLBACK_WRITE",
    ):
        assert marker not in generation_source

    production_root = Path(__file__).parents[1] / "apps" / "enterprise_document"
    consumers = []
    for path in production_root.rglob("*.py"):
        if path.name == "writer_invocation.py":
            continue
        if "PreWriteWriterInvocationService" in path.read_text(encoding="utf-8"):
            consumers.append(path)
    # P4 S11-18 supersedes the earlier P3 zero-consumer invariant: the
    # Coordinator is now the single canonical production consumer, while no
    # second production consumer may bypass that boundary.
    assert [path.relative_to(production_root).as_posix() for path in consumers] == [
        "services/scheme_writer/section_execution_coordinator.py"
    ]
