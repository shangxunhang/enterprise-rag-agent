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


@dataclass(frozen=True)
class TraceSpanContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]


@dataclass(frozen=True)
class TraceSpanHandle:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    span_name: str
    span_kind: str
    started_at: str
    started_perf: float

    def latency_ms(self) -> int:
        return max(0, int((time.perf_counter() - self.started_perf) * 1000))


_CURRENT_SPAN: ContextVar[Optional[TraceSpanContext]] = ContextVar(
    "agent_rag_current_trace_span",
    default=None,
)


def current_span() -> Optional[TraceSpanContext]:
    return _CURRENT_SPAN.get()


def new_span(
    *,
    run_id: str,
    span_name: str,
    span_kind: str = "internal",
    parent: Optional[TraceSpanContext] = None,
    clock: Clock | None = None,
) -> TraceSpanHandle:
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


@contextmanager
def activate_span(handle: TraceSpanHandle) -> Iterator[TraceSpanContext]:
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
