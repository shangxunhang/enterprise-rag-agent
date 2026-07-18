"""Text-generation port for RAG-side query and answer generation."""
from __future__ import annotations

from typing import Any, Protocol


class TextGenerator(Protocol):
    def generate(self, prompt: str, **kwargs: Any) -> str: ...
