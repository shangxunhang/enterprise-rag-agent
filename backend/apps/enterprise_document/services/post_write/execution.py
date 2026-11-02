"""Thin Target Post-write Validation common execution kernel.

This module owns identity, lifecycle and technical execution mechanics only.
Validator semantic rules and Delivery Gate business policy are intentionally absent.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Iterable, Protocol

from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactStateRefSchema,
    ExpectedItemExecutionAccountingSchema,
    InputEnvelopeSchema,
    LogicalConsumerBindingSchema,
    PhysicalModelCallRefSchema,
    StageDescriptorSchema,
    StagePlanSchema,
    ValidationCompletionItemSchema,
    ValidationCompletionRefSchema,
    ValidationCompletionSchema,
    ValidationExecutionEventSchema,
    ValidationFactRefSchema,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationRunSchema,
    ValidationRunState,
    ValidationScope,
    ValidationSubjectKeySchema,
    ValidationTargetSelectorSchema,
    ValidatorExecutionRecordSchema,
)
from apps.enterprise_document.schemas.post_write.foundation import PostWriteEntryRefSchema
from core.hashing import canonical_sha256

from .artifact_state import ArtifactValidationViewResolver
from .persistence import (
    PostWritePersistenceIntegrityError,
    ValidationStore,
    _validate_stage_plan_input_conformance,
)


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str:
        ...


class Clock(Protocol):
    def now_epoch(self) -> float:
        ...


class _SystemClock:
    def now_epoch(self) -> float:
        return time.time()


class ValidationSubjectKeyBuilder:
    """Sole ValidationSubject identity producer; no durable Subject entity exists."""

    @staticmethod
    def build(
        *,
        post_write_entry_ref: PostWriteEntryRefSchema,
        validation_scope: ValidationScope,
        target_selector: ValidationTargetSelectorSchema,
        subject_contract_version: str,
    ) -> ValidationSubjectKeySchema:
        preimage = {
            "post_write_entry_ref": post_write_entry_ref.model_dump(mode="json"),
            "validation_scope": validation_scope.value,
            "target_selector": target_selector.model_dump(mode="json"),
            "subject_contract_version": str(subject_contract_version),
        }
        return ValidationSubjectKeySchema(
            post_write_entry_ref=post_write_entry_ref,
            validation_scope=validation_scope,
            target_selector=target_selector,
            subject_contract_version=subject_contract_version,
            subject_hash=canonical_sha256(preimage),
        )


class ValidationRunFactory:
    """Creates one new physical ValidationRun occurrence for one frozen subject."""

    def __init__(
        self,
        *,
        id_generator: IdGenerator,
        clock: Clock | None = None,
    ) -> None:
        self.id_generator = id_generator
        self.clock = clock or _SystemClock()

    def create(
        self,
        *,
        subject_key: ValidationSubjectKeySchema,
        creation_command_id: str,
    ) -> ValidationRunSchema:
        return ValidationRunSchema(
            validation_run_id=self.id_generator.new_id("validation_run"),
            subject_key=subject_key,
            execution_state=ValidationRunState.CREATED,
            state_version=1,
            creation_command_id=creation_command_id,
            created_at_epoch=float(self.clock.now_epoch()),
        )


class ValidationRunLifecycleService:
    def __init__(
        self,
        *,
        store: ValidationStore,
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or _SystemClock()

    def create(self, run: ValidationRunSchema) -> ValidationRunSchema:
        return self.store.create_run_with_event_if_absent_or_same(
            run,
            run.creation_command_id,
        )

    def start(
        self,
        validation_run_id: str,
        *,
        expected_state_version: int,
        transition_command_id: str,
    ) -> ValidationRunSchema:
        return self.store.transition_run(
            validation_run_id,
            expected_state_version,
            transition_command_id,
            ValidationRunState.RUNNING,
            occurred_at_epoch=float(self.clock.now_epoch()),
        )

    def complete(
        self,
        validation_run_id: str,
        *,
        expected_state_version: int,
        transition_command_id: str,
    ) -> ValidationRunSchema:
        return self.store.transition_run(
            validation_run_id,
            expected_state_version,
            transition_command_id,
            ValidationRunState.COMPLETED,
            occurred_at_epoch=float(self.clock.now_epoch()),
        )

    def technical_failure(
        self,
        validation_run_id: str,
        *,
        expected_state_version: int,
        transition_command_id: str,
        failure_ref: dict,
    ) -> ValidationRunSchema:
        return self.store.transition_run(
            validation_run_id,
            expected_state_version,
            transition_command_id,
            ValidationRunState.TECHNICAL_FAILURE,
            failure_ref,
            occurred_at_epoch=float(self.clock.now_epoch()),
        )


class ValidatorInputEnvelopeBuilder:
    """Build one exact validator input identity over committed Artifact projection."""

    def __init__(
        self,
        *,
        validation_view_resolver: ArtifactValidationViewResolver,
    ) -> None:
        self.validation_view_resolver = validation_view_resolver

    def build(
        self,
        *,
        validation_run_id: str,
        subject_hash: str,
        artifact_state_ref: ArtifactStateRefSchema,
        stage_plan: StagePlanSchema,
        applicable_expected_validation_item_refs: Iterable[str],
        exact_authority_refs: Iterable[str],
        execution_config_refs: Iterable[str] = (),
        execution_contract_version: str = "validator-execution-v1",
        connection=None,
    ) -> InputEnvelopeSchema:
        artifact_ref = ArtifactStateRefSchema.model_validate(
            artifact_state_ref
        )
        projection = (self.validation_view_resolver.resolve_projection(artifact_ref)
            if connection is None else self.validation_view_resolver.resolve_projection(artifact_ref, connection=connection))
        projection_ref = projection.as_ref()
        expected_items = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in applicable_expected_validation_item_refs
                    if str(item).strip()
                }
            )
        )
        authorities = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in exact_authority_refs
                    if str(item).strip()
                }
            )
        )
        configs = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in execution_config_refs
                    if str(item).strip()
                }
            )
        )
        contract = str(execution_contract_version or "").strip()
        if not contract:
            raise ValueError("execution_contract_version is required")
        material = {
            "validation_run_id": str(validation_run_id).strip(),
            "subject_hash": str(subject_hash).strip(),
            "artifact_state_ref": artifact_ref.model_dump(mode="json"),
            "artifact_visible_projection_ref": projection_ref.model_dump(
                mode="json"
            ),
            "artifact_visible_projection_sha256": (
                projection.projection_sha256
            ),
            "validator_id": stage_plan.validator_id,
            "validator_version": stage_plan.validator_version,
            "stage_plan_ref": stage_plan.stage_plan_ref,
            "stage_plan_version": stage_plan.stage_plan_version,
            "stage_plan_hash": stage_plan.stage_plan_hash,
            "applicable_expected_validation_item_refs": list(
                expected_items
            ),
            "exact_authority_refs": list(authorities),
            "execution_config_refs": list(configs),
            "execution_contract_version": contract,
        }
        input_hash = canonical_sha256(material)
        return InputEnvelopeSchema(
            execution_contract_version=contract,
            input_envelope_ref="validator-input://sha256/" + input_hash,
            validation_run_id=material["validation_run_id"],
            subject_hash=material["subject_hash"],
            artifact_state_ref=artifact_ref,
            artifact_visible_projection_ref=projection_ref,
            artifact_visible_projection_sha256=(
                projection.projection_sha256
            ),
            validator_id=stage_plan.validator_id,
            validator_version=stage_plan.validator_version,
            stage_plan_ref=stage_plan.stage_plan_ref,
            stage_plan_version=stage_plan.stage_plan_version,
            stage_plan_hash=stage_plan.stage_plan_hash,
            applicable_expected_validation_item_refs=expected_items,
            exact_authority_refs=authorities,
            execution_config_refs=configs,
            input_hash=input_hash,
        )


class StagePlanConformanceGuard:
    @staticmethod
    def validate(
        stage_plan: StagePlanSchema,
        input_envelope: InputEnvelopeSchema,
    ) -> None:
        _validate_stage_plan_input_conformance(stage_plan, input_envelope)


class ModelCallCompatibilityGuard:
    """Proves whether one physical call may serve multiple logical consumers."""

    @staticmethod
    def validate_share(
        physical_ref: PhysicalModelCallRefSchema,
        requested_consumers: Iterable[LogicalConsumerBindingSchema],
        *,
        request_fingerprint: str,
    ) -> None:
        requested = tuple(requested_consumers)
        if physical_ref.request_fingerprint != request_fingerprint:
            raise PostWritePersistenceIntegrityError(
                "physical model call request fingerprint mismatch"
            )
        if not requested:
            raise PostWritePersistenceIntegrityError(
                "physical model call sharing requires logical consumers"
            )
        existing = {
            (
                item.validation_run_id,
                item.validator_execution_ref,
                item.stage_ref,
                item.expected_validation_item_ref,
            )
            for item in physical_ref.logical_consumers
        }
        for item in requested:
            key = (
                item.validation_run_id,
                item.validator_execution_ref,
                item.stage_ref,
                item.expected_validation_item_ref,
            )
            if key not in existing:
                raise PostWritePersistenceIntegrityError(
                    "physical model call is not bound to requested logical consumer"
                )


@dataclass(frozen=True)
class PhysicalModelDispatchResult:
    physical_ref: PhysicalModelCallRefSchema
    response_payload: dict


class PhysicalModelCallDispatcher:
    """Technical dispatcher; sharing is allowed only after exact compatibility proof."""

    def __init__(
        self,
        *,
        dispatch_fn: Callable[[dict], tuple[PhysicalModelCallRefSchema, dict]],
    ) -> None:
        self.dispatch_fn = dispatch_fn

    def dispatch(self, request_payload: dict) -> PhysicalModelDispatchResult:
        physical_ref, response = self.dispatch_fn(dict(request_payload))
        return PhysicalModelDispatchResult(
            physical_ref=PhysicalModelCallRefSchema.model_validate(physical_ref),
            response_payload=dict(response),
        )


class ValidationCompletionBuilder:
    """Builds exact per-item completion accounting; does not evaluate Gate policy."""

    @staticmethod
    def build(
        *,
        validation_run_id: str,
        subject_hash: str,
        items: tuple[ValidationCompletionItemSchema, ...],
    ) -> ValidationCompletionSchema:
        ordered = tuple(
            sorted(items, key=lambda item: item.expected_validation_item_ref)
        )
        ids = [item.expected_validation_item_ref for item in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("Completion expected item refs must be unique")
        preimage = {
            "schema_version": "validation_completion_v1",
            "contract_version": "validation_completion_accounting_v1",
            "validation_run_id": validation_run_id,
            "subject_hash": subject_hash,
            "items": [item.model_dump(mode="json") for item in ordered],
        }
        completion_id = "validation-completion:" + canonical_sha256(preimage)
        final_preimage = {
            **preimage,
            "completion_id": completion_id,
        }
        return ValidationCompletionSchema(
            completion_id=completion_id,
            validation_run_id=validation_run_id,
            subject_hash=subject_hash,
            items=ordered,
            completion_sha256=canonical_sha256(final_preimage),
        )


class ValidationIntermediateRuntime:
    """Generic typed/hash-bound replayable intermediate persistence facade."""

    def __init__(self, store: ValidationStore) -> None:
        self.store = store

    def commit(
        self,
        *,
        validation_run_id: str,
        producer_ref: str,
        target_ref: str,
        typed_intermediate: Any,
        intermediate_kind: ValidationIntermediateKind,
        causation_event_id: str | None = None,
        occurred_at_epoch: float | None = None,
    ) -> ValidationIntermediateRefSchema:
        if not hasattr(typed_intermediate, "model_dump"):
            raise TypeError("typed_intermediate must be a typed schema instance")
        payload = typed_intermediate.model_dump(mode="json")
        payload_hash = canonical_sha256(payload)
        event_id = "validation-intermediate:" + canonical_sha256(
            {
                "validation_run_id": str(validation_run_id).strip(),
                "producer_ref": str(producer_ref).strip(),
                "target_ref": str(target_ref).strip(),
                "intermediate_kind": intermediate_kind.value,
                "typed_payload_hash": payload_hash,
            }
        )
        ref = ValidationIntermediateRefSchema(
            validation_run_id=str(validation_run_id).strip(),
            event_id=event_id,
            intermediate_kind=intermediate_kind,
            intermediate_sha256=payload_hash,
        )
        return self.store.append_intermediate_if_absent_or_same(
            validation_run_id=ref.validation_run_id,
            event_id=event_id,
            typed_intermediate=payload,
            intermediate_ref=ref,
            causation_event_id=causation_event_id,
            producer_ref=str(producer_ref).strip(),
            target_ref=str(target_ref).strip(),
            occurred_at_epoch=occurred_at_epoch,
        )

    def resolve_typed(self, ref: ValidationIntermediateRefSchema, schema_cls, *, connection=None):
        payload = (self.store.resolve_intermediate(ref) if connection is None
            else self.store.resolve_intermediate(ref, connection=connection))
        return schema_cls.model_validate(payload)


class ValidatorExecutionRuntime:
    """Terminal accounting facade over ValidationStore; no validator semantics live here."""

    def __init__(self, store: ValidationStore) -> None:
        self.store = store

    def persist_fact(self, fact) -> ValidationFactRefSchema:
        return self.store.put_fact_if_absent_or_same(fact)

    def persist_completion(
        self,
        completion: ValidationCompletionSchema,
    ) -> ValidationCompletionRefSchema:
        return self.store.put_completion_if_absent_or_same(completion)

    def finalize(
        self,
        *,
        terminal_event: ValidationExecutionEventSchema,
        record: ValidatorExecutionRecordSchema,
        stage_plan: StagePlanSchema,
        input_envelope: InputEnvelopeSchema,
    ) -> ValidatorExecutionRecordSchema:
        return self.store.finalize_validator_execution(
            terminal_event,
            record,
            stage_plan=stage_plan,
            input_envelope=input_envelope,
        )


__all__ = [
    "ModelCallCompatibilityGuard",
    "PhysicalModelCallDispatcher",
    "PhysicalModelDispatchResult",
    "StagePlanConformanceGuard",
    "ValidationCompletionBuilder",
    "ValidationIntermediateRuntime",
    "ValidationRunFactory",
    "ValidationRunLifecycleService",
    "ValidationSubjectKeyBuilder",
    "ValidatorInputEnvelopeBuilder",
    "ValidatorExecutionRuntime",
]
