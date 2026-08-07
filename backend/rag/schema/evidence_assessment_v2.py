"""Frozen V2 contracts for item-level and set-level evidence assessment."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from rag.catalog.document_profile import (
    CURRENT_PROJECT_CLAIM_TYPES,
    TEMPLATE_ONLY_CLAIM_TYPES,
    AuthorityLevel,
    ClaimType,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceRelevance(str, Enum):
    RELEVANT = "relevant"
    PARTIAL = "partial"
    IRRELEVANT = "irrelevant"


class EvidenceStatus(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    ABSENT = "absent"
    CONFLICTED = "conflicted"
    NOT_ASSESSED = "not_assessed"


class CoverageStrategy(str, Enum):
    TOPIC_UNION = "topic_union"
    VERSIONED_FACT_RESOLUTION = "versioned_fact_resolution"
    POLICY_CLAUSE_COVERAGE = "policy_clause_coverage"
    STRUCTURED_FACT_RESOLUTION = "structured_fact_resolution"
    TEMPLATE_STRUCTURE_COVERAGE = "template_structure_coverage"


class EvidenceAssessmentV2Schema(_FrozenContract):
    """Eligibility-aware assessment for one retrieved evidence item."""

    schema_version: Literal["evidence_assessment_v2"] = "evidence_assessment_v2"
    evidence_id: NonEmptyStr
    document_id: NonEmptyStr
    document_type: DocumentType
    evidence_role: EvidenceRole
    project_relation: ProjectRelation
    authority_level: AuthorityLevel
    semantic_relevance: float = Field(ge=0.0, le=1.0)
    eligibility: bool
    relevance: EvidenceRelevance
    supported_topics: list[NonEmptyStr] = Field(default_factory=list)
    supported_claim_types: list[ClaimType] = Field(default_factory=list)
    forbidden_claim_types: list[ClaimType] = Field(default_factory=list)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _assessment_is_consistent(self) -> "EvidenceAssessmentV2Schema":
        for field_name in (
            "supported_topics",
            "supported_claim_types",
            "forbidden_claim_types",
            "reason_codes",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")

        overlap = set(self.supported_claim_types) & set(self.forbidden_claim_types)
        if overlap:
            raise ValueError(
                "claim types cannot be both supported and forbidden: "
                + ", ".join(sorted(item.value for item in overlap))
            )
        if not self.eligibility and self.supported_claim_types:
            raise ValueError("ineligible evidence cannot expose supported_claim_types")
        if self.relevance is EvidenceRelevance.IRRELEVANT:
            if self.supported_topics or self.supported_claim_types:
                raise ValueError(
                    "irrelevant evidence cannot expose supported topics or claim types"
                )
        if self.evidence_role is EvidenceRole.HISTORICAL_CASE:
            leaked_claims = set(self.supported_claim_types) & CURRENT_PROJECT_CLAIM_TYPES
            if leaked_claims:
                raise ValueError(
                    "historical evidence cannot support current-project claim types"
                )
        if self.evidence_role is EvidenceRole.TEMPLATE_STYLE:
            non_template_claims = set(self.supported_claim_types) - TEMPLATE_ONLY_CLAIM_TYPES
            if non_template_claims:
                raise ValueError(
                    "template evidence may only support template_structure or style_guidance"
                )
        return self


class EvidenceConflictSchema(_FrozenContract):
    conflict_id: NonEmptyStr
    topic: NonEmptyStr
    evidence_ids: list[NonEmptyStr] = Field(min_length=2)
    claim_type: ClaimType | None = None
    description: NonEmptyStr
    candidate_values: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> "EvidenceConflictSchema":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("conflict evidence_ids must be unique")
        return self


class EvidenceSetAssessmentSchema(_FrozenContract):
    """Coverage and conflict assessment for all evidence used by one section."""

    schema_version: Literal["evidence_set_assessment_v1"] = (
        "evidence_set_assessment_v1"
    )
    assessment_id: NonEmptyStr
    section_id: NonEmptyStr
    section_type: NonEmptyStr
    strategy: CoverageStrategy
    status: EvidenceStatus
    coverage_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    required_topics: list[NonEmptyStr] = Field(default_factory=list)
    covered_topics: list[NonEmptyStr] = Field(default_factory=list)
    missing_topics: list[NonEmptyStr] = Field(default_factory=list)
    direct_evidence_count: int = Field(default=0, ge=0)
    partial_evidence_count: int = Field(default=0, ge=0)
    distinct_document_count: int = Field(default=0, ge=0)
    evidence_ids: list[NonEmptyStr] = Field(default_factory=list)
    conflicts: list[EvidenceConflictSchema] = Field(default_factory=list)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _set_assessment_is_consistent(self) -> "EvidenceSetAssessmentSchema":
        for field_name in (
            "required_topics",
            "covered_topics",
            "missing_topics",
            "evidence_ids",
            "reason_codes",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")

        topic_overlap = set(self.covered_topics) & set(self.missing_topics)
        if topic_overlap:
            raise ValueError(
                "topics cannot be both covered and missing: "
                + ", ".join(sorted(topic_overlap))
            )
        required = set(self.required_topics)
        if required:
            outside = (set(self.covered_topics) | set(self.missing_topics)) - required
            if outside:
                raise ValueError(
                    "covered_topics and missing_topics must be declared in required_topics"
                )
        if self.evidence_ids and self.distinct_document_count > len(self.evidence_ids):
            raise ValueError(
                "distinct_document_count cannot exceed the number of evidence items"
            )

        if self.status is EvidenceStatus.SUFFICIENT:
            if self.coverage_ratio != 1.0:
                raise ValueError("sufficient evidence requires coverage_ratio=1.0")
            if self.missing_topics:
                raise ValueError("sufficient evidence cannot have missing_topics")
            if self.conflicts:
                raise ValueError("sufficient evidence cannot contain conflicts")
        elif self.status is EvidenceStatus.PARTIAL:
            if self.coverage_ratio is None or not 0.0 < self.coverage_ratio < 1.0:
                raise ValueError("partial evidence requires 0 < coverage_ratio < 1")
            if not self.covered_topics or not self.missing_topics:
                raise ValueError(
                    "partial evidence requires both covered_topics and missing_topics"
                )
            if self.conflicts:
                raise ValueError("partial evidence cannot contain unresolved conflicts")
        elif self.status is EvidenceStatus.ABSENT:
            if self.coverage_ratio != 0.0:
                raise ValueError("absent evidence requires coverage_ratio=0.0")
            if self.covered_topics:
                raise ValueError("absent evidence cannot have covered_topics")
            if self.direct_evidence_count or self.partial_evidence_count:
                raise ValueError("absent evidence cannot have supporting evidence counts")
            if self.conflicts:
                raise ValueError("absent evidence cannot contain conflicts")
        elif self.status is EvidenceStatus.CONFLICTED:
            if not self.conflicts:
                raise ValueError("conflicted evidence requires at least one conflict")
        elif self.status is EvidenceStatus.NOT_ASSESSED:
            if self.coverage_ratio is not None:
                raise ValueError("not_assessed evidence requires coverage_ratio=None")
            if self.covered_topics or self.missing_topics:
                raise ValueError("not_assessed evidence cannot declare coverage results")
            if self.direct_evidence_count or self.partial_evidence_count:
                raise ValueError("not_assessed evidence cannot have evidence counts")
            if self.conflicts:
                raise ValueError("not_assessed evidence cannot contain conflicts")
        return self


__all__ = [
    "CoverageStrategy",
    "EvidenceAssessmentV2Schema",
    "EvidenceConflictSchema",
    "EvidenceRelevance",
    "EvidenceSetAssessmentSchema",
    "EvidenceStatus",
]
