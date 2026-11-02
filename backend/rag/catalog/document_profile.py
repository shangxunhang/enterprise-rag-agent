"""Frozen V1 document-profile contract for evidence-aware retrieval.

A document profile is the authoritative catalog record that describes what one
raw document is, which project it belongs to, and which claim types it may or
may not support.  Knowledge-base metadata may provide defaults, but callers
must treat this document-level contract as the higher-priority boundary.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from schemas.claim import (
    CURRENT_PROJECT_CLAIM_TYPES,
    TEMPLATE_ONLY_CLAIM_TYPES,
    ClaimType,
)
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _FrozenContract(BaseModel):
    """Strict base for catalog contracts that cross persistence boundaries."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ParserStrategy(str, Enum):
    HIERARCHICAL_DOCUMENT = "hierarchical_document"
    POLICY_CLAUSE = "policy_clause"
    PROJECT_FACT_EXTRACTION = "project_fact_extraction"
    STRUCTURED_TABLE = "structured_table"
    TEMPLATE_DOCUMENT = "template_document"
    GENERIC_TEXT = "generic_text"


class ChunkStrategy(str, Enum):
    HEADING_PARENT_PARAGRAPH_CHILD = "heading_parent_paragraph_child"
    POLICY_ARTICLE_CLAUSE = "policy_article_clause"
    FACT_RECORD = "fact_record"
    TABLE_ROW_RECORD = "table_row_record"
    TEMPLATE_SECTION = "template_section"
    FIXED_TOKEN_FALLBACK = "fixed_token_fallback"


class ClassificationMethod(str, Enum):
    MANUAL_SEED = "manual_seed"
    RULE_BASED = "rule_based"
    LLM_SUGGESTION = "llm_suggestion"
    HYBRID_REVIEW = "hybrid_review"


class ProjectRelationSchema(_FrozenContract):
    relation: ProjectRelation
    project_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _project_id_matches_relation(self) -> "ProjectRelationSchema":
        if self.relation in {
            ProjectRelation.CURRENT_PROJECT,
            ProjectRelation.OTHER_PROJECT,
        } and self.project_id is None:
            raise ValueError("current_project and other_project relations require project_id")
        if self.relation is ProjectRelation.GENERAL and self.project_id is not None:
            raise ValueError("general relation must not carry project_id")
        return self


class AuthorityProfileSchema(_FrozenContract):
    level: AuthorityLevel
    issuer: NonEmptyStr | None = None
    version: NonEmptyStr | None = None
    effective_date: date | None = None
    expiration_date: date | None = None

    @model_validator(mode="after")
    def _date_window_is_valid(self) -> "AuthorityProfileSchema":
        if (
            self.effective_date is not None
            and self.expiration_date is not None
            and self.expiration_date < self.effective_date
        ):
            raise ValueError("expiration_date must not be earlier than effective_date")
        return self


class ContentCapabilitiesSchema(_FrozenContract):
    allowed_claim_types: list[ClaimType] = Field(default_factory=list)
    forbidden_claim_types: list[ClaimType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _claim_sets_are_valid(self) -> "ContentCapabilitiesSchema":
        allowed = self.allowed_claim_types
        forbidden = self.forbidden_claim_types
        if len(allowed) != len(set(allowed)):
            raise ValueError("allowed_claim_types must be unique")
        if len(forbidden) != len(set(forbidden)):
            raise ValueError("forbidden_claim_types must be unique")
        overlap = set(allowed) & set(forbidden)
        if overlap:
            raise ValueError(
                "claim types cannot be both allowed and forbidden: "
                + ", ".join(sorted(item.value for item in overlap))
            )
        return self


class ParsingProfileSchema(_FrozenContract):
    parser_strategy: ParserStrategy
    chunk_strategy: ChunkStrategy
    fallback_chunk_strategy: ChunkStrategy | None = ChunkStrategy.FIXED_TOKEN_FALLBACK


class ClassificationProfileSchema(_FrozenContract):
    method: ClassificationMethod
    confidence: float = Field(ge=0.0, le=1.0)
    reviewed: bool = False
    reviewer: NonEmptyStr | None = None
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reason_codes_are_unique(self) -> "ClassificationProfileSchema":
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("classification reason_codes must be unique")
        return self


_DOCUMENT_ROLE_REQUIREMENTS: dict[DocumentType, EvidenceRole] = {
    DocumentType.CURRENT_PROJECT_REQUIREMENT: EvidenceRole.PROJECT_FACT,
    DocumentType.CURRENT_PROJECT_DECISION: EvidenceRole.PROJECT_FACT,
    DocumentType.CURRENT_PROJECT_INVENTORY: EvidenceRole.PROJECT_FACT,
    DocumentType.HISTORICAL_FEASIBILITY_STUDY: EvidenceRole.HISTORICAL_CASE,
    DocumentType.HISTORICAL_CONSTRUCTION_SCHEME: EvidenceRole.HISTORICAL_CASE,
    DocumentType.POLICY_STANDARD: EvidenceRole.POLICY_STANDARD,
    DocumentType.TECHNICAL_WHITEPAPER: EvidenceRole.TECHNICAL_REFERENCE,
    DocumentType.TENDER_REQUIREMENT: EvidenceRole.PROCUREMENT_REQUIREMENT,
    DocumentType.DOCUMENT_TEMPLATE: EvidenceRole.TEMPLATE_STYLE,
    DocumentType.UNKNOWN: EvidenceRole.UNCLASSIFIED,
}

_CURRENT_PROJECT_DOCUMENT_TYPES = frozenset(
    {
        DocumentType.CURRENT_PROJECT_REQUIREMENT,
        DocumentType.CURRENT_PROJECT_DECISION,
        DocumentType.CURRENT_PROJECT_INVENTORY,
    }
)

_HISTORICAL_DOCUMENT_TYPES = frozenset(
    {
        DocumentType.HISTORICAL_FEASIBILITY_STUDY,
        DocumentType.HISTORICAL_CONSTRUCTION_SCHEME,
    }
)


class DocumentProfileSchema(_FrozenContract):
    """Canonical V1 profile for exactly one raw document."""

    schema_version: Literal["document_profile_v1"] = "document_profile_v1"
    document_id: NonEmptyStr
    document_type: DocumentType
    evidence_role: EvidenceRole
    kb_id: NonEmptyStr
    tenant_id: NonEmptyStr = "default"
    source_uri: NonEmptyStr | None = None
    source_name: NonEmptyStr | None = None
    title: NonEmptyStr | None = None
    project_relation: ProjectRelationSchema
    authority: AuthorityProfileSchema
    content_capabilities: ContentCapabilitiesSchema
    parsing: ParsingProfileSchema
    classification: ClassificationProfileSchema
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _document_semantics_are_consistent(self) -> "DocumentProfileSchema":
        required_role = _DOCUMENT_ROLE_REQUIREMENTS[self.document_type]
        if self.evidence_role is not required_role:
            raise ValueError(
                f"{self.document_type.value} requires evidence_role={required_role.value}"
            )

        relation = self.project_relation.relation
        if (
            self.document_type in _CURRENT_PROJECT_DOCUMENT_TYPES
            and relation is not ProjectRelation.CURRENT_PROJECT
        ):
            raise ValueError("current-project document types require current_project relation")
        if (
            self.document_type in _HISTORICAL_DOCUMENT_TYPES
            and relation is not ProjectRelation.OTHER_PROJECT
        ):
            raise ValueError("historical document types require other_project relation")

        allowed = set(self.content_capabilities.allowed_claim_types)
        if self.evidence_role is EvidenceRole.HISTORICAL_CASE:
            leaked_claims = allowed & CURRENT_PROJECT_CLAIM_TYPES
            if leaked_claims:
                raise ValueError(
                    "historical-case documents cannot allow current-project claims: "
                    + ", ".join(sorted(item.value for item in leaked_claims))
                )
        if self.evidence_role is EvidenceRole.TEMPLATE_STYLE:
            non_template_claims = allowed - TEMPLATE_ONLY_CLAIM_TYPES
            if non_template_claims:
                raise ValueError(
                    "template documents may only allow template_structure or style_guidance"
                )
        if self.evidence_role is EvidenceRole.UNCLASSIFIED and allowed:
            raise ValueError("unclassified documents cannot allow claim types")
        return self


__all__ = [
    "AuthorityLevel",
    "AuthorityProfileSchema",
    "ChunkStrategy",
    "ClaimType",
    "ClassificationMethod",
    "ClassificationProfileSchema",
    "ContentCapabilitiesSchema",
    "CURRENT_PROJECT_CLAIM_TYPES",
    "DocumentProfileSchema",
    "DocumentType",
    "EvidenceRole",
    "ParserStrategy",
    "ParsingProfileSchema",
    "ProjectRelation",
    "ProjectRelationSchema",
    "TEMPLATE_ONLY_CLAIM_TYPES",
]
