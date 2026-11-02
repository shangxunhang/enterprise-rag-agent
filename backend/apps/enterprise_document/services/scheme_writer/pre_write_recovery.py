"""Gate V2 Target pre-write Recovery control semantics.

This module owns Step-5 business control only.  It derives typed Recovery targets,
locks lifecycle limits/counters, classifies deterministic semantic progress, and
forms coherent promotion results.  It deliberately does not publish checkpoint
state or choose workflow routes; P4 orchestration owns the synchronized publication
boundary while ``PreWriteExecutionStore`` owns only physical dispatch dedupe.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceSupportState,
    EvidenceRequirementsSnapshotSchema,
    WriterAdmissionAssessmentState,
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
    WriterAdmissionFactAssessmentRefSchema,
    WriterAdmissionFactAssessmentSchema,
    WriterAdmissionOutcome,
    WriterAdmissionResolutionState,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterGateBindingSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    RecoveryControlFactsSchema,
    RecoveryExhaustionReason,
    RecoveryRoundOutcome,
    RecoveryRuntimeStateSchema,
    RecoveryTargetProjection,
)
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import (
    PreWriteExecutionStore,
    RecoveryDispatchClaim,
)
from schemas.rag import RAGEvidenceContractSchema


class PreWriteRecoveryIntegrityError(ValueError):
    """Current/candidate Recovery control lineage is stale or internally incoherent."""


@dataclass(frozen=True)
class RecoveryChainSnapshot:
    """One coherent immutable Evidence/Assessment/Decision/Binding lineage."""

    evidence_bundle: RAGEvidenceContractSchema
    requirements: EvidenceRequirementsSnapshotSchema
    assessment: WriterAdmissionFactAssessmentSchema
    decision: WriterAdmissionDecisionSchema
    binding: WriterGateBindingSchema


@dataclass(frozen=True)
class RecoveryRoundReservation:
    """Logical Step-5 round reservation proposed for durable P4 checkpoint publication."""

    target: RecoveryTargetProjection
    occurrence_id: str
    semantic_fingerprint: str
    state_before: RecoveryRuntimeStateSchema
    state_after_reservation: RecoveryRuntimeStateSchema


@dataclass(frozen=True)
class RecoveryPromotionProposal:
    """Complete candidate transition before the current-applicability commit check."""

    current_chain: RecoveryChainSnapshot
    candidate_chain: RecoveryChainSnapshot
    reservation: RecoveryRoundReservation
    tentative_outcome: RecoveryRoundOutcome
    proposed_runtime_state: RecoveryRuntimeStateSchema


@dataclass(frozen=True)
class RecoveryPromotionResult:
    """Atomic semantic publication payload consumed by later P4 orchestration."""

    committed: bool
    active_chain: RecoveryChainSnapshot
    runtime_state: RecoveryRuntimeStateSchema
    recovery_control_facts: RecoveryControlFactsSchema | None
    round_outcome: RecoveryRoundOutcome


@dataclass(frozen=True)
class RecoveryTechnicalFailureResult:
    """Committed technical round outcome while the previous active chain remains current."""

    active_chain: RecoveryChainSnapshot
    runtime_state: RecoveryRuntimeStateSchema
    recovery_control_facts: RecoveryControlFactsSchema


def _decision_ref(decision: WriterAdmissionDecisionSchema) -> WriterAdmissionDecisionRefSchema:
    return WriterAdmissionDecisionRefSchema(
        decision_id=decision.decision_id,
        decision_schema_version=decision.schema_version,
        decision_contract_version=decision.contract_version,
    )


def _binding_ref(binding: WriterGateBindingSchema) -> WriterGateBindingRefSchema:
    return WriterGateBindingRefSchema(
        binding_id=binding.binding_id,
        binding_schema_version=binding.schema_version,
        binding_contract_version=binding.contract_version,
    )


def _assessment_ref(
    assessment: WriterAdmissionFactAssessmentSchema,
) -> WriterAdmissionFactAssessmentRefSchema:
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


def _blocking_criterion_ids(
    assessment: WriterAdmissionFactAssessmentSchema,
) -> tuple[str, ...]:
    conflicted = {item.criterion_id for item in assessment.conflict_facts}
    return tuple(
        fact.criterion_id
        for fact in assessment.criterion_facts
        if (
            fact.support_state is not CriterionEvidenceSupportState.SUPPORTED
            or fact.criterion_id in conflicted
        )
    )


def _validate_chain_intrinsic(
    chain: RecoveryChainSnapshot,
    *,
    writer_subject_ref: WriterSubjectRefSchema,
    write_instance_id: str,
) -> None:
    if chain.binding.writer_subject_ref != writer_subject_ref:
        raise PreWriteRecoveryIntegrityError("Recovery chain WriterSubjectRef mismatch")
    if chain.binding.write_instance_id != write_instance_id:
        raise PreWriteRecoveryIntegrityError("Recovery chain write_instance_id mismatch")
    if chain.binding.decision_ref != _decision_ref(chain.decision):
        raise PreWriteRecoveryIntegrityError("Recovery Binding does not resolve Decision")
    if chain.decision.fact_assessment_ref != _assessment_ref(chain.assessment):
        raise PreWriteRecoveryIntegrityError(
            "Recovery Decision does not resolve its FactAssessment"
        )
    if chain.assessment.requirements_sha256 != chain.requirements.requirements_sha256:
        raise PreWriteRecoveryIntegrityError("Recovery Requirements identity mismatch")
    if chain.assessment.evidence_bundle_sha256 != evidence_bundle_sha256(
        chain.evidence_bundle
    ):
        raise PreWriteRecoveryIntegrityError("Recovery active Evidence identity mismatch")
    if (
        chain.decision.resolution_state is WriterAdmissionResolutionState.RESOLVED
        and chain.assessment.assessment_state is not WriterAdmissionAssessmentState.COMPLETED
    ):
        raise PreWriteRecoveryIntegrityError(
            "resolved Recovery Decision requires completed FactAssessment"
        )


def _validate_current_applicability(
    *,
    chain: RecoveryChainSnapshot,
    applicability: BindingApplicabilityResult,
) -> None:
    if applicability.state is not BindingApplicabilityState.APPLICABLE:
        raise PreWriteRecoveryIntegrityError(
            "normal Recovery control requires current applicable Binding"
        )
    if applicability.decision != chain.decision or applicability.binding != chain.binding:
        raise PreWriteRecoveryIntegrityError(
            "BindingApplicability resolved a different Decision/Binding"
        )


class RecoveryTargetProjector:
    """Project current canonical DENY blockers into the typed Step-5 retrieval target."""

    @staticmethod
    def project(
        *,
        chain: RecoveryChainSnapshot,
        applicability: BindingApplicabilityResult,
        runtime_state: RecoveryRuntimeStateSchema,
    ) -> RecoveryTargetProjection:
        _validate_chain_intrinsic(
            chain,
            writer_subject_ref=runtime_state.writer_subject_ref,
            write_instance_id=runtime_state.write_instance_id,
        )
        _validate_current_applicability(chain=chain, applicability=applicability)
        decision = chain.decision
        assessment = chain.assessment
        if not (
            decision.resolution_state is WriterAdmissionResolutionState.RESOLVED
            and decision.admission_outcome is WriterAdmissionOutcome.DENY
        ):
            raise PreWriteRecoveryIntegrityError(
                "Recovery target exists only for current RESOLVED/DENY"
            )
        if assessment.assessment_state is not WriterAdmissionAssessmentState.COMPLETED:
            raise PreWriteRecoveryIntegrityError(
                "Recovery target requires completed FactAssessment"
            )

        blocking_requirement_ids = tuple(decision.blocking_requirement_ids)
        known_requirement_ids = {item.requirement_id for item in assessment.requirement_facts}
        if not set(blocking_requirement_ids) <= known_requirement_ids:
            raise PreWriteRecoveryIntegrityError(
                "Decision blocker requirements do not resolve in FactAssessment"
            )

        decision_conflict_refs = set(decision.conflict_refs)
        conflict_by_id = {item.conflict_id: item for item in assessment.conflict_facts}
        if not decision_conflict_refs <= set(conflict_by_id):
            raise PreWriteRecoveryIntegrityError(
                "Decision conflict refs do not resolve in FactAssessment"
            )
        unresolved_conflict_criterion_ids = tuple(
            dict.fromkeys(
                conflict_by_id[conflict_id].criterion_id
                for conflict_id in decision.conflict_refs
            )
        )

        blocking_requirements = set(blocking_requirement_ids)
        blocking_criterion_ids = tuple(
            fact.criterion_id
            for fact in assessment.criterion_facts
            if fact.requirement_id in blocking_requirements
            and (
                fact.support_state is not CriterionEvidenceSupportState.SUPPORTED
                or bool(fact.conflict_refs)
            )
        )
        indeterminate_requirements = set(decision.indeterminate_requirement_ids)
        indeterminate_criterion_ids = tuple(
            fact.criterion_id
            for fact in assessment.criterion_facts
            if fact.requirement_id in indeterminate_requirements
            and fact.support_state is CriterionEvidenceSupportState.INDETERMINATE
        )

        return RecoveryTargetProjection(
            writer_subject_ref=runtime_state.writer_subject_ref,
            write_instance_id=runtime_state.write_instance_id,
            decision_ref=_decision_ref(decision),
            binding_ref=_binding_ref(chain.binding),
            fact_assessment_ref=_assessment_ref(assessment),
            requirements_sha256=assessment.requirements_sha256,
            expected_recovery_state_revision=runtime_state.recovery_state_revision,
            blocking_requirement_ids=blocking_requirement_ids,
            blocking_criterion_ids=blocking_criterion_ids,
            unresolved_conflict_criterion_ids=unresolved_conflict_criterion_ids,
            indeterminate_requirement_ids=tuple(decision.indeterminate_requirement_ids),
            indeterminate_criterion_ids=indeterminate_criterion_ids,
        )


class PreWriteRecoveryController:
    """Sole Step-5 Recovery eligibility/progress/exhaustion/promotion controller."""

    def __init__(self, *, execution_store: PreWriteExecutionStore | None = None) -> None:
        self._execution_store = execution_store
        self._target_projector = RecoveryTargetProjector()

    @staticmethod
    def initialize_state(
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        recovery_rounds_limit: int,
        no_progress_rounds_limit: int,
    ) -> RecoveryRuntimeStateSchema:
        return RecoveryRuntimeStateSchema(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            recovery_state_revision=0,
            recovery_rounds_used=0,
            recovery_rounds_limit=recovery_rounds_limit,
            consecutive_no_progress_rounds=0,
            no_progress_rounds_limit=no_progress_rounds_limit,
            last_round_outcome=RecoveryRoundOutcome.NOT_STARTED,
        )

    @staticmethod
    def _validate_current(
        *,
        chain: RecoveryChainSnapshot,
        applicability: BindingApplicabilityResult,
        runtime_state: RecoveryRuntimeStateSchema,
    ) -> None:
        _validate_chain_intrinsic(
            chain,
            writer_subject_ref=runtime_state.writer_subject_ref,
            write_instance_id=runtime_state.write_instance_id,
        )
        _validate_current_applicability(chain=chain, applicability=applicability)

    @staticmethod
    def _facts(
        *,
        chain: RecoveryChainSnapshot,
        runtime_state: RecoveryRuntimeStateSchema,
        recovery_available: bool,
        recovery_exhausted: bool,
        exhaustion_reason: RecoveryExhaustionReason | None,
    ) -> RecoveryControlFactsSchema:
        return RecoveryControlFactsSchema(
            writer_subject_ref=runtime_state.writer_subject_ref,
            write_instance_id=runtime_state.write_instance_id,
            decision_ref=_decision_ref(chain.decision),
            binding_ref=_binding_ref(chain.binding),
            recovery_state_revision=runtime_state.recovery_state_revision,
            recovery_available=recovery_available,
            recovery_exhausted=recovery_exhausted,
            recovery_rounds_used=runtime_state.recovery_rounds_used,
            recovery_rounds_limit=runtime_state.recovery_rounds_limit,
            consecutive_no_progress_rounds=runtime_state.consecutive_no_progress_rounds,
            no_progress_rounds_limit=runtime_state.no_progress_rounds_limit,
            last_round_outcome=runtime_state.last_round_outcome,
            exhaustion_reason=exhaustion_reason,
        )

    def evaluate_control_facts(
        self,
        *,
        chain: RecoveryChainSnapshot,
        applicability: BindingApplicabilityResult,
        runtime_state: RecoveryRuntimeStateSchema,
        dispatch_technically_blocked: bool = False,
    ) -> RecoveryControlFactsSchema:
        self._validate_current(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )
        decision = chain.decision
        if decision.resolution_state is WriterAdmissionResolutionState.UNRESOLVED:
            return self._facts(
                chain=chain,
                runtime_state=runtime_state,
                recovery_available=False,
                recovery_exhausted=False,
                exhaustion_reason=None,
            )
        if decision.admission_outcome is WriterAdmissionOutcome.ALLOW:
            return self._facts(
                chain=chain,
                runtime_state=runtime_state,
                recovery_available=False,
                recovery_exhausted=False,
                exhaustion_reason=None,
            )
        if decision.admission_outcome is not WriterAdmissionOutcome.DENY:
            raise PreWriteRecoveryIntegrityError("invalid resolved Admission outcome")

        if runtime_state.recovery_rounds_used >= runtime_state.recovery_rounds_limit:
            return self._facts(
                chain=chain,
                runtime_state=runtime_state,
                recovery_available=False,
                recovery_exhausted=True,
                exhaustion_reason=RecoveryExhaustionReason.ROUND_BUDGET_EXHAUSTED,
            )
        if (
            runtime_state.consecutive_no_progress_rounds
            >= runtime_state.no_progress_rounds_limit
        ):
            return self._facts(
                chain=chain,
                runtime_state=runtime_state,
                recovery_available=False,
                recovery_exhausted=True,
                exhaustion_reason=RecoveryExhaustionReason.NO_PROGRESS_EXHAUSTED,
            )
        if dispatch_technically_blocked:
            return self._facts(
                chain=chain,
                runtime_state=runtime_state,
                recovery_available=False,
                recovery_exhausted=False,
                exhaustion_reason=None,
            )
        return self._facts(
            chain=chain,
            runtime_state=runtime_state,
            recovery_available=True,
            recovery_exhausted=False,
            exhaustion_reason=None,
        )

    def project_target(
        self,
        *,
        chain: RecoveryChainSnapshot,
        applicability: BindingApplicabilityResult,
        runtime_state: RecoveryRuntimeStateSchema,
    ) -> RecoveryTargetProjection:
        return self._target_projector.project(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )

    @staticmethod
    def _reservation_identity(
        *,
        target: RecoveryTargetProjection,
        state_before: RecoveryRuntimeStateSchema,
    ) -> tuple[str, str]:
        round_number = state_before.recovery_rounds_used + 1
        semantic_preimage = {
            "writer_subject_ref": state_before.writer_subject_ref.model_dump(
                mode="json", round_trip=True
            ),
            "write_instance_id": state_before.write_instance_id,
            "round_number": round_number,
            "expected_recovery_state_revision": state_before.recovery_state_revision,
            "target": target.model_dump(mode="json", round_trip=True),
        }
        semantic_fingerprint = canonical_sha256(semantic_preimage)
        occurrence_id = f"pre_write_recovery_occurrence_{semantic_fingerprint}"
        return occurrence_id, semantic_fingerprint

    def prepare_round_reservation(
        self,
        *,
        chain: RecoveryChainSnapshot,
        applicability: BindingApplicabilityResult,
        runtime_state: RecoveryRuntimeStateSchema,
        target: RecoveryTargetProjection | None = None,
    ) -> RecoveryRoundReservation:
        facts = self.evaluate_control_facts(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )
        if not facts.recovery_available or facts.recovery_exhausted:
            raise PreWriteRecoveryIntegrityError("Recovery round is not currently available")
        current_target = target or self.project_target(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )
        expected_target = self.project_target(
            chain=chain,
            applicability=applicability,
            runtime_state=runtime_state,
        )
        if current_target != expected_target:
            raise PreWriteRecoveryIntegrityError(
                "stale or non-canonical RecoveryTargetProjection"
            )
        if (
            current_target.expected_recovery_state_revision
            != runtime_state.recovery_state_revision
        ):
            raise PreWriteRecoveryIntegrityError("stale Recovery state revision")

        round_number = runtime_state.recovery_rounds_used + 1
        occurrence_id, semantic_fingerprint = self._reservation_identity(
            target=current_target,
            state_before=runtime_state,
        )
        state_after_reservation = runtime_state.model_copy(
            update={
                "recovery_rounds_used": round_number,
                "recovery_state_revision": runtime_state.recovery_state_revision + 1,
            },
            deep=True,
        )
        return RecoveryRoundReservation(
            target=current_target,
            occurrence_id=occurrence_id,
            semantic_fingerprint=semantic_fingerprint,
            state_before=runtime_state,
            state_after_reservation=state_after_reservation,
        )

    def claim_physical_dispatch(
        self,
        reservation: RecoveryRoundReservation,
        *,
        owner_id: str,
    ) -> RecoveryDispatchClaim:
        if self._execution_store is None:
            raise RuntimeError("Recovery physical dispatch requires execution_store")
        return self._execution_store.claim_recovery_dispatch(
            reservation.occurrence_id,
            semantic_fingerprint=reservation.semantic_fingerprint,
            owner_id=owner_id,
        )

    def renew_physical_dispatch(self, claim: RecoveryDispatchClaim) -> bool:
        if self._execution_store is None:
            raise RuntimeError("Recovery physical dispatch requires execution_store")
        return self._execution_store.renew_recovery_claim(claim)

    def complete_physical_dispatch(
        self,
        claim: RecoveryDispatchClaim,
        *,
        result_ref: str | None = None,
    ) -> bool:
        if self._execution_store is None:
            raise RuntimeError("Recovery physical dispatch requires execution_store")
        return self._execution_store.complete_recovery_dispatch(
            claim,
            result_ref=result_ref,
        )

    def release_physical_dispatch(
        self,
        claim: RecoveryDispatchClaim,
        *,
        reason: str,
    ) -> bool:
        if self._execution_store is None:
            raise RuntimeError("Recovery physical dispatch requires execution_store")
        return self._execution_store.release_recovery_dispatch(claim, reason=reason)

    def validate_round_reservation(
        self,
        *,
        source_chain: RecoveryChainSnapshot,
        reservation: RecoveryRoundReservation,
    ) -> None:
        """Revalidate owner-produced lineage without any execution/state mutation."""

        self._validate_reservation_lineage(
            current_chain=source_chain, reservation=reservation,
        )

    @staticmethod
    def _validate_reservation_lineage(
        *,
        current_chain: RecoveryChainSnapshot,
        reservation: RecoveryRoundReservation,
    ) -> None:
        state_before = reservation.state_before
        _validate_chain_intrinsic(
            current_chain,
            writer_subject_ref=state_before.writer_subject_ref,
            write_instance_id=state_before.write_instance_id,
        )
        if not (
            current_chain.decision.resolution_state
            is WriterAdmissionResolutionState.RESOLVED
            and current_chain.decision.admission_outcome is WriterAdmissionOutcome.DENY
            and current_chain.assessment.assessment_state
            is WriterAdmissionAssessmentState.COMPLETED
        ):
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation requires current completed RESOLVED/DENY lineage"
            )
        target = reservation.target
        if target.writer_subject_ref != state_before.writer_subject_ref:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation target subject is stale"
            )
        if target.write_instance_id != state_before.write_instance_id:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation target write instance is stale"
            )
        if target.decision_ref != _decision_ref(current_chain.decision):
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation no longer belongs to current Decision"
            )
        if target.binding_ref != _binding_ref(current_chain.binding):
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation no longer belongs to current Binding"
            )
        if target.fact_assessment_ref != _assessment_ref(current_chain.assessment):
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation no longer belongs to current FactAssessment"
            )
        if target.requirements_sha256 != current_chain.requirements.requirements_sha256:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation Requirements lineage is stale"
            )
        if target.expected_recovery_state_revision != state_before.recovery_state_revision:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation expected state revision is stale"
            )
        expected_occurrence_id, expected_fingerprint = (
            PreWriteRecoveryController._reservation_identity(
                target=target,
                state_before=state_before,
            )
        )
        if reservation.semantic_fingerprint != expected_fingerprint:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation semantic fingerprint is invalid"
            )
        if reservation.occurrence_id != expected_occurrence_id:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation occurrence identity is invalid"
            )
        expected_after = state_before.model_copy(
            update={
                "recovery_rounds_used": state_before.recovery_rounds_used + 1,
                "recovery_state_revision": state_before.recovery_state_revision + 1,
            },
            deep=True,
        )
        if reservation.state_after_reservation != expected_after:
            raise PreWriteRecoveryIntegrityError(
                "Recovery reservation contains an invalid round-consumption transition"
            )

    @staticmethod
    def _technical_failure_state(
        reservation: RecoveryRoundReservation,
    ) -> RecoveryRuntimeStateSchema:
        state = reservation.state_after_reservation
        return state.model_copy(
            update={
                "recovery_state_revision": state.recovery_state_revision + 1,
                "last_round_outcome": RecoveryRoundOutcome.TECHNICAL_FAILURE,
            },
            deep=True,
        )

    def complete_technical_failure(
        self,
        *,
        current_chain: RecoveryChainSnapshot,
        applicability: BindingApplicabilityResult,
        reservation: RecoveryRoundReservation,
        persistent_technical_blocker: bool = False,
    ) -> RecoveryTechnicalFailureResult:
        self._validate_reservation_lineage(
            current_chain=current_chain,
            reservation=reservation,
        )
        state = self._technical_failure_state(reservation)
        facts = self.evaluate_control_facts(
            chain=current_chain,
            applicability=applicability,
            runtime_state=state,
            dispatch_technically_blocked=persistent_technical_blocker,
        )
        return RecoveryTechnicalFailureResult(
            active_chain=current_chain,
            runtime_state=state,
            recovery_control_facts=facts,
        )

    def prepare_candidate_promotion(
        self,
        *,
        current_chain: RecoveryChainSnapshot,
        candidate_chain: RecoveryChainSnapshot,
        reservation: RecoveryRoundReservation,
    ) -> RecoveryPromotionProposal:
        self._validate_reservation_lineage(
            current_chain=current_chain,
            reservation=reservation,
        )
        _validate_chain_intrinsic(
            candidate_chain,
            writer_subject_ref=reservation.state_before.writer_subject_ref,
            write_instance_id=reservation.state_before.write_instance_id,
        )
        if (
            current_chain.requirements.requirements_sha256
            != candidate_chain.requirements.requirements_sha256
        ):
            raise PreWriteRecoveryIntegrityError(
                "Recovery progress comparison requires the same Requirements Snapshot"
            )
        if candidate_chain.assessment.assessment_state is not WriterAdmissionAssessmentState.COMPLETED:
            raise PreWriteRecoveryIntegrityError(
                "candidate promotion requires completed FactAssessment"
            )
        if candidate_chain.decision.resolution_state is not WriterAdmissionResolutionState.RESOLVED:
            raise PreWriteRecoveryIntegrityError(
                "candidate promotion requires resolved WriterAdmissionDecision"
            )

        if candidate_chain.decision.admission_outcome is WriterAdmissionOutcome.ALLOW:
            outcome = RecoveryRoundOutcome.SUCCESS
        elif candidate_chain.decision.admission_outcome is WriterAdmissionOutcome.DENY:
            current_blockers = set(_blocking_criterion_ids(current_chain.assessment))
            candidate_blockers = set(_blocking_criterion_ids(candidate_chain.assessment))
            evidence_changed = evidence_bundle_sha256(
                candidate_chain.evidence_bundle
            ) != evidence_bundle_sha256(current_chain.evidence_bundle)
            outcome = (
                RecoveryRoundOutcome.PROGRESS
                if evidence_changed and candidate_blockers < current_blockers
                else RecoveryRoundOutcome.NO_PROGRESS
            )
        else:
            raise PreWriteRecoveryIntegrityError("candidate has invalid Admission outcome")

        state = reservation.state_after_reservation
        if outcome in {RecoveryRoundOutcome.SUCCESS, RecoveryRoundOutcome.PROGRESS}:
            no_progress = 0
        else:
            no_progress = state.consecutive_no_progress_rounds + 1
        proposed_runtime_state = state.model_copy(
            update={
                "recovery_state_revision": state.recovery_state_revision + 1,
                "consecutive_no_progress_rounds": no_progress,
                "last_round_outcome": outcome,
            },
            deep=True,
        )
        return RecoveryPromotionProposal(
            current_chain=current_chain,
            candidate_chain=candidate_chain,
            reservation=reservation,
            tentative_outcome=outcome,
            proposed_runtime_state=proposed_runtime_state,
        )

    def finalize_candidate_promotion(
        self,
        *,
        proposal: RecoveryPromotionProposal,
        transitioned_applicability: BindingApplicabilityResult,
    ) -> RecoveryPromotionResult:
        expected_proposal = self.prepare_candidate_promotion(
            current_chain=proposal.current_chain,
            candidate_chain=proposal.candidate_chain,
            reservation=proposal.reservation,
        )
        if proposal != expected_proposal:
            raise PreWriteRecoveryIntegrityError(
                "Recovery promotion proposal is stale or internally inconsistent"
            )
        candidate = proposal.candidate_chain
        commit_valid = (
            transitioned_applicability.state is BindingApplicabilityState.APPLICABLE
            and transitioned_applicability.decision == candidate.decision
            and transitioned_applicability.binding == candidate.binding
        )
        if not commit_valid:
            return RecoveryPromotionResult(
                committed=False,
                active_chain=proposal.current_chain,
                runtime_state=self._technical_failure_state(proposal.reservation),
                recovery_control_facts=None,
                round_outcome=RecoveryRoundOutcome.TECHNICAL_FAILURE,
            )

        facts = self.evaluate_control_facts(
            chain=candidate,
            applicability=transitioned_applicability,
            runtime_state=proposal.proposed_runtime_state,
        )
        return RecoveryPromotionResult(
            committed=True,
            active_chain=candidate,
            runtime_state=proposal.proposed_runtime_state,
            recovery_control_facts=facts,
            round_outcome=proposal.tentative_outcome,
        )


__all__ = [
    "PreWriteRecoveryController",
    "PreWriteRecoveryIntegrityError",
    "RecoveryChainSnapshot",
    "RecoveryPromotionProposal",
    "RecoveryPromotionResult",
    "RecoveryRoundReservation",
    "RecoveryTargetProjector",
    "RecoveryTechnicalFailureResult",
]
