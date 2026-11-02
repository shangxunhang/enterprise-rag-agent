"""提供进程内 Agent 实例注册与按名称查找能力。

注册表只管理已经由装配层创建的 Agent 实例，不负责构造、启动或销毁 Agent，也不
执行工作流路由。工作流步骤通过稳定 ``agent_name`` 获取实例，从而与具体类解耦。
"""

from __future__ import annotations

from typing import Dict

from agent.base_agent import BaseAgent


class AgentRegistry:
    """保存当前运行时可用的 Agent 实例。

    Attributes:
        _agents: 以稳定 Agent 名称为键的内部实例映射。
    """

    def __init__(self) -> None:
        """创建空注册表。"""

        # 注册表按实例保存，后续 get 返回装配阶段已经配置好的同一对象。
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """登记一个尚未占用名称的 Agent 实例。

        Args:
            agent: 已完成依赖装配且具有稳定 ``agent_name`` 的 Agent。

        Raises:
            ValueError: 相同名称已经登记时抛出，防止后注册实例静默覆盖前者。
        """

        # 第一步：以名称作为唯一键检查冲突，保证工作流解析结果确定。
        if agent.agent_name in self._agents:
            raise ValueError(f"Agent already registered: {agent.agent_name}")

        # 第二步：保存调用方已构造的实例；注册表不改变其生命周期。
        self._agents[agent.agent_name] = agent

    def get(self, agent_name: str) -> BaseAgent:
        """按稳定名称取得已注册 Agent。

        Args:
            agent_name: 工作流步骤或调用方请求的 Agent 名称。

        Returns:
            装配阶段登记的 Agent 实例。

        Raises:
            KeyError: 名称尚未注册时抛出，避免返回 ``None`` 后在远端调用点失败。
        """

        # 先显式检查并提供领域化错误文本，再返回实例。
        if agent_name not in self._agents:
            raise KeyError(f"Agent not found: {agent_name}")
        return self._agents[agent_name]

    def exists(self, agent_name: str) -> bool:
        """判断给定 Agent 名称是否已注册。"""

        return agent_name in self._agents

    def list_agents(self) -> list[str]:
        """按注册顺序返回当前全部 Agent 名称。"""

        # 返回新列表，避免调用方接触内部字典视图。
        return list(self._agents.keys())
