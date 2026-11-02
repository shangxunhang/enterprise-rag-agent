"""Entry-based Post-write currentness resolution."""

from __future__ import annotations

from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveAssemblySectionSelectionSchema,
    ActiveComparisonMode,
    ActiveDocumentBindingProjectionSchema,
    ActivePostWriteBindingProjectionSchema,
    ActiveSectionBindingProjectionSchema,
    CurrentnessMatchState,
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblyInputManifestSchema,
    AssemblySelectionState,
)
from apps.enterprise_document.schemas.post_write.foundation import PostWriteEntryRefSchema
from core.hashing import canonical_sha256

from .binding import PostWriteEntryIdentityBuilder
from .persistence import (
    DocumentBindingStore,
    PostWriteEntryStore,
    PostWritePersistenceIntegrityError,
)


class CurrentnessIntegrityError(PostWritePersistenceIntegrityError):
    pass


class CurrentnessResolver:
    def __init__(
        self,
        *,
        entry_store: PostWriteEntryStore,
        identity_builder: PostWriteEntryIdentityBuilder,
        document_binding_store: DocumentBindingStore | None = None,
    ) -> None:
        self.entry_store = entry_store
        self.identity_builder = identity_builder
        self.document_binding_store = document_binding_store

    def _expected_identity(
        self,
        projection: ActivePostWriteBindingProjectionSchema,
    ):
        if isinstance(projection, ActiveSectionBindingProjectionSchema):
            return self.identity_builder.from_active_section_projection(projection)
        if isinstance(projection, ActiveDocumentBindingProjectionSchema):
            return self.identity_builder.from_active_document_projection(projection)
        raise CurrentnessIntegrityError("unsupported active binding projection type")

    @staticmethod
    def _selection_payload_from_manifest_entry(entry) -> dict:
        candidate = entry.section_assembly_candidate
        payload = {
            "schema_version": "active_assembly_section_selection_v3",
            "selected_section_control_decision_ref": (
                entry.selected_section_control_decision_ref.model_dump(mode="json")
                if entry.selected_section_control_decision_ref is not None else None),
            "section_key": entry.section_key,
            "selection_state": entry.selection_state.value,
            "section_assembly_candidate": (
                candidate.model_dump(mode="json") if candidate is not None else None
            ),
            "section_assembly_candidate_hash": (
                candidate.candidate_hash if candidate is not None else None
            ),
            "artifact_state_ref": (
                candidate.section_artifact_ref.model_dump(mode="json")
                if candidate is not None
                else None
            ),
            "post_write_entry_ref": (
                candidate.section_post_write_entry_ref.model_dump(mode="json")
                if candidate is not None
                else None
            ),
            "section_evidence_snapshot_ref": (
                candidate.section_evidence_snapshot_ref.model_dump(mode="json")
                if candidate is not None
                else None
            ),
            "control_decision_ref": (
                candidate.section_control_decision_ref.model_dump(mode="json")
                if candidate is not None
                else None
            ),
            "section_quality_state": (
                candidate.quality_state.value if candidate is not None else None
            ),
            "section_availability_state": (
                candidate.availability_state.value if candidate is not None else None
            ),
            "section_enforcement_action": (
                candidate.enforcement_action.value if candidate is not None else None
            ),
            "inclusion_reason": entry.inclusion_reason_code,
        }
        return payload

    def _document_selection_state(
        self,
        *,
        historical_entry,
        active_projection: ActiveDocumentBindingProjectionSchema,
        connection=None,
    ) -> tuple[CurrentnessState | None, CurrentnessMatchState | None]:
        if self.document_binding_store is None:
            raise CurrentnessIntegrityError(
                "Document currentness requires DocumentBindingStore"
            )
        manifest_ref = historical_entry.identity.assembly_input_manifest_ref
        if manifest_ref is None:
            raise CurrentnessIntegrityError(
                "historical Document Entry has no AssemblyInputManifestRef"
            )
        manifest = self.document_binding_store.resolve_assembly_manifest(manifest_ref, connection=connection)
        active = tuple(active_projection.assembly_owner_section_selections)
        active_keys = tuple(item.section_key for item in active)
        required = tuple(manifest.required_section_keys)

        if len(active_keys) != len(set(active_keys)):
            raise CurrentnessIntegrityError(
                "active Document selection has duplicate section keys"
            )
        if set(active_keys) != set(required):
            return CurrentnessState.INDETERMINATE, CurrentnessMatchState.SELECTION_UNRESOLVED
        if active_keys != required:
            return CurrentnessState.INDETERMINATE, CurrentnessMatchState.SELECTION_UNRESOLVED

        persisted_hashes = tuple(
            canonical_sha256(self._selection_payload_from_manifest_entry(entry))
            for entry in manifest.ordered_section_entries
        )
        active_hashes = tuple(item.selection_binding_hash for item in active)
        if persisted_hashes != active_hashes:
            return CurrentnessState.STALE, CurrentnessMatchState.SELECTION_MISMATCH
        return None, None

    def prove(
        self,
        *,
        historical_entry_ref: PostWriteEntryRefSchema,
        active_projection: ActivePostWriteBindingProjectionSchema,
        comparison_mode: ActiveComparisonMode,
        active_materialized_post_write_entry_ref: PostWriteEntryRefSchema | None = None,
        connection=None,
    ) -> CurrentnessProofSchema:
        historical_entry = self.entry_store.resolve_exact(historical_entry_ref, connection=connection)
        selection_state = None
        selection_match = None
        if isinstance(active_projection, ActiveDocumentBindingProjectionSchema):
            selection_state, selection_match = self._document_selection_state(
                historical_entry=historical_entry,
                active_projection=active_projection,
                connection=connection,
            )
        expected = self._expected_identity(active_projection)

        if comparison_mode is ActiveComparisonMode.MATERIALIZED_ENTRY:
            if active_materialized_post_write_entry_ref is None:
                raise CurrentnessIntegrityError(
                    "MATERIALIZED_ENTRY mode requires exact active Entry ref"
                )
            active_entry = self.entry_store.resolve_exact(
                active_materialized_post_write_entry_ref, connection=connection
            )
            if active_entry.identity != expected:
                raise CurrentnessIntegrityError(
                    "materialized active Entry contradicts exact active binding projection"
                )
        elif active_materialized_post_write_entry_ref is not None:
            raise CurrentnessIntegrityError(
                "EXPECTED_BINDING_IDENTITY mode forbids materialized Entry ref"
            )

        if selection_state is not None:
            state = selection_state
            assert selection_match is not None
            match = selection_match
        elif historical_entry.identity == expected:
            state = CurrentnessState.CURRENT
            match = CurrentnessMatchState.EXACT_MATCH
        else:
            state = CurrentnessState.STALE
            match = CurrentnessMatchState.IDENTITY_MISMATCH

        preimage = {
            "schema_version": "currentness_proof_v1",
            "comparison_mode": comparison_mode.value,
            "historical_entry_ref": historical_entry_ref.model_dump(mode="json"),
            "active_projection": active_projection.model_dump(mode="json"),
            "expected_active_entry_identity": expected.model_dump(mode="json"),
            "active_materialized_post_write_entry_ref": (
                active_materialized_post_write_entry_ref.model_dump(mode="json")
                if active_materialized_post_write_entry_ref is not None
                else None
            ),
            "currentness_state": state.value,
            "match_state": match.value,
        }
        return CurrentnessProofSchema(
            comparison_mode=comparison_mode,
            historical_entry_ref=historical_entry_ref,
            active_projection=active_projection,
            expected_active_entry_identity=expected,
            active_materialized_post_write_entry_ref=active_materialized_post_write_entry_ref,
            currentness_state=state,
            match_state=match,
            proof_sha256=canonical_sha256(preimage),
        )


__all__ = ["CurrentnessIntegrityError", "CurrentnessResolver"]
