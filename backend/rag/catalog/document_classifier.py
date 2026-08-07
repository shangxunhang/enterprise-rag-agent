"""Deterministic Stage B document classifier driven by reviewed seed records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from .document_profile import (
    AuthorityLevel,
    AuthorityProfileSchema,
    ChunkStrategy,
    ClaimType,
    ClassificationMethod,
    ClassificationProfileSchema,
    ContentCapabilitiesSchema,
    DocumentProfileSchema,
    DocumentType,
    ParserStrategy,
    ParsingProfileSchema,
    ProjectRelation,
    ProjectRelationSchema,
)
from .document_type_registry import get_document_type_policy


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Str = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DocumentProfileSeedSchema(BaseModel):
    """Reviewed per-document classification seed bound to immutable source hash."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["document_profile_seed_v1"] = "document_profile_seed_v1"
    document_id: NonEmptyStr
    source_sha256: Sha256Str
    document_type: DocumentType
    project_relation: ProjectRelation
    project_id: NonEmptyStr | None = None
    authority_level: AuthorityLevel
    issuer: NonEmptyStr | None = None
    version: NonEmptyStr | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    classification_method: ClassificationMethod = ClassificationMethod.HYBRID_REVIEW
    confidence: float = Field(ge=0.0, le=1.0)
    reviewed: bool = True
    reviewer: NonEmptyStr | None = None
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    title: NonEmptyStr | None = None
    allowed_claim_types: list[ClaimType] | None = None
    forbidden_claim_types: list[ClaimType] | None = None
    parser_strategy: ParserStrategy | None = None
    chunk_strategy: ChunkStrategy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _seed_is_reviewable(self) -> "DocumentProfileSeedSchema":
        if self.project_relation in {
            ProjectRelation.CURRENT_PROJECT,
            ProjectRelation.OTHER_PROJECT,
        } and self.project_id is None:
            raise ValueError("project-specific seed requires project_id")
        if self.project_relation is ProjectRelation.GENERAL and self.project_id is not None:
            raise ValueError("general seed must not carry project_id")
        if self.reviewed and self.reviewer is None:
            raise ValueError("reviewed seed requires reviewer")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("seed reason_codes must be unique")
        return self


def _required_manifest_value(record: Mapping[str, Any], field: str) -> Any:
    value = record.get(field)
    if value is None or value == "":
        raise ValueError(f"manifest record missing required field: {field}")
    return value


def classify_manifest_record(
    manifest_record: Mapping[str, Any],
    seed: DocumentProfileSeedSchema,
) -> DocumentProfileSchema:
    """Build one canonical profile after identity and governance checks."""

    document_id = str(_required_manifest_value(manifest_record, "doc_id"))
    source_sha256 = str(_required_manifest_value(manifest_record, "source_sha256"))
    source_name = str(_required_manifest_value(manifest_record, "source_name"))
    _required_manifest_value(manifest_record, "source_uri")

    if document_id != seed.document_id:
        raise ValueError(
            f"seed document_id mismatch: manifest={document_id}, seed={seed.document_id}"
        )
    if source_sha256 != seed.source_sha256:
        raise ValueError(f"source hash mismatch for {document_id}")

    governance = dict(manifest_record.get("governance") or {})
    governance_project_id = governance.get("project_id")
    if governance_project_id is not None and governance_project_id != seed.project_id:
        raise ValueError(
            f"manifest governance project_id mismatch for {document_id}: "
            f"{governance_project_id!r} != {seed.project_id!r}"
        )

    policy = get_document_type_policy(seed.document_type)
    allowed_claim_types = (
        seed.allowed_claim_types
        if seed.allowed_claim_types is not None
        else list(policy.allowed_claim_types)
    )
    forbidden_claim_types = (
        seed.forbidden_claim_types
        if seed.forbidden_claim_types is not None
        else list(policy.forbidden_claim_types)
    )

    profile_metadata = {
        "catalog_batch": "document_full_v1",
        "source_sha256": source_sha256,
        "source_format": manifest_record.get("source_format"),
        "parsed_uri": manifest_record.get("parsed_uri"),
        "should_index": governance.get("should_index", True),
        "is_gold_reference": governance.get("is_gold_reference", False),
        **seed.metadata,
    }

    return DocumentProfileSchema(
        document_id=document_id,
        document_type=seed.document_type,
        evidence_role=policy.evidence_role,
        kb_id=policy.kb_id,
        tenant_id="default",
        source_name=source_name,
        title=seed.title,
        project_relation=ProjectRelationSchema(
            relation=seed.project_relation,
            project_id=seed.project_id,
        ),
        authority=AuthorityProfileSchema(
            level=seed.authority_level,
            issuer=seed.issuer,
            version=seed.version,
            effective_date=seed.effective_date,
            expiration_date=seed.expiration_date,
        ),
        content_capabilities=ContentCapabilitiesSchema(
            allowed_claim_types=allowed_claim_types,
            forbidden_claim_types=forbidden_claim_types,
        ),
        parsing=ParsingProfileSchema(
            parser_strategy=seed.parser_strategy or policy.parser_strategy,
            chunk_strategy=seed.chunk_strategy or policy.chunk_strategy,
        ),
        classification=ClassificationProfileSchema(
            method=seed.classification_method,
            confidence=seed.confidence,
            reviewed=seed.reviewed,
            reviewer=seed.reviewer,
            reason_codes=seed.reason_codes,
        ),
        metadata=profile_metadata,
    )


__all__ = ["DocumentProfileSeedSchema", "classify_manifest_record"]
