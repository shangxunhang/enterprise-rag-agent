# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：AgentRegistry。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Agent registry."""

from __future__ import annotations

from typing import Dict

from agent.base_agent import BaseAgent


# 阅读注释（类）：封装 Agent 注册表，集中封装相关状态、依赖和行为。
class AgentRegistry:
    """Registry for all agents."""

    # 阅读注释（函数）：初始化 AgentRegistry，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 AgentRegistry，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self._agents: Dict[str, BaseAgent] = {}

    # 阅读注释（函数）：注册 AgentRegistry。
    def register(self, agent: BaseAgent) -> None:
        """注册 AgentRegistry。

        参数:
            agent: Agent，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError。
        """
        if agent.agent_name in self._agents:
            raise ValueError(f"Agent already registered: {agent.agent_name}")
        self._agents[agent.agent_name] = agent

    # 阅读注释（函数）：获取 AgentRegistry。
    def get(self, agent_name: str) -> BaseAgent:
        """获取 AgentRegistry。

        参数:
            agent_name: Agent 名称，具体约束请结合类型标注和调用方确认。

        返回:
            BaseAgent

        阅读提示:
            主要直接调用：KeyError。
        """
        if agent_name not in self._agents:
            raise KeyError(f"Agent not found: {agent_name}")
        return self._agents[agent_name]

    # 阅读注释（函数）：处理 exists 相关逻辑。
    def exists(self, agent_name: str) -> bool:
        """处理 exists 相关逻辑。

        参数:
            agent_name: Agent 名称，具体约束请结合类型标注和调用方确认。

        返回:
            bool
        """
        return agent_name in self._agents

    # 阅读注释（函数）：列出 agents。
    def list_agents(self) -> list[str]:
        """列出 agents。

        返回:
            list[str]

        阅读提示:
            主要直接调用：list, self._agents.keys。
        """
        return list(self._agents.keys())