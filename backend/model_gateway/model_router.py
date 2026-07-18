"""Model selection policy."""

from __future__ import annotations

from schemas.model import ModelRequestSchema


class ModelRouter:
    def __init__(self, default_model_name: str) -> None:
        self.default_model_name = default_model_name

    def select(self, request: ModelRequestSchema) -> str:
        return request.model_name or self.default_model_name
