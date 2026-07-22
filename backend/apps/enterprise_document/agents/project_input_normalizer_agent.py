# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：ProjectInputNormalizerAgent。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Agent adapter for project-input normalization.

All business transformations live in
``services.project_input_normalization``.  The agent only adapts the workflow
protocol to the application use case and maps failures to AgentResultSchema.
"""

from __future__ import annotations

from agent.base_agent import BaseAgent
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.services.project_input_normalization import (
    ProjectInputNormalizationUseCase,
)
from core.error_factory import ErrorFactory
from core.runtime.clock import Clock, SystemClock
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 项目 输入 normalizer Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。
class ProjectInputNormalizerAgent(BaseAgent):
    """封装 项目 输入 normalizer Agent，负责接收状态、调用工具或服务并返回统一 Agent 结果。"""
    agent_name = "ProjectInputNormalizerAgent"
    agent_type = "sub_agent"

    # 阅读注释（函数）：初始化 ProjectInputNormalizerAgent，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        use_case: ProjectInputNormalizationUseCase | None = None,
        *,
        clock: Clock | None = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        """初始化 ProjectInputNormalizerAgent，保存运行所需的依赖、配置或状态。

        参数:
            use_case: use case，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。
            error_factory: 错误 工厂，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：SystemClock, ProjectInputNormalizationUseCase, ErrorFactory。
        """
        self.clock = clock or SystemClock()
        self.use_case = use_case or ProjectInputNormalizationUseCase(clock=self.clock)
        self.error_factory = error_factory or ErrorFactory(self.clock)

    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def _now_iso(self) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：self.clock.now_iso。
        """
        return self.clock.now_iso()

    # 阅读注释（函数）：执行 ProjectInputNormalizerAgent 的主流程。
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """执行 ProjectInputNormalizerAgent 的主流程。

        参数:
            shared_state: shared 状态，具体约束请结合类型标注和调用方确认。

        返回:
            AgentResultSchema

        阅读提示:
            主要直接调用：self.use_case.execute, AgentResultSchema, project_input.model_dump, output.model_dump, output.table_analysis.model_dump, fact.model_dump, bool, self.error_factory.create。
        """
        try:
            output = self.use_case.execute(shared_state)
            project_input = output.project_input
            return AgentResultSchema(
                result_id=f"result_{shared_state.run_id}_project_input",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.SUCCESS,
                result_type="project_input_normalization",
                result={
                    "project_input": project_input.model_dump(),
                    "table_agent_output": output.model_dump(),
                    "table_analysis": output.table_analysis.model_dump(),
                    "structured_facts": [
                        fact.model_dump() for fact in output.structured_facts
                    ],
                },
                need_human_review=bool(project_input.missing_information),
                metadata={
                    "output_schema": "TableAgentOutputSchema",
                    "project_input_schema_version": project_input.schema_version,
                },
            )
        except Exception as exc:
            error = self.error_factory.create(
                error_code="PROJECT_INPUT_NORMALIZATION_FAILED",
                error_type=exc.__class__.__name__,
                message=str(exc),
                user_visible_message="项目输入不完整或格式不合法，无法启动文档生成流程。",
                recoverable=True,
                retryable=False,
                failed_node=self.agent_name,
                component=self.__class__.__name__,
                agent_name=self.agent_name,
                step_name=shared_state.current_step,
            )
            SharedStateWriter().add_error(shared_state, error)
            return AgentResultSchema(
                result_id=f"result_{shared_state.run_id}_project_input_failed",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=ExecutionStatus.FAILED,
                result_type="project_input_normalization",
                result={},
                error=error,
                error_message=error.message,
                need_human_review=True,
            )
