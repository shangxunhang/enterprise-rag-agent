"""Backward-compatible name for the native Step 15 workflow engine."""

from __future__ import annotations

from copy import deepcopy

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.native_workflow_engine import NativeWorkflowEngine
from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema


class WorkflowExecutor(NativeWorkflowEngine):
    """Compatibility facade; new code depends on ``WorkflowEnginePort``.

    Pre-Step-15 tests and callers may still pass ``SharedStateSchema`` to
    ``run``.  The facade upgrades it to GraphState for execution and projects
    the stable shared fields back afterwards.
    """

    def run(
        self,
        workflow: WorkflowDefinitionSchema,
        shared_state: SharedStateSchema,
    ):
        if isinstance(shared_state, GraphStateSchema):
            return super().run(workflow, shared_state)

        graph_state = GraphStateSchema.model_validate(
            shared_state.model_dump(mode="python")
        )
        execution = self.execute(workflow, graph_state)
        for field_name in SharedStateSchema.model_fields:
            setattr(
                shared_state,
                field_name,
                deepcopy(getattr(graph_state, field_name)),
            )
        return execution.node_results
