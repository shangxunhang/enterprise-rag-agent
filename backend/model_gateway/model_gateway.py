"""Facade over model registry, routing, invocation, fallback and observability."""

from __future__ import annotations

from threading import Condition, RLock
from typing import Any, Dict, Optional
from uuid import uuid4

from contracts.base_client import BaseLLMClient
from contracts.model_gateway import ModelInvocationBudgetRuntimePort
from contracts.observability import TraceSink
from core.hashing import canonical_sha256
from core.diagnostics import current_diagnostics
from core.model_input_accounting import (
    ModelContextBudgetExceeded, estimate_model_input, request_input_payload,
)
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    current_execution_control,
)
from model_gateway.failure_policy import AvailabilityFailurePolicy
from model_gateway.model_contract import ModelSelection, ResidencyPolicy
from model_gateway.model_invoker import ModelInvoker
from model_gateway.model_observer import ModelCallObserver
from model_gateway.model_registry import ModelRegistry
from model_gateway.model_router import ModelRouter
from model_gateway.usage_ledger import ModelUsageLedger
from observability.trace_context import activate_span
from schemas.failure import FailureOrigin
from schemas.model import ModelRequestSchema, ModelResponseSchema


class ModelGateway:
    """Stable model boundary with role routing and availability fallback."""

    def __init__(
        self,
        default_model_name: str = "fake_llm",
        run_trace_recorder: Optional[TraceSink] = None,
        *,
        registry: ModelRegistry | None = None,
        router: ModelRouter | None = None,
        invoker: ModelInvoker | None = None,
        observer: ModelCallObserver | None = None,
        failure_policy: AvailabilityFailurePolicy | None = None,
        usage_ledger: ModelUsageLedger | None = None,
        budget_runtime: ModelInvocationBudgetRuntimePort | None = None,
    ) -> None:
        self.default_model_name = default_model_name
        self.run_trace_recorder = run_trace_recorder
        self.registry = registry or ModelRegistry()
        self.router = router or ModelRouter(default_model_name)
        self.invoker = invoker or ModelInvoker(self.registry)
        self.observer = observer or ModelCallObserver(run_trace_recorder)
        self.failure_policy = failure_policy or AvailabilityFailurePolicy()
        self.usage_ledger = usage_ledger or ModelUsageLedger()
        self.budget_runtime = budget_runtime
        self._lifecycle = Condition(RLock())
        self._lifecycle_state = "open"
        self._active_calls = 0
        self._logical_request_fingerprints: dict[str, str] = {}
        self._exclusive_residency_lock = RLock()
        self._active_resident_by_group: dict[str, str] = {}
        # Compatibility alias for existing introspection/tests.
        self._clients = self.registry._clients

    def register_client(self, client: BaseLLMClient) -> None:
        self.registry.register(client)

    def get_client(self, model_name: str) -> BaseLLMClient:
        return self.registry.get(model_name)

    def usage_snapshot(self) -> dict[str, Any]:
        return self.usage_ledger.snapshot()

    def _begin_call(self) -> None:
        with self._lifecycle:
            if self._lifecycle_state != "open":
                raise RuntimeError("ModelGateway is closed")
            self._active_calls += 1

    def _end_call(self) -> None:
        with self._lifecycle:
            self._active_calls -= 1
            if self._active_calls == 0:
                self._lifecycle.notify_all()

    def close(self) -> None:
        """Idempotently wait for active calls and release all model providers."""
        with self._lifecycle:
            if self._lifecycle_state == "closed":
                return
            if self._lifecycle_state == "closing":
                while self._lifecycle_state != "closed":
                    self._lifecycle.wait()
                return
            self._lifecycle_state = "closing"
            while self._active_calls:
                self._lifecycle.wait()

        try:
            self.registry.close()
        finally:
            with self._lifecycle:
                self._lifecycle_state = "closed"
                self._lifecycle.notify_all()

    def routing_capabilities(
        self,
        *,
        model_role: str,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Resolve fallback-safe capabilities before prompt construction."""
        probe = ModelRequestSchema(
            model_call_id="model_capability_probe",
            task_id="model_capability_probe",
            run_id="model_capability_probe",
            model_role=model_role,
            model_name=model_name,
            prompt="",
            created_at="capability_probe",
        )
        selections = self.router.plan(probe)
        profiles = [item.profile for item in selections]
        return {
            "model_role": model_role,
            "candidate_profiles": [item.profile_id for item in selections],
            "candidate_models": [item.model_name for item in selections],
            "candidate_capabilities": [
                {
                    "profile_id": item.profile_id,
                    "model_name": item.model_name,
                    "provider": item.provider,
                    "provider_model_name": item.profile.provider_model_name,
                    "local_path": item.profile.local_path,
                    "context_window": item.profile.context_window,
                    "max_output_tokens": item.profile.max_output_tokens,
                    "capability_source": item.profile.metadata.get("capability_source"),
                }
                for item in selections
            ],
            "safe_context_window": min(item.context_window for item in profiles),
            "safe_max_output_tokens": min(item.max_output_tokens for item in profiles),
            "primary_context_window": profiles[0].context_window,
            "primary_max_output_tokens": profiles[0].max_output_tokens,
        }

    def record_quality_escalation(self) -> None:
        """Called by quality-layer orchestration, never inferred by Gateway."""
        self.usage_ledger.record_quality_escalation()

    def _record(
        self,
        request: ModelRequestSchema,
        event_type: str,
        status: str,
        payload: Dict[str, Any],
    ) -> None:
        self.observer.record(
            request,
            event_type=event_type,
            status=status,
            payload=payload,
            model_name=str(request.model_name or self.default_model_name),
        )

    @staticmethod
    def _request_fingerprint(request: ModelRequestSchema) -> str:
        return canonical_sha256(
            {
                "model_role": request.model_role,
                "call_purpose": request.extra.get("call_purpose"),
                "prompt": request.prompt,
                "system_prompt": request.system_prompt,
                "messages": list(request.messages),
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "generation_params": request.extra.get("generation_params"),
                "prompt_id": request.extra.get("prompt_id"),
                "prompt_version": request.extra.get("prompt_version"),
                "logical_operation_id": request.extra.get("logical_operation_id"),
                "input_snapshot_version": request.extra.get("input_snapshot_version"),
                "context_package_id": (
                    request.extra.get("llm_context_summary") or {}
                ).get("package_id"),
                "context_sha256": (
                    request.extra.get("llm_context_summary") or {}
                ).get("context_sha256"),
            }
        )

    @staticmethod
    def _attempt_request(
        request: ModelRequestSchema,
        selection: ModelSelection,
        *,
        attempt_index: int,
        fallback_chain: list[str],
    ) -> ModelRequestSchema:
        extra = {
            **dict(request.extra),
            "model_role": (
                selection.role.value
                if selection.role is not None
                else request.model_role
            ),
            "selected_profile": selection.profile_id,
            "selected_model": selection.model_name,
            "provider": selection.provider,
            "model_candidate_index": attempt_index,
            # Physical provider-attempt identity is distinct from the stable
            # logical model_call_id. Trace observes both but owns neither
            # fallback nor Budget admission semantics.
            "provider_attempt_id": (
                f"provider_attempt_{request.model_call_id}_{attempt_index + 1}_"
                f"{uuid4().hex[:12]}"
            ),
            "provider_attempt_ordinal": attempt_index + 1,
            "availability_fallback_from": list(fallback_chain),
        }
        # Keep the caller's explicit override semantics in the original request,
        # while each provider receives the concrete selected model for traceability.
        return request.model_copy(
            update={
                "model_role": (
                    selection.role.value
                    if selection.role is not None
                    else request.model_role
                ),
                "model_name": selection.model_name,
                "max_tokens": min(
                    int(request.max_tokens),
                    int(selection.profile.max_output_tokens),
                ),
                "extra": extra,
            }
        )

    @staticmethod
    def _annotate_response(
        response: ModelResponseSchema,
        selection: ModelSelection,
        *,
        attempt_index: int,
        candidate_count: int,
        fallback_chain: list[str],
    ) -> None:
        response.metadata = {
            **dict(response.metadata or {}),
            "model_role": selection.role.value if selection.role else None,
            "selected_profile": selection.profile_id,
            "selected_model": selection.model_name,
            "provider": selection.provider,
            "model_candidate_index": attempt_index,
            "model_candidate_count": candidate_count,
            "availability_fallback_from": list(fallback_chain),
            "availability_fallback_used": bool(fallback_chain),
        }

    @staticmethod
    def _exclusive_residency_group(selection: ModelSelection) -> str | None:
        group = str(
            selection.profile.metadata.get("exclusive_residency_group") or ""
        ).strip()
        return group or None

    def _invoke_with_residency(
        self,
        request: ModelRequestSchema,
        selection: ModelSelection,
    ) -> ModelResponseSchema:
        """Serialize mutually-exclusive local residents and switch only on role change."""
        group = self._exclusive_residency_group(selection)
        if group is None:
            return self.invoker.invoke(request, selection.model_name)

        with self._exclusive_residency_lock:
            current_model = self._active_resident_by_group.get(group)
            if (
                current_model is not None
                and current_model != selection.model_name
                and self.registry.contains(current_model)
            ):
                self.registry.release(current_model)
            self._active_resident_by_group[group] = selection.model_name
            return self.invoker.invoke(request, selection.model_name)

    def _release_if_on_demand(self, selection: ModelSelection) -> None:
        if selection.profile.residency_policy != ResidencyPolicy.ON_DEMAND:
            return
        if not self.registry.contains(selection.model_name):
            return
        try:
            self.registry.release(selection.model_name)
        except Exception:
            # Cleanup is best-effort and must not mask the provider result.
            pass

    def _release_active_logical_reservation(
        self, request: ModelRequestSchema, reservation_id: str,
    ) -> bool:
        """Release only a canonical ACTIVE logical; reuse is not new ownership."""
        runtime = self.budget_runtime
        manager = getattr(runtime, "budget_manager", None)
        if manager is None:
            resolve_existing = getattr(runtime, "resolve_existing_budget_manager", None)
            if not callable(resolve_existing):
                raise RuntimeError("logical cleanup requires the existing canonical Budget owner")
            manager = resolve_existing(request.task_id, request.run_id)
        if (not getattr(manager, "_canonical_mode", False)
                or (manager.task_id, manager.run_id) != (request.task_id, request.run_id)):
            raise RuntimeError("logical cleanup Budget owner differs from request")
        reservation = manager.resolve_reservation(reservation_id)
        ref = reservation.reservation_ref
        if (ref.reservation_id != reservation_id
                or (ref.task_id, ref.run_id) != (request.task_id, request.run_id)
                or ref.reservation_kind != "logical_model_call"
                or ref.owner_occurrence_ref != request.model_call_id
                or ref.scope_ref != str(request.extra.get("section_id") or f"run:{request.run_id}")
                or ref.operation_ref != str(request.extra.get("logical_operation_id") or request.model_call_id)
                or ref.attempt_id != (str(request.extra["attempt_id"]) if request.extra.get("attempt_id") else None)):
            raise RuntimeError("logical cleanup reservation identity differs from request")
        if reservation.status in {"consumed", "released"}:
            return False
        if reservation.status != "active":
            raise RuntimeError("logical cleanup reservation has no canonical ACTIVE state")
        # The original owner still enforces revision, integrity and release rules.
        # Do not suppress genuine corruption or concurrent settlement failures.
        runtime.release_reservation(reservation_id)
        return True

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """Route one logical call; close its native frame before publishing no-effect proof."""
        self._begin_call()
        pre_dispatch_denial_context = []
        try:
            return self._generate(request, _pre_dispatch_denial_context=pre_dispatch_denial_context)
        finally:
            self._end_call()
            # This context can only be populated by the exact first token-admission
            # rejection branch, after actual original logical release. It cannot be
            # supplied through request.extra or manufactured from trace absence.
            for denied_request, native_error, logical_id in pre_dispatch_denial_context:
                producer = getattr(self.budget_runtime, "build_pre_physical_token_rejection_proof", None)
                if callable(producer):
                    try:
                        proof = producer(denied_request, getattr(native_error, "native_denial", None), logical_id)
                    except Exception as proof_error:
                        # Preserve original native failure; absence of a valid proof
                        # always prevents continuation. The diagnostic is not proof.
                        native_error.phase_proof_validation_error = {
                            "error_type": type(proof_error).__name__, "message": str(proof_error)}
                    else:
                        if proof is not None:
                            native_error.pre_physical_phase_proof = proof

    def _generate(self, request: ModelRequestSchema, *, _pre_dispatch_denial_context=None) -> ModelResponseSchema:
        """Execute one logical call through separately-budgeted provider attempts."""

        # Routing/config validation happens before Budget is consumed.  A route
        # configuration error is not an executed cognition occurrence.
        selections = self.router.plan(request)
        # Validate the complete input against every actual fallback candidate
        # before any cognition/attempt reservation, observer start or dispatch.
        input_tokens = estimate_model_input(request_input_payload(
            messages=request.messages, prompt=request.prompt,
            system_prompt=request.system_prompt,
        ))
        for selection in selections:
            output_tokens = min(int(request.max_tokens), selection.profile.max_output_tokens)
            if output_tokens <= 0 or input_tokens + output_tokens > selection.profile.context_window:
                raise ModelContextBudgetExceeded(
                    profile_id=selection.profile_id, input_tokens=input_tokens,
                    output_tokens=output_tokens, window=selection.profile.context_window,
                )
        request_fingerprint = self._request_fingerprint(request)
        with self._lifecycle:
            existing_fingerprint = self._logical_request_fingerprints.get(
                request.model_call_id
            )
            if (
                existing_fingerprint is not None
                and existing_fingerprint != request_fingerprint
            ):
                raise ValueError(
                    "model_call_id reused with materially different request fingerprint"
                )
            self._logical_request_fingerprints[request.model_call_id] = request_fingerprint
        request = request.model_copy(
            update={
                "extra": {
                    **dict(request.extra),
                    "model_request_fingerprint": request_fingerprint,
                }
            },
            deep=True,
        )
        logical_reservation_id: str | None = None
        logical_started = False
        if self.budget_runtime is not None:
            logical_reservation_id = self.budget_runtime.reserve_model_logical_call(
                request
            )

        fallback_chain: list[str] = []
        last_response: ModelResponseSchema | None = None

        for attempt_index, selection in enumerate(selections):
            execution_control = current_execution_control()
            if execution_control is not None:
                execution_control.checkpoint()
            attempt_request = self._attempt_request(
                request,
                selection,
                attempt_index=attempt_index,
                fallback_chain=fallback_chain,
            )

            token_reservation_id: str | None = None
            try:
                if self.budget_runtime is not None:
                    token_reservation_id = self.budget_runtime.reserve_provider_attempt(
                        attempt_request
                    )
            except Exception as admission_error:
                if (
                    self.budget_runtime is not None
                    and logical_reservation_id is not None
                    and not logical_started
                ):
                    released = self._release_active_logical_reservation(request, logical_reservation_id)
                    if (released and attempt_index == 0 and not fallback_chain
                            and _pre_dispatch_denial_context is not None
                            and getattr(admission_error, "native_denial", None) is not None):
                        _pre_dispatch_denial_context.append((
                            attempt_request.model_copy(deep=True), admission_error, logical_reservation_id))
                raise

            try:
                span_handle = self.observer.start(
                    attempt_request,
                    model_name=selection.model_name,
                )
            except Exception:
                if self.budget_runtime is not None:
                    if token_reservation_id is not None:
                        self.budget_runtime.release_reservation(token_reservation_id)
                    if logical_reservation_id is not None and not logical_started:
                        self._release_active_logical_reservation(request, logical_reservation_id)
                raise

            if (
                self.budget_runtime is not None
                and logical_reservation_id is not None
                and not logical_started
            ):
                self.budget_runtime.settle_model_logical_call(logical_reservation_id)
            if not logical_started:
                self.usage_ledger.record_logical_call(request.model_call_id)
                logical_started = True

            try:
                current_diagnostics().capture_model(
                    "MODEL_ATTEMPT_REQUEST", attempt_request,
                )
                with activate_span(span_handle):
                    with current_diagnostics().span(
                        attempt_request.model_call_id, "PROVIDER", unit_id="F01",
                        provider_attempt_id=attempt_request.extra.get("provider_attempt_id"),
                        provider_attempt_ordinal=attempt_request.extra.get("provider_attempt_ordinal"),
                        selected_profile=selection.profile_id,
                    ):
                        response = self._invoke_with_residency(
                            attempt_request,
                            selection,
                        )
                    # Preserve the client boundary before lifecycle or annotation
                    # mutates the representation delivered to callers.
                    current_diagnostics().capture_model(
                        "MODEL_CLIENT_RAW_RESPONSE", attempt_request, response,
                    )
            except WorkflowExecutionCancelled as exc:
                current_diagnostics().capture_model(
                    "MODEL_CLIENT_EXCEPTION", attempt_request, exception=exc,
                )
                response = ModelResponseSchema(
                    model_call_id=attempt_request.model_call_id,
                    task_id=attempt_request.task_id,
                    run_id=attempt_request.run_id,
                    model_name=selection.model_name,
                    success=False,
                    content="",
                    error_message=str(exc),
                    failure_origin=FailureOrigin.EXECUTION_LIFECYCLE,
                    latency_ms=span_handle.latency_ms(),
                    created_at=attempt_request.created_at,
                    finish_reason="cancelled",
                    metadata={
                        "execution_cancelled": True,
                        "usage_complete": False,
                        "cancel_reason": (
                            execution_control.cancel_reason
                            if execution_control is not None
                            else None
                        ),
                    },
                )
                self._annotate_response(
                    response,
                    selection,
                    attempt_index=attempt_index,
                    candidate_count=len(selections),
                    fallback_chain=fallback_chain,
                )
                if self.budget_runtime is not None and token_reservation_id is not None:
                    self.budget_runtime.settle_provider_attempt(
                        token_reservation_id,
                        attempt_request,
                        response,
                    )
                current_diagnostics().capture_model(
                    "MODEL_GATEWAY_PROJECTED_RESPONSE", attempt_request, response,
                )
                try:
                    self.observer.finish(
                        attempt_request,
                        response,
                        model_name=selection.model_name,
                        handle=span_handle,
                    )
                except Exception:
                    pass
                self.usage_ledger.record_attempt(
                    selection=selection,
                    response=response,
                )
                raise
            except BaseException as exc:
                current_diagnostics().capture_model(
                    "MODEL_CLIENT_EXCEPTION", attempt_request, exception=exc,
                )
                raise
            finally:
                self._release_if_on_demand(selection)

            if execution_control is not None and execution_control.should_stop:
                if not execution_control.is_cancelled:
                    execution_control.cancel("deadline_exceeded")
                provider_response_success = bool(response.success)
                response = response.model_copy(deep=True)
                response.success = False
                response.error = None
                response.error_message = (
                    f"workflow execution cancelled: "
                    f"{execution_control.cancel_reason or 'cancelled'}"
                )
                response.failure_origin = FailureOrigin.EXECUTION_LIFECYCLE
                response.finish_reason = "cancelled"
                response.metadata = {
                    **dict(response.metadata or {}),
                    "execution_cancelled": True,
                    "usage_complete": True,
                    "cancel_reason": execution_control.cancel_reason,
                    "provider_response_success": provider_response_success,
                }

            self._annotate_response(
                response,
                selection,
                attempt_index=attempt_index,
                candidate_count=len(selections),
                fallback_chain=fallback_chain,
            )
            if self.budget_runtime is not None and token_reservation_id is not None:
                self.budget_runtime.settle_provider_attempt(
                    token_reservation_id,
                    attempt_request,
                    response,
                )
                response.metadata = {
                    **dict(response.metadata or {}),
                    "canonical_budget_token_reservation_id": token_reservation_id,
                    "canonical_budget_logical_reservation_id": logical_reservation_id,
                }
            current_diagnostics().capture_model(
                "MODEL_GATEWAY_PROJECTED_RESPONSE", attempt_request, response,
            )
            self.observer.finish(
                attempt_request,
                response,
                model_name=selection.model_name,
                handle=span_handle,
            )
            self.usage_ledger.record_attempt(
                selection=selection,
                response=response,
            )
            last_response = response

            if execution_control is not None:
                execution_control.checkpoint()
            if response.success:
                return response

            has_next = attempt_index + 1 < len(selections)
            if not has_next or not self.failure_policy.is_availability_failure(
                response,
                selection,
            ):
                return response

            fallback_chain.append(selection.profile_id)
            self.usage_ledger.record_availability_fallback()

        if last_response is None:  # pragma: no cover - router forbids empty plans
            if (
                self.budget_runtime is not None
                and logical_reservation_id is not None
                and not logical_started
            ):
                self._release_active_logical_reservation(request, logical_reservation_id)
            raise RuntimeError("model routing returned no candidates")
        return last_response
