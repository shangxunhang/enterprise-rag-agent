"""Post-write exact binding and Entry identity assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveDocumentBindingProjectionSchema,
    ActiveSectionBindingProjectionSchema,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblyInputManifestRefSchema,
)
from apps.enterprise_document.schemas.post_write.entry import (
    DocumentPostWriteEntrySchema,
    PostWriteEntryIdentitySchema,
    PostWriteEvidenceBindingRef,
    SectionPostWriteEntrySchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    EvidenceSnapshotRefSchema,
    QualityDeliveryContractRefSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
)
from core.hashing import canonical_sha256

from .persistence import PostWriteEntryStore


class PostWriteEntryIdentityBuilder:
    """Sole deterministic Entry identity producer used by persistence/currentness."""

    @staticmethod
    def _finalize(payload: dict) -> PostWriteEntryIdentitySchema:
        entry_id = "post-write-entry:" + canonical_sha256(payload)
        entry_sha256 = canonical_sha256(
            {
                "entry_id": entry_id,
                "identity": payload,
            }
        )
        return PostWriteEntryIdentitySchema(
            **payload,
            entry_id=entry_id,
            entry_sha256=entry_sha256,
        )

    def build_section(
        self,
        *,
        run_id: str,
        document_id: str,
        section_id: str,
        artifact_state_ref: ArtifactStateRefSchema,
        writer_execution_ref: WriterExecutionRefSchema,
        writer_invocation_ref: WriterGenerationInvocationRefSchema,
        route_decision_ref: PreWriteRouteDecisionRefSchema,
        evidence_binding: PostWriteEvidenceBindingRef,
        requirement_ref: RequirementRefSchema,
        quality_delivery_contract_ref: QualityDeliveryContractRefSchema,
        generation_class: WriterGenerationClass,
    ) -> PostWriteEntryIdentitySchema:
        if artifact_state_ref.artifact_scope is not ArtifactScope.SECTION:
            raise ValueError("section Entry identity requires SECTION Artifact")
        logical_key = writer_execution_ref.logical_execution_key
        if writer_invocation_ref.logical_execution_key != logical_key:
            raise ValueError("Writer execution/invocation logical keys differ")
        if logical_key.route_decision_ref != route_decision_ref:
            raise ValueError("Writer logical execution RouteDecision differs")
        if (
            evidence_binding.section_writer_snapshot_ref is None
            or evidence_binding.section_writer_snapshot_ref.writer_invocation_ref
            != writer_invocation_ref
        ):
            raise ValueError(
                "Section evidence snapshot must bind the exact Writer invocation"
            )
        payload = {
            "entry_scope": ArtifactScope.SECTION,
            "run_id": run_id,
            "document_id": document_id,
            "section_id": section_id,
            "assembly_input_manifest_ref": None,
            "artifact_state_ref": artifact_state_ref,
            "evidence_binding": evidence_binding,
            "requirement_ref": requirement_ref,
            "quality_delivery_contract_ref": quality_delivery_contract_ref,
            "writer_execution_ref": writer_execution_ref,
            "writer_invocation_ref": writer_invocation_ref,
            "route_decision_ref": route_decision_ref,
            "generation_class": generation_class,
        }
        return self._finalize(payload)

    def build_document(
        self,
        *,
        run_id: str,
        document_id: str,
        assembly_input_manifest_ref: AssemblyInputManifestRefSchema,
        artifact_state_ref: ArtifactStateRefSchema,
        evidence_binding: PostWriteEvidenceBindingRef,
        requirement_ref: RequirementRefSchema,
        quality_delivery_contract_ref: QualityDeliveryContractRefSchema,
    ) -> PostWriteEntryIdentitySchema:
        if artifact_state_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise ValueError("document Entry identity requires DOCUMENT Artifact")
        payload = {
            "entry_scope": ArtifactScope.DOCUMENT,
            "run_id": run_id,
            "document_id": document_id,
            "section_id": None,
            "assembly_input_manifest_ref": assembly_input_manifest_ref,
            "artifact_state_ref": artifact_state_ref,
            "evidence_binding": evidence_binding,
            "requirement_ref": requirement_ref,
            "quality_delivery_contract_ref": quality_delivery_contract_ref,
            "writer_execution_ref": None,
            "writer_invocation_ref": None,
            "route_decision_ref": None,
            "generation_class": None,
        }
        return self._finalize(payload)

    def from_active_section_projection(
        self,
        projection: ActiveSectionBindingProjectionSchema,
    ) -> PostWriteEntryIdentitySchema:
        return self.build_section(
            run_id=projection.run_id,
            document_id=projection.document_id,
            section_id=projection.section_id,
            artifact_state_ref=projection.artifact_state_ref,
            writer_execution_ref=projection.writer_execution_ref,
            writer_invocation_ref=projection.writer_invocation_ref,
            route_decision_ref=projection.route_decision_ref,
            evidence_binding=projection.evidence_binding,
            requirement_ref=projection.requirement_ref,
            quality_delivery_contract_ref=projection.quality_delivery_contract_ref,
            generation_class=projection.generation_class,
        )

    def from_active_document_projection(
        self,
        projection: ActiveDocumentBindingProjectionSchema,
    ) -> PostWriteEntryIdentitySchema:
        return self.build_document(
            run_id=projection.run_id,
            document_id=projection.document_id,
            assembly_input_manifest_ref=projection.assembly_input_manifest_ref,
            artifact_state_ref=projection.artifact_state_ref,
            evidence_binding=projection.evidence_binding,
            requirement_ref=projection.requirement_ref,
            quality_delivery_contract_ref=projection.quality_delivery_contract_ref,
        )


class SectionPostWriteEntryAssembler:
    def __init__(
        self,
        *,
        store: PostWriteEntryStore,
        identity_builder: PostWriteEntryIdentityBuilder,
    ) -> None:
        self.store = store
        self.identity_builder = identity_builder

    def assemble_and_persist(self, **kwargs):
        identity = self.identity_builder.build_section(**kwargs)
        entry = SectionPostWriteEntrySchema(identity=identity)
        return self.store.put_if_absent_or_same(entry)


class DocumentPostWriteEntryAssembler:
    def __init__(
        self,
        *,
        store: PostWriteEntryStore,
        identity_builder: PostWriteEntryIdentityBuilder,
    ) -> None:
        self.store = store
        self.identity_builder = identity_builder

    def assemble_and_persist(self, **kwargs):
        identity = self.identity_builder.build_document(**kwargs)
        entry = DocumentPostWriteEntrySchema(
            identity=identity,
            assembly_input_manifest_ref=identity.assembly_input_manifest_ref,
        )
        return self.store.put_if_absent_or_same(entry)


class PostWriteEntryResolver:
    def __init__(self, store: PostWriteEntryStore) -> None:
        self.store = store

    def resolve_exact(self, ref):
        return self.store.resolve_exact(ref)


@dataclass(frozen=True)
class ResolvedEvidenceView:
    binding: PostWriteEvidenceBindingRef
    resolved_value: Any


class PostWriteEvidenceBindingResolver:
    """Resolve the exact scope-valid evidence binding without recomputation."""

    def __init__(
        self,
        *,
        section_evidence_resolver: Callable[[object], Any],
        document_evidence_resolver: Callable[[object], Any],
    ) -> None:
        if not callable(section_evidence_resolver) or not callable(
            document_evidence_resolver
        ):
            raise TypeError("PostWriteEvidenceBindingResolver requires resolvers")
        self.section_evidence_resolver = section_evidence_resolver
        self.document_evidence_resolver = document_evidence_resolver

    def resolve(
        self,
        binding: PostWriteEvidenceBindingRef,
    ) -> ResolvedEvidenceView:
        exact = PostWriteEvidenceBindingRef.model_validate(binding)
        if exact.binding_kind.value == "SECTION_WRITER_SNAPSHOT":
            assert exact.section_writer_snapshot_ref is not None
            value = self.section_evidence_resolver(
                exact.section_writer_snapshot_ref
            )
        else:
            assert exact.document_projection_ref is not None
            value = self.document_evidence_resolver(exact.document_projection_ref)
        return ResolvedEvidenceView(binding=exact, resolved_value=value)


__all__ = [
    "DocumentPostWriteEntryAssembler",
    "PostWriteEntryIdentityBuilder",
    "PostWriteEntryResolver",
    "PostWriteEvidenceBindingResolver",
    "ResolvedEvidenceView",
    "SectionPostWriteEntryAssembler",
]
