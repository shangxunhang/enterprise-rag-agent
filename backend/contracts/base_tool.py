# =============================================================================
# 中文阅读说明：端口与协议定义模块，用于约束模块间依赖边界。
# 主要定义：BaseTool。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Base tool interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.tool import ToolCallSchema, ToolResultSchema


# 阅读注释（类）：封装 base 工具，集中封装相关状态、依赖和行为。
class BaseTool(ABC):
    """Base class for all tools."""

    tool_name: str
    description: str = ""

    # 阅读注释（函数）：执行 BaseTool 的主流程。
    @abstractmethod
    def run(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """Run tool with a standardized ToolCallSchema."""
        raise NotImplementedError