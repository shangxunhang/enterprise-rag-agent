"""Dependency-neutral Post-write schema foundation.

This module contains immutable exact-reference envelopes plus the Step-5.2
structural Artifact state record. It does not implement Post-write policy, Gate,
Control, Validation, Artifact currentness, Claim/Support semantics, repair
authorization, completion, or release authority.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.artifact_reference import ArtifactReferenceSchema
from apps.enterprise_document.schemas.contract_base import ContractSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterGenerationClass,
    WriterGenerationInvocationRefSchema,
)
from core.hashing import canonical_sha256


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


class _FrozenPostWriteRef(ContractSchema):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=True,
        frozen=True,
    )


class EvidenceSnapshotRefSchema(_FrozenPostWriteRef):
    """Exact immutable ref to the Writer-visible canonical Evidence snapshot."""

    evidence_bundle_schema_version: NonEmptyStr
    evidence_bundle_sha256: Sha256Hex
    writer_invocation_ref: WriterGenerationInvocationRefSchema



class ArtifactScope(str, Enum):
    SECTION = "SECTION"
    DOCUMENT = "DOCUMENT"


class ArtifactStateRefSchema(_FrozenPostWriteRef):
    schema_version: Literal["artifact_state_ref_v1"] = "artifact_state_ref_v1"
    artifact_id: NonEmptyStr
    artifact_version: int
    artifact_hash: Sha256Hex
    artifact_scope: ArtifactScope

    def model_post_init(self, __context: Any) -> None:
        if self.artifact_version < 1:
            raise ValueError("artifact_version must be >= 1")


class ArtifactOriginKind(str, Enum):
    INITIAL_WRITER = "INITIAL_WRITER"
    CORRECTION = "CORRECTION"
    DOCUMENT_ASSEMBLY = "DOCUMENT_ASSEMBLY"


class InitialWriterArtifactOriginSchema(_FrozenPostWriteRef):
    origin_kind: Literal[ArtifactOriginKind.INITIAL_WRITER] = ArtifactOriginKind.INITIAL_WRITER
    writer_execution_ref: WriterExecutionRefSchema
    writer_invocation_ref: WriterGenerationInvocationRefSchema


class CorrectionArtifactOriginSchema(_FrozenPostWriteRef):
    origin_kind: Literal[ArtifactOriginKind.CORRECTION] = ArtifactOriginKind.CORRECTION
    correction_attempt_ref: NonEmptyStr


class DocumentAssemblyArtifactOriginSchema(_FrozenPostWriteRef):
    origin_kind: Literal[ArtifactOriginKind.DOCUMENT_ASSEMBLY] = (
        ArtifactOriginKind.DOCUMENT_ASSEMBLY
    )
    assembly_input_manifest_ref: NonEmptyStr


ArtifactOriginSchema = (
    InitialWriterArtifactOriginSchema
    | CorrectionArtifactOriginSchema
    | DocumentAssemblyArtifactOriginSchema
)


class ArtifactVisibleProjectionRefSchema(_FrozenPostWriteRef):
    schema_version: Literal["artifact_visible_projection_ref_v1"] = (
        "artifact_visible_projection_ref_v1"
    )
    projection_id: NonEmptyStr
    projection_schema_version: NonEmptyStr
    projection_contract_version: NonEmptyStr
    projection_sha256: Sha256Hex


class ArtifactVisibleProjectionSchema(_FrozenPostWriteRef):
    schema_version: Literal["artifact_visible_projection_v1"] = (
        "artifact_visible_projection_v1"
    )
    projection_contract_version: NonEmptyStr
    projection_id: NonEmptyStr
    artifact_scope: ArtifactScope
    logical_target_ref: NonEmptyStr
    visible_payload: dict[str, Any]
    projection_sha256: Sha256Hex

    @model_validator(mode="after")
    def _projection_integrity(self) -> "ArtifactVisibleProjectionSchema":
        payload = self.model_dump(mode="json")
        payload.pop("projection_id", None)
        payload.pop("projection_sha256", None)
        expected_hash = canonical_sha256(payload)
        if self.projection_sha256 != expected_hash:
            raise ValueError("ArtifactVisibleProjection hash mismatch")
        expected_id = "artifact-visible://sha256/" + expected_hash
        if self.projection_id != expected_id:
            raise ValueError("ArtifactVisibleProjection id mismatch")
        return self

    def as_ref(self) -> ArtifactVisibleProjectionRefSchema:
        return ArtifactVisibleProjectionRefSchema(
            projection_id=self.projection_id,
            projection_schema_version=self.schema_version,
            projection_contract_version=self.projection_contract_version,
            projection_sha256=self.projection_sha256,
        )


class ArtifactValidatedOutputBindingSchema(_FrozenPostWriteRef):
    schema_version: Literal["artifact_validated_output_binding_v1"] = (
        "artifact_validated_output_binding_v1"
    )
    output_format: NonEmptyStr
    renderer_contract_version: NonEmptyStr
    output_artifact_ref: ArtifactReferenceSchema
    output_payload_sha256: Sha256Hex
    binding_sha256: Sha256Hex

    @model_validator(mode="after")
    def _binding_integrity(self) -> "ArtifactValidatedOutputBindingSchema":
        if self.output_artifact_ref.content_hash != f"sha256:{self.output_payload_sha256}":
            raise ValueError("validated output Artifact content hash mismatch")
        expected = canonical_sha256(
            {
                "schema_version": self.schema_version,
                "output_format": self.output_format,
                "renderer_contract_version": self.renderer_contract_version,
                "output_payload_sha256": self.output_payload_sha256,
            }
        )
        if self.binding_sha256 != expected:
            raise ValueError("ArtifactValidatedOutputBinding hash mismatch")
        return self


class OutputConformanceState(str, Enum):
    CONFORMING = "CONFORMING"
    NONCONFORMING = "NONCONFORMING"


class ArtifactOutputConformanceProofSchema(_FrozenPostWriteRef):
    schema_version: Literal["artifact_output_conformance_proof_v1"] = (
        "artifact_output_conformance_proof_v1"
    )
    conformance_contract_version: NonEmptyStr
    adapter_contract_version: NonEmptyStr
    artifact_id: NonEmptyStr
    artifact_hash: Sha256Hex
    visible_projection_ref: ArtifactVisibleProjectionRefSchema
    visible_projection_sha256: Sha256Hex
    validated_output_binding_sha256: Sha256Hex
    output_format: NonEmptyStr
    renderer_contract_version: NonEmptyStr
    output_payload_sha256: Sha256Hex
    extracted_visible_projection_sha256: Sha256Hex
    state: OutputConformanceState
    reason_codes: tuple[NonEmptyStr, ...] = ()
    proof_sha256: Sha256Hex

    @model_validator(mode="after")
    def _proof_integrity(self) -> "ArtifactOutputConformanceProofSchema":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("output conformance reason_codes must be ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("proof_sha256", None)
        if canonical_sha256(payload) != self.proof_sha256:
            raise ValueError("ArtifactOutputConformanceProof hash mismatch")
        return self


class ArtifactStateRecordSchema(_FrozenPostWriteRef):
    schema_version: Literal["artifact_state_record_v1"] = "artifact_state_record_v1"
    artifact_ref: ArtifactStateRefSchema
    parent_artifact_ref: ArtifactStateRefSchema | None = None
    canonical_payload_ref: dict[str, Any]
    rendered_output_ref: dict[str, Any] | None = None
    visible_projection_ref: ArtifactVisibleProjectionRefSchema | None = None
    visible_projection: ArtifactVisibleProjectionSchema | None = None
    validated_output_bindings: tuple[ArtifactValidatedOutputBindingSchema, ...] = ()
    output_conformance_proofs: tuple[ArtifactOutputConformanceProofSchema, ...] = ()
    origin: ArtifactOriginSchema
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _release_binding_integrity(self) -> "ArtifactStateRecordSchema":
        bindings = tuple(self.validated_output_bindings)
        proofs = tuple(self.output_conformance_proofs)
        binding_keys = [(item.output_format, item.binding_sha256) for item in bindings]
        if binding_keys != sorted(binding_keys) or len(binding_keys) != len(set(binding_keys)):
            raise ValueError("validated output bindings must be canonical ordered-unique")
        if (bindings or proofs) and (
            self.visible_projection_ref is None or self.visible_projection is None
        ):
            raise ValueError(
                "release-bound Artifact requires exact visible projection value/ref"
            )
        if self.visible_projection is not None:
            if self.visible_projection_ref != self.visible_projection.as_ref():
                raise ValueError("Artifact visible projection value/ref mismatch")
            if self.visible_projection.artifact_scope is not self.artifact_ref.artifact_scope:
                raise ValueError("Artifact visible projection scope mismatch")
        elif self.visible_projection_ref is not None:
            raise ValueError("Artifact visible projection ref is dangling")
        proof_keys = [
            (item.output_format, item.validated_output_binding_sha256)
            for item in proofs
        ]
        if proof_keys != sorted(proof_keys) or len(proof_keys) != len(set(proof_keys)):
            raise ValueError("output conformance proofs must be canonical ordered-unique")
        binding_by_hash = {item.binding_sha256: item for item in bindings}
        for proof in proofs:
            binding = binding_by_hash.get(proof.validated_output_binding_sha256)
            if binding is None:
                raise ValueError("conformance proof references unknown output binding")
            if (
                proof.artifact_id != self.artifact_ref.artifact_id
                or proof.artifact_hash != self.artifact_ref.artifact_hash
                or proof.visible_projection_ref != self.visible_projection_ref
                or proof.visible_projection_sha256
                != self.visible_projection_ref.projection_sha256
                or proof.output_format != binding.output_format
                or proof.renderer_contract_version != binding.renderer_contract_version
                or proof.output_payload_sha256 != binding.output_payload_sha256
            ):
                raise ValueError("output conformance proof/Artifact binding mismatch")
        if bindings and len(proofs) != len(bindings):
            raise ValueError("every validated output binding requires exact conformance proof")

        if self.visible_projection_ref is not None:
            if (
                self.canonical_payload_ref.get("visible_projection_sha256")
                != self.visible_projection_ref.projection_sha256
            ):
                raise ValueError(
                    "visible projection hash is outside Artifact canonical hash boundary"
                )
        if bindings:
            expected_semantics = [
                {
                    "output_format": item.output_format,
                    "renderer_contract_version": item.renderer_contract_version,
                    "output_payload_sha256": item.output_payload_sha256,
                }
                for item in bindings
            ]
            if self.canonical_payload_ref.get("validated_output_semantics") != expected_semantics:
                raise ValueError(
                    "release-visible output semantics are outside Artifact canonical hash boundary"
                )
        return self


class PostWriteUpstreamBindingSchema(_FrozenPostWriteRef):
    schema_version: Literal["post_write_upstream_binding_v1"] = (
        "post_write_upstream_binding_v1"
    )
    writer_execution_ref: WriterExecutionRefSchema
    writer_invocation_ref: WriterGenerationInvocationRefSchema
    route_decision_ref: PreWriteRouteDecisionRefSchema
    evidence_snapshot_ref: EvidenceSnapshotRefSchema
    generation_class: WriterGenerationClass


class PostWriteEntryRefSchema(_FrozenPostWriteRef):
    """Exact immutable reference envelope for one persisted Post-write Entry."""

    entry_id: NonEmptyStr
    entry_schema_version: NonEmptyStr
    entry_contract_version: NonEmptyStr
    entry_sha256: Sha256Hex


class QualityDeliveryContractRefSchema(_FrozenPostWriteRef):
    """Exact immutable ref to one historical Quality/Delivery Contract."""

    contract_id: NonEmptyStr
    contract_schema_version: NonEmptyStr
    contract_version: NonEmptyStr
    contract_hash: Sha256Hex


class DeliveryGateDecisionRefSchema(_FrozenPostWriteRef):
    """Exact immutable ref envelope only; no Delivery Gate semantics live here."""

    delivery_gate_decision_id: NonEmptyStr
    schema_version: NonEmptyStr
    gate_contract_version: NonEmptyStr
    decision_hash: Sha256Hex


class CorrectionPolicyRefSchema(_FrozenPostWriteRef):
    """Exact immutable ref envelope only; no correction policy evaluation lives here."""

    policy_id: NonEmptyStr
    policy_schema_version: NonEmptyStr
    policy_version: NonEmptyStr
    policy_hash: Sha256Hex


class PostWriteControlDecisionRefSchema(_FrozenPostWriteRef):
    """Exact immutable ref envelope only; no Post-write Control semantics live here."""

    control_decision_id: NonEmptyStr
    schema_version: NonEmptyStr
    control_contract_version: NonEmptyStr
    control_decision_hash: Sha256Hex


class OverallQualityState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"


class AvailabilityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    BEST_EFFORT = "BEST_EFFORT"
    WITHHELD = "WITHHELD"


class EnforcementAction(str, Enum):
    OUTPUT = "OUTPUT"
    OUTPUT_BEST_EFFORT = "OUTPUT_BEST_EFFORT"
    CORRECT = "CORRECT"
    BLOCK = "BLOCK"


class ValidationScope(str, Enum):
    SECTION = "SECTION"
    DOCUMENT = "DOCUMENT"


class ValidationTargetSelectorSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_target_selector_v1"] = (
        "validation_target_selector_v1"
    )
    selector_kind: NonEmptyStr
    target_ref: NonEmptyStr
    selector_contract_version: NonEmptyStr


class ValidationSubjectKeySchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_subject_key_v1"] = (
        "validation_subject_key_v1"
    )
    post_write_entry_ref: PostWriteEntryRefSchema
    validation_scope: ValidationScope
    target_selector: ValidationTargetSelectorSchema
    subject_contract_version: NonEmptyStr
    subject_hash: Sha256Hex


class ValidationRunState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class ValidationRunSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_run_v1"] = "validation_run_v1"
    contract_version: Literal["validation_run_lifecycle_v1"] = (
        "validation_run_lifecycle_v1"
    )
    validation_run_id: NonEmptyStr
    subject_key: ValidationSubjectKeySchema
    execution_state: ValidationRunState
    state_version: int = Field(ge=1)
    creation_command_id: NonEmptyStr
    created_at_epoch: float
    started_at_epoch: float | None = None
    finished_at_epoch: float | None = None
    run_failure_kind: NonEmptyStr | None = None
    run_error_ref: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _lifecycle_shape(self) -> "ValidationRunSchema":
        if self.execution_state is ValidationRunState.CREATED:
            if self.state_version != 1 or self.started_at_epoch is not None or self.finished_at_epoch is not None:
                raise ValueError("CREATED ValidationRun must be state_version=1 and unstarted")
        elif self.execution_state is ValidationRunState.RUNNING:
            if self.started_at_epoch is None or self.finished_at_epoch is not None:
                raise ValueError("RUNNING ValidationRun requires started_at only")
        elif self.execution_state is ValidationRunState.COMPLETED:
            if self.started_at_epoch is None or self.finished_at_epoch is None:
                raise ValueError("COMPLETED ValidationRun requires lifecycle timestamps")
            if self.run_failure_kind is not None or self.run_error_ref is not None:
                raise ValueError("COMPLETED ValidationRun cannot carry run technical failure")
        else:
            if self.finished_at_epoch is None or self.run_failure_kind is None or self.run_error_ref is None:
                raise ValueError("TECHNICAL_FAILURE requires finished timestamp and typed failure")
        return self


class ValidationFactFamily(str, Enum):
    CLAIM_EVIDENCE_SUPPORT = "CLAIM_EVIDENCE_SUPPORT"
    FAITHFULNESS = "FAITHFULNESS"
    REQUIREMENT_ADHERENCE = "REQUIREMENT_ADHERENCE"
    CITATION_INTEGRITY = "CITATION_INTEGRITY"
    CITATION_GROUNDING = "CITATION_GROUNDING"
    COVERAGE_COMPLETENESS = "COVERAGE_COMPLETENESS"
    ARTIFACT_CONSISTENCY = "ARTIFACT_CONSISTENCY"


class ValidationSemanticState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"


class ValidationFactSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_fact_v1"] = "validation_fact_v1"
    contract_version: NonEmptyStr
    fact_id: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    producer_validator_id: NonEmptyStr
    producer_validator_version: NonEmptyStr
    family: ValidationFactFamily
    target_ref: NonEmptyStr
    semantic_state: ValidationSemanticState
    payload: dict[str, Any]
    fact_sha256: Sha256Hex


class ValidationFactRefSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_fact_ref_v1"] = "validation_fact_ref_v1"
    fact_id: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    fact_schema_version: Literal["validation_fact_v1"] = "validation_fact_v1"
    fact_contract_version: NonEmptyStr
    fact_sha256: Sha256Hex


class ValidationCompletionItemState(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_RUN = "NOT_RUN"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    INDETERMINATE = "INDETERMINATE"
    PASS = "PASS"
    FAIL = "FAIL"


class ValidationCompletionItemSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_completion_item_v1"] = (
        "validation_completion_item_v1"
    )
    expected_validation_item_ref: NonEmptyStr
    required: bool
    state: ValidationCompletionItemState
    fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    # Procedural positive proof is an existing immutable ValidationIntermediate.
    # exclude_if preserves the exact historical v1 JSON/hash shape when unused.
    positive_proof_refs: tuple["ValidationIntermediateRefSchema", ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    indeterminate_proof_refs: tuple["ValidationIntermediateRefSchema", ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    technical_failure_ref: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _item_shape(self) -> "ValidationCompletionItemSchema":
        if self.state is ValidationCompletionItemState.FAIL and not self.fact_refs:
            raise ValueError("semantic FAIL requires canonical Fact refs")
        if self.state is ValidationCompletionItemState.INDETERMINATE and not (self.fact_refs or self.indeterminate_proof_refs):
            raise ValueError("INDETERMINATE requires canonical Fact refs or indeterminate proof refs")
        if (
            self.state is ValidationCompletionItemState.PASS
            and not self.fact_refs
            and not self.positive_proof_refs
        ):
            raise ValueError("PASS requires canonical Fact refs or positive proof refs")
        if self.positive_proof_refs and self.state is not ValidationCompletionItemState.PASS:
            raise ValueError("positive_proof_refs only belong to PASS")
        if self.indeterminate_proof_refs and self.state is not ValidationCompletionItemState.INDETERMINATE:
            raise ValueError("indeterminate_proof_refs only belong to INDETERMINATE")
        proof_ids = [
            (item.validation_run_id, item.event_id)
            for item in (*self.positive_proof_refs, *self.indeterminate_proof_refs)
        ]
        if len(proof_ids) != len(set(proof_ids)):
            raise ValueError("positive_proof_refs must be canonical unique")
        if self.state is ValidationCompletionItemState.TECHNICAL_FAILURE:
            if self.technical_failure_ref is None:
                raise ValueError("TECHNICAL_FAILURE requires typed failure ref")
        elif self.technical_failure_ref is not None:
            raise ValueError("technical_failure_ref only belongs to TECHNICAL_FAILURE")
        return self


class ValidationCompletionSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_completion_v1"] = "validation_completion_v1"
    contract_version: Literal["validation_completion_accounting_v1"] = (
        "validation_completion_accounting_v1"
    )
    completion_id: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    items: tuple[ValidationCompletionItemSchema, ...]
    completion_sha256: Sha256Hex

    @model_validator(mode="after")
    def _unique_expected_items(self) -> "ValidationCompletionSchema":
        refs = [item.expected_validation_item_ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("Completion expected item refs must be unique")
        if any(
            proof.validation_run_id != self.validation_run_id
            for item in self.items
            for proof in (*item.positive_proof_refs, *item.indeterminate_proof_refs)
        ):
            raise ValueError("Completion proof belongs to another ValidationRun")
        return self


class ValidationCompletionRefSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_completion_ref_v1"] = (
        "validation_completion_ref_v1"
    )
    completion_id: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    completion_schema_version: Literal["validation_completion_v1"] = (
        "validation_completion_v1"
    )
    completion_contract_version: Literal["validation_completion_accounting_v1"] = (
        "validation_completion_accounting_v1"
    )
    completion_sha256: Sha256Hex


class ValidationExecutionEventSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_execution_event_v1"] = (
        "validation_execution_event_v1"
    )
    validation_run_id: NonEmptyStr
    event_id: NonEmptyStr
    run_event_sequence: int = Field(ge=1)
    event_kind: NonEmptyStr
    payload: dict[str, Any]
    payload_hash: Sha256Hex
    event_hash: Sha256Hex
    occurred_at_epoch: float


class ValidatorTerminalExecutionState(str, Enum):
    COMPLETED = "COMPLETED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class ExpectedItemExecutionState(str, Enum):
    COMPLETED = "COMPLETED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class ExpectedItemExecutionAccountingSchema(_FrozenPostWriteRef):
    expected_validation_item_ref: NonEmptyStr
    execution_state: ExpectedItemExecutionState
    fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    positive_proof_refs: tuple["ValidationIntermediateRefSchema", ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    indeterminate_proof_refs: tuple["ValidationIntermediateRefSchema", ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    technical_failure_ref: dict[str, Any] | None = None
    provenance_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _execution_shape(self) -> "ExpectedItemExecutionAccountingSchema":
        if self.positive_proof_refs and self.indeterminate_proof_refs:
            raise ValueError("execution proof kinds cannot be mixed")
        proofs = (*self.positive_proof_refs, *self.indeterminate_proof_refs)
        proof_ids = [(ref.validation_run_id, ref.event_id) for ref in proofs]
        if len(proof_ids) != len(set(proof_ids)):
            raise ValueError("execution positive proof refs must be unique")
        if self.positive_proof_refs and self.execution_state is not ExpectedItemExecutionState.COMPLETED:
            raise ValueError("positive proof requires COMPLETED execution accounting")
        if self.indeterminate_proof_refs and self.execution_state is not ExpectedItemExecutionState.COMPLETED:
            raise ValueError("indeterminate proof requires COMPLETED execution accounting")
        if self.execution_state is ExpectedItemExecutionState.TECHNICAL_FAILURE:
            if self.technical_failure_ref is None:
                raise ValueError("TECHNICAL_FAILURE accounting requires typed failure ref")
        elif self.technical_failure_ref is not None:
            raise ValueError(
                "technical_failure_ref only belongs to TECHNICAL_FAILURE accounting"
            )
        return self


class ValidationIntermediateKind(str, Enum):
    GENERIC = "GENERIC"
    MODEL_DERIVATION = "MODEL_DERIVATION"
    TARGET_ACCOUNTING = "TARGET_ACCOUNTING"


class ValidationIntermediateRefSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_intermediate_ref_v1"] = (
        "validation_intermediate_ref_v1"
    )
    validation_run_id: NonEmptyStr
    event_id: NonEmptyStr
    intermediate_kind: ValidationIntermediateKind
    intermediate_sha256: Sha256Hex


# Resolve the Completion forward reference only after the existing generic
# intermediate ref is defined. Empty proof refs remain serialization-compatible.
ValidationCompletionItemSchema.model_rebuild()
ValidationCompletionSchema.model_rebuild()
ExpectedItemExecutionAccountingSchema.model_rebuild()


class ValidatorExecutionRecordSchema(_FrozenPostWriteRef):
    schema_version: Literal["validator_execution_record_v1"] = (
        "validator_execution_record_v1"
    )
    validator_execution_ref: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    validator_id: NonEmptyStr
    validator_version: NonEmptyStr
    stage_plan_ref: NonEmptyStr
    stage_plan_version: NonEmptyStr
    input_hash: Sha256Hex
    terminal_event_id: NonEmptyStr
    terminal_execution_state: ValidatorTerminalExecutionState
    expected_item_accounting: tuple[ExpectedItemExecutionAccountingSchema, ...]
    produced_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    intermediate_refs: tuple[ValidationIntermediateRefSchema, ...] = ()
    record_sha256: Sha256Hex

    @model_validator(mode="after")
    def _unique_item_accounting(self) -> "ValidatorExecutionRecordSchema":
        refs = [
            item.expected_validation_item_ref
            for item in self.expected_item_accounting
        ]
        if len(refs) != len(set(refs)):
            raise ValueError(
                "ValidatorExecution expected-item accounting must be unique"
            )
        fact_ids = [item.fact_id for item in self.produced_fact_refs]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("ValidatorExecution produced_fact_refs must be unique")
        intermediate_ids = [
            (item.validation_run_id, item.event_id)
            for item in self.intermediate_refs
        ]
        if len(intermediate_ids) != len(set(intermediate_ids)):
            raise ValueError(
                "ValidatorExecution intermediate_refs must be unique"
            )
        if any(
            item.validation_run_id != self.validation_run_id
            for item in self.intermediate_refs
        ):
            raise ValueError(
                "ValidatorExecution intermediate_refs must belong to same ValidationRun"
            )
        for item in self.expected_item_accounting:
            if not set(item.positive_proof_refs) <= set(self.intermediate_refs):
                raise ValueError("execution positive proofs must be bound in same-run intermediate_refs")
            if not set(item.indeterminate_proof_refs) <= set(self.intermediate_refs):
                raise ValueError("execution indeterminate proofs must be bound in same-run intermediate_refs")
        return self


class ValidationIntermediateCommittedPayloadSchema(_FrozenPostWriteRef):
    schema_version: Literal["validation_intermediate_committed_payload_v1"] = (
        "validation_intermediate_committed_payload_v1"
    )
    intermediate_ref: ValidationIntermediateRefSchema
    producer_ref: NonEmptyStr
    target_ref: NonEmptyStr
    typed_payload: dict[str, Any]
    causation_event_id: NonEmptyStr | None = None


class StageDescriptorSchema(_FrozenPostWriteRef):
    schema_version: Literal["validator_stage_descriptor_v1"] = (
        "validator_stage_descriptor_v1"
    )
    stage_ref: NonEmptyStr
    stage_version: NonEmptyStr
    sequence_index: int = Field(ge=0)
    stage_input_schema_ref: NonEmptyStr
    stage_output_schema_ref: NonEmptyStr
    execution_policy_ref: NonEmptyStr
    execution_policy_version: NonEmptyStr
    timeout_seconds: float = Field(gt=0)
    max_attempts: int = Field(ge=1)
    retryable_failure_categories: tuple[NonEmptyStr, ...] = ()


class StagePlanSchema(_FrozenPostWriteRef):
    schema_version: Literal["validator_stage_plan_v1"] = "validator_stage_plan_v1"
    stage_plan_ref: NonEmptyStr
    validator_id: NonEmptyStr
    validator_version: NonEmptyStr
    stage_plan_version: NonEmptyStr
    stages: tuple[StageDescriptorSchema, ...] = Field(min_length=1)
    stage_plan_hash: Sha256Hex

    @model_validator(mode="after")
    def _ordered_unique_stages(self) -> "StagePlanSchema":
        refs = [item.stage_ref for item in self.stages]
        indexes = [item.sequence_index for item in self.stages]
        if len(refs) != len(set(refs)):
            raise ValueError("stage_ref values must be unique")
        if indexes != list(range(len(indexes))):
            raise ValueError("stage sequence_index must be contiguous from zero")
        return self


class InputEnvelopeSchema(_FrozenPostWriteRef):
    schema_version: Literal["validator_input_envelope_v1"] = (
        "validator_input_envelope_v1"
    )
    execution_contract_version: NonEmptyStr
    input_envelope_ref: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    artifact_state_ref: ArtifactStateRefSchema
    artifact_visible_projection_ref: ArtifactVisibleProjectionRefSchema
    artifact_visible_projection_sha256: Sha256Hex
    validator_id: NonEmptyStr
    validator_version: NonEmptyStr
    stage_plan_ref: NonEmptyStr
    stage_plan_version: NonEmptyStr
    stage_plan_hash: Sha256Hex
    applicable_expected_validation_item_refs: tuple[NonEmptyStr, ...]
    exact_authority_refs: tuple[NonEmptyStr, ...]
    execution_config_refs: tuple[NonEmptyStr, ...] = ()
    input_hash: Sha256Hex

    @model_validator(mode="after")
    def _projection_binding_integrity(self) -> "InputEnvelopeSchema":
        if (
            self.artifact_visible_projection_sha256
            != self.artifact_visible_projection_ref.projection_sha256
        ):
            raise ValueError(
                "validator input visible projection hash/ref mismatch"
            )
        return self


class LogicalConsumerBindingSchema(_FrozenPostWriteRef):
    schema_version: Literal["logical_consumer_binding_v1"] = (
        "logical_consumer_binding_v1"
    )
    validation_run_id: NonEmptyStr
    validator_execution_ref: NonEmptyStr
    stage_ref: NonEmptyStr
    expected_validation_item_ref: NonEmptyStr


class PhysicalModelCallRefSchema(_FrozenPostWriteRef):
    schema_version: Literal["physical_model_call_ref_v1"] = (
        "physical_model_call_ref_v1"
    )
    model_call_id: NonEmptyStr
    provider_attempt_id: NonEmptyStr
    request_fingerprint: Sha256Hex
    response_fingerprint: Sha256Hex
    logical_consumers: tuple[LogicalConsumerBindingSchema, ...] = Field(min_length=1)


__all__ = [
    "ArtifactOriginKind",
    "ArtifactOriginSchema",
    "ArtifactOutputConformanceProofSchema",
    "ArtifactScope",
    "ArtifactStateRecordSchema",
    "ArtifactStateRefSchema",
    "ArtifactValidatedOutputBindingSchema",
    "ArtifactVisibleProjectionRefSchema",
    "ArtifactVisibleProjectionSchema",
    "AvailabilityState",
    "CorrectionArtifactOriginSchema",
    "CorrectionPolicyRefSchema",
    "DocumentAssemblyArtifactOriginSchema",
    "DeliveryGateDecisionRefSchema",
    "EnforcementAction",
    "EvidenceSnapshotRefSchema",
    "InitialWriterArtifactOriginSchema",
    "PostWriteUpstreamBindingSchema",
    "PostWriteControlDecisionRefSchema",
    "PostWriteEntryRefSchema",
    "QualityDeliveryContractRefSchema",
    "ExpectedItemExecutionAccountingSchema",
    "ExpectedItemExecutionState",
    "InputEnvelopeSchema",
    "LogicalConsumerBindingSchema",
    "OverallQualityState",
    "OutputConformanceState",
    "PhysicalModelCallRefSchema",
    "StageDescriptorSchema",
    "StagePlanSchema",
    "ValidationCompletionItemSchema",
    "ValidationCompletionItemState",
    "ValidationCompletionRefSchema",
    "ValidationCompletionSchema",
    "ValidationExecutionEventSchema",
    "ValidationFactFamily",
    "ValidationFactRefSchema",
    "ValidationFactSchema",
    "ValidationIntermediateCommittedPayloadSchema",
    "ValidationIntermediateKind",
    "ValidationIntermediateRefSchema",
    "ValidationRunSchema",
    "ValidationRunState",
    "ValidationScope",
    "ValidationSemanticState",
    "ValidationSubjectKeySchema",
    "ValidationTargetSelectorSchema",
    "ValidatorExecutionRecordSchema",
    "ValidatorTerminalExecutionState",
]
