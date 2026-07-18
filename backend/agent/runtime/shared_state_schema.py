"""Shared workflow state with typed base contexts and runtime status."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.context import ContextBundleSchema, WorkflowStepStateSchema
from schemas.status import ExecutionStatus


class SharedStateSchema(SchemaBase):
    schema_version: str = "shared_state_v2"

    task_id: str
    run_id: str
    task_type: str
    user_input: str
    tenant_id: str = "default"

    task: Dict[str, Any] = Field(default_factory=dict)
    workflow: Dict[str, Any] = Field(default_factory=dict)
    requirements: Dict[str, Any] = Field(default_factory=dict)

    context_bundle: ContextBundleSchema

    # Compatibility views retained during migration. New code should prefer
    # context_bundle.
    contexts: Dict[str, Any] = Field(default_factory=dict)
    structured_facts: List[Dict[str, Any]] = Field(default_factory=list)

    agent_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    tool_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    workflow_step_states: Dict[str, WorkflowStepStateSchema] = Field(
        default_factory=dict
    )

    generated_outputs: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[WarningSchema] = Field(default_factory=list)
    errors: List[ErrorSchema] = Field(default_factory=list)

    status: ExecutionStatus = ExecutionStatus.PENDING
    current_step: Optional[str] = None
    final_result: Optional[Dict[str, Any]] = None

    state_version: int = 2
    created_at: str
    updated_at: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
