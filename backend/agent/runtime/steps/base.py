"""Workflow step handler contract."""

from __future__ import annotations

from typing import Protocol

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowStepSchema
from schemas.agent import AgentResultSchema


class WorkflowStepHandler(Protocol):
    step_type: str

    def execute(
        self,
        step: WorkflowStepSchema,
        state: SharedStateSchema,
    ) -> AgentResultSchema:
        ...
