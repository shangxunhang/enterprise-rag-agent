"""T12-07: Gate V2 checkpoint-v3 / technical-recovery acceptance proof."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction,
    RunningInvocationRecoveryContextSchema,
    RunningInvocationRecoveryInputSchema,
    UnderlyingInvocationStatus,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionFactAssessmentSchema,
    WriterAdmissionResolutionState,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingRefSchema,
    WriterGateBindingSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
    EnforcementResultSchema,
    EnforcementSnapshotRefSchema,
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
    PreWriteRouteDecisionSchema,
    RecoveryControlFactsSchema,
    RecoveryControlSnapshotRefSchema,
    RecoveryRoundOutcome,
    RecoveryRuntimeStateSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterGenerationClass,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SCHEME_GENERATION_GRAPH_STATE_VERSION,
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStateSchema,
    HardGateResultSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionPlanSchema,
    SectionStateSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SECTION_EXECUTION_GRAPH_STATE_VERSION,
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphStateCodec,
)
from apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery import (
    RunningInvocationRecoveryService,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema
from schemas.status import ExecutionStatus


SHA_A = "a" * 64
SHA_B = "b" * 64
_NOW = "2026-08-22T13:00:00+08:00"


def _project_input(task_id: str) -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={
            "required_sections": ["项目概述"],
            "need_citation": False,
            "citation_required_sections": [],
        },
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["项目概述"],
        },
    )


def _section_plan(section_id: str = "section-1") -> SectionPlanSchema:
    return SectionPlanSchema(
        section_id=section_id,
        section_title="项目概述",
        section_order=1,
        citation_required=False,
    )


def _graph_state(*, task_id: str, run_id: str) -> GraphStateSchema:
    return GraphStateSchema(
        task_id=task_id,
        run_id=run_id,
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id=task_id,
                run_id=run_id,
                task_type="scheme_generation",
            ),
        ),
        created_at=_NOW,
    )


def _subject() -> WriterSubjectRefSchema:
    return WriterSubjectRefSchema(
        run_id="run-p2c",
        document_id="doc-p2c",
        section_id="section-1",
    )


def _requirements() -> EvidenceRequirementsSnapshotSchema:
    return EvidenceRequirementsSnapshotSchema(
        requirements_sha256=SHA_A,
        source_profile_ref="profile:p2c",
        source_task_ref="task:p2c",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="req-1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="criterion-1",
                        criterion="必须存在已核验事实",
                    ),
                ),
            ),
        ),
    )


def _evidence_bundle() -> EvidenceBundleSchema:
    return EvidenceBundleSchema(
        query="项目概述",
        context=RAGContextSchema(context_text="", used_context_chars=0),
    )


def _assessment() -> WriterAdmissionFactAssessmentSchema:
    return WriterAdmissionFactAssessmentSchema(
        assessment_id="assessment-1",
        assessment_state=WriterAdmissionAssessmentState.PENDING,
        requirements_schema_version="evidence_requirements_snapshot_v1",
        requirements_contract_version="evidence_requirement_semantics_v1",
        requirements_sha256=SHA_A,
        evidence_bundle_schema_version="rag_evidence_contract_v1",
        evidence_bundle_sha256=SHA_B,
    )


def _assessment_ref() -> WriterAdmissionFactAssessmentRefSchema:
    assessment = _assessment()
    return WriterAdmissionFactAssessmentRefSchema(
        assessment_id=assessment.assessment_id,
        assessment_schema_version=assessment.schema_version,
        assessment_contract_version=assessment.contract_version,
        requirements_schema_version=assessment.requirements_schema_version,
        requirements_contract_version=assessment.requirements_contract_version,
        requirements_sha256=assessment.requirements_sha256,
        evidence_bundle_schema_version=assessment.evidence_bundle_schema_version,
        evidence_bundle_sha256=assessment.evidence_bundle_sha256,
    )


def _decision() -> WriterAdmissionDecisionSchema:
    return WriterAdmissionDecisionSchema(
        decision_id="decision-1",
        resolution_state=WriterAdmissionResolutionState.UNRESOLVED,
        admission_outcome=None,
        fact_assessment_ref=_assessment_ref(),
        reason_codes=("assessment_pending",),
    )


def _decision_ref() -> WriterAdmissionDecisionRefSchema:
    decision = _decision()
    return WriterAdmissionDecisionRefSchema(
        decision_id=decision.decision_id,
        decision_schema_version=decision.schema_version,
        decision_contract_version=decision.contract_version,
    )


def _binding() -> WriterGateBindingSchema:
    return WriterGateBindingSchema(
        binding_id="binding-1",
        decision_ref=_decision_ref(),
        writer_subject_ref=_subject(),
        write_instance_id="attempt-1",
    )


def _binding_ref() -> WriterGateBindingRefSchema:
    binding = _binding()
    return WriterGateBindingRefSchema(
        binding_id=binding.binding_id,
        binding_schema_version=binding.schema_version,
        binding_contract_version=binding.contract_version,
    )


def _recovery_runtime_state() -> RecoveryRuntimeStateSchema:
    return RecoveryRuntimeStateSchema(
        writer_subject_ref=_subject(),
        write_instance_id="attempt-1",
        recovery_state_revision=3,
        recovery_rounds_used=1,
        recovery_rounds_limit=2,
        consecutive_no_progress_rounds=0,
        no_progress_rounds_limit=1,
        last_round_outcome=RecoveryRoundOutcome.PROGRESS,
    )


def _enforcement() -> EnforcementResultSchema:
    return EnforcementResultSchema(
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
        mode="strict",
        action=EnforcementAction.CONTINUE,
        would_block_in_strict=False,
        enforcement_basis=EnforcementBasis.BUSINESS_ADMISSION,
    )


def _recovery_control() -> RecoveryControlFactsSchema:
    return RecoveryControlFactsSchema(
        writer_subject_ref=_subject(),
        write_instance_id="attempt-1",
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
        recovery_state_revision=3,
        recovery_available=True,
        recovery_exhausted=False,
        recovery_rounds_used=1,
        recovery_rounds_limit=2,
        consecutive_no_progress_rounds=0,
        no_progress_rounds_limit=1,
        last_round_outcome=RecoveryRoundOutcome.PROGRESS,
    )


def _route() -> PreWriteRouteDecisionSchema:
    return PreWriteRouteDecisionSchema(
        route_decision_id="route-1",
        writer_subject_ref=_subject(),
        write_instance_id="attempt-1",
        decision_ref=_decision_ref(),
        binding_ref=_binding_ref(),
        enforcement_ref=EnforcementSnapshotRefSchema(mode="strict"),
        recovery_control_ref=RecoveryControlSnapshotRefSchema(
            recovery_state_revision=3
        ),
        selected_route=PreWriteRoute.RECOVERY,
    )


def _route_ref() -> PreWriteRouteDecisionRefSchema:
    route = _route()
    return PreWriteRouteDecisionRefSchema(
        route_decision_id=route.route_decision_id,
        route_decision_schema_version=route.schema_version,
        route_decision_contract_version=route.contract_version,
    )


def _writer_key() -> WriterLogicalExecutionKey:
    return WriterLogicalExecutionKey(
        writer_subject_ref=_subject(),
        write_instance_id="attempt-1",
        route_decision_ref=_route_ref(),
    )


def _writer_invocation() -> WriterGenerationInvocationSchema:
    bundle = _evidence_bundle()
    return WriterGenerationInvocationSchema(
        writer_subject_ref=_subject(),
        write_instance_id="attempt-1",
        route_decision_ref=_route_ref(),
        generation_class=WriterGenerationClass.NORMAL_WRITE,
        canonical_evidence_input={
            **bundle.model_dump(mode="json"),
            "evidence_bundle_sha256": SHA_B,
        },
        unified_writer_input_snapshot={"section_contract": "p2c"},
        technical_execution_constraints={"timeout_seconds": 30},
    )


def _section_state() -> SectionStateSchema:
    return SectionStateSchema(
        section_id="section-1",
        section_title="项目概述",
        section_order=1,
        attempt=AttemptStateSchema(
            attempt_id="attempt-1",
            attempt_number=1,
            execution_id="execution-1",
            plan_revision=1,
        ),
    )


def _section_payload() -> SectionExecutionGraphPayloadSchema:
    task_id = "task-p2c"
    run_id = "run-p2c"
    return SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id="doc-p2c",
        project_input=_project_input(task_id),
        section_plan=_section_plan(),
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _graph_state(task_id=task_id, run_id=run_id)
        ),
        section_id="section-1",
        section_title="项目概述",
        section_order=1,
        section_state=_section_state(),
        active_evidence_bundle=_evidence_bundle(),
        active_evidence_requirements_snapshot=_requirements(),
        active_writer_admission_fact_assessment=_assessment(),
        active_writer_admission_decision=_decision(),
        active_writer_gate_binding=_binding(),
        recovery_runtime_state=_recovery_runtime_state(),
        current_enforcement_result=_enforcement(),
        current_recovery_control_facts=_recovery_control(),
        current_pre_write_route_decision=_route(),
        pending_writer_generation_invocation=_writer_invocation(),
        writer_logical_execution_key=_writer_key(),
        pending_recovery_dispatch={
            "occurrence_id": "recovery-occurrence-1",
            "semantic_fingerprint": "fingerprint-1",
            "state": "reserved",
        },
        # Deliberately business-looking legacy/passive values. Version-3
        # validation/restore must not translate them into Target authority.
        recovery_count=9,
        evidence_blocked=True,
        generation_check={"need_retrieve_more": True, "passed": False},
        current_node="evaluate_generation",
    )


def test_section_checkpoint_v3_roundtrip_contains_recovery_required_state() -> None:
    assert SECTION_EXECUTION_GRAPH_STATE_VERSION == 3
    codec = SectionExecutionGraphStateCodec()
    payload = _section_payload()

    first_state = codec.build_initial_state(payload)
    codec.assert_json_serializable(first_state)
    wire_state = json.loads(json.dumps(first_state, ensure_ascii=False))
    restored = codec.restore_payload(wire_state)
    second_dump = codec.dump_payload(restored)

    assert first_state["section_payload"] == second_dump
    assert restored.active_evidence_requirements_snapshot == payload.active_evidence_requirements_snapshot
    assert restored.active_writer_admission_fact_assessment == payload.active_writer_admission_fact_assessment
    assert restored.active_writer_admission_decision == payload.active_writer_admission_decision
    assert restored.active_writer_gate_binding == payload.active_writer_gate_binding
    assert restored.recovery_runtime_state == payload.recovery_runtime_state
    assert restored.current_enforcement_result == payload.current_enforcement_result
    assert restored.current_recovery_control_facts == payload.current_recovery_control_facts
    assert restored.current_pre_write_route_decision == payload.current_pre_write_route_decision
    assert restored.pending_writer_generation_invocation == payload.pending_writer_generation_invocation
    assert restored.writer_logical_execution_key == payload.writer_logical_execution_key
    assert restored.pending_recovery_dispatch == payload.pending_recovery_dispatch


def test_section_checkpoint_restore_preserves_identity_and_creates_no_business_projection() -> None:
    codec = SectionExecutionGraphStateCodec()
    payload = _section_payload()
    before_state = payload.section_state.model_dump(mode="json")
    before_ids = (
        payload.active_writer_admission_fact_assessment.assessment_id,
        payload.active_writer_admission_decision.decision_id,
        payload.active_writer_gate_binding.binding_id,
        payload.current_pre_write_route_decision.route_decision_id,
        payload.section_state.attempt.attempt_id,
    )

    restored = codec.restore_payload(codec.build_initial_state(payload))
    after_ids = (
        restored.active_writer_admission_fact_assessment.assessment_id,
        restored.active_writer_admission_decision.decision_id,
        restored.active_writer_gate_binding.binding_id,
        restored.current_pre_write_route_decision.route_decision_id,
        restored.section_state.attempt.attempt_id,
    )

    assert after_ids == before_ids
    assert restored.section_state.model_dump(mode="json") == before_state
    assert restored.section_state.research.recommended_action is None
    assert restored.section_state.verification.recommended_action is None


def test_checkpoint_v2_reject_and_restore_never_reconstructs_authority() -> None:
    section_codec = SectionExecutionGraphStateCodec()
    section_state = section_codec.build_initial_state(_section_payload())
    for invalid in (2, 0, 4, "3", None):
        candidate = dict(section_state)
        candidate["section_graph_state_version"] = invalid
        with pytest.raises(ValueError, match="unsupported section graph state version"):
            section_codec.restore_payload(candidate)

    task_id = "task-p2c-passive"
    run_id = "run-p2c-passive"
    passive = SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id="doc-p2c-passive",
        project_input=_project_input(task_id),
        section_plan=_section_plan("section-passive"),
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _graph_state(task_id=task_id, run_id=run_id)
        ),
        section_id="section-passive",
        section_title="项目概述",
        section_order=1,
        section_state=SectionStateSchema(
            section_id="section-passive",
            section_title="项目概述",
            section_order=1,
            attempt=AttemptStateSchema(
                attempt_id="attempt-passive",
                attempt_number=1,
                execution_id="execution-passive",
                plan_revision=1,
            ),
        ),
    )
    restored = section_codec.restore_payload(section_codec.build_initial_state(passive))
    assert restored.active_writer_admission_fact_assessment is None
    assert restored.active_writer_admission_decision is None
    assert restored.active_writer_gate_binding is None
    assert restored.current_enforcement_result is None
    assert restored.current_recovery_control_facts is None
    assert restored.current_pre_write_route_decision is None
    assert restored.pending_writer_generation_invocation is None
    assert restored.writer_logical_execution_key is None
    assert restored.pending_recovery_dispatch is None


def test_top_level_checkpoint_v3_is_behavior_neutral() -> None:
    assert SCHEME_GENERATION_GRAPH_STATE_VERSION == 3
    section_state = _section_state()
    section = SchemeSectionSchema(
        section_id="section-1",
        section_title="项目概述",
        section_order=1,
        content="partial content",
        status=ExecutionStatus.PARTIAL_SUCCESS,
        eval_result=SectionEvalSchema(
            passed=False,
            checks={"content_present": False},
            failures=["content_present"],
        ),
    )
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task-p2c",
        run_id="run-p2c",
        document_id="doc-p2c",
        sections=[section],
        section_states={"section-1": section_state},
        hard_gate=HardGateResultSchema(
            passed=False,
            checks={"required_sections_present": False},
            failures=["required_sections_present"],
        ),
    )
    before_section_state = section_state.model_dump(mode="json")
    before_document_validation = payload.document_validation.model_dump(mode="json")

    codec = SchemeGenerationGraphStateCodec()
    runtime_state = {
        "task_id": payload.task_id,
        "run_id": payload.run_id,
        **codec.build_initial_update(payload),
    }
    codec.assert_json_serializable(runtime_state)
    restored = codec.restore_payload(json.loads(json.dumps(runtime_state)))

    assert codec.dump_payload(restored) == runtime_state["scheme_generation_payload"]
    assert restored.section_states["section-1"].model_dump(mode="json") == before_section_state
    assert restored.document_validation.model_dump(mode="json") == before_document_validation
    assert restored.section_states["section-1"].verification.recommended_action is None


def test_top_level_checkpoint_v2_and_unknown_versions_are_explicitly_rejected() -> None:
    payload = SchemeGenerationGraphPayloadSchema(task_id="task-p2c", run_id="run-p2c")
    codec = SchemeGenerationGraphStateCodec()
    state = {
        "task_id": payload.task_id,
        "run_id": payload.run_id,
        **codec.build_initial_update(payload),
    }
    for invalid in (2, 0, 4, "3", None):
        candidate = dict(state)
        candidate["scheme_generation_state_version"] = invalid
        with pytest.raises(ValueError, match="unsupported scheme generation graph state version"):
            codec.restore_payload(candidate)


def test_checkpoint_static_authority_fences_are_behavior_neutral() -> None:
    root = Path(__file__).resolve().parents[1]
    section_source = (
        root / "apps/enterprise_document/schemas/section_execution_graph.py"
    ).read_text(encoding="utf-8")
    top_source = (
        root / "apps/enterprise_document/schemas/scheme_generation_graph.py"
    ).read_text(encoding="utf-8")

    assert "_project_section_lifecycle" not in section_source
    assert "_project_canonical_business_state" not in top_source
    for forbidden in (
        '"block_generation"',
        '"research_more"',
        '"accept" if',
        '"rewrite"',
        "SectionLifecycleStatus.PARTIALLY_ACCEPTED",
    ):
        assert forbidden not in section_source
        assert forbidden not in top_source
    assert "from apps.enterprise_document.services" not in section_source
    assert "from apps.enterprise_document.services" not in top_source
    assert "observability" not in section_source
    assert "observability" not in top_source
    assert "SECTION_EXECUTION_GRAPH_STATE_VERSION = 2" not in section_source
    assert "SCHEME_GENERATION_GRAPH_STATE_VERSION = 2" not in top_source


def _running_input(**updates: object) -> RunningInvocationRecoveryInputSchema:
    data: dict[str, object] = {
        "invocation_id": "invocation-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "execution_id": "execution-1",
        "attempt_id": "attempt-1",
        "input_snapshot_version": "snapshot-1",
        "idempotency_key": "idem-1",
    }
    data.update(updates)
    return RunningInvocationRecoveryInputSchema.model_validate(data)


def _running_context(**updates: object) -> RunningInvocationRecoveryContextSchema:
    data: dict[str, object] = {
        "task_id": "task-1",
        "run_id": "run-1",
        "input_snapshot_version": "snapshot-1",
        "active_execution_ids": ["execution-1"],
        "active_attempt_ids": ["attempt-1"],
    }
    data.update(updates)
    return RunningInvocationRecoveryContextSchema.model_validate(data)


def test_running_invocation_recovery_is_technical_only_and_preserves_all_actions() -> None:
    """Native readers may use typed refs; unbound flags supply no effect proof."""
    service = RunningInvocationRecoveryService()
    cases = (
        (
            _running_input(),
            _running_context(task_id="other-task"),
            InvocationRecoveryAction.DISCARD_STALE,
        ),
        (
            _running_input(),
            _running_context(draining=True),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
        (
            _running_input(completion_event_persisted=True),
            _running_context(),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
        (
            _running_input(result_persisted=True, result_ref="result://1"),
            _running_context(),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
        (
            _running_input(
                underlying_queryable=True,
                underlying_status=UnderlyingInvocationStatus.COMPLETED,
            ),
            _running_context(),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
        (
            _running_input(
                underlying_queryable=True,
                underlying_status=UnderlyingInvocationStatus.RUNNING,
            ),
            _running_context(),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
        (
            _running_input(),
            _running_context(),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
    )

    observed = set()
    for invocation, context, expected in cases:
        decision = service.decide(invocation, context)
        assert decision.action is expected
        assert not decision.starts_new_call
        observed.add(decision.action)

    assert observed == {InvocationRecoveryAction.DISCARD_STALE, InvocationRecoveryAction.DO_NOT_RESUME}
    decision_fields = set(type(service.decide(_running_input(), _running_context())).model_fields)
    assert decision_fields == {
        "schema_version",
        "invocation_id",
        "action",
        "reason_code",
        "idempotency_key",
        "result_ref",
        "starts_new_call",
    }
    assert not decision_fields & {
        "recovery_available",
        "recovery_exhausted",
        "selected_route",
        "generation_class",
        "admission_outcome",
        "writer_authorized",
    }

    recovery_source = Path(
        RunningInvocationRecoveryService.__module__.replace(".", "/") + ".py"
    )
    backend_root = Path(__file__).resolve().parents[1]
    source = (backend_root / recovery_source).read_text(encoding="utf-8")
    assert "pre_write_control" not in source
    assert "PreWriteRoute" not in source
    assert "RecoveryControlFacts" not in source
    for producer_call in (".generate_from_pre_write_invocation(", ".generate_section(",
                          ".execute_initial(", ".mark_writer_dispatched(",
                          ".persist_writer_result(", ".commit_support(", ".commit_verification("):
        assert producer_call not in source


def test_durable_before_reference_crash_windows() -> None:
    codec = SectionExecutionGraphStateCodec()
    payload = _section_payload()
    restored = codec.restore_payload(
        json.loads(json.dumps(codec.build_initial_state(payload)))
    )
    assert (
        restored.pending_writer_generation_invocation
        == payload.pending_writer_generation_invocation
    )
    assert restored.writer_logical_execution_key == payload.writer_logical_execution_key
    assert restored.pending_recovery_dispatch == payload.pending_recovery_dispatch

    graph_source = (
        Path(__file__).resolve().parents[1]
        / "apps/enterprise_document/workflows/section_execution_graph.py"
    ).read_text(encoding="utf-8")
    assert graph_source.index(
        '"recovery_runtime_state": reservation.state_after_reservation'
    ) < graph_source.index("claim_physical_dispatch(")
    assert graph_source.index(
        '"pending_writer_generation_invocation": prepared.invocation'
    ) < graph_source.index("def _invoke_pre_write_writer(")
    assert graph_source.index('"phase": "candidate_durable"') < graph_source.index(
        "def _complete_pre_write_recovery_dispatch("
    )




def _p4_runtime_writer_graph(tmp_path: Path, *, execution_store=None):
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from backend.tests import test_gate_v2_pre_write_writer_invocation as writer_fixtures
    from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore

    store = execution_store or SQLitePreWriteExecutionStore(
        tmp_path / "writer-execution-control.sqlite3"
    )
    provider_calls: list[dict[str, object]] = []
    section_service = writer_fixtures._section_service(
        provider_calls,
        responses=[
            writer_fixtures._success_response(
                content="P4 canonical durable Writer content. " * 40,
            )
        ],
    )
    writer_service = writer_fixtures._service(
        store=store,
        section_service=section_service,
        duplicate_wait_seconds=0,
    )
    coordinator = p4_e2e._p4_coordinator(
        writer=writer_service,
        recovery_rounds_limit=2,
    )
    writer_evidence = writer_fixtures._bundle(
        marker="P4-GRAPH",
        allow=True,
    ).model_copy(
        update={"task_id": "p4-task", "run_id": "p4-run"},
        deep=True,
    )
    graph, checkpoint = p4_e2e._p4_run_to_route(
        coordinator,
        writer_evidence,
    )
    return store, provider_calls, coordinator, graph, checkpoint


def _p4_prepared_writer(graph, coordinator, checkpoint):
    payload, shared_state = graph._restore(checkpoint)
    chain = graph._pre_write_chain(payload)
    subject, write_instance_id = graph._pre_write_identity(payload)
    applicability = graph._pre_write_applicability(payload)
    return coordinator.prepare_pre_write_writer(
        payload.current_pre_write_route_decision,
        writer_subject_ref=subject,
        write_instance_id=write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=applicability,
        enforcement=payload.current_enforcement_result,
        recovery_control=payload.current_recovery_control_facts,
        shared_state=shared_state,
        project_input=payload.project_input,
        evidence_bundle=chain.evidence_bundle,
        section_title=str(payload.section_title),
        section_order=int(payload.section_order or 0),
        structured_facts=list(payload.structured_facts),
        previous_sections=list(payload.previous_sections),
    )


def test_option_d_checkpoint_route_replay_uses_canonical_store(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e

    _, calls, _, graph, checkpoint = _p4_runtime_writer_graph(tmp_path)
    before = graph.codec.restore_payload(checkpoint)
    replayed = p4_e2e._p4_apply(
        graph,
        json.loads(json.dumps(checkpoint)),
        "_commit_pre_write_route",
    )
    after = graph.codec.restore_payload(replayed)
    assert after.current_pre_write_route_decision == before.current_pre_write_route_decision
    assert calls == []


def test_option_d_checkpoint_missing_canonical_route_fails_and_cannot_recreate(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e

    store, calls, _, graph, checkpoint = _p4_runtime_writer_graph(tmp_path)
    payload = graph.codec.restore_payload(checkpoint)
    route = payload.current_pre_write_route_decision
    assert route is not None
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM pre_write_route_decisions WHERE route_decision_id = ?",
            (route.route_decision_id,),
        )
        connection.commit()

    with pytest.raises(ValueError):
        p4_e2e._p4_apply(
            graph,
            json.loads(json.dumps(checkpoint)),
            "_commit_pre_write_route",
        )
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT route_decision_id FROM pre_write_route_decisions "
            "WHERE route_decision_id = ?",
            (route.route_decision_id,),
        ).fetchone()
    assert row is None
    assert calls == []


def test_option_d_checkpoint_route_payload_disagreement_fails_closed(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e

    _, calls, _, graph, checkpoint = _p4_runtime_writer_graph(tmp_path)
    tampered = json.loads(json.dumps(checkpoint))
    tampered["section_payload"]["current_pre_write_route_decision"][
        "selected_route"
    ] = "fallback"
    with pytest.raises(ValueError, match="disagrees with canonical store"):
        p4_e2e._p4_apply(graph, tampered, "_commit_pre_write_route")
    assert calls == []


def test_option_d_checkpoint_changed_current_snapshot_fails_closed(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e

    _, calls, _, graph, checkpoint = _p4_runtime_writer_graph(tmp_path)
    changed = json.loads(json.dumps(checkpoint))
    recovery = changed["section_payload"]["current_recovery_control_facts"]
    recovery["recovery_rounds_used"] = int(recovery["recovery_rounds_used"]) + 1
    with pytest.raises(ValueError, match="authorization snapshot mismatch"):
        p4_e2e._p4_apply(graph, changed, "_commit_pre_write_route")
    assert calls == []


def test_graph_reserved_writer_dispatches_exactly_once(tmp_path: Path) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from core.runtime.pre_write_execution_store import WriterExecutionState

    store, calls, coordinator, graph, checkpoint = _p4_runtime_writer_graph(
        tmp_path
    )
    payload = graph.codec.restore_payload(checkpoint)
    assert payload.writer_logical_execution_key is not None
    early_reserved = store.get_writer_execution(payload.writer_logical_execution_key)
    assert early_reserved is not None
    assert early_reserved.state is WriterExecutionState.RESERVED
    prepared = _p4_prepared_writer(graph, coordinator, checkpoint)
    reserved = store.get_writer_execution(prepared.logical_execution_key)
    assert reserved is not None
    assert reserved.state is WriterExecutionState.RESERVED
    assert calls == []

    converged = p4_e2e._p4_apply(
        graph,
        json.loads(json.dumps(checkpoint)),
        "_invoke_pre_write_writer",
    )
    record = store.get_writer_execution(prepared.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.SUCCEEDED
    assert len(calls) == 1
    assert graph.codec.restore_payload(converged).section is not None


def test_graph_ambiguous_dispatched_writer_never_redispatches(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from apps.enterprise_document.services.scheme_writer.writer_invocation import (
        WriterIndeterminateExternalExecutionError,
    )
    from core.runtime.pre_write_execution_store import WriterExecutionState

    store, calls, coordinator, graph, checkpoint = _p4_runtime_writer_graph(
        tmp_path
    )
    prepared = _p4_prepared_writer(graph, coordinator, checkpoint)
    store.reserve_writer_execution(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )
    assert store.mark_writer_dispatched(
        prepared.logical_execution_key,
        semantic_fingerprint=prepared.semantic_fingerprint,
    )

    with pytest.raises(
        WriterIndeterminateExternalExecutionError,
        match="automatic redispatch is forbidden",
    ):
        p4_e2e._p4_apply(
            graph,
            json.loads(json.dumps(checkpoint)),
            "_invoke_pre_write_writer",
        )

    record = store.get_writer_execution(prepared.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.DISPATCHED
    assert calls == []


def test_graph_reuses_durable_writer_result_when_checkpoint_is_stale(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from core.runtime.pre_write_execution_store import WriterExecutionState

    store, calls, coordinator, graph, checkpoint = _p4_runtime_writer_graph(
        tmp_path
    )
    prepared = _p4_prepared_writer(graph, coordinator, checkpoint)
    stale_checkpoint = json.loads(json.dumps(checkpoint))

    first = p4_e2e._p4_apply(
        graph,
        stale_checkpoint,
        "_invoke_pre_write_writer",
    )
    assert len(calls) == 1
    record = store.get_writer_execution(prepared.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.SUCCEEDED

    replayed = p4_e2e._p4_apply(
        graph,
        json.loads(json.dumps(checkpoint)),
        "_invoke_pre_write_writer",
    )
    assert len(calls) == 1
    first_payload = graph.codec.restore_payload(first)
    replayed_payload = graph.codec.restore_payload(replayed)
    assert replayed_payload.section == first_payload.section
    assert replayed["section_route"] == "prepare_paragraph_pipeline"


class _DispatchPersistenceFailingStore:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def mark_writer_dispatched(self, *args, **kwargs) -> bool:
        from core.runtime.pre_write_execution_store import (
            PreWriteExecutionIntegrityError,
        )

        del args, kwargs
        raise PreWriteExecutionIntegrityError(
            "injected dispatch persistence failure"
        )


def test_graph_dispatch_persistence_failure_calls_no_provider(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from core.runtime.pre_write_execution_store import (
        SQLitePreWriteExecutionStore,
        WriterExecutionState,
    )

    durable_store = SQLitePreWriteExecutionStore(
        tmp_path / "writer-dispatch-failure.sqlite3"
    )
    failing_store = _DispatchPersistenceFailingStore(durable_store)
    _, calls, coordinator, graph, checkpoint = _p4_runtime_writer_graph(
        tmp_path,
        execution_store=failing_store,
    )
    prepared = _p4_prepared_writer(graph, coordinator, checkpoint)

    with pytest.raises(p4_e2e.PreWriteWriterExecutionFailed) as captured:
        p4_e2e._p4_apply(
            graph,
            json.loads(json.dumps(checkpoint)),
            "_invoke_pre_write_writer",
        )

    assert calls == []
    assert captured.value.result.failure_kind.value == "invocation_integrity_failure"
    record = durable_store.get_writer_execution(prepared.logical_execution_key)
    assert record is not None
    assert record.state is WriterExecutionState.RESERVED


def test_recovery_reserved_occurrence_replay_consumes_zero_additional_rounds(
    tmp_path: Path,
) -> None:
    from backend.tests import test_gate_v2_pre_write_e2e as p4_e2e
    from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
        PreWriteRecoveryController,
    )
    from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore

    class FailOnceEvidenceService(p4_e2e._P4RecoveryEvidenceService):
        def retrieve(self, *args, **kwargs):
            del args, kwargs
            self.calls += 1
            if self.calls == 1:
                return None
            return self.candidate

    store = SQLitePreWriteExecutionStore(
        tmp_path / "recovery-execution-control.sqlite3"
    )
    evidence_service = FailOnceEvidenceService(
        p4_e2e._p4_bundle(selected=True)
    )
    coordinator = p4_e2e._p4_coordinator(
        writer=p4_e2e._P4WriterInvocation(),
        recovery_rounds_limit=2,
        recovery_controller=PreWriteRecoveryController(
            execution_store=store
        ),
        evidence_service=evidence_service,
        query_builder=p4_e2e._P4RecoveryQueryBuilder(),
    )
    graph, route_checkpoint = p4_e2e._p4_run_to_route(
        coordinator,
        p4_e2e._p4_bundle(selected=False),
    )
    reserved_checkpoint = p4_e2e._p4_apply(
        graph,
        route_checkpoint,
        "_dispatch_pre_write_recovery",
    )
    reserved_checkpoint = json.loads(json.dumps(reserved_checkpoint))
    reserved = graph.codec.restore_payload(reserved_checkpoint)
    reservation = reserved.pending_recovery_dispatch["reservation"]

    assert reserved.pending_recovery_dispatch["phase"] == "reserved"
    assert reserved.recovery_runtime_state.recovery_rounds_used == 1
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM recovery_dispatches"
        ).fetchone()[0] == 0

    failed = p4_e2e._p4_apply(
        graph,
        reserved_checkpoint,
        "_execute_pre_write_recovery",
    )
    after_failure = graph.codec.restore_payload(failed)
    assert after_failure.recovery_runtime_state.recovery_rounds_used == 1
    assert evidence_service.calls == 1

    replayed = p4_e2e._p4_apply(
        graph,
        json.loads(json.dumps(reserved_checkpoint)),
        "_execute_pre_write_recovery",
    )
    candidate = graph.codec.restore_payload(replayed)
    replay_reservation = candidate.pending_recovery_dispatch["reservation"]
    assert candidate.pending_recovery_dispatch["phase"] == "candidate_durable"
    assert candidate.recovery_runtime_state.recovery_rounds_used == 1
    assert replay_reservation["occurrence_id"] == reservation["occurrence_id"]
    assert evidence_service.calls == 2
    with store._connect() as connection:
        row = connection.execute(
            "SELECT occurrence_id, status, owner_id, claim_token "
            "FROM recovery_dispatches"
        ).fetchone()
    assert tuple(row[:2]) == (reservation["occurrence_id"], "in_progress")
    assert row["owner_id"] is not None
    assert row["claim_token"] is not None


def test_running_invocation_recovery_and_archived_runtime_are_not_gate_recovery_authority() -> None:
    test_running_invocation_recovery_is_technical_only_and_preserves_all_actions()

    graph_source = (
        Path(__file__).resolve().parents[1]
        / "apps/enterprise_document/workflows/section_execution_graph.py"
    ).read_text(encoding="utf-8")
    target_control = graph_source[
        graph_source.index("def _evaluate_pre_write_admission(") :
        graph_source.index("def _generate_section(")
    ]
    assert "RunningInvocationRecovery" not in target_control
    assert "ArchivedRuntime" not in target_control
    assert "ResearchRecommendedAction" not in target_control
