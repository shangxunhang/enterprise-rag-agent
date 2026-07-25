"""Production RAG service port backed by a LangChain Runnable."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable

from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema


class LangChainRAGServiceFacade:
    """Keep the canonical RAG port while making Runnable execution authoritative."""

    def __init__(
        self,
        *,
        delegate: Any,
        runnable: Runnable[Any, EvidenceBundleSchema],
    ) -> None:
        self.delegate = delegate
        self.runnable = runnable
        self.runnable_invocation_count = 0

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema:
        self.runnable_invocation_count += 1
        return self.runnable.invoke(
            request,
            config={
                "run_name": "production_rag_service",
                "tags": [
                    "enterprise-rag-agent",
                    "production-mainline",
                    "rag-retrieval",
                ],
                "metadata": {
                    "task_id": request.task_id,
                    "run_id": request.run_id,
                    "agent_name": request.agent_name,
                    "retrieval_trace_id": request.extra.get(
                        "retrieval_trace_id"
                    ),
                    "retrieval_scope": request.extra.get("retrieval_scope"),
                },
            },
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
