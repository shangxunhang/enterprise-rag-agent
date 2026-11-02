"""Deterministic Gate V2 Target pre-write Router.

The Router consumes already-produced Step-2/3/4/5 control facts and is the sole
owner of NORMAL/RECOVERY/FALLBACK selection.  It never recalculates Admission,
Binding applicability, Enforcement, or Recovery truth.
"""

from __future__ import annotations

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
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
    EnforcementAction,
    EnforcementBasis,
    EnforcementResultSchema,
    EnforcementSnapshotRefSchema,
    PreWriteRoute,
    PreWriteRouteDecisionSchema,
    RecoveryControlFactsSchema,
    RecoveryControlSnapshotRefSchema,
)
from core.hashing import canonical_sha256


class PreWriteRoutingIntegrityError(ValueError):
    """Routing inputs or a persisted RouteDecision are stale/incoherent."""


class PreWriteRoutingNotReadyError(RuntimeError):
    """The canonical pre-write lifecycle is valid but not yet routable."""


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


class PreWriteRouter:
    """Sole deterministic producer and commit validator for PreWriteRouteDecision."""

    @staticmethod
    def _validate_snapshot(
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision: WriterAdmissionDecisionSchema,
        binding: WriterGateBindingSchema,
        applicability: BindingApplicabilityResult,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
    ) -> None:
        normalized_write_instance_id = str(write_instance_id).strip()
        if not normalized_write_instance_id:
            raise PreWriteRoutingIntegrityError("write_instance_id is required")
        if decision.resolution_state is WriterAdmissionResolutionState.UNRESOLVED:
            raise PreWriteRoutingNotReadyError(
                "UNRESOLVED Admission lifecycle emits no business route"
            )
        if decision.resolution_state is not WriterAdmissionResolutionState.RESOLVED:
            raise PreWriteRoutingIntegrityError("unsupported Admission resolution state")
        if binding.writer_subject_ref != writer_subject_ref:
            raise PreWriteRoutingIntegrityError("Routing WriterSubjectRef mismatch")
        if binding.write_instance_id != normalized_write_instance_id:
            raise PreWriteRoutingIntegrityError("Routing write_instance_id mismatch")
        decision_ref = _decision_ref(decision)
        binding_ref = _binding_ref(binding)
        if binding.decision_ref != decision_ref:
            raise PreWriteRoutingIntegrityError("Binding does not resolve current Decision")
        if applicability.state is not BindingApplicabilityState.APPLICABLE:
            raise PreWriteRoutingIntegrityError(
                "Routing requires current APPLICABLE BindingApplicabilityResult"
            )
        if applicability.decision != decision or applicability.binding != binding:
            raise PreWriteRoutingIntegrityError(
                "BindingApplicability resolved a different Decision/Binding"
            )
        if enforcement.enforcement_basis is not EnforcementBasis.BUSINESS_ADMISSION:
            raise PreWriteRoutingIntegrityError(
                "technical/integrity EnforcementResult is not business-routable"
            )
        if enforcement.decision_ref != decision_ref or enforcement.binding_ref != binding_ref:
            raise PreWriteRoutingIntegrityError(
                "EnforcementResult lineage does not match current Decision/Binding"
            )
        if recovery_control.writer_subject_ref != writer_subject_ref:
            raise PreWriteRoutingIntegrityError("RecoveryControlFacts subject is stale")
        if recovery_control.write_instance_id != normalized_write_instance_id:
            raise PreWriteRoutingIntegrityError("RecoveryControlFacts write instance is stale")
        if recovery_control.decision_ref != decision_ref:
            raise PreWriteRoutingIntegrityError("RecoveryControlFacts Decision ref is stale")
        if recovery_control.binding_ref != binding_ref:
            raise PreWriteRoutingIntegrityError("RecoveryControlFacts Binding ref is stale")

    @staticmethod
    def _select_route(
        *,
        decision: WriterAdmissionDecisionSchema,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
    ) -> PreWriteRoute:
        if decision.admission_outcome is WriterAdmissionOutcome.ALLOW:
            if enforcement.action is not EnforcementAction.CONTINUE:
                raise PreWriteRoutingIntegrityError("ALLOW + business BLOCK is invalid")
            return PreWriteRoute.NORMAL

        if decision.admission_outcome is not WriterAdmissionOutcome.DENY:
            raise PreWriteRoutingIntegrityError("resolved Decision requires ALLOW or DENY")

        if enforcement.action is EnforcementAction.CONTINUE:
            return PreWriteRoute.NORMAL
        if enforcement.action is not EnforcementAction.BLOCK:
            raise PreWriteRoutingIntegrityError("unsupported Enforcement action")
        if recovery_control.recovery_available and not recovery_control.recovery_exhausted:
            return PreWriteRoute.RECOVERY
        if not recovery_control.recovery_available and recovery_control.recovery_exhausted:
            return PreWriteRoute.FALLBACK
        raise PreWriteRoutingIntegrityError(
            "DENY + BLOCK requires exactly Recovery available or exhausted"
        )

    @staticmethod
    def _enforcement_ref(
        enforcement: EnforcementResultSchema,
    ) -> EnforcementSnapshotRefSchema:
        return EnforcementSnapshotRefSchema(
            schema_version=enforcement.schema_version,
            contract_version=enforcement.contract_version,
            mode=enforcement.mode,
        )

    @staticmethod
    def _recovery_ref(
        recovery_control: RecoveryControlFactsSchema,
    ) -> RecoveryControlSnapshotRefSchema:
        return RecoveryControlSnapshotRefSchema(
            schema_version=recovery_control.schema_version,
            contract_version=recovery_control.contract_version,
            recovery_state_revision=recovery_control.recovery_state_revision,
        )

    @classmethod
    def authorization_snapshot_sha256(
        cls,
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision: WriterAdmissionDecisionSchema,
        binding: WriterGateBindingSchema,
        applicability: BindingApplicabilityResult,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
    ) -> str:
        """Hash the exact upstream semantics authorized for this route decision."""

        cls._validate_snapshot(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            decision=decision,
            binding=binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        snapshot = {
            "snapshot_schema_version": "pre_write_route_authorization_snapshot_v1",
            "route_contract_version": "pre_write_routing_v1",
            "writer_subject_ref": writer_subject_ref.model_dump(
                mode="json", round_trip=True
            ),
            "write_instance_id": str(write_instance_id).strip(),
            "decision": decision.model_dump(mode="json", round_trip=True),
            "binding": binding.model_dump(mode="json", round_trip=True),
            "applicability_state": BindingApplicabilityState.APPLICABLE.value,
            "enforcement": enforcement.model_dump(mode="json", round_trip=True),
            "recovery_control": recovery_control.model_dump(
                mode="json", round_trip=True
            ),
        }
        return canonical_sha256(snapshot)

    @classmethod
    def _effective_snapshot_key(
        cls,
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision: WriterAdmissionDecisionSchema,
        binding: WriterGateBindingSchema,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
    ) -> dict[str, object]:
        return {
            "route_decision_schema_version": "pre_write_route_decision_v1",
            "route_decision_contract_version": "pre_write_routing_v1",
            "writer_subject_ref": writer_subject_ref.model_dump(
                mode="json", round_trip=True
            ),
            "write_instance_id": str(write_instance_id).strip(),
            "decision_ref": _decision_ref(decision).model_dump(
                mode="json", round_trip=True
            ),
            "binding_ref": _binding_ref(binding).model_dump(
                mode="json", round_trip=True
            ),
            "enforcement_ref": cls._enforcement_ref(enforcement).model_dump(
                mode="json", round_trip=True
            ),
            "recovery_control_ref": cls._recovery_ref(recovery_control).model_dump(
                mode="json", round_trip=True
            ),
        }

    @classmethod
    def evaluate(
        cls,
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision: WriterAdmissionDecisionSchema,
        binding: WriterGateBindingSchema,
        applicability: BindingApplicabilityResult,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
    ) -> PreWriteRouteDecisionSchema:
        cls._validate_snapshot(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            decision=decision,
            binding=binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        selected_route = cls._select_route(
            decision=decision,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        snapshot_key = cls._effective_snapshot_key(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            decision=decision,
            binding=binding,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        route_decision_id = f"pre_write_route_{canonical_sha256(snapshot_key)}"
        return PreWriteRouteDecisionSchema(
            route_decision_id=route_decision_id,
            writer_subject_ref=writer_subject_ref,
            write_instance_id=str(write_instance_id).strip(),
            decision_ref=_decision_ref(decision),
            binding_ref=_binding_ref(binding),
            enforcement_ref=cls._enforcement_ref(enforcement),
            recovery_control_ref=cls._recovery_ref(recovery_control),
            selected_route=selected_route,
        )

    @classmethod
    def validate_commit(
        cls,
        route_decision: PreWriteRouteDecisionSchema,
        *,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
        decision: WriterAdmissionDecisionSchema,
        binding: WriterGateBindingSchema,
        applicability: BindingApplicabilityResult,
        enforcement: EnforcementResultSchema,
        recovery_control: RecoveryControlFactsSchema,
    ) -> PreWriteRouteDecisionSchema:
        """Revalidate the exact current snapshot and deterministic expected route.

        The same Router matrix is reused here; no Step-2/3/4/5 producer is called.
        A stale or contradictory stored RouteDecision fails closed and is never patched.
        """

        expected = cls.evaluate(
            writer_subject_ref=writer_subject_ref,
            write_instance_id=write_instance_id,
            decision=decision,
            binding=binding,
            applicability=applicability,
            enforcement=enforcement,
            recovery_control=recovery_control,
        )
        if route_decision != expected:
            raise PreWriteRoutingIntegrityError(
                "PreWriteRouteDecision is stale, contradictory, or identity-colliding"
            )
        return route_decision


__all__ = [
    "PreWriteRouter",
    "PreWriteRoutingIntegrityError",
    "PreWriteRoutingNotReadyError",
]
