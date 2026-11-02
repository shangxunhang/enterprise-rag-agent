"""Canonical semantic identity for RAG Evidence-use eligibility."""

from __future__ import annotations

EVIDENCE_USE_ELIGIBILITY_POLICY_ID = "rag_evidence_use_eligibility"
EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION = "v1"


def resolve_evidence_use_eligibility_policy(
    policy_id: str,
    policy_version: str,
) -> tuple[str, str]:
    """Resolve the only Target V1 eligibility semantic policy pair."""

    normalized = (str(policy_id or "").strip(), str(policy_version or "").strip())
    expected = (
        EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
    )
    if normalized != expected:
        raise ValueError(
            "unknown evidence-use eligibility semantic policy: "
            f"{normalized[0]}@{normalized[1]}"
        )
    return expected


__all__ = [
    "EVIDENCE_USE_ELIGIBILITY_POLICY_ID",
    "EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION",
    "resolve_evidence_use_eligibility_policy",
]
