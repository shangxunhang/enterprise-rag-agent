"""Base LLM client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.model import ModelRequestSchema, ModelResponseSchema


class BaseLLMClient(ABC):
    """Base class for all LLM clients."""

    model_name: str

    @abstractmethod
    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        """Generate text from model request."""
        raise NotImplementedError