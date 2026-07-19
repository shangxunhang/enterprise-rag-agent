"""Public evidence-retrieval boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema


@runtime_checkable
class RAGServicePort(Protocol):
    """Retrieve canonical evidence without generating a business answer."""

    def retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema: ...
