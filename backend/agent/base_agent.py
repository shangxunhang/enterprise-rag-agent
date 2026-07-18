"""Base agent interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.agent import AgentResultSchema
from agent.runtime.shared_state_schema import SharedStateSchema


class BaseAgent(ABC):
    """Base class for all agents."""

    agent_name: str
    agent_type: str

    @abstractmethod
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """Run agent with shared state."""
        raise NotImplementedError