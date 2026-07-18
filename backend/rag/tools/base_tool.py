"""Deprecated aliases for the legacy dict-based tool contract.

Agent-facing tools use ``contracts.base_tool.BaseTool``.
"""
from rag.legacy.tool_contract import LegacyDictTool, LegacyToolResult

BaseTool = LegacyDictTool
ToolResult = LegacyToolResult

__all__ = ["BaseTool", "ToolResult", "LegacyDictTool", "LegacyToolResult"]
