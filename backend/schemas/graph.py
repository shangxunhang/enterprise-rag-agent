"""定义框架无关的图状态、节点执行和工作流引擎结果契约。

LangGraph 实现通过这些 Schema 与业务 Agent 交换受控输入、状态增量和执行结果；
业务层不接触框架内部状态对象。输入、增量和最终状态都使用确定性哈希支持审计、
检查点恢复和重放一致性校验。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from .agent import AgentResultSchema
from .common import ErrorSchema, SchemaBase, WarningSchema
from .status import ExecutionStatus


def _stable_hash(value: Any) -> str:
    """为 JSON-like 数据计算键顺序无关的稳定 SHA-256 哈希。

    Args:
        value: 可由 ``json.dumps`` 序列化的数据；非标准对象通过 ``str`` 兜底编码。

    Returns:
        规范 JSON UTF-8 字节的 SHA-256 十六进制摘要。

    该哈希用于一致性检查，不是签名或访问控制机制。进入哈希的数据必须先完成脱敏，
    因为摘要并不会自动消除低熵敏感信息的推断风险。
    """

    # 第一步：按键排序、使用紧凑分隔符并保留中文，生成确定性 JSON 文本。
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    # 第二步：对 UTF-8 字节计算 SHA-256，供输入、增量和状态一致性校验复用。
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GraphNodeInputSchema(SchemaBase):
    """表示当前节点被允许读取的 GraphState 投影。

    节点输入只包含步骤声明的读取键，不包含完整共享状态；``missing_keys`` 明确记录
    未解析值，``input_sha256`` 则绑定节点、工作流、状态修订和实际输入内容。
    """

    # 当前节点输入契约版本。
    schema_version: str = "graph_node_input_v1"

    # 当前节点稳定标识。
    node_id: str
    # 当前节点可读名称。
    node_name: str
    # 节点执行类型，例如 agent、tool 或 rule_check。
    node_type: str
    # 节点将调用的 Agent、工具或组件名称。
    target_name: str

    # 所属业务工作流标识。
    workflow_id: str
    # 所属业务工作流版本。
    workflow_version: str
    # 所属业务任务标识。
    task_id: str
    # 所属单次运行标识。
    run_id: str

    # 投影输入时真实 GraphState 的受控提交修订号。
    state_revision: int
    # 工作流步骤声明的逻辑读取键，保留声明顺序。
    declared_read_keys: List[str] = Field(default_factory=list)
    # 读取键到深拷贝值的映射；缺失键也以 ``None`` 占位。
    values: Dict[str, Any] = Field(default_factory=dict)
    # 未能从状态解析的读取键集合。
    missing_keys: List[str] = Field(default_factory=list)
    # 绑定本节点输入语义的稳定哈希。
    input_sha256: str

    # 投影模式、状态 Schema 版本等输入元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_hash(self) -> "GraphNodeInputSchema":
        """重新计算节点输入哈希并验证载荷未漂移。

        Returns:
            哈希一致的当前节点输入。

        Raises:
            ValueError: 实际哈希与 ``input_sha256`` 不一致时抛出。
        """

        # 第一步：只使用创建输入时约定的稳定字段重建哈希载荷。
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

        # 第二步：任何字段变化都会使哈希失配，阻止节点消费被修改的投影。
        if actual != self.input_sha256:
            raise ValueError("input_sha256 does not match graph-node input")

        return self


class GraphStateDeltaSchema(SchemaBase):
    """表示一个节点产生的确定性、受写入契约约束的状态增量。

    ``set_values`` 以 GraphState 顶层字段为键执行整体替换，保持提交器简单且能通过
    Pydantic 验证完整状态；``changed_paths`` 仍保留叶子级变更路径，用于权限校验和
    审计。每个增量必须把修订号恰好推进一位。
    """

    # 当前状态增量契约版本。
    schema_version: str = "graph_state_delta_v1"

    # 产生增量的节点标识。
    node_id: str
    # 节点执行前真实状态的受控修订号。
    base_revision: int
    # 成功提交后应达到的修订号。
    next_revision: int

    # 要在提交时替换的顶层 GraphState 字段和值。
    set_values: Dict[str, Any] = Field(default_factory=dict)
    # 实际发生变化的细粒度点分隔路径。
    changed_paths: List[str] = Field(default_factory=list)
    # 工作流步骤声明的逻辑输出键。
    declared_write_keys: List[str] = Field(default_factory=list)
    # 解析后的物理允许写入路径前缀。
    declared_write_paths: List[str] = Field(default_factory=list)
    # 实际发生变化的顶层根字段。
    observed_write_roots: List[str] = Field(default_factory=list)

    # 提交前完整 GraphState 哈希。
    state_sha256_before: str
    # 模拟提交后完整 GraphState 哈希。
    state_sha256_after: str
    # 当前增量稳定载荷哈希。
    delta_sha256: str

    # diff 模式、兼容适配和写入契约版本等元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_delta(self) -> "GraphStateDeltaSchema":
        """验证增量修订连续性和内容哈希。

        Returns:
            校验通过后的当前增量。

        Raises:
            ValueError: 修订号不连续或增量哈希不匹配时抛出。
        """

        # 第一步：每个受控业务提交只能把 graph_revision 推进一位。
        if self.next_revision != self.base_revision + 1:
            raise ValueError("next_revision must equal base_revision + 1")

        # 第二步：按创建增量时的稳定字段重新计算 delta 哈希。
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

        # 第三步：载荷被修改时拒绝构造，防止声明路径和实际值分离。
        if actual != self.delta_sha256:
            raise ValueError("delta_sha256 does not match graph-state delta")

        return self


class GraphNodeOutputSchema(SchemaBase):
    """表示一次节点尝试的标准结果和拟议状态增量。"""

    # 当前节点输出契约版本。
    schema_version: str = "graph_node_output_v1"

    # 当前节点稳定标识。
    node_id: str
    # 当前节点可读名称。
    node_name: str
    # 当前节点执行类型。
    node_type: str
    # 当前节点实际调用的目标组件名称。
    target_name: str

    # 节点尝试的规范执行状态。
    status: ExecutionStatus
    # 节点目标 Agent 返回的完整标准结果。
    result: AgentResultSchema
    # 节点在状态副本上产生并通过契约校验的增量。
    state_delta: GraphStateDeltaSchema

    # 节点尝试开始时间。
    started_at: Optional[str] = None
    # 节点尝试结束时间。
    finished_at: Optional[str] = None
    # 节点尝试毫秒耗时。
    latency_ms: Optional[int] = None

    # 节点级附加告警。
    warnings: List[WarningSchema] = Field(default_factory=list)
    # 节点级规范错误；通常与 AgentResult.error 保持一致或补充执行器错误。
    error: Optional[ErrorSchema] = None
    # 提交决定、尝试次数和执行器信息等元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result_status(self) -> "GraphNodeOutputSchema":
        """保证节点外层状态与 Agent 结果状态一致。

        Returns:
            状态一致的当前节点输出。

        Raises:
            ValueError: 两个状态不一致时抛出，避免执行器和 Agent 形成双事实源。
        """

        if self.status != self.result.status:
            raise ValueError("node output status must match AgentResult status")
        return self


class GraphNodeExecutionRecordSchema(SchemaBase):
    """保存在 GraphState 中的有界节点执行历史记录。"""

    # 当前节点历史记录契约版本。
    schema_version: str = "graph_node_execution_record_v1"

    # 已执行节点标识。
    node_id: str
    # 已执行节点名称。
    node_name: str
    # 节点目标组件名称。
    target_name: str
    # 节点最终状态。
    status: ExecutionStatus
    # 节点输入投影哈希。
    input_sha256: str
    # 节点状态增量哈希。
    delta_sha256: str
    # 增量基线修订。
    base_revision: int
    # 增量目标修订。
    next_revision: int
    # 节点实际修改的状态路径。
    changed_paths: List[str] = Field(default_factory=list)
    # 节点开始时间。
    started_at: Optional[str] = None
    # 节点结束时间。
    finished_at: Optional[str] = None
    # 节点毫秒耗时。
    latency_ms: Optional[int] = None
    # 尝试次数、提交策略和其他历史元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEngineResultSchema(SchemaBase):
    """表示工作流引擎通过框架无关端口返回的完整执行结果。"""

    # 当前工作流引擎结果契约版本。
    schema_version: str = "workflow_engine_result_v1"

    # 实际工作流引擎名称。
    engine_name: str
    # 实际工作流引擎实现版本。
    engine_version: str
    # 被执行业务工作流标识。
    workflow_id: str
    # 被执行业务工作流版本。
    workflow_version: str
    # 执行所属任务标识。
    task_id: str
    # 执行所属单次运行标识。
    run_id: str

    # 整个工作流的规范终态。
    status: ExecutionStatus
    # 按执行顺序保存的节点输入投影。
    node_inputs: List[GraphNodeInputSchema] = Field(default_factory=list)
    # 按完成顺序保存的节点输出。
    node_outputs: List[GraphNodeOutputSchema] = Field(default_factory=list)
    # 与 node_outputs 一一对应的 Agent 结果兼容列表。
    node_results: List[AgentResultSchema] = Field(default_factory=list)
    # 与 node_outputs 顺序完全一致的已完成节点标识。
    completed_node_ids: List[str] = Field(default_factory=list)

    # 工作流开始时的受控业务提交修订。
    initial_revision: int = 0
    # 工作流结束时的受控业务提交修订。
    final_revision: int = 0
    # 工作流结束时完整 GraphState 哈希。
    final_state_sha256: str

    # 工作流终止时的主结构化错误。
    error: Optional[ErrorSchema] = None
    # 检查点、恢复、路由和执行器能力等结果元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_counts(self) -> "WorkflowEngineResultSchema":
        """验证节点结果列表顺序和修订区间一致。

        Returns:
            校验通过后的当前引擎结果。

        Raises:
            ValueError: 节点输出与 Agent 结果数量不同、完成节点顺序不一致，或最终
                修订小于初始修订时抛出。
        """

        # 第一步：每个节点输出必须对应一个 Agent 结果兼容项。
        if len(self.node_outputs) != len(self.node_results):
            raise ValueError("node_outputs and node_results must have equal length")

        # 第二步：完成节点列表只是 node_outputs 顺序的显式投影，不能独立漂移。
        if self.completed_node_ids != [item.node_id for item in self.node_outputs]:
            raise ValueError("completed_node_ids must follow node output order")

        # 第三步：工作流执行不能使受控业务修订倒退。
        if self.final_revision < self.initial_revision:
            raise ValueError("final_revision cannot be less than initial_revision")

        return self


# 对外暴露语义更清晰的稳定哈希名称，同时保持单一实现。
stable_graph_hash = _stable_hash
