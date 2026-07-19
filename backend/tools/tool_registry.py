# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：ToolRegistry。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Tool registry."""

from __future__ import annotations

from typing import Dict, List

from contracts.base_tool import BaseTool


# 阅读注释（类）：封装 工具 注册表，集中封装相关状态、依赖和行为。
class ToolRegistry:
    """Registry for tools."""

    # 阅读注释（函数）：初始化 ToolRegistry，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 ToolRegistry，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self._tools: Dict[str, BaseTool] = {}

    # 阅读注释（函数）：注册 ToolRegistry。
    def register(self, tool: BaseTool) -> None:
        """注册 ToolRegistry。

        参数:
            tool: 工具，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError。
        """
        if tool.tool_name in self._tools:
            raise ValueError(f"Tool already registered: {tool.tool_name}")
        self._tools[tool.tool_name] = tool

    # 阅读注释（函数）：获取 ToolRegistry。
    def get(self, tool_name: str) -> BaseTool:
        """获取 ToolRegistry。

        参数:
            tool_name: 工具 名称，具体约束请结合类型标注和调用方确认。

        返回:
            BaseTool

        阅读提示:
            主要直接调用：KeyError。
        """
        if tool_name not in self._tools:
            raise KeyError(f"Tool not found: {tool_name}")
        return self._tools[tool_name]

    # 阅读注释（函数）：处理 exists 相关逻辑。
    def exists(self, tool_name: str) -> bool:
        """处理 exists 相关逻辑。

        参数:
            tool_name: 工具 名称，具体约束请结合类型标注和调用方确认。

        返回:
            bool
        """
        return tool_name in self._tools

    # 阅读注释（函数）：列出 tools。
    def list_tools(self) -> List[str]:
        """列出 tools。

        返回:
            List[str]

        阅读提示:
            主要直接调用：list, self._tools.keys。
        """
        return list(self._tools.keys())