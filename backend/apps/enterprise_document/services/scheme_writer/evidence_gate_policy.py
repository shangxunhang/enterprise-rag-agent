"""Application-owned business actions derived from public evidence facts."""

from __future__ import annotations

from collections.abc import Iterable

from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from schemas.claim import (
    CURRENT_PROJECT_CLAIM_TYPES,
    TEMPLATE_ONLY_CLAIM_TYPES,
    ClaimType,
)
from schemas.rag import EvidenceStatus, RAGEvidenceAssessmentSchema


_FACT_STATUS_REVIEW_REASON_CODES = frozenset(
    {
        "fact_status_estimated",
        "fact_status_proposed",
        "fact_status_planned",
    }
)

# Application-owned identity for the frozen Evidence Gate business policy.
APPLICATION_EVIDENCE_GATE_POLICY_ID = "candidate_capability_union_v1"


def _stable_unique(values: Iterable[str]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip() for value in values if str(value or "").strip()
        )
    )


class EvidenceGatePolicy:
    """Resolve Application Gate actions from the public RAG assessment only."""

    @staticmethod
    def _claim_risk_level(claims: set[ClaimType]) -> ClaimRiskLevel:
        if claims & (
            CURRENT_PROJECT_CLAIM_TYPES | {ClaimType.PROCUREMENT_REQUIREMENT}
        ):
            return ClaimRiskLevel.HIGH
        if claims & {
            ClaimType.POLICY_REQUIREMENT,
            ClaimType.TECHNICAL_SPECIFICATION,
        }:
            return ClaimRiskLevel.MEDIUM
        return ClaimRiskLevel.LOW

    @staticmethod
    def _coverage_reason(assessment: RAGEvidenceAssessmentSchema) -> str:
        if assessment.status is EvidenceStatus.CONFLICTED:
            return "evidence_conflict_detected"
        if assessment.status is EvidenceStatus.SUFFICIENT:
            return "coverage_sufficient"
        if (
            assessment.status is EvidenceStatus.INSUFFICIENT
            and assessment.coverage_ratio is not None
            and assessment.coverage_ratio > 0
            and bool(assessment.covered_topics)
        ):
            return "coverage_partial"
        return "coverage_absent"

    def evaluate(
        self,
        assessment: RAGEvidenceAssessmentSchema,
        *,
        policy_id: str = APPLICATION_EVIDENCE_GATE_POLICY_ID,
    ) -> GateDecisionSchema:
        """Apply the frozen Gate policy without consulting RAG internals."""

        if assessment.assessment_state != "completed" or assessment.status is None:
            raise ValueError("Evidence Gate requires a completed public assessment")

        supported = set(assessment.supported_claim_types)
        allowed = set(supported)
        forbidden = set(assessment.forbidden_claim_types)
        resolved_risk = self._claim_risk_level(supported)
        status_qualifiers = list(assessment.required_qualifiers)
        public_reason_codes = set(assessment.reason_codes)
        certainty_review_required = bool(
            public_reason_codes & _FACT_STATUS_REVIEW_REASON_CODES
        )

        qualifiers: list[str] = []
        human_review = False
        reason_codes = [self._coverage_reason(assessment)]

        if assessment.prompt_evidence_contract_missing:
            delivery = DeliveryMode.BLOCKED
            allowed = set()
            reason_codes.append("prompt_evidence_contract_missing")
        elif assessment.status is EvidenceStatus.CONFLICTED:
            delivery = DeliveryMode.HUMAN_REVIEW
            allowed = set()
            human_review = True
            reason_codes.append("unresolved_evidence_conflict")
        elif assessment.status is EvidenceStatus.SUFFICIENT:
            if status_qualifiers:
                delivery = DeliveryMode.QUALIFIED
                qualifiers = status_qualifiers
                human_review = certainty_review_required
                reason_codes.append("fact_status_requires_qualification")
            elif allowed and allowed <= TEMPLATE_ONLY_CLAIM_TYPES:
                delivery = DeliveryMode.GROUNDED
            elif (
                allowed & CURRENT_PROJECT_CLAIM_TYPES
                and not assessment.authoritative_evidence_present
            ):
                delivery = DeliveryMode.QUALIFIED
                qualifiers = ["依据当前可用材料", "相关事实仍需业务确认"]
                human_review = resolved_risk in {
                    ClaimRiskLevel.HIGH,
                    ClaimRiskLevel.CRITICAL,
                }
                reason_codes.append("current_project_evidence_not_authoritative")
            elif assessment.historical_or_reference_evidence_present:
                delivery = DeliveryMode.QUALIFIED
                qualifiers = ["建议", "参考同类项目", "需结合本项目确认"]
                reason_codes.append("reference_evidence_requires_qualification")
            else:
                delivery = DeliveryMode.GROUNDED
        elif (
            assessment.status is EvidenceStatus.INSUFFICIENT
            and assessment.coverage_ratio is not None
            and assessment.coverage_ratio > 0
            and bool(assessment.missing_topics)
        ):
            if resolved_risk in {
                ClaimRiskLevel.HIGH,
                ClaimRiskLevel.CRITICAL,
            }:
                delivery = DeliveryMode.HUMAN_REVIEW
                allowed = set()
                human_review = True
                reason_codes.append("partial_high_risk_evidence")
            else:
                delivery = DeliveryMode.QUALIFIED
                qualifiers = _stable_unique(
                    [
                        *status_qualifiers,
                        "仅覆盖已具备证据的部分",
                        "缺失内容待补充确认",
                    ]
                )
                human_review = True
                reason_codes.append("partial_low_or_medium_risk_evidence")
        else:
            delivery = DeliveryMode.BLOCKED
            allowed = set()
            reason_codes.append("required_prompt_evidence_absent")

        if assessment.prompt_evidence_source == "legacy_fallback" and delivery in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY,
        }:
            delivery = DeliveryMode.QUALIFIED
            qualifiers = _stable_unique(
                [*qualifiers, "基于旧版 Prompt 证据兼容投影，需人工复核"]
            )
            human_review = True
            reason_codes.append("legacy_prompt_evidence_fallback")

        return GateDecisionSchema(
            policy_id=policy_id,
            evidence_status=assessment.status,
            delivery_mode=delivery,
            claim_risk_level=resolved_risk,
            allowed_claim_types=sorted(allowed, key=lambda item: item.value),
            forbidden_claim_types=sorted(
                forbidden, key=lambda item: item.value
            ),
            required_qualifiers=_stable_unique(qualifiers),
            covered_topics=list(assessment.covered_topics),
            missing_topics=list(assessment.missing_topics),
            human_review_required=human_review,
            reason_codes=_stable_unique(reason_codes),
            metadata={
                "authoritative_evidence_present": (
                    assessment.authoritative_evidence_present
                ),
                "historical_or_reference_evidence_present": (
                    assessment.historical_or_reference_evidence_present
                ),
                "prompt_evidence_source": assessment.prompt_evidence_source,
                "claim_resolution_mode": "candidate_capability_union",
                "requested_claim_types_ignored": True,
            },
        )


__all__ = ["APPLICATION_EVIDENCE_GATE_POLICY_ID", "EvidenceGatePolicy"]
