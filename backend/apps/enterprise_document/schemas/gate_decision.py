"""Frozen V1 gate-decision and delivery-mode contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from rag.catalog.document_profile import (
    CURRENT_PROJECT_CLAIM_TYPES,
    TEMPLATE_ONLY_CLAIM_TYPES,
    ClaimType,
)
from rag.schema.evidence_assessment_v2 import EvidenceStatus


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class DeliveryMode(str, Enum):
    GROUNDED = "grounded"
    QUALIFIED = "qualified"
    FRAMEWORK_ONLY = "framework_only"
    BLOCKED = "blocked"
    HUMAN_REVIEW = "human_review"


class ClaimRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GateDecisionSchema(_FrozenContract):
    """Auditable policy resolution consumed by the unified section writer."""

    schema_version: Literal["gate_decision_v1"] = "gate_decision_v1"
    policy_id: NonEmptyStr
    evidence_status: EvidenceStatus
    delivery_mode: DeliveryMode
    claim_risk_level: ClaimRiskLevel = ClaimRiskLevel.LOW
    allowed_claim_types: list[ClaimType] = Field(default_factory=list)
    forbidden_claim_types: list[ClaimType] = Field(default_factory=list)
    required_qualifiers: list[NonEmptyStr] = Field(default_factory=list)
    covered_topics: list[NonEmptyStr] = Field(default_factory=list)
    missing_topics: list[NonEmptyStr] = Field(default_factory=list)
    human_review_required: bool = False
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _decision_is_consistent(self) -> "GateDecisionSchema":
        for field_name in (
            "allowed_claim_types",
            "forbidden_claim_types",
            "required_qualifiers",
            "covered_topics",
            "missing_topics",
            "reason_codes",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")

        overlap = set(self.allowed_claim_types) & set(self.forbidden_claim_types)
        if overlap:
            raise ValueError(
                "claim types cannot be both allowed and forbidden: "
                + ", ".join(sorted(item.value for item in overlap))
            )
        topic_overlap = set(self.covered_topics) & set(self.missing_topics)
        if topic_overlap:
            raise ValueError(
                "topics cannot be both covered and missing: "
                + ", ".join(sorted(topic_overlap))
            )

        if self.evidence_status is EvidenceStatus.CONFLICTED and self.delivery_mode not in {
            DeliveryMode.BLOCKED,
            DeliveryMode.HUMAN_REVIEW,
        }:
            raise ValueError("conflicted evidence must be blocked or sent to human_review")
        if self.evidence_status in {
            EvidenceStatus.ABSENT,
            EvidenceStatus.NOT_ASSESSED,
        } and self.delivery_mode in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
        }:
            raise ValueError(
                "absent or not_assessed evidence cannot produce grounded/qualified delivery"
            )

        if self.delivery_mode is DeliveryMode.GROUNDED:
            if self.evidence_status is not EvidenceStatus.SUFFICIENT:
                raise ValueError("grounded delivery requires sufficient evidence")
            if self.human_review_required:
                raise ValueError("grounded delivery cannot require human review")
        elif self.delivery_mode is DeliveryMode.QUALIFIED:
            if self.evidence_status not in {
                EvidenceStatus.SUFFICIENT,
                EvidenceStatus.PARTIAL,
            }:
                raise ValueError("qualified delivery requires sufficient or partial evidence")
            if not self.required_qualifiers:
                raise ValueError("qualified delivery requires required_qualifiers")
            if (
                self.evidence_status is EvidenceStatus.PARTIAL
                and not self.missing_topics
            ):
                raise ValueError("partial qualified delivery requires missing_topics")
            if (
                self.evidence_status is EvidenceStatus.PARTIAL
                and self.claim_risk_level
                in {ClaimRiskLevel.HIGH, ClaimRiskLevel.CRITICAL}
            ):
                raise ValueError(
                    "partial high-risk claims must be blocked or sent to human_review"
                )
        elif self.delivery_mode is DeliveryMode.FRAMEWORK_ONLY:
            if self.evidence_status not in {
                EvidenceStatus.ABSENT,
                EvidenceStatus.NOT_ASSESSED,
            }:
                raise ValueError(
                    "framework_only delivery requires absent or not_assessed evidence"
                )
            disallowed = set(self.allowed_claim_types) - TEMPLATE_ONLY_CLAIM_TYPES
            if disallowed:
                raise ValueError(
                    "framework_only may only allow template_structure or style_guidance"
                )
            if set(self.allowed_claim_types) & CURRENT_PROJECT_CLAIM_TYPES:
                raise ValueError(
                    "framework_only cannot allow current-project factual claims"
                )
        elif self.delivery_mode is DeliveryMode.BLOCKED:
            if self.allowed_claim_types:
                raise ValueError("blocked delivery cannot allow claim types")
        elif self.delivery_mode is DeliveryMode.HUMAN_REVIEW:
            if not self.human_review_required:
                raise ValueError("human_review delivery requires human_review_required=true")
        return self


__all__ = ["ClaimRiskLevel", "DeliveryMode", "GateDecisionSchema"]
