"""Stable human-review protocol shared by workflow and application layers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field

from schemas.common import SchemaBase


class HumanReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class HumanReviewRequestSchema(SchemaBase):
    """Checkpoint-safe request shown to a human reviewer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "human_review_request_v1"
    task_id: str
    run_id: str
    workflow_id: str
    workflow_version: str
    requested_at: str
    document_id: str | None = None
    hard_gate_passed: bool | None = None
    artifact_route: str | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanReviewDecisionSchema(SchemaBase):
    """Validated reviewer decision used to resume a durable workflow."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "human_review_decision_v1"
    decision: HumanReviewStatus
    feedback: str | None = None
    decided_at: str | None = None
    reviewer_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.decision not in {
            HumanReviewStatus.APPROVED,
            HumanReviewStatus.REJECTED,
            HumanReviewStatus.CANCELLED,
        }:
            raise ValueError(
                "human review decision must be approved, rejected, or cancelled"
            )
