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


class ProjectInputNormalizationUseCase:
    def __init__(
        self,
        *,
        reader: ProjectInputReader | None = None,
        table_builder: TableAnalysisBuilder | None = None,
        fact_extractor: StructuredFactExtractor | None = None,
        state_writer: SharedStateWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.reader = reader or ProjectInputReader()
        self.table_builder = table_builder or TableAnalysisBuilder()
        self.fact_extractor = fact_extractor or StructuredFactExtractor()
        self.state_writer = state_writer or SharedStateWriter()
        self.clock = clock or SystemClock()

    def execute(self, state: SharedStateSchema) -> TableAgentOutputSchema:
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
