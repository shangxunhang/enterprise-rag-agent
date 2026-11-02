"""352 additive transport only; all status axes reuse their original owners."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.post_write.foundation import (
    AvailabilityState, EnforcementAction, OverallQualityState, ValidationCompletionItemState,
)
from schemas.failure import FailureCategory, FailureOrigin
from schemas.status import ExecutionStatus
from core.hashing import canonical_sha256

OpaqueRef = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
ScopeRef = Annotated[str, Field(pattern=r"^(?:[A-Za-z0-9_-]{1,128}|sha256:[0-9a-f]{64})$")]


class _Transport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TerminalDiagnostic(_Transport):
    diagnostic_ref: OpaqueRef
    code: str = Field(max_length=96, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(max_length=240)
    failure_category: FailureCategory | None = None
    failure_origin: FailureOrigin | None = None


class TerminalArtifact(_Transport):
    """Opaque content-addressed handle to owner-approved nonpublication content."""
    artifact_ref: OpaqueRef
    section_id: ScopeRef
    content: str
    quality_state: OverallQualityState
    availability_state: AvailabilityState


class TerminalOutcomeResultV1(_Transport):
    schema_version: Literal["terminal_outcome_v1"] = "terminal_outcome_v1"
    result_id: OpaqueRef
    task_id: ScopeRef
    run_id: ScopeRef
    status: ExecutionStatus | EnforcementAction | FailureCategory | ValidationCompletionItemState
    execution_status: ExecutionStatus
    run_status: RunLifecycleStatus | None = None
    control_action: EnforcementAction | None = None
    quality_state: OverallQualityState | None = None
    availability_state: AvailabilityState | None = None
    phase: ScopeRef
    failure_category: FailureCategory | None = None
    failure_origin: FailureOrigin | None = None
    completed_scope_refs: tuple[ScopeRef, ...] = ()
    unfinished_scope_refs: tuple[ScopeRef, ...] = ()
    artifacts: tuple[TerminalArtifact, ...] = ()
    artifact_exists: bool = False
    publication_exists: bool = False
    publication_ref: str | None = Field(default=None, pattern=r"^post-write-release://sha256/[0-9a-f]{64}$")
    is_partial: bool = False
    is_best_effort: bool = False
    fallback_occurred: bool = False
    recovery_attempts: int = Field(default=0, ge=0)
    recovery_refs: tuple[OpaqueRef, ...] = ()
    diagnostics: tuple[TerminalDiagnostic, ...] = ()
    requires_human: bool = False
    requires_new_input: bool = False
    retryable: bool = False
    recoverable: bool = False

    @model_validator(mode="after")
    def _publication_boundary(self):
        if self.publication_exists:
            if (not self.publication_ref or self.run_status != RunLifecycleStatus.SUCCESS
                    or self.control_action not in {EnforcementAction.OUTPUT, EnforcementAction.OUTPUT_BEST_EFFORT}):
                raise ValueError("publication requires exact eligible canonical completion")
        elif self.publication_ref is not None:
            raise ValueError("unpublished result cannot carry a publication ref")
        if self.control_action is EnforcementAction.BLOCK and self.status != EnforcementAction.BLOCK:
            raise ValueError("BLOCK must remain explicit")
        if self.is_best_effort and not self.is_partial:
            raise ValueError("BEST_EFFORT cannot imply complete success")
        if self.control_action is EnforcementAction.OUTPUT_BEST_EFFORT and not self.is_best_effort:
            raise ValueError("canonical BEST_EFFORT must remain explicit")
        if self.execution_status is ExecutionStatus.PARTIAL_SUCCESS and not self.is_partial:
            raise ValueError("partial execution must remain explicit")
        expected_id = "sha256:" + canonical_sha256(self.model_dump(mode="json", exclude={"result_id"}))
        if self.result_id != expected_id:
            raise ValueError("terminal result content identity mismatch")
        return self
