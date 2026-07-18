"""Model client registry."""

from __future__ import annotations

from typing import Dict

from contracts.base_client import BaseLLMClient


class ModelRegistry:
    def __init__(self) -> None:
        self._clients: Dict[str, BaseLLMClient] = {}

    def register(self, client: BaseLLMClient) -> None:
        if client.model_name in self._clients:
            raise ValueError(f"Model client already registered: {client.model_name}")
        self._clients[client.model_name] = client

    def get(self, model_name: str) -> BaseLLMClient:
        if model_name not in self._clients:
            raise KeyError(f"Model client not found: {model_name}")
        return self._clients[model_name]

    def contains(self, model_name: str) -> bool:
        return model_name in self._clients

    def names(self) -> list[str]:
        return list(self._clients)
