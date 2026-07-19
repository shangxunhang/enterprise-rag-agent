# =============================================================================
# 中文阅读说明：端口与协议定义模块，用于约束模块间依赖边界。
# 主要定义：TraceSink、DataCaptureSink。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Ports for trace and data-capture sinks.

Application code depends on these protocols instead of concrete JSONL writers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


# 阅读注释（类）：封装 Trace sink，集中封装相关状态、依赖和行为。
class TraceSink(Protocol):
    """封装 Trace sink，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：记录 TraceSink。
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
        """记录 TraceSink。

        参数:
            task_id: 任务唯一标识。
            run_id: 本次运行唯一标识。
            event_type: 事件 类型，具体约束请结合类型标注和调用方确认。
            component_type: component 类型，具体约束请结合类型标注和调用方确认。
            component_name: component 名称，具体约束请结合类型标注和调用方确认。
            payload: 跨层传递的数据载荷。
            input_payload: 输入 载荷，具体约束请结合类型标注和调用方确认。
            output_payload: 输出 载荷，具体约束请结合类型标注和调用方确认。
            workflow_id: 工作流 标识，具体约束请结合类型标注和调用方确认。
            workflow_version: 工作流 版本，具体约束请结合类型标注和调用方确认。
            step_id: step 标识，具体约束请结合类型标注和调用方确认。
            step_name: step 名称，具体约束请结合类型标注和调用方确认。
            step_order: step order，具体约束请结合类型标注和调用方确认。
            call_id: call 标识，具体约束请结合类型标注和调用方确认。
            caller: caller，具体约束请结合类型标注和调用方确认。
            callee: callee，具体约束请结合类型标注和调用方确认。
            status: 状态，具体约束请结合类型标注和调用方确认。
            error_message: 错误 消息，具体约束请结合类型标注和调用方确认。
            latency_ms: latency ms，具体约束请结合类型标注和调用方确认。
            token_usage: Token 用量，具体约束请结合类型标注和调用方确认。
            cost: cost，具体约束请结合类型标注和调用方确认。
            metrics: 指标，具体约束请结合类型标注和调用方确认。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            tool_name: 工具 名称，具体约束请结合类型标注和调用方确认。
            agent_name: Agent 名称，具体约束请结合类型标注和调用方确认。
            tags: tags，具体约束请结合类型标注和调用方确认。
            metadata: 随对象传递的元数据。
            extra: extra，具体约束请结合类型标注和调用方确认。
            trace_id: Trace 标识，具体约束请结合类型标注和调用方确认。
            span_id: span 标识，具体约束请结合类型标注和调用方确认。
            parent_span_id: 父块 span 标识，具体约束请结合类型标注和调用方确认。
            span_name: span 名称，具体约束请结合类型标注和调用方确认。
            span_kind: span kind，具体约束请结合类型标注和调用方确认。
            phase: phase，具体约束请结合类型标注和调用方确认。
            started_at: started at，具体约束请结合类型标注和调用方确认。
            finished_at: finished at，具体约束请结合类型标注和调用方确认。
            input_summary: 输入 summary，具体约束请结合类型标注和调用方确认。
            output_summary: 输出 summary，具体约束请结合类型标注和调用方确认。
            lineage: lineage，具体约束请结合类型标注和调用方确认。

        返回:
            Any
        """
        ...


# 阅读注释（类）：封装 数据 capture sink，集中封装相关状态、依赖和行为。
class DataCaptureSink(Protocol):
    """封装 数据 capture sink，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：记录 DataCaptureSink。
    def record(self, **kwargs: Any) -> Any:
        """记录 DataCaptureSink。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            Any
        """
        ...
