"""RAG services contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.rag import RAGToolInputSchema, RAGToolOutputSchema


class BaseRAGService(ABC):
    """Contract implemented by RAG services providers."""

    @abstractmethod
    def retrieve(
        self,
        request: RAGToolInputSchema,
    ) -> RAGToolOutputSchema:
        """Retrieve evidence and return a standardized RAG result."""
        raise NotImplementedError