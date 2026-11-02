"""Target Post-write Quality Policy / Delivery Gate contracts.

Semantic quality truth remains ValidationFact. This module owns only typed
Contract policy projection and immutable delivery-eligibility decision schemas.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator, field_validator

from apps.enterprise_document.schemas.contract_base import ContractSchema
from core.hashing import canonical_sha256

from .currentness import CurrentnessProofSchema
from .entry import PostWriteEvidenceBindingRef
from .foundation import (
    ArtifactStateRefSchema,
    DeliveryGateDecisionRefSchema,
    OverallQualityState,
    PostWriteEntryRefSchema,
    QualityDeliveryContractRefSchema,
    ValidationCompletionRefSchema,
    ValidationIntermediateRefSchema,
    ValidationFactFamily,
    ValidationFactRefSchema,
    ValidationSemanticState,
    ValidationSubjectKeySchema,
)
from .requirement import RequirementRefSchema
from .consistency_procedure import ConsistencyProcedureInputSchema


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


class _FrozenGateContract(ContractSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class QualityPolicyEffect(str, Enum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFORMATIONAL = "INFORMATIONAL"


class RequiredConditionState(str, Enum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DimensionQualityState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GateBlockingConditionKind(str, Enum):
    FACT_POLICY_BLOCK = "FACT_POLICY_BLOCK"
    REQUIRED_DIMENSION_CONDITION_VIOLATION = (
        "REQUIRED_DIMENSION_CONDITION_VIOLATION"
    )


class FactPolicyRuleSchema(_FrozenGateContract):
    schema_version: Literal["quality_fact_policy_rule_v1"] = (
        "quality_fact_policy_rule_v1"
    )
    policy_rule_ref: NonEmptyStr
    fact_family: ValidationFactFamily
    semantic_states: tuple[ValidationSemanticState, ...] = (
        ValidationSemanticState.FAIL,
    )
    effect: QualityPolicyEffect
    reason_code: NonEmptyStr
    target_refs: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _canonical_rule(self) -> "FactPolicyRuleSchema":
        states = tuple(self.semantic_states)
        if len(states) != len(set(states)):
            raise ValueError("fact policy semantic_states must be unique")
        targets = tuple(self.target_refs)
        if targets != tuple(sorted(set(targets))):
            raise ValueError("fact policy target_refs must be canonical ordered-unique")
        return self


class RequiredDimensionConditionSchema(_FrozenGateContract):
    schema_version: Literal["required_dimension_condition_v1"] = (
        "required_dimension_condition_v1"
    )
    dimension_id: NonEmptyStr
    required: bool = True
    accepted_states: tuple[Literal["PASS", "FAIL", "UNRESOLVED"], ...] = ("PASS",)
    fact_families: tuple[ValidationFactFamily, ...] = ()
    target_refs: tuple[NonEmptyStr, ...] = ()
    condition_ref: NonEmptyStr | None = None
    condition_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_states(self) -> "RequiredDimensionConditionSchema":
        if len(self.accepted_states) != len(set(self.accepted_states)):
            raise ValueError("accepted_states must be unique")
        families = tuple(self.fact_families)
        if len(families) != len(set(families)):
            raise ValueError("fact_families must be unique")
        targets = tuple(self.target_refs)
        if targets != tuple(sorted(set(targets))):
            raise ValueError("condition target_refs must be canonical ordered-unique")
        return self

    def effective_condition_ref(self) -> str:
        if self.condition_ref is not None:
            return self.condition_ref
        return "quality-condition://sha256/" + canonical_sha256(
            {
                "schema_version": self.schema_version,
                "dimension_id": self.dimension_id,
                "required": self.required,
                "accepted_states": self.accepted_states,
                "fact_families": [item.value for item in self.fact_families],
                "target_refs": self.target_refs,
                "condition_metadata": self.condition_metadata,
            }
        )


class QualityDeliveryContractSchema(_FrozenGateContract):
    schema_version: Literal["quality_delivery_contract_v1"] = (
        "quality_delivery_contract_v1"
    )
    contract_id: NonEmptyStr
    contract_version: NonEmptyStr
    required_dimensions: tuple[RequiredDimensionConditionSchema, ...] = Field(
        min_length=1
    )
    fact_policy_rules: tuple[FactPolicyRuleSchema, ...] = ()
    unmatched_fact_effect: QualityPolicyEffect = QualityPolicyEffect.INFORMATIONAL
    gate_contract_version: NonEmptyStr = "delivery_gate_v1"
    policy_projection_contract_version: NonEmptyStr = "quality_policy_projection_v1"
    contract_metadata: dict[str, Any] = Field(default_factory=dict)
    consistency_procedure_input: ConsistencyProcedureInputSchema | None = Field(default=None, exclude_if=lambda value: value is None)
    contract_hash: Sha256Hex

    @field_validator("consistency_procedure_input", mode="before")
    @classmethod
    def _empty_procedure_input(cls, value):
        return None if value == {} else value

    @model_validator(mode="after")
    def _contract_shape(self) -> "QualityDeliveryContractSchema":
        dimension_ids = [item.dimension_id for item in self.required_dimensions]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("quality dimension ids must be unique")
        rule_ids = [item.policy_rule_ref for item in self.fact_policy_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("quality fact policy rule refs must be unique")
        return self

    def as_ref(self) -> QualityDeliveryContractRefSchema:
        return QualityDeliveryContractRefSchema(
            contract_id=self.contract_id,
            contract_schema_version=self.schema_version,
            contract_version=self.contract_version,
            contract_hash=self.contract_hash,
        )


class FactPolicyEffectSchema(_FrozenGateContract):
    schema_version: Literal["fact_policy_effect_v1"] = "fact_policy_effect_v1"
    fact_ref: ValidationFactRefSchema
    effect: QualityPolicyEffect
    policy_rule_ref: NonEmptyStr
    reason_code: NonEmptyStr


class GateBlockingConditionSchema(_FrozenGateContract):
    schema_version: Literal["gate_blocking_condition_v1"] = (
        "gate_blocking_condition_v1"
    )
    blocking_kind: GateBlockingConditionKind
    condition_ref: NonEmptyStr
    reason_code: NonEmptyStr
    fact_ref: ValidationFactRefSchema | None = None
    policy_rule_ref: NonEmptyStr | None = None


class RequiredDimensionConditionResultSchema(_FrozenGateContract):
    schema_version: Literal["required_dimension_condition_result_v1"] = (
        "required_dimension_condition_result_v1"
    )
    condition_ref: NonEmptyStr
    dimension_id: NonEmptyStr
    state: RequiredConditionState
    consumed_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    positive_proof_refs: tuple[ValidationIntermediateRefSchema, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    indeterminate_proof_refs: tuple[ValidationIntermediateRefSchema, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()
    result_hash: Sha256Hex

    @model_validator(mode="after")
    def _canonical_result(self) -> "RequiredDimensionConditionResultSchema":
        fact_ids = [item.fact_id for item in self.consumed_fact_refs]
        if fact_ids != sorted(fact_ids) or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("condition consumed facts must be canonical ordered-unique")
        proof_ids = [
            (item.validation_run_id, item.event_id)
            for item in (*self.positive_proof_refs, *self.indeterminate_proof_refs)
        ]
        if proof_ids != sorted(proof_ids) or len(proof_ids) != len(set(proof_ids)):
            raise ValueError("condition positive proofs must be canonical ordered-unique")
        if self.positive_proof_refs and self.state is not RequiredConditionState.SATISFIED:
            raise ValueError("condition positive proofs only belong to SATISFIED")
        if self.indeterminate_proof_refs and self.state is not RequiredConditionState.UNRESOLVED:
            raise ValueError("condition indeterminate proofs only belong to UNRESOLVED")
        reasons = tuple(self.unresolved_reason_refs)
        if reasons != tuple(sorted(set(reasons))):
            raise ValueError("condition unresolved reasons must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("result_hash", None)
        if canonical_sha256(payload) != self.result_hash:
            raise ValueError("required dimension condition result hash mismatch")
        return self


class QualityPolicyProjectionSchema(_FrozenGateContract):
    schema_version: Literal["quality_policy_projection_v1"] = (
        "quality_policy_projection_v1"
    )
    projection_id: NonEmptyStr
    projection_contract_version: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_hash: Sha256Hex
    quality_delivery_contract_ref: QualityDeliveryContractRefSchema
    consumed_fact_refs: tuple[ValidationFactRefSchema, ...]
    consumed_fact_manifest_hash: Sha256Hex
    fact_policy_effects: tuple[FactPolicyEffectSchema, ...]
    required_dimension_conditions: tuple[RequiredDimensionConditionSchema, ...]
    condition_results: tuple[RequiredDimensionConditionResultSchema, ...]
    blocking_conditions: tuple[GateBlockingConditionSchema, ...]
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...]
    warning_fact_refs: tuple[ValidationFactRefSchema, ...]
    informational_fact_refs: tuple[ValidationFactRefSchema, ...]
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _projection_integrity(self) -> "QualityPolicyProjectionSchema":
        for name in (
            "consumed_fact_refs",
            "blocking_fact_refs",
            "warning_fact_refs",
            "informational_fact_refs",
        ):
            values = tuple(getattr(self, name))
            ids = [item.fact_id for item in values]
            if ids != sorted(ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        condition_refs = [item.condition_ref for item in self.condition_results]
        if condition_refs != sorted(condition_refs) or len(condition_refs) != len(
            set(condition_refs)
        ):
            raise ValueError("quality projection condition results must be ordered-unique")
        if canonical_sha256(
            [item.model_dump(mode="json") for item in self.consumed_fact_refs]
        ) != self.consumed_fact_manifest_hash:
            raise ValueError("quality projection fact manifest hash mismatch")
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("quality policy projection hash mismatch")
        proof_refs = tuple(
            sorted(
                (
                    proof
                    for result in self.condition_results
                    for proof in result.positive_proof_refs
                ),
                key=lambda item: (item.validation_run_id, item.event_id),
            )
        )
        identity = {
            "contract_ref": self.quality_delivery_contract_ref.model_dump(mode="json"),
            "validation_run_id": self.validation_run_id,
            "subject_hash": self.subject_hash,
            "fact_manifest_hash": self.consumed_fact_manifest_hash,
            "projection_contract_version": self.projection_contract_version,
        }
        if proof_refs:
            identity["positive_proof_manifest_hash"] = canonical_sha256(
                [item.model_dump(mode="json") for item in proof_refs]
            )
        expected_id = "quality-policy-projection://sha256/" + canonical_sha256(identity)
        if self.projection_id != expected_id:
            raise ValueError("quality policy projection id mismatch")
        return self


class DimensionResultProjectionSchema(_FrozenGateContract):
    schema_version: Literal["dimension_result_projection_v1"] = (
        "dimension_result_projection_v1"
    )
    dimension_id: NonEmptyStr
    state: DimensionQualityState
    consumed_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    warning_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    informational_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()
    required_condition_refs: tuple[NonEmptyStr, ...] = ()
    condition_results: tuple[RequiredDimensionConditionResultSchema, ...] = ()
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _dimension_integrity(self) -> "DimensionResultProjectionSchema":
        for name in (
            "consumed_fact_refs",
            "blocking_fact_refs",
            "warning_fact_refs",
            "informational_fact_refs",
        ):
            ids = [item.fact_id for item in getattr(self, name)]
            if ids != sorted(ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        for name in ("unresolved_reason_refs", "required_condition_refs"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("dimension result projection hash mismatch")
        return self


class OverallQualityProjectionSchema(_FrozenGateContract):
    schema_version: Literal["overall_quality_projection_v1"] = (
        "overall_quality_projection_v1"
    )
    overall_quality_state: OverallQualityState
    dimension_projection_hashes: tuple[Sha256Hex, ...]
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    warning_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    informational_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()
    projection_hash: Sha256Hex

    @model_validator(mode="after")
    def _overall_integrity(self) -> "OverallQualityProjectionSchema":
        if tuple(self.dimension_projection_hashes) != tuple(
            sorted(self.dimension_projection_hashes)
        ):
            raise ValueError("dimension projection hashes must be canonically sorted")
        for name in (
            "blocking_fact_refs",
            "warning_fact_refs",
            "informational_fact_refs",
        ):
            ids = [item.fact_id for item in getattr(self, name)]
            if ids != sorted(ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        reasons = tuple(self.unresolved_reason_refs)
        if reasons != tuple(sorted(set(reasons))):
            raise ValueError("overall unresolved reasons must be canonical ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("projection_hash", None)
        if canonical_sha256(payload) != self.projection_hash:
            raise ValueError("overall quality projection hash mismatch")
        return self


class GateInputManifestSchema(_FrozenGateContract):
    schema_version: Literal["gate_input_manifest_v1"] = "gate_input_manifest_v1"
    validation_run_id: NonEmptyStr
    subject_key: ValidationSubjectKeySchema
    subject_hash: Sha256Hex
    post_write_entry_ref: PostWriteEntryRefSchema
    artifact_state_ref: ArtifactStateRefSchema
    evidence_binding: PostWriteEvidenceBindingRef
    requirement_ref: RequirementRefSchema
    quality_delivery_contract_ref: QualityDeliveryContractRefSchema
    validation_fact_refs: tuple[ValidationFactRefSchema, ...]
    validation_fact_manifest_hash: Sha256Hex
    validation_completion_ref: ValidationCompletionRefSchema
    validation_completion_hash: Sha256Hex
    quality_policy_projection_id: NonEmptyStr
    quality_policy_projection_hash: Sha256Hex
    currentness_proof_sha256: Sha256Hex
    gate_contract_version: NonEmptyStr
    input_manifest_hash: Sha256Hex

    @model_validator(mode="after")
    def _manifest_integrity(self) -> "GateInputManifestSchema":
        ids = [item.fact_id for item in self.validation_fact_refs]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("Gate manifest Fact refs must be canonical ordered-unique")
        if canonical_sha256(
            [item.model_dump(mode="json") for item in self.validation_fact_refs]
        ) != self.validation_fact_manifest_hash:
            raise ValueError("Gate manifest Fact hash mismatch")
        payload = self.model_dump(mode="json")
        payload.pop("input_manifest_hash", None)
        if canonical_sha256(payload) != self.input_manifest_hash:
            raise ValueError("Gate input manifest hash mismatch")
        return self


class DeliveryGateDecisionSchema(_FrozenGateContract):
    schema_version: Literal["delivery_gate_decision_v1"] = (
        "delivery_gate_decision_v1"
    )
    delivery_gate_decision_id: NonEmptyStr
    gate_contract_version: NonEmptyStr
    validation_run_id: NonEmptyStr
    subject_key: ValidationSubjectKeySchema
    gate_input_manifest: GateInputManifestSchema
    quality_policy_projection: QualityPolicyProjectionSchema
    currentness_proof: CurrentnessProofSchema
    dimension_result_projections: tuple[DimensionResultProjectionSchema, ...]
    overall_quality_projection: OverallQualityProjectionSchema
    overall_quality_state: OverallQualityState
    blocking_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    warning_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    informational_fact_refs: tuple[ValidationFactRefSchema, ...] = ()
    unresolved_reason_refs: tuple[NonEmptyStr, ...] = ()
    decision_reason_codes: tuple[NonEmptyStr, ...]
    created_at_epoch: float
    decision_hash: Sha256Hex

    @model_validator(mode="after")
    def _decision_integrity(self) -> "DeliveryGateDecisionSchema":
        if self.overall_quality_projection.overall_quality_state is not self.overall_quality_state:
            raise ValueError("Gate decision/overall projection state mismatch")
        if self.validation_run_id != self.gate_input_manifest.validation_run_id:
            raise ValueError("Gate decision/manifest Run mismatch")
        if self.subject_key != self.gate_input_manifest.subject_key:
            raise ValueError("Gate decision/manifest subject mismatch")
        if (
            self.quality_policy_projection.projection_hash
            != self.gate_input_manifest.quality_policy_projection_hash
        ):
            raise ValueError("Gate decision policy projection/manifest mismatch")
        if (
            self.currentness_proof.proof_sha256
            != self.gate_input_manifest.currentness_proof_sha256
        ):
            raise ValueError("Gate decision CurrentnessProof/manifest mismatch")
        for name in (
            "blocking_fact_refs",
            "warning_fact_refs",
            "informational_fact_refs",
        ):
            ids = [item.fact_id for item in getattr(self, name)]
            if ids != sorted(ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{name} must be canonical ordered-unique")
        reasons = tuple(self.unresolved_reason_refs)
        if reasons != tuple(sorted(set(reasons))):
            raise ValueError("decision unresolved reasons must be ordered-unique")
        decision_reasons = tuple(self.decision_reason_codes)
        if decision_reasons != tuple(sorted(set(decision_reasons))):
            raise ValueError("decision reason codes must be ordered-unique")
        payload = self.model_dump(mode="json")
        payload.pop("decision_hash", None)
        payload.pop("created_at_epoch", None)
        if canonical_sha256(payload) != self.decision_hash:
            raise ValueError("DeliveryGateDecision hash mismatch")
        expected_id = "delivery-gate://sha256/" + canonical_sha256(
            {
                "gate_contract_version": self.gate_contract_version,
                "gate_input_manifest_hash": self.gate_input_manifest.input_manifest_hash,
            }
        )
        if self.delivery_gate_decision_id != expected_id:
            raise ValueError("DeliveryGateDecision id mismatch")
        return self

    def as_ref(self) -> DeliveryGateDecisionRefSchema:
        return DeliveryGateDecisionRefSchema(
            delivery_gate_decision_id=self.delivery_gate_decision_id,
            schema_version=self.schema_version,
            gate_contract_version=self.gate_contract_version,
            decision_hash=self.decision_hash,
        )


__all__ = [
    "DeliveryGateDecisionSchema",
    "DimensionQualityState",
    "DimensionResultProjectionSchema",
    "FactPolicyEffectSchema",
    "FactPolicyRuleSchema",
    "GateBlockingConditionKind",
    "GateBlockingConditionSchema",
    "GateInputManifestSchema",
    "OverallQualityProjectionSchema",
    "QualityDeliveryContractSchema",
    "QualityPolicyEffect",
    "QualityPolicyProjectionSchema",
    "RequiredConditionState",
    "RequiredDimensionConditionResultSchema",
    "RequiredDimensionConditionSchema",
]
