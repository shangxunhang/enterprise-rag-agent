"""Target pre-write enforcement, Recovery, and routing carrier contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.pre_write_admission import (
    WriterAdmissionDecisionRefSchema,
    WriterAdmissionFactAssessmentRefSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)


_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _duplicates(values: tuple[str, ...]) -> bool:
    return len(values) != len(set(values))


class EnforcementBasis(str, Enum):
    BUSINESS_ADMISSION = "business_admission"
    TECHNICAL_INTEGRITY = "technical_integrity"


class EnforcementAction(str, Enum):
    CONTINUE = "continue"
    BLOCK = "block"


class EnforcementResultSchema(_FrozenSchema):
    schema_version: Literal["writer_admission_enforcement_result_v1"] = (
        "writer_admission_enforcement_result_v1"
    )
    contract_version: Literal["writer_admission_enforcement_v1"] = (
        "writer_admission_enforcement_v1"
    )
    decision_ref: WriterAdmissionDecisionRefSchema | None = None
    binding_ref: WriterGateBindingRefSchema | None = None
    mode: Literal["strict", "shadow"]
    action: EnforcementAction
    would_block_in_strict: bool
    enforcement_basis: EnforcementBasis

    @model_validator(mode="after")
    def _validate_structure(self) -> "EnforcementResultSchema":
        if self.enforcement_basis is EnforcementBasis.TECHNICAL_INTEGRITY:
            if self.action is not EnforcementAction.BLOCK:
                raise ValueError("technical integrity always blocks")
            if not self.would_block_in_strict:
                raise ValueError("technical integrity would block in strict mode")
        elif self.decision_ref is None or self.binding_ref is None:
            raise ValueError("business enforcement requires decision and binding refs")
        if self.action is EnforcementAction.BLOCK and not self.would_block_in_strict:
            raise ValueError("a blocking action must also block in strict mode")
        if self.mode == "strict":
            expected = (
                EnforcementAction.BLOCK
                if self.would_block_in_strict
                else EnforcementAction.CONTINUE
            )
            if self.action is not expected:
                raise ValueError("strict mode must apply the strict consequence")
        elif self.enforcement_basis is EnforcementBasis.BUSINESS_ADMISSION:
            if self.action is not EnforcementAction.CONTINUE:
                raise ValueError("shadow business enforcement must continue")
        return self


class RecoveryRoundOutcome(str, Enum):
    NOT_STARTED = "not_started"
    SUCCESS = "success"
    PROGRESS = "progress"
    NO_PROGRESS = "no_progress"
    TECHNICAL_FAILURE = "technical_failure"


class RecoveryExhaustionReason(str, Enum):
    ROUND_BUDGET_EXHAUSTED = "round_budget_exhausted"
    NO_PROGRESS_EXHAUSTED = "no_progress_exhausted"


class RecoveryRuntimeStateSchema(_FrozenSchema):
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr
    recovery_state_revision: int = Field(ge=0)
    recovery_rounds_used: int = Field(ge=0)
    recovery_rounds_limit: int = Field(ge=0)
    consecutive_no_progress_rounds: int = Field(ge=0)
    no_progress_rounds_limit: int = Field(ge=1)
    last_round_outcome: RecoveryRoundOutcome

    @model_validator(mode="after")
    def _counter_relationships(self) -> "RecoveryRuntimeStateSchema":
        if self.consecutive_no_progress_rounds > self.recovery_rounds_used:
            raise ValueError(
                "consecutive_no_progress_rounds cannot exceed recovery_rounds_used"
            )
        return self


class RecoveryTargetProjection(_FrozenSchema):
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr
    decision_ref: WriterAdmissionDecisionRefSchema
    binding_ref: WriterGateBindingRefSchema
    fact_assessment_ref: WriterAdmissionFactAssessmentRefSchema
    requirements_sha256: Annotated[
        str,
        StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64),
    ]
    expected_recovery_state_revision: int = Field(ge=0)
    blocking_requirement_ids: tuple[_NonEmptyStr, ...]
    blocking_criterion_ids: tuple[_NonEmptyStr, ...]
    unresolved_conflict_criterion_ids: tuple[_NonEmptyStr, ...]
    indeterminate_requirement_ids: tuple[_NonEmptyStr, ...]
    indeterminate_criterion_ids: tuple[_NonEmptyStr, ...]

    @model_validator(mode="after")
    def _lists_are_unique(self) -> "RecoveryTargetProjection":
        for field_name in (
            "blocking_requirement_ids",
            "blocking_criterion_ids",
            "unresolved_conflict_criterion_ids",
            "indeterminate_requirement_ids",
            "indeterminate_criterion_ids",
        ):
            if _duplicates(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be duplicate-free")
        return self


class RecoveryControlFactsSchema(_FrozenSchema):
    schema_version: Literal["writer_recovery_control_facts_v1"] = (
        "writer_recovery_control_facts_v1"
    )
    contract_version: Literal["writer_recovery_control_v1"] = (
        "writer_recovery_control_v1"
    )
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr
    decision_ref: WriterAdmissionDecisionRefSchema
    binding_ref: WriterGateBindingRefSchema
    recovery_state_revision: int = Field(ge=0)
    recovery_available: bool
    recovery_exhausted: bool
    recovery_rounds_used: int = Field(ge=0)
    recovery_rounds_limit: int = Field(ge=0)
    consecutive_no_progress_rounds: int = Field(ge=0)
    no_progress_rounds_limit: int = Field(ge=1)
    last_round_outcome: RecoveryRoundOutcome
    exhaustion_reason: RecoveryExhaustionReason | None = None

    @model_validator(mode="after")
    def _validate_structure(self) -> "RecoveryControlFactsSchema":
        if self.recovery_available and self.recovery_exhausted:
            raise ValueError("recovery cannot be both available and exhausted")
        if self.recovery_exhausted != (self.exhaustion_reason is not None):
            raise ValueError("exhaustion_reason must match recovery_exhausted")
        if self.consecutive_no_progress_rounds > self.recovery_rounds_used:
            raise ValueError("no-progress count cannot exceed used rounds")
        return self


class EnforcementSnapshotRefSchema(_FrozenSchema):
    schema_version: Literal["writer_admission_enforcement_result_v1"] = (
        "writer_admission_enforcement_result_v1"
    )
    contract_version: Literal["writer_admission_enforcement_v1"] = (
        "writer_admission_enforcement_v1"
    )
    mode: Literal["strict", "shadow"]


class RecoveryControlSnapshotRefSchema(_FrozenSchema):
    schema_version: Literal["writer_recovery_control_facts_v1"] = (
        "writer_recovery_control_facts_v1"
    )
    contract_version: Literal["writer_recovery_control_v1"] = (
        "writer_recovery_control_v1"
    )
    recovery_state_revision: int = Field(ge=0)


class PreWriteRoute(str, Enum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    FALLBACK = "fallback"


class PreWriteRouteDecisionSchema(_FrozenSchema):
    schema_version: Literal["pre_write_route_decision_v1"] = (
        "pre_write_route_decision_v1"
    )
    contract_version: Literal["pre_write_routing_v1"] = "pre_write_routing_v1"
    route_decision_id: _NonEmptyStr
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: _NonEmptyStr
    decision_ref: WriterAdmissionDecisionRefSchema
    binding_ref: WriterGateBindingRefSchema
    enforcement_ref: EnforcementSnapshotRefSchema
    recovery_control_ref: RecoveryControlSnapshotRefSchema
    selected_route: PreWriteRoute


class PreWriteRouteDecisionRefSchema(_FrozenSchema):
    route_decision_id: _NonEmptyStr
    route_decision_schema_version: Literal["pre_write_route_decision_v1"] = (
        "pre_write_route_decision_v1"
    )
    route_decision_contract_version: Literal["pre_write_routing_v1"] = (
        "pre_write_routing_v1"
    )


__all__ = [
    "EnforcementAction",
    "EnforcementBasis",
    "EnforcementResultSchema",
    "EnforcementSnapshotRefSchema",
    "PreWriteRoute",
    "PreWriteRouteDecisionRefSchema",
    "PreWriteRouteDecisionSchema",
    "RecoveryControlFactsSchema",
    "RecoveryControlSnapshotRefSchema",
    "RecoveryExhaustionReason",
    "RecoveryRoundOutcome",
    "RecoveryRuntimeStateSchema",
    "RecoveryTargetProjection",
]
