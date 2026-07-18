"""Agent registry."""

from __future__ import annotations

from typing import Dict

from agent.base_agent import BaseAgent


class AgentRegistry:
    """Registry for all agents."""

    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if agent.agent_name in self._agents:
            raise ValueError(f"Agent already registered: {agent.agent_name}")
        self._agents[agent.agent_name] = agent

    def get(self, agent_name: str) -> BaseAgent:
        if agent_name not in self._agents:
            raise KeyError(f"Agent not found: {agent_name}")
        return self._agents[agent_name]

    def exists(self, agent_name: str) -> bool:
        return agent_name in self._agents

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())