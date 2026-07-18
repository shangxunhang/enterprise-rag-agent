"""Tool registry."""

from __future__ import annotations

from typing import Dict, List

from contracts.base_tool import BaseTool


class ToolRegistry:
    """Registry for tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.tool_name in self._tools:
            raise ValueError(f"Tool already registered: {tool.tool_name}")
        self._tools[tool.tool_name] = tool

    def get(self, tool_name: str) -> BaseTool:
        if tool_name not in self._tools:
            raise KeyError(f"Tool not found: {tool_name}")
        return self._tools[tool_name]

    def exists(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())