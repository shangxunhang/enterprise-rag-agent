"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import List, Tuple

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateReader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema, TableAnalysisSchema
from .base import RuntimeBoundService


class SchemeInputService(RuntimeBoundService):
    def _read_inputs(
        self, shared_state: SharedStateSchema
    ) -> Tuple[ProjectInputSchema, TableAnalysisSchema, List[StructuredFactSchema]]:
        reader = SharedStateReader()
        normalizer_result = (
            reader.get_agent_result(shared_state, "ProjectInputNormalizerAgent")
            or reader.get_agent_result(shared_state, "FakeTableAgent")
            or {}
        )
        payload = normalizer_result.get("result") or {}
        raw_project_input = (
            payload.get("project_input")
            or shared_state.context_bundle.business.project_input
            or (shared_state.requirements or {}).get("project_input")
        )
        if not raw_project_input:
            raise ValueError("PROJECT_INPUT_REQUIRED: no ProjectInput found in workflow state")
        project_input = ProjectInputSchema.model_validate(raw_project_input)

        raw_analysis = payload.get("table_analysis") or {}
        table_analysis = TableAnalysisSchema.model_validate(raw_analysis)

        structured_facts = [
            StructuredFactSchema.model_validate(item)
            for item in (payload.get("structured_facts") or shared_state.structured_facts)
        ]
        return project_input, table_analysis, structured_facts
