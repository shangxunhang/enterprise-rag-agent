"""Stage F immutable release publication contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.artifact_reference import (
    ArtifactReferenceSchema,
)
from apps.enterprise_document.schemas.contract_base import ContractSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class ReleaseEligibilityProofSchema(ContractSchema):
    """Proof that one exact draft is eligible for immutable publication."""

    schema_version: Literal["release_eligibility_proof_v1"] = (
        "release_eligibility_proof_v1"
    )
    task_id: NonEmptyStr
    run_id: NonEmptyStr
    document_id: NonEmptyStr
    draft_id: NonEmptyStr
    document_version: NonEmptyStr
    release_request_sha256: Sha256Hex
    draft_sha256: Sha256Hex
    hard_gate_sha256: Sha256Hex
    generation_report_sha256: Sha256Hex
    version_decision_sha256: Sha256Hex
    section_artifact_sha256: dict[NonEmptyStr, Sha256Hex] = Field(
        default_factory=dict
    )
    writer_gate_binding_sha256: dict[NonEmptyStr, Sha256Hex] = Field(
        default_factory=dict
    )
    claim_audit_sha256: dict[NonEmptyStr, Sha256Hex] = Field(
        default_factory=dict
    )
    hard_gate_passed: Literal[True] = True
    all_writer_artifacts_eligible: Literal[True] = True
    human_review_required: Literal[False] = False
    verified_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _proof_is_complete(self) -> "ReleaseEligibilityProofSchema":
        if self.verified_at.tzinfo is None:
            raise ValueError("verified_at must be timezone-aware")
        section_ids = set(self.section_artifact_sha256)
        if not section_ids:
            raise ValueError("release proof requires at least one section")
        if set(self.writer_gate_binding_sha256) != section_ids:
            raise ValueError("writer binding proof must cover every released section")
        if set(self.claim_audit_sha256) != section_ids:
            raise ValueError("claim audit proof must cover every released section")
        return self


class ReleaseManifestSchema(ContractSchema):
    """Immutable publication manifest referencing content and eligibility proof."""

    schema_version: Literal["release_manifest_v1"] = "release_manifest_v1"
    release_id: NonEmptyStr
    idempotency_key: Sha256Hex
    release_request_sha256: Sha256Hex
    task_id: NonEmptyStr
    run_id: NonEmptyStr
    document_id: NonEmptyStr
    draft_id: NonEmptyStr
    document_version: NonEmptyStr
    release_status: Literal["published"] = "published"
    document_artifact: ArtifactReferenceSchema
    eligibility_artifact: ArtifactReferenceSchema
    release_payload_sha256: Sha256Hex
    eligibility_proof_sha256: Sha256Hex
    manifest_payload_sha256: Sha256Hex
    publisher: NonEmptyStr
    published_at: datetime
    immutable: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _manifest_is_consistent(self) -> "ReleaseManifestSchema":
        if self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        if self.document_artifact.task_id != self.task_id:
            raise ValueError("document artifact task identity mismatch")
        if self.eligibility_artifact.task_id != self.task_id:
            raise ValueError("eligibility artifact task identity mismatch")
        if self.document_artifact.attempt_id != self.run_id:
            raise ValueError("document artifact run identity mismatch")
        if self.eligibility_artifact.attempt_id != self.run_id:
            raise ValueError("eligibility artifact run identity mismatch")
        if self.document_artifact.input_snapshot_version != self.document_version:
            raise ValueError("document artifact version identity mismatch")
        if self.eligibility_artifact.input_snapshot_version != self.document_version:
            raise ValueError("eligibility artifact version identity mismatch")
        if self.document_artifact.content_hash != f"sha256:{self.release_payload_sha256}":
            raise ValueError("release payload hash does not match document artifact")
        if self.eligibility_artifact.content_hash != f"sha256:{self.eligibility_proof_sha256}":
            raise ValueError("eligibility proof hash does not match eligibility artifact")
        return self


class PublishedReleaseSchema(ContractSchema):
    """Verified result of one immutable release publication attempt."""

    schema_version: Literal["published_release_v1"] = "published_release_v1"
    manifest: ReleaseManifestSchema
    manifest_artifact: ArtifactReferenceSchema
    verified_after_write: Literal[True] = True
    idempotent_replay: bool = False

    @model_validator(mode="after")
    def _published_release_is_consistent(self) -> "PublishedReleaseSchema":
        manifest = self.manifest
        reference = self.manifest_artifact
        if reference.task_id != manifest.task_id:
            raise ValueError("manifest artifact task identity mismatch")
        if reference.attempt_id != manifest.run_id:
            raise ValueError("manifest artifact run identity mismatch")
        if reference.input_snapshot_version != manifest.document_version:
            raise ValueError("manifest artifact version identity mismatch")
        return self


__all__ = [
    "PublishedReleaseSchema",
    "ReleaseEligibilityProofSchema",
    "ReleaseManifestSchema",
]
