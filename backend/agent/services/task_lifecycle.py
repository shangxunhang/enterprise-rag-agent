"""把 Agent 最终结果映射为任务生命周期状态变更。

Supervisor 只需要提交运行中和终态结果，本服务再通过 ``TaskStateManager`` 端口调用
具体持久化实现。未配置任务管理器时服务退化为无副作用模式，适合纯内存测试或仅
需要 Agent 结果的调用路径。
"""

from __future__ import annotations

from typing import Optional

from contracts.task_state import TaskStateManager
from core.error_factory import ErrorFactory
from schemas.agent import AgentResultSchema
from schemas.failure import FailureCategory
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


class TaskLifecycleService:
    """集中处理任务从开始运行到各种终态的持久化映射。

    Attributes:
        task_manager: 可选任务状态端口；为空时所有状态写入均跳过。
        error_factory: 在失败结果缺少结构化错误时创建兜底错误。
    """

    def __init__(
        self,
        task_manager: Optional[TaskStateManager] = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        """创建任务生命周期服务。

        Args:
            task_manager: 具体任务状态管理器或 ``None``。
            error_factory: 自定义错误工厂；未提供时创建默认实现。
        """

        # 保存可选端口；服务不会自行创建文件或数据库持久化实现。
        self.task_manager = task_manager
        # 兜底错误仍使用统一工厂，避免手工拼装不完整 ErrorSchema。
        self.error_factory = error_factory or ErrorFactory()

    def mark_running(self, task: TaskSchema) -> None:
        """在已配置状态管理器时把任务标记为工作流已开始。

        Args:
            task: 即将进入 Supervisor/工作流执行的任务。
        """

        # 无持久化模式直接跳过；不把缺少可选 manager 当作运行错误。
        if self.task_manager is not None:
            self.task_manager.mark_running(task, current_step="workflow_started")

    def mark_terminal(self, task: TaskSchema, result: AgentResultSchema) -> None:
        """根据 Agent 最终状态写入对应任务终态。

        Args:
            task: 已完成或中断执行的任务。
            result: Supervisor 返回的标准最终结果。

        Raises:
            RuntimeError: 结果处于 ``PAUSED``，但注入的任务管理器没有提供
                ``mark_paused`` 扩展能力时抛出。
        """

        # 第一步：未配置持久化端口时，保留 Agent 结果但不执行状态副作用。
        if self.task_manager is None:
            return

        # 第二步：完整成功直接写入成功终态和最终结果。
        if result.status == ExecutionStatus.SUCCESS:
            self.task_manager.mark_success(task, result)

        # 第三步：部分成功单独持久化，不能降格成成功或失败。
        elif result.status == ExecutionStatus.PARTIAL_SUCCESS:
            self.task_manager.mark_partial_success(task, result)

        # 第四步：暂停是可恢复中间态，通过可选扩展方法保存人工中断信息。
        elif result.status == ExecutionStatus.PAUSED:
            mark_paused = getattr(self.task_manager, "mark_paused", None)
            if not callable(mark_paused):
                raise RuntimeError("task manager does not support paused workflows")
            mark_paused(task, result)

        else:
            # 第五步：优先保留最接近失败源创建的结构化错误。
            error = result.error

            # 第六步：只有工作流失败却没有结构化源错误时，才创建 UNKNOWN 兜底错误。
            if error is None:
                error = self.error_factory.create(
                    error_code="WORKFLOW_FAILED",
                    error_type="WorkflowFailure",
                    message=result.error_message or "workflow failed",
                    component="TaskLifecycleService",
                    failure_category=FailureCategory.UNKNOWN,
                    failed_node="SupervisorAgent",
                )

            # 第七步：取消必须使用独立终态，不能混入一般失败统计和重试判断。
            if result.status == ExecutionStatus.CANCELLED:
                self.task_manager.mark_cancelled(
                    task,
                    error=error,
                    result=result,
                )
            else:
                # 第八步：其他失败按结果状态明确传递是否可重试，并保留部分结果。
                self.task_manager.mark_failed(
                    task,
                    error=error,
                    retryable=result.status == ExecutionStatus.RETRYABLE_FAILED,
                    result=result,
                )
