"""Crash recovery and conditional running-invocation recovery contracts."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field, model_validator

from ._base import ContractSchema
from .enums import (
    InvocationRecoveryAction,
    ResumeTriggerKind,
    UnderlyingInvocationStatus,
)
from .state import HumanReviewStateSchema
from .workflow import ResumeDecisionSchema


class RunningInvocationRecoveryInputSchema(ContractSchema):
    schema_version: str = "running_invocation_recovery_input_v1"
    invocation_id: str
    task_id: str
    run_id: str
    execution_id: str
    attempt_id: str
    input_snapshot_version: str
    idempotency_key: str
    completion_event_persisted: bool = False
    result_persisted: bool = False
    result_ref: Optional[str] = None
    underlying_queryable: bool = False
    underlying_status: UnderlyingInvocationStatus = (
        UnderlyingInvocationStatus.UNKNOWN
    )

    @model_validator(mode="after")
    def _validate_result_and_query_state(
        self,
    ) -> "RunningInvocationRecoveryInputSchema":
        if self.result_persisted and not self.result_ref:
            raise ValueError("persisted invocation result requires result_ref")
        if (
            not self.underlying_queryable
            and self.underlying_status
            not in {
                UnderlyingInvocationStatus.UNKNOWN,
                UnderlyingInvocationStatus.NOT_FOUND,
            }
        ):
            raise ValueError(
                "non-queryable invocation cannot report a live underlying status"
            )
        return self


class RunningInvocationRecoveryContextSchema(ContractSchema):
    schema_version: str = "running_invocation_recovery_context_v1"
    task_id: str
    run_id: str
    input_snapshot_version: str
    active_execution_ids: List[str] = Field(default_factory=list)
    active_attempt_ids: List[str] = Field(default_factory=list)
    draining: bool = False
    cancelled: bool = False


class InvocationRecoveryDecisionSchema(ContractSchema):
    schema_version: str = "invocation_recovery_decision_v1"
    invocation_id: str
    action: InvocationRecoveryAction
    reason_code: str
    idempotency_key: Optional[str] = None
    result_ref: Optional[str] = None
    starts_new_call: bool = False


class HumanReviewResumeRequestSchema(ContractSchema):
    schema_version: str = "human_review_resume_request_v1"
    task_id: str
    review_id: str
    input_snapshot_version: str
    decision_ref: str
    trigger_kind: ResumeTriggerKind


class HumanReviewResumeResultSchema(ContractSchema):
    schema_version: str = "human_review_resume_result_v1"
    review: HumanReviewStateSchema
    resume_decision: ResumeDecisionSchema
