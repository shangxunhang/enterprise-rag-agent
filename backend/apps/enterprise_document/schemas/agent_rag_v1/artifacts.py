"""Canonical SIF-03 Artifact identity, version, and active-head contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.artifact_reference import (
    ArtifactReferenceSchema,
)

from ._base import ContractSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
ContentSha256 = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
]


class _FrozenArtifactContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class ArtifactLogicalRefSchema(_FrozenArtifactContract):
    schema_version: Literal["artifact_logical_ref_v1"] = "artifact_logical_ref_v1"
    run_id: NonEmptyStr
    logical_artifact_id: NonEmptyStr
    artifact_type: NonEmptyStr
    domain_scope_ref: NonEmptyStr


class ArtifactVersionRefSchema(_FrozenArtifactContract):
    schema_version: Literal["artifact_version_ref_v1"] = "artifact_version_ref_v1"
    logical_ref: ArtifactLogicalRefSchema
    artifact_version_id: NonEmptyStr
    content_ref: NonEmptyStr
    content_hash: ContentSha256
    content_schema_version: NonEmptyStr
    committed_by_operation_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    producer_attempt_scope_ref: NonEmptyStr | None = None
    producer_attempt_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _attempt_provenance_is_paired(self) -> "ArtifactVersionRefSchema":
        if bool(self.producer_attempt_scope_ref) != bool(self.producer_attempt_id):
            raise ValueError(
                "producer_attempt_scope_ref and producer_attempt_id must be supplied together"
            )
        return self


class ArtifactActiveHeadSchema(_FrozenArtifactContract):
    schema_version: Literal["artifact_active_head_v1"] = "artifact_active_head_v1"
    logical_ref: ArtifactLogicalRefSchema
    head_revision: int = Field(ge=0)
    active_version_ref: ArtifactVersionRefSchema | None = None

    @model_validator(mode="after")
    def _head_is_consistent(self) -> "ArtifactActiveHeadSchema":
        if self.head_revision == 0 and self.active_version_ref is not None:
            raise ValueError("head_revision=0 cannot have an active version")
        if self.head_revision > 0 and self.active_version_ref is None:
            raise ValueError("promoted head must have an active version")
        if (
            self.active_version_ref is not None
            and self.active_version_ref.logical_ref != self.logical_ref
        ):
            raise ValueError("active version must belong to the same logical Artifact")
        return self


class ArtifactPromotionReceiptSchema(_FrozenArtifactContract):
    schema_version: Literal["artifact_promotion_receipt_v1"] = (
        "artifact_promotion_receipt_v1"
    )
    logical_ref: ArtifactLogicalRefSchema
    promotion_operation_id: NonEmptyStr
    semantic_fingerprint: Sha256Hex
    previous_active_version_id: NonEmptyStr | None = None
    promoted_version_id: NonEmptyStr
    previous_head_revision: int = Field(ge=0)
    resulting_head_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _receipt_is_consistent(self) -> "ArtifactPromotionReceiptSchema":
        if self.resulting_head_revision != self.previous_head_revision + 1:
            raise ValueError(
                "resulting_head_revision must equal previous_head_revision + 1"
            )
        if self.previous_head_revision == 0 and self.previous_active_version_id is not None:
            raise ValueError(
                "initial promotion cannot name a previous active version"
            )
        if self.previous_head_revision > 0 and self.previous_active_version_id is None:
            raise ValueError(
                "non-initial promotion must name the previous active version"
            )
        return self


__all__ = [
    "ArtifactActiveHeadSchema",
    "ArtifactLogicalRefSchema",
    "ArtifactPromotionReceiptSchema",
    "ArtifactReferenceSchema",
    "ArtifactVersionRefSchema",
]
