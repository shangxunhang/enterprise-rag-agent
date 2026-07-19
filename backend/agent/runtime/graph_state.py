# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：GraphStateSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Canonical state used by native and future LangGraph workflow engines."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.graph import GraphNodeExecutionRecordSchema


# 阅读注释（类）：封装 graph 状态 Schema，定义跨模块传递的数据结构与字段约束。
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
