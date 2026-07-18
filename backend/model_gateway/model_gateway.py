"""Facade over model registry, routing, invocation and observability."""

from __future__ import annotations

from typing import Any, Dict, Optional

from contracts.base_client import BaseLLMClient
from contracts.observability import TraceSink
from model_gateway.model_invoker import ModelInvoker
from model_gateway.model_observer import ModelCallObserver
from model_gateway.model_registry import ModelRegistry
from model_gateway.model_router import ModelRouter
from observability.trace_context import activate_span
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.status import ExecutionStatus


class ModelGateway:
    def __init__(
        self,
        default_model_name: str = "fake_llm",
        run_trace_recorder: Optional[TraceSink] = None,
        *,
        registry: ModelRegistry | None = None,
        router: ModelRouter | None = None,
        invoker: ModelInvoker | None = None,
        observer: ModelCallObserver | None = None,
    ) -> None:
        self.default_model_name = default_model_name
        self.run_trace_recorder = run_trace_recorder
        self.registry = registry or ModelRegistry()
        self.router = router or ModelRouter(default_model_name)
        self.invoker = invoker or ModelInvoker(self.registry)
        self.observer = observer or ModelCallObserver(run_trace_recorder)
        # Compatibility alias for existing introspection/tests.
        self._clients = self.registry._clients

    def register_client(self, client: BaseLLMClient) -> None:
        self.registry.register(client)

    def get_client(self, model_name: str) -> BaseLLMClient:
        return self.registry.get(model_name)

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
            model_name=request.model_name or self.default_model_name,
        )

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        model_name = self.router.select(request)
        span_handle = self.observer.start(request, model_name=model_name)
        with activate_span(span_handle):
            response = self.invoker.invoke(request, model_name)
        self.observer.finish(
            request,
            response,
            model_name=model_name,
            handle=span_handle,
        )
        return response
