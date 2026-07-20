# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：_stable_hash、GraphNodeInputSchema、GraphStateDeltaSchema、GraphNodeOutputSchema、GraphNodeExecutionRecordSchema、WorkflowEngineResultSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Graph-state and workflow-node execution contracts.

These contracts are framework-neutral.  The LangGraph workflow engine exchanges these schemas through a framework-neutral
port so business Agents remain independent from the orchestration library.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .agent import AgentResultSchema
from .common import ErrorSchema, SchemaBase, WarningSchema
from .status import ExecutionStatus


# 阅读注释（函数）：处理 stable hash 相关逻辑。
def _stable_hash(value: Any) -> str:
    """处理 stable hash 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：json.dumps, hexdigest, hashlib.sha256, payload.encode。
    """
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 阅读注释（类）：封装 graph node 输入 Schema，定义跨模块传递的数据结构与字段约束。
class GraphNodeInputSchema(SchemaBase):
    """Declared subset of graph state supplied to one workflow node."""

    schema_version: str = "graph_node_input_v1"

    node_id: str
    node_name: str
    node_type: str
    target_name: str

    workflow_id: str
    workflow_version: str
    task_id: str
    run_id: str

    state_revision: int
    declared_read_keys: List[str] = Field(default_factory=list)
    values: Dict[str, Any] = Field(default_factory=dict)
    missing_keys: List[str] = Field(default_factory=list)
    input_sha256: str

    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 hash。
    @model_validator(mode="after")
    def validate_hash(self) -> "GraphNodeInputSchema":
        """校验 hash。

        返回:
            'GraphNodeInputSchema'

        阅读提示:
            主要直接调用：_stable_hash, ValueError, model_validator。
        """
        actual = _stable_hash(
            {
                "node_id": self.node_id,
                "workflow_id": self.workflow_id,
                "state_revision": self.state_revision,
                "declared_read_keys": self.declared_read_keys,
                "values": self.values,
                "missing_keys": self.missing_keys,
            }
        )
        if actual != self.input_sha256:
            raise ValueError("input_sha256 does not match graph-node input")
        return self


# 阅读注释（类）：封装 graph 状态 delta Schema，定义跨模块传递的数据结构与字段约束。
class GraphStateDeltaSchema(SchemaBase):
    """Deterministic state update emitted by a workflow node.

    ``set_values`` is keyed by top-level GraphState fields.  ``changed_paths``
    retains fine-grained audit paths while the top-level replacement keeps the
    v1 applier simple and fully validated by Pydantic.
    """

    schema_version: str = "graph_state_delta_v1"

    node_id: str
    base_revision: int
    next_revision: int

    set_values: Dict[str, Any] = Field(default_factory=dict)
    changed_paths: List[str] = Field(default_factory=list)
    declared_write_keys: List[str] = Field(default_factory=list)
    declared_write_paths: List[str] = Field(default_factory=list)
    observed_write_roots: List[str] = Field(default_factory=list)

    state_sha256_before: str
    state_sha256_after: str
    delta_sha256: str

    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 delta。
    @model_validator(mode="after")
    def validate_delta(self) -> "GraphStateDeltaSchema":
        """校验 delta。

        返回:
            'GraphStateDeltaSchema'

        阅读提示:
            主要直接调用：ValueError, _stable_hash, model_validator。
        """
        if self.next_revision != self.base_revision + 1:
            raise ValueError("next_revision must equal base_revision + 1")
        actual = _stable_hash(
            {
                "node_id": self.node_id,
                "base_revision": self.base_revision,
                "next_revision": self.next_revision,
                "set_values": self.set_values,
                "changed_paths": self.changed_paths,
                "declared_write_keys": self.declared_write_keys,
                "declared_write_paths": self.declared_write_paths,
                "observed_write_roots": self.observed_write_roots,
                "state_sha256_before": self.state_sha256_before,
                "state_sha256_after": self.state_sha256_after,
            }
        )
        if actual != self.delta_sha256:
            raise ValueError("delta_sha256 does not match graph-state delta")
        return self


# 阅读注释（类）：封装 graph node 输出 Schema，定义跨模块传递的数据结构与字段约束。
class GraphNodeOutputSchema(SchemaBase):
    """Canonical output of one workflow node."""

    schema_version: str = "graph_node_output_v1"

    node_id: str
    node_name: str
    node_type: str
    target_name: str

    status: ExecutionStatus
    result: AgentResultSchema
    state_delta: GraphStateDeltaSchema

    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None

    warnings: List[WarningSchema] = Field(default_factory=list)
    error: Optional[ErrorSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 结果 状态。
    @model_validator(mode="after")
    def validate_result_status(self) -> "GraphNodeOutputSchema":
        """校验 结果 状态。

        返回:
            'GraphNodeOutputSchema'

        阅读提示:
            主要直接调用：ValueError, model_validator。
        """
        if self.status != self.result.status:
            raise ValueError("node output status must match AgentResult status")
        return self


# 阅读注释（类）：封装 graph node execution 记录 Schema，定义跨模块传递的数据结构与字段约束。
class GraphNodeExecutionRecordSchema(SchemaBase):
    """Bounded graph history retained inside GraphState."""

    schema_version: str = "graph_node_execution_record_v1"

    node_id: str
    node_name: str
    target_name: str
    status: ExecutionStatus
    input_sha256: str
    delta_sha256: str
    base_revision: int
    next_revision: int
    changed_paths: List[str] = Field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 工作流 engine 结果 Schema，定义跨模块传递的数据结构与字段约束。
class WorkflowEngineResultSchema(SchemaBase):
    """Framework-neutral result returned by a workflow engine."""

    schema_version: str = "workflow_engine_result_v1"

    engine_name: str
    engine_version: str
    workflow_id: str
    workflow_version: str
    task_id: str
    run_id: str

    status: ExecutionStatus
    node_inputs: List[GraphNodeInputSchema] = Field(default_factory=list)
    node_outputs: List[GraphNodeOutputSchema] = Field(default_factory=list)
    node_results: List[AgentResultSchema] = Field(default_factory=list)
    completed_node_ids: List[str] = Field(default_factory=list)

    initial_revision: int = 0
    final_revision: int = 0
    final_state_sha256: str

    error: Optional[ErrorSchema] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 阅读注释（函数）：校验 counts。
    @model_validator(mode="after")
    def validate_counts(self) -> "WorkflowEngineResultSchema":
        """校验 counts。

        返回:
            'WorkflowEngineResultSchema'

        阅读提示:
            主要直接调用：len, ValueError, model_validator。
        """
        if len(self.node_outputs) != len(self.node_results):
            raise ValueError("node_outputs and node_results must have equal length")
        if self.completed_node_ids != [item.node_id for item in self.node_outputs]:
            raise ValueError("completed_node_ids must follow node output order")
        if self.final_revision < self.initial_revision:
            raise ValueError("final_revision cannot be less than initial_revision")
        return self


stable_graph_hash = _stable_hash
