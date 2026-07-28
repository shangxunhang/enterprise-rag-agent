"""定义应用服务访问任务生命周期状态时依赖的端口。

应用层通过该协议创建任务状态、推进终态并读取最新记录，而不直接依赖 JSONL、
数据库或其他具体持久化实现。每次状态变更都返回规范化状态记录，便于调用方立即
写入响应、日志或运行报告。
"""

from __future__ import annotations

from typing import Protocol

from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.task import TaskSchema
from schemas.task_state import TaskStateRecordSchema


class TaskStateManager(Protocol):
    """声明任务状态管理器必须支持的完整生命周期操作。"""

    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema:
        """为新任务创建初始状态记录。

        Args:
            task: 已完成业务输入规范化的任务对象。

        Returns:
            新建后的任务状态记录，通常处于创建态或待执行态。
        """

        ...

    def mark_running(
        self,
        task: TaskSchema,
        current_step: str = "workflow_started",
    ) -> TaskStateRecordSchema:
        """把任务推进到运行态并记录当前步骤。

        Args:
            task: 正在执行的任务。
            current_step: 用于可观测性和恢复判断的当前流程步骤名称。

        Returns:
            更新后的运行态记录。
        """

        ...

    def mark_success(
        self,
        task: TaskSchema,
        result: AgentResultSchema,
        current_step: str = "workflow_finished",
    ) -> TaskStateRecordSchema:
        """把任务标记为完全成功并保存最终 Agent 结果。

        Args:
            task: 已完成执行的任务。
            result: 完整成功的最终结果。
            current_step: 记录终态所对应的流程步骤。

        Returns:
            成功终态记录。
        """

        ...

    def mark_partial_success(
        self,
        task: TaskSchema,
        result: AgentResultSchema,
        current_step: str = "workflow_finished",
    ) -> TaskStateRecordSchema:
        """把任务标记为部分成功并保留已经完成的有效结果。

        Args:
            task: 已结束但未完全满足目标的任务。
            result: 包含已完成部分以及降级或缺失信息的最终结果。
            current_step: 记录部分成功发生时的流程步骤。

        Returns:
            部分成功终态记录。
        """

        ...

    def mark_failed(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_failed",
        retryable: bool = False,
        result: AgentResultSchema | None = None,
    ) -> TaskStateRecordSchema:
        """记录任务失败，并区分可重试与不可重试失败。

        Args:
            task: 执行失败的任务。
            error: 已归一化的失败原因。
            current_step: 失败发生时所在的流程步骤。
            retryable: 是否允许上层根据策略再次执行。
            result: 失败前已经产生的可保留部分结果；没有时为 ``None``。

        Returns:
            ``failed`` 或 ``retryable_failed`` 终态记录。
        """

        ...

    def mark_cancelled(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_cancelled",
        result: AgentResultSchema | None = None,
    ) -> TaskStateRecordSchema:
        """记录任务被主动取消后的终态。

        Args:
            task: 被取消的任务。
            error: 描述取消来源和原因的标准错误对象。
            current_step: 取消发生时所在的流程步骤。
            result: 取消前已经生成且需要保留的部分结果。

        Returns:
            取消终态记录。
        """

        ...

    def get_task(self, task_id: str) -> TaskStateRecordSchema:
        """读取指定任务当前最新的状态记录。

        Args:
            task_id: 要查询的业务任务标识。

        Returns:
            该任务的最新规范化状态记录。
        """

        ...
