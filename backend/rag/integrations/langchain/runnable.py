"""Expose the canonical RAG service as a LangChain Runnable without losing schema fidelity."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable, RunnableLambda

from contracts.rag import RAGServicePort
from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema


def _coerce_request(value: Any) -> RAGToolInputSchema:
    if isinstance(value, RAGToolInputSchema):
        return value
    if isinstance(value, dict):
        return RAGToolInputSchema.model_validate(value)
    raise TypeError(
        "LangChain RAG runnable expects RAGToolInputSchema or dict, "
        f"got {type(value).__name__}"
    )


def build_rag_runnable(
    rag_service: RAGServicePort,
    *,
    run_name: str = "enterprise_rag_retrieval",
) -> Runnable[Any, EvidenceBundleSchema]:
    """Build a LangChain Runnable over the stable application-facing RAG port.

    Important:
    - The RAG core remains the source of truth.
    - EvidenceBundleSchema is preserved end-to-end.
    - LangChain is used as an interoperability/orchestration interface only.
    """

    def invoke(value: Any) -> EvidenceBundleSchema:
        request = _coerce_request(value)
        return rag_service.retrieve(request)

    return RunnableLambda(invoke).with_config(
        {
            "run_name": run_name,
            "tags": ["enterprise-rag", "langchain", "retrieval"],
        }
    )
