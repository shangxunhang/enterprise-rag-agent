# =============================================================================
# 中文阅读说明：端口与协议定义模块，用于约束模块间依赖边界。
# 主要定义：TaskStateManager。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Task lifecycle port used by application services."""
from __future__ import annotations

from typing import Protocol

from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.task import TaskSchema
from schemas.task_state import TaskStateRecordSchema


# 阅读注释（类）：封装 任务 状态 管理器，集中封装相关状态、依赖和行为。
class TaskStateManager(Protocol):
    """封装 任务 状态 管理器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：创建 任务。
    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema: ...
    # 阅读注释（函数）：处理 mark running 相关逻辑。
    def mark_running(self, task: TaskSchema, current_step: str = "workflow_started") -> TaskStateRecordSchema: ...
    # 阅读注释（函数）：处理 mark success 相关逻辑。
    def mark_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema: ...
    # 阅读注释（函数）：处理 mark partial success 相关逻辑。
    def mark_partial_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema: ...
    # 阅读注释（函数）：处理 mark failed 相关逻辑。
    def mark_failed(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_failed",
        retryable: bool = False,
        result: AgentResultSchema | None = None,
    ) -> TaskStateRecordSchema: ...
    # 阅读注释（函数）：获取 任务。
    def get_task(self, task_id: str) -> TaskStateRecordSchema: ...
