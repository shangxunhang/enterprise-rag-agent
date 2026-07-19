# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：TaskLifecycleService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Task-state lifecycle port adapter."""

from __future__ import annotations

from typing import Optional

from contracts.task_state import TaskStateManager
from core.error_factory import ErrorFactory
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


# 阅读注释（类）：封装 任务 lifecycle 服务，封装一组可复用的业务能力。
class TaskLifecycleService:
    """封装 任务 lifecycle 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：初始化 TaskLifecycleService，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        task_manager: Optional[TaskStateManager] = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        """初始化 TaskLifecycleService，保存运行所需的依赖、配置或状态。

        参数:
            task_manager: 任务 管理器，具体约束请结合类型标注和调用方确认。
            error_factory: 错误 工厂，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ErrorFactory。
        """
        self.task_manager = task_manager
        self.error_factory = error_factory or ErrorFactory()

    # 阅读注释（函数）：处理 mark running 相关逻辑。
    def mark_running(self, task: TaskSchema) -> None:
        """处理 mark running 相关逻辑。

        参数:
            task: 待执行的任务对象。

        返回:
            None

        阅读提示:
            主要直接调用：self.task_manager.mark_running。
        """
        if self.task_manager is not None:
            self.task_manager.mark_running(task, current_step="workflow_started")

    # 阅读注释（函数）：处理 mark terminal 相关逻辑。
    def mark_terminal(self, task: TaskSchema, result: AgentResultSchema) -> None:
        """处理 mark terminal 相关逻辑。

        参数:
            task: 待执行的任务对象。
            result: 待处理的结果对象。

        返回:
            None

        阅读提示:
            主要直接调用：self.task_manager.mark_success, self.task_manager.mark_partial_success, self.error_factory.create, self.task_manager.mark_failed。
        """
        if self.task_manager is None:
            return
        if result.status == ExecutionStatus.SUCCESS:
            self.task_manager.mark_success(task, result)
        elif result.status == ExecutionStatus.PARTIAL_SUCCESS:
            self.task_manager.mark_partial_success(task, result)
        else:
            error = result.error or self.error_factory.create(
                error_code="WORKFLOW_FAILED",
                error_type="WorkflowFailure",
                message=result.error_message or "workflow failed",
                component="TaskLifecycleService",
                failed_node="SupervisorAgent",
            )
            self.task_manager.mark_failed(
                task,
                error=error,
                retryable=result.status == ExecutionStatus.RETRYABLE_FAILED,
                result=result,
            )
