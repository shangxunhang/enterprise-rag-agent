"""Agent adapter for project-input normalization.

All business transformations live in
``services.project_input_normalization``.  The agent only adapts the workflow
protocol to the application use case and maps failures to AgentResultSchema.
"""

from __future__ import annotations

from agent.base_agent import BaseAgent
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import (
    StructuredFactSchema,
    TableAnalysisSchema,
)
from apps.enterprise_document.services.project_input_normalization import (
    ProjectInputNormalizationUseCase,
)
from core.error_factory import ErrorFactory
from core.runtime.clock import Clock, SystemClock
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus


class ProjectInputNormalizerAgent(BaseAgent):
    agent_name = "ProjectInputNormalizerAgent"
    agent_type = "sub_agent"

    def __init__(
        self,
        use_case: ProjectInputNormalizationUseCase | None = None,
        *,
        clock: Clock | None = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.use_case = use_case or ProjectInputNormalizationUseCase(clock=self.clock)
        self.error_factory = error_factory or ErrorFactory(self.clock)

    # Compatibility methods retained for callers/tests that used the old
    # private API. They delegate to isolated services and contain no logic.
    def _now_iso(self) -> str:
        return self.clock.now_iso()

    def _read_project_input(self, state: SharedStateSchema) -> ProjectInputSchema:
        return self.use_case.reader.read(state)

    def _hardware_summary(self, project_input: ProjectInputSchema) -> str:
        return self.use_case.table_builder.summaries.hardware_summary(project_input)

    def _organization_summary(self, project_input: ProjectInputSchema) -> str:
        return self.use_case.fact_extractor.summaries.organization_summary(project_input)

    def _build_table_analysis(self, project_input: ProjectInputSchema) -> TableAnalysisSchema:
        return self.use_case.table_builder.build(project_input)

    def _build_structured_facts(
        self,
        state: SharedStateSchema,
        project_input: ProjectInputSchema,
        created_at: str,
    ) -> list[StructuredFactSchema]:
        return self.use_case.fact_extractor.extract(state, project_input, created_at)

    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
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
