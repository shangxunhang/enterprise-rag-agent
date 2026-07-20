"""Application-facing model gateway contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from schemas.model import ModelRequestSchema, ModelResponseSchema


@runtime_checkable
class ModelGatewayPort(Protocol):
    """Generate through the configured model registry and runtime boundary."""

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema: ...
