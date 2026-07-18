"""Legacy dict-based Tool contract used only inside rag-template."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class LegacyToolResult:
    success: bool
    tool_name: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool_name": self.tool_name,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }


class LegacyDictTool(ABC):
    name: str = "base_tool"
    description: str = "Legacy dictionary tool interface."

    @abstractmethod
    def run(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def _ok(self, data: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return LegacyToolResult(True, self.name, data, None, metadata or {}).to_dict()

    def _fail(self, error: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return LegacyToolResult(False, self.name, {}, error, metadata or {}).to_dict()
