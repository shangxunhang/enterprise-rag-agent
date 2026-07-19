# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：TraceSpanContext、TraceSpanHandle、current_span、new_span、activate_span。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Lightweight synchronous trace context for nested Agent-RAG spans.

The project is currently a modular monolith with synchronous execution.  A
``ContextVar`` is therefore enough to propagate the active span through
Supervisor -> Workflow -> Agent -> Tool / Model calls without adding a tracing
SDK dependency.  LangGraph/OpenTelemetry adapters can map this contract later.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional

from core.runtime.clock import Clock, SystemClock


# 阅读注释（类）：封装 Trace span 上下文，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class TraceSpanContext:
    """封装 Trace span 上下文，集中封装相关状态、依赖和行为。"""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]


# 阅读注释（类）：封装 Trace span handle，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class TraceSpanHandle:
    """封装 Trace span handle，集中封装相关状态、依赖和行为。"""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    span_name: str
    span_kind: str
    started_at: str
    started_perf: float

    # 阅读注释（函数）：处理 latency ms 相关逻辑。
    def latency_ms(self) -> int:
        """处理 latency ms 相关逻辑。

        返回:
            int

        阅读提示:
            主要直接调用：max, int, time.perf_counter。
        """
        return max(0, int((time.perf_counter() - self.started_perf) * 1000))


_CURRENT_SPAN: ContextVar[Optional[TraceSpanContext]] = ContextVar(
    "agent_rag_current_trace_span",
    default=None,
)


# 阅读注释（函数）：处理 current span 相关逻辑。
def current_span() -> Optional[TraceSpanContext]:
    """处理 current span 相关逻辑。

    返回:
        Optional[TraceSpanContext]

    阅读提示:
        主要直接调用：_CURRENT_SPAN.get。
    """
    return _CURRENT_SPAN.get()


# 阅读注释（函数）：处理 new span 相关逻辑。
def new_span(
    *,
    run_id: str,
    span_name: str,
    span_kind: str = "internal",
    parent: Optional[TraceSpanContext] = None,
    clock: Clock | None = None,
) -> TraceSpanHandle:
    """处理 new span 相关逻辑。

    参数:
        run_id: 本次运行唯一标识。
        span_name: span 名称，具体约束请结合类型标注和调用方确认。
        span_kind: span kind，具体约束请结合类型标注和调用方确认。
        parent: 父块，具体约束请结合类型标注和调用方确认。
        clock: clock，具体约束请结合类型标注和调用方确认。

    返回:
        TraceSpanHandle

    阅读提示:
        主要直接调用：current_span, TraceSpanHandle, uuid.uuid4, now_iso, SystemClock, time.perf_counter。
    """
    parent = parent if parent is not None else current_span()
    return TraceSpanHandle(
        trace_id=(parent.trace_id if parent else f"trace_{run_id}"),
        span_id=f"span_{uuid.uuid4().hex[:16]}",
        parent_span_id=(parent.span_id if parent else None),
        span_name=span_name,
        span_kind=span_kind,
        started_at=(clock or SystemClock()).now_iso(),
        started_perf=time.perf_counter(),
    )


# 阅读注释（函数）：处理 activate span 相关逻辑。
@contextmanager
def activate_span(handle: TraceSpanHandle) -> Iterator[TraceSpanContext]:
    """处理 activate span 相关逻辑。

    参数:
        handle: handle，具体约束请结合类型标注和调用方确认。

    返回:
        Iterator[TraceSpanContext]

    阅读提示:
        主要直接调用：TraceSpanContext, _CURRENT_SPAN.set, _CURRENT_SPAN.reset。
    """
    context = TraceSpanContext(
        trace_id=handle.trace_id,
        span_id=handle.span_id,
        parent_span_id=handle.parent_span_id,
    )
    token = _CURRENT_SPAN.set(context)
    try:
        yield context
    finally:
        _CURRENT_SPAN.reset(token)
