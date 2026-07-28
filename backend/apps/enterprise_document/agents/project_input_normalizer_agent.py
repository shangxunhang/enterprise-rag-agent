"""ProjectInput 规范化用例的 Agent 协议适配器。

业务转换全部位于 ``services.project_input_normalization``；本类只负责把共享状态交给用例，
再将成功输出或结构化失败映射为 ``AgentResultSchema``。因此阅读数据清洗规则时应继续进入
Use Case 和各个领域服务，而不是在 Agent 层寻找规则实现。
"""

from __future__ import annotations

from agent.base_agent import BaseAgent
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.services.project_input_normalization import (
    ProjectInputNormalizationUseCase,
)
from core.error_factory import ErrorFactory
from core.retry_policy import RetryDecisionPolicy, classify_failure
from core.runtime.clock import Clock, SystemClock
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


class ProjectInputNormalizerAgent(BaseAgent):
    """把原始项目输入规范化为后续检索与文档生成可依赖的统一事实结构。"""
    agent_name = "ProjectInputNormalizerAgent"
    agent_type = "sub_agent"

    def __init__(
        self,
        use_case: ProjectInputNormalizationUseCase | None = None,
        *,
        clock: Clock | None = None,
        error_factory: ErrorFactory | None = None,
        retry_policy: RetryDecisionPolicy | None = None,
    ) -> None:
        """注入规范化用例、统一时钟、错误工厂和失败重试策略。"""
        self.clock = clock or SystemClock()
        self.use_case = use_case or ProjectInputNormalizationUseCase(clock=self.clock)
        self.error_factory = error_factory or ErrorFactory(self.clock)
        self.retry_policy = retry_policy or RetryDecisionPolicy()

    def _now_iso(self) -> str:
        """兼容旧调用的统一时间入口。"""
        return self.clock.now_iso()

    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        """执行规范化用例并返回统一 Agent 结果。

        成功结果同时暴露规范化 ProjectInput、表格分析和结构化事实，供现有工作流兼容读取；
        缺失信息只触发人工复核建议，不把成功状态改成失败。异常则在最近事实源分类，写入
        共享状态错误列表，并根据类别返回可重试失败或终止失败。
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
            failure_category = classify_failure(exc)
            retryable = self.retry_policy.should_retry(failure_category)
            error = self.error_factory.create(
                error_code="PROJECT_INPUT_NORMALIZATION_FAILED",
                error_type=exc.__class__.__name__,
                message=str(exc),
                user_visible_message="项目输入不完整或格式不合法，无法启动文档生成流程。",
                recoverable=retryable,
                retryable=retryable,
                failure_category=failure_category,
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
                status=(
                    ExecutionStatus.RETRYABLE_FAILED
                    if retryable
                    else ExecutionStatus.FAILED
                ),
                result_type="project_input_normalization",
                result={},
                error=error,
                error_message=error.message,
                need_human_review=True,
            )
