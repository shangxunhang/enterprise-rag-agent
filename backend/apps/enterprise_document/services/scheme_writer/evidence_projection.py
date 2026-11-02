"""Application-owned projections derived from canonical EvidenceBundle facts."""

from __future__ import annotations

from typing import Any, Dict

from schemas.rag import RAGEvidenceContractSchema


def bundle_first_assessment_projection(
    bundle: RAGEvidenceContractSchema | None,
) -> Dict[str, Any]:
    """Project canonical Evidence assessment into Application state wording."""

    if bundle is not None:
        assessment: Dict[str, Any] = bundle.assessment.model_dump(
            mode="json",
            exclude_none=True,
        )
    else:
        assessment = {}
    raw_status = assessment.get("status") or "not_assessed"
    if hasattr(raw_status, "value"):
        raw_status = raw_status.value
    assessment["status"] = str(raw_status).strip().lower() or "not_assessed"
    details = assessment.get("details")
    assessment["details"] = dict(details) if isinstance(details, dict) else {}
    return assessment


__all__ = ["bundle_first_assessment_projection"]
