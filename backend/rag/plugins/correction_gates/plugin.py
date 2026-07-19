"""Corrective retrieval gates evaluated after evidence assessment."""

from __future__ import annotations

from typing import Any

from rag.ports.quality import CorrectionGateDecision, EvidenceAssessment


class EvidenceSufficiencyCorrectionGate:
    """Open correction only for insufficient evidence with remaining budget."""

    def __init__(self, *, build_context: Any = None) -> None:
        del build_context

    def decide(
        self,
        *,
        assessment: EvidenceAssessment,
        correction_budget: int,
        completed_rounds: int,
        runtime_context: dict[str, Any] | None = None,
    ) -> CorrectionGateDecision:
        del runtime_context
        budget = max(0, int(correction_budget))
        completed = max(0, int(completed_rounds))
        remaining = max(0, budget - completed)
        required = (not assessment.sufficient) and remaining > 0
        if assessment.sufficient:
            reason = "evidence is sufficient"
        elif remaining <= 0:
            reason = "evidence is insufficient but correction budget is exhausted"
        else:
            reason = assessment.reason or "evidence is insufficient"
        return CorrectionGateDecision(
            required=required,
            reason=reason,
            remaining_rounds=remaining,
            metadata={
                "correction_budget": budget,
                "completed_rounds": completed,
                "assessment_confidence": assessment.confidence,
            },
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {"enabled": True, "mode": "evidence_sufficiency_and_budget"}
