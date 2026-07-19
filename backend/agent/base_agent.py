# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：BaseAgent。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Base agent interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.agent import AgentResultSchema
from agent.runtime.shared_state_schema import SharedStateSchema


# 阅读注释（类）：封装 base Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class BaseAgent(ABC):
    """Base class for all agents."""

    agent_name: str
    agent_type: str

    # 阅读注释（函数）：执行 BaseAgent 的主流程。
    @abstractmethod
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """Run agent with shared state."""
        raise NotImplementedError