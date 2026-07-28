"""把工作流状态迁移与具体节点执行逻辑隔离。

``WorkflowStateController`` 只负责在工作流开始、步骤开始、步骤结束和工作流结束时
更新共享状态。它不调用 Agent、不选择分支，也不决定重试策略，使状态写入规则可以
独立测试并在不同执行引擎之间复用。
"""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from agent.runtime.workflow_schema import WorkflowStepSchema
from core.runtime.clock import Clock, SystemClock
from schemas.agent import AgentResultSchema
from schemas.context import WorkflowStepStateSchema
from schemas.status import ExecutionStatus


class WorkflowStateController:
    """集中管理共享状态中的工作流和步骤生命周期字段。

    Attributes:
        writer: 受控状态写入器，负责同步规范字段和迁移期兼容视图。
        clock: 可注入时钟，用于生成稳定、可测试的步骤时间戳。
    """

    def __init__(
        self,
        *,
        writer: SharedStateWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        """创建工作流状态控制器并补齐默认依赖。

        Args:
            writer: 自定义状态写入器；未提供时创建默认 ``SharedStateWriter``。
            clock: 自定义时钟；未提供时使用系统时钟。
        """

        # 第一步：优先使用调用方注入的写入器，便于测试或替换状态写入策略。
        self.writer = writer or SharedStateWriter()
        # 第二步：使用可替换时钟，避免业务代码直接依赖当前系统时间。
        self.clock = clock or SystemClock()

    def start_workflow(self, state: SharedStateSchema) -> None:
        """把共享状态及其规范运行时上下文同时推进到运行态。

        Args:
            state: 即将开始执行的共享状态对象；该方法原地修改它。
        """

        # 第一步：更新共享状态顶层兼容状态，供现有调用方快速判断。
        state.status = ExecutionStatus.RUNNING
        # 第二步：同步更新 context_bundle 中的规范运行时状态，保持事实源一致。
        state.context_bundle.runtime.status = ExecutionStatus.RUNNING

    def start_step(
        self,
        state: SharedStateSchema,
        step: WorkflowStepSchema,
    ) -> WorkflowStepStateSchema:
        """创建并登记一个工作流步骤的首次运行状态。

        Args:
            state: 当前工作流共享状态。
            step: 即将执行的步骤定义。

        Returns:
            已写入共享状态的运行态步骤记录；调用方随后可在同一对象上补充终态。
        """

        # 第一步：从步骤定义复制稳定身份信息，并记录首次尝试和开始时间。
        step_state = WorkflowStepStateSchema(
            step_id=step.step_id,
            step_name=step.step_name,
            target_name=step.target_name,
            status=ExecutionStatus.RUNNING,
            started_at=self.clock.now_iso(),
            attempt=1,
        )

        # 第二步：通过受控写入器登记步骤状态，避免直接操作内部映射。
        self.writer.set_step_state(state, step_state)
        # 第三步：同步当前步骤名称，供轨迹、错误和任务状态记录使用。
        self.writer.set_current_step(state, step.step_name)
        return step_state

    def finish_step(
        self,
        state: SharedStateSchema,
        step_state: WorkflowStepStateSchema,
        result: AgentResultSchema,
    ) -> None:
        """根据 Agent 结果完成步骤状态并汇总错误。

        Args:
            state: 当前工作流共享状态。
            step_state: ``start_step`` 创建的步骤状态对象。
            result: 当前步骤目标 Agent 返回的标准结果。
        """

        # 第一步：先登记完整 Agent 结果，使后续节点能够按名称读取结构化输出。
        self.writer.add_agent_result(state, result)

        # 第二步：把 Agent 终态、结束时间和主错误复制到步骤生命周期记录。
        step_state.status = result.status
        step_state.finished_at = self.clock.now_iso()
        step_state.error = result.error

        # 第三步：重新写回已完成的步骤记录，覆盖之前的运行态快照。
        self.writer.set_step_state(state, step_state)

        # 第四步：若 Agent 返回错误，将其加入共享错误集合供最终状态判定和审计。
        if result.error is not None:
            self.writer.add_error(state, result.error)

    def finish_workflow(
        self,
        state: SharedStateSchema,
        status: ExecutionStatus,
    ) -> None:
        """把工作流顶层状态和规范运行时状态同步推进到给定终态。

        Args:
            state: 已结束执行的共享状态对象。
            status: 经执行器或业务门禁确定的最终规范状态。
        """

        # 第一步：更新顶层状态，兼容直接读取 state.status 的现有调用方。
        state.status = status
        # 第二步：同步规范 runtime 状态，保证 context_bundle 仍是可信事实源。
        state.context_bundle.runtime.status = status
