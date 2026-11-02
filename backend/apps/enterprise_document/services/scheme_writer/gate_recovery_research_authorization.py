"""Owner-context Gate Research authorization; physical persistence is evidence only."""

from __future__ import annotations

from dataclasses import dataclass

from apps.enterprise_document.schemas.agent_rag_v1.research import (
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import PreWriteRoute
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
    TargetWorkflowRuntime,
)
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
    PreWriteRecoveryIntegrityError,
    RecoveryChainSnapshot,
    RecoveryRoundReservation,
)
from core.runtime.pre_write_execution_store import PreWriteExecutionStore
from core.runtime.pre_write_route_decision_store import PreWriteRouteDecisionStore


@dataclass(frozen=True)
class GateRecoveryResearchAuthorizationContext:
    reservation: RecoveryRoundReservation
    source_chain: RecoveryChainSnapshot


class GateRecoveryResearchAuthorizationValidator:
    def __init__(
        self,
        *,
        recovery_controller: PreWriteRecoveryController,
        route_decision_store: PreWriteRouteDecisionStore,
        execution_store: PreWriteExecutionStore,
    ) -> None:
        self.recovery_controller = recovery_controller
        self.route_decision_store = route_decision_store
        self.execution_store = execution_store

    def validate(
        self,
        binding: ResearchRoundAuthorizationBindingSchema,
        round_ref: ResearchRoundRefSchema,
        context: GateRecoveryResearchAuthorizationContext,
    ) -> None:
        # Reparse serialized fields, including instances made by model_copy/construct.
        binding = ResearchRoundAuthorizationBindingSchema.model_validate(
            binding.model_dump(mode="python", round_trip=True)
        )
        round_ref = ResearchRoundRefSchema.model_validate(
            round_ref.model_dump(mode="python", round_trip=True)
        )
        if binding.authorization_kind != "GATE_RECOVERY_RESEARCH":
            raise PreWriteRecoveryIntegrityError("Gate validator requires GATE_RECOVERY_RESEARCH")
        if type(context) is not GateRecoveryResearchAuthorizationContext:
            raise PreWriteRecoveryIntegrityError("Gate Research requires exact owner context")
        if (type(context.reservation) is not RecoveryRoundReservation
                or type(context.source_chain) is not RecoveryChainSnapshot):
            raise PreWriteRecoveryIntegrityError("Gate Research context has invalid owner values")
        route = self.route_decision_store.resolve_exact(binding.route_decision_ref)
        if route.selected_route is not PreWriteRoute.RECOVERY:
            raise PreWriteRecoveryIntegrityError("Gate Research requires exact RECOVERY route")
        if (route.writer_subject_ref.run_id != round_ref.run_id
                or route.writer_subject_ref.section_id != round_ref.section_id
                or route.write_instance_id != round_ref.attempt_id
                or round_ref.attempt_scope_ref != f"section:{round_ref.section_id}"):
            raise PreWriteRecoveryIntegrityError("Gate Research route/Attempt identity mismatch")

        reservation = context.reservation
        self.recovery_controller.validate_round_reservation(
            source_chain=context.source_chain, reservation=reservation,
        )
        # Bind the exact resolved route to the owner proof, not just its revision.
        if (route.writer_subject_ref != reservation.state_before.writer_subject_ref
                or route.write_instance_id != reservation.state_before.write_instance_id
                or route.decision_ref != reservation.target.decision_ref
                or route.binding_ref != reservation.target.binding_ref):
            raise PreWriteRecoveryIntegrityError("Gate Research route/reservation lineage mismatch")
        revision = route.recovery_control_ref.recovery_state_revision
        if (revision != reservation.state_before.recovery_state_revision
                or binding.gate_recovery_state_revision
                != reservation.state_after_reservation.recovery_state_revision
                or binding.gate_recovery_state_revision != revision + 1):
            raise PreWriteRecoveryIntegrityError("Gate Research reservation/route revision mismatch")
        if (binding.authorization_ref != reservation.occurrence_id
                or binding.gate_recovery_dispatch_occurrence_ref != reservation.occurrence_id):
            raise PreWriteRecoveryIntegrityError("Gate Research reservation occurrence mismatch")
        physical = self.execution_store.resolve_recovery_dispatch(reservation.occurrence_id)
        if physical is None:
            raise PreWriteRecoveryIntegrityError("Gate Research physical occurrence missing")
        if (physical.occurrence_id != reservation.occurrence_id
                or physical.semantic_fingerprint != reservation.semantic_fingerprint):
            raise PreWriteRecoveryIntegrityError("Gate Research physical fingerprint/identity mismatch")
        # Lease/token/status ownership is enforced by the execution owner, not here.


class ResearchAuthorizationDispatcher:
    """One injected seam; delegates without owning either authorization policy."""

    def __init__(
        self,
        *,
        workflow_runtime: TargetWorkflowRuntime,
        recovery_validator: GateRecoveryResearchAuthorizationValidator,
    ) -> None:
        self.workflow_runtime = workflow_runtime
        self.recovery_validator = recovery_validator

    def __call__(
        self,
        binding: ResearchRoundAuthorizationBindingSchema,
        round_ref: ResearchRoundRefSchema,
        authorization_context: object | None = None,
    ) -> None:
        if binding.authorization_kind == "WORKFLOW_RESEARCH":
            if authorization_context is not None:
                raise PreWriteRecoveryIntegrityError("WORKFLOW_RESEARCH forbids Gate context")
            self.workflow_runtime.validate_research_authorization(binding, round_ref)
        elif binding.authorization_kind == "GATE_RECOVERY_RESEARCH":
            self.recovery_validator.validate(binding, round_ref, authorization_context)
        else:
            raise PreWriteRecoveryIntegrityError("unknown Research authorization kind")
