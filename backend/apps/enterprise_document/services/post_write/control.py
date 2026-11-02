"""Deterministic Target Post-write Control service.

This module converts trusted Delivery Gate + currentness + usability + exact
CorrectionPolicy + canonical SIF-12 repair Budget projection into one immutable
PostWriteControlDecision. It never executes semantic Writer repair.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TypeVar

from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetStateSchema,
)
from apps.enterprise_document.schemas.post_write.control import (
    ArtifactCorrectionAuthorizationSchema,
    ArtifactUsabilityIntegrityProofSchema,
    ArtifactUsabilityIntegrityState,
    AvailabilityProjectionSchema,
    CorrectabilityState,
    CorrectionAuthorizationSource,
    CorrectionLineageProjectionSchema,
    CorrectionPolicySchema,
    CorrectionProjectionSchema,
    CorrectionProjectionState,
    DocumentCorrectionDispatchMode,
    DocumentCorrectionDispatchProjectionSchema,
    DocumentPostWriteControlInputManifestSchema,
    PostWriteControlDecisionSchema,
    PostWriteControlInputManifestSchema,
    SectionPostWriteControlInputManifestSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import (
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblyInputManifestRefSchema,
    AssemblyMechanicalRepairProjectionSchema,
    DocumentDefectAttributionSchema,
    SectionAssemblyCandidateSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    AvailabilityState,
    CorrectionPolicyRefSchema,
    DeliveryGateDecisionRefSchema,
    EnforcementAction,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.post_write.gate import (
    DeliveryGateDecisionSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationClass
from core.hashing import canonical_sha256

from .persistence import (
    ArtifactStateStore,
    ControlDecisionStore,
    GateDecisionStore,
    PostWritePersistenceIntegrityError,
)
from .policy import CorrectionPolicyResolver


class PostWriteControlError(ValueError):
    """Base deterministic Control failure."""


class PostWriteControlInputIntegrityError(PostWriteControlError):
    """Exact Control input bundle is internally inconsistent."""


T = TypeVar("T")


def _hash_bound_model(
    model_cls,
    hash_field: str,
    *,
    exclude_from_hash: tuple[str, ...] = (),
    **kwargs,
):
    """Construct one hash-bound Pydantic record without duplicating wire rules."""

    placeholder = model_cls.model_construct(
        **kwargs,
        **{hash_field: "0" * 64},
    )
    payload = placeholder.model_dump(mode="json")
    payload.pop(hash_field, None)
    for field_name in exclude_from_hash:
        payload.pop(field_name, None)
    return model_cls(
        **kwargs,
        **{hash_field: canonical_sha256(payload)},
    )


def _validate_currentness_hash(proof: CurrentnessProofSchema) -> None:
    payload = proof.model_dump(mode="json")
    payload.pop("proof_sha256", None)
    if canonical_sha256(payload) != proof.proof_sha256:
        raise PostWriteControlInputIntegrityError(
            "fresh CurrentnessProof canonical hash mismatch"
        )


@dataclass(frozen=True)
class ResolvedControlInput:
    gate_decision: DeliveryGateDecisionSchema
    correction_policy: CorrectionPolicySchema
    fresh_currentness_proof: CurrentnessProofSchema
    artifact_usability_integrity_proof: ArtifactUsabilityIntegrityProofSchema
    correction_lineage_projection: CorrectionLineageProjectionSchema


class CanonicalRepairBudgetProjectionAdapter:
    """Read-only SIF-12 projection. Never owns or mutates repair Budget."""

    @staticmethod
    def project(
        *,
        target_artifact_ref: ArtifactStateRefSchema,
        budget_state: CanonicalBudgetStateSchema,
        prior_correct_control_decision_refs: tuple[
            PostWriteControlDecisionRefSchema, ...
        ] = (),
        correction_attempt_projection_refs: tuple[str, ...] = (),
    ) -> CorrectionLineageProjectionSchema:
        state = CanonicalBudgetStateSchema.model_validate(budget_state)
        artifact_ref = ArtifactStateRefSchema.model_validate(target_artifact_ref)
        kwargs = {
            "target_artifact_ref": artifact_ref,
            "prior_correct_control_decision_refs": tuple(
                sorted(
                    prior_correct_control_decision_refs,
                    key=lambda item: item.control_decision_id,
                )
            ),
            "correction_attempt_projection_refs": tuple(
                sorted(set(correction_attempt_projection_refs))
            ),
            "repair_budget_policy_ref": state.policy_ref,
            "repair_budget_policy_version": state.policy_version,
            "repair_budget_revision": state.revision,
            "repair_attempts_limit": state.limits.repair_attempts,
            "repair_attempts_used": state.used.repair_attempts,
            "repair_attempts_active_reserved": state.active_reserved.repair_attempts,
            "repair_attempts_remaining": state.remaining.repair_attempts,
        }
        return _hash_bound_model(
            CorrectionLineageProjectionSchema,
            "projection_hash",
            **kwargs,
        )


class ArtifactUsabilityIntegrityResolver:
    """Prove exact persisted Artifact bytes/ref integrity for one control invocation."""

    def __init__(self, artifact_store: ArtifactStateStore) -> None:
        self.artifact_store = artifact_store

    def resolve(
        self,
        *,
        artifact_state_ref: ArtifactStateRefSchema,
        currentness_proof: CurrentnessProofSchema,
        connection=None,
    ) -> ArtifactUsabilityIntegrityProofSchema:
        artifact_ref = ArtifactStateRefSchema.model_validate(artifact_state_ref)
        currentness = CurrentnessProofSchema.model_validate(currentness_proof)
        _validate_currentness_hash(currentness)

        try:
            record = self.artifact_store.resolve_exact(artifact_ref, connection=connection)
            state = ArtifactUsabilityIntegrityState.TRUSTED_USABLE
            record_hash = record.record_hash
            reasons: tuple[str, ...] = ()
        except PostWritePersistenceIntegrityError:
            state = ArtifactUsabilityIntegrityState.INTEGRITY_INVALID
            record_hash = None
            reasons = ("ARTIFACT_EXACT_RESOLUTION_FAILED",)

        return _hash_bound_model(
            ArtifactUsabilityIntegrityProofSchema,
            "proof_sha256",
            artifact_state_ref=artifact_ref,
            currentness_proof_sha256=currentness.proof_sha256,
            artifact_record_hash=record_hash,
            state=state,
            reason_codes=reasons,
        )


class PostWriteControlInputIntegrityGuard:
    """Resolve every external authority and reject mixed-version bundles."""

    def __init__(
        self,
        *,
        gate_store: GateDecisionStore,
        correction_policy_resolver: CorrectionPolicyResolver,
    ) -> None:
        self.gate_store = gate_store
        self.correction_policy_resolver = correction_policy_resolver

    def validate(
        self,
        *,
        delivery_gate_decision_ref: DeliveryGateDecisionRefSchema,
        artifact_state_ref: ArtifactStateRefSchema,
        correction_policy_ref: CorrectionPolicyRefSchema,
        fresh_currentness_proof: CurrentnessProofSchema,
        artifact_usability_integrity_proof: ArtifactUsabilityIntegrityProofSchema,
        correction_lineage_projection: CorrectionLineageProjectionSchema,
    ) -> ResolvedControlInput:
        gate_ref = DeliveryGateDecisionRefSchema.model_validate(
            delivery_gate_decision_ref
        )
        artifact_ref = ArtifactStateRefSchema.model_validate(artifact_state_ref)
        policy_ref = CorrectionPolicyRefSchema.model_validate(correction_policy_ref)
        currentness = CurrentnessProofSchema.model_validate(fresh_currentness_proof)
        usability = ArtifactUsabilityIntegrityProofSchema.model_validate(
            artifact_usability_integrity_proof
        )
        lineage = CorrectionLineageProjectionSchema.model_validate(
            correction_lineage_projection
        )
        _validate_currentness_hash(currentness)

        try:
            gate = self.gate_store.resolve_exact(gate_ref)
            policy = self.correction_policy_resolver.resolve(policy_ref)
        except PostWritePersistenceIntegrityError as exc:
            raise PostWriteControlInputIntegrityError(str(exc)) from exc

        gate_manifest = gate.gate_input_manifest
        if gate_manifest.artifact_state_ref != artifact_ref:
            raise PostWriteControlInputIntegrityError(
                "Control Artifact differs from exact DeliveryGateDecision Artifact"
            )
        if currentness.historical_entry_ref != gate_manifest.post_write_entry_ref:
            raise PostWriteControlInputIntegrityError(
                "Control currentness proof targets another historical Entry"
            )
        if currentness.active_projection.artifact_state_ref != artifact_ref:
            raise PostWriteControlInputIntegrityError(
                "Control currentness active projection targets another Artifact"
            )
        if currentness.expected_active_entry_identity.artifact_state_ref != artifact_ref:
            raise PostWriteControlInputIntegrityError(
                "Control expected active Entry targets another Artifact"
            )
        if usability.artifact_state_ref != artifact_ref:
            raise PostWriteControlInputIntegrityError(
                "Artifact usability proof targets another Artifact"
            )
        if usability.currentness_proof_sha256 != currentness.proof_sha256:
            raise PostWriteControlInputIntegrityError(
                "Artifact usability proof binds another CurrentnessProof"
            )
        if lineage.target_artifact_ref != artifact_ref:
            raise PostWriteControlInputIntegrityError(
                "Correction lineage targets another Artifact"
            )
        if gate.validation_run_id != gate_manifest.validation_run_id:
            raise PostWriteControlInputIntegrityError(
                "DeliveryGateDecision Run/manifest contradiction"
            )
        return ResolvedControlInput(
            gate_decision=gate,
            correction_policy=policy,
            fresh_currentness_proof=currentness,
            artifact_usability_integrity_proof=usability,
            correction_lineage_projection=lineage,
        )


class ArtifactCorrectionAuthorizationBuilder:
    """Build embedded control evidence for SECTION_GATE semantic correction."""

    @staticmethod
    def _condition_partition(
        *,
        gate: DeliveryGateDecisionSchema,
        policy: CorrectionPolicySchema,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        correctable: list[str] = []
        non_correctable: list[str] = []
        reasons: list[str] = []
        for condition in gate.quality_policy_projection.blocking_conditions:
            state = policy.correctability_for(condition.condition_ref)
            if state is CorrectabilityState.CORRECTABLE_ARTIFACT_SIDE:
                correctable.append(condition.condition_ref)
                reasons.append(condition.reason_code)
            else:
                non_correctable.append(condition.condition_ref)
        return (
            tuple(sorted(set(correctable))),
            tuple(sorted(set(non_correctable))),
            tuple(sorted(set(reasons))),
        )

    def build_section_gate(
        self,
        *,
        gate: DeliveryGateDecisionSchema,
        policy: CorrectionPolicySchema,
        lineage: CorrectionLineageProjectionSchema,
        generation_class: WriterGenerationClass,
    ) -> ArtifactCorrectionAuthorizationSchema | None:
        if generation_class is not WriterGenerationClass.NORMAL_WRITE:
            return None
        correctable, _, reasons = self._condition_partition(
            gate=gate,
            policy=policy,
        )
        if not correctable or lineage.repair_attempts_remaining <= 0:
            return None
        gate_manifest = gate.gate_input_manifest
        artifact_ref = gate_manifest.artifact_state_ref
        gate_ref = gate.as_ref()
        policy_ref = policy.as_ref()

        identity_payload = {
            "source": CorrectionAuthorizationSource.SECTION_GATE.value,
            "target_artifact_ref": artifact_ref.model_dump(mode="json"),
            "target_post_write_entry_ref": gate_manifest.post_write_entry_ref.model_dump(
                mode="json"
            ),
            "source_gate_decision_ref": gate_ref.model_dump(mode="json"),
            "document_artifact_ref": None,
            "assembly_input_manifest_ref": None,
            "document_defect_attribution_id": None,
            "document_defect_attribution_hash": None,
            "authorized_blocking_condition_refs": list(correctable),
            "correction_policy_ref": policy_ref.model_dump(mode="json"),
            "repair_budget_revision": lineage.repair_budget_revision,
            "correction_context_hash": None,
        }
        kwargs = {
            "authorization_id": (
                "artifact-correction-auth://sha256/"
                + canonical_sha256(identity_payload)
            ),
            "source": CorrectionAuthorizationSource.SECTION_GATE,
            "target_artifact_ref": artifact_ref,
            "target_post_write_entry_ref": gate_manifest.post_write_entry_ref,
            "source_gate_decision_ref": gate_ref,
            "authorized_blocking_condition_refs": correctable,
            "correction_reason_refs": reasons,
            "correction_policy_ref": policy_ref,
            "target_generation_class": generation_class,
            "repair_budget_revision": lineage.repair_budget_revision,
            "repair_attempts_remaining": lineage.repair_attempts_remaining,
        }
        return _hash_bound_model(
            ArtifactCorrectionAuthorizationSchema,
            "authorization_hash",
            **kwargs,
        )

    def build_document_attribution(
        self,
        *,
        document_gate_decision_ref: DeliveryGateDecisionRefSchema,
        document_artifact_ref: ArtifactStateRefSchema,
        assembly_input_manifest_ref: AssemblyInputManifestRefSchema,
        attribution: DocumentDefectAttributionSchema,
        target_candidate: SectionAssemblyCandidateSchema,
        correction_policy_ref: CorrectionPolicyRefSchema,
        target_budget_revision: int,
        target_repair_attempts_remaining: int,
        correction_context=None,
    ) -> ArtifactCorrectionAuthorizationSchema | None:
        if target_candidate.generation_class is not WriterGenerationClass.NORMAL_WRITE:
            return None
        if target_repair_attempts_remaining <= 0:
            return None
        if (
            attribution.document_gate_decision_ref != document_gate_decision_ref
            or target_candidate.section_artifact_ref
            not in attribution.affected_section_artifact_refs
        ):
            raise PostWriteControlInputIntegrityError(
                "Document attribution does not own target Section"
            )
        identity_payload = {
            "source": CorrectionAuthorizationSource.DOCUMENT_ATTRIBUTION.value,
            "target_artifact_ref": target_candidate.section_artifact_ref.model_dump(
                mode="json"
            ),
            "target_post_write_entry_ref": (
                target_candidate.section_post_write_entry_ref.model_dump(mode="json")
            ),
            "source_gate_decision_ref": document_gate_decision_ref.model_dump(
                mode="json"
            ),
            "document_artifact_ref": document_artifact_ref.model_dump(mode="json"),
            "assembly_input_manifest_ref": assembly_input_manifest_ref.model_dump(
                mode="json"
            ),
            "document_defect_attribution_id": attribution.attribution_id,
            "document_defect_attribution_hash": attribution.attribution_hash,
            "authorized_blocking_condition_refs": [
                attribution.blocking_condition_ref
            ],
            "correction_policy_ref": correction_policy_ref.model_dump(mode="json"),
            "repair_budget_revision": int(target_budget_revision),
            "correction_context_hash": (
                correction_context.context_hash
                if correction_context is not None
                else None
            ),
        }
        kwargs = {
            "authorization_id": (
                "artifact-correction-auth://sha256/"
                + canonical_sha256(identity_payload)
            ),
            "source": CorrectionAuthorizationSource.DOCUMENT_ATTRIBUTION,
            "target_artifact_ref": target_candidate.section_artifact_ref,
            "target_post_write_entry_ref": (
                target_candidate.section_post_write_entry_ref
            ),
            "source_gate_decision_ref": document_gate_decision_ref,
            "document_artifact_ref": document_artifact_ref,
            "assembly_input_manifest_ref": assembly_input_manifest_ref,
            "document_defect_attribution_id": attribution.attribution_id,
            "document_defect_attribution_hash": attribution.attribution_hash,
            "authorized_blocking_condition_refs": (
                attribution.blocking_condition_ref,
            ),
            "correction_reason_refs": attribution.correction_reason_refs,
            "correction_policy_ref": correction_policy_ref,
            "target_generation_class": target_candidate.generation_class,
            "repair_budget_revision": int(target_budget_revision),
            "repair_attempts_remaining": int(target_repair_attempts_remaining),
            "correction_context": correction_context,
        }
        return _hash_bound_model(
            ArtifactCorrectionAuthorizationSchema,
            "authorization_hash",
            **kwargs,
        )


class DocumentCorrectionDispatchBuilder:
    @staticmethod
    def build(
        *,
        document_gate_decision_ref: DeliveryGateDecisionRefSchema,
        document_artifact_ref: ArtifactStateRefSchema,
        assembly_input_manifest_ref: AssemblyInputManifestRefSchema,
        correction_mode: DocumentCorrectionDispatchMode,
        defect_attributions: tuple[DocumentDefectAttributionSchema, ...] = (),
        correction_authorizations: tuple[
            ArtifactCorrectionAuthorizationSchema, ...
        ] = (),
        assembly_mechanical_repairs: tuple[
            AssemblyMechanicalRepairProjectionSchema, ...
        ] = (),
        dispatch_reason_codes: tuple[str, ...] = (),
        projection_contract_version: str = "document_correction_dispatch_v1",
    ) -> DocumentCorrectionDispatchProjectionSchema:
        kwargs = {
            "projection_contract_version": projection_contract_version,
            "document_gate_decision_ref": document_gate_decision_ref,
            "document_artifact_ref": document_artifact_ref,
            "assembly_input_manifest_ref": assembly_input_manifest_ref,
            "defect_attributions": defect_attributions,
            "correction_authorizations": correction_authorizations,
            "assembly_mechanical_repairs": assembly_mechanical_repairs,
            "correction_mode": correction_mode,
            "dispatch_reason_codes": tuple(sorted(set(dispatch_reason_codes))),
        }
        return _hash_bound_model(
            DocumentCorrectionDispatchProjectionSchema,
            "projection_hash",
            **kwargs,
        )


class PostWriteControlService:
    """Deterministic Control decision builder; no LLM and no Artifact mutation."""

    def __init__(
        self,
        *,
        integrity_guard: PostWriteControlInputIntegrityGuard,
        control_store: ControlDecisionStore,
        authorization_builder: ArtifactCorrectionAuthorizationBuilder | None = None,
        control_contract_version: str = "post_write_control_v1",
    ) -> None:
        self.integrity_guard = integrity_guard
        self.control_store = control_store
        self.authorization_builder = (
            authorization_builder or ArtifactCorrectionAuthorizationBuilder()
        )
        self.control_contract_version = str(control_contract_version).strip()
        if not self.control_contract_version:
            raise ValueError("control_contract_version is required")

    @staticmethod
    def _correction_projection(
        *,
        gate: DeliveryGateDecisionSchema,
        policy: CorrectionPolicySchema,
        currentness: CurrentnessProofSchema,
        usability: ArtifactUsabilityIntegrityProofSchema,
        lineage: CorrectionLineageProjectionSchema,
        generation_class: WriterGenerationClass | None,
        authorization_builder: ArtifactCorrectionAuthorizationBuilder,
        document_dispatch: DocumentCorrectionDispatchProjectionSchema | None = None,
    ) -> CorrectionProjectionSchema:
        correctable, non_correctable, _ = authorization_builder._condition_partition(
            gate=gate,
            policy=policy,
        )
        authorization = None
        reasons: tuple[str, ...]

        if gate.overall_quality_state is OverallQualityState.PASS:
            state = CorrectionProjectionState.NOT_NEEDED
            reasons = ("QUALITY_PASS",)
        elif currentness.currentness_state is not CurrentnessState.CURRENT:
            state = CorrectionProjectionState.STALE_INPUT
            reasons = ("CURRENTNESS_NOT_CURRENT",)
        elif usability.state is not ArtifactUsabilityIntegrityState.TRUSTED_USABLE:
            state = CorrectionProjectionState.NOT_ELIGIBLE
            reasons = ("ARTIFACT_NOT_TRUSTED_USABLE",)
        elif generation_class is WriterGenerationClass.FALLBACK_WRITE:
            state = CorrectionProjectionState.NOT_ELIGIBLE
            reasons = ("FALLBACK_WRITE_NO_AUTOMATIC_CORRECTION",)
        elif generation_class is None:
            if gate.overall_quality_state is OverallQualityState.UNRESOLVED:
                state = CorrectionProjectionState.NOT_ELIGIBLE
                reasons = ("QUALITY_UNRESOLVED",)
            elif not policy.automatic_correction_enabled:
                state = CorrectionProjectionState.NOT_ELIGIBLE
                reasons = ("AUTOMATIC_CORRECTION_DISABLED",)
            elif (
                document_dispatch is not None
                and document_dispatch.correction_mode
                is not DocumentCorrectionDispatchMode.NOT_ELIGIBLE
            ):
                state = CorrectionProjectionState.ELIGIBLE_AND_REQUESTED
                reasons = ("DOCUMENT_CORRECTION_AUTHORIZED",)
            else:
                state = CorrectionProjectionState.NOT_ELIGIBLE
                reasons = ("DOCUMENT_ATTRIBUTION_REQUIRED",)
        elif gate.overall_quality_state is OverallQualityState.UNRESOLVED:
            state = CorrectionProjectionState.NOT_ELIGIBLE
            reasons = ("QUALITY_UNRESOLVED",)
        elif not policy.automatic_correction_enabled:
            state = CorrectionProjectionState.NOT_ELIGIBLE
            reasons = ("AUTOMATIC_CORRECTION_DISABLED",)
        elif lineage.repair_attempts_remaining <= 0:
            state = CorrectionProjectionState.EXHAUSTED
            reasons = ("SIF12_REPAIR_BUDGET_EXHAUSTED",)
        elif not correctable:
            state = CorrectionProjectionState.NOT_CORRECTABLE
            reasons = ("NO_EXACT_CORRECTABLE_BLOCKER",)
        else:
            authorization = authorization_builder.build_section_gate(
                gate=gate,
                policy=policy,
                lineage=lineage,
                generation_class=generation_class,
            )
            if authorization is None:
                raise PostWriteControlInputIntegrityError(
                    "eligible correction could not materialize exact authorization"
                )
            state = CorrectionProjectionState.ELIGIBLE_AND_REQUESTED
            reasons = ("SECTION_GATE_CORRECTION_AUTHORIZED",)

        return _hash_bound_model(
            CorrectionProjectionSchema,
            "projection_hash",
            state=state,
            correctable_blocking_condition_refs=correctable,
            non_correctable_blocking_condition_refs=non_correctable,
            authorization=authorization,
            reason_codes=tuple(sorted(set(reasons))),
        )

    @staticmethod
    def _availability_and_action(
        *,
        quality: OverallQualityState,
        currentness: CurrentnessProofSchema,
        usability: ArtifactUsabilityIntegrityProofSchema,
        correction: CorrectionProjectionSchema,
    ) -> tuple[AvailabilityProjectionSchema, EnforcementAction]:
        if (
            currentness.currentness_state is not CurrentnessState.CURRENT
            or usability.state is not ArtifactUsabilityIntegrityState.TRUSTED_USABLE
        ):
            state = AvailabilityState.WITHHELD
            action = EnforcementAction.BLOCK
            reasons = ("INPUT_NOT_CURRENT_TRUSTED_USABLE",)
        elif (
            correction.state
            is CorrectionProjectionState.ELIGIBLE_AND_REQUESTED
        ):
            state = AvailabilityState.WITHHELD
            action = EnforcementAction.CORRECT
            reasons = ("CORRECTION_REQUIRED_BEFORE_OUTPUT",)
        elif quality is OverallQualityState.PASS:
            state = AvailabilityState.AVAILABLE
            action = EnforcementAction.OUTPUT
            reasons = ("QUALITY_PASS",)
        else:
            state = AvailabilityState.BEST_EFFORT
            action = EnforcementAction.OUTPUT_BEST_EFFORT
            reasons = ("TRUSTED_TERMINAL_BEST_EFFORT",)

        availability = _hash_bound_model(
            AvailabilityProjectionSchema,
            "projection_hash",
            state=state,
            reason_codes=tuple(sorted(set(reasons))),
        )
        return availability, action

    def decide(
        self,
        *,
        delivery_gate_decision_ref: DeliveryGateDecisionRefSchema,
        artifact_state_ref: ArtifactStateRefSchema,
        correction_policy_ref: CorrectionPolicyRefSchema,
        fresh_currentness_proof: CurrentnessProofSchema,
        artifact_usability_integrity_proof: ArtifactUsabilityIntegrityProofSchema,
        correction_lineage_projection: CorrectionLineageProjectionSchema,
        generation_class: WriterGenerationClass | None = None,
        assembly_input_manifest_ref: AssemblyInputManifestRefSchema | None = None,
        document_correction_dispatch_projection: (
            DocumentCorrectionDispatchProjectionSchema | None
        ) = None,
        created_at_epoch: float | None = None,
    ) -> PostWriteControlDecisionRefSchema:
        resolved = self.integrity_guard.validate(
            delivery_gate_decision_ref=delivery_gate_decision_ref,
            artifact_state_ref=artifact_state_ref,
            correction_policy_ref=correction_policy_ref,
            fresh_currentness_proof=fresh_currentness_proof,
            artifact_usability_integrity_proof=artifact_usability_integrity_proof,
            correction_lineage_projection=correction_lineage_projection,
        )
        gate = resolved.gate_decision
        artifact_ref = ArtifactStateRefSchema.model_validate(artifact_state_ref)

        if artifact_ref.artifact_scope is ArtifactScope.SECTION:
            if generation_class is None:
                raise PostWriteControlInputIntegrityError(
                    "SECTION Control requires exact generation_class"
                )
            manifest_kwargs = {
                "validation_run_id": gate.validation_run_id,
                "delivery_gate_decision_ref": gate.as_ref(),
                "post_write_entry_ref": gate.gate_input_manifest.post_write_entry_ref,
                "artifact_state_ref": artifact_ref,
                "fresh_currentness_proof_sha256": (
                    resolved.fresh_currentness_proof.proof_sha256
                ),
                "artifact_usability_integrity_proof_sha256": (
                    resolved.artifact_usability_integrity_proof.proof_sha256
                ),
                "correction_policy_ref": resolved.correction_policy.as_ref(),
                "correction_lineage_projection_hash": (
                    resolved.correction_lineage_projection.projection_hash
                ),
                "control_contract_version": self.control_contract_version,
                "generation_class": generation_class,
            }
            input_manifest: PostWriteControlInputManifestSchema = _hash_bound_model(
                SectionPostWriteControlInputManifestSchema,
                "input_manifest_hash",
                **manifest_kwargs,
            )
        else:
            if generation_class is not None:
                raise PostWriteControlInputIntegrityError(
                    "DOCUMENT Control forbids singular generation_class"
                )
            if assembly_input_manifest_ref is None:
                raise PostWriteControlInputIntegrityError(
                    "DOCUMENT Control requires assembly_input_manifest_ref"
                )
            exact_manifest_ref = AssemblyInputManifestRefSchema.model_validate(
                assembly_input_manifest_ref
            )
            expected_manifest_ref = (
                resolved.fresh_currentness_proof.expected_active_entry_identity
                .assembly_input_manifest_ref
            )
            if expected_manifest_ref != exact_manifest_ref:
                raise PostWriteControlInputIntegrityError(
                    "DOCUMENT Control manifest differs from exact current Entry identity"
                )
            if document_correction_dispatch_projection is not None:
                dispatch = DocumentCorrectionDispatchProjectionSchema.model_validate(
                    document_correction_dispatch_projection
                )
                if (
                    dispatch.document_gate_decision_ref != gate.as_ref()
                    or dispatch.document_artifact_ref != artifact_ref
                    or dispatch.assembly_input_manifest_ref != exact_manifest_ref
                ):
                    raise PostWriteControlInputIntegrityError(
                        "DOCUMENT correction dispatch outer binding mismatch"
                    )
                if (
                    dispatch.correction_mode
                    is not DocumentCorrectionDispatchMode.NOT_ELIGIBLE
                ):
                    gate_blockers = {
                        item.condition_ref
                        for item in gate.quality_policy_projection.blocking_conditions
                    }
                    for attribution in dispatch.defect_attributions:
                        if attribution.blocking_condition_ref not in gate_blockers:
                            raise PostWriteControlInputIntegrityError(
                                "Document attribution blocker is absent from exact Gate"
                            )
                        if (
                            resolved.correction_policy.correctability_for(
                                attribution.blocking_condition_ref
                            )
                            is not CorrectabilityState.CORRECTABLE_ARTIFACT_SIDE
                        ):
                            raise PostWriteControlInputIntegrityError(
                                "Document attribution blocker is not policy-correctable"
                            )
                    policy_ref = resolved.correction_policy.as_ref()
                    if any(
                        item.correction_policy_ref != policy_ref
                        for item in dispatch.correction_authorizations
                    ) or any(
                        item.correction_policy_ref != policy_ref
                        for item in dispatch.assembly_mechanical_repairs
                    ):
                        raise PostWriteControlInputIntegrityError(
                            "Document dispatch correction policy mismatch"
                        )
                document_correction_dispatch_projection = dispatch
            manifest_kwargs = {
                "validation_run_id": gate.validation_run_id,
                "delivery_gate_decision_ref": gate.as_ref(),
                "post_write_entry_ref": gate.gate_input_manifest.post_write_entry_ref,
                "artifact_state_ref": artifact_ref,
                "fresh_currentness_proof_sha256": (
                    resolved.fresh_currentness_proof.proof_sha256
                ),
                "artifact_usability_integrity_proof_sha256": (
                    resolved.artifact_usability_integrity_proof.proof_sha256
                ),
                "correction_policy_ref": resolved.correction_policy.as_ref(),
                "correction_lineage_projection_hash": (
                    resolved.correction_lineage_projection.projection_hash
                ),
                "control_contract_version": self.control_contract_version,
                "assembly_input_manifest_ref": exact_manifest_ref,
                "document_correction_dispatch_projection_hash": (
                    document_correction_dispatch_projection.projection_hash
                    if document_correction_dispatch_projection is not None
                    else None
                ),
            }
            input_manifest = _hash_bound_model(
                DocumentPostWriteControlInputManifestSchema,
                "input_manifest_hash",
                **manifest_kwargs,
            )

        correction = self._correction_projection(
            gate=gate,
            policy=resolved.correction_policy,
            currentness=resolved.fresh_currentness_proof,
            usability=resolved.artifact_usability_integrity_proof,
            lineage=resolved.correction_lineage_projection,
            generation_class=generation_class,
            authorization_builder=self.authorization_builder,
            document_dispatch=document_correction_dispatch_projection,
        )
        availability, action = self._availability_and_action(
            quality=gate.overall_quality_state,
            currentness=resolved.fresh_currentness_proof,
            usability=resolved.artifact_usability_integrity_proof,
            correction=correction,
        )
        decision_id = "post-write-control://sha256/" + canonical_sha256(
            {
                "control_contract_version": self.control_contract_version,
                "input_manifest_hash": input_manifest.input_manifest_hash,
            }
        )
        now = float(time.time() if created_at_epoch is None else created_at_epoch)
        kwargs = {
            "control_decision_id": decision_id,
            "control_contract_version": self.control_contract_version,
            "validation_run_id": gate.validation_run_id,
            "delivery_gate_decision_ref": gate.as_ref(),
            "input_manifest": input_manifest,
            "fresh_currentness_proof": resolved.fresh_currentness_proof,
            "artifact_usability_integrity_proof": (
                resolved.artifact_usability_integrity_proof
            ),
            "correction_lineage_projection": (
                resolved.correction_lineage_projection
            ),
            "correction_projection": correction,
            "document_correction_dispatch_projection": (
                document_correction_dispatch_projection
            ),
            "availability_projection": availability,
            "quality_state_passthrough": gate.overall_quality_state,
            "blocking_fact_refs": gate.blocking_fact_refs,
            "unresolved_reason_refs": gate.unresolved_reason_refs,
            "control_reason_codes": tuple(
                sorted(
                    set(
                        gate.decision_reason_codes
                        + correction.reason_codes
                        + availability.reason_codes
                    )
                )
            ),
            "enforcement_action": action,
            "created_at_epoch": now,
        }
        decision = _hash_bound_model(
            PostWriteControlDecisionSchema,
            "control_decision_hash",
            exclude_from_hash=("created_at_epoch",),
            **kwargs,
        )
        return self.control_store.put_if_absent_or_same(decision)


class EnforcementExecutor:
    """Bounded runtime projection: execute nothing semantic, expose exact action."""

    @staticmethod
    def resolve_action(
        decision: PostWriteControlDecisionSchema,
    ) -> EnforcementAction:
        trusted = PostWriteControlDecisionSchema.model_validate(decision)
        return trusted.enforcement_action


__all__ = [
    "ArtifactCorrectionAuthorizationBuilder",
    "ArtifactUsabilityIntegrityResolver",
    "CanonicalRepairBudgetProjectionAdapter",
    "DocumentCorrectionDispatchBuilder",
    "EnforcementExecutor",
    "PostWriteControlError",
    "PostWriteControlInputIntegrityError",
    "PostWriteControlInputIntegrityGuard",
    "PostWriteControlService",
    "ResolvedControlInput",
]
