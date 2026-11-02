"""提供进程内工具实例注册与按名称查找能力。

注册表只保存装配层已经创建的工具实例，不负责构造、执行或销毁工具。Agent 和
``ToolExecutor`` 通过稳定 ``tool_name`` 查找实例，从而与具体工具类解耦。
"""

from __future__ import annotations

from typing import Dict, List

from contracts.base_tool import BaseTool


class ToolRegistry:
    """维护当前运行时可调用的工具实例集合。

    Attributes:
        _tools: 以稳定工具名称为键的内部实例映射。
    """

    def __init__(self) -> None:
        """创建空工具注册表。"""

        # 注册表保存实例本身，后续查找返回装配阶段配置好的同一对象。
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """登记一个名称尚未占用的工具实例。

        Args:
            tool: 已完成依赖装配且具有稳定 ``tool_name`` 的工具。

        Raises:
            ValueError: 相同名称已经登记时抛出，防止后注册实例静默覆盖前者。
        """

        # 第一步：以工具名称检查唯一性，保证调用解析结果确定。
        if tool.tool_name in self._tools:
            raise ValueError(f"Tool already registered: {tool.tool_name}")

        # 第二步：保存调用方创建的实例；注册表不改变其生命周期。
        self._tools[tool.tool_name] = tool

    def get(self, tool_name: str) -> BaseTool:
        """按稳定名称取得已注册工具。

        Args:
            tool_name: ``ToolCallSchema`` 请求的工具名称。

        Returns:
            装配阶段登记的工具实例。

        Raises:
            KeyError: 名称尚未注册时抛出，避免把 ``None`` 延迟传到远端执行点。
        """

        # 显式检查并提供领域化错误文本，方便 ToolExecutor 统一归一化异常。
        if tool_name not in self._tools:
            raise KeyError(f"Tool not found: {tool_name}")
        return self._tools[tool_name]

    def exists(self, tool_name: str) -> bool:
        """判断给定工具名称是否已注册。"""

        return tool_name in self._tools

    def list_tools(self) -> List[str]:
        """按注册顺序返回全部工具名称。"""

        # 返回新列表，避免调用方接触内部字典视图。
        return list(self._tools.keys())
