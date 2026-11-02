"""Target Post-write deterministic Quality / Delivery Gate service."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Iterable

from apps.enterprise_document.schemas.post_write.currentness import (
    CurrentnessProofSchema,
    CurrentnessState,
)
from apps.enterprise_document.schemas.post_write.entry import (
    PostWriteEntrySchema,
    entry_ref_from_identity,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    DeliveryGateDecisionRefSchema,
    OverallQualityState,
    QualityDeliveryContractRefSchema,
    ValidationCompletionItemState,
    ValidationCompletionRefSchema,
    ValidationCompletionSchema,
    ValidationFactRefSchema,
    ValidationFactSchema,
    ValidationRunSchema,
    ValidationRunState,
)
from apps.enterprise_document.schemas.post_write.gate import (
    DeliveryGateDecisionSchema,
    DimensionQualityState,
    DimensionResultProjectionSchema,
    GateInputManifestSchema,
    OverallQualityProjectionSchema,
    QualityDeliveryContractSchema,
    QualityPolicyEffect,
    QualityPolicyProjectionSchema,
    RequiredConditionState,
)
from core.hashing import canonical_sha256

from .persistence import (
    GateDecisionStore,
    PostWriteEntryStore,
    PostWritePersistenceIntegrityError,
    QualityDeliveryContractStore,
    ValidationStore,
)
from .policy import QualityPolicyProjectionError, QualityPolicyProjector


class GateInputIntegrityError(PostWritePersistenceIntegrityError):
    """Gate input cannot be trusted; no DeliveryGateDecision may be created."""


@dataclass(frozen=True)
class ResolvedGateInput:
    validation_run: ValidationRunSchema
    post_write_entry: PostWriteEntrySchema
    facts: tuple[ValidationFactSchema, ...]
    completion: ValidationCompletionSchema
    contract: QualityDeliveryContractSchema
    quality_policy_projection: QualityPolicyProjectionSchema
    currentness_proof: CurrentnessProofSchema
    manifest: GateInputManifestSchema


def _fact_ref(fact: ValidationFactSchema) -> ValidationFactRefSchema:
    return ValidationFactRefSchema(
        fact_id=fact.fact_id,
        validation_run_id=fact.validation_run_id,
        subject_hash=fact.subject_hash,
        fact_contract_version=fact.contract_version,
        fact_sha256=fact.fact_sha256,
    )


def _canonical_fact_refs(
    refs: Iterable[ValidationFactRefSchema],
) -> tuple[ValidationFactRefSchema, ...]:
    by_id: dict[str, ValidationFactRefSchema] = {}
    for ref in refs:
        existing = by_id.get(ref.fact_id)
        if existing is not None and existing != ref:
            raise GateInputIntegrityError(
                f"conflicting exact Fact refs for {ref.fact_id}"
            )
        by_id[ref.fact_id] = ref
    return tuple(by_id[key] for key in sorted(by_id))


def _verify_self_hash(model, field: str, label: str) -> None:
    payload = model.model_dump(mode="json")
    expected = payload.pop(field, None)
    if expected is None or canonical_sha256(payload) != expected:
        raise GateInputIntegrityError(f"{label} canonical hash mismatch")


def _jsonable(value):
    """Convert typed Gate material to the exact JSON-mode hash preimage."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


class GateInputIntegrityGuard:
    """Resolve and verify the complete exact Gate input manifest."""

    def __init__(
        self,
        *,
        validation_store: ValidationStore,
        entry_store: PostWriteEntryStore,
        contract_store: QualityDeliveryContractStore,
        policy_projector: type[QualityPolicyProjector] = QualityPolicyProjector,
    ) -> None:
        self.validation_store = validation_store
        self.entry_store = entry_store
        self.contract_store = contract_store
        self.policy_projector = policy_projector

    def resolve(
        self,
        *,
        validation_run_id: str,
        validation_completion_ref: ValidationCompletionRefSchema,
        quality_delivery_contract_ref: QualityDeliveryContractRefSchema,
        currentness_proof: CurrentnessProofSchema,
        connection=None,
    ) -> ResolvedGateInput:
        try:
            run = self.validation_store.resolve_run_exact(validation_run_id, connection=connection)
            completion = self.validation_store.resolve_completion(
                validation_completion_ref, connection=connection
            )
            contract = self.contract_store.resolve_exact(
                quality_delivery_contract_ref, connection=connection
            )
            entry = self.entry_store.resolve_exact(
                run.subject_key.post_write_entry_ref, connection=connection
            )
            facts = self.validation_store.list_facts_for_run(validation_run_id, connection=connection)
        except GateInputIntegrityError:
            raise
        except PostWritePersistenceIntegrityError as exc:
            raise GateInputIntegrityError(str(exc)) from exc
        except Exception as exc:
            raise GateInputIntegrityError(
                "Gate exact ref resolution failed"
            ) from exc

        if run.execution_state is not ValidationRunState.COMPLETED:
            raise GateInputIntegrityError(
                "Gate requires terminal COMPLETED ValidationRun accounting"
            )
        subject_hash = run.subject_key.subject_hash
        if (
            completion.validation_run_id != run.validation_run_id
            or completion.subject_hash != subject_hash
        ):
            raise GateInputIntegrityError(
                "ValidationCompletion Run/subject mismatch"
            )
        _verify_self_hash(
            completion,
            "completion_sha256",
            "ValidationCompletion",
        )

        entry_ref = entry_ref_from_identity(entry.identity)
        if entry_ref != run.subject_key.post_write_entry_ref:
            raise GateInputIntegrityError(
                "ValidationRun subject Entry ref does not resolve exact Entry"
            )
        if (
            entry.identity.quality_delivery_contract_ref
            != quality_delivery_contract_ref
        ):
            raise GateInputIntegrityError(
                "historical PostWriteEntry binds another QualityDeliveryContract"
            )

        contract_payload = contract.model_dump(mode="json")
        contract_hash = contract_payload.pop("contract_hash", None)
        if contract_hash is None or canonical_sha256(contract_payload) != contract_hash:
            raise GateInputIntegrityError(
                "QualityDeliveryContract canonical hash mismatch"
            )
        if contract.as_ref() != quality_delivery_contract_ref:
            raise GateInputIntegrityError(
                "QualityDeliveryContract exact ref mismatch"
            )

        facts_by_id = {item.fact_id: item for item in facts}
        if len(facts_by_id) != len(facts):
            raise GateInputIntegrityError(
                "ValidationStore returned duplicate Fact identities"
            )
        for fact in facts:
            _verify_self_hash(fact, "fact_sha256", "ValidationFact")
            if (
                fact.validation_run_id != run.validation_run_id
                or fact.subject_hash != subject_hash
            ):
                raise GateInputIntegrityError(
                    "ValidationFact Run/subject mismatch"
                )

        # Completion may reference only exact Facts from this full canonical run
        # manifest. Gate never accepts caller-supplied subset selection.
        for item in completion.items:
            for ref in item.fact_refs:
                fact = facts_by_id.get(ref.fact_id)
                if fact is None or _fact_ref(fact) != ref:
                    raise GateInputIntegrityError(
                        "ValidationCompletion references missing/conflicting Fact"
                    )
            # Option-A procedural PASS evidence is still only a result carrier.
            # Gate resolves every immutable proof ref and never trusts a string.
            for proof_ref in (*item.positive_proof_refs, *item.indeterminate_proof_refs):
                if proof_ref.validation_run_id != run.validation_run_id:
                    raise GateInputIntegrityError(
                        "ValidationCompletion proof belongs to another Run"
                    )
                try:
                    self.validation_store.resolve_intermediate(proof_ref, connection=connection)
                except PostWritePersistenceIntegrityError as exc:
                    raise GateInputIntegrityError(
                        "ValidationCompletion proof cannot resolve exact intermediate"
                    ) from exc

        proof = CurrentnessProofSchema.model_validate(currentness_proof)
        _verify_self_hash(proof, "proof_sha256", "CurrentnessProof")
        if proof.historical_entry_ref != entry_ref:
            raise GateInputIntegrityError(
                "CurrentnessProof binds another historical PostWriteEntry"
            )
        if proof.active_projection.run_id != entry.identity.run_id:
            raise GateInputIntegrityError(
                "CurrentnessProof active projection belongs to another Run"
            )

        try:
            projection = self.policy_projector.project(
                validation_run_id=run.validation_run_id,
                subject_hash=subject_hash,
                contract=contract,
                facts=facts,
                completion=completion,
            )
        except QualityPolicyProjectionError as exc:
            raise GateInputIntegrityError(str(exc)) from exc

        fact_refs = tuple(_fact_ref(item) for item in facts)
        fact_manifest_hash = canonical_sha256(
            [item.model_dump(mode="json") for item in fact_refs]
        )
        if (
            projection.consumed_fact_refs != fact_refs
            or projection.consumed_fact_manifest_hash != fact_manifest_hash
        ):
            raise GateInputIntegrityError(
                "QualityPolicyProjection does not consume exact full Fact manifest"
            )

        identity = entry.identity
        manifest_kwargs = {
            "validation_run_id": run.validation_run_id,
            "subject_key": run.subject_key,
            "subject_hash": subject_hash,
            "post_write_entry_ref": entry_ref,
            "artifact_state_ref": identity.artifact_state_ref,
            "evidence_binding": identity.evidence_binding,
            "requirement_ref": identity.requirement_ref,
            "quality_delivery_contract_ref": quality_delivery_contract_ref,
            "validation_fact_refs": fact_refs,
            "validation_fact_manifest_hash": fact_manifest_hash,
            "validation_completion_ref": validation_completion_ref,
            "validation_completion_hash": completion.completion_sha256,
            "quality_policy_projection_id": projection.projection_id,
            "quality_policy_projection_hash": projection.projection_hash,
            "currentness_proof_sha256": proof.proof_sha256,
            "gate_contract_version": contract.gate_contract_version,
        }
        manifest_preimage = {
            "schema_version": "gate_input_manifest_v1",
            **{
                key: _jsonable(value)
                for key, value in manifest_kwargs.items()
            },
        }
        manifest = GateInputManifestSchema(
            **manifest_kwargs,
            input_manifest_hash=canonical_sha256(manifest_preimage),
        )
        return ResolvedGateInput(
            validation_run=run,
            post_write_entry=entry,
            facts=facts,
            completion=completion,
            contract=contract,
            quality_policy_projection=projection,
            currentness_proof=proof,
            manifest=manifest,
        )


class DeliveryGateService:
    """Read-only semantic Gate + one immutable DeliveryGateDecision write."""

    def __init__(
        self,
        *,
        input_guard: GateInputIntegrityGuard,
        decision_store: GateDecisionStore,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.input_guard = input_guard
        self.decision_store = decision_store
        self.clock = clock or time.time

    @staticmethod
    def _dimension_projections(
        resolved: ResolvedGateInput,
    ) -> tuple[DimensionResultProjectionSchema, ...]:
        projection = resolved.quality_policy_projection
        effect_by_fact = {
            item.fact_ref.fact_id: item for item in projection.fact_policy_effects
        }
        results_by_dimension: dict[str, list] = {}
        for result in projection.condition_results:
            results_by_dimension.setdefault(result.dimension_id, []).append(result)

        dimensions: list[DimensionResultProjectionSchema] = []
        for condition in projection.required_dimension_conditions:
            results_by_dimension.setdefault(condition.dimension_id, [])

        for dimension_id in sorted(results_by_dimension):
            condition_results = tuple(
                sorted(
                    results_by_dimension[dimension_id],
                    key=lambda item: item.condition_ref,
                )
            )
            condition_refs = tuple(
                sorted(item.condition_ref for item in condition_results)
            )
            consumed = _canonical_fact_refs(
                ref
                for result in condition_results
                for ref in result.consumed_fact_refs
            )
            blocking = _canonical_fact_refs(
                ref
                for ref in consumed
                if (
                    effect_by_fact.get(ref.fact_id) is not None
                    and effect_by_fact[ref.fact_id].effect
                    is QualityPolicyEffect.BLOCKING
                )
            )
            warning = _canonical_fact_refs(
                ref
                for ref in consumed
                if (
                    effect_by_fact.get(ref.fact_id) is not None
                    and effect_by_fact[ref.fact_id].effect
                    is QualityPolicyEffect.WARNING
                )
            )
            informational = _canonical_fact_refs(
                ref
                for ref in consumed
                if (
                    effect_by_fact.get(ref.fact_id) is not None
                    and effect_by_fact[ref.fact_id].effect
                    is QualityPolicyEffect.INFORMATIONAL
                )
            )
            unresolved_reasons = tuple(
                sorted(
                    {
                        reason
                        for result in condition_results
                        for reason in result.unresolved_reason_refs
                    }
                )
            )
            required_results = tuple(
                result
                for result in condition_results
                if next(
                    (
                        condition
                        for condition in projection.required_dimension_conditions
                        if condition.effective_condition_ref()
                        == result.condition_ref
                    ),
                    None,
                )
                is not None
                and next(
                    condition
                    for condition in projection.required_dimension_conditions
                    if condition.effective_condition_ref()
                    == result.condition_ref
                ).required
            )
            if not required_results:
                state = DimensionQualityState.NOT_APPLICABLE
            elif blocking or any(
                item.state is RequiredConditionState.VIOLATED
                for item in required_results
            ):
                state = DimensionQualityState.FAIL
            elif any(
                item.state is RequiredConditionState.UNRESOLVED
                for item in required_results
            ):
                state = DimensionQualityState.UNRESOLVED
            elif all(
                item.state is RequiredConditionState.SATISFIED
                for item in required_results
            ):
                state = DimensionQualityState.PASS
            else:
                state = DimensionQualityState.UNRESOLVED

            kwargs = {
                "dimension_id": dimension_id,
                "state": state,
                "consumed_fact_refs": consumed,
                "blocking_fact_refs": blocking,
                "warning_fact_refs": warning,
                "informational_fact_refs": informational,
                "unresolved_reason_refs": unresolved_reasons,
                "required_condition_refs": condition_refs,
                "condition_results": condition_results,
            }
            preimage = {
                "schema_version": "dimension_result_projection_v1",
                **{
                    key: _jsonable(value)
                    for key, value in kwargs.items()
                },
            }
            dimensions.append(
                DimensionResultProjectionSchema(
                    **kwargs,
                    projection_hash=canonical_sha256(preimage),
                )
            )
        return tuple(dimensions)

    @staticmethod
    def _completion_unresolved_reasons(
        completion: ValidationCompletionSchema,
    ) -> tuple[str, ...]:
        unresolved_states = {
            ValidationCompletionItemState.NOT_RUN,
            ValidationCompletionItemState.TECHNICAL_FAILURE,
            ValidationCompletionItemState.INDETERMINATE,
        }
        return tuple(
            sorted(
                f"COMPLETION_{item.state.value}:{item.expected_validation_item_ref}"
                for item in completion.items
                if item.required and item.state in unresolved_states
            )
        )

    @classmethod
    def _overall_projection(
        cls,
        resolved: ResolvedGateInput,
        dimensions: tuple[DimensionResultProjectionSchema, ...],
    ) -> OverallQualityProjectionSchema:
        policy = resolved.quality_policy_projection
        completion_reasons = cls._completion_unresolved_reasons(
            resolved.completion
        )
        condition_reasons = tuple(
            sorted(
                {
                    reason
                    for item in policy.condition_results
                    if item.state is RequiredConditionState.UNRESOLVED
                    for reason in item.unresolved_reason_refs
                }
            )
        )
        unresolved_reasons = set(completion_reasons) | set(condition_reasons)

        if (
            resolved.currentness_proof.currentness_state
            is not CurrentnessState.CURRENT
        ):
            state = OverallQualityState.UNRESOLVED
            unresolved_reasons.add(
                "CURRENTNESS_" + resolved.currentness_proof.currentness_state.value
            )
        elif policy.blocking_conditions:
            state = OverallQualityState.FAIL
        elif completion_reasons:
            state = OverallQualityState.UNRESOLVED
        elif any(
            item.state is RequiredConditionState.UNRESOLVED
            for item in policy.condition_results
        ):
            state = OverallQualityState.UNRESOLVED
        elif all(
            item.state in {
                DimensionQualityState.PASS,
                DimensionQualityState.NOT_APPLICABLE,
            }
            for item in dimensions
        ):
            state = OverallQualityState.PASS
        else:
            state = OverallQualityState.UNRESOLVED

        kwargs = {
            "overall_quality_state": state,
            "dimension_projection_hashes": tuple(
                sorted(item.projection_hash for item in dimensions)
            ),
            "blocking_fact_refs": policy.blocking_fact_refs,
            "warning_fact_refs": policy.warning_fact_refs,
            "informational_fact_refs": policy.informational_fact_refs,
            "unresolved_reason_refs": tuple(sorted(unresolved_reasons)),
        }
        preimage = {
            "schema_version": "overall_quality_projection_v1",
            **{
                key: _jsonable(value)
                for key, value in kwargs.items()
            },
        }
        return OverallQualityProjectionSchema(
            **kwargs,
            projection_hash=canonical_sha256(preimage),
        )

    def evaluate(
        self,
        *,
        validation_run_id: str,
        validation_completion_ref: ValidationCompletionRefSchema,
        quality_delivery_contract_ref: QualityDeliveryContractRefSchema,
        currentness_proof: CurrentnessProofSchema,
    ) -> DeliveryGateDecisionSchema:
        resolved = self.input_guard.resolve(
            validation_run_id=validation_run_id,
            validation_completion_ref=validation_completion_ref,
            quality_delivery_contract_ref=quality_delivery_contract_ref,
            currentness_proof=currentness_proof,
        )
        dimensions = self._dimension_projections(resolved)
        overall = self._overall_projection(resolved, dimensions)

        unresolved_reasons = overall.unresolved_reason_refs
        decision_reason_codes: set[str] = {
            "QUALITY_STATE_" + overall.overall_quality_state.value,
        }
        if resolved.quality_policy_projection.blocking_conditions:
            decision_reason_codes.add("CONTRACT_BLOCKING_CONDITION_PRESENT")
        if unresolved_reasons:
            decision_reason_codes.add("UNRESOLVED_DIAGNOSTICS_PRESENT")

        decision_id = "delivery-gate://sha256/" + canonical_sha256(
            {
                "gate_contract_version": resolved.contract.gate_contract_version,
                "gate_input_manifest_hash": resolved.manifest.input_manifest_hash,
            }
        )
        kwargs = {
            "delivery_gate_decision_id": decision_id,
            "gate_contract_version": resolved.contract.gate_contract_version,
            "validation_run_id": resolved.validation_run.validation_run_id,
            "subject_key": resolved.validation_run.subject_key,
            "gate_input_manifest": resolved.manifest,
            "quality_policy_projection": resolved.quality_policy_projection,
            "currentness_proof": resolved.currentness_proof,
            "dimension_result_projections": dimensions,
            "overall_quality_projection": overall,
            "overall_quality_state": overall.overall_quality_state,
            "blocking_fact_refs": (
                resolved.quality_policy_projection.blocking_fact_refs
            ),
            "warning_fact_refs": (
                resolved.quality_policy_projection.warning_fact_refs
            ),
            "informational_fact_refs": (
                resolved.quality_policy_projection.informational_fact_refs
            ),
            "unresolved_reason_refs": unresolved_reasons,
            "decision_reason_codes": tuple(sorted(decision_reason_codes)),
        }
        preimage = {
            "schema_version": "delivery_gate_decision_v1",
            **{
                key: _jsonable(value)
                for key, value in kwargs.items()
            },
        }
        decision = DeliveryGateDecisionSchema(
            **kwargs,
            created_at_epoch=float(self.clock()),
            decision_hash=canonical_sha256(preimage),
        )
        ref = self.decision_store.put_if_absent_or_same(decision)
        return self.decision_store.resolve_exact(ref)

    def resolve_exact(
        self,
        ref: DeliveryGateDecisionRefSchema,
    ) -> DeliveryGateDecisionSchema:
        return self.decision_store.resolve_exact(ref)


__all__ = [
    "DeliveryGateService",
    "GateInputIntegrityError",
    "GateInputIntegrityGuard",
    "ResolvedGateInput",
]
