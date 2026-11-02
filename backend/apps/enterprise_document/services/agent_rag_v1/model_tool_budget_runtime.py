"""Canonical Budget consumer adapter for Model/Tool runtime boundaries.

This adapter owns no Budget policy or balance. It translates one exact Model/Tool
execution occurrence into replay/correlation-stable reservation requests consumed by
the Step-3 canonical BudgetManager.
"""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, TypeVar, Literal

from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    BudgetReservationRefSchema,
    CanonicalBudgetVectorSchema,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    BudgetManager, CanonicalBudgetRevisionConflict,
)
from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import canonical_json_sha256
from pydantic import BaseModel, ConfigDict, Field, model_validator
from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetAdmissionDenialSchema
from schemas.model import ModelRequestSchema, ModelResponseSchema


BudgetManagerProvider = Callable[[str, str], BudgetManager]
_T = TypeVar("_T")


class PrePhysicalTokenAdmissionProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["pre_physical_token_admission_proof_v1"] = "pre_physical_token_admission_proof_v1"
    phase: Literal["PRE_PHYSICAL_TOKEN_ADMISSION_REJECTED"] = "PRE_PHYSICAL_TOKEN_ADMISSION_REJECTED"
    routed_request: ModelRequestSchema
    native_denial: CanonicalBudgetAdmissionDenialSchema
    logical_reservation_id: str = Field(min_length=1)
    release_budget_revision: int = Field(ge=1)
    release_budget_state_sha256: str = Field(min_length=64, max_length=64)
    first_provider_attempt_ordinal: Literal[1] = 1
    availability_fallback_from: tuple[str, ...] = ()
    physical_dispatch_started: Literal[False] = False
    gateway_call_closed: Literal[True] = True
    proof_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _bound_hash(self):
        if self.availability_fallback_from:
            raise ValueError("first-dispatch proof cannot contain availability fallback")
        base = self.model_dump(mode="json", exclude={"proof_sha256"})
        if self.proof_sha256 != canonical_json_sha256(base):
            raise ValueError("first-dispatch phase proof hash differs")
        return self


class CanonicalInvocationBudgetAdapter:
    """Thin Model/Tool execution adapter over one canonical BudgetManager."""

    def __init__(self, budget_manager: BudgetManager) -> None:
        if not getattr(budget_manager, "_canonical_mode", False):
            raise ValueError("Model/Tool runtime requires canonical BudgetManager")
        self.budget_manager = budget_manager

    def _revision(self) -> int:
        return int(self.budget_manager.snapshot().revision)

    def _with_current_revision(self, operation: Callable[[int], _T]) -> _T:
        # A rejected stale CAS has not admitted/consumed anything. Re-read only
        # this same operation; never retry a provider or enlarge Budget capacity.
        for attempt in range(8):
            try:
                return operation(self._revision())
            except CanonicalBudgetRevisionConflict:
                if attempt == 7:
                    raise
        raise AssertionError("unreachable CAS attempt boundary")

    @staticmethod
    def _reservation_id(kind: str, payload: dict[str, Any]) -> str:
        return f"budget:{kind}:{canonical_sha256(payload)}"

    def _ref(
        self,
        *,
        reservation_kind: str,
        owner_occurrence_ref: str,
        scope_ref: str,
        operation_ref: str,
        attempt_id: str | None = None,
    ) -> BudgetReservationRefSchema:
        task_id = str(self.budget_manager.task_id)
        run_id = str(self.budget_manager.run_id)
        material = {
            "reservation_kind": reservation_kind,
            "task_id": task_id,
            "run_id": run_id,
            "owner_occurrence_ref": owner_occurrence_ref,
            "scope_ref": scope_ref,
            "operation_ref": operation_ref,
            "attempt_id": attempt_id,
        }
        return BudgetReservationRefSchema(
            reservation_id=self._reservation_id(reservation_kind, material),
            task_id=task_id,
            run_id=run_id,
            reservation_kind=reservation_kind,
            owner_occurrence_ref=owner_occurrence_ref,
            scope_ref=scope_ref,
            operation_ref=operation_ref,
            attempt_id=attempt_id,
        )

    def reserve_model_logical_call(
        self,
        request: ModelRequestSchema,
    ) -> str:
        ref = self._ref(
            reservation_kind="logical_model_call",
            owner_occurrence_ref=request.model_call_id,
            scope_ref=str(request.extra.get("section_id") or f"run:{request.run_id}"),
            operation_ref=str(
                request.extra.get("logical_operation_id") or request.model_call_id
            ),
            attempt_id=(
                str(request.extra.get("attempt_id"))
                if request.extra.get("attempt_id")
                else None
            ),
        )
        reservation = self._with_current_revision(
            lambda revision: self.budget_manager.reserve(
                ref, CanonicalBudgetVectorSchema(logical_model_calls=1),
                expected_revision=revision,
            )
        )
        return reservation.reservation_ref.reservation_id

    def settle_model_logical_call(self, reservation_id: str) -> None:
        self._with_current_revision(
            lambda revision: self.budget_manager.consume(
                reservation_id, CanonicalBudgetVectorSchema(logical_model_calls=1),
                expected_revision=revision,
            )
        )

    def release_reservation(self, reservation_id: str) -> None:
        self._with_current_revision(
            lambda revision: self.budget_manager.release(
                reservation_id, expected_revision=revision,
            )
        )

    @staticmethod
    def provider_token_allowance(request: ModelRequestSchema) -> int:
        """Conservative provider-independent token upper bound.

        UTF-8 byte length is a deterministic upper bound for input token count for
        supported byte/codepoint text tokenizers; output allowance is the routed
        request max_tokens. This avoids depending on one provider tokenizer for
        canonical admission.
        """

        material = [request.prompt or "", request.system_prompt or ""]
        for message in request.messages:
            material.append(str(message.get("role") or ""))
            material.append(str(message.get("content") or ""))
        input_upper_bound = sum(len(item.encode("utf-8")) for item in material)
        return max(1, input_upper_bound + int(request.max_tokens))

    def reserve_provider_attempt(
        self,
        request: ModelRequestSchema,
    ) -> str:
        provider_attempt_id = str(request.extra.get("provider_attempt_id") or "").strip()
        if not provider_attempt_id:
            raise ValueError("provider_attempt_id is required before Budget admission")
        ref = self._ref(
            reservation_kind="model_provider_attempt_tokens",
            owner_occurrence_ref=provider_attempt_id,
            scope_ref=str(request.extra.get("section_id") or f"run:{request.run_id}"),
            operation_ref=request.model_call_id,
            attempt_id=(
                str(request.extra.get("attempt_id"))
                if request.extra.get("attempt_id")
                else None
            ),
        )
        def admit(revision: int):
            # Re-evaluate the original guard after contention, not only before
            # the first CAS. A concurrent physical attempt must not sneak in.
            state = self.budget_manager.canonical_state()
            unresolved = [
                item for item in state.reservations
                if item.status == "active"
                and item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
                and item.reservation_ref.operation_ref == request.model_call_id
            ]
            if unresolved:
                raise RuntimeError(
                    "unreconciled active provider-token reservation blocks new provider attempt"
                )
            return self.budget_manager.admit_next_model_provider_attempt(
                ref, token_allowance=self.provider_token_allowance(request),
                expected_revision=revision,
            )

        reservation = self._with_current_revision(admit)
        return reservation.reservation_ref.reservation_id

    def settle_provider_attempt(
        self,
        reservation_id: str,
        request: ModelRequestSchema,
        response: ModelResponseSchema,
    ) -> None:
        reservation = self.budget_manager.resolve_reservation(reservation_id)
        allowance = int(reservation.requested.model_tokens_total)
        usage_complete = dict(response.metadata or {}).get("usage_complete") is not False
        exact_total = response.token_usage.total_tokens
        if usage_complete and exact_total is not None:
            actual = max(0, int(exact_total))
        else:
            observed_lower_bound = max(
                0,
                int(response.token_usage.prompt_tokens or 0)
                + int(response.token_usage.completion_tokens or 0),
            )
            actual = max(allowance, observed_lower_bound)
        self._with_current_revision(
            lambda revision: self.budget_manager.consume(
                reservation_id, CanonicalBudgetVectorSchema(model_tokens_total=actual),
                expected_revision=revision,
            )
        )

    def reserve_tool_logical_call(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_operation_id: str,
        scope_ref: str,
        parent_logical_operation_id: str,
        attempt_id: str | None,
    ):
        if task_id != self.budget_manager.task_id or run_id != self.budget_manager.run_id:
            raise ValueError("Tool Budget account does not match task/run")
        ref = self._ref(
            reservation_kind="logical_tool_call",
            owner_occurrence_ref=tool_operation_id,
            scope_ref=scope_ref,
            operation_ref=parent_logical_operation_id,
            attempt_id=attempt_id,
        )
        return self._with_current_revision(
            lambda revision: self.budget_manager.admit_tool_attempt(
                ref, expected_revision=revision,
            )
        )

    def settle_tool_logical_call(self, reservation_id: str):
        return self._with_current_revision(
            lambda revision: self.budget_manager.consume(
                reservation_id, CanonicalBudgetVectorSchema(logical_tool_calls=1),
                expected_revision=revision,
            )
        )


    def _logical_ref_for_proof(self, request):
        return self._ref(
            reservation_kind="logical_model_call", owner_occurrence_ref=request.model_call_id,
            scope_ref=str(request.extra.get("section_id") or f"run:{request.run_id}"),
            operation_ref=str(request.extra.get("logical_operation_id") or request.model_call_id),
            attempt_id=str(request.extra["attempt_id"]) if request.extra.get("attempt_id") else None,
        )

    def _validate_phase_native_states(self, request, denial, logical_id, release_revision=None,
                                      release_sha256=None):
        if (request.task_id, request.run_id) != (self.budget_manager.task_id, self.budget_manager.run_id):
            raise ValueError("phase proof request belongs to another Budget account")
        if (request.extra.get("provider_attempt_ordinal") != 1
                or request.extra.get("availability_fallback_from") not in ([], ())
                or not request.extra.get("model_request_fingerprint")):
            raise ValueError("phase proof is not an actual first routed provider attempt")
        wanted = self._ref(
            reservation_kind="model_provider_attempt_tokens",
            owner_occurrence_ref=str(request.extra["provider_attempt_id"]),
            scope_ref=str(request.extra.get("section_id") or f"run:{request.run_id}"),
            operation_ref=request.model_call_id,
            attempt_id=str(request.extra["attempt_id"]) if request.extra.get("attempt_id") else None,
        )
        if (denial.reservation_ref != wanted or denial.denied_dimension != "model_tokens_total"
                or denial.requested != CanonicalBudgetVectorSchema(model_tokens_total=self.provider_token_allowance(request))
                or denial.draining or denial.required <= denial.remaining):
            raise ValueError("native token denial differs from actual routed request")
        denied = self.budget_manager.resolve_state_revision(
            denial.budget_revision, expected_sha256=denial.budget_state_sha256)
        latest = self.budget_manager.canonical_state()
        if (denied.limits != denial.baseline_limits or denied.effective_limits != denial.effective_limits
                or denied.remaining != denial.available or denied.draining != denial.draining):
            raise ValueError("native token denial context differs from exact state revision")
        logical_ref = self._logical_ref_for_proof(request)
        def find(state):
            return next((row for row in state.reservations if row.reservation_ref.reservation_id == logical_id), None)
        before, after = find(denied), find(latest)
        if (before is None or after is None or logical_ref.reservation_id != logical_id
                or before.reservation_ref != logical_ref or after.reservation_ref != logical_ref
                or before.status != "active" or after.status != "released"
                or before.requested != CanonicalBudgetVectorSchema(logical_model_calls=1)
                or not after.actual.is_zero() or after.terminal_budget_revision is None):
            raise ValueError("original logical reservation has no exact active-to-released closure")
        for state in (denied, latest):
            if any(row.reservation_ref.reservation_kind == "model_provider_attempt_tokens"
                   and row.reservation_ref.operation_ref == request.model_call_id for row in state.reservations):
                raise ValueError("native previous or active provider occurrence forbids no-effect proof")
        revision = int(after.terminal_budget_revision)
        if release_revision is not None and int(release_revision) != revision:
            raise ValueError("phase proof release revision differs from native logical terminal fact")
        released = self.budget_manager.resolve_state_revision(revision, expected_sha256=release_sha256)
        if find(released) != after:
            raise ValueError("phase proof logical release differs from immutable terminal reservation")
        return revision, canonical_json_sha256(released.model_dump(mode="json"))

    def build_pre_physical_token_rejection_proof(self, request, native_denial, logical_reservation_id):
        if native_denial is None:
            return None
        denial = CanonicalBudgetAdmissionDenialSchema.model_validate(native_denial)
        revision, sha = self._validate_phase_native_states(request, denial, logical_reservation_id)
        base = dict(schema_version="pre_physical_token_admission_proof_v1",
            phase="PRE_PHYSICAL_TOKEN_ADMISSION_REJECTED", routed_request=request.model_dump(mode="json"),
            native_denial=denial.model_dump(mode="json"), logical_reservation_id=logical_reservation_id,
            release_budget_revision=revision, release_budget_state_sha256=sha,
            first_provider_attempt_ordinal=1, availability_fallback_from=[],
            physical_dispatch_started=False, gateway_call_closed=True)
        return PrePhysicalTokenAdmissionProof(**base, proof_sha256=canonical_json_sha256(base))

    def validate_pre_physical_token_rejection_proof(self, proof):
        validated = PrePhysicalTokenAdmissionProof.model_validate(proof)
        self._validate_phase_native_states(validated.routed_request, validated.native_denial,
            validated.logical_reservation_id, validated.release_budget_revision,
            validated.release_budget_state_sha256)
        return validated.model_copy(deep=True)


class CanonicalInvocationBudgetRouter:
    """Route Model Budget calls to the canonical task/run BudgetManager.

    The router owns no policy or balance.  Reservation ownership is retained
    only long enough to settle/release the exact reservation produced by the
    canonical manager that owns it.
    """

    def __init__(self, provider: BudgetManagerProvider, *,
                 existing_manager_provider: BudgetManagerProvider | None = None) -> None:
        if not callable(provider):
            raise TypeError("canonical Budget provider must be callable")
        if existing_manager_provider is not None and not callable(existing_manager_provider):
            raise TypeError("existing canonical Budget provider must be callable")
        self.provider = provider
        self.existing_manager_provider = existing_manager_provider
        self._lock = RLock()
        self._reservation_owners: dict[str, CanonicalInvocationBudgetAdapter] = {}

    def _adapter(self, task_id: str, run_id: str) -> CanonicalInvocationBudgetAdapter:
        return CanonicalInvocationBudgetAdapter(self.provider(task_id, run_id))

    def resolve_existing_budget_manager(self, task_id: str, run_id: str) -> BudgetManager:
        """Resolve durable existing-account truth without admission or owner cache."""
        task, run = str(task_id or "").strip(), str(run_id or "").strip()
        if not task or not run:
            raise ValueError("existing canonical Budget requires task_id/run_id")
        if self.existing_manager_provider is None:
            raise RuntimeError("existing canonical Budget provider is not configured")
        manager = self.existing_manager_provider(task, run)
        if (not getattr(manager, "_canonical_mode", False)
                or manager.task_id != task or manager.run_id != run):
            raise RuntimeError("existing canonical Budget manager belongs to another account")
        # A cached object cannot replace the durable account after its removal.
        manager.canonical_state()
        return manager

    def _remember(
        self,
        reservation_id: str,
        adapter: CanonicalInvocationBudgetAdapter,
    ) -> str:
        with self._lock:
            current = self._reservation_owners.get(reservation_id)
            if current is not None and (
                current.budget_manager is not adapter.budget_manager
            ):
                raise RuntimeError(
                    "canonical Budget reservation is bound to another manager"
                )
            self._reservation_owners[reservation_id] = adapter
        return reservation_id

    def _owner(self, reservation_id: str) -> CanonicalInvocationBudgetAdapter:
        with self._lock:
            owner = self._reservation_owners.get(str(reservation_id))
        if owner is None:
            raise RuntimeError(
                "canonical Budget reservation owner is not available in this execution"
            )
        return owner

    def reserve_model_logical_call(self, request: ModelRequestSchema) -> str:
        adapter = self._adapter(request.task_id, request.run_id)
        return self._remember(
            adapter.reserve_model_logical_call(request),
            adapter,
        )

    def settle_model_logical_call(self, reservation_id: str) -> None:
        owner = self._owner(reservation_id)
        owner.settle_model_logical_call(reservation_id)

    def release_reservation(self, reservation_id: str) -> None:
        owner = self._owner(reservation_id)
        owner.release_reservation(reservation_id)

    def reserve_provider_attempt(self, request: ModelRequestSchema) -> str:
        adapter = self._adapter(request.task_id, request.run_id)
        return self._remember(
            adapter.reserve_provider_attempt(request),
            adapter,
        )

    def settle_provider_attempt(
        self,
        reservation_id: str,
        request: ModelRequestSchema,
        response: ModelResponseSchema,
    ) -> None:
        owner = self._owner(reservation_id)
        if (
            owner.budget_manager.task_id != request.task_id
            or owner.budget_manager.run_id != request.run_id
        ):
            raise RuntimeError(
                "provider Budget settlement request belongs to another task/run"
            )
        owner.settle_provider_attempt(reservation_id, request, response)


    def build_pre_physical_token_rejection_proof(self, request, native_denial, logical_reservation_id):
        return self._owner(logical_reservation_id).build_pre_physical_token_rejection_proof(
            request, native_denial, logical_reservation_id)

    def validate_pre_physical_token_rejection_proof(self, proof):
        validated = PrePhysicalTokenAdmissionProof.model_validate(proof)
        manager = self.resolve_existing_budget_manager(validated.routed_request.task_id, validated.routed_request.run_id)
        return CanonicalInvocationBudgetAdapter(manager).validate_pre_physical_token_rejection_proof(validated)


__all__ = [
    "PrePhysicalTokenAdmissionProof",
    "CanonicalInvocationBudgetAdapter",
    "CanonicalInvocationBudgetRouter",
]
