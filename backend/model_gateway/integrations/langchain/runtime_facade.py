"""Production ModelGateway port backed by a LangChain Runnable."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from threading import Lock
from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig

from core.runtime.langchain_config import (
    LangChainRuntimeCapabilities,
    merge_runnable_config,
)
from schemas.model import ModelRequestSchema, ModelResponseSchema


class LangChainModelGatewayFacade:
    """Preserve the gateway port while making Runnable invocation authoritative.

    The wrapped Runnable still calls the original gateway exactly once, so
    routing, fallback, trace and usage accounting remain owned by the existing
    gateway implementation.  Other gateway capabilities are delegated without
    exposing a second source of truth.
    """

    capabilities = LangChainRuntimeCapabilities()
    stream_semantics = "canonical_final_response"
    supports_token_streaming = False
    supports_native_async = False

    def __init__(
        self,
        *,
        delegate: Any,
        runnable: Runnable[Any, ModelResponseSchema],
    ) -> None:
        self.delegate = delegate
        self.runnable = runnable
        self.runnable_invocation_count = 0
        self._invocation_lock = Lock()

    def _record_invocations(self, count: int = 1) -> None:
        with self._invocation_lock:
            self.runnable_invocation_count += count

    @staticmethod
    def _base_config(request: ModelRequestSchema) -> RunnableConfig:
        return RunnableConfig(
            run_name="production_model_gateway",
            tags=[
                "enterprise-rag-agent",
                "production-mainline",
                "model-gateway",
            ],
            metadata={
                "task_id": request.task_id,
                "run_id": request.run_id,
                "model_call_id": request.model_call_id,
                "model_role": request.model_role,
                "caller_agent": request.caller_agent,
                "cancellation_semantics": "cooperative_gateway_boundary",
                "stream_semantics": "canonical_final_response",
            },
        )

    def _config(
        self,
        request: ModelRequestSchema,
        config: RunnableConfig | None,
    ) -> RunnableConfig:
        return merge_runnable_config(
            base=self._base_config(request),
            supplied=config,
        )

    def invoke(
        self,
        request: ModelRequestSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> ModelResponseSchema:
        self._record_invocations()
        return self.runnable.invoke(
            request,
            config=self._config(request, config),
            **kwargs,
        )

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        return self.invoke(request)

    async def ainvoke(
        self,
        request: ModelRequestSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> ModelResponseSchema:
        self._record_invocations()
        return await self.runnable.ainvoke(
            request,
            config=self._config(request, config),
            **kwargs,
        )

    def stream(
        self,
        request: ModelRequestSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[ModelResponseSchema]:
        """Yield one canonical response, not provider-native token chunks."""

        self._record_invocations()
        yield from self.runnable.stream(
            request,
            config=self._config(request, config),
            **kwargs,
        )

    async def astream(
        self,
        request: ModelRequestSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelResponseSchema]:
        """Async-compatible final-response stream; provider I/O remains sync."""

        self._record_invocations()
        async for item in self.runnable.astream(
            request,
            config=self._config(request, config),
            **kwargs,
        ):
            yield item

    def batch(
        self,
        inputs: Sequence[ModelRequestSchema],
        config: RunnableConfig | Sequence[RunnableConfig] | None = None,
        *,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[ModelResponseSchema]:
        requests = list(inputs)
        configs = self._batch_configs(requests, config)
        self._record_invocations(len(requests))
        return self.runnable.batch(
            requests,
            config=configs,
            return_exceptions=return_exceptions,
            **kwargs,
        )

    async def abatch(
        self,
        inputs: Sequence[ModelRequestSchema],
        config: RunnableConfig | Sequence[RunnableConfig] | None = None,
        *,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[ModelResponseSchema]:
        requests = list(inputs)
        configs = self._batch_configs(requests, config)
        self._record_invocations(len(requests))
        return await self.runnable.abatch(
            requests,
            config=configs,
            return_exceptions=return_exceptions,
            **kwargs,
        )

    def _batch_configs(
        self,
        requests: Sequence[ModelRequestSchema],
        config: RunnableConfig | Sequence[RunnableConfig] | None,
    ) -> list[RunnableConfig]:
        if isinstance(config, Sequence) and not isinstance(config, dict):
            supplied = list(config)
            if len(supplied) != len(requests):
                raise ValueError("batch config length must match input length")
        else:
            supplied = [config] * len(requests)
        return [
            self._config(request, item)
            for request, item in zip(requests, supplied)
        ]

    def as_runnable(self) -> Runnable[Any, ModelResponseSchema]:
        """Expose the schema-preserving Runnable for LCEL composition."""

        return self.runnable.with_types(
            input_type=ModelRequestSchema,
            output_type=ModelResponseSchema,
        )

    def with_structured_output(
        self,
        schema: type[ModelResponseSchema] = ModelResponseSchema,
    ) -> Runnable[Any, ModelResponseSchema]:
        """Provide model-like compatibility without changing the domain schema."""

        if schema is not ModelResponseSchema:
            raise TypeError(
                "ModelGateway output is fixed to ModelResponseSchema; "
                "provider-native structured output must stay behind the gateway"
            )
        return self.as_runnable()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
