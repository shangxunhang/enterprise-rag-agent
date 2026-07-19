"""Evidence assessment plugins; these never plan corrective retrieval."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from rag.judge.rag_quality_judge import CRAGJudge
from rag.ports.quality import EvidenceAssessment, EvidenceJudgement


class CRAGEvidenceAssessorPlugin:
    """Observe reranked evidence using CRAG-style quality judgement."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        max_judge_chunks: int = 8,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Iterable[str] | None = None,
        confidence_threshold: float = 0.55,
        min_relevant_chunks: int = 1,
    ) -> None:
        context = build_context if isinstance(build_context, dict) else {}
        context_llm_enabled = bool(context.get("enable_quality_llm", False))
        self.use_llm = context_llm_enabled if use_llm is None else bool(use_llm)
        self.max_judge_chunks = max(1, int(max_judge_chunks))
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.noise_terms = tuple(str(item) for item in (noise_terms or ()))
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self.min_relevant_chunks = max(0, int(min_relevant_chunks))
        self.llm_generator = context.get("quality_llm_generator")
        self.backend = CRAGJudge(
            llm_generator=self.llm_generator,
            use_llm=self.use_llm,
            generation_params=dict(context.get("quality_generation_params") or {}),
            fallback_to_deterministic=self.fallback_to_deterministic,
            noise_terms=self.noise_terms,
        )

    @staticmethod
    def _relevant_count(report: dict[str, Any]) -> int:
        return sum(
            1
            for item in list(report.get("item_judgements") or [])
            if str(item.get("relevance_label") or "") == "relevant"
        )

    @staticmethod
    def _item_judgements(report: dict[str, Any]) -> tuple[EvidenceJudgement, ...]:
        output: list[EvidenceJudgement] = []
        for raw in list(report.get("item_judgements") or []):
            metadata = dict(raw)
            evidence_id = str(metadata.pop("chunk_id", "") or "")
            relevance = str(metadata.pop("relevance_label", "unknown") or "unknown")
            confidence = max(
                0.0,
                min(1.0, float(metadata.pop("score", 0.0) or 0.0)),
            )
            reason = str(metadata.pop("reason", "") or "")
            output.append(
                EvidenceJudgement(
                    evidence_id=evidence_id,
                    relevance=relevance,
                    confidence=confidence,
                    reason=reason,
                    metadata=metadata,
                )
            )
        return tuple(output)

    def assess(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceAssessment:
        del runtime_context
        report_object = self.backend.evaluate(
            query=query,
            results=deepcopy(list(results or [])),
            max_judge_chunks=self.max_judge_chunks,
        )
        report = report_object.to_dict()
        item_judgements = self._item_judgements(report)
        confidence = float(report.get("retrieval_confidence") or 0.0)
        relevant_count = self._relevant_count(report)
        report.pop("item_judgements", None)
        reasons: list[str] = []
        if confidence < self.confidence_threshold:
            reasons.append(
                f"retrieval_confidence {confidence:.4f} below "
                f"threshold {self.confidence_threshold:.4f}"
            )
        if relevant_count < self.min_relevant_chunks:
            reasons.append(
                f"relevant_chunk_count {relevant_count} below "
                f"minimum {self.min_relevant_chunks}"
            )
        sufficient = not reasons
        report["evidence_assessment"] = {
            "sufficient": sufficient,
            "confidence": confidence,
            "reason": "; ".join(reasons) or "evidence meets configured thresholds",
            "relevant_chunk_count": relevant_count,
        }
        return EvidenceAssessment(
            sufficient=sufficient,
            confidence=confidence,
            reason=report["evidence_assessment"]["reason"],
            item_judgements=item_judgements,
            report=report,
            metadata={
                "relevant_chunk_count": relevant_count,
                "observed_evidence_count": len(results or []),
            },
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "crag_evidence_assessment",
            "max_judge_chunks": self.max_judge_chunks,
            "confidence_threshold": self.confidence_threshold,
            "min_relevant_chunks": self.min_relevant_chunks,
            "use_llm": self.use_llm,
            "llm_available": self.llm_generator is not None,
            "fallback_to_deterministic": self.fallback_to_deterministic,
            "assessment_only": True,
        }


class NoOpEvidenceAssessorPlugin:
    """Explicit assessment implementation for tests and controlled baselines."""

    def __init__(self, *, build_context: Any = None) -> None:
        del build_context

    def assess(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceAssessment:
        del query, runtime_context
        observed_count = len(results or [])
        return EvidenceAssessment(
            sufficient=bool(observed_count),
            confidence=1.0 if observed_count else 0.0,
            reason="noop assessor treats non-empty evidence as sufficient",
            report={"method": "noop_evidence_assessor"},
            metadata={"observed_evidence_count": observed_count},
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "noop_evidence_assessment",
            "assessment_only": True,
        }
