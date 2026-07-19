"""Independent contracts for evidence assessment and corrective retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EvidenceJudgement:
    """Read-only quality label for one evidence item."""

    evidence_id: str
    relevance: str
    confidence: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "relevance": self.relevance,
            "confidence": float(self.confidence),
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvidenceAssessment:
    """Read-only quality assessment of one reranked evidence set.

    Evidence deliberately is not carried by this value object. The retrieval
    pipeline owns evidence quantity, order and content; an assessor can only
    describe what it observed.
    """

    sufficient: bool
    confidence: float
    reason: str
    item_judgements: tuple[EvidenceJudgement, ...] = ()
    report: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficient": bool(self.sufficient),
            "confidence": float(self.confidence),
            "reason": self.reason,
            "item_judgements": [item.to_dict() for item in self.item_judgements],
            "report": dict(self.report),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CorrectionGateDecision:
    """Budget-aware decision made after evidence has been assessed."""

    required: bool
    reason: str
    remaining_rounds: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": bool(self.required),
            "reason": self.reason,
            "remaining_rounds": max(0, int(self.remaining_rounds)),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CorrectiveQueryPlan:
    """Queries proposed after the correction gate has opened."""

    queries: tuple[str, ...] = ()
    reason: str = ""
    merge_original_candidates: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_queries(self, *, original_query: str = "") -> list[str]:
        original_key = str(original_query or "").strip().lower()
        seen: set[str] = set()
        output: list[str] = []
        for item in self.queries:
            text = str(item or "").strip()
            key = text.lower()
            if not text or key == original_key or key in seen:
                continue
            seen.add(key)
            output.append(text)
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries": list(self.queries),
            "reason": self.reason,
            "merge_original_candidates": bool(self.merge_original_candidates),
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class EvidenceAssessorPort(Protocol):
    def assess(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceAssessment: ...

    def execution_metadata(self) -> dict[str, Any]: ...


@runtime_checkable
class CorrectiveRetrievalGatePort(Protocol):
    def decide(
        self,
        *,
        assessment: EvidenceAssessment,
        correction_budget: int,
        completed_rounds: int,
        runtime_context: dict[str, Any] | None = None,
    ) -> CorrectionGateDecision: ...

    def execution_metadata(self) -> dict[str, Any]: ...


@runtime_checkable
class CorrectiveQueryPlannerPort(Protocol):
    def plan(
        self,
        *,
        query: str,
        assessment: EvidenceAssessment,
        runtime_context: dict[str, Any] | None = None,
    ) -> CorrectiveQueryPlan: ...

    def execution_metadata(self) -> dict[str, Any]: ...
