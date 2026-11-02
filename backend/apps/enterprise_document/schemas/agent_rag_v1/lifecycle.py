"""Canonical SIF-01 Run / Attempt lifecycle and active-lineage contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from core.hashing import canonical_sha256

from ._base import ContractSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class _FrozenLifecycleContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class RunLifecycleStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINALIZING = "finalizing"
    PAUSED = "paused"
    RETRYABLE_FAILED = "retryable_failed"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUN_TERMINAL_STATUSES = frozenset(
    {
        RunLifecycleStatus.SUCCESS,
        RunLifecycleStatus.PARTIAL_SUCCESS,
        RunLifecycleStatus.FAILED,
        RunLifecycleStatus.CANCELLED,
    }
)


class AttemptLifecycleStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


ATTEMPT_TERMINAL_STATUSES = frozenset(
    {
        AttemptLifecycleStatus.COMPLETED,
        AttemptLifecycleStatus.PARTIAL,
        AttemptLifecycleStatus.FAILED,
        AttemptLifecycleStatus.CANCELLED,
        AttemptLifecycleStatus.SUPERSEDED,
    }
)


class RunIdentitySchema(_FrozenLifecycleContract):
    schema_version: Literal["run_identity_v1"] = "run_identity_v1"
    run_id: NonEmptyStr
    task_id: NonEmptyStr
    tenant_id: NonEmptyStr
    canonical_request_sha256: Sha256Hex


class RunCompletionAggregateResult(str, Enum):
    SUCCESS = "SUCCESS"


class RunCompletionExactRefEnvelopeSchema(_FrozenLifecycleContract):
    """Dependency-neutral exact-ref envelope for completion inputs.

    Core lifecycle intentionally does not import Post-write schemas. The higher
    integration runtime constructs these envelopes from already-resolved exact
    refs and this schema hash-binds the canonical ref payload.
    """

    schema_version: Literal["run_completion_exact_ref_envelope_v1"] = (
        "run_completion_exact_ref_envelope_v1"
    )
    source_authority: NonEmptyStr
    source_kind: NonEmptyStr
    source_scope_ref: NonEmptyStr
    exact_ref_payload: dict[str, Any]
    exact_ref_schema_version: NonEmptyStr
    exact_ref_sha256: Sha256Hex

    @model_validator(mode="after")
    def _exact_ref_integrity(self) -> "RunCompletionExactRefEnvelopeSchema":
        if not self.exact_ref_payload:
            raise ValueError("exact_ref_payload must be non-empty")
        payload_schema = str(self.exact_ref_payload.get("schema_version") or "").strip()
        if payload_schema and payload_schema != self.exact_ref_schema_version:
            raise ValueError("exact ref schema version mismatch")
        if canonical_sha256(self.exact_ref_payload) != self.exact_ref_sha256:
            raise ValueError("exact completion ref hash mismatch")
        return self


class RunCompletionLogicalOperationRefSchema(_FrozenLifecycleContract):
    schema_version: Literal["run_completion_logical_operation_ref_v1"] = (
        "run_completion_logical_operation_ref_v1"
    )
    logical_operation_id: NonEmptyStr
    operation_kind: Literal["RUN_COMPLETION_ASSESSMENT_COMMIT"] = (
        "RUN_COMPLETION_ASSESSMENT_COMMIT"
    )
    run_id: NonEmptyStr
    operation_scope_ref: NonEmptyStr

    @model_validator(mode="after")
    def _operation_integrity(self) -> "RunCompletionLogicalOperationRefSchema":
        expected = "run-completion-op://sha256/" + canonical_sha256(
            {
                "operation_kind": self.operation_kind,
                "run_id": self.run_id,
                "operation_scope_ref": self.operation_scope_ref,
            }
        )
        if self.logical_operation_id != expected:
            raise ValueError("RunCompletion logical operation identity mismatch")
        return self


class RunCompletionAssessmentRefSchema(_FrozenLifecycleContract):
    schema_version: Literal["run_completion_assessment_ref_v1"] = (
        "run_completion_assessment_ref_v1"
    )
    assessment_id: NonEmptyStr
    run_id: NonEmptyStr
    aggregate_result: RunCompletionAggregateResult
    assessment_contract_version: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    assessment_sha256: Sha256Hex


class RunCompletionAssessmentRecordSchema(_FrozenLifecycleContract):
    schema_version: Literal["run_completion_assessment_record_v1"] = (
        "run_completion_assessment_record_v1"
    )
    assessment_id: NonEmptyStr
    run_id: NonEmptyStr
    aggregate_result: RunCompletionAggregateResult
    # Approved I8 evaluated-only semantics: all-MISSING has no fake completion.
    section_completion_refs: tuple[RunCompletionExactRefEnvelopeSchema, ...]
    document_artifact_ref: RunCompletionExactRefEnvelopeSchema
    document_post_write_entry_ref: RunCompletionExactRefEnvelopeSchema
    post_write_control_decision_ref: RunCompletionExactRefEnvelopeSchema
    post_write_control_decision_hash: Sha256Hex
    post_write_mutation_closure_refs: tuple[
        RunCompletionExactRefEnvelopeSchema, ...
    ] = ()
    assessment_contract_version: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    assessment_sha256: Sha256Hex
    committed_operation_ref: RunCompletionLogicalOperationRefSchema

    @staticmethod
    def _canonical_ref_key(
        item: RunCompletionExactRefEnvelopeSchema,
    ) -> tuple[str, str, str, str]:
        return (
            item.source_authority,
            item.source_kind,
            item.source_scope_ref,
            item.exact_ref_sha256,
        )

    @model_validator(mode="after")
    def _assessment_integrity(self) -> "RunCompletionAssessmentRecordSchema":
        section_keys = [self._canonical_ref_key(item) for item in self.section_completion_refs]
        if section_keys != sorted(section_keys) or len(section_keys) != len(set(section_keys)):
            raise ValueError("section_completion_refs must be canonical ordered-unique")
        mutation_keys = [
            self._canonical_ref_key(item)
            for item in self.post_write_mutation_closure_refs
        ]
        if mutation_keys != sorted(mutation_keys) or len(mutation_keys) != len(set(mutation_keys)):
            raise ValueError(
                "post_write_mutation_closure_refs must be canonical ordered-unique"
            )
        control_hash = str(
            self.post_write_control_decision_ref.exact_ref_payload.get(
                "control_decision_hash"
            )
            or ""
        ).strip()
        if control_hash != self.post_write_control_decision_hash:
            raise ValueError("Post-write Control decision hash binding mismatch")

        identity_payload = {
            "run_id": self.run_id,
            "document_artifact_ref": self.document_artifact_ref.model_dump(mode="json"),
            "document_post_write_entry_ref": (
                self.document_post_write_entry_ref.model_dump(mode="json")
            ),
            "post_write_control_decision_ref": (
                self.post_write_control_decision_ref.model_dump(mode="json")
            ),
            "post_write_mutation_closure_refs": [
                item.model_dump(mode="json")
                for item in self.post_write_mutation_closure_refs
            ],
            "assessment_contract_version": self.assessment_contract_version,
        }
        expected_assessment_id = (
            "run-completion-assessment://sha256/" + canonical_sha256(identity_payload)
        )
        if self.assessment_id != expected_assessment_id:
            raise ValueError("RunCompletionAssessment identity mismatch")

        semantic_payload = {
            "run_id": self.run_id,
            "aggregate_result": self.aggregate_result.value,
            "section_completion_refs": [
                item.model_dump(mode="json") for item in self.section_completion_refs
            ],
            "document_artifact_ref": self.document_artifact_ref.model_dump(mode="json"),
            "document_post_write_entry_ref": (
                self.document_post_write_entry_ref.model_dump(mode="json")
            ),
            "post_write_control_decision_ref": (
                self.post_write_control_decision_ref.model_dump(mode="json")
            ),
            "post_write_control_decision_hash": self.post_write_control_decision_hash,
            "post_write_mutation_closure_refs": [
                item.model_dump(mode="json")
                for item in self.post_write_mutation_closure_refs
            ],
            "assessment_contract_version": self.assessment_contract_version,
        }
        if canonical_sha256(semantic_payload) != self.semantic_fingerprint:
            raise ValueError("RunCompletionAssessment semantic fingerprint mismatch")
        if (
            self.committed_operation_ref.run_id != self.run_id
            or self.committed_operation_ref.operation_scope_ref != self.assessment_id
        ):
            raise ValueError("RunCompletion assessment operation binding mismatch")
        record_payload = self.model_dump(mode="json")
        record_payload.pop("assessment_sha256", None)
        if canonical_sha256(record_payload) != self.assessment_sha256:
            raise ValueError("RunCompletionAssessment record hash mismatch")
        return self

    def as_ref(self) -> RunCompletionAssessmentRefSchema:
        return RunCompletionAssessmentRefSchema(
            assessment_id=self.assessment_id,
            run_id=self.run_id,
            aggregate_result=self.aggregate_result,
            assessment_contract_version=self.assessment_contract_version,
            semantic_fingerprint=self.semantic_fingerprint,
            assessment_sha256=self.assessment_sha256,
        )


class RunFinalizationBarrierRefSchema(_FrozenLifecycleContract):
    schema_version: Literal["run_finalization_barrier_ref_v1"] = (
        "run_finalization_barrier_ref_v1"
    )
    run_id: NonEmptyStr
    barrier_contract_version: Literal["v1"] = "v1"
    barrier_sha256: Sha256Hex


class RunFinalizationBarrierSchema(_FrozenLifecycleContract):
    """Exact coordination material durably embedded in the lifecycle ledger."""

    schema_version: Literal["run_finalization_barrier_v1"] = (
        "run_finalization_barrier_v1"
    )
    run_id: NonEmptyStr
    final_control_decision_ref: RunCompletionExactRefEnvelopeSchema
    barrier_contract_version: Literal["v1"] = "v1"
    barrier_sha256: Sha256Hex

    @model_validator(mode="after")
    def _barrier_integrity(self) -> "RunFinalizationBarrierSchema":
        if (
            self.final_control_decision_ref.source_authority != "POST_WRITE_CONTROL"
            or self.final_control_decision_ref.source_kind != "POST_WRITE_CONTROL_DECISION"
        ):
            raise ValueError("finalization barrier requires exact final Control envelope")
        payload = self.model_dump(mode="json")
        payload.pop("barrier_sha256")
        if canonical_sha256(payload) != self.barrier_sha256:
            raise ValueError("RunFinalizationBarrier hash mismatch")
        return self

    def as_ref(self) -> RunFinalizationBarrierRefSchema:
        return RunFinalizationBarrierRefSchema(
            run_id=self.run_id,
            barrier_contract_version=self.barrier_contract_version,
            barrier_sha256=self.barrier_sha256,
        )


class RunLifecycleRecordSchema(_FrozenLifecycleContract):
    schema_version: Literal["run_lifecycle_record_v2"] = "run_lifecycle_record_v2"
    identity: RunIdentitySchema
    status: RunLifecycleStatus
    lifecycle_revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    terminal_reason_ref: NonEmptyStr | None = None
    run_completion_assessment_ref: RunCompletionAssessmentRefSchema | None = None
    run_finalization_barrier_ref: RunFinalizationBarrierRefSchema | None = None
    run_finalization_barrier: RunFinalizationBarrierSchema | None = None

    @field_validator("created_at", "updated_at", "started_at", "finished_at")
    @classmethod
    def _timestamps_are_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("lifecycle timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _lifecycle_is_consistent(self) -> "RunLifecycleRecordSchema":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        terminal = self.status in RUN_TERMINAL_STATUSES
        if terminal != (self.finished_at is not None):
            raise ValueError("finished_at must exist exactly for terminal Run status")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        barrier = self.run_finalization_barrier
        barrier_ref = self.run_finalization_barrier_ref
        if (barrier is None) != (barrier_ref is None):
            raise ValueError("finalization barrier material and ref must bind exactly")
        if barrier is not None:
            if barrier.run_id != self.identity.run_id or barrier.as_ref() != barrier_ref:
                raise ValueError("finalization barrier Run/ref binding mismatch")
            if self.status not in {
                RunLifecycleStatus.FINALIZING, RunLifecycleStatus.SUCCESS,
                RunLifecycleStatus.FAILED, RunLifecycleStatus.CANCELLED,
            }:
                raise ValueError("Run status cannot carry active finalization barrier")
        if self.status in {RunLifecycleStatus.FINALIZING, RunLifecycleStatus.SUCCESS}:
            if barrier is None:
                raise ValueError("FINALIZING/SUCCESS Run requires exact finalization barrier")
        if self.status is RunLifecycleStatus.SUCCESS:
            if self.run_completion_assessment_ref is None:
                raise ValueError(
                    "SUCCESS Run requires exact RunCompletionAssessmentRef"
                )
            if (
                self.run_completion_assessment_ref.run_id
                != self.identity.run_id
                or self.run_completion_assessment_ref.aggregate_result
                is not RunCompletionAggregateResult.SUCCESS
            ):
                raise ValueError(
                    "SUCCESS Run assessment ref belongs to another/non-success completion"
                )
        elif self.run_completion_assessment_ref is not None:
            raise ValueError(
                "only SUCCESS Run lifecycle may bind RunCompletionAssessmentRef"
            )
        return self


class AttemptIdentitySchema(_FrozenLifecycleContract):
    schema_version: Literal["attempt_identity_v1"] = "attempt_identity_v1"
    attempt_id: NonEmptyStr
    run_id: NonEmptyStr
    attempt_scope_ref: NonEmptyStr
    attempt_ordinal: int = Field(ge=1)
    input_snapshot_version: NonEmptyStr


class AttemptLifecycleRecordSchema(_FrozenLifecycleContract):
    schema_version: Literal["attempt_lifecycle_record_v1"] = (
        "attempt_lifecycle_record_v1"
    )
    identity: AttemptIdentitySchema
    status: AttemptLifecycleStatus
    lifecycle_revision: int = Field(ge=0)
    supersedes_attempt_id: NonEmptyStr | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_ref: NonEmptyStr | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _attempt_timestamps_are_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("attempt timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _attempt_lifecycle_is_consistent(self) -> "AttemptLifecycleRecordSchema":
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        terminal = self.status in ATTEMPT_TERMINAL_STATUSES
        if terminal != (self.finished_at is not None):
            raise ValueError("finished_at must exist exactly for terminal Attempt status")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        if self.supersedes_attempt_id == self.identity.attempt_id:
            raise ValueError("Attempt cannot supersede itself")
        return self


class ActiveExecutionLineageSchema(_FrozenLifecycleContract):
    schema_version: Literal["active_execution_lineage_v1"] = (
        "active_execution_lineage_v1"
    )
    run_id: NonEmptyStr
    run_lifecycle_revision: int = Field(ge=0)
    run_status: RunLifecycleStatus
    attempt_scope_ref: NonEmptyStr
    attempt_id: NonEmptyStr
    attempt_lifecycle_revision: int = Field(ge=0)
    attempt_status: AttemptLifecycleStatus
    input_snapshot_version: NonEmptyStr

    @property
    def commit_eligible(self) -> bool:
        return (
            self.run_status is RunLifecycleStatus.RUNNING
            and self.attempt_status is AttemptLifecycleStatus.RUNNING
        )


__all__ = [
    "ATTEMPT_TERMINAL_STATUSES",
    "RUN_TERMINAL_STATUSES",
    "ActiveExecutionLineageSchema",
    "AttemptIdentitySchema",
    "AttemptLifecycleRecordSchema",
    "AttemptLifecycleStatus",
    "RunCompletionAggregateResult",
    "RunCompletionAssessmentRecordSchema",
    "RunCompletionAssessmentRefSchema",
    "RunCompletionExactRefEnvelopeSchema",
    "RunCompletionLogicalOperationRefSchema",
    "RunFinalizationBarrierRefSchema",
    "RunFinalizationBarrierSchema",
    "RunIdentitySchema",
    "RunLifecycleRecordSchema",
    "RunLifecycleStatus",
]
