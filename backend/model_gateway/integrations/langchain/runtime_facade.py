"""Production ModelGateway port backed by a LangChain Runnable."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable

from schemas.model import ModelRequestSchema, ModelResponseSchema


class LangChainModelGatewayFacade:
    """Preserve the gateway port while making Runnable invocation authoritative.

    The wrapped Runnable still calls the original gateway exactly once, so
    routing, fallback, trace and usage accounting remain owned by the existing
    gateway implementation.  Other gateway capabilities are delegated without
    exposing a second source of truth.
    """

    def __init__(
        self,
        *,
        delegate: Any,
        runnable: Runnable[Any, ModelResponseSchema],
    ) -> None:
        self.delegate = delegate
        self.runnable = runnable
        self.runnable_invocation_count = 0

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        self.runnable_invocation_count += 1
        return self.runnable.invoke(
            request,
            config={
                "run_name": "production_model_gateway",
                "tags": [
                    "enterprise-rag-agent",
                    "production-mainline",
                    "model-gateway",
                ],
                "metadata": {
                    "task_id": request.task_id,
                    "run_id": request.run_id,
                    "model_call_id": request.model_call_id,
                    "model_role": request.model_role,
                    "caller_agent": request.caller_agent,
                },
            },
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
