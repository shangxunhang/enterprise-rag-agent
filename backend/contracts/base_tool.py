"""Base tool interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.tool import ToolCallSchema, ToolResultSchema


class BaseTool(ABC):
    """Base class for all tools."""

    tool_name: str
    description: str = ""

    @abstractmethod
    def run(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """Run tool with a standardized ToolCallSchema."""
        raise NotImplementedError