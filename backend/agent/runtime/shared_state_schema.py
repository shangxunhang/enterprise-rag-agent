# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：SharedStateSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Shared workflow state with typed base contexts and runtime status."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.context import ContextBundleSchema, WorkflowStepStateSchema
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 shared 状态 Schema，定义跨模块传递的数据结构与字段约束。
class SharedStateSchema(SchemaBase):
    """封装 shared 状态 Schema，定义跨模块传递的数据结构与字段约束。"""
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

    # Compatibility views retained during migration. ``context_bundle`` is the
    # canonical source of truth for new business/evidence/generation/runtime
    # state. Compatibility views are one-way projections owned by
    # SharedStateWriter; new production code must not write them directly or
    # treat them as an authority that can overwrite canonical state.
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
