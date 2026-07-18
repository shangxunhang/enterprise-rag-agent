"""Read and validate caller-provided project input from workflow state."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateReader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


class ProjectInputReader:
    def __init__(self, state_reader: SharedStateReader | None = None) -> None:
        self.state_reader = state_reader or SharedStateReader()

    def read(self, state: SharedStateSchema) -> ProjectInputSchema:
        for candidate in self.state_reader.project_input_candidates(state):
            if isinstance(candidate, ProjectInputSchema):
                return candidate
            if hasattr(candidate, "model_dump"):
                candidate = candidate.model_dump()
            if isinstance(candidate, dict) and candidate:
                return ProjectInputSchema.model_validate(candidate)
        raise ValueError(
            "PROJECT_INPUT_REQUIRED: caller must provide a validated "
            "ProjectInputSchema; the workflow no longer injects demo business facts."
        )
