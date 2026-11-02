"""Deterministic Post-write Section→Document lifecycle.

This module performs assembly/binding/currentness work only. It has no model,
retrieval, RAG assessment, or free-form Document writer dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Any

from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from schemas.rag import EvidenceBundleSchema, EvidenceDisposition, RAGEvidenceItemSchema

from apps.enterprise_document.schemas.post_write.control import (
    ArtifactUsabilityIntegrityState,
    PostWriteControlDecisionSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveAssemblySectionSelectionSchema,
    ActiveComparisonMode,
    ActiveDocumentBindingProjectionSchema,
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblyInputManifestRefSchema,
    AssemblyInputManifestSchema,
    AssemblyMechanicalRepairProjectionSchema,
    AssemblySectionEntrySchema,
    AssemblySelectionState,
    DocumentDefectAttributionSchema,
    DocumentDefectOwnership,
    DocumentEvidenceMemberRefSchema,
    DocumentEvidenceSnapshotProjectionSchema,
    DocumentEvidenceSnapshotRefSchema,
    DocumentEvidenceSourceBindingSchema,
    SectionAssemblyCandidateSchema,
)
from apps.enterprise_document.schemas.post_write.entry import (
    PostWriteEvidenceBindingKind,
    PostWriteEvidenceBindingRef,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    AvailabilityState,
    DeliveryGateDecisionRefSchema,
    DocumentAssemblyArtifactOriginSchema,
    EnforcementAction,
    EvidenceSnapshotRefSchema,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
    QualityDeliveryContractRefSchema,
)
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema

from .artifact_state import ArtifactStateService
from .binding import (
    DocumentPostWriteEntryAssembler,
    PostWriteEntryIdentityBuilder,
)
from .currentness import CurrentnessResolver
from .persistence import (
    ArtifactStateStore,
    ControlDecisionStore,
    DocumentBindingStore,
    PostWriteEntryStore,
    PostWritePersistenceIntegrityError,
)


class DocumentLifecycleError(ValueError):
    pass


class SectionAssemblyIneligible(DocumentLifecycleError):
    pass


class DocumentEvidenceIntegrityError(DocumentLifecycleError):
    pass


class DocumentAssemblyError(DocumentLifecycleError):
    pass


EvidenceBundleResolver = Callable[[EvidenceSnapshotRefSchema], EvidenceBundleSchema]
VisibleSectionPayloadProjector = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _model_hash_payload(model_cls, hash_field: str, **kwargs):
    placeholder = model_cls.model_construct(**kwargs, **{hash_field: "0" * 64})
    payload = placeholder.model_dump(mode="json")
    payload.pop(hash_field, None)
    return model_cls(**kwargs, **{hash_field: canonical_sha256(payload)})


@dataclass(frozen=True)
class SectionAssemblyEligibility:
    selection_state: AssemblySelectionState
    candidate: SectionAssemblyCandidateSchema | None
    reason_code: str
    selected_section_control_decision_ref: PostWriteControlDecisionRefSchema | None = None


class SectionAssemblyEligibilityResolver:
    """Resolve exact Section Control and project deterministic assembly eligibility."""

    def __init__(
        self,
        *,
        control_store: ControlDecisionStore,
        entry_store: PostWriteEntryStore,
    ) -> None:
        self.control_store = control_store
        self.entry_store = entry_store

    def resolve(
        self,
        *,
        section_key: str,
        control_decision_ref: PostWriteControlDecisionRefSchema,
        connection=None,
    ) -> SectionAssemblyEligibility:
        decision = self.control_store.resolve_exact(control_decision_ref, connection=connection)
        manifest = decision.input_manifest
        if manifest.artifact_scope is not ArtifactScope.SECTION:
            raise DocumentLifecycleError(
                "Section assembly eligibility requires SECTION ControlDecision"
            )
        entry = self.entry_store.resolve_exact(manifest.post_write_entry_ref, connection=connection)
        if entry.identity.entry_scope is not ArtifactScope.SECTION:
            raise DocumentLifecycleError(
                "Section ControlDecision resolves non-SECTION Entry"
            )
        if entry.identity.artifact_state_ref != manifest.artifact_state_ref:
            raise DocumentLifecycleError(
                "Section ControlDecision/Entry Artifact binding mismatch"
            )
        evidence_ref = entry.identity.evidence_binding.section_writer_snapshot_ref
        if evidence_ref is None:
            raise DocumentLifecycleError(
                "Section Entry has no Writer-visible EvidenceSnapshotRef"
            )

        currentness = decision.fresh_currentness_proof
        usability = decision.artifact_usability_integrity_proof
        if currentness.currentness_state is not CurrentnessState.CURRENT:
            return SectionAssemblyEligibility(
                selection_state=AssemblySelectionState.WITHHELD,
                candidate=None,
                reason_code="SECTION_CURRENTNESS_NOT_CURRENT",
                selected_section_control_decision_ref=decision.as_ref(),
            )
        if usability.state is not ArtifactUsabilityIntegrityState.TRUSTED_USABLE:
            return SectionAssemblyEligibility(
                selection_state=AssemblySelectionState.WITHHELD,
                candidate=None,
                reason_code="SECTION_ARTIFACT_NOT_TRUSTED_USABLE",
                selected_section_control_decision_ref=decision.as_ref(),
            )
        if decision.enforcement_action not in {
            EnforcementAction.OUTPUT,
            EnforcementAction.OUTPUT_BEST_EFFORT,
        }:
            return SectionAssemblyEligibility(
                selection_state=AssemblySelectionState.WITHHELD,
                candidate=None,
                reason_code=f"SECTION_ACTION_{decision.enforcement_action.value}",
                selected_section_control_decision_ref=decision.as_ref(),
            )

        generation_class = getattr(manifest, "generation_class", None)
        if generation_class is None:
            raise DocumentLifecycleError(
                "SECTION Control manifest is missing generation_class"
            )
        kwargs = {
            "section_key": str(section_key).strip(),
            "section_artifact_ref": manifest.artifact_state_ref,
            "section_post_write_entry_ref": manifest.post_write_entry_ref,
            "section_evidence_snapshot_ref": evidence_ref,
            "section_gate_decision_ref": decision.delivery_gate_decision_ref,
            "section_control_decision_ref": decision.as_ref(),
            "generation_class": generation_class,
            "quality_state": decision.quality_state_passthrough,
            "availability_state": decision.availability_projection.state,
            "enforcement_action": decision.enforcement_action,
        }
        candidate = _model_hash_payload(
            SectionAssemblyCandidateSchema,
            "candidate_hash",
            **kwargs,
        )
        return SectionAssemblyEligibility(
            selection_state=AssemblySelectionState.INCLUDED,
            candidate=candidate,
            selected_section_control_decision_ref=decision.as_ref(),
            reason_code=(
                "SECTION_OUTPUT"
                if decision.enforcement_action is EnforcementAction.OUTPUT
                else "SECTION_OUTPUT_BEST_EFFORT"
            ),
        )


class AssemblyInputManifestBuilder:
    def __init__(
        self,
        *,
        store: DocumentBindingStore,
        assembly_contract_version: str = "document_assembly_v2",
    ) -> None:
        self.store = store
        self.assembly_contract_version = str(assembly_contract_version).strip()
        if self.assembly_contract_version != "document_assembly_v2":
            raise ValueError("active assembly requires document_assembly_v2")

    @staticmethod
    def _entry(
        *,
        section_key: str,
        selection_state: AssemblySelectionState,
        candidate: SectionAssemblyCandidateSchema | None,
        reason_code: str,
        selected_section_control_decision_ref=None,
    ) -> AssemblySectionEntrySchema:
        kwargs = {
            "section_key": str(section_key).strip(),
            "selection_state": selection_state,
            "section_assembly_candidate": candidate,
            "selected_section_control_decision_ref": selected_section_control_decision_ref,
            "inclusion_reason_code": str(reason_code).strip(),
        }
        return _model_hash_payload(
            AssemblySectionEntrySchema,
            "entry_binding_hash",
            **kwargs,
        )

    def missing_entry(self, section_key: str, reason_code: str = "SECTION_MISSING"):
        return self._entry(
            section_key=section_key,
            selection_state=AssemblySelectionState.MISSING,
            candidate=None,
            reason_code=reason_code,
        )

    def selection_entry(
        self,
        *,
        section_key: str,
        eligibility: SectionAssemblyEligibility,
    ) -> AssemblySectionEntrySchema:
        return self._entry(
            section_key=section_key,
            selection_state=eligibility.selection_state,
            candidate=eligibility.candidate,
            reason_code=eligibility.reason_code,
            selected_section_control_decision_ref=(eligibility.selected_section_control_decision_ref
                or (eligibility.candidate.section_control_decision_ref if eligibility.candidate is not None else None)),
        )

    def build_and_persist(
        self,
        *,
        document_logical_id: str,
        required_section_keys: Iterable[str],
        ordered_section_entries: Iterable[AssemblySectionEntrySchema],
    ) -> AssemblyInputManifestRefSchema:
        required = tuple(str(item).strip() for item in required_section_keys)
        entries = tuple(
            AssemblySectionEntrySchema.model_validate(item)
            for item in ordered_section_entries
        )
        if not required or any(not item for item in required):
            raise DocumentAssemblyError("required section keys must be non-empty")
        if tuple(item.section_key for item in entries) != required:
            raise DocumentAssemblyError(
                "assembly entries must exactly follow required section order"
            )

        membership_payload = {
            "document_logical_id": str(document_logical_id).strip(),
            "assembly_contract_version": self.assembly_contract_version,
            "required_section_keys": list(required),
            "ordered_sections": [
                {
                    "section_key": item.section_key,
                    "selection_state": item.selection_state.value,
                    "section_artifact_ref": (
                        item.section_assembly_candidate.section_artifact_ref.model_dump(
                            mode="json"
                        )
                        if item.section_assembly_candidate is not None
                        else None
                    ),
                }
                for item in entries
            ],
        }
        membership_hash = canonical_sha256(membership_payload)
        provenance_hash = canonical_sha256(
            {
                "document_logical_id": str(document_logical_id).strip(),
                "assembly_contract_version": self.assembly_contract_version,
                "entries": [item.model_dump(mode="json") for item in entries],
            }
        )
        full_payload = {
            "schema_version": "assembly_input_manifest_v2",
            "assembly_contract_version": self.assembly_contract_version,
            "document_logical_id": str(document_logical_id).strip(),
            "required_section_keys": list(required),
            "ordered_section_entries": [
                item.model_dump(mode="json") for item in entries
            ],
            "assembly_membership_sha256": membership_hash,
            "assembly_control_provenance_sha256": provenance_hash,
        }
        full_hash = canonical_sha256(full_payload)
        manifest = AssemblyInputManifestSchema(
            assembly_contract_version=self.assembly_contract_version,
            manifest_id="assembly-manifest://sha256/" + full_hash,
            document_logical_id=str(document_logical_id).strip(),
            required_section_keys=required,
            ordered_section_entries=entries,
            assembly_membership_sha256=membership_hash,
            assembly_control_provenance_sha256=provenance_hash,
            full_manifest_sha256=full_hash,
        )
        return self.store.put_assembly_manifest_if_absent_or_same(manifest)


@dataclass(frozen=True)
class ResolvedDocumentEvidenceMember:
    member_ref: DocumentEvidenceMemberRefSchema
    item: RAGEvidenceItemSchema


@dataclass(frozen=True)
class DocumentEvidenceView:
    projection_ref: DocumentEvidenceSnapshotRefSchema
    source_bindings: tuple[DocumentEvidenceSourceBindingSchema, ...]
    members: tuple[ResolvedDocumentEvidenceMember, ...]


class DocumentEvidenceSnapshotProjector:
    """Project exact selected Section Evidence membership; never recompute assessment."""

    def __init__(
        self,
        *,
        binding_store: DocumentBindingStore,
        entry_store: PostWriteEntryStore,
        evidence_bundle_resolver: EvidenceBundleResolver,
        projection_contract_version: str = "document_evidence_projection_v1",
    ) -> None:
        if not callable(evidence_bundle_resolver):
            raise TypeError("evidence_bundle_resolver must be callable")
        self.binding_store = binding_store
        self.entry_store = entry_store
        self.evidence_bundle_resolver = evidence_bundle_resolver
        self.projection_contract_version = str(projection_contract_version).strip()

    @staticmethod
    def _resolve_bundle_exact(
        resolver: EvidenceBundleResolver,
        ref: EvidenceSnapshotRefSchema,
    ) -> EvidenceBundleSchema:
        try:
            bundle = EvidenceBundleSchema.model_validate(resolver(ref))
        except Exception as exc:
            raise DocumentEvidenceIntegrityError(
                "Section EvidenceSnapshot cannot be resolved exactly"
            ) from exc
        if evidence_bundle_sha256(bundle) != ref.evidence_bundle_sha256:
            raise DocumentEvidenceIntegrityError(
                "Section EvidenceSnapshot canonical hash mismatch"
            )
        return bundle

    def project_and_persist(
        self,
        manifest_ref: AssemblyInputManifestRefSchema,
    ) -> DocumentEvidenceSnapshotRefSchema:
        manifest = self.binding_store.resolve_assembly_manifest(manifest_ref)
        sources: list[DocumentEvidenceSourceBindingSchema] = []
        members: list[DocumentEvidenceMemberRefSchema] = []

        for entry in manifest.ordered_section_entries:
            candidate = entry.section_assembly_candidate
            if candidate is None:
                continue
            section_entry = self.entry_store.resolve_exact(
                candidate.section_post_write_entry_ref
            )
            if (
                section_entry.identity.artifact_state_ref
                != candidate.section_artifact_ref
                or section_entry.identity.entry_scope is not ArtifactScope.SECTION
                or section_entry.identity.evidence_binding.section_writer_snapshot_ref
                != candidate.section_evidence_snapshot_ref
            ):
                raise DocumentEvidenceIntegrityError(
                    "Manifest Section candidate/Entry/Evidence lineage mismatch"
                )
            source = DocumentEvidenceSourceBindingSchema(
                logical_section_id=entry.section_key,
                section_artifact_ref=candidate.section_artifact_ref,
                section_post_write_entry_ref=candidate.section_post_write_entry_ref,
                section_evidence_snapshot_ref=candidate.section_evidence_snapshot_ref,
            )
            sources.append(source)
            bundle = self._resolve_bundle_exact(
                self.evidence_bundle_resolver,
                candidate.section_evidence_snapshot_ref,
            )
            item_by_id = {item.evidence_id: item for item in bundle.items}
            for ordinal, evidence_id in enumerate(bundle.selected_evidence_ids):
                item = item_by_id.get(evidence_id)
                if item is None or item.disposition is not EvidenceDisposition.SELECTED:
                    raise DocumentEvidenceIntegrityError(
                        "selected Evidence member is absent or not Writer-visible"
                    )
                members.append(
                    DocumentEvidenceMemberRefSchema(
                        logical_section_id=entry.section_key,
                        source_evidence_snapshot_ref=candidate.section_evidence_snapshot_ref,
                        evidence_id=evidence_id,
                        evidence_item_sha256=canonical_sha256(
                            item.model_dump(mode="json", round_trip=True)
                        ),
                        source_member_ordinal=ordinal,
                    )
                )

        identity_payload = {
            "document_logical_id": manifest.document_logical_id,
            "projection_schema_version": "document_evidence_snapshot_projection_v1",
            "projection_contract_version": self.projection_contract_version,
            "source_bindings": [item.model_dump(mode="json") for item in sources],
            "selected_members": [item.model_dump(mode="json") for item in members],
        }
        projection_id = "document-evidence://sha256/" + canonical_sha256(
            identity_payload
        )
        hash_payload = {
            "schema_version": "document_evidence_snapshot_projection_v1",
            "projection_contract_version": self.projection_contract_version,
            "document_logical_id": manifest.document_logical_id,
            "source_bindings": [item.model_dump(mode="json") for item in sources],
            "selected_members": [item.model_dump(mode="json") for item in members],
        }
        projection = DocumentEvidenceSnapshotProjectionSchema(
            projection_contract_version=self.projection_contract_version,
            projection_id=projection_id,
            document_logical_id=manifest.document_logical_id,
            source_bindings=tuple(sources),
            selected_members=tuple(members),
            projection_sha256=canonical_sha256(hash_payload),
        )
        return self.binding_store.put_document_evidence_snapshot_if_absent_or_same(
            projection
        )


class DocumentEvidenceSnapshotResolver:
    def __init__(
        self,
        *,
        binding_store: DocumentBindingStore,
        evidence_bundle_resolver: EvidenceBundleResolver,
    ) -> None:
        self.binding_store = binding_store
        self.evidence_bundle_resolver = evidence_bundle_resolver

    def resolve(
        self,
        ref: DocumentEvidenceSnapshotRefSchema,
    ) -> DocumentEvidenceView:
        projection = self.binding_store.resolve_document_evidence_snapshot(ref)
        source_refs = {
            (
                item.logical_section_id,
                item.section_evidence_snapshot_ref.evidence_bundle_sha256,
            ): item
            for item in projection.source_bindings
        }
        resolved: list[ResolvedDocumentEvidenceMember] = []
        bundle_cache: dict[tuple[str, str], EvidenceBundleSchema] = {}
        for member in projection.selected_members:
            key = (
                member.logical_section_id,
                member.source_evidence_snapshot_ref.evidence_bundle_sha256,
            )
            if key not in source_refs:
                raise DocumentEvidenceIntegrityError(
                    "Document Evidence member has no exact source binding"
                )
            if key not in bundle_cache:
                bundle_cache[key] = DocumentEvidenceSnapshotProjector._resolve_bundle_exact(
                    self.evidence_bundle_resolver,
                    member.source_evidence_snapshot_ref,
                )
            bundle = bundle_cache[key]
            if member.evidence_id not in bundle.selected_evidence_ids:
                raise DocumentEvidenceIntegrityError(
                    "Document Evidence replay member is not selected in source snapshot"
                )
            item = next(
                (item for item in bundle.items if item.evidence_id == member.evidence_id),
                None,
            )
            if item is None or item.disposition is not EvidenceDisposition.SELECTED:
                raise DocumentEvidenceIntegrityError(
                    "Document Evidence replay member is unavailable"
                )
            if canonical_sha256(
                item.model_dump(mode="json", round_trip=True)
            ) != member.evidence_item_sha256:
                raise DocumentEvidenceIntegrityError(
                    "Document Evidence replay member hash mismatch"
                )
            resolved.append(
                ResolvedDocumentEvidenceMember(member_ref=member, item=item)
            )
        return DocumentEvidenceView(
            projection_ref=projection.as_ref(),
            source_bindings=projection.source_bindings,
            members=tuple(resolved),
        )


class PostWriteDocumentAssembler:
    """Deterministic assembly over exact Section Artifact payloads."""

    def __init__(
        self,
        *,
        artifact_service: ArtifactStateService,
        binding_store: DocumentBindingStore,
        visible_payload_projector: VisibleSectionPayloadProjector | None = None,
        document_payload_projector: Callable[[dict[str, Any], AssemblyInputManifestSchema], dict[str, Any]] | None = None,
        committed_document_resolver: Callable[..., ArtifactStateRefSchema | None] | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.binding_store = binding_store
        self.document_payload_projector = document_payload_projector
        self.committed_document_resolver = committed_document_resolver
        self.visible_payload_projector = visible_payload_projector or (
            lambda payload: dict(payload)
        )

    def source_document_payload(
        self,
        manifest: AssemblyInputManifestSchema,
        *, section_records=None,
    ) -> dict[str, Any]:
        """Exact included Section projection before any assembly-owned rendering."""
        included = []
        missing: list[str] = []
        withheld: list[str] = []
        for entry in manifest.ordered_section_entries:
            candidate = entry.section_assembly_candidate
            if candidate is None:
                if entry.selection_state is AssemblySelectionState.MISSING:
                    missing.append(entry.section_key)
                else:
                    withheld.append(entry.section_key)
                continue
            record = (self.artifact_service.resolve_exact(candidate.section_artifact_ref)
                if section_records is None else section_records[entry.section_key])
            if record.artifact_ref != candidate.section_artifact_ref:
                raise DocumentAssemblyError('exact Section projection differs from manifest candidate')
            visible = dict(
                self.visible_payload_projector(record.canonical_payload_ref)
            )
            included.append(
                {
                    "section_key": entry.section_key,
                    "visible_payload": visible,
                }
            )
        if not included:
            raise DocumentAssemblyError(
                "no assembly-eligible Section content; no valid Document Artifact"
            )
        return {
            "payload_kind": "document",
            "document_logical_id": manifest.document_logical_id,
            "included_sections": included,
            "missing_section_keys": missing,
            "withheld_section_keys": withheld,
            "partial": bool(missing or withheld),
        }

    def _canonical_document_payload(self, manifest: AssemblyInputManifestSchema) -> dict[str, Any]:
        source = self.source_document_payload(manifest)
        if self.document_payload_projector is None:
            return source
        return self.document_payload_projector(source, manifest)

    def assemble(
        self,
        *,
        run_id: str,
        document_id: str,
        manifest_ref: AssemblyInputManifestRefSchema,
        expected_parent_ref: ArtifactStateRefSchema | None = None,
    ) -> ArtifactStateRefSchema:
        manifest = self.binding_store.resolve_assembly_manifest(manifest_ref)
        if self.committed_document_resolver is not None:
            committed = self.committed_document_resolver(
                artifact_id=f"document-artifact:{run_id}:{document_id}",
                artifact_version=1 if expected_parent_ref is None else expected_parent_ref.artifact_version + 1,
                manifest=manifest, expected_parent_ref=expected_parent_ref)
            if committed is not None:
                return committed
        payload = self._canonical_document_payload(manifest)
        origin = DocumentAssemblyArtifactOriginSchema(
            assembly_input_manifest_ref=manifest_ref.manifest_id
        )
        artifact_id = f"document-artifact:{run_id}:{document_id}"
        if expected_parent_ref is None:
            return self.artifact_service.create_initial(
                artifact_id=artifact_id,
                artifact_scope=ArtifactScope.DOCUMENT,
                canonical_payload_ref=payload,
                origin=origin,
            )
        if (
            expected_parent_ref.artifact_scope is not ArtifactScope.DOCUMENT
            or expected_parent_ref.artifact_id != artifact_id
        ):
            raise DocumentAssemblyError(
                "Document reassembly expected parent belongs to another lineage"
            )
        return self.artifact_service.append_next_version(
            expected_parent_ref=expected_parent_ref,
            canonical_payload_ref=payload,
            origin=origin,
        )


class DocumentDefectAttributionResolver:
    """Build deterministic explicit attribution; never guesses ownership."""

    def attribute(
        self,
        *,
        document_gate_decision_ref: DeliveryGateDecisionRefSchema,
        blocking_condition_ref: str,
        ownership: DocumentDefectOwnership,
        affected_section_artifact_refs: Iterable[ArtifactStateRefSchema] = (),
        correction_reason_refs: Iterable[str] = (),
        exact_basis_refs: Iterable[str] = (),
        attribution_contract_version: str = "document_defect_attribution_v1",
    ) -> DocumentDefectAttributionSchema:
        affected = tuple(
            sorted(
                (
                    ArtifactStateRefSchema.model_validate(item)
                    for item in affected_section_artifact_refs
                ),
                key=lambda item: (item.artifact_id, item.artifact_version),
            )
        )
        reasons = tuple(sorted(set(str(item).strip() for item in correction_reason_refs)))
        basis = tuple(sorted(set(str(item).strip() for item in exact_basis_refs)))
        identity_payload = {
            "attribution_contract_version": attribution_contract_version,
            "document_gate_decision_ref": document_gate_decision_ref.model_dump(
                mode="json"
            ),
            "blocking_condition_ref": str(blocking_condition_ref).strip(),
            "ownership": ownership.value,
            "affected_section_artifact_refs": [
                item.model_dump(mode="json") for item in affected
            ],
            "correction_reason_refs": list(reasons),
            "exact_basis_refs": list(basis),
        }
        attribution_id = (
            "document-defect-attribution://sha256/"
            + canonical_sha256(identity_payload)
        )
        kwargs = {
            "attribution_contract_version": attribution_contract_version,
            "attribution_id": attribution_id,
            "document_gate_decision_ref": document_gate_decision_ref,
            "blocking_condition_ref": str(blocking_condition_ref).strip(),
            "ownership": ownership,
            "affected_section_artifact_refs": affected,
            "correction_reason_refs": reasons,
            "exact_basis_refs": basis,
        }
        return _model_hash_payload(
            DocumentDefectAttributionSchema,
            "attribution_hash",
            **kwargs,
        )


def active_selection_from_manifest_entry(
    entry: AssemblySectionEntrySchema,
) -> ActiveAssemblySectionSelectionSchema:
    candidate = entry.section_assembly_candidate
    kwargs = {
        "selected_section_control_decision_ref": entry.selected_section_control_decision_ref,
        "section_key": entry.section_key,
        "selection_state": entry.selection_state,
        "section_assembly_candidate": candidate,
        "section_assembly_candidate_hash": (
            candidate.candidate_hash if candidate is not None else None
        ),
        "artifact_state_ref": (
            candidate.section_artifact_ref if candidate is not None else None
        ),
        "post_write_entry_ref": (
            candidate.section_post_write_entry_ref if candidate is not None else None
        ),
        "section_evidence_snapshot_ref": (
            candidate.section_evidence_snapshot_ref
            if candidate is not None
            else None
        ),
        "control_decision_ref": (
            candidate.section_control_decision_ref if candidate is not None else None
        ),
        "section_quality_state": (
            candidate.quality_state if candidate is not None else None
        ),
        "section_availability_state": (
            candidate.availability_state if candidate is not None else None
        ),
        "section_enforcement_action": (
            candidate.enforcement_action if candidate is not None else None
        ),
        "inclusion_reason": entry.inclusion_reason_code,
    }
    return _model_hash_payload(
        ActiveAssemblySectionSelectionSchema,
        "selection_binding_hash",
        **kwargs,
    )


@dataclass(frozen=True)
class DocumentLifecycleResult:
    manifest_ref: AssemblyInputManifestRefSchema
    document_evidence_ref: DocumentEvidenceSnapshotRefSchema
    document_artifact_ref: ArtifactStateRefSchema
    document_entry_ref: PostWriteEntryRefSchema
    active_projection: ActiveDocumentBindingProjectionSchema


class DocumentLifecycleCoordinator:
    """Single deterministic owner for one Section→Document lifecycle transition."""

    def __init__(
        self,
        *,
        manifest_builder: AssemblyInputManifestBuilder,
        evidence_projector: DocumentEvidenceSnapshotProjector,
        document_assembler: PostWriteDocumentAssembler,
        entry_assembler: DocumentPostWriteEntryAssembler,
    ) -> None:
        self.manifest_builder = manifest_builder
        self.evidence_projector = evidence_projector
        self.document_assembler = document_assembler
        self.entry_assembler = entry_assembler

    def materialize(
        self,
        *,
        run_id: str,
        document_id: str,
        document_logical_id: str,
        required_section_keys: Iterable[str],
        ordered_section_entries: Iterable[AssemblySectionEntrySchema],
        requirement_ref: RequirementRefSchema,
        quality_delivery_contract_ref: QualityDeliveryContractRefSchema,
        expected_parent_ref: ArtifactStateRefSchema | None = None,
    ) -> DocumentLifecycleResult:
        entries = tuple(
            AssemblySectionEntrySchema.model_validate(item)
            for item in ordered_section_entries
        )
        manifest_ref = self.manifest_builder.build_and_persist(
            document_logical_id=document_logical_id,
            required_section_keys=required_section_keys,
            ordered_section_entries=entries,
        )
        evidence_ref = self.evidence_projector.project_and_persist(manifest_ref)
        artifact_ref = self.document_assembler.assemble(
            run_id=run_id,
            document_id=document_id,
            manifest_ref=manifest_ref,
            expected_parent_ref=expected_parent_ref,
        )
        evidence_binding = PostWriteEvidenceBindingRef(
            binding_kind=PostWriteEvidenceBindingKind.DOCUMENT_PROJECTION,
            document_projection_ref=evidence_ref,
        )
        entry_ref = self.entry_assembler.assemble_and_persist(
            run_id=run_id,
            document_id=document_id,
            assembly_input_manifest_ref=manifest_ref,
            artifact_state_ref=artifact_ref,
            evidence_binding=evidence_binding,
            requirement_ref=requirement_ref,
            quality_delivery_contract_ref=quality_delivery_contract_ref,
        )
        selections = tuple(active_selection_from_manifest_entry(item) for item in entries)
        active_projection = ActiveDocumentBindingProjectionSchema(
            run_id=run_id,
            document_id=document_id,
            assembly_input_manifest_ref=manifest_ref,
            artifact_state_ref=artifact_ref,
            evidence_binding=evidence_binding,
            requirement_ref=requirement_ref,
            quality_delivery_contract_ref=quality_delivery_contract_ref,
            assembly_owner_section_selections=selections,
        )
        return DocumentLifecycleResult(
            manifest_ref=manifest_ref,
            document_evidence_ref=evidence_ref,
            document_artifact_ref=artifact_ref,
            document_entry_ref=entry_ref,
            active_projection=active_projection,
        )


__all__ = [
    "AssemblyInputManifestBuilder",
    "DocumentAssemblyError",
    "DocumentDefectAttributionResolver",
    "DocumentEvidenceIntegrityError",
    "DocumentEvidenceSnapshotProjector",
    "DocumentEvidenceSnapshotResolver",
    "DocumentEvidenceView",
    "DocumentLifecycleCoordinator",
    "DocumentLifecycleError",
    "DocumentLifecycleResult",
    "PostWriteDocumentAssembler",
    "ResolvedDocumentEvidenceMember",
    "SectionAssemblyEligibility",
    "SectionAssemblyEligibilityResolver",
    "SectionAssemblyIneligible",
    "active_selection_from_manifest_entry",
]
