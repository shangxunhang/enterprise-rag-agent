"""Workflow engine port implemented by native and future LangGraph adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from schemas.graph import WorkflowEngineResultSchema


@runtime_checkable
class WorkflowEnginePort(Protocol):
    engine_name: str
    engine_version: str

    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """Execute one workflow against the canonical graph state."""
        ...
