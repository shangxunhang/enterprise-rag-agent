"""Target Post-write release/publication contracts.

These schemas are isolated from the legacy Stage-F release authority. Target
Release consumes exact Post-write Control and canonical Run-completion evidence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import ConfigDict, StringConstraints, model_validator

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAggregateResult,
    RunCompletionAssessmentRefSchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.schemas.artifact_reference import (
    ArtifactReferenceSchema,
)
from apps.enterprise_document.schemas.contract_base import ContractSchema
from core.hashing import canonical_sha256

from .control import (
    ArtifactUsabilityIntegrityProofSchema,
    ArtifactUsabilityIntegrityState,
)
from .currentness import CurrentnessProofSchema, CurrentnessState
from .document import AssemblyInputManifestRefSchema
from .foundation import (
    ArtifactOutputConformanceProofSchema,
    ArtifactScope,
    ArtifactStateRefSchema,
    EnforcementAction,
    OverallQualityState,
    OutputConformanceState,
    PostWriteControlDecisionRefSchema,
    ValidationFactRefSchema,
)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenReleaseContract(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class PostWriteReleaseMode(str, Enum):
    STANDARD = "STANDARD"
    BEST_EFFORT = "BEST_EFFORT"


class PublicationReuseState(str, Enum):
    NEW_COMMIT = "NEW_COMMIT"
    EXACT_REQUEST_REPLAY = "EXACT_REQUEST_REPLAY"
    ALREADY_PUBLISHED_SAME_OUTPUT = "ALREADY_PUBLISHED_SAME_OUTPUT"


class PostWriteReleaseRequestSchema(_FrozenReleaseContract):
    schema_version: Literal["post_write_release_request_v1"] = (
        "post_write_release_request_v1"
    )
    release_contract_version: NonEmptyStr
    task_id: NonEmptyStr
    run_id: NonEmptyStr
    document_logical_id: NonEmptyStr
    document_artifact_ref: ArtifactStateRefSchema
    post_write_control_decision_ref: PostWriteControlDecisionRefSchema
    output_format: NonEmptyStr
    validated_output_binding_sha256: Sha256Hex
    publication_target_id: NonEmptyStr
    request_sha256: Sha256Hex

    @model_validator(mode="after")
    def _request_integrity(self) -> "PostWriteReleaseRequestSchema":
        if self.document_artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise ValueError("Target Release requires DOCUMENT Artifact")
        payload = self.model_dump(mode="json")
        payload.pop("request_sha256", None)
        if canonical_sha256(payload) != self.request_sha256:
            raise ValueError("PostWriteReleaseRequest hash mismatch")
        return self


class PostWriteReleaseInputProofSchema(_FrozenReleaseContract):
    schema_version: Literal["post_write_release_input_proof_v1"] = (
        "post_write_release_input_proof_v1"
    )
    release_contract_version: NonEmptyStr
    release_request_sha256: Sha256Hex

    run_id: NonEmptyStr
    run_completion_assessment_ref: RunCompletionAssessmentRefSchema
    resolved_run_completion_assessment_sha256: Sha256Hex
    run_lifecycle_revision: int
    resolved_run_lifecycle_record_sha256: Sha256Hex
    run_lifecycle_status: Literal[RunLifecycleStatus.SUCCESS] = (
        RunLifecycleStatus.SUCCESS
    )

    document_artifact_ref: ArtifactStateRefSchema
    post_write_control_decision_ref: PostWriteControlDecisionRefSchema
    resolved_control_decision_hash: Sha256Hex
    enforcement_action: EnforcementAction

    release_time_currentness_proof: CurrentnessProofSchema
    release_time_artifact_usability_proof: ArtifactUsabilityIntegrityProofSchema
    output_conformance_proof: ArtifactOutputConformanceProofSchema

    release_payload_artifact_ref: ArtifactReferenceSchema
    release_payload_sha256: Sha256Hex
    validated_output_binding_sha256: Sha256Hex
    output_format: NonEmptyStr
    renderer_contract_version: NonEmptyStr

    quality_state_passthrough: OverallQualityState
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()

    verified_at: datetime
    proof_sha256: Sha256Hex

    @model_validator(mode="after")
    def _proof_integrity(self) -> "PostWriteReleaseInputProofSchema":
        if self.verified_at.tzinfo is None:
            raise ValueError("release proof timestamp must be timezone-aware")
        if self.run_lifecycle_revision < 0:
            raise ValueError("run_lifecycle_revision must be >= 0")
        if (
            self.run_completion_assessment_ref.run_id != self.run_id
            or self.run_completion_assessment_ref.aggregate_result
            is not RunCompletionAggregateResult.SUCCESS
        ):
            raise ValueError("RunCompletionAssessment release binding mismatch")
        if (
            self.resolved_control_decision_hash
            != self.post_write_control_decision_ref.control_decision_hash
        ):
            raise ValueError("resolved Control decision hash mismatch")
        if self.enforcement_action not in {
            EnforcementAction.OUTPUT,
            EnforcementAction.OUTPUT_BEST_EFFORT,
        }:
            raise ValueError("Target Release proof forbids CORRECT/BLOCK")
        if (
            self.release_time_currentness_proof.currentness_state
            is not CurrentnessState.CURRENT
            or self.release_time_currentness_proof.active_projection.artifact_state_ref
            != self.document_artifact_ref
        ):
            raise ValueError("Target Release proof requires CURRENT exact Artifact")
        usability = self.release_time_artifact_usability_proof
        if (
            usability.state is not ArtifactUsabilityIntegrityState.TRUSTED_USABLE
            or usability.artifact_state_ref != self.document_artifact_ref
            or usability.currentness_proof_sha256
            != self.release_time_currentness_proof.proof_sha256
        ):
            raise ValueError("Target Release proof requires trusted exact Artifact")
        conformance = self.output_conformance_proof
        if (
            conformance.state is not OutputConformanceState.CONFORMING
            or conformance.artifact_id != self.document_artifact_ref.artifact_id
            or conformance.artifact_hash != self.document_artifact_ref.artifact_hash
            or conformance.validated_output_binding_sha256
            != self.validated_output_binding_sha256
            or conformance.output_format != self.output_format
            or conformance.renderer_contract_version
            != self.renderer_contract_version
            or conformance.output_payload_sha256 != self.release_payload_sha256
        ):
            raise ValueError("Target Release output conformance binding mismatch")
        if (
            self.release_payload_artifact_ref.content_hash
            != f"sha256:{self.release_payload_sha256}"
        ):
            raise ValueError("release payload Artifact hash mismatch")
        fact_ids = [item.fact_id for item in self.blocking_fact_refs]
        if fact_ids != sorted(fact_ids) or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("release blocking_fact_refs must be canonical unique")
        if self.unresolved_reason_refs != tuple(
            sorted(set(self.unresolved_reason_refs))
        ):
            raise ValueError("release unresolved_reason_refs must be canonical unique")

        payload = self.model_dump(mode="json")
        payload.pop("proof_sha256", None)
        if canonical_sha256(payload) != self.proof_sha256:
            raise ValueError("PostWriteReleaseInputProof hash mismatch")
        return self


class PostWriteReleaseManifestSchema(_FrozenReleaseContract):
    schema_version: Literal["post_write_release_manifest_v1"] = (
        "post_write_release_manifest_v1"
    )
    release_id: NonEmptyStr
    idempotency_key: Sha256Hex
    release_contract_version: NonEmptyStr
    request_sha256: Sha256Hex

    task_id: NonEmptyStr
    run_id: NonEmptyStr
    document_logical_id: NonEmptyStr
    document_artifact_ref: ArtifactStateRefSchema
    post_write_control_decision_ref: PostWriteControlDecisionRefSchema
    run_completion_assessment_ref: RunCompletionAssessmentRefSchema
    run_lifecycle_revision: int

    release_mode: PostWriteReleaseMode
    enforcement_action_passthrough: EnforcementAction
    quality_state_passthrough: OverallQualityState
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()

    output_format: NonEmptyStr
    renderer_contract_version: NonEmptyStr
    validated_output_binding_sha256: Sha256Hex
    publication_target_id: NonEmptyStr

    release_payload_artifact: ArtifactReferenceSchema
    release_payload_sha256: Sha256Hex
    release_input_proof_artifact: ArtifactReferenceSchema
    release_input_proof_sha256: Sha256Hex

    manifest_payload_sha256: Sha256Hex
    publisher: NonEmptyStr
    published_at: datetime
    immutable: Literal[True] = True

    @model_validator(mode="after")
    def _manifest_integrity(self) -> "PostWriteReleaseManifestSchema":
        if self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        identity_payload = {
            "release_contract_version": self.release_contract_version,
            "document_artifact_ref": self.document_artifact_ref.model_dump(
                mode="json"
            ),
            "output_format": self.output_format,
            "validated_output_binding_sha256": (
                self.validated_output_binding_sha256
            ),
            "publication_target_id": self.publication_target_id,
        }
        expected_key = canonical_sha256(identity_payload)
        if self.idempotency_key != expected_key:
            raise ValueError("Target publication identity mismatch")
        if self.release_id != "post-write-release://sha256/" + expected_key:
            raise ValueError("Target release_id mismatch")
        if self.run_lifecycle_revision < 0:
            raise ValueError("run_lifecycle_revision must be >= 0")
        if (
            self.run_completion_assessment_ref.run_id != self.run_id
            or self.run_completion_assessment_ref.aggregate_result
            is not RunCompletionAggregateResult.SUCCESS
        ):
            raise ValueError("manifest RunCompletionAssessment mismatch")
        if self.enforcement_action_passthrough is EnforcementAction.OUTPUT:
            if self.release_mode is not PostWriteReleaseMode.STANDARD:
                raise ValueError("OUTPUT requires STANDARD release mode")
        elif (
            self.enforcement_action_passthrough
            is EnforcementAction.OUTPUT_BEST_EFFORT
        ):
            if self.release_mode is not PostWriteReleaseMode.BEST_EFFORT:
                raise ValueError(
                    "OUTPUT_BEST_EFFORT requires BEST_EFFORT release mode"
                )
        else:
            raise ValueError("release manifest forbids CORRECT/BLOCK")
        if (
            self.release_payload_artifact.content_hash
            != f"sha256:{self.release_payload_sha256}"
        ):
            raise ValueError("manifest release payload hash mismatch")
        fact_ids = [item.fact_id for item in self.blocking_fact_refs]
        if fact_ids != sorted(fact_ids) or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("manifest blocking_fact_refs must be canonical unique")
        if self.unresolved_reason_refs != tuple(
            sorted(set(self.unresolved_reason_refs))
        ):
            raise ValueError("manifest unresolved_reason_refs must be canonical unique")

        payload = self.model_dump(mode="json")
        payload.pop("manifest_payload_sha256", None)
        if canonical_sha256(payload) != self.manifest_payload_sha256:
            raise ValueError("PostWriteReleaseManifest hash mismatch")
        return self


class PostWritePublishedReleaseSchema(_FrozenReleaseContract):
    schema_version: Literal["post_write_published_release_v1"] = (
        "post_write_published_release_v1"
    )
    manifest: PostWriteReleaseManifestSchema
    manifest_artifact: ArtifactReferenceSchema
    verified_after_write: Literal[True] = True
    reuse_state: PublicationReuseState

    @model_validator(mode="after")
    def _published_integrity(self) -> "PostWritePublishedReleaseSchema":
        manifest = self.manifest
        ref = self.manifest_artifact
        if (
            ref.task_id != manifest.task_id
            or ref.run_id != manifest.run_id
            or ref.artifact_ref
            != f"target-release://{manifest.idempotency_key}/manifest"
        ):
            raise ValueError("Target release manifest Artifact identity mismatch")
        return self


__all__ = [
    "PostWritePublishedReleaseSchema",
    "PostWriteReleaseInputProofSchema",
    "PostWriteReleaseManifestSchema",
    "PostWriteReleaseMode",
    "PostWriteReleaseRequestSchema",
    "PublicationReuseState",
]
