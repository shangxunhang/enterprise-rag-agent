"""Target Post-write release publication.

This module is intentionally isolated from legacy Stage-F release authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAggregateResult,
    RunCompletionAssessmentRecordSchema,
    RunCompletionAssessmentRefSchema,
    RunLifecycleRecordSchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.schemas.post_write.control import (
    ArtifactUsabilityIntegrityState,
    DocumentPostWriteControlInputManifestSchema,
    PostWriteControlDecisionSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    ActiveComparisonMode,
    ActiveDocumentBindingProjectionSchema,
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactOutputConformanceProofSchema,
    ArtifactScope,
    ArtifactStateRecordSchema,
    ArtifactValidatedOutputBindingSchema,
    EnforcementAction,
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.post_write.release import (
    PostWritePublishedReleaseSchema,
    PostWriteReleaseInputProofSchema,
    PostWriteReleaseManifestSchema,
    PostWriteReleaseMode,
    PostWriteReleaseRequestSchema,
    PublicationReuseState,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    ArtifactIntegrityError,
    FileArtifactStore,
)
from core.hashing import canonical_sha256
from core.persistence.interprocess_lock import InterProcessFileLock

from .artifact_state import (
    ArtifactOutputConformanceVerifier,
    ArtifactStateIntegrityError,
)
from .control import ArtifactUsabilityIntegrityResolver
from .currentness import CurrentnessResolver
from .persistence import (
    ArtifactStateStore,
    ControlDecisionStore,
    PostWritePersistenceIntegrityError,
    PostWriteReleaseCommitFence,
)


class PostWriteReleaseError(ValueError):
    pass


class PostWriteReleaseEligibilityError(PostWriteReleaseError):
    pass


class PostWriteReleaseIntegrityError(PostWriteReleaseError):
    pass


class PostWriteReleaseConflictError(PostWriteReleaseError):
    pass


RunCompletionAssessmentResolver = Callable[
    [RunCompletionAssessmentRefSchema],
    RunCompletionAssessmentRecordSchema,
]
RunLifecycleRevisionResolver = Callable[[str, int], RunLifecycleRecordSchema]
ActiveDocumentBindingSupplier = Callable[
    [], ActiveDocumentBindingProjectionSchema
]
ReleaseClock = Callable[[], datetime]


@dataclass(frozen=True)
class ResolvedReleaseInput:
    request: PostWriteReleaseRequestSchema
    artifact_record: ArtifactStateRecordSchema
    output_binding: ArtifactValidatedOutputBindingSchema
    output_conformance_proof: ArtifactOutputConformanceProofSchema
    control_decision: PostWriteControlDecisionSchema
    currentness_proof: CurrentnessProofSchema
    artifact_usability_proof: object
    run_completion_assessment: RunCompletionAssessmentRecordSchema
    run_lifecycle_record: RunLifecycleRecordSchema


class PostWriteReleaseInputIntegrityGuard:
    """Resolve exact Target authorities and prove one NEW publication input."""

    def __init__(
        self,
        *,
        artifact_state_store: ArtifactStateStore,
        control_store: ControlDecisionStore,
        currentness_resolver: CurrentnessResolver,
        artifact_usability_resolver: ArtifactUsabilityIntegrityResolver,
        output_conformance_verifier: ArtifactOutputConformanceVerifier,
        run_completion_assessment_resolver: RunCompletionAssessmentResolver,
        run_lifecycle_revision_resolver: RunLifecycleRevisionResolver,
    ) -> None:
        self.artifact_state_store = artifact_state_store
        self.control_store = control_store
        self.currentness_resolver = currentness_resolver
        self.artifact_usability_resolver = artifact_usability_resolver
        self.output_conformance_verifier = output_conformance_verifier
        self.run_completion_assessment_resolver = (
            run_completion_assessment_resolver
        )
        self.run_lifecycle_revision_resolver = (
            run_lifecycle_revision_resolver
        )

    @staticmethod
    def _find_output_binding(
        record: ArtifactStateRecordSchema,
        request: PostWriteReleaseRequestSchema,
    ) -> ArtifactValidatedOutputBindingSchema:
        matches = [
            item
            for item in record.validated_output_bindings
            if item.output_format == request.output_format
            and item.binding_sha256
            == request.validated_output_binding_sha256
        ]
        if len(matches) != 1:
            raise PostWriteReleaseEligibilityError(
                "requested exact validated output binding is missing or ambiguous"
            )
        return matches[0]

    def resolve_preflight_output(
        self,
        request: PostWriteReleaseRequestSchema,
    ) -> tuple[ArtifactStateRecordSchema, ArtifactValidatedOutputBindingSchema]:
        exact = PostWriteReleaseRequestSchema.model_validate(request)
        try:
            record = self.artifact_state_store.resolve_exact(
                exact.document_artifact_ref
            )
        except PostWritePersistenceIntegrityError as exc:
            raise PostWriteReleaseIntegrityError(
                "release Document Artifact cannot be resolved exactly"
            ) from exc
        if record.artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise PostWriteReleaseEligibilityError(
                "Target Release requires DOCUMENT Artifact"
            )
        binding = self._find_output_binding(record, exact)
        if (
            binding.output_artifact_ref.task_id != exact.task_id
            or binding.output_artifact_ref.run_id != exact.run_id
        ):
            raise PostWriteReleaseIntegrityError(
                "validated output Artifact task/run binding mismatch"
            )
        try:
            self.output_conformance_verifier.resolve_bound_conforming(
                record=record,
                output_binding=binding,
            )
        except ArtifactStateIntegrityError as exc:
            raise PostWriteReleaseIntegrityError(str(exc)) from exc
        return record, binding

    def validate_new_publication(
        self,
        *,
        request: PostWriteReleaseRequestSchema,
        run_completion_assessment_ref: RunCompletionAssessmentRefSchema,
        run_lifecycle_revision: int,
        active_document_binding: ActiveDocumentBindingProjectionSchema,
    ) -> ResolvedReleaseInput:
        exact = PostWriteReleaseRequestSchema.model_validate(request)
        completion_ref = RunCompletionAssessmentRefSchema.model_validate(
            run_completion_assessment_ref
        )
        active = ActiveDocumentBindingProjectionSchema.model_validate(
            active_document_binding
        )

        try:
            control = self.control_store.resolve_exact(
                exact.post_write_control_decision_ref
            )
            artifact_record = self.artifact_state_store.resolve_exact(
                exact.document_artifact_ref
            )
        except PostWritePersistenceIntegrityError as exc:
            raise PostWriteReleaseIntegrityError(str(exc)) from exc

        if not isinstance(
            control.input_manifest,
            DocumentPostWriteControlInputManifestSchema,
        ):
            raise PostWriteReleaseEligibilityError(
                "Target Release requires exact DOCUMENT ControlDecision"
            )
        if control.enforcement_action not in {
            EnforcementAction.OUTPUT,
            EnforcementAction.OUTPUT_BEST_EFFORT,
        }:
            raise PostWriteReleaseEligibilityError(
                "Target Release rejects CORRECT/BLOCK"
            )
        if (
            control.input_manifest.artifact_state_ref
            != exact.document_artifact_ref
            or control.as_ref() != exact.post_write_control_decision_ref
        ):
            raise PostWriteReleaseIntegrityError(
                "release request differs from exact ControlDecision subject"
            )
        if active.run_id != exact.run_id:
            raise PostWriteReleaseIntegrityError(
                "active Document binding belongs to another Run"
            )

        output_binding = self._find_output_binding(artifact_record, exact)
        try:
            conformance = (
                self.output_conformance_verifier.resolve_bound_conforming(
                    record=artifact_record,
                    output_binding=output_binding,
                )
            )
        except ArtifactStateIntegrityError as exc:
            raise PostWriteReleaseIntegrityError(str(exc)) from exc

        try:
            currentness = self.currentness_resolver.prove(
                historical_entry_ref=(
                    control.input_manifest.post_write_entry_ref
                ),
                active_projection=active,
                comparison_mode=(
                    ActiveComparisonMode.EXPECTED_BINDING_IDENTITY
                ),
            )
        except Exception as exc:
            raise PostWriteReleaseIntegrityError(
                "release-time currentness proof failed"
            ) from exc
        if currentness.currentness_state is not CurrentnessState.CURRENT:
            raise PostWriteReleaseEligibilityError(
                "release-time Document currentness is not CURRENT"
            )
        if (
            currentness.expected_active_entry_identity
            != control.fresh_currentness_proof.expected_active_entry_identity
        ):
            raise PostWriteReleaseEligibilityError(
                "ControlDecision is stale against active Document binding"
            )

        usability = self.artifact_usability_resolver.resolve(
            artifact_state_ref=exact.document_artifact_ref,
            currentness_proof=currentness,
        )
        if usability.state is not ArtifactUsabilityIntegrityState.TRUSTED_USABLE:
            raise PostWriteReleaseEligibilityError(
                "release-time Artifact is not TRUSTED_USABLE"
            )

        try:
            assessment = self.run_completion_assessment_resolver(
                completion_ref
            )
            assessment = RunCompletionAssessmentRecordSchema.model_validate(
                assessment
            )
            run_record = self.run_lifecycle_revision_resolver(
                exact.run_id,
                int(run_lifecycle_revision),
            )
            run_record = RunLifecycleRecordSchema.model_validate(
                run_record
            )
        except Exception as exc:
            raise PostWriteReleaseIntegrityError(
                "canonical Run completion/lifecycle proof cannot resolve"
            ) from exc

        if (
            assessment.as_ref() != completion_ref
            or assessment.run_id != exact.run_id
            or assessment.aggregate_result
            is not RunCompletionAggregateResult.SUCCESS
        ):
            raise PostWriteReleaseEligibilityError(
                "RunCompletionAssessment is not exact SUCCESS for release Run"
            )
        if (
            run_record.identity.run_id != exact.run_id
            or run_record.lifecycle_revision != int(run_lifecycle_revision)
            or run_record.status is not RunLifecycleStatus.SUCCESS
            or run_record.run_completion_assessment_ref != completion_ref
        ):
            raise PostWriteReleaseEligibilityError(
                "Run lifecycle is not exact terminal SUCCESS for assessment"
            )

        assessment_control = PostWriteControlDecisionRefSchema.model_validate(
            assessment.post_write_control_decision_ref.exact_ref_payload
        )
        if assessment_control != control.as_ref():
            raise PostWriteReleaseEligibilityError(
                "RunCompletionAssessment binds another final ControlDecision"
            )
        assessment_artifact = assessment.document_artifact_ref.exact_ref_payload
        if assessment_artifact != exact.document_artifact_ref.model_dump(
            mode="json"
        ):
            raise PostWriteReleaseEligibilityError(
                "RunCompletionAssessment binds another Document Artifact"
            )
        assessment_entry = assessment.document_post_write_entry_ref.exact_ref_payload
        if assessment_entry != control.input_manifest.post_write_entry_ref.model_dump(
            mode="json"
        ):
            raise PostWriteReleaseEligibilityError(
                "RunCompletionAssessment binds another Document Entry"
            )

        return ResolvedReleaseInput(
            request=exact,
            artifact_record=artifact_record,
            output_binding=output_binding,
            output_conformance_proof=conformance,
            control_decision=control,
            currentness_proof=currentness,
            artifact_usability_proof=usability,
            run_completion_assessment=assessment,
            run_lifecycle_record=run_record,
        )


class PostWriteReleasePublicationService:
    """Atomic immutable Target publisher with manifest-last commit semantics."""

    def __init__(
        self,
        *,
        publication_store: FileArtifactStore,
        input_guard: PostWriteReleaseInputIntegrityGuard,
        release_commit_fence: PostWriteReleaseCommitFence,
        publisher: str = "PostWriteReleasePublicationService",
        release_contract_version: str = "post_write_release_v1",
        clock: ReleaseClock | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.publication_store = publication_store
        self.input_guard = input_guard
        self.release_commit_fence = release_commit_fence
        self.publisher = str(publisher or "").strip()
        self.release_contract_version = str(
            release_contract_version or ""
        ).strip()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fault_injector = fault_injector
        if not self.publisher or not self.release_contract_version:
            raise ValueError("publisher/release_contract_version are required")

    @staticmethod
    def _publication_identity_payload(
        request: PostWriteReleaseRequestSchema,
    ) -> dict:
        return {
            "release_contract_version": request.release_contract_version,
            "document_artifact_ref": request.document_artifact_ref.model_dump(
                mode="json"
            ),
            "output_format": request.output_format,
            "validated_output_binding_sha256": (
                request.validated_output_binding_sha256
            ),
            "publication_target_id": request.publication_target_id,
        }

    @classmethod
    def publication_identity(
        cls,
        request: PostWriteReleaseRequestSchema,
    ) -> tuple[str, str]:
        exact = PostWriteReleaseRequestSchema.model_validate(request)
        key = canonical_sha256(cls._publication_identity_payload(exact))
        return "post-write-release://sha256/" + key, key

    @classmethod
    def build_request(
        cls,
        *,
        release_contract_version: str,
        task_id: str,
        run_id: str,
        document_logical_id: str,
        document_artifact_ref,
        post_write_control_decision_ref,
        output_format: str,
        validated_output_binding_sha256: str,
        publication_target_id: str,
    ) -> PostWriteReleaseRequestSchema:
        kwargs = {
            "release_contract_version": str(
                release_contract_version
            ).strip(),
            "task_id": str(task_id).strip(),
            "run_id": str(run_id).strip(),
            "document_logical_id": str(document_logical_id).strip(),
            "document_artifact_ref": document_artifact_ref,
            "post_write_control_decision_ref": (
                post_write_control_decision_ref
            ),
            "output_format": str(output_format).strip(),
            "validated_output_binding_sha256": (
                validated_output_binding_sha256
            ),
            "publication_target_id": str(
                publication_target_id
            ).strip(),
        }
        placeholder = PostWriteReleaseRequestSchema.model_construct(
            **kwargs,
            request_sha256="0" * 64,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("request_sha256", None)
        return PostWriteReleaseRequestSchema(
            **kwargs,
            request_sha256=canonical_sha256(payload),
        )

    def _lock_path(self, key: str) -> Path:
        return (
            self.publication_store.root
            / ".target-release-locks"
            / f"{key}.lock"
        )

    @staticmethod
    def _payload_ref(key: str, payload_sha256: str) -> str:
        return (
            f"target-release://{key}/payload/{payload_sha256}"
        )

    @staticmethod
    def _proof_ref(key: str, proof_sha256: str) -> str:
        return f"target-release://{key}/proof/{proof_sha256}"

    @staticmethod
    def _manifest_ref(key: str) -> str:
        return f"target-release://{key}/manifest"

    def _fault(self, stage: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(stage)

    @staticmethod
    def _release_mode(action: EnforcementAction) -> PostWriteReleaseMode:
        if action is EnforcementAction.OUTPUT:
            return PostWriteReleaseMode.STANDARD
        if action is EnforcementAction.OUTPUT_BEST_EFFORT:
            return PostWriteReleaseMode.BEST_EFFORT
        raise PostWriteReleaseEligibilityError(
            "Target Release rejects CORRECT/BLOCK"
        )

    def _restore_existing(
        self,
        *,
        request: PostWriteReleaseRequestSchema,
        key: str,
    ) -> PostWritePublishedReleaseSchema | None:
        manifest_ref = self._manifest_ref(key)
        try:
            manifest_artifact, raw_manifest = (
                self.publication_store.get_json_with_reference(
                    manifest_ref,
                    task_id=request.task_id,
                )
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise PostWriteReleaseIntegrityError(
                "committed Target release manifest cannot be read"
            ) from exc

        try:
            manifest = PostWriteReleaseManifestSchema.model_validate(
                raw_manifest
            )
        except Exception as exc:
            raise PostWriteReleaseIntegrityError(
                "committed Target release manifest is corrupt"
            ) from exc

        _, expected_key = self.publication_identity(request)
        if (
            manifest.task_id != request.task_id
            or manifest.run_id != request.run_id
            or manifest.document_logical_id != request.document_logical_id
            or manifest.release_contract_version != request.release_contract_version
            or manifest.idempotency_key != expected_key
            or manifest.document_artifact_ref
            != request.document_artifact_ref
            or manifest.output_format != request.output_format
            or manifest.validated_output_binding_sha256
            != request.validated_output_binding_sha256
            or manifest.publication_target_id
            != request.publication_target_id
        ):
            raise PostWriteReleaseConflictError(
                "publication identity collision with different scope/output identity"
            )

        try:
            payload_artifact, payload_bytes = self.publication_store.get_bytes_with_reference(
                manifest.release_payload_artifact.artifact_ref,
                task_id=manifest.release_payload_artifact.task_id,
                run_id=manifest.release_payload_artifact.run_id,
            )
            proof_artifact, raw_proof = self.publication_store.get_json_with_reference(
                manifest.release_input_proof_artifact.artifact_ref,
                task_id=manifest.release_input_proof_artifact.task_id,
                run_id=manifest.release_input_proof_artifact.run_id,
            )
            proof = PostWriteReleaseInputProofSchema.model_validate(raw_proof)
        except Exception as exc:
            raise PostWriteReleaseIntegrityError(
                "committed Target release payload/proof cannot be verified"
            ) from exc

        if (
            hashlib.sha256(payload_bytes).hexdigest()
            != manifest.release_payload_sha256
            or proof.proof_sha256 != manifest.release_input_proof_sha256
            or proof.release_request_sha256 != manifest.request_sha256
            or proof.document_artifact_ref
            != manifest.document_artifact_ref
            or proof.post_write_control_decision_ref
            != manifest.post_write_control_decision_ref
            or proof.run_completion_assessment_ref
            != manifest.run_completion_assessment_ref
            or proof.run_lifecycle_revision
            != manifest.run_lifecycle_revision
            or proof_artifact != manifest.release_input_proof_artifact
            or payload_artifact != manifest.release_payload_artifact
            or proof.release_contract_version != manifest.release_contract_version
            or proof.run_id != manifest.run_id
            or proof.release_payload_artifact_ref != manifest.release_payload_artifact
            or proof.release_payload_sha256 != manifest.release_payload_sha256
            or proof.assembly_input_manifest_ref != manifest.assembly_input_manifest_ref
            or proof.output_format != manifest.output_format
            or proof.renderer_contract_version != manifest.renderer_contract_version
            or proof.validated_output_binding_sha256 != manifest.validated_output_binding_sha256
            or proof.enforcement_action != manifest.enforcement_action_passthrough
            or proof.quality_state_passthrough != manifest.quality_state_passthrough
            or proof.blocking_fact_refs != manifest.blocking_fact_refs
            or proof.unresolved_reason_refs != manifest.unresolved_reason_refs
        ):
            raise PostWriteReleaseIntegrityError(
                "committed Target release manifest/proof/payload mismatch"
            )

        # Exact immutable Assembly authority anchors historical logical scope;
        # the incoming request (including GC's reconstruction) cannot prove it.
        try:
            assembly = (
                self.input_guard.currentness_resolver.document_binding_store
                .resolve_assembly_manifest(manifest.assembly_input_manifest_ref)
            )
        except Exception as exc:
            raise PostWriteReleaseIntegrityError(
                "committed Target release exact Assembly cannot be verified"
            ) from exc
        if (
            assembly.as_ref() != manifest.assembly_input_manifest_ref
            or assembly.document_logical_id != manifest.document_logical_id
        ):
            raise PostWriteReleaseIntegrityError(
                "committed Target release manifest/Assembly scope mismatch"
            )

        reuse = (
            PublicationReuseState.EXACT_REQUEST_REPLAY
            if manifest.request_sha256 == request.request_sha256
            else PublicationReuseState.ALREADY_PUBLISHED_SAME_OUTPUT
        )
        return PostWritePublishedReleaseSchema(
            manifest=manifest,
            manifest_artifact=manifest_artifact,
            reuse_state=reuse,
        )

    def _build_input_proof(
        self,
        *,
        resolved: ResolvedReleaseInput,
        request: PostWriteReleaseRequestSchema,
        release_payload_artifact,
        release_payload_sha256: str,
        verified_at: datetime,
    ) -> PostWriteReleaseInputProofSchema:
        control = resolved.control_decision
        manifest = control.input_manifest
        assert isinstance(
            manifest,
            DocumentPostWriteControlInputManifestSchema,
        )
        kwargs = {
            "release_contract_version": request.release_contract_version,
            "release_request_sha256": request.request_sha256,
            "run_id": request.run_id,
            "run_completion_assessment_ref": (
                resolved.run_completion_assessment.as_ref()
            ),
            "resolved_run_completion_assessment_sha256": (
                resolved.run_completion_assessment.assessment_sha256
            ),
            "run_lifecycle_revision": (
                resolved.run_lifecycle_record.lifecycle_revision
            ),
            "resolved_run_lifecycle_record_sha256": canonical_sha256(
                resolved.run_lifecycle_record.model_dump(mode="json")
            ),
            "run_lifecycle_status": RunLifecycleStatus.SUCCESS,
            "document_artifact_ref": request.document_artifact_ref,
            "post_write_control_decision_ref": control.as_ref(),
            "resolved_control_decision_hash": (
                control.control_decision_hash
            ),
            "enforcement_action": control.enforcement_action,
            "release_time_currentness_proof": (
                resolved.currentness_proof
            ),
            "release_time_artifact_usability_proof": (
                resolved.artifact_usability_proof
            ),
            "output_conformance_proof": (
                resolved.output_conformance_proof
            ),
            "release_payload_artifact_ref": release_payload_artifact,
            "release_payload_sha256": release_payload_sha256,
            "validated_output_binding_sha256": (
                resolved.output_binding.binding_sha256
            ),
            "output_format": resolved.output_binding.output_format,
            "renderer_contract_version": (
                resolved.output_binding.renderer_contract_version
            ),
            "quality_state_passthrough": (
                control.quality_state_passthrough
            ),
            "assembly_input_manifest_ref": (
                manifest.assembly_input_manifest_ref
            ),
            "blocking_fact_refs": control.blocking_fact_refs,
            "unresolved_reason_refs": control.unresolved_reason_refs,
            "verified_at": verified_at,
        }
        placeholder = PostWriteReleaseInputProofSchema.model_construct(
            **kwargs,
            proof_sha256="0" * 64,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("proof_sha256", None)
        return PostWriteReleaseInputProofSchema(
            **kwargs,
            proof_sha256=canonical_sha256(payload),
        )

    def _build_manifest(
        self,
        *,
        key: str,
        request: PostWriteReleaseRequestSchema,
        resolved: ResolvedReleaseInput,
        proof: PostWriteReleaseInputProofSchema,
        release_payload_artifact,
        proof_artifact,
        published_at: datetime,
    ) -> PostWriteReleaseManifestSchema:
        release_id = "post-write-release://sha256/" + key
        control = resolved.control_decision
        manifest_input = control.input_manifest
        assert isinstance(
            manifest_input,
            DocumentPostWriteControlInputManifestSchema,
        )
        kwargs = {
            "release_id": release_id,
            "idempotency_key": key,
            "release_contract_version": request.release_contract_version,
            "request_sha256": request.request_sha256,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "document_logical_id": request.document_logical_id,
            "document_artifact_ref": request.document_artifact_ref,
            "post_write_control_decision_ref": control.as_ref(),
            "run_completion_assessment_ref": (
                resolved.run_completion_assessment.as_ref()
            ),
            "run_lifecycle_revision": (
                resolved.run_lifecycle_record.lifecycle_revision
            ),
            "release_mode": self._release_mode(
                control.enforcement_action
            ),
            "enforcement_action_passthrough": (
                control.enforcement_action
            ),
            "quality_state_passthrough": (
                control.quality_state_passthrough
            ),
            "assembly_input_manifest_ref": (
                manifest_input.assembly_input_manifest_ref
            ),
            "blocking_fact_refs": control.blocking_fact_refs,
            "unresolved_reason_refs": control.unresolved_reason_refs,
            "output_format": resolved.output_binding.output_format,
            "renderer_contract_version": (
                resolved.output_binding.renderer_contract_version
            ),
            "validated_output_binding_sha256": (
                resolved.output_binding.binding_sha256
            ),
            "publication_target_id": request.publication_target_id,
            "release_payload_artifact": release_payload_artifact,
            "release_payload_sha256": (
                resolved.output_binding.output_payload_sha256
            ),
            "release_input_proof_artifact": proof_artifact,
            "release_input_proof_sha256": proof.proof_sha256,
            "publisher": self.publisher,
            "published_at": published_at,
            "immutable": True,
        }
        placeholder = PostWriteReleaseManifestSchema.model_construct(
            **kwargs,
            manifest_payload_sha256="0" * 64,
        )
        payload = placeholder.model_dump(mode="json")
        payload.pop("manifest_payload_sha256", None)
        return PostWriteReleaseManifestSchema(
            **kwargs,
            manifest_payload_sha256=canonical_sha256(payload),
        )

    def publish(
        self,
        *,
        request: PostWriteReleaseRequestSchema,
        run_completion_assessment_ref: RunCompletionAssessmentRefSchema,
        run_lifecycle_revision: int,
        active_document_binding_supplier: ActiveDocumentBindingSupplier,
    ) -> PostWritePublishedReleaseSchema:
        exact = PostWriteReleaseRequestSchema.model_validate(request)
        if exact.release_contract_version != self.release_contract_version:
            raise PostWriteReleaseIntegrityError(
                "release contract version mismatch"
            )
        if not callable(active_document_binding_supplier):
            raise TypeError(
                "active_document_binding_supplier must be callable"
            )
        _, key = self.publication_identity(exact)

        with InterProcessFileLock(self._lock_path(key)):
            existing = self._restore_existing(
                request=exact,
                key=key,
            )
            if existing is not None:
                return existing

            record, binding = self.input_guard.resolve_preflight_output(
                exact
            )
            try:
                source_bytes = (
                    self.input_guard.output_conformance_verifier.artifact_store.get_bytes(
                        binding.output_artifact_ref.artifact_ref,
                        task_id=binding.output_artifact_ref.task_id,
                        run_id=binding.output_artifact_ref.run_id,
                    )
                )
            except Exception as exc:
                raise PostWriteReleaseIntegrityError(
                    "validated output bytes cannot be read"
                ) from exc
            if (
                hashlib.sha256(source_bytes).hexdigest()
                != binding.output_payload_sha256
            ):
                raise PostWriteReleaseIntegrityError(
                    "preflight output payload hash mismatch"
                )

            release_payload_artifact = self.publication_store.put_bytes(
                source_bytes,
                artifact_type="post_write_release_payload",
                schema_version="post_write_release_payload_v1",
                created_by=self.publisher,
                task_id=exact.task_id,
                run_id=exact.run_id,
                input_snapshot_version=str(
                    exact.document_artifact_ref.artifact_version
                ),
                artifact_ref=self._payload_ref(
                    key,
                    binding.output_payload_sha256,
                ),
            )
            self._fault("after_payload_before_fence")

            with self.release_commit_fence.hold():
                active = active_document_binding_supplier()
                resolved = self.input_guard.validate_new_publication(
                    request=exact,
                    run_completion_assessment_ref=(
                        run_completion_assessment_ref
                    ),
                    run_lifecycle_revision=run_lifecycle_revision,
                    active_document_binding=active,
                )
                if resolved.output_binding != binding:
                    raise PostWriteReleaseIntegrityError(
                        "commit-time output binding differs from preflight"
                    )
                if (
                    release_payload_artifact.content_hash
                    != f"sha256:{resolved.output_binding.output_payload_sha256}"
                ):
                    raise PostWriteReleaseIntegrityError(
                        "release payload copy differs at commit time"
                    )

                verified_at = self.clock()
                if verified_at.tzinfo is None:
                    raise PostWriteReleaseIntegrityError(
                        "release Clock must be timezone-aware"
                    )
                proof = self._build_input_proof(
                    resolved=resolved,
                    request=exact,
                    release_payload_artifact=release_payload_artifact,
                    release_payload_sha256=(
                        resolved.output_binding.output_payload_sha256
                    ),
                    verified_at=verified_at,
                )
                proof_artifact = self.publication_store.put_json(
                    proof.model_dump(mode="json"),
                    artifact_type="post_write_release_input_proof",
                    schema_version=proof.schema_version,
                    created_by=self.publisher,
                    task_id=exact.task_id,
                    run_id=exact.run_id,
                    input_snapshot_version=str(
                        exact.document_artifact_ref.artifact_version
                    ),
                    artifact_ref=self._proof_ref(
                        key,
                        proof.proof_sha256,
                    ),
                )
                self._fault("after_proof_before_manifest")

                manifest = self._build_manifest(
                    key=key,
                    request=exact,
                    resolved=resolved,
                    proof=proof,
                    release_payload_artifact=release_payload_artifact,
                    proof_artifact=proof_artifact,
                    published_at=verified_at,
                )
                manifest_artifact = self.publication_store.put_json(
                    manifest.model_dump(mode="json"),
                    artifact_type="post_write_release_manifest",
                    schema_version=manifest.schema_version,
                    created_by=self.publisher,
                    task_id=exact.task_id,
                    run_id=exact.run_id,
                    input_snapshot_version=str(
                        exact.document_artifact_ref.artifact_version
                    ),
                    artifact_ref=self._manifest_ref(key),
                )
                self._fault("after_manifest_before_response")

                restored = self._restore_existing(
                    request=exact,
                    key=key,
                )
                if (
                    restored is None
                    or restored.manifest != manifest
                    or restored.manifest_artifact != manifest_artifact
                ):
                    raise PostWriteReleaseIntegrityError(
                        "manifest read-back verification failed"
                    )

            return PostWritePublishedReleaseSchema(
                manifest=manifest,
                manifest_artifact=manifest_artifact,
                reuse_state=PublicationReuseState.NEW_COMMIT,
            )


__all__ = [
    "ActiveDocumentBindingSupplier",
    "PostWriteReleaseConflictError",
    "PostWriteReleaseEligibilityError",
    "PostWriteReleaseError",
    "PostWriteReleaseInputIntegrityGuard",
    "PostWriteReleaseIntegrityError",
    "PostWriteReleasePublicationService",
    "ResolvedReleaseInput",
]
