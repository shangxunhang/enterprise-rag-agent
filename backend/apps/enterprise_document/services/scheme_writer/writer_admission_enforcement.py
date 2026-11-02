"""Typed Target Writer Admission enforcement boundary.

Enforcement consumes already-canonical Decision/Binding applicability truth and maps it to
one runtime consequence.  STRICT/SHADOW never revise Admission or Binding truth.
"""

from __future__ import annotations

from typing import Any

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
)
from apps.enterprise_document.schemas.pre_write_control import (
    EnforcementAction,
    EnforcementBasis,
    EnforcementResultSchema,
)


class WriterAdmissionEnforcement:
    """Sole Writer-specific producer of the typed EnforcementResult value."""

    @staticmethod
    def _mode_value(mode: Any) -> str:
        value = getattr(mode, "value", mode)
        if value not in {"strict", "shadow"}:
            raise ValueError("Writer Admission enforcement mode must be strict or shadow")
        return str(value)

    @staticmethod
    def _decision_ref(
        decision: WriterAdmissionDecisionSchema,
    ) -> WriterAdmissionDecisionRefSchema:
        return WriterAdmissionDecisionRefSchema(
            decision_id=decision.decision_id,
            decision_schema_version=decision.schema_version,
            decision_contract_version=decision.contract_version,
        )

    def evaluate(
        self,
        *,
        applicability: BindingApplicabilityResult,
        decision: WriterAdmissionDecisionSchema | None,
        mode: Any,
    ) -> EnforcementResultSchema:
        """Apply the frozen business/technical × STRICT/SHADOW matrix."""

        mode_value = self._mode_value(mode)

        if applicability.state is not BindingApplicabilityState.APPLICABLE:
            return EnforcementResultSchema(
                mode=mode_value,
                action=EnforcementAction.BLOCK,
                would_block_in_strict=True,
                enforcement_basis=EnforcementBasis.TECHNICAL_INTEGRITY,
            )

        resolved_decision = applicability.decision
        binding = applicability.binding
        if resolved_decision is None or binding is None or decision is None:
            return EnforcementResultSchema(
                mode=mode_value,
                action=EnforcementAction.BLOCK,
                would_block_in_strict=True,
                enforcement_basis=EnforcementBasis.TECHNICAL_INTEGRITY,
            )

        exact_ref = self._decision_ref(decision)
        if (
            self._decision_ref(resolved_decision) != exact_ref
            or binding.decision_ref != exact_ref
        ):
            return EnforcementResultSchema(
                mode=mode_value,
                action=EnforcementAction.BLOCK,
                would_block_in_strict=True,
                enforcement_basis=EnforcementBasis.TECHNICAL_INTEGRITY,
            )

        would_block = not (
            decision.resolution_state is WriterAdmissionResolutionState.RESOLVED
            and decision.admission_outcome is WriterAdmissionOutcome.ALLOW
        )
        action = (
            EnforcementAction.BLOCK
            if mode_value == "strict" and would_block
            else EnforcementAction.CONTINUE
        )
        return EnforcementResultSchema(
            decision_ref=exact_ref,
            binding_ref=WriterGateBindingRefSchema(
                binding_id=binding.binding_id,
                binding_schema_version=binding.schema_version,
                binding_contract_version=binding.contract_version,
            ),
            mode=mode_value,
            action=action,
            would_block_in_strict=would_block,
            enforcement_basis=EnforcementBasis.BUSINESS_ADMISSION,
        )


__all__ = ["WriterAdmissionEnforcement"]
