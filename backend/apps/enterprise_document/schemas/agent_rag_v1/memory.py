"""Canonical SIF-09 Durable Run Memory contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import json
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from ._base import ContractSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Ref = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^sha256:[0-9a-f]{64}$"),
]


class _FrozenMemoryContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class DomainMemoryType(str, Enum):
    FACT = "fact"
    EVIDENCE = "evidence"
    VERIFIED_CLAIM = "verified_claim"
    RESEARCH_HISTORY = "research_history"
    VERIFIER_ISSUE = "verifier_issue"
    ARTIFACT_REFERENCE = "artifact_reference"


class MemoryFreshnessEligibility(str, Enum):
    ACTIVE_RECALL = "ACTIVE_RECALL"
    HISTORICAL_ONLY = "HISTORICAL_ONLY"
    INELIGIBLE = "INELIGIBLE"


class MemoryRecallMode(str, Enum):
    ACTIVE_ONLY = "ACTIVE_ONLY"
    HISTORICAL_ONLY = "HISTORICAL_ONLY"
    ACTIVE_AND_HISTORICAL = "ACTIVE_AND_HISTORICAL"


class MemoryQueryPurpose(str, Enum):
    NORMAL_RECALL = "NORMAL_RECALL"
    HISTORICAL_RECALL = "HISTORICAL_RECALL"
    AUDIT_RECALL = "AUDIT_RECALL"


class MemorySelectorOperator(str, Enum):
    EQ = "EQ"
    IN = "IN"
    CONTAINS_SET_MEMBER = "CONTAINS_SET_MEMBER"


class MemoryLogicalOperationRefSchema(_FrozenMemoryContract):
    """Persistence mirror of core.runtime.side_effects.LogicalOperationRef."""

    schema_version: Literal["memory_logical_operation_ref_v1"] = (
        "memory_logical_operation_ref_v1"
    )
    logical_operation_id: NonEmptyStr
    operation_kind: NonEmptyStr
    run_id: NonEmptyStr
    operation_scope_ref: NonEmptyStr
    attempt_scope_ref: NonEmptyStr | None = None
    attempt_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _attempt_pair(self) -> "MemoryLogicalOperationRefSchema":
        if bool(self.attempt_scope_ref) != bool(self.attempt_id):
            raise ValueError(
                "attempt_scope_ref and attempt_id must be supplied together"
            )
        return self


class DurableMemoryRefSchema(_FrozenMemoryContract):
    schema_version: Literal["durable_memory_ref_v1"] = "durable_memory_ref_v1"
    run_id: NonEmptyStr
    memory_type: DomainMemoryType
    scope_ref: NonEmptyStr
    memory_id: NonEmptyStr


class CanonicalMemorySourceRefEnvelopeSchema(_FrozenMemoryContract):
    schema_version: Literal["canonical_memory_source_ref_envelope_v1"] = (
        "canonical_memory_source_ref_envelope_v1"
    )
    source_authority: NonEmptyStr
    source_kind: NonEmptyStr
    source_scope_ref: NonEmptyStr
    logical_source_ref: NonEmptyStr
    exact_source_ref: dict[str, Any]
    exact_source_ref_schema_id: NonEmptyStr
    exact_source_ref_sha256: Sha256Ref
    source_semantic_fingerprint: Sha256Ref

    @field_validator("exact_source_ref")
    @classmethod
    def _exact_source_ref_non_empty(cls, value):
        if not value:
            raise ValueError("exact_source_ref must be a non-empty canonical ref object")
        return value


class MemorySourceBindingSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_source_binding_v1"] = "memory_source_binding_v1"
    primary_source: CanonicalMemorySourceRefEnvelopeSchema
    dependency_sources: tuple[CanonicalMemorySourceRefEnvelopeSchema, ...] = ()
    source_set_sha256: Sha256Ref
    currentness_resolver_id: NonEmptyStr
    currentness_policy_version: NonEmptyStr
    source_binding_semantic_fingerprint: Sha256Ref

    @model_validator(mode="after")
    def _dependencies_are_canonical(self) -> "MemorySourceBindingSchema":
        keys = [
            (
                item.source_authority,
                item.source_kind,
                item.source_scope_ref,
                item.logical_source_ref,
                json.dumps(
                    item.exact_source_ref,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            )
            for item in self.dependency_sources
        ]
        if keys != sorted(keys):
            raise ValueError("dependency_sources must be canonically sorted")
        if len(keys) != len(set(keys)):
            raise ValueError("dependency_sources must be unique")
        return self


class MemoryProjectionPayloadEnvelopeSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_projection_payload_envelope_v1"] = (
        "memory_projection_payload_envelope_v1"
    )
    projection_schema_id: NonEmptyStr
    projection_schema_version: NonEmptyStr
    projection_payload: dict[str, Any]
    projection_payload_sha256: Sha256Ref


class DurableMemoryRecordSchema(_FrozenMemoryContract):
    schema_version: Literal["durable_memory_record_v1"] = "durable_memory_record_v1"
    memory_ref: DurableMemoryRefSchema
    source_binding: MemorySourceBindingSchema
    projection: MemoryProjectionPayloadEnvelopeSchema
    projection_policy_id: NonEmptyStr
    projection_policy_version: NonEmptyStr
    committed_operation_ref: MemoryLogicalOperationRefSchema
    record_semantic_fingerprint: Sha256Ref
    created_at: datetime

    @model_validator(mode="after")
    def _operation_is_memory_projection(self) -> "DurableMemoryRecordSchema":
        operation = self.committed_operation_ref
        if operation.operation_kind != "MEMORY_PROJECTION_COMMIT":
            raise ValueError("DurableMemoryRecord requires MEMORY_PROJECTION_COMMIT")
        if operation.run_id != self.memory_ref.run_id:
            raise ValueError("Memory operation belongs to another Run")
        if operation.operation_scope_ref != self.memory_ref.memory_id:
            raise ValueError("Memory operation scope must equal stable memory_id")
        if operation.attempt_scope_ref is not None or operation.attempt_id is not None:
            raise ValueError("Memory projection commit is Run/source derived, not Attempt-owned")
        return self


class MemoryProjectionRequestSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_projection_request_v1"] = (
        "memory_projection_request_v1"
    )
    run_id: NonEmptyStr
    memory_type: DomainMemoryType
    scope_ref: NonEmptyStr
    primary_source_ref: CanonicalMemorySourceRefEnvelopeSchema
    dependency_source_refs: tuple[CanonicalMemorySourceRefEnvelopeSchema, ...] = ()
    projection_schema_id: NonEmptyStr
    projection_schema_version: NonEmptyStr
    projection_policy_id: NonEmptyStr
    projection_policy_version: NonEmptyStr
    trigger_ref: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _dependencies_are_canonical(self) -> "MemoryProjectionRequestSchema":
        keys = [
            (
                item.source_authority,
                item.source_kind,
                item.source_scope_ref,
                item.logical_source_ref,
                json.dumps(
                    item.exact_source_ref,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            )
            for item in self.dependency_source_refs
        ]
        if keys != sorted(keys):
            raise ValueError("dependency_source_refs must be canonically sorted")
        if len(keys) != len(set(keys)):
            raise ValueError("dependency_source_refs must be unique")
        return self


class MemoryFreshnessAssessmentSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_freshness_assessment_v1"] = (
        "memory_freshness_assessment_v1"
    )
    memory_ref: DurableMemoryRefSchema
    assessed_source_set_sha256: Sha256Ref
    eligibility: MemoryFreshnessEligibility
    primary_source_current: bool
    dependency_sources_current: bool | Literal["NOT_APPLICABLE"]
    lifecycle_eligible: bool | Literal["NOT_APPLICABLE"]
    source_resolvable: bool
    integrity_valid: bool
    assessment_reason_codes: tuple[NonEmptyStr, ...]
    assessed_with_resolver_id: NonEmptyStr
    assessed_with_policy_version: NonEmptyStr
    assessed_at: datetime

    @model_validator(mode="after")
    def _reason_codes_unique(self) -> "MemoryFreshnessAssessmentSchema":
        if len(self.assessment_reason_codes) != len(set(self.assessment_reason_codes)):
            raise ValueError("assessment_reason_codes must be ordered-unique")
        if self.eligibility is MemoryFreshnessEligibility.ACTIVE_RECALL:
            if not self.source_resolvable or not self.integrity_valid:
                raise ValueError("ACTIVE_RECALL requires resolvable integrity-valid source")
            if not self.primary_source_current:
                raise ValueError("ACTIVE_RECALL requires current primary source")
            if self.dependency_sources_current is False:
                raise ValueError("ACTIVE_RECALL cannot have stale dependencies")
            if self.lifecycle_eligible is False:
                raise ValueError("ACTIVE_RECALL cannot have ineligible lifecycle")
        return self


class MemorySelectorIndexRecordSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_selector_index_record_v1"] = (
        "memory_selector_index_record_v1"
    )
    memory_ref: DurableMemoryRefSchema
    source_memory_fingerprint: Sha256Ref
    selector_schema_id: NonEmptyStr
    selector_schema_version: NonEmptyStr
    selector_values: dict[str, Any]
    selector_values_sha256: Sha256Ref
    index_policy_id: NonEmptyStr
    index_policy_version: NonEmptyStr
    index_record_fingerprint: Sha256Ref
    built_at: datetime


class TypedMemorySelectorClauseSchema(_FrozenMemoryContract):
    schema_version: Literal["typed_memory_selector_clause_v1"] = (
        "typed_memory_selector_clause_v1"
    )
    selector_schema_id: NonEmptyStr
    selector_schema_version: NonEmptyStr
    key: NonEmptyStr
    operator: MemorySelectorOperator
    value: Any


class MemoryQuerySchema(_FrozenMemoryContract):
    schema_version: Literal["memory_query_v1"] = "memory_query_v1"
    run_id: NonEmptyStr
    scope_refs: tuple[NonEmptyStr, ...] = ()
    memory_types: tuple[DomainMemoryType, ...] = ()
    recall_mode: MemoryRecallMode = MemoryRecallMode.ACTIVE_ONLY
    source_kinds: tuple[NonEmptyStr, ...] = ()
    exact_source_refs: tuple[dict[str, Any], ...] = ()
    selector_clauses: tuple[TypedMemorySelectorClauseSchema, ...] = ()
    limit: int = Field(default=50, ge=1, le=200)
    cursor: NonEmptyStr | None = None
    query_purpose: MemoryQueryPurpose = MemoryQueryPurpose.NORMAL_RECALL

    @field_validator("scope_refs", "source_kinds")
    @classmethod
    def _canonical_string_filters(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Memory query filter lists must be ordered-unique")
        if tuple(value) != tuple(sorted(value)):
            raise ValueError("Memory query string filters must be canonically sorted")
        return value

    @field_validator("memory_types")
    @classmethod
    def _canonical_memory_types(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Memory query memory_types must be ordered-unique")
        if tuple(value) != tuple(sorted(value, key=lambda item: item.value)):
            raise ValueError("Memory query memory_types must be canonically sorted")
        return value

    @field_validator("exact_source_refs")
    @classmethod
    def _exact_refs_ordered_unique(cls, value):
        if any(not item for item in value):
            raise ValueError("exact_source_refs cannot contain an empty ref object")
        keys = [
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            for item in value
        ]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("exact_source_refs must be canonical ordered-unique")
        return value


class MemoryQueryResultItemSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_query_result_item_v1"] = (
        "memory_query_result_item_v1"
    )
    memory_record: DurableMemoryRecordSchema
    freshness_assessment: MemoryFreshnessAssessmentSchema

    @model_validator(mode="after")
    def _same_memory(self) -> "MemoryQueryResultItemSchema":
        if self.memory_record.memory_ref != self.freshness_assessment.memory_ref:
            raise ValueError("query item record/freshness memory_ref mismatch")
        if (
            self.memory_record.source_binding.source_set_sha256
            != self.freshness_assessment.assessed_source_set_sha256
        ):
            raise ValueError("query item freshness assessed different source set")
        if (
            self.freshness_assessment.eligibility
            is MemoryFreshnessEligibility.INELIGIBLE
        ):
            raise ValueError("INELIGIBLE Memory cannot be a normal query result item")
        return self


class MemoryQueryResultSchema(_FrozenMemoryContract):
    schema_version: Literal["memory_query_result_v1"] = "memory_query_result_v1"
    run_id: NonEmptyStr
    normalized_query_fingerprint: Sha256Ref
    items: tuple[MemoryQueryResultItemSchema, ...] = ()
    next_cursor: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _run_is_consistent(self) -> "MemoryQueryResultSchema":
        for item in self.items:
            if item.memory_record.memory_ref.run_id != self.run_id:
                raise ValueError("Memory query result contains another Run")
        keys = [
            (
                item.memory_record.memory_ref.memory_type.value,
                item.memory_record.memory_ref.scope_ref,
                item.memory_record.memory_ref.memory_id,
            )
            for item in self.items
        ]
        if keys != sorted(keys):
            raise ValueError("Memory query result ordering is not deterministic")
        return self


__all__ = [
    "CanonicalMemorySourceRefEnvelopeSchema",
    "DomainMemoryType",
    "DurableMemoryRecordSchema",
    "DurableMemoryRefSchema",
    "MemoryFreshnessAssessmentSchema",
    "MemoryFreshnessEligibility",
    "MemoryLogicalOperationRefSchema",
    "MemoryProjectionPayloadEnvelopeSchema",
    "MemoryProjectionRequestSchema",
    "MemoryQueryPurpose",
    "MemoryQueryResultItemSchema",
    "MemoryQueryResultSchema",
    "MemoryQuerySchema",
    "MemoryRecallMode",
    "MemorySelectorIndexRecordSchema",
    "MemorySelectorOperator",
    "MemorySourceBindingSchema",
    "TypedMemorySelectorClauseSchema",
]
