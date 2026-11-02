"""Versioned QualityDeliveryContract registry/resolver and deterministic policy projection."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from apps.enterprise_document.schemas.post_write.foundation import (
    CorrectionPolicyRefSchema,
    QualityDeliveryContractRefSchema,
    ValidationCompletionItemState,
    ValidationCompletionSchema,
    ValidationFactFamily,
    ValidationFactRefSchema,
    ValidationFactSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.control import (
    CorrectionPolicyRuleSchema,
    CorrectionPolicySchema,
)
from apps.enterprise_document.schemas.post_write.gate import (
    FactPolicyEffectSchema,
    FactPolicyRuleSchema,
    GateBlockingConditionKind,
    GateBlockingConditionSchema,
    QualityDeliveryContractSchema,
    QualityPolicyEffect,
    QualityPolicyProjectionSchema,
    RequiredConditionState,
    RequiredDimensionConditionResultSchema,
    RequiredDimensionConditionSchema,
)
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.consistency_procedure import ConsistencyProcedureInputSchema

from .persistence import CorrectionPolicyStore, QualityDeliveryContractStore


class QualityPolicyProjectionError(ValueError):
    """Contract/fact input cannot produce one deterministic policy projection."""


_DIMENSION_FAMILY_DEFAULTS: dict[str, tuple[ValidationFactFamily, ...]] = {
    "faithfulness": (ValidationFactFamily.FAITHFULNESS,),
    "relevancy": (ValidationFactFamily.REQUIREMENT_ADHERENCE,),
    "requirement_adherence": (ValidationFactFamily.REQUIREMENT_ADHERENCE,),
    "citation": (
        ValidationFactFamily.CITATION_INTEGRITY,
        ValidationFactFamily.CITATION_GROUNDING,
    ),
    "coverage": (ValidationFactFamily.COVERAGE_COMPLETENESS,),
    "coverage_completeness": (ValidationFactFamily.COVERAGE_COMPLETENESS,),
    "consistency": (ValidationFactFamily.ARTIFACT_CONSISTENCY,),
    "artifact_consistency": (ValidationFactFamily.ARTIFACT_CONSISTENCY,),
}


def _fact_ref(fact: ValidationFactSchema) -> ValidationFactRefSchema:
    return ValidationFactRefSchema(
        fact_id=fact.fact_id,
        validation_run_id=fact.validation_run_id,
        subject_hash=fact.subject_hash,
        fact_contract_version=fact.contract_version,
        fact_sha256=fact.fact_sha256,
    )


def _validate_fact_integrity(fact: ValidationFactSchema) -> None:
    payload = fact.model_dump(mode="json")
    payload.pop("fact_sha256", None)
    if canonical_sha256(payload) != fact.fact_sha256:
        raise QualityPolicyProjectionError(
            f"ValidationFact canonical hash mismatch: {fact.fact_id}"
        )


def _mapped_condition_state(fact: ValidationFactSchema) -> str:
    if fact.semantic_state is ValidationSemanticState.PASS:
        return "PASS"
    if fact.semantic_state is ValidationSemanticState.FAIL:
        return "FAIL"
    return "UNRESOLVED"


class QualityDeliveryContractRegistry:
    def __init__(self, store: QualityDeliveryContractStore) -> None:
        self.store = store

    def register(
        self,
        *,
        contract_id: str,
        contract_version: str,
        required_dimensions: tuple[RequiredDimensionConditionSchema, ...],
        fact_policy_rules: tuple[FactPolicyRuleSchema, ...] = (),
        unmatched_fact_effect: QualityPolicyEffect = QualityPolicyEffect.INFORMATIONAL,
        gate_contract_version: str = "delivery_gate_v1",
        policy_projection_contract_version: str = "quality_policy_projection_v1",
        contract_metadata: dict | None = None,
        consistency_procedure_input: ConsistencyProcedureInputSchema | None = None,
    ) -> QualityDeliveryContractRefSchema:
        ordered_dimensions = tuple(
            sorted(
                required_dimensions,
                key=lambda item: (
                    item.dimension_id,
                    item.effective_condition_ref(),
                ),
            )
        )
        ordered_rules = tuple(
            sorted(fact_policy_rules, key=lambda item: item.policy_rule_ref)
        )
        preimage = {
            "schema_version": "quality_delivery_contract_v1",
            "contract_id": contract_id,
            "contract_version": contract_version,
            "required_dimensions": [
                item.model_dump(mode="json") for item in ordered_dimensions
            ],
            "fact_policy_rules": [
                item.model_dump(mode="json") for item in ordered_rules
            ],
            "unmatched_fact_effect": unmatched_fact_effect.value,
            "gate_contract_version": gate_contract_version,
            "policy_projection_contract_version": (
                policy_projection_contract_version
            ),
            "contract_metadata": dict(contract_metadata or {}),
            **({"consistency_procedure_input": ConsistencyProcedureInputSchema.model_validate(consistency_procedure_input).model_dump(mode="json")}
               if consistency_procedure_input is not None else {}),
        }
        contract = QualityDeliveryContractSchema(
            contract_id=contract_id,
            contract_version=contract_version,
            required_dimensions=ordered_dimensions,
            fact_policy_rules=ordered_rules,
            unmatched_fact_effect=unmatched_fact_effect,
            gate_contract_version=gate_contract_version,
            policy_projection_contract_version=(
                policy_projection_contract_version
            ),
            contract_metadata=dict(contract_metadata or {}),
            consistency_procedure_input=consistency_procedure_input,
            contract_hash=canonical_sha256(preimage),
        )
        return self.store.put_if_absent_or_same(contract)


class QualityDeliveryContractResolver:
    def __init__(self, store: QualityDeliveryContractStore) -> None:
        self.store = store

    def resolve(
        self,
        ref: QualityDeliveryContractRefSchema,
    ) -> QualityDeliveryContractSchema:
        return self.store.resolve_exact(ref)


class CorrectionPolicyRegistry:
    """Register immutable historical correction policy; never own semantic Budget."""

    def __init__(self, store: CorrectionPolicyStore) -> None:
        self.store = store

    def register(
        self,
        *,
        policy_id: str,
        policy_version: str,
        rules: tuple[CorrectionPolicyRuleSchema, ...] = (),
        automatic_correction_enabled: bool = True,
        technical_retry_limit: int = 1,
    ) -> CorrectionPolicyRefSchema:
        ordered_rules = tuple(sorted(rules, key=lambda item: item.policy_rule_ref))
        preimage = {
            "schema_version": "correction_policy_v1",
            "policy_id": policy_id,
            "policy_version": policy_version,
            "automatic_correction_enabled": automatic_correction_enabled,
            "rules": [item.model_dump(mode="json") for item in ordered_rules],
            "technical_retry_limit": technical_retry_limit,
            "repair_budget_dimension": "repair_attempts",
        }
        policy = CorrectionPolicySchema(
            policy_id=policy_id,
            policy_version=policy_version,
            automatic_correction_enabled=automatic_correction_enabled,
            rules=ordered_rules,
            technical_retry_limit=technical_retry_limit,
            policy_hash=canonical_sha256(preimage),
        )
        return self.store.put_if_absent_or_same(policy)


class CorrectionPolicyResolver:
    def __init__(self, store: CorrectionPolicyStore) -> None:
        self.store = store

    def resolve(
        self,
        ref: CorrectionPolicyRefSchema,
    ) -> CorrectionPolicySchema:
        return self.store.resolve_exact(ref)


class QualityPolicyProjector:
    """Pure deterministic Contract policy projection over canonical Facts."""

    @staticmethod
    def _matching_rules(
        contract: QualityDeliveryContractSchema,
        fact: ValidationFactSchema,
    ) -> tuple[FactPolicyRuleSchema, ...]:
        return tuple(
            rule
            for rule in contract.fact_policy_rules
            if rule.fact_family is fact.family
            and fact.semantic_state in rule.semantic_states
            and (
                not rule.target_refs
                or fact.target_ref in rule.target_refs
            )
        )

    @staticmethod
    def _condition_families(
        condition: RequiredDimensionConditionSchema,
    ) -> tuple[ValidationFactFamily, ...]:
        if condition.fact_families:
            return condition.fact_families
        return _DIMENSION_FAMILY_DEFAULTS.get(
            condition.dimension_id.strip().lower(),
            (),
        )

    @classmethod
    def _evaluate_condition(
        cls,
        condition: RequiredDimensionConditionSchema,
        facts: tuple[ValidationFactSchema, ...],
        completion: ValidationCompletionSchema | None = None,
    ) -> RequiredDimensionConditionResultSchema:
        condition_ref = condition.effective_condition_ref()
        if not condition.required:
            preimage = {
                "schema_version": "required_dimension_condition_result_v1",
                "condition_ref": condition_ref,
                "dimension_id": condition.dimension_id,
                "state": RequiredConditionState.NOT_APPLICABLE.value,
                "consumed_fact_refs": [],
                "unresolved_reason_refs": [],
            }
            return RequiredDimensionConditionResultSchema(
                condition_ref=condition_ref,
                dimension_id=condition.dimension_id,
                state=RequiredConditionState.NOT_APPLICABLE,
                consumed_fact_refs=(),
                unresolved_reason_refs=(),
                result_hash=canonical_sha256(preimage),
            )

        families = cls._condition_families(condition)
        relevant = tuple(
            fact
            for fact in facts
            if (
                (not families or fact.family in families)
                and (
                    not condition.target_refs
                    or fact.target_ref in condition.target_refs
                )
            )
        )
        relevant = tuple(sorted(relevant, key=lambda item: item.fact_id))
        refs = tuple(_fact_ref(item) for item in relevant)

        positive_proofs = ()
        indeterminate_proofs = ()
        aliases = {
            "requirement_adherence": "relevancy",
            "coverage_completeness": "coverage",
            "artifact_consistency": "consistency",
        }
        dimension = aliases.get(condition.dimension_id.strip().lower(), condition.dimension_id.strip().lower())
        completion_matches = tuple(
            item for item in completion.items
            if item.expected_validation_item_ref.startswith(f"{dimension}:item:")
        ) if completion is not None else ()
        if len(completion_matches) > 1:
            raise QualityPolicyProjectionError(f"ambiguous Completion item for required dimension {dimension}")
        proof_item = completion_matches[0] if completion_matches else None
        if proof_item is not None and proof_item.indeterminate_proof_refs:
            state = RequiredConditionState.UNRESOLVED
            reasons = (f"SEMANTIC_INDETERMINATE:{condition_ref}",)
            indeterminate_proofs = proof_item.indeterminate_proof_refs
        elif not relevant:
            if (
                proof_item is not None
                and proof_item.state is ValidationCompletionItemState.PASS
                and proof_item.positive_proof_refs
            ):
                state = RequiredConditionState.SATISFIED
                reasons = ()
                positive_proofs = proof_item.positive_proof_refs
            else:
                state = RequiredConditionState.UNRESOLVED
                reasons = (f"NO_TRUSTED_FACTS:{condition_ref}",)
        else:
            mapped = tuple(_mapped_condition_state(item) for item in relevant)
            determinate_violations = tuple(
                item
                for item in mapped
                if item != "UNRESOLVED"
                and item not in condition.accepted_states
            )
            unresolved = tuple(
                item
                for item in mapped
                if item == "UNRESOLVED"
                and item not in condition.accepted_states
            )
            if determinate_violations:
                state = RequiredConditionState.VIOLATED
                reasons = ()
            elif unresolved:
                state = RequiredConditionState.UNRESOLVED
                reasons = (f"SEMANTIC_INDETERMINATE:{condition_ref}",)
            else:
                state = RequiredConditionState.SATISFIED
                reasons = ()

        preimage = {
            "schema_version": "required_dimension_condition_result_v1",
            "condition_ref": condition_ref,
            "dimension_id": condition.dimension_id,
            "state": state.value,
            "consumed_fact_refs": [
                item.model_dump(mode="json") for item in refs
            ],
            **(
                {
                    "positive_proof_refs": [
                        item.model_dump(mode="json")
                        for item in positive_proofs
                    ]
                }
                if positive_proofs
                else {}
            ),
            **({"indeterminate_proof_refs": [item.model_dump(mode="json") for item in indeterminate_proofs]}
               if indeterminate_proofs else {}),
            "unresolved_reason_refs": list(reasons),
        }
        return RequiredDimensionConditionResultSchema(
            condition_ref=condition_ref,
            dimension_id=condition.dimension_id,
            state=state,
            consumed_fact_refs=refs,
            positive_proof_refs=positive_proofs,
            indeterminate_proof_refs=indeterminate_proofs,
            unresolved_reason_refs=reasons,
            result_hash=canonical_sha256(preimage),
        )

    @classmethod
    def project(
        cls,
        *,
        validation_run_id: str,
        subject_hash: str,
        contract: QualityDeliveryContractSchema,
        facts: Iterable[ValidationFactSchema],
        completion: ValidationCompletionSchema | None = None,
    ) -> QualityPolicyProjectionSchema:
        contract = QualityDeliveryContractSchema.model_validate(contract)
        contract_payload = contract.model_dump(mode="json")
        contract_payload.pop("contract_hash", None)
        if canonical_sha256(contract_payload) != contract.contract_hash:
            raise QualityPolicyProjectionError(
                "QualityDeliveryContract canonical hash mismatch"
            )

        items = tuple(
            sorted(
                (
                    ValidationFactSchema.model_validate(item)
                    for item in facts
                ),
                key=lambda item: item.fact_id,
            )
        )
        ids = [item.fact_id for item in items]
        if len(ids) != len(set(ids)):
            raise QualityPolicyProjectionError(
                "duplicate ValidationFact identity in policy projection input"
            )
        for fact in items:
            _validate_fact_integrity(fact)
            if (
                fact.validation_run_id != validation_run_id
                or fact.subject_hash != subject_hash
            ):
                raise QualityPolicyProjectionError(
                    "policy projection Fact run/subject mismatch"
                )

        refs = tuple(_fact_ref(item) for item in items)
        manifest_hash = canonical_sha256(
            [item.model_dump(mode="json") for item in refs]
        )

        effects: list[FactPolicyEffectSchema] = []
        blocking_conditions: list[GateBlockingConditionSchema] = []
        buckets: dict[QualityPolicyEffect, list[ValidationFactRefSchema]] = defaultdict(list)

        for fact, ref in zip(items, refs):
            matches = cls._matching_rules(contract, fact)
            if len(matches) > 1:
                signatures = {
                    (
                        item.effect.value,
                        item.reason_code,
                    )
                    for item in matches
                }
                if len(signatures) > 1:
                    raise QualityPolicyProjectionError(
                        f"ambiguous policy rules for Fact {fact.fact_id}"
                    )
                rule = sorted(matches, key=lambda item: item.policy_rule_ref)[0]
            elif matches:
                rule = matches[0]
            else:
                rule = None

            if rule is None:
                effect = contract.unmatched_fact_effect
                policy_rule_ref = "contract-default-unmatched-fact"
                reason_code = f"UNMATCHED_FACT_{effect.value}"
            else:
                effect = rule.effect
                policy_rule_ref = rule.policy_rule_ref
                reason_code = rule.reason_code

            effects.append(
                FactPolicyEffectSchema(
                    fact_ref=ref,
                    effect=effect,
                    policy_rule_ref=policy_rule_ref,
                    reason_code=reason_code,
                )
            )
            buckets[effect].append(ref)
            if effect is QualityPolicyEffect.BLOCKING:
                blocking_conditions.append(
                    GateBlockingConditionSchema(
                        blocking_kind=GateBlockingConditionKind.FACT_POLICY_BLOCK,
                        condition_ref=(
                            f"fact-block://{fact.fact_id}/{policy_rule_ref}"
                        ),
                        reason_code=reason_code,
                        fact_ref=ref,
                        policy_rule_ref=policy_rule_ref,
                    )
                )

        if completion is not None and (
            completion.validation_run_id != validation_run_id
            or completion.subject_hash != subject_hash
        ):
            raise QualityPolicyProjectionError(
                "policy projection Completion run/subject mismatch"
            )
        condition_results = tuple(
            sorted(
                (
                    cls._evaluate_condition(condition, items, completion)
                    for condition in contract.required_dimensions
                ),
                key=lambda item: item.condition_ref,
            )
        )
        for result in condition_results:
            if result.state is RequiredConditionState.VIOLATED:
                blocking_conditions.append(
                    GateBlockingConditionSchema(
                        blocking_kind=(
                            GateBlockingConditionKind.REQUIRED_DIMENSION_CONDITION_VIOLATION
                        ),
                        condition_ref=result.condition_ref,
                        reason_code=(
                            "REQUIRED_DIMENSION_CONDITION_VIOLATED"
                        ),
                    )
                )

        projection_identity = {
            "contract_ref": contract.as_ref().model_dump(mode="json"),
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "fact_manifest_hash": manifest_hash,
            "projection_contract_version": (
                contract.policy_projection_contract_version
            ),
        }
        positive_proof_refs = tuple(
            sorted(
                (
                    proof
                    for result in condition_results
                    for proof in result.positive_proof_refs
                ),
                key=lambda item: (item.validation_run_id, item.event_id),
            )
        )
        if positive_proof_refs:
            projection_identity["positive_proof_manifest_hash"] = canonical_sha256(
                [item.model_dump(mode="json") for item in positive_proof_refs]
            )
        projection_id = "quality-policy-projection://sha256/" + canonical_sha256(
            projection_identity
        )
        kwargs = {
            "projection_id": projection_id,
            "projection_contract_version": (
                contract.policy_projection_contract_version
            ),
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "quality_delivery_contract_ref": contract.as_ref(),
            "consumed_fact_refs": refs,
            "consumed_fact_manifest_hash": manifest_hash,
            "fact_policy_effects": tuple(
                sorted(effects, key=lambda item: item.fact_ref.fact_id)
            ),
            "required_dimension_conditions": tuple(
                sorted(
                    contract.required_dimensions,
                    key=lambda item: item.effective_condition_ref(),
                )
            ),
            "condition_results": condition_results,
            "blocking_conditions": tuple(
                sorted(
                    blocking_conditions,
                    key=lambda item: (
                        item.blocking_kind.value,
                        item.condition_ref,
                    ),
                )
            ),
            "blocking_fact_refs": tuple(
                sorted(
                    buckets[QualityPolicyEffect.BLOCKING],
                    key=lambda item: item.fact_id,
                )
            ),
            "warning_fact_refs": tuple(
                sorted(
                    buckets[QualityPolicyEffect.WARNING],
                    key=lambda item: item.fact_id,
                )
            ),
            "informational_fact_refs": tuple(
                sorted(
                    buckets[QualityPolicyEffect.INFORMATIONAL],
                    key=lambda item: item.fact_id,
                )
            ),
        }
        preimage = {
            "schema_version": "quality_policy_projection_v1",
            **{
                key: (
                    [
                        item.model_dump(mode="json")
                        for item in value
                    ]
                    if isinstance(value, tuple)
                    else (
                        value.model_dump(mode="json")
                        if hasattr(value, "model_dump")
                        else value
                    )
                )
                for key, value in kwargs.items()
            },
        }
        return QualityPolicyProjectionSchema(
            **kwargs,
            projection_hash=canonical_sha256(preimage),
        )


__all__ = [
    "CorrectionPolicyRegistry",
    "CorrectionPolicyResolver",
    "QualityDeliveryContractRegistry",
    "QualityDeliveryContractResolver",
    "QualityPolicyProjectionError",
    "QualityPolicyProjector",
]
