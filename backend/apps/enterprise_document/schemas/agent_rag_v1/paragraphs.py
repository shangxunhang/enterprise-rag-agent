"""Canonical SIF-06 Paragraph / SectionParagraphManifest contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from ._base import ContractSchema
from .artifacts import ArtifactLogicalRefSchema, ArtifactVersionRefSchema
from .research import SectionPlanSourceRefSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenParagraphContract(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


ParagraphOperationKind = Literal["CREATE", "REVISE", "STRUCTURAL_CREATE"]
ParagraphAuthorizationKind = Literal["INITIAL_WRITER", "REPAIR"]
ManifestMembershipState = Literal["OPEN", "SEALED"]
ManifestOperationKind = Literal[
    "SECTION_PARAGRAPH_MANIFEST_UPDATE",
    "SECTION_PARAGRAPH_MANIFEST_SEAL",
]


class ParagraphCreationOccurrenceRefSchema(_FrozenParagraphContract):
    schema_version: Literal["paragraph_creation_occurrence_ref_v1"] = (
        "paragraph_creation_occurrence_ref_v1"
    )
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    write_instance_id: NonEmptyStr
    authorization_kind: ParagraphAuthorizationKind
    authorization_ref: NonEmptyStr
    creation_slot_ref: NonEmptyStr


class ParagraphOperationRefSchema(_FrozenParagraphContract):
    schema_version: Literal["paragraph_operation_ref_v1"] = (
        "paragraph_operation_ref_v1"
    )
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    write_instance_id: NonEmptyStr
    paragraph_operation_id: NonEmptyStr
    operation_kind: ParagraphOperationKind
    target_logical_ref: ArtifactLogicalRefSchema
    base_version_ref: ArtifactVersionRefSchema | None = None
    creation_occurrence_ref: ParagraphCreationOccurrenceRefSchema | None = None
    authorization_kind: ParagraphAuthorizationKind
    authorization_ref: NonEmptyStr

    @model_validator(mode="after")
    def _validate_operation(self) -> "ParagraphOperationRefSchema":
        logical = self.target_logical_ref
        if logical.artifact_type != "paragraph":
            raise ValueError("Paragraph operation target must use artifact_type=paragraph")
        if logical.run_id != self.run_id:
            raise ValueError("Paragraph operation target Run mismatch")
        if logical.domain_scope_ref != f"section:{self.section_id}":
            raise ValueError("Paragraph operation target must use canonical Section scope")

        if self.operation_kind in {"CREATE", "STRUCTURAL_CREATE"}:
            if self.base_version_ref is not None:
                raise ValueError("Paragraph CREATE cannot bind a base version")
            occurrence = self.creation_occurrence_ref
            if occurrence is None:
                raise ValueError("Paragraph CREATE requires creation_occurrence_ref")
            if (
                occurrence.run_id != self.run_id
                or occurrence.section_id != self.section_id
                or occurrence.write_instance_id != self.write_instance_id
                or occurrence.authorization_kind != self.authorization_kind
                or occurrence.authorization_ref != self.authorization_ref
            ):
                raise ValueError(
                    "Paragraph creation occurrence must match operation lineage"
                )
        else:
            if self.creation_occurrence_ref is not None:
                raise ValueError("Paragraph REVISE cannot allocate a creation occurrence")
            if self.base_version_ref is None:
                raise ValueError("Paragraph REVISE requires exact base_version_ref")
            if self.base_version_ref.logical_ref != logical:
                raise ValueError("Paragraph REVISE base must belong to target logical ref")
        return self


class ParagraphWriterSourceBindingSchema(_FrozenParagraphContract):
    schema_version: Literal["paragraph_writer_source_binding_v1"] = (
        "paragraph_writer_source_binding_v1"
    )
    writer_subject_ref: WriterSubjectRefSchema
    write_instance_id: NonEmptyStr
    route_decision_ref: PreWriteRouteDecisionRefSchema
    writer_gate_binding_ref: WriterGateBindingRefSchema
    generation_class: Literal["normal_write", "fallback_write"]
    section_plan_source: SectionPlanSourceRefSchema
    fact_snapshot_version_ref: ArtifactVersionRefSchema
    evidence_package_version_ref: ArtifactVersionRefSchema
    context_package_id: NonEmptyStr
    context_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_source_identity(self) -> "ParagraphWriterSourceBindingSchema":
        subject = self.writer_subject_ref
        if subject.run_id != self.section_plan_source.plan_version_ref.logical_ref.run_id:
            raise ValueError("Paragraph Writer source Plan belongs to another Run")
        if subject.section_id != self.section_plan_source.section_id:
            raise ValueError("Paragraph Writer source Plan targets another Section")
        if self.fact_snapshot_version_ref.logical_ref.run_id != subject.run_id:
            raise ValueError("Paragraph FactSnapshot belongs to another Run")
        if self.fact_snapshot_version_ref.logical_ref.artifact_type != "fact_snapshot":
            raise ValueError("Paragraph source requires FactSnapshot Artifact")
        if self.evidence_package_version_ref.logical_ref.run_id != subject.run_id:
            raise ValueError("Paragraph EvidencePackage belongs to another Run")
        if self.evidence_package_version_ref.logical_ref.artifact_type != "evidence_package":
            raise ValueError("Paragraph source requires EvidencePackage Artifact")
        if (
            self.evidence_package_version_ref.logical_ref.domain_scope_ref
            != f"section:{subject.section_id}"
        ):
            raise ValueError("Paragraph EvidencePackage targets another Section")
        return self


class ParagraphCandidateSchema(_FrozenParagraphContract):
    schema_version: Literal["paragraph_candidate_v1"] = "paragraph_candidate_v1"
    content_contract_version: NonEmptyStr
    operation_ref: ParagraphOperationRefSchema
    source_binding: ParagraphWriterSourceBindingSchema
    role: NonEmptyStr
    text: NonEmptyStr

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Paragraph candidate text must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_candidate_lineage(self) -> "ParagraphCandidateSchema":
        op = self.operation_ref
        source = self.source_binding
        subject = source.writer_subject_ref
        if (
            op.run_id != subject.run_id
            or op.section_id != subject.section_id
            or op.write_instance_id != source.write_instance_id
        ):
            raise ValueError("Paragraph candidate source/operation lineage mismatch")
        return self


class ParagraphContentSchema(_FrozenParagraphContract):
    schema_version: Literal["paragraph_content_v1"] = "paragraph_content_v1"
    content_contract_version: NonEmptyStr
    role: NonEmptyStr
    text: NonEmptyStr
    source_binding: ParagraphWriterSourceBindingSchema

    @field_validator("text")
    @classmethod
    def _content_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Paragraph content text must not be blank")
        return value


class SectionParagraphManifestRefSchema(_FrozenParagraphContract):
    schema_version: Literal["section_paragraph_manifest_ref_v1"] = (
        "section_paragraph_manifest_ref_v1"
    )
    run_id: NonEmptyStr
    section_id: NonEmptyStr
    attempt_scope_ref: NonEmptyStr
    attempt_id: NonEmptyStr
    write_instance_id: NonEmptyStr

    @model_validator(mode="after")
    def _section_scope_is_exact(self) -> "SectionParagraphManifestRefSchema":
        if self.attempt_scope_ref != f"section:{self.section_id}":
            raise ValueError("Paragraph Manifest Attempt scope must target the Section")
        return self


class SectionParagraphManifestSchema(_FrozenParagraphContract):
    schema_version: Literal["section_paragraph_manifest_v1"] = (
        "section_paragraph_manifest_v1"
    )
    manifest_ref: SectionParagraphManifestRefSchema
    manifest_revision: int = Field(ge=0)
    generation_membership_state: ManifestMembershipState = "OPEN"
    members: tuple[ArtifactVersionRefSchema, ...] = ()

    @model_validator(mode="after")
    def _validate_members(self) -> "SectionParagraphManifestSchema":
        seen: set[tuple[str, str]] = set()
        for member in self.members:
            logical = member.logical_ref
            if logical.artifact_type != "paragraph":
                raise ValueError("Manifest members must be Paragraph Artifact versions")
            if logical.run_id != self.manifest_ref.run_id:
                raise ValueError("Manifest member belongs to another Run")
            if logical.domain_scope_ref != f"section:{self.manifest_ref.section_id}":
                raise ValueError("Manifest member belongs to another Section")
            key = (logical.run_id, logical.logical_artifact_id)
            if key in seen:
                raise ValueError("Manifest cannot contain one logical Paragraph twice")
            seen.add(key)
        if self.manifest_revision == 0 and self.members:
            raise ValueError("initial Manifest revision 0 must be empty")
        if self.manifest_revision == 0 and self.generation_membership_state != "OPEN":
            raise ValueError("initial Manifest must be OPEN")
        return self


class SectionParagraphManifestOperationRefSchema(_FrozenParagraphContract):
    schema_version: Literal["section_paragraph_manifest_operation_ref_v1"] = (
        "section_paragraph_manifest_operation_ref_v1"
    )
    manifest_ref: SectionParagraphManifestRefSchema
    manifest_operation_id: NonEmptyStr
    operation_kind: ManifestOperationKind
    authorization_ref: NonEmptyStr


class SectionParagraphManifestCommitReceiptSchema(_FrozenParagraphContract):
    schema_version: Literal["section_paragraph_manifest_commit_receipt_v1"] = (
        "section_paragraph_manifest_commit_receipt_v1"
    )
    operation_ref: SectionParagraphManifestOperationRefSchema
    semantic_fingerprint: Sha256Hex
    prior_manifest_revision: int = Field(ge=0)
    resulting_manifest_revision: int = Field(ge=1)
    prior_generation_membership_state: ManifestMembershipState
    resulting_generation_membership_state: ManifestMembershipState
    prior_member_sequence_sha256: Sha256Hex
    resulting_members: tuple[ArtifactVersionRefSchema, ...]
    resulting_member_sequence_sha256: Sha256Hex

    @model_validator(mode="after")
    def _revision_advances_once(
        self,
    ) -> "SectionParagraphManifestCommitReceiptSchema":
        if self.resulting_manifest_revision != self.prior_manifest_revision + 1:
            raise ValueError("Manifest commit must advance revision exactly once")
        return self


class ParagraphBindingSchema(_FrozenParagraphContract):
    schema_version: Literal["paragraph_binding_v1"] = "paragraph_binding_v1"
    version_ref: ArtifactVersionRefSchema
    resolved_head_revision: int = Field(ge=1)
    manifest_ref: SectionParagraphManifestRefSchema
    resolved_manifest_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _binding_is_paragraph(self) -> "ParagraphBindingSchema":
        logical = self.version_ref.logical_ref
        if logical.artifact_type != "paragraph":
            raise ValueError("ParagraphBinding requires Paragraph Artifact version")
        if logical.run_id != self.manifest_ref.run_id:
            raise ValueError("ParagraphBinding Manifest belongs to another Run")
        if logical.domain_scope_ref != f"section:{self.manifest_ref.section_id}":
            raise ValueError("ParagraphBinding Manifest belongs to another Section")
        return self


__all__ = [
    "ManifestMembershipState",
    "ManifestOperationKind",
    "ParagraphAuthorizationKind",
    "ParagraphBindingSchema",
    "ParagraphCandidateSchema",
    "ParagraphContentSchema",
    "ParagraphCreationOccurrenceRefSchema",
    "ParagraphOperationKind",
    "ParagraphOperationRefSchema",
    "ParagraphWriterSourceBindingSchema",
    "SectionParagraphManifestCommitReceiptSchema",
    "SectionParagraphManifestOperationRefSchema",
    "SectionParagraphManifestRefSchema",
    "SectionParagraphManifestSchema",
]
