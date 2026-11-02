"""定义执行轨迹与训练数据采集的输出端口。

应用层只依赖这些协议，不直接创建 JSONL 写入器或其他持久化实现。这样测试可以注入
内存 sink，部署时也可以替换为数据库、消息队列或外部可观测平台，而不改变主链。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from schemas.common import ErrorSchema
from schemas.failure import FailureOrigin
from schemas.trace import TraceCorrelationSchema


class TraceSink(Protocol):
    """声明结构化运行轨迹接收器必须提供的记录入口。"""

    def record(
        self,
        task_id: str,
        run_id: str,
        event_type: str,
        component_type: str,
        component_name: str,
        payload: Optional[Dict[str, Any]] = None,
        input_payload: Optional[Dict[str, Any]] = None,
        output_payload: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[str] = None,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
        step_order: Optional[int] = None,
        call_id: Optional[str] = None,
        caller: Optional[str] = None,
        callee: Optional[str] = None,
        status: Optional[str] = None,
        correlation: Optional[TraceCorrelationSchema] = None,
        error: Optional[ErrorSchema] = None,
        failure_origin: FailureOrigin = FailureOrigin.UNKNOWN,
        error_message: Optional[str] = None,
        latency_ms: Optional[int] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        cost: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        span_name: Optional[str] = None,
        span_kind: str = "internal",
        phase: str = "event",
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        input_summary: Optional[Dict[str, Any]] = None,
        output_summary: Optional[Dict[str, Any]] = None,
        lineage: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """记录一个规范运行事件或 span 生命周期事件。

        Args:
            task_id: 事件所属业务任务标识。
            run_id: 事件所属单次执行标识。
            event_type: 稳定事件类别，用于查询和聚合。
            component_type: 产生事件的组件类型，例如 workflow、agent、tool 或 model。
            component_name: 产生事件的具体组件名称。
            payload: 无需区分输入输出时使用的通用结构化载荷。
            input_payload: 组件接收到的完整或脱敏输入载荷。
            output_payload: 组件产生的完整或脱敏输出载荷。
            workflow_id: 当前业务工作流标识。
            workflow_version: 当前业务工作流版本。
            step_id: 当前步骤稳定标识。
            step_name: 当前步骤可读名称。
            step_order: 当前步骤默认线性顺序。
            call_id: 一次具体跨组件调用的关联标识。
            caller: 发起调用的组件名称。
            callee: 接收调用的组件名称。
            status: 事件发生时的规范执行状态。
            correlation: typed observability correlation；不得作为业务 authority。
            error: 已由来源边界构造的结构化错误事实。
            failure_origin: 失败来源 family；只用于事实区分，不授予控制权限。
            error_message: 已脱敏的错误摘要。
            latency_ms: 当前事件或 span 的毫秒耗时。
            token_usage: 模型输入、输出和总 token 用量。
            cost: 与本次调用关联的成本明细或聚合值。
            metrics: 组件特有的数值指标。
            model_name: 发生模型调用时实际使用的模型名称。
            tool_name: 发生工具调用时使用的工具名称。
            agent_name: 发生 Agent 调用时使用的 Agent 名称。
            tags: 便于筛选和聚合的稳定标签。
            metadata: 与业务对象或运行环境关联的元数据。
            extra: 尚未进入稳定事件契约的扩展字段。
            trace_id: 分布式或项目内整条调用链的标识。
            span_id: 当前 span 标识。
            parent_span_id: 当前 span 的直接父 span 标识。
            span_name: 当前 span 的可读名称。
            span_kind: span 方向或角色，默认 ``internal``。
            phase: span 生命周期阶段，例如 start、event 或 end。
            started_at: 当前 span 开始时间。
            finished_at: 当前 span 结束时间。
            input_summary: 为展示和检索准备的脱敏输入摘要。
            output_summary: 为展示和检索准备的脱敏输出摘要。
            lineage: 数据或调用血缘信息，例如上游证据与下游产物关联。

        Returns:
            具体 sink 的记录结果；应用层不得依赖某个固定返回类型。
        """

        ...


class DataCaptureSink(Protocol):
    """声明原始交互、SFT 候选或评测样本采集器的通用记录入口。"""

    def record(self, **kwargs: Any) -> Any:
        """把调用方提供的命名字段写入具体数据采集实现。

        Args:
            **kwargs: 由具体采集类型定义的结构化字段。调用方仍应遵守对应采集
                Schema；该宽松端口只用于让不同采集器共享注入边界。

        Returns:
            具体 sink 的记录结果，例如规范记录对象或持久化位置。
        """

        ...
