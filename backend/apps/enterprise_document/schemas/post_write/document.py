"""Target Post-write Document assembly/lifecycle value contracts.

Document values carry assembly/binding provenance and deterministic action-routing
proofs. They do not own quality truth, RAG Evidence assessment, or free-form
Document generation.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationClass
from core.hashing import canonical_sha256

from .foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    AvailabilityState,
    CorrectionPolicyRefSchema,
    DeliveryGateDecisionRefSchema,
    EnforcementAction,
    EvidenceSnapshotRefSchema,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenDocumentContract(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class AssemblySelectionState(str, Enum):
    INCLUDED = "INCLUDED"
    MISSING = "MISSING"
    WITHHELD = "WITHHELD"


class SectionAssemblyCandidateSchema(_FrozenDocumentContract):
    schema_version: Literal["section_assembly_candidate_v1"] = (
        "section_assembly_candidate_v1"
    )
    section_key: NonEmptyStr
    section_artifact_ref: ArtifactStateRefSchema
    section_post_write_entry_ref: PostWriteEntryRefSchema
    section_evidence_snapshot_ref: EvidenceSnapshotRefSchema
    section_gate_decision_ref: DeliveryGateDecisionRefSchema
    section_control_decision_ref: PostWriteControlDecisionRefSchema
    generation_class: WriterGenerationClass
    quality_state: OverallQualityState
    availability_state: AvailabilityState
    enforcement_action: EnforcementAction
    candidate_hash: Sha256Hex

    @model_validator(mode="after")
    def _candidate_integrity(self) -> "SectionAssemblyCandidateSchema":
        if self.section_artifact_ref.artifact_scope is not ArtifactScope.SECTION:
            raise ValueError("SectionAssemblyCandidate requires SECTION Artifact")
        if self.enforcement_action is EnforcementAction.OUTPUT:
            if (
                self.quality_state is not OverallQualityState.PASS
                or self.availability_state is not AvailabilityState.AVAILABLE
            ):
                raise ValueError("OUTPUT assembly candidate requires PASS/AVAILABLE")
        elif self.enforcement_action is EnforcementAction.OUTPUT_BEST_EFFORT:
            if (
                self.quality_state is OverallQualityState.PASS
                or self.availability_state is not AvailabilityState.BEST_EFFORT
            ):
                raise ValueError(
                    "OUTPUT_BEST_EFFORT candidate requires non-PASS/BEST_EFFORT"
                )
        else:
            raise ValueError("CORRECT/BLOCK are not assembly-eligible Section actions")
        payload = self.model_dump(mode="json")
        payload.pop("candidate_hash", None)
        if canonical_sha256(payload) != self.candidate_hash:
            raise ValueError("SectionAssemblyCandidate hash mismatch")
        return self


class AssemblySectionEntrySchema(_FrozenDocumentContract):
    schema_version: Literal["assembly_section_entry_v2"] = "assembly_section_entry_v2"
    section_key: NonEmptyStr
    selection_state: AssemblySelectionState
    section_assembly_candidate: SectionAssemblyCandidateSchema | None = None
    selected_section_control_decision_ref: PostWriteControlDecisionRefSchema | None = None
    inclusion_reason_code: NonEmptyStr
    entry_binding_hash: Sha256Hex

    @model_validator(mode="after")
    def _entry_integrity(self) -> "AssemblySectionEntrySchema":
        included = self.selection_state is AssemblySelectionState.INCLUDED
        evaluated = self.selection_state is not AssemblySelectionState.MISSING
        if evaluated != (self.selected_section_control_decision_ref is not None):
            raise ValueError("evaluated Section requires selected Control; MISSING forbids it")
        if included != (self.section_assembly_candidate is not None):
            raise ValueError(
                "INCLUDED requires candidate; MISSING/WITHHELD forbid candidate"
            )
        if (
            self.section_assembly_candidate is not None
            and self.section_assembly_candidate.section_key != self.section_key
        ):
            raise ValueError("assembly entry candidate section_key mismatch")
        if included and self.selected_section_control_decision_ref != self.section_assembly_candidate.section_control_decision_ref:
            raise ValueError("selected Section Control differs from INCLUDED candidate")
        payload = self.model_dump(mode="json")
        payload.pop("entry_binding_hash", None)
        if canonical_sha256(payload) != self.entry_binding_hash:
            raise ValueError("AssemblySectionEntry binding hash mismatch")
        return self


class AssemblyInputManifestRefSchema(_FrozenDocumentContract):
    schema_version: Literal["assembly_input_manifest_ref_v2"] = (
        "assembly_input_manifest_ref_v2"
    )
    manifest_id: NonEmptyStr
    manifest_schema_version: Literal["assembly_input_manifest_v2"]
    assembly_contract_version: Literal["document_assembly_v2"]
    full_manifest_sha256: Sha256Hex


class AssemblyInputManifestSchema(_FrozenDocumentContract):
    schema_version: Literal["assembly_input_manifest_v2"] = "assembly_input_manifest_v2"
    assembly_contract_version: Literal["document_assembly_v2"]
    manifest_id: NonEmptyStr
    document_logical_id: NonEmptyStr
    required_section_keys: tuple[NonEmptyStr, ...] = Field(min_length=1)
    ordered_section_entries: tuple[AssemblySectionEntrySchema, ...] = Field(min_length=1)
    assembly_membership_sha256: Sha256Hex
    assembly_control_provenance_sha256: Sha256Hex
    full_manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def _manifest_integrity(self) -> "AssemblyInputManifestSchema":
        required = tuple(self.required_section_keys)
        if len(required) != len(set(required)):
            raise ValueError("required_section_keys must be ordered-unique")
        entry_keys = tuple(item.section_key for item in self.ordered_section_entries)
        if entry_keys != required:
            raise ValueError(
                "ordered_section_entries must exactly cover required_section_keys"
            )

        membership_payload = {
            "document_logical_id": self.document_logical_id,
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
                for item in self.ordered_section_entries
            ],
        }
        if canonical_sha256(membership_payload) != self.assembly_membership_sha256:
            raise ValueError("Assembly membership hash mismatch")

        provenance_payload = {
            "document_logical_id": self.document_logical_id,
            "assembly_contract_version": self.assembly_contract_version,
            "entries": [item.model_dump(mode="json") for item in self.ordered_section_entries],
        }
        if (
            canonical_sha256(provenance_payload)
            != self.assembly_control_provenance_sha256
        ):
            raise ValueError("Assembly control provenance hash mismatch")

        full_payload = self.model_dump(mode="json")
        full_payload.pop("manifest_id", None)
        full_payload.pop("full_manifest_sha256", None)
        expected_full = canonical_sha256(full_payload)
        if expected_full != self.full_manifest_sha256:
            raise ValueError("AssemblyInputManifest full hash mismatch")
        expected_id = "assembly-manifest://sha256/" + expected_full
        if expected_id != self.manifest_id:
            raise ValueError("AssemblyInputManifest id mismatch")
        return self

    def as_ref(self) -> AssemblyInputManifestRefSchema:
        return AssemblyInputManifestRefSchema(
            manifest_id=self.manifest_id,
            manifest_schema_version=self.schema_version,
            assembly_contract_version=self.assembly_contract_version,
            full_manifest_sha256=self.full_manifest_sha256,
        )


class DocumentEvidenceSourceBindingSchema(_FrozenDocumentContract):
    schema_version: Literal["document_evidence_source_binding_v1"] = (
        "document_evidence_source_binding_v1"
    )
    logical_section_id: NonEmptyStr
    section_artifact_ref: ArtifactStateRefSchema
    section_post_write_entry_ref: PostWriteEntryRefSchema
    section_evidence_snapshot_ref: EvidenceSnapshotRefSchema

    @model_validator(mode="after")
    def _source_scope(self) -> "DocumentEvidenceSourceBindingSchema":
        if self.section_artifact_ref.artifact_scope is not ArtifactScope.SECTION:
            raise ValueError("Document Evidence source requires SECTION Artifact")
        return self


class DocumentEvidenceMemberRefSchema(_FrozenDocumentContract):
    schema_version: Literal["document_evidence_member_ref_v1"] = (
        "document_evidence_member_ref_v1"
    )
    logical_section_id: NonEmptyStr
    source_evidence_snapshot_ref: EvidenceSnapshotRefSchema
    evidence_id: NonEmptyStr
    evidence_item_sha256: Sha256Hex
    source_member_ordinal: int = Field(ge=0)


class DocumentEvidenceSnapshotRefSchema(_FrozenDocumentContract):
    schema_version: Literal["document_evidence_snapshot_ref_v1"] = (
        "document_evidence_snapshot_ref_v1"
    )
    projection_id: NonEmptyStr
    projection_schema_version: NonEmptyStr
    projection_contract_version: NonEmptyStr
    projection_sha256: Sha256Hex


class DocumentEvidenceSnapshotProjectionSchema(_FrozenDocumentContract):
    schema_version: Literal["document_evidence_snapshot_projection_v1"] = (
        "document_evidence_snapshot_projection_v1"
    )
    projection_contract_version: NonEmptyStr
    projection_id: NonEmptyStr
    document_logical_id: NonEmptyStr
    source_bindings: tuple[DocumentEvidenceSourceBindingSchema, ...]
    selected_members: tuple[DocumentEvidenceMemberRefSchema, ...]
    projection_sha256: Sha256Hex

    @model_validator(mode="after")
    def _projection_integrity(self) -> "DocumentEvidenceSnapshotProjectionSchema":
        source_keys = [
            (
                item.logical_section_id,
                item.section_post_write_entry_ref.entry_id,
                item.section_evidence_snapshot_ref.evidence_bundle_sha256,
            )
            for item in self.source_bindings
        ]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("Document Evidence source bindings must be ordered-unique")

        member_keys = [
            (
                item.logical_section_id,
                item.source_evidence_snapshot_ref.evidence_bundle_sha256,
                item.evidence_id,
                item.source_member_ordinal,
            )
            for item in self.selected_members
        ]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("Document Evidence members must be ordered-unique")

        payload = self.model_dump(mode="json")
        payload.pop("projection_id", None)
        payload.pop("projection_sha256", None)
        expected_hash = canonical_sha256(payload)
        if expected_hash != self.projection_sha256:
            raise ValueError("Document Evidence projection hash mismatch")
        expected_id = "document-evidence://sha256/" + canonical_sha256(
            {
                "document_logical_id": self.document_logical_id,
                "projection_schema_version": self.schema_version,
                "projection_contract_version": self.projection_contract_version,
                "source_bindings": [
                    item.model_dump(mode="json") for item in self.source_bindings
                ],
                "selected_members": [
                    item.model_dump(mode="json") for item in self.selected_members
                ],
            }
        )
        if expected_id != self.projection_id:
            raise ValueError("Document Evidence projection id mismatch")
        return self

    def as_ref(self) -> DocumentEvidenceSnapshotRefSchema:
        return DocumentEvidenceSnapshotRefSchema(
            projection_id=self.projection_id,
            projection_schema_version=self.schema_version,
            projection_contract_version=self.projection_contract_version,
            projection_sha256=self.projection_sha256,
        )


class DocumentDefectOwnership(str, Enum):
    SECTION_CONTENT = "SECTION_CONTENT"
    ASSEMBLY_MECHANICAL = "ASSEMBLY_MECHANICAL"
    UNATTRIBUTABLE = "UNATTRIBUTABLE"


class DocumentDefectAttributionSchema(_FrozenDocumentContract):
    schema_version: Literal["document_defect_attribution_v1"] = (
        "document_defect_attribution_v1"
    )
    attribution_contract_version: NonEmptyStr
    attribution_id: NonEmptyStr
    document_gate_decision_ref: DeliveryGateDecisionRefSchema
    blocking_condition_ref: NonEmptyStr
    ownership: DocumentDefectOwnership
    affected_section_artifact_refs: tuple[ArtifactStateRefSchema, ...] = ()
    correction_reason_refs: tuple[NonEmptyStr, ...] = ()
    exact_basis_refs: tuple[NonEmptyStr, ...] = ()
    attribution_hash: Sha256Hex

    @model_validator(mode="after")
    def _attribution_integrity(self) -> "DocumentDefectAttributionSchema":
        refs = tuple(self.affected_section_artifact_refs)
        ref_keys = [(item.artifact_id, item.artifact_version) for item in refs]
        if ref_keys != sorted(ref_keys) or len(ref_keys) != len(set(ref_keys)):
            raise ValueError("affected Section Artifact refs must be canonical unique")
        if any(item.artifact_scope is not ArtifactScope.SECTION for item in refs):
            raise ValueError("Document defect attribution may target Sections only")
        if (
            self.ownership is DocumentDefectOwnership.SECTION_CONTENT
            and not refs
        ):
            raise ValueError("SECTION_CONTENT attribution requires affected Section")
        if (
            self.ownership is not DocumentDefectOwnership.SECTION_CONTENT
            and refs
        ):
            raise ValueError(
                "only SECTION_CONTENT attribution may carry affected Section refs"
            )
        for name in ("correction_reason_refs", "exact_basis_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")

        identity_payload = {
            "attribution_contract_version": self.attribution_contract_version,
            "document_gate_decision_ref": self.document_gate_decision_ref.model_dump(
                mode="json"
            ),
            "blocking_condition_ref": self.blocking_condition_ref,
            "ownership": self.ownership.value,
            "affected_section_artifact_refs": [
                item.model_dump(mode="json") for item in refs
            ],
            "correction_reason_refs": list(self.correction_reason_refs),
            "exact_basis_refs": list(self.exact_basis_refs),
        }
        expected_id = "document-defect-attribution://sha256/" + canonical_sha256(
            identity_payload
        )
        if expected_id != self.attribution_id:
            raise ValueError("DocumentDefectAttribution id mismatch")
        payload = self.model_dump(mode="json")
        payload.pop("attribution_hash", None)
        if canonical_sha256(payload) != self.attribution_hash:
            raise ValueError("DocumentDefectAttribution hash mismatch")
        return self


class AssemblyMechanicalRepairProjectionSchema(_FrozenDocumentContract):
    schema_version: Literal["assembly_mechanical_repair_projection_v1"] = (
        "assembly_mechanical_repair_projection_v1"
    )
    repair_id: NonEmptyStr
    repair_kind: NonEmptyStr
    correction_policy_ref: CorrectionPolicyRefSchema
    correction_policy_rule_ref: NonEmptyStr
    exact_input_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    expected_reassembly_scope: NonEmptyStr
    repair_projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _repair_integrity(self) -> "AssemblyMechanicalRepairProjectionSchema":
        if self.exact_input_refs != tuple(sorted(set(self.exact_input_refs))):
            raise ValueError("assembly mechanical repair inputs must be ordered-unique")
        identity_payload = {
            "repair_kind": self.repair_kind,
            "correction_policy_ref": self.correction_policy_ref.model_dump(mode="json"),
            "correction_policy_rule_ref": self.correction_policy_rule_ref,
            "exact_input_refs": list(self.exact_input_refs),
            "expected_reassembly_scope": self.expected_reassembly_scope,
        }
        expected_id = "assembly-mechanical-repair://sha256/" + canonical_sha256(
            identity_payload
        )
        if expected_id != self.repair_id:
            raise ValueError("AssemblyMechanicalRepairProjection id mismatch")
        payload = self.model_dump(mode="json")
        payload.pop("repair_projection_hash", None)
        if canonical_sha256(payload) != self.repair_projection_hash:
            raise ValueError("AssemblyMechanicalRepairProjection hash mismatch")
        return self


__all__ = [
    "AssemblyInputManifestRefSchema",
    "AssemblyInputManifestSchema",
    "AssemblyMechanicalRepairProjectionSchema",
    "AssemblySectionEntrySchema",
    "AssemblySelectionState",
    "DocumentDefectAttributionSchema",
    "DocumentDefectOwnership",
    "DocumentEvidenceMemberRefSchema",
    "DocumentEvidenceSnapshotProjectionSchema",
    "DocumentEvidenceSnapshotRefSchema",
    "DocumentEvidenceSourceBindingSchema",
    "SectionAssemblyCandidateSchema",
]
