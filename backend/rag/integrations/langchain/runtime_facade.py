"""Production RAG service port backed by a LangChain Runnable."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from threading import Lock
from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig

from core.runtime.langchain_config import (
    LangChainRuntimeCapabilities,
    merge_runnable_config,
)
from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema


class LangChainRAGServiceFacade:
    """Keep the canonical RAG port while making Runnable execution authoritative."""

    capabilities = LangChainRuntimeCapabilities()
    stream_semantics = "canonical_final_evidence_bundle"
    supports_token_streaming = False
    supports_native_async = False

    def __init__(
        self,
        *,
        delegate: Any,
        runnable: Runnable[Any, EvidenceBundleSchema],
    ) -> None:
        self.delegate = delegate
        self.runnable = runnable
        self.runnable_invocation_count = 0
        self._invocation_lock = Lock()

    def _record_invocations(self, count: int = 1) -> None:
        with self._invocation_lock:
            self.runnable_invocation_count += count

    @staticmethod
    def _base_config(request: RAGToolInputSchema) -> RunnableConfig:
        return RunnableConfig(
            run_name="production_rag_service",
            tags=[
                "enterprise-rag-agent",
                "production-mainline",
                "rag-retrieval",
            ],
            metadata={
                "task_id": request.task_id,
                "run_id": request.run_id,
                "agent_name": request.agent_name,
                "retrieval_trace_id": request.retrieval_trace_id,
                "retrieval_scope": request.retrieval_scope,
                "cancellation_semantics": "cooperative_rag_boundary",
                "stream_semantics": "canonical_evidence_bundle",
            },
        )

    def _config(
        self,
        request: RAGToolInputSchema,
        config: RunnableConfig | None,
    ) -> RunnableConfig:
        return merge_runnable_config(
            base=self._base_config(request),
            supplied=config,
        )

    def invoke(
        self,
        request: RAGToolInputSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> EvidenceBundleSchema:
        self._record_invocations()
        return self.runnable.invoke(
            request,
            config=self._config(request, config),
            **kwargs,
        )

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        return self.invoke(request)

    async def ainvoke(
        self,
        request: RAGToolInputSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> EvidenceBundleSchema:
        self._record_invocations()
        return await self.runnable.ainvoke(
            request,
            config=self._config(request, config),
            **kwargs,
        )

    def stream(
        self,
        request: RAGToolInputSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[EvidenceBundleSchema]:
        """Yield one canonical evidence bundle, never provider token chunks."""

        self._record_invocations()
        yield from self.runnable.stream(
            request,
            config=self._config(request, config),
            **kwargs,
        )

    async def astream(
        self,
        request: RAGToolInputSchema,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[EvidenceBundleSchema]:
        """Async-compatible final-bundle stream; provider I/O remains sync."""

        self._record_invocations()
        async for item in self.runnable.astream(
            request,
            config=self._config(request, config),
            **kwargs,
        ):
            yield item

    def batch(
        self,
        inputs: Sequence[RAGToolInputSchema],
        config: RunnableConfig | Sequence[RunnableConfig] | None = None,
        *,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[EvidenceBundleSchema]:
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
        inputs: Sequence[RAGToolInputSchema],
        config: RunnableConfig | Sequence[RunnableConfig] | None = None,
        *,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[EvidenceBundleSchema]:
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
        requests: Sequence[RAGToolInputSchema],
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

    def as_runnable(self) -> Runnable[Any, EvidenceBundleSchema]:
        """Expose the schema-preserving Runnable for LCEL composition."""

        return self.runnable.with_types(
            input_type=RAGToolInputSchema,
            output_type=EvidenceBundleSchema,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
