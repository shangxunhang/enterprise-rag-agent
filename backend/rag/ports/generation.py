"""Minimal text-model port used by retrieval-time reasoning plugins."""
from __future__ import annotations

from typing import Any, Protocol


class TextGenerator(Protocol):
    """Generate query transforms or grading text; never the final answer."""

    def generate(self, prompt: str, **kwargs: Any) -> str: ...
