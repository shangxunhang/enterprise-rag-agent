"""定义单次任务运行中的统一 Trace v2 事件外壳。

工作流、Agent、工具和模型事件共享同一结构，通过 trace/span 标识形成层级调用链。
稳定核心字段用于检索和统计；组件特有内容应优先放入输入输出载荷、摘要、血缘、指标、
元数据或扩展字段，避免频繁修改事件顶层契约。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import ErrorSchema, SchemaBase


class RunTraceEventSchema(SchemaBase):
    """表示一次任务运行中的一个结构化事件或 span 生命周期记录。

    同一 ``run_id`` 的事件应共享 ``trace_id``；每条事件有独立 ``event_id`` 和顺序号。
    start、end/error 事件可使用相同 ``span_id`` 表示同一组件调用的生命周期。
    """

    # 当前运行轨迹事件契约版本。
    schema_version: str = "run_trace_event_v2"

    # 当前事件唯一标识。
    event_id: str
    # 同一运行内的单调事件顺序号；用于文件和展示排序。
    event_sequence: int = 0
    # 整条调用链标识。
    trace_id: str
    # 单次任务执行标识。
    run_id: str
    # 业务任务标识。
    task_id: str

    # 旧事件层级中的直接父事件标识。
    parent_event_id: Optional[str] = None
    # 当前调用或操作 span 标识。
    span_id: Optional[str] = None
    # 当前 span 的直接父 span 标识。
    parent_span_id: Optional[str] = None
    # 当前 span 的可读名称。
    span_name: Optional[str] = None
    # span 角色，例如 server、client 或 internal。
    span_kind: str = "internal"
    # 生命周期阶段，例如 start、end、error 或普通 event。
    phase: str = "event"

    # 稳定事件类别，例如 workflow_started 或 model_finished。
    event_type: str
    # 可选可读事件名称。
    event_name: Optional[str] = None

    # 产生事件的组件类别，例如 runtime、workflow、agent、tool 或 model。
    component_type: str
    # 产生事件的具体组件名称。
    component_name: str
    # 组件实现版本。
    component_version: Optional[str] = None

    # 当前业务工作流标识。
    workflow_id: Optional[str] = None
    # 当前业务工作流版本。
    workflow_version: Optional[str] = None
    # 当前步骤稳定标识。
    step_id: Optional[str] = None
    # 当前步骤可读名称。
    step_name: Optional[str] = None
    # 当前步骤默认线性顺序。
    step_order: Optional[int] = None

    # 一次跨组件调用的标识。
    call_id: Optional[str] = None
    # 发起调用的组件。
    caller: Optional[str] = None
    # 接收调用的组件。
    callee: Optional[str] = None

    # 事件发生时的规范或兼容状态字符串。
    status: Optional[str] = None
    # 与事件直接关联的结构化错误。
    error: Optional[ErrorSchema] = None
    # 兼容或快速查询使用的错误摘要。
    error_message: Optional[str] = None

    # 组件完整或脱敏输入载荷；高敏数据进入前必须经过边界审查。
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    # 组件完整或脱敏输出载荷。
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    # 为展示和检索准备的有限输入摘要。
    input_summary: Dict[str, Any] = Field(default_factory=dict)
    # 为展示和检索准备的有限输出摘要。
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    # 上游来源、证据、模型输出与下游产物之间的血缘信息。
    lineage: Dict[str, Any] = Field(default_factory=dict)

    # 迁移期通用载荷；新事件应优先使用语义更明确的输入输出字段。
    payload: Dict[str, Any] = Field(default_factory=dict)

    # 当前事件或 span 的毫秒耗时。
    latency_ms: Optional[int] = None
    # 模型输入、输出和总 token 用量。
    token_usage: Dict[str, Any] = Field(default_factory=dict)
    # 本次调用成本明细或聚合值。
    cost: Dict[str, Any] = Field(default_factory=dict)
    # 组件特有的数值或结构化指标。
    metrics: Dict[str, Any] = Field(default_factory=dict)

    # span 或操作开始时间。
    started_at: Optional[str] = None
    # span 或操作结束时间。
    finished_at: Optional[str] = None
    # 当前事件记录创建时间。
    created_at: str

    # 可选业务会话标识。
    session_id: Optional[str] = None
    # 可选用户标识。
    user_id: Optional[str] = None
    # 外部请求或 API 调用关联标识。
    request_id: Optional[str] = None

    # 事件涉及的实际模型名称。
    model_name: Optional[str] = None
    # 事件涉及的工具名称。
    tool_name: Optional[str] = None
    # 事件涉及的 Agent 名称。
    agent_name: Optional[str] = None

    # 用于筛选、聚合和版本迁移的稳定标签。
    tags: List[str] = Field(default_factory=list)
    # 业务对象或运行环境相关元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 尚未进入稳定轨迹事件契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
