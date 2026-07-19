# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：ProjectInputNormalizationUseCase。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Application use case for project-input normalization."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.table_agent_schema import TableAgentOutputSchema
from core.runtime.clock import Clock, SystemClock
from schemas.status import ExecutionStatus
from .fact_extractor import StructuredFactExtractor
from .input_reader import ProjectInputReader
from .table_analysis_service import TableAnalysisBuilder


# 阅读注释（类）：封装 项目 输入 normalization use case，集中封装相关状态、依赖和行为。
class ProjectInputNormalizationUseCase:
    """封装 项目 输入 normalization use case，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ProjectInputNormalizationUseCase，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        reader: ProjectInputReader | None = None,
        table_builder: TableAnalysisBuilder | None = None,
        fact_extractor: StructuredFactExtractor | None = None,
        state_writer: SharedStateWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化 ProjectInputNormalizationUseCase，保存运行所需的依赖、配置或状态。

        参数:
            reader: reader，具体约束请结合类型标注和调用方确认。
            table_builder: table builder，具体约束请结合类型标注和调用方确认。
            fact_extractor: fact extractor，具体约束请结合类型标注和调用方确认。
            state_writer: 状态 writer，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ProjectInputReader, TableAnalysisBuilder, StructuredFactExtractor, SharedStateWriter, SystemClock。
        """
        self.reader = reader or ProjectInputReader()
        self.table_builder = table_builder or TableAnalysisBuilder()
        self.fact_extractor = fact_extractor or StructuredFactExtractor()
        self.state_writer = state_writer or SharedStateWriter()
        self.clock = clock or SystemClock()

    # 阅读注释（函数）：执行 ProjectInputNormalizationUseCase。
    def execute(self, state: SharedStateSchema) -> TableAgentOutputSchema:
        """执行 ProjectInputNormalizationUseCase。

        参数:
            state: 工作流共享状态。

        返回:
            TableAgentOutputSchema

        阅读提示:
            主要直接调用：self.reader.read, self.clock.now_iso, self.table_builder.build, self.fact_extractor.extract, TableAgentOutputSchema, self.state_writer.set_project_input_normalization, project_input.model_dump, output.model_dump。
        """
        project_input = self.reader.read(state)
        created_at = self.clock.now_iso()
        table_analysis = self.table_builder.build(project_input)
        structured_facts = self.fact_extractor.extract(state, project_input, created_at)
        output = TableAgentOutputSchema(
            task_id=state.task_id,
            run_id=state.run_id,
            status=ExecutionStatus.SUCCESS,
            project_input=project_input,
            table_analysis=table_analysis,
            structured_facts=structured_facts,
            extra={
                "contract": "TableAgentOutputSchema",
                "input_contract": "ProjectInputSchema",
                "input_mode": "caller_supplied",
            },
        )
        self.state_writer.set_project_input_normalization(
            state,
            project_input=project_input.model_dump(),
            table_agent_output=output.model_dump(),
            structured_facts=[fact.model_dump() for fact in structured_facts],
            source_materials=[item.model_dump() for item in project_input.source_materials],
            missing_information=list(project_input.missing_information),
            conflicting_information=list(project_input.conflicting_information),
            manual_boundaries=[item.model_dump() for item in project_input.manual_boundaries],
        )
        return output
