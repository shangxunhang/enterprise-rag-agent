"""Canonical Target contracts for pre-write Writer admission.

The models in this module are immutable structural carriers.  Business truth is
produced by the Step-2 assessment and admission-policy services, never here.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
_DecisionReason = Literal[
    "assessment_pending",
    "normal_admission_satisfied",
    "requirement_not_fully_supported",
    "requirement_indeterminate",
    "unresolved_evidence_conflict",
]


class _FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _duplicates(values: tuple[str, ...]) -> bool:
    return len(values) != len(set(values))


class EvidenceCriterionSchema(_FrozenSchema):
    criterion_id: _NonEmptyStr
    criterion: _NonEmptyStr


class EvidenceRequirementSchema(_FrozenSchema):
    requirement_id: _NonEmptyStr
    criteria: tuple[EvidenceCriterionSchema, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _criterion_ids_are_unique(self) -> "EvidenceRequirementSchema":
        ids = tuple(item.criterion_id for item in self.criteria)
        if _duplicates(ids):
            raise ValueError("criterion_id values must be unique")
        return self


class EvidenceRequirementsSnapshotSchema(_FrozenSchema):
    schema_version: Literal["evidence_requirements_snapshot_v1"] = (
        "evidence_requirements_snapshot_v1"
    )
    contract_version: Literal["evidence_requirement_semantics_v1"] = (
        "evidence_requirement_semantics_v1"
    )
    requirements_sha256: _Sha256Hex
    source_profile_ref: _NonEmptyStr
    source_task_ref: _NonEmptyStr
    requirements: tuple[EvidenceRequirementSchema, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _snapshot_ids_are_unique(self) -> "EvidenceRequirementsSnapshotSchema":
        requirement_ids = tuple(item.requirement_id for item in self.requirements)
        criterion_ids = tuple(
            criterion.criterion_id
            for requirement in self.requirements
            for criterion in requirement.criteria
        )
        if _duplicates(requirement_ids):
            raise ValueError("requirement_id values must be unique")
        if _duplicates(criterion_ids):
            raise ValueError(
                "criterion_id values must be globally unique within the snapshot"
            )
        return self


class CriterionEvidenceSupportState(str, Enum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    INDETERMINATE = "indeterminate"


class CriterionEvidenceFactSchema(_FrozenSchema):
    fact_id: _NonEmptyStr
    requirement_id: _NonEmptyStr
    criterion_id: _NonEmptyStr
    support_state: CriterionEvidenceSupportState
    evidence_ids: tuple[_NonEmptyStr, ...] = ()
    conflict_refs: tuple[_NonEmptyStr, ...] = ()
    reason_codes: tuple[_NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _validate_structure(self) -> "CriterionEvidenceFactSchema":
        for field_name in ("evidence_ids", "conflict_refs", "reason_codes"):
            if _duplicates(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be duplicate-free")
        if (
            self.support_state is CriterionEvidenceSupportState.SUPPORTED
            and not self.evidence_ids
        ):
            raise ValueError("supported criterion facts require evidence_ids")
        return self


class RequirementEvidenceFactSchema(_FrozenSchema):
    class SupportState(str, Enum):
        SUPPORTED = "supported"
        PARTIALLY_SUPPORTED = "partially_supported"
        NOT_SUPPORTED = "not_supported"
        INDETERMINATE = "indeterminate"

    fact_id: _NonEmptyStr
    requirement_id: _NonEmptyStr
    support_state: SupportState
    criterion_fact_ids: tuple[_NonEmptyStr, ...] = Field(min_length=1)
    conflict_refs: tuple[_NonEmptyStr, ...] = ()
    reason_codes: tuple[_NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _lists_are_unique(self) -> "RequirementEvidenceFactSchema":
        for field_name in ("criterion_fact_ids", "conflict_refs", "reason_codes"):
            if _duplicates(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be duplicate-free")
        return self


class EvidenceConflictFactSchema(_FrozenSchema):
    conflict_id: _NonEmptyStr
    criterion_id: _NonEmptyStr
    evidence_ids: tuple[_NonEmptyStr, ...] = Field(min_length=2)
    reason_codes: tuple[_NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _lists_are_unique(self) -> "EvidenceConflictFactSchema":
        if _duplicates(self.evidence_ids):
            raise ValueError("evidence_ids must be duplicate-free")
        if _duplicates(self.reason_codes):
            raise ValueError("reason_codes must be duplicate-free")
        return self


class WriterAdmissionAssessmentState(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class WriterAdmissionFactAssessmentSchema(_FrozenSchema):
    schema_version: Literal["writer_admission_fact_assessment_v1"] = (
        "writer_admission_fact_assessment_v1"
    )
    contract_version: Literal["writer_admission_fact_semantics_v1"] = (
        "writer_admission_fact_semantics_v1"
    )
    assessment_id: _NonEmptyStr
    assessment_state: WriterAdmissionAssessmentState
    requirements_schema_version: _NonEmptyStr
    requirements_contract_version: _NonEmptyStr
    requirements_sha256: _Sha256Hex
    evidence_bundle_schema_version: _NonEmptyStr
    evidence_bundle_sha256: _Sha256Hex
    producer_id: Literal["writer_admission_fact_assessor"] = (
        "writer_admission_fact_assessor"
    )
    producer_version: Literal["v1"] = "v1"
    conflict_facts: tuple[EvidenceConflictFactSchema, ...] = ()
    criterion_facts: tuple[CriterionEvidenceFactSchema, ...] = ()
    requirement_facts: tuple[RequirementEvidenceFactSchema, ...] = ()

    @model_validator(mode="after")
    def _validate_internal_graph(self) -> "WriterAdmissionFactAssessmentSchema":
        if self.assessment_state is WriterAdmissionAssessmentState.PENDING:
            if self.conflict_facts or self.criterion_facts or self.requirement_facts:
                raise ValueError("pending assessments cannot contain completed facts")
            return self

        if not self.criterion_facts or not self.requirement_facts:
            raise ValueError(
                "completed assessments require criterion and requirement facts"
            )

        conflict_ids = tuple(item.conflict_id for item in self.conflict_facts)
        criterion_fact_ids = tuple(item.fact_id for item in self.criterion_facts)
        requirement_fact_ids = tuple(item.fact_id for item in self.requirement_facts)
        criterion_ids = tuple(item.criterion_id for item in self.criterion_facts)
        requirement_ids = tuple(item.requirement_id for item in self.requirement_facts)
        for values, label in (
            (conflict_ids, "conflict_id"),
            (criterion_fact_ids, "criterion fact_id"),
            (requirement_fact_ids, "requirement fact_id"),
            (criterion_ids, "criterion_id"),
            (requirement_ids, "requirement_id"),
        ):
            if _duplicates(values):
                raise ValueError(f"{label} values must be unique")
        known_conflicts = set(conflict_ids)
        known_criterion_facts = set(criterion_fact_ids)
        known_criteria = set(criterion_ids)
        known_requirements = set(requirement_ids)
        if {item.requirement_id for item in self.criterion_facts} != known_requirements:
            raise ValueError(
                "criterion and requirement facts must cover the same requirements"
            )
        if {item.criterion_id for item in self.conflict_facts} - known_criteria:
            raise ValueError("conflict criterion_id must resolve in criterion_facts")
        for fact in self.criterion_facts:
            if not set(fact.conflict_refs) <= known_conflicts:
                raise ValueError("criterion conflict_refs must resolve in conflict_facts")
            if any(
                item.criterion_id != fact.criterion_id
                for item in self.conflict_facts
                if item.conflict_id in fact.conflict_refs
            ):
                raise ValueError("criterion conflict_refs must match criterion_id")
        for fact in self.requirement_facts:
            if not set(fact.conflict_refs) <= known_conflicts:
                raise ValueError("requirement conflict_refs must resolve in conflict_facts")
            if not set(fact.criterion_fact_ids) <= known_criterion_facts:
                raise ValueError(
                    "criterion_fact_ids must resolve in criterion_facts"
                )
            if any(
                item.requirement_id != fact.requirement_id
                for item in self.criterion_facts
                if item.fact_id in fact.criterion_fact_ids
            ):
                raise ValueError(
                    "criterion_fact_ids must belong to the requirement fact"
                )
        return self


class WriterAdmissionFactAssessmentRefSchema(_FrozenSchema):
    assessment_id: _NonEmptyStr
    assessment_schema_version: Literal["writer_admission_fact_assessment_v1"] = (
        "writer_admission_fact_assessment_v1"
    )
    assessment_contract_version: Literal["writer_admission_fact_semantics_v1"] = (
        "writer_admission_fact_semantics_v1"
    )
    requirements_schema_version: _NonEmptyStr
    requirements_contract_version: _NonEmptyStr
    requirements_sha256: _Sha256Hex
    evidence_bundle_schema_version: _NonEmptyStr
    evidence_bundle_sha256: _Sha256Hex


class WriterAdmissionContractSchema(_FrozenSchema):
    schema_version: Literal["writer_admission_contract_v1"] = (
        "writer_admission_contract_v1"
    )
    contract_id: Literal["normal_writer_admission"] = "normal_writer_admission"
    contract_version: Literal["v1"] = "v1"
    required_support_state: Literal["supported"] = "supported"
    unresolved_conflict_allowed: Literal[False] = False


class WriterAdmissionResolutionState(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class WriterAdmissionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class WriterAdmissionDecisionSchema(_FrozenSchema):
    schema_version: Literal["writer_admission_decision_v1"] = (
        "writer_admission_decision_v1"
    )
    contract_version: Literal["writer_admission_control_v1"] = (
        "writer_admission_control_v1"
    )
    decision_id: _NonEmptyStr
    resolution_state: WriterAdmissionResolutionState
    admission_outcome: WriterAdmissionOutcome | None
    policy_id: Literal["writer_admission_policy"] = "writer_admission_policy"
    policy_version: Literal["v1"] = "v1"
    supersedes_decision_id: _NonEmptyStr | None = None
    fact_assessment_ref: WriterAdmissionFactAssessmentRefSchema
    blocking_requirement_ids: tuple[_NonEmptyStr, ...] = ()
    indeterminate_requirement_ids: tuple[_NonEmptyStr, ...] = ()
    conflict_refs: tuple[_NonEmptyStr, ...] = ()
    reason_codes: tuple[_DecisionReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_coherence(self) -> "WriterAdmissionDecisionSchema":
        if self.supersedes_decision_id == self.decision_id:
            raise ValueError("supersedes_decision_id cannot self-reference")
        for field_name in (
            "blocking_requirement_ids",
            "indeterminate_requirement_ids",
            "conflict_refs",
            "reason_codes",
        ):
            if _duplicates(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be duplicate-free")
        if not set(self.indeterminate_requirement_ids) <= set(
            self.blocking_requirement_ids
        ):
            raise ValueError(
                "indeterminate_requirement_ids must be a blocking subset"
            )
        if self.resolution_state is WriterAdmissionResolutionState.UNRESOLVED:
            if self.admission_outcome is not None:
                raise ValueError("unresolved decisions require a null outcome")
            if (
                self.blocking_requirement_ids
                or self.indeterminate_requirement_ids
                or self.conflict_refs
                or self.reason_codes != ("assessment_pending",)
            ):
                raise ValueError("unresolved decision payload must describe pending")
        elif self.admission_outcome is None:
            raise ValueError("resolved decisions require allow or deny")
        elif self.admission_outcome is WriterAdmissionOutcome.ALLOW:
            if (
                self.blocking_requirement_ids
                or self.indeterminate_requirement_ids
                or self.conflict_refs
                or self.reason_codes != ("normal_admission_satisfied",)
            ):
                raise ValueError("allow decision payload must be non-blocking")
        elif not self.blocking_requirement_ids or any(
            code in {"assessment_pending", "normal_admission_satisfied"}
            for code in self.reason_codes
        ):
            raise ValueError("deny decision payload must describe completed blockers")
        return self


class WriterAdmissionDecisionRefSchema(_FrozenSchema):
    decision_id: _NonEmptyStr
    decision_schema_version: Literal["writer_admission_decision_v1"] = (
        "writer_admission_decision_v1"
    )
    decision_contract_version: Literal["writer_admission_control_v1"] = (
        "writer_admission_control_v1"
    )


__all__ = [
    "CriterionEvidenceFactSchema",
    "CriterionEvidenceSupportState",
    "EvidenceConflictFactSchema",
    "EvidenceCriterionSchema",
    "EvidenceRequirementSchema",
    "EvidenceRequirementsSnapshotSchema",
    "RequirementEvidenceFactSchema",
    "WriterAdmissionAssessmentState",
    "WriterAdmissionContractSchema",
    "WriterAdmissionDecisionRefSchema",
    "WriterAdmissionDecisionSchema",
    "WriterAdmissionFactAssessmentRefSchema",
    "WriterAdmissionFactAssessmentSchema",
    "WriterAdmissionOutcome",
    "WriterAdmissionResolutionState",
]
