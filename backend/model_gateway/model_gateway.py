"""Facade over model registry, routing, invocation, fallback and observability."""

from __future__ import annotations

from threading import Condition, RLock
from typing import Any, Dict, Optional

from contracts.base_client import BaseLLMClient
from contracts.observability import TraceSink
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
    ) -> None:
        self.default_model_name = default_model_name
        self.run_trace_recorder = run_trace_recorder
        self.registry = registry or ModelRegistry()
        self.router = router or ModelRouter(default_model_name)
        self.invoker = invoker or ModelInvoker(self.registry)
        self.observer = observer or ModelCallObserver(run_trace_recorder)
        self.failure_policy = failure_policy or AvailabilityFailurePolicy()
        self.usage_ledger = usage_ledger or ModelUsageLedger()
        self._lifecycle = Condition(RLock())
        self._lifecycle_state = "open"
        self._active_calls = 0
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

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """Route one logical call through bounded concrete-provider attempts."""

        self._begin_call()
        try:
            return self._generate(request)
        finally:
            self._end_call()

    def _generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """Execute one active logical call and its provider fallback attempts."""

        self.usage_ledger.record_logical_call(request.model_call_id)
        selections = self.router.plan(request)
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
            span_handle = self.observer.start(
                attempt_request,
                model_name=selection.model_name,
            )
            try:
                with activate_span(span_handle):
                    response = self.invoker.invoke(
                        attempt_request,
                        selection.model_name,
                    )
            except WorkflowExecutionCancelled as exc:
                # Entering the concrete client is a real provider attempt even
                # when cooperative cancellation is observed before a canonical
                # response can escape. Close its trace span and account for it,
                # but always preserve cancellation as control flow so no
                # availability fallback can start.
                response = ModelResponseSchema(
                    model_call_id=attempt_request.model_call_id,
                    task_id=attempt_request.task_id,
                    run_id=attempt_request.run_id,
                    model_name=selection.model_name,
                    success=False,
                    content="",
                    error_message=str(exc),
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
                try:
                    self.observer.finish(
                        attempt_request,
                        response,
                        model_name=selection.model_name,
                        handle=span_handle,
                    )
                except Exception:
                    # A trace sink failure must not turn cancellation into a
                    # provider error or permit the fallback loop to continue.
                    pass
                self.usage_ledger.record_attempt(
                    selection=selection,
                    response=response,
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
            # A native/CUDA provider cannot be interrupted safely mid-call.
            # Once it returns, usage is recorded and cancellation stops here
            # before a success commit or availability fallback.
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
            raise RuntimeError("model routing returned no candidates")
        return last_response
