"""Target Post-write Control / Correction contracts.

Control owns durable runtime action-policy truth. Semantic Writer repair remains
owned by the SIF-11 ParagraphRepairAuthorization path and canonical SIF-12 Budget.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationClass
from core.hashing import canonical_sha256

from .currentness import CurrentnessProofSchema, CurrentnessState
from .document import (
    AssemblyInputManifestRefSchema,
    AssemblyMechanicalRepairProjectionSchema,
    DocumentDefectAttributionSchema,
    DocumentDefectOwnership,
)
from .foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    AvailabilityState,
    CorrectionPolicyRefSchema,
    DeliveryGateDecisionRefSchema,
    EnforcementAction,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
    ValidationFactRefSchema,
)
from .gate import DeliveryGateDecisionSchema


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class _FrozenControlContract(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class CorrectabilityState(str, Enum):
    CORRECTABLE_ARTIFACT_SIDE = "CORRECTABLE_ARTIFACT_SIDE"
    NOT_CORRECTABLE = "NOT_CORRECTABLE"
    UNKNOWN = "UNKNOWN"


class CorrectionPolicyRuleSchema(_FrozenControlContract):
    schema_version: Literal["correction_policy_rule_v1"] = "correction_policy_rule_v1"
    policy_rule_ref: NonEmptyStr
    blocking_condition_ref: NonEmptyStr
    correctability: CorrectabilityState
    reason_code: NonEmptyStr


class CorrectionPolicySchema(_FrozenControlContract):
    schema_version: Literal["correction_policy_v1"] = "correction_policy_v1"
    policy_id: NonEmptyStr
    policy_version: NonEmptyStr
    automatic_correction_enabled: bool = True
    rules: tuple[CorrectionPolicyRuleSchema, ...] = ()
    technical_retry_limit: int = Field(default=1, ge=0)
    repair_budget_dimension: Literal["repair_attempts"] = "repair_attempts"
    policy_hash: Sha256Hex

    @model_validator(mode="after")
    def _policy_integrity(self) -> "CorrectionPolicySchema":
        refs = [item.policy_rule_ref for item in self.rules]
        conditions = [item.blocking_condition_ref for item in self.rules]
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise ValueError("CorrectionPolicy rules must be canonically ordered-unique")
        if len(conditions) != len(set(conditions)):
            raise ValueError("CorrectionPolicy condition mappings must be unique")
        payload = self.model_dump(mode="json")
        payload.pop("policy_hash", None)
        if canonical_sha256(payload) != self.policy_hash:
            raise ValueError("CorrectionPolicy hash mismatch")
        return self

    def as_ref(self) -> CorrectionPolicyRefSchema:
        return CorrectionPolicyRefSchema(
            policy_id=self.policy_id,
            policy_schema_version=self.schema_version,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
        )

    def correctability_for(self, condition_ref: str) -> CorrectabilityState:
        for rule in self.rules:
            if rule.blocking_condition_ref == condition_ref:
                return rule.correctability
        return CorrectabilityState.UNKNOWN


class CorrectionAuthorizationSource(str, Enum):
    SECTION_GATE = "SECTION_GATE"
    DOCUMENT_ATTRIBUTION = "DOCUMENT_ATTRIBUTION"


class CorrectionContextSchema(_FrozenControlContract):
    schema_version: Literal["correction_context_v1"] = "correction_context_v1"
    exact_context_refs: tuple[NonEmptyStr, ...] = ()
    context_hash: Sha256Hex

    @model_validator(mode="after")
    def _context_integrity(self) -> "CorrectionContextSchema":
        if self.exact_context_refs != tuple(sorted(set(self.exact_context_refs))):
            raise ValueError("CorrectionContext refs must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("context_hash", None)
        if canonical_sha256(payload) != self.context_hash:
            raise ValueError("CorrectionContext hash mismatch")
        return self


class ArtifactCorrectionAuthorizationSchema(_FrozenControlContract):
    """Embedded Post-write control evidence; never semantic Writer authority."""

    schema_version: Literal["artifact_correction_authorization_v1"] = (
        "artifact_correction_authorization_v1"
    )
    authorization_contract_version: Literal["post_write_correction_control_v1"] = (
        "post_write_correction_control_v1"
    )
    authorization_id: NonEmptyStr
    source: CorrectionAuthorizationSource
    target_artifact_ref: ArtifactStateRefSchema
    target_post_write_entry_ref: PostWriteEntryRefSchema
    source_gate_decision_ref: DeliveryGateDecisionRefSchema
    document_artifact_ref: ArtifactStateRefSchema | None = None
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema | None = None
    document_defect_attribution_id: NonEmptyStr | None = None
    document_defect_attribution_hash: Sha256Hex | None = None
    authorized_blocking_condition_refs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    correction_reason_refs: tuple[NonEmptyStr, ...] = ()
    correction_policy_ref: CorrectionPolicyRefSchema
    target_generation_class: WriterGenerationClass
    repair_budget_revision: int = Field(ge=0)
    repair_attempts_remaining: int = Field(ge=1)
    correction_context: CorrectionContextSchema | None = None
    authorization_hash: Sha256Hex

    @model_validator(mode="after")
    def _authorization_integrity(self) -> "ArtifactCorrectionAuthorizationSchema":
        if self.target_artifact_ref.artifact_scope is not ArtifactScope.SECTION:
            raise ValueError("semantic correction authorization must target a SECTION")
        if self.target_generation_class is not WriterGenerationClass.NORMAL_WRITE:
            raise ValueError("FALLBACK_WRITE cannot receive automatic correction")
        for name in (
            "authorized_blocking_condition_refs",
            "correction_reason_refs",
        ):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        attributed = self.source is CorrectionAuthorizationSource.DOCUMENT_ATTRIBUTION
        if attributed:
            if (
                self.document_artifact_ref is None
                or self.assembly_input_manifest_ref is None
                or self.document_defect_attribution_id is None
                or self.document_defect_attribution_hash is None
            ):
                raise ValueError(
                    "DOCUMENT_ATTRIBUTION requires exact document/manifest/attribution"
                )
        elif any(
            item is not None
            for item in (
                self.document_artifact_ref,
                self.assembly_input_manifest_ref,
                self.document_defect_attribution_id,
                self.document_defect_attribution_hash,
            )
        ):
            raise ValueError("SECTION_GATE cannot carry document attribution fields")
        payload = self.model_dump(mode="json")
        payload.pop("authorization_hash", None)
        expected_hash = canonical_sha256(payload)
        if expected_hash != self.authorization_hash:
            raise ValueError("ArtifactCorrectionAuthorization hash mismatch")
        expected_id = "artifact-correction-auth://sha256/" + canonical_sha256(
            {
                "source": self.source.value,
                "target_artifact_ref": self.target_artifact_ref.model_dump(mode="json"),
                "target_post_write_entry_ref": self.target_post_write_entry_ref.model_dump(
                    mode="json"
                ),
                "source_gate_decision_ref": self.source_gate_decision_ref.model_dump(
                    mode="json"
                ),
                "document_artifact_ref": (
                    self.document_artifact_ref.model_dump(mode="json")
                    if self.document_artifact_ref is not None
                    else None
                ),
                "assembly_input_manifest_ref": (
                    self.assembly_input_manifest_ref.model_dump(mode="json")
                    if self.assembly_input_manifest_ref is not None
                    else None
                ),
                "document_defect_attribution_id": (
                    self.document_defect_attribution_id
                ),
                "document_defect_attribution_hash": (
                    self.document_defect_attribution_hash
                ),
                "authorized_blocking_condition_refs": list(
                    self.authorized_blocking_condition_refs
                ),
                "correction_policy_ref": self.correction_policy_ref.model_dump(mode="json"),
                "repair_budget_revision": self.repair_budget_revision,
                "correction_context_hash": (
                    self.correction_context.context_hash
                    if self.correction_context is not None
                    else None
                ),
            }
        )
        if self.authorization_id != expected_id:
            raise ValueError("ArtifactCorrectionAuthorization id mismatch")
        return self


class CorrectionAttemptOutcome(str, Enum):
    CANDIDATE_COMMITTED = "CANDIDATE_COMMITTED"
    NO_PROGRESS_NOOP = "NO_PROGRESS_NOOP"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    STALE_INPUT = "STALE_INPUT"


class CorrectionAttemptRecordSchema(_FrozenControlContract):
    """Compatibility/observability projection only, never repair authority."""

    schema_version: Literal["correction_attempt_projection_v1"] = (
        "correction_attempt_projection_v1"
    )
    projection_id: NonEmptyStr
    run_id: NonEmptyStr
    target_artifact_ref: ArtifactStateRefSchema
    control_decision_ref: PostWriteControlDecisionRefSchema
    embedded_authorization_id: NonEmptyStr
    embedded_authorization_hash: Sha256Hex
    paragraph_repair_authorization_ref: NonEmptyStr | None = None
    canonical_result_refs: tuple[NonEmptyStr, ...] = ()
    outcome: CorrectionAttemptOutcome
    projection_hash: Sha256Hex
    created_at_epoch: float

    @model_validator(mode="after")
    def _projection_integrity(self) -> "CorrectionAttemptRecordSchema":
        if self.canonical_result_refs != tuple(sorted(set(self.canonical_result_refs))):
            raise ValueError("canonical_result_refs must be ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        payload.pop("created_at_epoch", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("CorrectionAttempt projection hash mismatch")
        return self


class CorrectionProjectionState(str, Enum):
    NOT_NEEDED = "NOT_NEEDED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ELIGIBLE_AND_REQUESTED = "ELIGIBLE_AND_REQUESTED"
    CANDIDATE_COMMITTED = "CANDIDATE_COMMITTED"
    EXHAUSTED = "EXHAUSTED"
    NOT_CORRECTABLE = "NOT_CORRECTABLE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    STALE_INPUT = "STALE_INPUT"


class CorrectionLineageProjectionSchema(_FrozenControlContract):
    """Decision-owned projection over canonical lineage + SIF-12 Budget state."""

    schema_version: Literal["correction_lineage_projection_v1"] = (
        "correction_lineage_projection_v1"
    )
    target_artifact_ref: ArtifactStateRefSchema
    prior_correct_control_decision_refs: tuple[PostWriteControlDecisionRefSchema, ...] = ()
    correction_attempt_projection_refs: tuple[NonEmptyStr, ...] = ()
    repair_budget_policy_ref: NonEmptyStr
    repair_budget_policy_version: NonEmptyStr
    repair_budget_revision: int = Field(ge=0)
    repair_attempts_limit: int = Field(ge=0)
    repair_attempts_used: int = Field(ge=0)
    repair_attempts_active_reserved: int = Field(ge=0)
    repair_attempts_remaining: int = Field(ge=0)
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _lineage_integrity(self) -> "CorrectionLineageProjectionSchema":
        refs = [item.control_decision_id for item in self.prior_correct_control_decision_refs]
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise ValueError("prior CORRECT decisions must be ordered-unique")
        if self.correction_attempt_projection_refs != tuple(
            sorted(set(self.correction_attempt_projection_refs))
        ):
            raise ValueError("correction attempt projection refs must be ordered-unique")
        if (
            self.repair_attempts_used
            + self.repair_attempts_active_reserved
            + self.repair_attempts_remaining
            != self.repair_attempts_limit
        ):
            raise ValueError("repair_attempts Budget projection does not balance")
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("CorrectionLineageProjection hash mismatch")
        return self


class CorrectionProjectionSchema(_FrozenControlContract):
    schema_version: Literal["correction_projection_v1"] = "correction_projection_v1"
    state: CorrectionProjectionState
    correctable_blocking_condition_refs: tuple[NonEmptyStr, ...] = ()
    non_correctable_blocking_condition_refs: tuple[NonEmptyStr, ...] = ()
    authorization: ArtifactCorrectionAuthorizationSchema | None = None
    reason_codes: tuple[NonEmptyStr, ...] = ()
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _correction_projection_integrity(self) -> "CorrectionProjectionSchema":
        for name in (
            "correctable_blocking_condition_refs",
            "non_correctable_blocking_condition_refs",
            "reason_codes",
        ):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        if (
            self.authorization is not None
            and self.state is not CorrectionProjectionState.ELIGIBLE_AND_REQUESTED
        ):
            raise ValueError(
                "ArtifactCorrectionAuthorization requires ELIGIBLE_AND_REQUESTED"
            )
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("CorrectionProjection hash mismatch")
        return self


class ArtifactUsabilityIntegrityState(str, Enum):
    TRUSTED_USABLE = "TRUSTED_USABLE"
    UNUSABLE = "UNUSABLE"
    INTEGRITY_INVALID = "INTEGRITY_INVALID"
    UNRESOLVED = "UNRESOLVED"


class ArtifactUsabilityIntegrityProofSchema(_FrozenControlContract):
    schema_version: Literal["artifact_usability_integrity_proof_v1"] = (
        "artifact_usability_integrity_proof_v1"
    )
    artifact_state_ref: ArtifactStateRefSchema
    currentness_proof_sha256: Sha256Hex
    artifact_record_hash: Sha256Hex | None = None
    state: ArtifactUsabilityIntegrityState
    reason_codes: tuple[NonEmptyStr, ...] = ()
    proof_sha256: Sha256Hex

    @model_validator(mode="after")
    def _usability_integrity(self) -> "ArtifactUsabilityIntegrityProofSchema":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("usability reason codes must be ordered-unique")
        if (
            self.state is ArtifactUsabilityIntegrityState.TRUSTED_USABLE
            and self.artifact_record_hash is None
        ):
            raise ValueError("TRUSTED_USABLE requires exact Artifact record hash")
        payload = self.model_dump(mode="json")
        payload.pop("proof_sha256", None)
        if canonical_sha256(payload) != self.proof_sha256:
            raise ValueError("Artifact usability proof hash mismatch")
        return self


class AvailabilityProjectionSchema(_FrozenControlContract):
    schema_version: Literal["availability_projection_v1"] = "availability_projection_v1"
    state: AvailabilityState
    reason_codes: tuple[NonEmptyStr, ...] = ()
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _availability_integrity(self) -> "AvailabilityProjectionSchema":
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("availability reason codes must be ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("AvailabilityProjection hash mismatch")
        return self


class _ControlManifestBase(_FrozenControlContract):
    validation_run_id: NonEmptyStr
    delivery_gate_decision_ref: DeliveryGateDecisionRefSchema
    post_write_entry_ref: PostWriteEntryRefSchema
    artifact_state_ref: ArtifactStateRefSchema
    fresh_currentness_proof_sha256: Sha256Hex
    artifact_usability_integrity_proof_sha256: Sha256Hex
    correction_policy_ref: CorrectionPolicyRefSchema
    correction_lineage_projection_hash: Sha256Hex
    control_contract_version: NonEmptyStr
    input_manifest_hash: Sha256Hex

    def _validate_hash(self) -> None:
        payload = self.model_dump(mode="json")
        payload.pop("input_manifest_hash", None)
        if canonical_sha256(payload) != self.input_manifest_hash:
            raise ValueError("PostWriteControl input manifest hash mismatch")


class SectionPostWriteControlInputManifestSchema(_ControlManifestBase):
    schema_version: Literal["section_post_write_control_input_manifest_v1"] = (
        "section_post_write_control_input_manifest_v1"
    )
    artifact_scope: Literal[ArtifactScope.SECTION] = ArtifactScope.SECTION
    generation_class: WriterGenerationClass

    @model_validator(mode="after")
    def _section_integrity(self) -> "SectionPostWriteControlInputManifestSchema":
        if self.artifact_state_ref.artifact_scope is not ArtifactScope.SECTION:
            raise ValueError("SECTION Control manifest requires SECTION Artifact")
        self._validate_hash()
        return self


class DocumentPostWriteControlInputManifestSchema(_ControlManifestBase):
    schema_version: Literal["document_post_write_control_input_manifest_v1"] = (
        "document_post_write_control_input_manifest_v1"
    )
    artifact_scope: Literal[ArtifactScope.DOCUMENT] = ArtifactScope.DOCUMENT
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema
    document_correction_dispatch_projection_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def _document_integrity(self) -> "DocumentPostWriteControlInputManifestSchema":
        if self.artifact_state_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise ValueError("DOCUMENT Control manifest requires DOCUMENT Artifact")
        self._validate_hash()
        return self


PostWriteControlInputManifestSchema = (
    SectionPostWriteControlInputManifestSchema
    | DocumentPostWriteControlInputManifestSchema
)


class DocumentCorrectionDispatchMode(str, Enum):
    SECTION_DISPATCH = "SECTION_DISPATCH"
    ASSEMBLY_MECHANICAL = "ASSEMBLY_MECHANICAL"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class DocumentCorrectionDispatchProjectionSchema(_FrozenControlContract):
    schema_version: Literal["document_correction_dispatch_projection_v1"] = (
        "document_correction_dispatch_projection_v1"
    )
    projection_contract_version: NonEmptyStr
    document_gate_decision_ref: DeliveryGateDecisionRefSchema
    document_artifact_ref: ArtifactStateRefSchema
    assembly_input_manifest_ref: AssemblyInputManifestRefSchema
    defect_attributions: tuple[DocumentDefectAttributionSchema, ...] = ()
    correction_authorizations: tuple[ArtifactCorrectionAuthorizationSchema, ...] = ()
    assembly_mechanical_repairs: tuple[AssemblyMechanicalRepairProjectionSchema, ...] = ()
    correction_mode: DocumentCorrectionDispatchMode
    dispatch_reason_codes: tuple[NonEmptyStr, ...] = ()
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _dispatch_integrity(self) -> "DocumentCorrectionDispatchProjectionSchema":
        if self.document_artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise ValueError("Document correction dispatch requires DOCUMENT Artifact")
        attribution_keys = [
            (item.attribution_id, item.attribution_hash)
            for item in self.defect_attributions
        ]
        if len(attribution_keys) != len(set(attribution_keys)):
            raise ValueError("Document defect attributions must be unique")
        authorization_keys = [
            (item.authorization_id, item.authorization_hash)
            for item in self.correction_authorizations
        ]
        if len(authorization_keys) != len(set(authorization_keys)):
            raise ValueError("Document correction authorizations must be unique")
        repair_keys = [
            (item.repair_id, item.repair_projection_hash)
            for item in self.assembly_mechanical_repairs
        ]
        if len(repair_keys) != len(set(repair_keys)):
            raise ValueError("assembly mechanical repairs must be unique")
        if self.dispatch_reason_codes != tuple(
            sorted(set(self.dispatch_reason_codes))
        ):
            raise ValueError("dispatch reason codes must be canonical ordered-unique")

        attribution_by_id = {
            item.attribution_id: item for item in self.defect_attributions
        }
        for attribution in self.defect_attributions:
            if attribution.document_gate_decision_ref != self.document_gate_decision_ref:
                raise ValueError("Document attribution binds another Gate decision")

        if self.correction_mode is DocumentCorrectionDispatchMode.SECTION_DISPATCH:
            if not self.correction_authorizations:
                raise ValueError("SECTION_DISPATCH requires correction authorizations")
            if self.assembly_mechanical_repairs:
                raise ValueError("SECTION_DISPATCH forbids mechanical repairs")
            for authorization in self.correction_authorizations:
                if authorization.source is not CorrectionAuthorizationSource.DOCUMENT_ATTRIBUTION:
                    raise ValueError(
                        "SECTION_DISPATCH requires DOCUMENT_ATTRIBUTION authorization"
                    )
                if (
                    authorization.document_artifact_ref != self.document_artifact_ref
                    or authorization.assembly_input_manifest_ref
                    != self.assembly_input_manifest_ref
                    or authorization.source_gate_decision_ref
                    != self.document_gate_decision_ref
                ):
                    raise ValueError(
                        "Document correction authorization outer binding mismatch"
                    )
                attribution = attribution_by_id.get(
                    authorization.document_defect_attribution_id or ""
                )
                if (
                    attribution is None
                    or attribution.attribution_hash
                    != authorization.document_defect_attribution_hash
                    or attribution.ownership
                    is not DocumentDefectOwnership.SECTION_CONTENT
                    or authorization.target_artifact_ref
                    not in attribution.affected_section_artifact_refs
                ):
                    raise ValueError(
                        "Document correction authorization attribution mismatch"
                    )
        elif (
            self.correction_mode
            is DocumentCorrectionDispatchMode.ASSEMBLY_MECHANICAL
        ):
            if not self.assembly_mechanical_repairs:
                raise ValueError(
                    "ASSEMBLY_MECHANICAL requires exact mechanical repair projection"
                )
            if self.correction_authorizations:
                raise ValueError(
                    "ASSEMBLY_MECHANICAL forbids semantic Section authorizations"
                )
        else:
            if self.correction_authorizations or self.assembly_mechanical_repairs:
                raise ValueError(
                    "NOT_ELIGIBLE forbids correction authorization/repair execution"
                )

        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("DocumentCorrectionDispatch projection hash mismatch")
        return self


class PostWriteControlDecisionSchema(_FrozenControlContract):
    schema_version: Literal["post_write_control_decision_v1"] = (
        "post_write_control_decision_v1"
    )
    control_decision_id: NonEmptyStr
    control_contract_version: NonEmptyStr
    validation_run_id: NonEmptyStr
    delivery_gate_decision_ref: DeliveryGateDecisionRefSchema
    input_manifest: PostWriteControlInputManifestSchema
    fresh_currentness_proof: CurrentnessProofSchema
    artifact_usability_integrity_proof: ArtifactUsabilityIntegrityProofSchema
    correction_lineage_projection: CorrectionLineageProjectionSchema
    correction_projection: CorrectionProjectionSchema
    document_correction_dispatch_projection: (
        DocumentCorrectionDispatchProjectionSchema | None
    ) = None
    availability_projection: AvailabilityProjectionSchema
    quality_state_passthrough: OverallQualityState
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()
    control_reason_codes: tuple[NonEmptyStr, ...] = ()
    enforcement_action: EnforcementAction
    created_at_epoch: float
    control_decision_hash: Sha256Hex

    @model_validator(mode="after")
    def _decision_integrity(self) -> "PostWriteControlDecisionSchema":
        if self.control_contract_version != self.input_manifest.control_contract_version:
            raise ValueError("Control contract version mismatch")
        if self.validation_run_id != self.input_manifest.validation_run_id:
            raise ValueError("Control Run mismatch")
        if self.delivery_gate_decision_ref != self.input_manifest.delivery_gate_decision_ref:
            raise ValueError("Control Gate ref mismatch")
        if (
            self.fresh_currentness_proof.proof_sha256
            != self.input_manifest.fresh_currentness_proof_sha256
        ):
            raise ValueError("Control currentness proof/manifest mismatch")
        if (
            self.artifact_usability_integrity_proof.proof_sha256
            != self.input_manifest.artifact_usability_integrity_proof_sha256
        ):
            raise ValueError("Control usability proof/manifest mismatch")
        if (
            self.correction_lineage_projection.projection_hash
            != self.input_manifest.correction_lineage_projection_hash
        ):
            raise ValueError("Control correction lineage/manifest mismatch")
        if (
            self.artifact_usability_integrity_proof.artifact_state_ref
            != self.input_manifest.artifact_state_ref
        ):
            raise ValueError("Control usability proof targets another Artifact")
        fact_ids = [item.fact_id for item in self.blocking_fact_refs]
        if fact_ids != sorted(fact_ids) or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("blocking_fact_refs must be canonical ordered-unique")
        for name in ("unresolved_reason_refs", "control_reason_codes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")

        is_document = isinstance(
            self.input_manifest,
            DocumentPostWriteControlInputManifestSchema,
        )
        dispatch = self.document_correction_dispatch_projection
        if is_document:
            if dispatch is not None and (
                dispatch.document_gate_decision_ref
                != self.delivery_gate_decision_ref
                or dispatch.document_artifact_ref
                != self.input_manifest.artifact_state_ref
                or dispatch.assembly_input_manifest_ref
                != self.input_manifest.assembly_input_manifest_ref
                or dispatch.projection_hash
                != self.input_manifest.document_correction_dispatch_projection_hash
            ):
                raise ValueError("Document correction dispatch/input mismatch")
            if dispatch is not None:
                policy_ref = self.input_manifest.correction_policy_ref
                if any(
                    item.correction_policy_ref != policy_ref
                    for item in dispatch.correction_authorizations
                ) or any(
                    item.correction_policy_ref != policy_ref
                    for item in dispatch.assembly_mechanical_repairs
                ):
                    raise ValueError(
                        "Document correction dispatch uses another CorrectionPolicy"
                    )
            if (
                dispatch is None
                and self.input_manifest.document_correction_dispatch_projection_hash
                is not None
            ):
                raise ValueError(
                    "Document Control input carries dangling dispatch projection hash"
                )
            if self.correction_projection.authorization is not None:
                raise ValueError(
                    "Document Control cannot use singular Section correction authorization"
                )
        elif dispatch is not None:
            raise ValueError("SECTION Control cannot embed Document correction dispatch")

        if self.enforcement_action is EnforcementAction.CORRECT:
            if (
                self.correction_projection.state
                is not CorrectionProjectionState.ELIGIBLE_AND_REQUESTED
                or self.availability_projection.state is not AvailabilityState.WITHHELD
            ):
                raise ValueError("CORRECT requires requested correction + WITHHELD")
            if is_document:
                if (
                    dispatch is None
                    or dispatch.correction_mode
                    is DocumentCorrectionDispatchMode.NOT_ELIGIBLE
                ):
                    raise ValueError(
                        "Document CORRECT requires executable dispatch projection"
                    )
            elif self.correction_projection.authorization is None:
                raise ValueError(
                    "Section CORRECT requires exact ArtifactCorrectionAuthorization"
                )
        elif self.enforcement_action is EnforcementAction.OUTPUT:
            if (
                self.quality_state_passthrough is not OverallQualityState.PASS
                or self.availability_projection.state is not AvailabilityState.AVAILABLE
            ):
                raise ValueError("OUTPUT requires PASS + AVAILABLE")
            if (
                is_document
                and dispatch is not None
                and dispatch.correction_mode
                is not DocumentCorrectionDispatchMode.NOT_ELIGIBLE
            ):
                raise ValueError("non-CORRECT Document cannot carry executable dispatch")
        elif self.enforcement_action is EnforcementAction.OUTPUT_BEST_EFFORT:
            if (
                self.quality_state_passthrough is OverallQualityState.PASS
                or self.availability_projection.state is not AvailabilityState.BEST_EFFORT
            ):
                raise ValueError("OUTPUT_BEST_EFFORT requires non-PASS + BEST_EFFORT")
            if (
                is_document
                and dispatch is not None
                and dispatch.correction_mode
                is not DocumentCorrectionDispatchMode.NOT_ELIGIBLE
            ):
                raise ValueError("non-CORRECT Document cannot carry executable dispatch")
        elif self.availability_projection.state is not AvailabilityState.WITHHELD:
            raise ValueError("BLOCK requires WITHHELD")

        payload = self.model_dump(mode="json")
        payload.pop("control_decision_hash", None)
        payload.pop("created_at_epoch", None)
        if canonical_sha256(payload) != self.control_decision_hash:
            raise ValueError("PostWriteControlDecision hash mismatch")
        expected_id = "post-write-control://sha256/" + canonical_sha256(
            {
                "control_contract_version": self.control_contract_version,
                "input_manifest_hash": self.input_manifest.input_manifest_hash,
            }
        )
        if self.control_decision_id != expected_id:
            raise ValueError("PostWriteControlDecision id mismatch")
        return self

    def as_ref(self) -> PostWriteControlDecisionRefSchema:
        return PostWriteControlDecisionRefSchema(
            control_decision_id=self.control_decision_id,
            schema_version=self.schema_version,
            control_contract_version=self.control_contract_version,
            control_decision_hash=self.control_decision_hash,
        )


__all__ = [
    "ArtifactCorrectionAuthorizationSchema",
    "ArtifactUsabilityIntegrityProofSchema",
    "ArtifactUsabilityIntegrityState",
    "AvailabilityProjectionSchema",
    "CorrectabilityState",
    "CorrectionAttemptOutcome",
    "CorrectionAttemptRecordSchema",
    "CorrectionAuthorizationSource",
    "CorrectionContextSchema",
    "CorrectionLineageProjectionSchema",
    "CorrectionPolicyRuleSchema",
    "CorrectionPolicySchema",
    "CorrectionProjectionSchema",
    "CorrectionProjectionState",
    "DocumentCorrectionDispatchMode",
    "DocumentCorrectionDispatchProjectionSchema",
    "DocumentPostWriteControlInputManifestSchema",
    "PostWriteControlDecisionSchema",
    "PostWriteControlInputManifestSchema",
    "SectionPostWriteControlInputManifestSchema",
]
