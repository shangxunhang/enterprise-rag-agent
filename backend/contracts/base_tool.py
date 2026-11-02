"""定义 Agent 可调用工具的抽象基类。

工具实现通过统一的调用模型接收参数，并通过统一结果模型返回成功、失败和产物
信息。这样 Agent 与工作流不需要了解每个工具的具体函数签名或内部依赖。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.tool import ToolCallSchema, ToolResultSchema


class BaseTool(ABC):
    """约束所有 Agent 工具必须暴露的元数据和执行入口。

    Attributes:
        tool_name: 工具注册和路由使用的稳定唯一名称。
        description: 提供给 Agent 或人工阅读者的能力说明；实现类可覆盖默认值。

    工具内部可以访问文件、检索服务或其他业务组件，但必须在返回前将结果转换为
    ``ToolResultSchema``，避免上层依赖实现细节。
    """

    tool_name: str
    description: str = ""

    @abstractmethod
    def run(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """执行一次标准化工具调用。

        Args:
            tool_call: 包含工具名称、参数和调用上下文的标准调用对象。

        Returns:
            标准工具结果，包含执行状态、结构化输出以及可能的错误信息。

        实现类应在该边界内完成参数解释、副作用执行和异常归一化；基类不提供
        默认执行逻辑。
        """

        raise NotImplementedError
