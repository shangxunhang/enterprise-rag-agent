"""T12-04 focused Gate V2 Target Recovery control acceptance tests."""

from __future__ import annotations

import time

import pytest

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
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    RecoveryExhaustionReason,
    RecoveryRoundOutcome,
)
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
    PreWriteRecoveryIntegrityError,
    RecoveryChainSnapshot,
    RecoveryPromotionProposal,
    RecoveryRoundReservation,
)
from apps.enterprise_document.services.scheme_writer.section_retrieval_query_builder import (
    SectionRetrievalQueryBuilder,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    WriterAdmissionPolicy,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    WriterGateApplicabilityValidator,
    WriterGateBinder,
)
from contracts.evidence import evidence_bundle_sha256
from core.runtime.pre_write_execution_store import (
    RecoveryDispatchStatus,
    SQLitePreWriteExecutionStore,
)
from schemas.rag import EvidenceBundleSchema, RAGContextSchema


SUBJECT = WriterSubjectRefSchema(
    run_id="run-p3a",
    document_id="document-p3a",
    section_id="section-p3a",
)
WRITE_INSTANCE_ID = "write-instance-p3a"


def _requirements(*, source_profile_ref: str = "profile-v1"):
    return EvidenceRequirementsMaterializer.materialize(
        source_profile_ref=source_profile_ref,
        source_task_ref="task-v1",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="r1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="c1", criterion="必须说明建设范围"
                    ),
                    EvidenceCriterionSchema(
                        criterion_id="c2", criterion="必须给出资源容量依据"
                    ),
                    EvidenceCriterionSchema(
                        criterion_id="c3", criterion="必须说明验收指标"
                    ),
                ),
            ),
        ),
    )


def _bundle(query: str) -> EvidenceBundleSchema:
    return EvidenceBundleSchema(
        query=query,
        context=RAGContextSchema(context_text="", used_context_chars=0),
    )


def _assessment(*, assessment_id: str, requirements, bundle, blockers: set[str]):
    criterion_facts = []
    for index, criterion_id in enumerate(("c1", "c2", "c3"), start=1):
        blocked = criterion_id in blockers
        criterion_facts.append(
            CriterionEvidenceFactSchema(
                fact_id=f"{assessment_id}-criterion-{index}",
                requirement_id="r1",
                criterion_id=criterion_id,
                support_state=(
                    CriterionEvidenceSupportState.NOT_SUPPORTED
                    if blocked
                    else CriterionEvidenceSupportState.SUPPORTED
                ),
                evidence_ids=() if blocked else (f"evidence-{criterion_id}",),
            )
        )
    requirement_state = (
        RequirementEvidenceFactSchema.SupportState.SUPPORTED
        if not blockers
        else (
            RequirementEvidenceFactSchema.SupportState.NOT_SUPPORTED
            if len(blockers) == 3
            else RequirementEvidenceFactSchema.SupportState.PARTIALLY_SUPPORTED
        )
    )
    return WriterAdmissionFactAssessmentSchema(
        assessment_id=assessment_id,
        assessment_state=WriterAdmissionAssessmentState.COMPLETED,
        requirements_schema_version=requirements.schema_version,
        requirements_contract_version=requirements.contract_version,
        requirements_sha256=requirements.requirements_sha256,
        evidence_bundle_schema_version=bundle.schema_version,
        evidence_bundle_sha256=evidence_bundle_sha256(bundle),
        criterion_facts=tuple(criterion_facts),
        requirement_facts=(
            RequirementEvidenceFactSchema(
                fact_id=f"{assessment_id}-requirement-1",
                requirement_id="r1",
                support_state=requirement_state,
                criterion_fact_ids=tuple(item.fact_id for item in criterion_facts),
            ),
        ),
    )


def _pending_assessment(*, assessment_id: str, requirements, bundle):
    return WriterAdmissionFactAssessmentSchema(
        assessment_id=assessment_id,
        assessment_state=WriterAdmissionAssessmentState.PENDING,
        requirements_schema_version=requirements.schema_version,
        requirements_contract_version=requirements.contract_version,
        requirements_sha256=requirements.requirements_sha256,
        evidence_bundle_schema_version=bundle.schema_version,
        evidence_bundle_sha256=evidence_bundle_sha256(bundle),
    )


def _chain(*, name: str, blockers: set[str], bundle_query: str, requirements=None):
    requirements = requirements or _requirements()
    bundle = _bundle(bundle_query)
    assessment = _assessment(
        assessment_id=f"assessment-{name}",
        requirements=requirements,
        bundle=bundle,
        blockers=blockers,
    )
    decision = WriterAdmissionPolicy(
        decision_id_factory=lambda: f"decision-{name}"
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )
    binding = WriterGateBinder.bind(
        decision=decision,
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
    )
    return RecoveryChainSnapshot(
        evidence_bundle=bundle,
        requirements=requirements,
        assessment=assessment,
        decision=decision,
        binding=binding,
    )


def _pending_chain(*, name: str = "pending"):
    requirements = _requirements()
    bundle = _bundle("pending")
    assessment = _pending_assessment(
        assessment_id=f"assessment-{name}",
        requirements=requirements,
        bundle=bundle,
    )
    decision = WriterAdmissionPolicy(
        decision_id_factory=lambda: f"decision-{name}"
    ).evaluate(
        assessment=assessment,
        contract=WriterAdmissionContractSchema(),
    )
    binding = WriterGateBinder.bind(
        decision=decision,
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
    )
    return RecoveryChainSnapshot(
        evidence_bundle=bundle,
        requirements=requirements,
        assessment=assessment,
        decision=decision,
        binding=binding,
    )


def _binding_ref(chain: RecoveryChainSnapshot) -> WriterGateBindingRefSchema:
    return WriterGateBindingRefSchema(
        binding_id=chain.binding.binding_id,
        binding_schema_version=chain.binding.schema_version,
        binding_contract_version=chain.binding.contract_version,
    )


def _applicable(chain: RecoveryChainSnapshot) -> BindingApplicabilityResult:
    result = WriterGateApplicabilityValidator().validate(
        binding=chain.binding,
        current_writer_subject_ref=SUBJECT,
        current_write_instance_id=WRITE_INSTANCE_ID,
        current_decision=chain.decision,
        current_active_evidence_bundle_sha256=evidence_bundle_sha256(
            chain.evidence_bundle
        ),
        current_active_requirements_sha256=chain.requirements.requirements_sha256,
        current_active_binding_ref=_binding_ref(chain),
    )
    assert result.state is BindingApplicabilityState.APPLICABLE
    return result


def _state(controller: PreWriteRecoveryController, *, rounds=2, no_progress=1):
    return controller.initialize_state(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        recovery_rounds_limit=rounds,
        no_progress_rounds_limit=no_progress,
    )


def test_recovery_entry_matrix_allow_deny_unresolved_and_integrity() -> None:
    controller = PreWriteRecoveryController()
    state = _state(controller)

    deny = _chain(name="deny", blockers={"c1", "c2"}, bundle_query="deny")
    deny_facts = controller.evaluate_control_facts(
        chain=deny,
        applicability=_applicable(deny),
        runtime_state=state,
    )
    assert deny_facts.recovery_available is True
    assert deny_facts.recovery_exhausted is False

    allow = _chain(name="allow", blockers=set(), bundle_query="allow")
    allow_facts = controller.evaluate_control_facts(
        chain=allow,
        applicability=_applicable(allow),
        runtime_state=state,
    )
    assert allow_facts.recovery_available is False
    assert allow_facts.recovery_exhausted is False

    pending = _pending_chain()
    pending_facts = controller.evaluate_control_facts(
        chain=pending,
        applicability=_applicable(pending),
        runtime_state=state,
    )
    assert pending_facts.recovery_available is False
    assert pending_facts.recovery_exhausted is False

    with pytest.raises(PreWriteRecoveryIntegrityError, match="applicable"):
        controller.evaluate_control_facts(
            chain=deny,
            applicability=BindingApplicabilityResult(
                state=BindingApplicabilityState.NOT_APPLICABLE
            ),
            runtime_state=state,
        )


def test_recovery_eligibility_is_identical_under_strict_and_shadow() -> None:
    """AV-S12-020: Step5 business eligibility has no enforcement-mode input."""

    import inspect

    controller = PreWriteRecoveryController()
    current = _chain(name="mode-independent", blockers={"c1"}, bundle_query="e1")
    applicability = _applicable(current)
    state = _state(controller, rounds=2, no_progress=1)

    first = controller.evaluate_control_facts(
        chain=current,
        applicability=applicability,
        runtime_state=state,
    )
    second = controller.evaluate_control_facts(
        chain=current,
        applicability=applicability,
        runtime_state=state,
    )
    assert first == second
    assert first.recovery_available is True
    assert "mode" not in inspect.signature(
        PreWriteRecoveryController.evaluate_control_facts
    ).parameters
    assert "enforcement" not in inspect.signature(
        PreWriteRecoveryController.evaluate_control_facts
    ).parameters


def test_recovery_lifecycle_limits_lock_to_write_instance() -> None:
    """AV-S12-021/061: persisted lifecycle limits do not hot-rewrite."""

    controller = PreWriteRecoveryController()
    existing = controller.initialize_state(
        writer_subject_ref=SUBJECT,
        write_instance_id=WRITE_INSTANCE_ID,
        recovery_rounds_limit=2,
        no_progress_rounds_limit=1,
    )
    persisted = existing.model_validate(existing.model_dump(mode="json"))

    # A later runtime configuration can only seed a new write-instance lifecycle.
    changed_runtime_defaults = controller.initialize_state(
        writer_subject_ref=SUBJECT,
        write_instance_id="write-instance-new",
        recovery_rounds_limit=5,
        no_progress_rounds_limit=4,
    )
    assert persisted.write_instance_id == WRITE_INSTANCE_ID
    assert persisted.recovery_rounds_limit == 2
    assert persisted.no_progress_rounds_limit == 1
    assert changed_runtime_defaults.write_instance_id == "write-instance-new"
    assert changed_runtime_defaults.recovery_rounds_limit == 5
    assert changed_runtime_defaults.no_progress_rounds_limit == 4


def test_recovery_limits_lock_and_round_reservation_is_replay_stable() -> None:
    controller = PreWriteRecoveryController()
    current = _chain(name="current", blockers={"c1", "c2"}, bundle_query="e1")
    applicability = _applicable(current)
    state = _state(controller, rounds=2, no_progress=2)
    target = controller.project_target(
        chain=current,
        applicability=applicability,
        runtime_state=state,
    )

    first = controller.prepare_round_reservation(
        chain=current,
        applicability=applicability,
        runtime_state=state,
        target=target,
    )
    replay = controller.prepare_round_reservation(
        chain=current,
        applicability=applicability,
        runtime_state=state,
        target=target,
    )
    assert first.occurrence_id == replay.occurrence_id
    assert first.semantic_fingerprint == replay.semantic_fingerprint
    assert first.state_after_reservation.recovery_rounds_used == 1
    assert first.state_after_reservation.recovery_state_revision == 1
    assert first.state_after_reservation.recovery_rounds_limit == 2
    assert first.state_after_reservation.no_progress_rounds_limit == 2

    with pytest.raises(PreWriteRecoveryIntegrityError, match="stale"):
        controller.prepare_round_reservation(
            chain=current,
            applicability=applicability,
            runtime_state=first.state_after_reservation,
            target=target,
        )


def test_stale_or_fabricated_reservation_cannot_commit_round_outcome() -> None:
    controller = PreWriteRecoveryController()
    current = _chain(name="reservation-current", blockers={"c1"}, bundle_query="e1")
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=_applicable(current),
        runtime_state=_state(controller, rounds=2, no_progress=2),
    )

    replaced_current = _chain(
        name="reservation-new-decision",
        blockers={"c1"},
        bundle_query="e1",
        requirements=current.requirements,
    )
    with pytest.raises(PreWriteRecoveryIntegrityError, match="current Decision"):
        controller.complete_technical_failure(
            current_chain=replaced_current,
            applicability=_applicable(replaced_current),
            reservation=reservation,
        )

    fabricated = RecoveryRoundReservation(
        target=reservation.target,
        occurrence_id=reservation.occurrence_id,
        semantic_fingerprint=reservation.semantic_fingerprint,
        state_before=reservation.state_before,
        state_after_reservation=reservation.state_before,
    )
    with pytest.raises(PreWriteRecoveryIntegrityError, match="round-consumption"):
        controller.complete_technical_failure(
            current_chain=current,
            applicability=_applicable(current),
            reservation=fabricated,
        )

    tampered_identity = RecoveryRoundReservation(
        target=reservation.target,
        occurrence_id="pre_write_recovery_occurrence_tampered",
        semantic_fingerprint=reservation.semantic_fingerprint,
        state_before=reservation.state_before,
        state_after_reservation=reservation.state_after_reservation,
    )
    with pytest.raises(PreWriteRecoveryIntegrityError, match="occurrence identity"):
        controller.complete_technical_failure(
            current_chain=current,
            applicability=_applicable(current),
            reservation=tampered_identity,
        )


def test_same_recovery_occurrence_two_workers_has_one_physical_owner(tmp_path) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    controller = PreWriteRecoveryController(execution_store=store)
    current = _chain(name="current", blockers={"c1"}, bundle_query="e1")
    applicability = _applicable(current)
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=applicability,
        runtime_state=_state(controller),
    )

    first = controller.claim_physical_dispatch(reservation, owner_id="worker-1")
    second = controller.claim_physical_dispatch(reservation, owner_id="worker-2")
    assert first.status is RecoveryDispatchStatus.ACQUIRED
    assert second.status is RecoveryDispatchStatus.IN_PROGRESS
    assert reservation.state_after_reservation.recovery_rounds_used == 1

    assert controller.release_physical_dispatch(first, reason="worker restart") is True
    reclaimed = controller.claim_physical_dispatch(reservation, owner_id="worker-2")
    assert reclaimed.status is RecoveryDispatchStatus.ACQUIRED
    assert reclaimed.occurrence_id == first.occurrence_id
    assert reservation.state_after_reservation.recovery_rounds_used == 1


def test_recovery_physical_claim_renewal_prevents_live_owner_reclaim(tmp_path) -> None:
    store = SQLitePreWriteExecutionStore(
        tmp_path / "execution_control.sqlite3",
        recovery_claim_lease_seconds=0.30,
    )
    controller = PreWriteRecoveryController(execution_store=store)
    current = _chain(name="renew", blockers={"c1"}, bundle_query="e1")
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=_applicable(current),
        runtime_state=_state(controller),
    )
    first = controller.claim_physical_dispatch(reservation, owner_id="worker-1")
    assert first.status is RecoveryDispatchStatus.ACQUIRED
    time.sleep(0.20)
    assert controller.renew_physical_dispatch(first) is True
    time.sleep(0.20)
    second = controller.claim_physical_dispatch(reservation, owner_id="worker-2")
    assert second.status is RecoveryDispatchStatus.IN_PROGRESS
    assert reservation.state_after_reservation.recovery_rounds_used == 1


def test_recovery_progress_uses_exact_blocker_sets_and_evidence_identity() -> None:
    controller = PreWriteRecoveryController()
    requirements = _requirements()
    current = _chain(
        name="current",
        blockers={"c1", "c2"},
        bundle_query="e1",
        requirements=requirements,
    )
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=_applicable(current),
        runtime_state=_state(controller, no_progress=2),
    )

    progress = _chain(
        name="progress",
        blockers={"c2"},
        bundle_query="e2",
        requirements=requirements,
    )
    proposal = controller.prepare_candidate_promotion(
        current_chain=current,
        candidate_chain=progress,
        reservation=reservation,
    )
    assert proposal.tentative_outcome is RecoveryRoundOutcome.PROGRESS
    assert proposal.proposed_runtime_state.consecutive_no_progress_rounds == 0

    same_evidence_fewer_blockers = _chain(
        name="same-evidence",
        blockers={"c2"},
        bundle_query="e1",
        requirements=requirements,
    )
    same_evidence = controller.prepare_candidate_promotion(
        current_chain=current,
        candidate_chain=same_evidence_fewer_blockers,
        reservation=reservation,
    )
    assert same_evidence.tentative_outcome is RecoveryRoundOutcome.NO_PROGRESS

    changed_same_blockers = _chain(
        name="same-blockers",
        blockers={"c1", "c2"},
        bundle_query="e-same-set",
        requirements=requirements,
    )
    same_set = controller.prepare_candidate_promotion(
        current_chain=current,
        candidate_chain=changed_same_blockers,
        reservation=reservation,
    )
    assert same_set.tentative_outcome is RecoveryRoundOutcome.NO_PROGRESS

    changed_but_not_subset = _chain(
        name="regression",
        blockers={"c2", "c3"},
        bundle_query="e3",
        requirements=requirements,
    )
    regression = controller.prepare_candidate_promotion(
        current_chain=current,
        candidate_chain=changed_but_not_subset,
        reservation=reservation,
    )
    assert regression.tentative_outcome is RecoveryRoundOutcome.NO_PROGRESS

    different_requirements = _requirements(source_profile_ref="profile-v2")
    incomparable = _chain(
        name="different-requirements",
        blockers={"c2"},
        bundle_query="e4",
        requirements=different_requirements,
    )
    with pytest.raises(PreWriteRecoveryIntegrityError, match="Requirements"):
        controller.prepare_candidate_promotion(
            current_chain=current,
            candidate_chain=incomparable,
            reservation=reservation,
        )


def test_no_progress_candidate_can_promote_then_exhaust_by_exact_threshold() -> None:
    controller = PreWriteRecoveryController()
    requirements = _requirements()
    current = _chain(
        name="no-progress-current",
        blockers={"c1", "c2"},
        bundle_query="e1",
        requirements=requirements,
    )
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=_applicable(current),
        runtime_state=_state(controller, rounds=2, no_progress=1),
    )
    candidate = _chain(
        name="no-progress-candidate",
        blockers={"c2", "c3"},
        bundle_query="e2",
        requirements=requirements,
    )
    proposal = controller.prepare_candidate_promotion(
        current_chain=current,
        candidate_chain=candidate,
        reservation=reservation,
    )
    assert proposal.tentative_outcome is RecoveryRoundOutcome.NO_PROGRESS
    committed = controller.finalize_candidate_promotion(
        proposal=proposal,
        transitioned_applicability=_applicable(candidate),
    )
    assert committed.committed is True
    assert committed.active_chain == candidate
    assert committed.recovery_control_facts is not None
    assert committed.recovery_control_facts.recovery_available is False
    assert committed.recovery_control_facts.recovery_exhausted is True
    assert (
        committed.recovery_control_facts.exhaustion_reason
        is RecoveryExhaustionReason.NO_PROGRESS_EXHAUSTED
    )


def test_recovery_success_and_failed_promotion_commit_are_coherent() -> None:
    controller = PreWriteRecoveryController()
    requirements = _requirements()
    current = _chain(
        name="current",
        blockers={"c1"},
        bundle_query="e1",
        requirements=requirements,
    )
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=_applicable(current),
        runtime_state=_state(controller, rounds=2, no_progress=2),
    )
    candidate = _chain(
        name="allow",
        blockers=set(),
        bundle_query="e2",
        requirements=requirements,
    )
    proposal = controller.prepare_candidate_promotion(
        current_chain=current,
        candidate_chain=candidate,
        reservation=reservation,
    )
    assert proposal.tentative_outcome is RecoveryRoundOutcome.SUCCESS

    tampered = RecoveryPromotionProposal(
        current_chain=proposal.current_chain,
        candidate_chain=proposal.candidate_chain,
        reservation=proposal.reservation,
        tentative_outcome=proposal.tentative_outcome,
        proposed_runtime_state=proposal.proposed_runtime_state.model_copy(
            update={
                "recovery_state_revision": (
                    proposal.proposed_runtime_state.recovery_state_revision + 1
                )
            }
        ),
    )
    with pytest.raises(PreWriteRecoveryIntegrityError, match="proposal"):
        controller.finalize_candidate_promotion(
            proposal=tampered,
            transitioned_applicability=_applicable(candidate),
        )

    committed = controller.finalize_candidate_promotion(
        proposal=proposal,
        transitioned_applicability=_applicable(candidate),
    )
    assert committed.committed is True
    assert committed.active_chain == candidate
    assert committed.round_outcome is RecoveryRoundOutcome.SUCCESS
    assert committed.runtime_state.last_round_outcome is RecoveryRoundOutcome.SUCCESS
    assert committed.recovery_control_facts is not None
    assert committed.recovery_control_facts.recovery_available is False
    assert committed.recovery_control_facts.recovery_exhausted is False

    rejected = controller.finalize_candidate_promotion(
        proposal=proposal,
        transitioned_applicability=_applicable(current),
    )
    assert rejected.committed is False
    assert rejected.active_chain == current
    assert rejected.round_outcome is RecoveryRoundOutcome.TECHNICAL_FAILURE
    assert rejected.runtime_state.recovery_rounds_used == 1
    assert rejected.runtime_state.consecutive_no_progress_rounds == 0
    assert rejected.recovery_control_facts is None


def test_recovery_technical_failure_preserves_semantic_no_progress_history() -> None:
    controller = PreWriteRecoveryController()
    current = _chain(name="current", blockers={"c1"}, bundle_query="e1")
    applicability = _applicable(current)
    existing_history = _state(controller, rounds=3, no_progress=2).model_copy(
        update={
            "recovery_state_revision": 2,
            "recovery_rounds_used": 1,
            "consecutive_no_progress_rounds": 1,
            "last_round_outcome": RecoveryRoundOutcome.NO_PROGRESS,
        }
    )
    reservation = controller.prepare_round_reservation(
        chain=current,
        applicability=applicability,
        runtime_state=existing_history,
    )
    failed = controller.complete_technical_failure(
        current_chain=current,
        applicability=applicability,
        reservation=reservation,
    )
    assert failed.runtime_state.last_round_outcome is RecoveryRoundOutcome.TECHNICAL_FAILURE
    assert failed.runtime_state.recovery_rounds_used == 2
    assert failed.runtime_state.consecutive_no_progress_rounds == 1
    assert failed.recovery_control_facts.recovery_available is True
    assert failed.recovery_control_facts.recovery_exhausted is False

    final_round = controller.prepare_round_reservation(
        chain=current,
        applicability=applicability,
        runtime_state=_state(controller, rounds=1, no_progress=2),
    )
    exhausted = controller.complete_technical_failure(
        current_chain=current,
        applicability=applicability,
        reservation=final_round,
    )
    assert exhausted.recovery_control_facts.recovery_available is False
    assert exhausted.recovery_control_facts.recovery_exhausted is True
    assert (
        exhausted.recovery_control_facts.exhaustion_reason
        is RecoveryExhaustionReason.ROUND_BUDGET_EXHAUSTED
    )


def test_recovery_query_renderer_consumes_typed_target_only() -> None:
    controller = PreWriteRecoveryController()
    current = _chain(name="current", blockers={"c1", "c2"}, bundle_query="e1")
    state = _state(controller)
    target = controller.project_target(
        chain=current,
        applicability=_applicable(current),
        runtime_state=state,
    )
    query = SectionRetrievalQueryBuilder.build_recovery_target_query(
        target,
        current.requirements,
        section_title="安全设计",
    )
    assert "c1" in query and "必须说明建设范围" in query
    assert "c2" in query and "必须给出资源容量依据" in query
    assert "c3" not in query
    assert "身份认证" not in query
    assert "不得扩展到未列出的要求或判据" in query

    stale_requirements = _requirements(source_profile_ref="profile-v2")
    with pytest.raises(ValueError, match="stale"):
        SectionRetrievalQueryBuilder.build_recovery_target_query(
            target,
            stale_requirements,
        )
