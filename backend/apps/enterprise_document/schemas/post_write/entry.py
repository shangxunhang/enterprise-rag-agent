"""Canonical Post-write Entry binding schemas."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import ConfigDict, StringConstraints, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
)

from .document import (
    AssemblyInputManifestRefSchema,
    DocumentEvidenceSnapshotRefSchema,
)
from .foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    EvidenceSnapshotRefSchema,
    PostWriteEntryRefSchema,
    QualityDeliveryContractRefSchema,
)
from .requirement import RequirementRefSchema


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


class _FrozenEntry(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class PostWriteEvidenceBindingKind(str, Enum):
    SECTION_WRITER_SNAPSHOT = "SECTION_WRITER_SNAPSHOT"
    DOCUMENT_PROJECTION = "DOCUMENT_PROJECTION"


class PostWriteEvidenceBindingRef(_FrozenEntry):
    schema_version: Literal["post_write_evidence_binding_ref_v1"] = (
        "post_write_evidence_binding_ref_v1"
    )
    binding_kind: PostWriteEvidenceBindingKind
    section_writer_snapshot_ref: EvidenceSnapshotRefSchema | None = None
    document_projection_ref: DocumentEvidenceSnapshotRefSchema | None = None

    @model_validator(mode="after")
    def _exclusive_binding(self) -> "PostWriteEvidenceBindingRef":
        if self.binding_kind is PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT:
            if (
                self.section_writer_snapshot_ref is None
                or self.document_projection_ref is not None
            ):
                raise ValueError(
                    "SECTION_WRITER_SNAPSHOT requires only section_writer_snapshot_ref"
                )
        else:
            if (
                self.document_projection_ref is None
                or self.section_writer_snapshot_ref is not None
            ):
                raise ValueError(
                    "DOCUMENT_PROJECTION requires only document_projection_ref"
                )
        return self


class PostWriteEntryIdentitySchema(_FrozenEntry):
    schema_version: Literal["post_write_entry_identity_v1"] = (
        "post_write_entry_identity_v1"
    )
    entry_scope: ArtifactScope
    run_id: NonEmptyStr
    document_id: NonEmptyStr
    section_id: NonEmptyStr | None = None
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema | None = None
    artifact_state_ref: ArtifactStateRefSchema
    evidence_binding: PostWriteEvidenceBindingRef
    requirement_ref: RequirementRefSchema
    quality_delivery_contract_ref: QualityDeliveryContractRefSchema
    writer_execution_ref: WriterExecutionRefSchema | None = None
    writer_invocation_ref: WriterGenerationInvocationRefSchema | None = None
    route_decision_ref: PreWriteRouteDecisionRefSchema | None = None
    generation_class: WriterGenerationClass | None = None
    entry_id: NonEmptyStr
    entry_sha256: Sha256Hex

    @model_validator(mode="after")
    def _scope_shape(self) -> "PostWriteEntryIdentitySchema":
        if self.artifact_state_ref.artifact_scope is not self.entry_scope:
            raise ValueError("Entry scope must match Artifact scope")
        if self.entry_scope is ArtifactScope.SECTION:
            if self.section_id is None:
                raise ValueError("SECTION Entry identity requires section_id")
            if self.assembly_input_manifest_ref is not None:
                raise ValueError("SECTION Entry cannot carry assembly manifest")
            if (
                self.writer_execution_ref is None
                or self.writer_invocation_ref is None
                or self.route_decision_ref is None
                or self.generation_class is None
            ):
                raise ValueError("SECTION Entry requires exact Writer/Route binding")
            logical_key = self.writer_execution_ref.logical_execution_key
            if self.writer_invocation_ref.logical_execution_key != logical_key:
                raise ValueError("SECTION Writer execution/invocation logical keys differ")
            if logical_key.route_decision_ref != self.route_decision_ref:
                raise ValueError("SECTION Writer logical RouteDecision differs")
            subject = logical_key.writer_subject_ref
            if (
                subject.run_id != self.run_id
                or subject.document_id != self.document_id
                or subject.section_id != self.section_id
            ):
                raise ValueError(
                    "SECTION Entry target identity contradicts Writer logical subject"
                )
            if (
                self.evidence_binding.binding_kind
                is not PostWriteEvidenceBindingKind.SECTION_WRITER_SNAPSHOT
            ):
                raise ValueError("SECTION Entry requires SECTION_WRITER_SNAPSHOT")
            if (
                self.evidence_binding.section_writer_snapshot_ref is None
                or self.evidence_binding.section_writer_snapshot_ref.writer_invocation_ref
                != self.writer_invocation_ref
            ):
                raise ValueError(
                    "SECTION Entry Evidence snapshot must bind exact Writer invocation"
                )
        else:
            if self.section_id is not None:
                raise ValueError("DOCUMENT Entry cannot carry section_id")
            if self.assembly_input_manifest_ref is None:
                raise ValueError("DOCUMENT Entry requires assembly_input_manifest_ref")
            if any(
                item is not None
                for item in (
                    self.writer_execution_ref,
                    self.writer_invocation_ref,
                    self.route_decision_ref,
                    self.generation_class,
                )
            ):
                raise ValueError(
                    "DOCUMENT Entry forbids singular Writer/Route/generation bindings"
                )
            if (
                self.evidence_binding.binding_kind
                is not PostWriteEvidenceBindingKind.DOCUMENT_PROJECTION
            ):
                raise ValueError("DOCUMENT Entry requires DOCUMENT_PROJECTION")
        return self


class SectionPostWriteEntrySchema(_FrozenEntry):
    schema_version: Literal["section_post_write_entry_v1"] = (
        "section_post_write_entry_v1"
    )
    contract_version: Literal["post_write_entry_binding_v1"] = (
        "post_write_entry_binding_v1"
    )
    identity: PostWriteEntryIdentitySchema

    @model_validator(mode="after")
    def _section_only(self) -> "SectionPostWriteEntrySchema":
        if self.identity.entry_scope is not ArtifactScope.SECTION:
            raise ValueError("SectionPostWriteEntry requires SECTION identity")
        return self


class DocumentPostWriteEntrySchema(_FrozenEntry):
    schema_version: Literal["document_post_write_entry_v1"] = (
        "document_post_write_entry_v1"
    )
    contract_version: Literal["post_write_entry_binding_v1"] = (
        "post_write_entry_binding_v1"
    )
    identity: PostWriteEntryIdentitySchema
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema

    @model_validator(mode="after")
    def _document_only(self) -> "DocumentPostWriteEntrySchema":
        if self.identity.entry_scope is not ArtifactScope.DOCUMENT:
            raise ValueError("DocumentPostWriteEntry requires DOCUMENT identity")
        if self.assembly_input_manifest_ref != self.identity.assembly_input_manifest_ref:
            raise ValueError(
                "DocumentPostWriteEntry assembly manifest contradicts identity"
            )
        return self


PostWriteEntrySchema = SectionPostWriteEntrySchema | DocumentPostWriteEntrySchema


def entry_ref_from_identity(identity: PostWriteEntryIdentitySchema) -> PostWriteEntryRefSchema:
    return PostWriteEntryRefSchema(
        entry_id=identity.entry_id,
        entry_schema_version=(
            "section_post_write_entry_v1"
            if identity.entry_scope is ArtifactScope.SECTION
            else "document_post_write_entry_v1"
        ),
        entry_contract_version="post_write_entry_binding_v1",
        entry_sha256=identity.entry_sha256,
    )


__all__ = [
    "DocumentPostWriteEntrySchema",
    "PostWriteEntryIdentitySchema",
    "PostWriteEntrySchema",
    "PostWriteEvidenceBindingKind",
    "PostWriteEvidenceBindingRef",
    "SectionPostWriteEntrySchema",
    "entry_ref_from_identity",
]
