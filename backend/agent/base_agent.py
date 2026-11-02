"""定义所有业务 Agent 必须遵守的抽象执行接口。

工作流通过统一共享状态调用 Agent，并接收统一 ``AgentResultSchema``。具体 Agent
可以编排工具、模型或领域服务，但不能把实现特有返回值直接暴露给工作流执行器。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.agent import AgentResultSchema


class BaseAgent(ABC):
    """约束 Agent 的稳定身份和同步执行入口。

    Attributes:
        agent_name: 注册表和工作流定义引用的稳定唯一名称。
        agent_type: Agent 的职责类别，用于轨迹、统计和管理界面分组。
    """

    agent_name: str
    agent_type: str

    @abstractmethod
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """读取共享状态并执行一次 Agent 任务。

        Args:
            shared_state: 当前任务的规范共享状态。实现应通过受控写入器或返回结果
                表达变更，不应任意覆盖其他节点拥有的状态路径。

        Returns:
            标准 Agent 结果，包含状态、结构化输出、错误和人工审核信息。
        """

        raise NotImplementedError
