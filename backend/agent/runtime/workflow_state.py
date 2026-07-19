# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：WorkflowStateController。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Workflow state transitions isolated from execution logic."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from agent.runtime.workflow_schema import WorkflowStepSchema
from core.runtime.clock import Clock, SystemClock
from schemas.agent import AgentResultSchema
from schemas.context import WorkflowStepStateSchema
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 工作流 状态 controller，集中封装相关状态、依赖和行为。
class WorkflowStateController:
    """封装 工作流 状态 controller，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 WorkflowStateController，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        writer: SharedStateWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化 WorkflowStateController，保存运行所需的依赖、配置或状态。

        参数:
            writer: writer，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：SharedStateWriter, SystemClock。
        """
        self.writer = writer or SharedStateWriter()
        self.clock = clock or SystemClock()

    # 阅读注释（函数）：启动 工作流。
    def start_workflow(self, state: SharedStateSchema) -> None:
        """启动 工作流。

        参数:
            state: 工作流共享状态。

        返回:
            None
        """
        state.status = ExecutionStatus.RUNNING
        state.context_bundle.runtime.status = ExecutionStatus.RUNNING

    # 阅读注释（函数）：启动 step。
    def start_step(
        self,
        state: SharedStateSchema,
        step: WorkflowStepSchema,
    ) -> WorkflowStepStateSchema:
        """启动 step。

        参数:
            state: 工作流共享状态。
            step: step，具体约束请结合类型标注和调用方确认。

        返回:
            WorkflowStepStateSchema

        阅读提示:
            主要直接调用：WorkflowStepStateSchema, self.clock.now_iso, self.writer.set_step_state, self.writer.set_current_step。
        """
        step_state = WorkflowStepStateSchema(
            step_id=step.step_id,
            step_name=step.step_name,
            target_name=step.target_name,
            status=ExecutionStatus.RUNNING,
            started_at=self.clock.now_iso(),
            attempt=1,
        )
        self.writer.set_step_state(state, step_state)
        self.writer.set_current_step(state, step.step_name)
        return step_state

    # 阅读注释（函数）：处理 finish step 相关逻辑。
    def finish_step(
        self,
        state: SharedStateSchema,
        step_state: WorkflowStepStateSchema,
        result: AgentResultSchema,
    ) -> None:
        """处理 finish step 相关逻辑。

        参数:
            state: 工作流共享状态。
            step_state: step 状态，具体约束请结合类型标注和调用方确认。
            result: 待处理的结果对象。

        返回:
            None

        阅读提示:
            主要直接调用：self.writer.add_agent_result, self.clock.now_iso, self.writer.set_step_state, self.writer.add_error。
        """
        self.writer.add_agent_result(state, result)
        step_state.status = result.status
        step_state.finished_at = self.clock.now_iso()
        step_state.error = result.error
        self.writer.set_step_state(state, step_state)
        if result.error is not None:
            self.writer.add_error(state, result.error)

    # 阅读注释（函数）：处理 finish 工作流 相关逻辑。
    def finish_workflow(
        self,
        state: SharedStateSchema,
        status: ExecutionStatus,
    ) -> None:
        """处理 finish 工作流 相关逻辑。

        参数:
            state: 工作流共享状态。
            status: 状态，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        state.status = status
        state.context_bundle.runtime.status = status
