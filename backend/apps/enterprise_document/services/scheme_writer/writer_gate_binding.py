"""Target Writer Gate binding construction and deterministic applicability checks."""

from __future__ import annotations

import re

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionDecisionSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    BindingApplicabilityResult,
    BindingApplicabilityState,
    WriterGateBindingRefSchema,
    WriterGateBindingSchema,
    WriterSubjectRefSchema,
)
from core.hashing import canonical_sha256


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DecisionReferenceIntegrityError(ValueError):
    """The exact immutable Decision tuple cannot be resolved."""


def _decision_ref(
    decision: WriterAdmissionDecisionSchema,
) -> WriterAdmissionDecisionRefSchema:
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


def resolve_decision_ref(
    decision_ref: WriterAdmissionDecisionRefSchema,
    *,
    active_decision: WriterAdmissionDecisionSchema | None,
) -> WriterAdmissionDecisionSchema:
    """Resolve one exact Decision ref against independently active canonical state.

    This helper deliberately accepts no Evidence, Policy, Binding-derived current pointer,
    or historical compatibility projection.  Missing or mismatched resolution stays a
    technical reference-integrity failure.
    """

    if active_decision is None:
        raise DecisionReferenceIntegrityError("active canonical Decision is unavailable")
    if decision_ref != _decision_ref(active_decision):
        raise DecisionReferenceIntegrityError(
            "Decision ref does not resolve to the active canonical Decision"
        )
    return active_decision


class WriterGateBinder:
    """Sole canonical producer of an immutable Decision/Writer relation artifact."""

    @staticmethod
    def bind(
        *,
        decision: WriterAdmissionDecisionSchema,
        writer_subject_ref: WriterSubjectRefSchema,
        write_instance_id: str,
    ) -> WriterGateBindingSchema:
        normalized_write_instance_id = str(write_instance_id).strip()
        if not normalized_write_instance_id:
            raise ValueError("write_instance_id is required for Writer Gate binding")
        decision_ref = _decision_ref(decision)
        preimage = {
            "schema_version": "writer_gate_binding_v3",
            "contract_version": "writer_gate_binding_control_v1",
            "decision_ref": decision_ref.model_dump(mode="json", round_trip=True),
            "writer_subject_ref": writer_subject_ref.model_dump(
                mode="json", round_trip=True
            ),
            "write_instance_id": normalized_write_instance_id,
        }
        binding_id = f"writer_gate_binding_{canonical_sha256(preimage)}"
        return WriterGateBindingSchema(
            binding_id=binding_id,
            decision_ref=decision_ref,
            writer_subject_ref=writer_subject_ref,
            write_instance_id=normalized_write_instance_id,
        )


class WriterGateApplicabilityValidator:
    """Validate Binding currentness from independently supplied active state only."""

    @staticmethod
    def resolve_decision_ref(
        decision_ref: WriterAdmissionDecisionRefSchema,
        *,
        active_decision: WriterAdmissionDecisionSchema | None,
    ) -> WriterAdmissionDecisionSchema:
        return resolve_decision_ref(decision_ref, active_decision=active_decision)

    @staticmethod
    def _reference_integrity_failure() -> BindingApplicabilityResult:
        return BindingApplicabilityResult(
            state=BindingApplicabilityState.REFERENCE_INTEGRITY_FAILURE
        )

    @staticmethod
    def _not_applicable() -> BindingApplicabilityResult:
        return BindingApplicabilityResult(state=BindingApplicabilityState.NOT_APPLICABLE)

    def validate(
        self,
        *,
        binding: WriterGateBindingSchema,
        current_writer_subject_ref: WriterSubjectRefSchema | None,
        current_write_instance_id: str | None,
        current_decision: WriterAdmissionDecisionSchema | None,
        current_active_evidence_bundle_sha256: str | None,
        current_active_requirements_sha256: str | None,
        current_active_binding_ref: WriterGateBindingRefSchema | None,
    ) -> BindingApplicabilityResult:
        """Apply the frozen six-part currentness predicate.

        Current values must come from the active orchestration/checkpoint authority.  The
        validator never synthesizes them from ``binding`` and never publishes/promotes a
        Binding as part of validation.
        """

        if (
            current_writer_subject_ref is None
            or current_decision is None
            or current_active_binding_ref is None
            or not str(current_write_instance_id or "").strip()
            or not _SHA256_RE.fullmatch(
                str(current_active_evidence_bundle_sha256 or "")
            )
            or not _SHA256_RE.fullmatch(str(current_active_requirements_sha256 or ""))
        ):
            return self._reference_integrity_failure()

        # Identity/current-relation mismatches are stale, not business DENY and not a
        # fabricated UNRESOLVED Decision.
        if binding.writer_subject_ref != current_writer_subject_ref:
            return self._not_applicable()
        if binding.write_instance_id != str(current_write_instance_id):
            return self._not_applicable()
        if binding.decision_ref != _decision_ref(current_decision):
            return self._not_applicable()
        if _binding_ref(binding) != current_active_binding_ref:
            return self._not_applicable()

        try:
            resolved_decision = resolve_decision_ref(
                binding.decision_ref,
                active_decision=current_decision,
            )
        except DecisionReferenceIntegrityError:
            return self._reference_integrity_failure()

        fact_ref = resolved_decision.fact_assessment_ref
        if (
            fact_ref.evidence_bundle_sha256
            != current_active_evidence_bundle_sha256
            or fact_ref.requirements_sha256 != current_active_requirements_sha256
        ):
            return self._not_applicable()

        return BindingApplicabilityResult(
            state=BindingApplicabilityState.APPLICABLE,
            decision=resolved_decision,
            binding=binding,
        )


__all__ = [
    "DecisionReferenceIntegrityError",
    "WriterGateApplicabilityValidator",
    "WriterGateBinder",
    "resolve_decision_ref",
]
