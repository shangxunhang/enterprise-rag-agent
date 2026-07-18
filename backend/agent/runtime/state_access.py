"""Canonical read/write access to ``SharedStateSchema``.

The adapter keeps legacy compatibility fields synchronized while preventing
application services from scattering direct dictionary mutation throughout the
codebase.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.context import WorkflowStepStateSchema


class SharedStateReader:
    def project_input_candidates(self, state: SharedStateSchema) -> list[Any]:
        return [
            state.context_bundle.business.project_input,
            (state.requirements or {}).get("project_input"),
            (state.task or {}).get("project_input"),
            (state.contexts or {}).get("project_input"),
        ]

    def get_agent_result(
        self,
        state: SharedStateSchema,
        agent_name: str,
    ) -> Optional[Dict[str, Any]]:
        return state.agent_results.get(agent_name)


class SharedStateWriter:
    def set_project_input_normalization(
        self,
        state: SharedStateSchema,
        *,
        project_input: Dict[str, Any],
        table_agent_output: Dict[str, Any],
        structured_facts: list[Dict[str, Any]],
        source_materials: list[Dict[str, Any]],
        missing_information: list[str],
        conflicting_information: list[str],
        manual_boundaries: list[Dict[str, Any]],
    ) -> None:
        state.context_bundle.business.project_input = project_input
        state.context_bundle.business.source_materials = source_materials
        state.context_bundle.business.missing_information = missing_information
        state.context_bundle.business.conflicting_information = conflicting_information
        state.context_bundle.business.manual_boundaries = manual_boundaries
        state.structured_facts = structured_facts

        # Compatibility projection. New code should use context_bundle.
        state.contexts["project_input"] = project_input
        state.contexts["table_agent_output"] = table_agent_output

    def set_step_state(
        self,
        state: SharedStateSchema,
        step_state: WorkflowStepStateSchema,
    ) -> None:
        state.workflow_step_states[step_state.step_id] = step_state
        state.context_bundle.runtime.workflow_step_states[step_state.step_id] = step_state

    def set_current_step(self, state: SharedStateSchema, step_name: str) -> None:
        state.current_step = step_name
        state.context_bundle.runtime.current_step = step_name

    def add_agent_result(
        self,
        state: SharedStateSchema,
        result: AgentResultSchema,
    ) -> None:
        state.agent_results[result.agent_name] = result.model_dump()

    def set_tool_result(
        self,
        state: SharedStateSchema,
        tool_call_id: str,
        result: Dict[str, Any],
    ) -> None:
        state.tool_results[tool_call_id] = result

    def add_error(self, state: SharedStateSchema, error: ErrorSchema) -> None:
        state.errors.append(error)
        if error not in state.context_bundle.runtime.errors:
            state.context_bundle.runtime.errors.append(error)

    def add_errors(
        self,
        state: SharedStateSchema,
        errors: Iterable[ErrorSchema],
    ) -> None:
        for error in errors:
            self.add_error(state, error)

    def set_evidence_context(
        self,
        state: SharedStateSchema,
        *,
        query: str,
        context_text: str,
        retrieved_chunks: list[Dict[str, Any]],
        citations: list[Dict[str, Any]],
        used_doc_ids: list[str],
        evidence_sufficient: Optional[bool] = None,
        evidence_contract: Optional[Dict[str, Any]] = None,
        evidence_available: Optional[bool] = None,
        assessment_status: str = "not_assessed",
    ) -> None:
        evidence = state.context_bundle.evidence
        evidence.query = query
        evidence.contract = dict(evidence_contract or {})
        evidence.context_text = context_text
        evidence.retrieved_chunks = retrieved_chunks
        evidence.citations = citations
        evidence.used_doc_ids = used_doc_ids
        evidence.evidence_available = (
            bool(retrieved_chunks) if evidence_available is None else evidence_available
        )
        evidence.assessment_status = assessment_status
        evidence.evidence_sufficient = evidence_sufficient
        evidence.metadata["context_is_projection"] = bool(evidence.contract)

    def initialize_generation(
        self,
        state: SharedStateSchema,
        *,
        document_id: str,
        document_title: str,
        required_sections: list[str],
    ) -> None:
        generation = state.context_bundle.generation
        generation.document_id = document_id
        generation.document_title = document_title
        generation.required_sections = required_sections

    def set_current_section(
        self,
        state: SharedStateSchema,
        *,
        section_id: str,
        section_title: str,
    ) -> None:
        generation = state.context_bundle.generation
        generation.current_section_id = section_id
        generation.current_section_title = section_title

    def add_generated_section(self, state: SharedStateSchema, section_id: str) -> None:
        state.context_bundle.generation.generated_section_ids.append(section_id)

    def set_scheme_outputs(
        self,
        state: SharedStateSchema,
        *,
        scheme_writer_input: Dict[str, Any],
        scheme_writer_output: Dict[str, Any],
        rag_tool_output: Dict[str, Any],
    ) -> None:
        # Compatibility projection retained for v1 callers.
        state.contexts["scheme_writer_input"] = scheme_writer_input
        state.contexts["scheme_writer_output"] = scheme_writer_output
        state.contexts["rag_tool_output"] = rag_tool_output

    def set_final_result(
        self,
        state: SharedStateSchema,
        result: Dict[str, Any],
    ) -> None:
        state.final_result = result

