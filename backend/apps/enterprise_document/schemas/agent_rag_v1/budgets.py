"""节点级、兼容预算投影与 Multi-Agent V3 canonical Budget contracts。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt, field_validator, model_serializer, model_validator

from core.persistence.crash_safe_jsonl import canonical_json_sha256

from ._base import ContractSchema


# ---------------------------------------------------------------------------
# Current/legacy compatibility projections.
# These remain readable by existing Workflow composition roots until the later
# cutover barrier.  They are not canonical Multi-Agent V3 Budget truth.
# ---------------------------------------------------------------------------


class TaskBudgetLimitsSchema(ContractSchema):
    schema_version: str = "task_budget_limits_v1"
    max_model_calls: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_total_replans: int = Field(ge=0)
    max_total_rewrites: int = Field(ge=0)
    max_document_repair_tasks: int = Field(ge=0)
    max_wall_clock_seconds: int = Field(gt=0)


class BudgetVectorSchema(ContractSchema):
    """Legacy five-dimension projection used by the pre-Target workflow."""

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    total_replans: int = Field(default=0, ge=0)
    total_rewrites: int = Field(default=0, ge=0)
    document_repair_tasks: int = Field(default=0, ge=0)


class LegacyBudgetReservationSchema(ContractSchema):
    schema_version: str = "budget_reservation_v1"
    reservation_id: str
    task_id: str
    requested: BudgetVectorSchema
    settled: BudgetVectorSchema = Field(default_factory=BudgetVectorSchema)
    status: Literal["active", "settled", "released"] = "active"


class BudgetSnapshotSchema(ContractSchema):
    """Compatibility/reporting projection; never a canonical restore source."""

    schema_version: str = "budget_snapshot_v1"
    task_id: str
    limits: TaskBudgetLimitsSchema
    reserved: BudgetVectorSchema
    used: BudgetVectorSchema
    draining: bool = False
    elapsed_seconds: float = Field(default=0.0, ge=0)


# ---------------------------------------------------------------------------
# Multi-Agent V3 canonical Budget contracts.
# ---------------------------------------------------------------------------


CANONICAL_BUDGET_DIMENSIONS: tuple[str, ...] = (
    "logical_model_calls",
    "logical_tool_calls",
    "model_tokens_total",
    "research_rounds",
    "repair_attempts",
    "generation_attempts",
    "replan_attempts",
    "true_retry_attempts",
)

CanonicalBudgetDimension = Literal[
    "logical_model_calls",
    "logical_tool_calls",
    "model_tokens_total",
    "research_rounds",
    "repair_attempts",
    "generation_attempts",
    "replan_attempts",
    "true_retry_attempts",
]


class CanonicalBudgetVectorSchema(ContractSchema):
    """Exactly the eight frozen SIF-12 hard Budget dimensions."""

    logical_model_calls: int = Field(default=0, ge=0)
    logical_tool_calls: int = Field(default=0, ge=0)
    model_tokens_total: int = Field(default=0, ge=0)
    research_rounds: int = Field(default=0, ge=0)
    repair_attempts: int = Field(default=0, ge=0)
    generation_attempts: int = Field(default=0, ge=0)
    replan_attempts: int = Field(default=0, ge=0)
    true_retry_attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_known_dimensions_only(self) -> "CanonicalBudgetVectorSchema":
        if tuple(type(self).model_fields) != CANONICAL_BUDGET_DIMENSIONS:
            raise ValueError("canonical Budget vector dimension registry drift")
        return self

    def is_zero(self) -> bool:
        return all(int(getattr(self, name)) == 0 for name in CANONICAL_BUDGET_DIMENSIONS)


class BudgetReservationRefSchema(ContractSchema):
    """Replay-stable semantic identity for one Budget reservation occurrence."""

    schema_version: str = "budget_reservation_ref_v1"
    reservation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    reservation_kind: str = Field(min_length=1)
    owner_occurrence_ref: str = Field(min_length=1)
    scope_ref: str = Field(min_length=1)
    operation_ref: str = Field(min_length=1)
    attempt_id: str | None = None
    parent_reservation_id: str | None = None

    @model_validator(mode="after")
    def _normalize_optional_refs(self) -> "BudgetReservationRefSchema":
        for field_name in ("attempt_id", "parent_reservation_id"):
            value = getattr(self, field_name)
            if value is not None and not str(value).strip():
                raise ValueError(f"{field_name} cannot be blank")
        return self


class BudgetReservationSchema(ContractSchema):
    """Canonical ACTIVE/terminal reservation record."""

    schema_version: str = "budget_reservation_v2"
    reservation_ref: BudgetReservationRefSchema
    requested: CanonicalBudgetVectorSchema
    semantic_fingerprint: str = Field(min_length=64, max_length=64)
    status: Literal["active", "consumed", "released"] = "active"
    actual: CanonicalBudgetVectorSchema = Field(
        default_factory=CanonicalBudgetVectorSchema
    )
    created_against_budget_revision: int = Field(ge=0)
    terminal_budget_revision: int | None = Field(default=None, ge=0)
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _validate_terminal_shape(self) -> "BudgetReservationSchema":
        if any(ch not in "0123456789abcdef" for ch in self.semantic_fingerprint):
            raise ValueError("semantic_fingerprint must be lowercase SHA-256 hex")
        if self.status == "active":
            if self.terminal_budget_revision is not None:
                raise ValueError("active reservation cannot have terminal revision")
            if not self.actual.is_zero():
                raise ValueError("active reservation cannot expose actual usage")
        else:
            if self.terminal_budget_revision is None:
                raise ValueError("terminal reservation requires terminal revision")
            if self.status == "released" and not self.actual.is_zero():
                raise ValueError("released reservation must have zero actual usage")
        return self


class BudgetReservationSliceSchema(ContractSchema):
    """Deterministic sub-scope allocation under one canonical parent owner."""

    schema_version: str = "budget_reservation_slice_v1"
    slice_id: str = Field(min_length=1)
    parent_reservation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scope_ref: str = Field(min_length=1)
    allocation: CanonicalBudgetVectorSchema


class BudgetSettlementSchema(ContractSchema):
    """Terminal consumption/release fact for one canonical reservation."""

    schema_version: str = "budget_settlement_v1"
    reservation_id: str = Field(min_length=1)
    status: Literal["consumed", "released"]
    actual: CanonicalBudgetVectorSchema
    semantic_fingerprint: str = Field(min_length=64, max_length=64)
    settled_against_budget_revision: int = Field(ge=0)
    terminal_budget_revision: int = Field(ge=0)
    settled_at: datetime

    @model_validator(mode="after")
    def _validate_settlement(self) -> "BudgetSettlementSchema":
        if any(ch not in "0123456789abcdef" for ch in self.semantic_fingerprint):
            raise ValueError("semantic_fingerprint must be lowercase SHA-256 hex")
        if self.status == "released" and not self.actual.is_zero():
            raise ValueError("released settlement must have zero actual usage")
        return self


_MAX_TOPUP_VALUE = (1 << 63) - 1


def _strict_topup_vector(value):
    """New authorization amounts do not inherit old vector coercion rules."""
    if isinstance(value, CanonicalBudgetVectorSchema):
        value = value.model_dump(mode="python")
    if not isinstance(value, dict):
        raise ValueError("top-up vector must be an eight-dimensional object")
    if set(value) - set(CANONICAL_BUDGET_DIMENSIONS):
        raise ValueError("top-up vector contains an unknown dimension")
    for amount in value.values():
        if type(amount) is not int or not 0 <= amount <= _MAX_TOPUP_VALUE:
            raise ValueError("top-up amounts must be finite nonnegative integers")
    return value


def _lower_sha256(value: str) -> str:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("top-up proof must use lowercase SHA-256 hex")
    return value


class CanonicalBudgetTopupAuthorizationSchema(ContractSchema):
    """Immutable trusted command carrier; only an owner resolver can supply it."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["canonical_budget_topup_authorization_v1"] = "canonical_budget_topup_authorization_v1"
    authorization_kind: Literal["SINGLE_USE", "PREAUTHORIZED"]
    authorization_id: str = Field(min_length=1)
    authorization_ref: str = Field(min_length=1)
    authorization_sha256: str
    command_source: Literal["APPLICATION", "CLI", "API"]
    issuer: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    canonical_request_ref: str = Field(min_length=1)
    canonical_request_sha256: str
    semantic_operation_ref: str = Field(min_length=1)
    semantic_input_sha256: str
    artifact_version: str = Field(min_length=1)
    baseline_policy_sha256: str
    authorized_delta: CanonicalBudgetVectorSchema
    cumulative_ceiling: CanonicalBudgetVectorSchema
    single_use_wait_event_ref: str | None = None
    single_use_native_denial_sha256: str | None = None
    single_use_child_preimage_sha256: str | None = None
    expires_at: datetime | None = None
    revoked: StrictBool = False
    decision: Literal["APPROVED", "DENIED"] = "APPROVED"

    @field_validator("*")
    @classmethod
    def _nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("authorization provenance/binding cannot be blank")
        return value

    @field_validator("authorized_delta", "cumulative_ceiling", mode="before")
    @classmethod
    def _finite_vectors(cls, value):
        return _strict_topup_vector(value)

    @field_validator("authorization_sha256", "canonical_request_sha256", "semantic_input_sha256", "baseline_policy_sha256")
    @classmethod
    def _hashes(cls, value):
        return _lower_sha256(value)

    @model_validator(mode="after")
    def _scope_and_provenance(self):
        if self.authorized_delta.is_zero():
            raise ValueError("authorization must permit a positive finite delta")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("authorization expiry must be timezone-aware")
        single_fields = (self.single_use_wait_event_ref, self.single_use_native_denial_sha256,
                         self.single_use_child_preimage_sha256)
        if self.authorization_kind == "SINGLE_USE":
            if not all(single_fields):
                raise ValueError("single-use authorization requires exact wait/denial/Child binding")
            _lower_sha256(self.single_use_native_denial_sha256)
            _lower_sha256(self.single_use_child_preimage_sha256)
        elif any(value is not None for value in single_fields):
            raise ValueError("preauthorization root cannot impersonate one fixed debit")
        return self


class CanonicalBudgetTopupRequestSchema(ContractSchema):
    """One exact pending DOCUMENT debit, independent of client aliases."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["canonical_budget_topup_request_v1"] = "canonical_budget_topup_request_v1"
    approval_id: str = Field(min_length=1)
    authorization_ref: str = Field(min_length=1)
    authorization_sha256: str
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    canonical_request_ref: str = Field(min_length=1)
    canonical_request_sha256: str
    semantic_operation_ref: str = Field(min_length=1)
    semantic_input_sha256: str
    artifact_version: str = Field(min_length=1)
    native_denial_ref: str = Field(min_length=1)
    native_denial_sha256: str
    phase_proof_ref: str = Field(min_length=1)
    phase_proof_sha256: str
    wait_event_ref: str = Field(min_length=1)
    wait_event_sha256: str
    child_preimage_sha256: str
    baseline_policy_sha256: str
    expected_budget_revision: StrictInt = Field(ge=0)
    expected_lifecycle_revision: StrictInt = Field(ge=0)
    delta: CanonicalBudgetVectorSchema
    required: CanonicalBudgetVectorSchema

    @field_validator("*")
    @classmethod
    def _nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("top-up identity/binding cannot be blank")
        return value

    @field_validator("delta", "required", mode="before")
    @classmethod
    def _finite_vectors(cls, value):
        return _strict_topup_vector(value)

    @field_validator("authorization_sha256", "canonical_request_sha256", "semantic_input_sha256",
                     "native_denial_sha256", "phase_proof_sha256", "wait_event_sha256",
                     "child_preimage_sha256", "baseline_policy_sha256")
    @classmethod
    def _hashes(cls, value):
        return _lower_sha256(value)

    @model_validator(mode="after")
    def _positive_delta(self):
        if self.delta.is_zero() or self.required.is_zero():
            raise ValueError("top-up requires a positive delta and actual pending allowance")
        return self


def canonical_budget_topup_consumption_key(request, authorization) -> str:
    root = {"authorization_ref": authorization.authorization_ref,
            "authorization_sha256": authorization.authorization_sha256}
    if authorization.authorization_kind == "PREAUTHORIZED":
        root["debit"] = {
            "task_id": request.task_id, "run_id": request.run_id,
            "wait_event_ref": request.wait_event_ref, "wait_event_sha256": request.wait_event_sha256,
            "native_denial_ref": request.native_denial_ref, "native_denial_sha256": request.native_denial_sha256,
            "semantic_operation_ref": request.semantic_operation_ref,
            "semantic_input_sha256": request.semantic_input_sha256,
            "artifact_version": request.artifact_version,
        }
    return canonical_json_sha256(root)


def canonical_budget_topup_fingerprint(request, authorization) -> str:
    values = request.model_dump(mode="json")
    values.pop("approval_id")
    return canonical_json_sha256({"request": values, "authorization": authorization.model_dump(mode="json")})


class CanonicalBudgetTopupGrantSchema(ContractSchema):
    """Accepted additive fact, not mutable capacity or a dispatch permission."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["canonical_budget_topup_grant_v1"] = "canonical_budget_topup_grant_v1"
    request: CanonicalBudgetTopupRequestSchema
    authorization: CanonicalBudgetTopupAuthorizationSchema
    authorization_consumption_key: str
    semantic_fingerprint: str
    created_against_budget_revision: StrictInt = Field(ge=0)
    resulting_budget_revision: StrictInt = Field(ge=1)
    accepted_at: datetime

    @model_validator(mode="after")
    def _validate_native_grant(self):
        if self.resulting_budget_revision != self.created_against_budget_revision + 1:
            raise ValueError("top-up grant revision must advance exactly once")
        if self.request.expected_budget_revision != self.created_against_budget_revision:
            raise ValueError("top-up grant must retain its exact Budget CAS preimage")
        if self.authorization_consumption_key != canonical_budget_topup_consumption_key(self.request, self.authorization):
            raise ValueError("top-up authorization consumption key mismatch")
        if self.semantic_fingerprint != canonical_budget_topup_fingerprint(self.request, self.authorization):
            raise ValueError("top-up semantic fingerprint mismatch")
        if self.accepted_at.tzinfo is None:
            raise ValueError("top-up acceptance must be timezone-aware")
        return self

    @property
    def grant_ref(self) -> str:
        return f"budget_grant:{self.authorization_consumption_key}:{self.semantic_fingerprint}"

    @property
    def grant_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class CanonicalBudgetAdmissionDenialSchema(ContractSchema):
    """Positive native capacity fact captured under the admission owner lock."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["canonical_budget_admission_denial_v1"] = "canonical_budget_admission_denial_v1"
    task_id: str
    run_id: str
    budget_revision: StrictInt = Field(ge=0)
    budget_state_sha256: str
    reservation_ref: BudgetReservationRefSchema
    requested: CanonicalBudgetVectorSchema
    denied_dimension: CanonicalBudgetDimension
    required: StrictInt = Field(gt=0)
    remaining: StrictInt = Field(ge=0)
    baseline_limits: CanonicalBudgetVectorSchema
    effective_limits: CanonicalBudgetVectorSchema
    available: CanonicalBudgetVectorSchema
    draining: bool

    @model_validator(mode="after")
    def _exact_denial(self):
        _lower_sha256(self.budget_state_sha256)
        if self.task_id != self.reservation_ref.task_id or self.run_id != self.reservation_ref.run_id:
            raise ValueError("denial account and reservation identity mismatch")
        if self.required != getattr(self.requested, self.denied_dimension):
            raise ValueError("denial required amount mismatch")
        if self.remaining != getattr(self.available, self.denied_dimension) or self.remaining >= self.required:
            raise ValueError("denial does not prove finite capacity refusal")
        return self


class CanonicalBudgetStateSchema(ContractSchema):
    """Durable implementation carrier for the single canonical Budget owner."""

    schema_version: Literal["canonical_budget_state_v1", "canonical_budget_state_v2"] = "canonical_budget_state_v1"
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    limits: CanonicalBudgetVectorSchema
    used: CanonicalBudgetVectorSchema
    active_reserved: CanonicalBudgetVectorSchema
    remaining: CanonicalBudgetVectorSchema
    exhausted_dimensions: list[CanonicalBudgetDimension] = Field(
        default_factory=list
    )
    draining: bool = False
    revision: int = Field(ge=0)
    started_at: datetime
    deadline_at: datetime
    reservations: list[BudgetReservationSchema] = Field(default_factory=list)
    grant_history: list[CanonicalBudgetTopupGrantSchema] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _preserve_v1_serialization(self, handler):
        payload = handler(self)
        if self.schema_version == "canonical_budget_state_v1":
            payload.pop("grant_history", None)
        return payload

    @property
    def effective_limits(self) -> CanonicalBudgetVectorSchema:
        values = self.limits.model_dump(mode="python")
        for grant in self.grant_history:
            for name in CANONICAL_BUDGET_DIMENSIONS:
                values[name] += getattr(grant.request.delta, name)
                if values[name] > _MAX_TOPUP_VALUE:
                    raise ValueError("effective top-up limit overflow")
        return CanonicalBudgetVectorSchema(**values)

    @model_validator(mode="after")
    def _validate_state(self) -> "CanonicalBudgetStateSchema":
        if self.deadline_at <= self.started_at:
            raise ValueError("canonical Budget deadline must be after started_at")
        if len(self.exhausted_dimensions) != len(set(self.exhausted_dimensions)):
            raise ValueError("exhausted_dimensions must be unique")
        reservation_ids = [
            item.reservation_ref.reservation_id for item in self.reservations
        ]
        if len(reservation_ids) != len(set(reservation_ids)):
            raise ValueError("canonical Budget reservation ids must be unique")
        if self.schema_version == "canonical_budget_state_v1" and self.grant_history:
            raise ValueError("v1 Budget state cannot carry additive grants")
        if self.schema_version == "canonical_budget_state_v2" and not self.grant_history:
            raise ValueError("v2 Budget state requires immutable grant history")
        consumption_keys = [item.authorization_consumption_key for item in self.grant_history]
        approval_ids = [item.request.approval_id for item in self.grant_history]
        if len(consumption_keys) != len(set(consumption_keys)) or len(approval_ids) != len(set(approval_ids)):
            raise ValueError("canonical Budget grants contain duplicate authorization/client identity")
        return self
