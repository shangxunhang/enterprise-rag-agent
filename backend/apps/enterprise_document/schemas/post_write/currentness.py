"""Post-write entry-based currentness proof schemas.

No durable active-head/currentness entity exists. The proof embeds the exact active
binding projection used for one decision and the expected Entry identity built from it.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import ConfigDict, StringConstraints, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
)
from core.hashing import canonical_sha256

from .document import (
    AssemblyInputManifestRefSchema,
    AssemblySelectionState,
    SectionAssemblyCandidateSchema,
)
from .entry import PostWriteEntryIdentitySchema, PostWriteEvidenceBindingRef
from .foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    AvailabilityState,
    EnforcementAction,
    EvidenceSnapshotRefSchema,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
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


class _FrozenCurrentness(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class CurrentnessState(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    INDETERMINATE = "INDETERMINATE"


class CurrentnessMatchState(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    MATERIALIZED_ENTRY_CONTRADICTION = "MATERIALIZED_ENTRY_CONTRADICTION"
    SELECTION_MISMATCH = "SELECTION_MISMATCH"
    SELECTION_UNRESOLVED = "SELECTION_UNRESOLVED"


class ActiveComparisonMode(str, Enum):
    MATERIALIZED_ENTRY = "MATERIALIZED_ENTRY"
    EXPECTED_BINDING_IDENTITY = "EXPECTED_BINDING_IDENTITY"


class ActiveSectionBindingProjectionSchema(_FrozenCurrentness):
    schema_version: Literal["active_section_binding_projection_v1"] = (
        "active_section_binding_projection_v1"
    )
    run_id: NonEmptyStr
    document_id: NonEmptyStr
    section_id: NonEmptyStr
    artifact_state_ref: ArtifactStateRefSchema
    evidence_binding: PostWriteEvidenceBindingRef
    requirement_ref: RequirementRefSchema
    quality_delivery_contract_ref: QualityDeliveryContractRefSchema
    writer_execution_ref: WriterExecutionRefSchema
    writer_invocation_ref: WriterGenerationInvocationRefSchema
    route_decision_ref: PreWriteRouteDecisionRefSchema
    generation_class: WriterGenerationClass

    @model_validator(mode="after")
    def _section_scope(self) -> "ActiveSectionBindingProjectionSchema":
        if self.artifact_state_ref.artifact_scope is not ArtifactScope.SECTION:
            raise ValueError("active Section projection requires SECTION Artifact")
        return self


class ActiveAssemblySectionSelectionSchema(_FrozenCurrentness):
    schema_version: Literal["active_assembly_section_selection_v3"] = (
        "active_assembly_section_selection_v3"
    )
    section_key: NonEmptyStr
    selection_state: AssemblySelectionState
    section_assembly_candidate: SectionAssemblyCandidateSchema | None = None
    section_assembly_candidate_hash: Sha256Hex | None = None
    artifact_state_ref: ArtifactStateRefSchema | None = None
    post_write_entry_ref: PostWriteEntryRefSchema | None = None
    section_evidence_snapshot_ref: EvidenceSnapshotRefSchema | None = None
    control_decision_ref: PostWriteControlDecisionRefSchema | None = None
    selected_section_control_decision_ref: PostWriteControlDecisionRefSchema | None = None
    section_quality_state: OverallQualityState | None = None
    section_availability_state: AvailabilityState | None = None
    section_enforcement_action: EnforcementAction | None = None
    inclusion_reason: NonEmptyStr
    selection_binding_hash: Sha256Hex

    @model_validator(mode="after")
    def _selection_shape(self) -> "ActiveAssemblySectionSelectionSchema":
        included = self.selection_state is AssemblySelectionState.INCLUDED
        evaluated = self.selection_state is not AssemblySelectionState.MISSING
        if evaluated != (self.selected_section_control_decision_ref is not None):
            raise ValueError("evaluated selection requires selected Control; MISSING forbids it")
        if included and self.selected_section_control_decision_ref != self.control_decision_ref:
            raise ValueError("selected Control differs from included Control lineage")
        exact_values = (
            self.section_assembly_candidate,
            self.section_assembly_candidate_hash,
            self.artifact_state_ref,
            self.post_write_entry_ref,
            self.section_evidence_snapshot_ref,
            self.control_decision_ref,
            self.section_quality_state,
            self.section_availability_state,
            self.section_enforcement_action,
        )
        if included and any(item is None for item in exact_values):
            raise ValueError("INCLUDED selection requires exact candidate lineage")
        if not included and any(item is not None for item in exact_values):
            raise ValueError(
                "MISSING/WITHHELD selection cannot fabricate included lineage"
            )
        if included:
            candidate = self.section_assembly_candidate
            assert candidate is not None
            if (
                candidate.section_key != self.section_key
                or candidate.candidate_hash != self.section_assembly_candidate_hash
                or candidate.section_artifact_ref != self.artifact_state_ref
                or candidate.section_post_write_entry_ref != self.post_write_entry_ref
                or candidate.section_evidence_snapshot_ref
                != self.section_evidence_snapshot_ref
                or candidate.section_control_decision_ref != self.control_decision_ref
                or candidate.quality_state != self.section_quality_state
                or candidate.availability_state != self.section_availability_state
                or candidate.enforcement_action != self.section_enforcement_action
            ):
                raise ValueError("INCLUDED selection candidate/provenance mismatch")
        payload = self.model_dump(mode="json")
        payload.pop("selection_binding_hash", None)
        if canonical_sha256(payload) != self.selection_binding_hash:
            raise ValueError("active assembly selection binding hash mismatch")
        return self


class ActiveDocumentBindingProjectionSchema(_FrozenCurrentness):
    schema_version: Literal["active_document_binding_projection_v1"] = (
        "active_document_binding_projection_v1"
    )
    run_id: NonEmptyStr
    document_id: NonEmptyStr
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema
    artifact_state_ref: ArtifactStateRefSchema
    evidence_binding: PostWriteEvidenceBindingRef
    requirement_ref: RequirementRefSchema
    quality_delivery_contract_ref: QualityDeliveryContractRefSchema
    assembly_owner_section_selections: tuple[
        ActiveAssemblySectionSelectionSchema, ...
    ]

    @model_validator(mode="after")
    def _document_scope(self) -> "ActiveDocumentBindingProjectionSchema":
        if self.artifact_state_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise ValueError("active Document projection requires DOCUMENT Artifact")
        keys = [item.section_key for item in self.assembly_owner_section_selections]
        if len(keys) != len(set(keys)):
            raise ValueError("assembly selection section_key values must be unique")
        return self


ActivePostWriteBindingProjectionSchema = (
    ActiveSectionBindingProjectionSchema | ActiveDocumentBindingProjectionSchema
)


class CurrentnessProofSchema(_FrozenCurrentness):
    schema_version: Literal["currentness_proof_v1"] = "currentness_proof_v1"
    comparison_mode: ActiveComparisonMode
    historical_entry_ref: PostWriteEntryRefSchema
    active_projection: ActivePostWriteBindingProjectionSchema
    expected_active_entry_identity: PostWriteEntryIdentitySchema
    active_materialized_post_write_entry_ref: PostWriteEntryRefSchema | None = None
    currentness_state: CurrentnessState
    match_state: CurrentnessMatchState
    proof_sha256: Sha256Hex

    @model_validator(mode="after")
    def _mode_shape(self) -> "CurrentnessProofSchema":
        if self.comparison_mode is ActiveComparisonMode.MATERIALIZED_ENTRY:
            if self.active_materialized_post_write_entry_ref is None:
                raise ValueError(
                    "MATERIALIZED_ENTRY mode requires active materialized Entry ref"
                )
        elif self.active_materialized_post_write_entry_ref is not None:
            raise ValueError(
                "EXPECTED_BINDING_IDENTITY mode forbids fake materialized Entry ref"
            )
        return self


__all__ = [
    "ActiveAssemblySectionSelectionSchema",
    "ActiveComparisonMode",
    "ActiveDocumentBindingProjectionSchema",
    "ActivePostWriteBindingProjectionSchema",
    "ActiveSectionBindingProjectionSchema",
    "AssemblySelectionState",
    "CurrentnessMatchState",
    "CurrentnessProofSchema",
    "CurrentnessState",
]
