"""定义 LangGraph 运行时与业务 Agent 共用的规范图状态。

``GraphStateSchema`` 在通用共享状态之上补充图执行位置、节点历史和受控提交修订号。
业务节点仍可沿用 ``SharedStateSchema`` 的字段，而工作流引擎可以依赖新增字段实现
审计、检查点恢复和节点级状态跟踪。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from agent.runtime.shared_state_schema import SharedStateSchema
from schemas.graph import GraphNodeExecutionRecordSchema


class GraphStateSchema(SharedStateSchema):
    """表示一次图工作流执行期间可持久化、可审计的完整规范状态。

    该模型继承 ``SharedStateSchema``，因此已有 Agent 无需改动方法签名即可接入图
    运行时。新增字段只描述图执行语义，不替代父类中的业务上下文和结果字段。
    """

    # 当前图状态数据契约版本，用于持久化兼容和迁移判断。
    schema_version: str = "graph_state_v1"

    # 受控业务状态提交修订号；只有通过校验的 GraphStateDelta 提交后才递增。
    # 节点运行态、重试次数和历史记录等运行时变化不会推动该值，因此它不是完整
    # GraphState 快照版本，检查点恢复不能只依赖该字段判断状态新旧。
    graph_revision: int = 0

    # 当前正在执行或最近进入的节点标识；尚未进入节点时为 None。
    current_node_id: Optional[str] = None

    # 已成功完成并提交节点结果的节点标识列表，按完成顺序保留。
    completed_node_ids: List[str] = Field(default_factory=list)

    # 每次节点执行的规范记录，包括状态、尝试次数、时间和错误信息。
    node_history: List[GraphNodeExecutionRecordSchema] = Field(default_factory=list)

    # 产生当前状态的工作流引擎名称，用于运行结果归因和恢复兼容检查。
    workflow_engine_name: str = "langgraph_workflow_engine"

    # 工作流引擎实现版本；与业务工作流版本是两个不同维度。
    workflow_engine_version: str = "v1"

    # 图运行时的可扩展元数据，例如线程标识、检查点命名空间和调试信息。
    graph_metadata: Dict[str, Any] = Field(default_factory=dict)
