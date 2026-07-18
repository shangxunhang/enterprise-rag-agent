"""Canonical state used by native and future LangGraph workflow engines."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.graph import GraphNodeExecutionRecordSchema


class GraphStateSchema(SharedStateSchema):
    """Framework-neutral graph state.

    It extends the already stable ``SharedStateSchema`` so existing Agents can
    keep their current signatures while the workflow engine gains revisioned,
    auditable graph semantics.
    """

    schema_version: str = "graph_state_v1"

    graph_revision: int = 0
    current_node_id: Optional[str] = None
    completed_node_ids: List[str] = Field(default_factory=list)
    node_history: List[GraphNodeExecutionRecordSchema] = Field(default_factory=list)

    workflow_engine_name: str = "native_workflow_engine"
    workflow_engine_version: str = "v1"
    graph_metadata: Dict[str, Any] = Field(default_factory=dict)
