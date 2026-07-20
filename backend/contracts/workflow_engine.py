# =============================================================================
# 中文阅读说明：端口与协议定义模块，用于约束模块间依赖边界。
# 主要定义：WorkflowEnginePort。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Framework-neutral workflow engine port implemented by LangGraph runtime."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from schemas.graph import WorkflowEngineResultSchema


@runtime_checkable
class WorkflowEnginePort(Protocol):
    """封装 工作流 engine port，定义模块间调用契约，具体实现由适配器或插件提供。"""
    engine_name: str
    engine_version: str

    # 阅读注释（函数）：执行 WorkflowEnginePort。
    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """Execute one workflow against the canonical graph state."""
        ...
