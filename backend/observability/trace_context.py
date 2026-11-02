"""提供轻量同步 Trace span 上下文传播。

当前系统是同步模块化单体，使用 ``ContextVar`` 即可在 Supervisor → Workflow → Agent →
Tool/Model 调用链中传播活动 span，无需引入完整 tracing SDK。未来 LangGraph 或
OpenTelemetry 适配器可以把该最小契约映射到外部追踪系统。
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
    """表示当前执行上下文中可向下游传播的最小 span 关联信息。

    Attributes:
        run_id: 当前 span 所属 Run 标识，仅用于相关性完整性校验。
        trace_id: 整条运行调用链标识。
        span_id: 当前活动 span 标识。
        parent_span_id: 当前 span 的直接父级标识；顶层 span 为 ``None``。
    """

    run_id: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]


@dataclass(frozen=True)
class TraceSpanHandle:
    """保存一个 span 从开始到结束所需的身份和计时信息。

    Attributes:
        run_id: 当前 span 所属 Run 标识。
        trace_id: 整条调用链标识。
        span_id: 当前 span 标识。
        parent_span_id: 直接父 span 标识。
        span_name: 当前 span 可读名称。
        span_kind: span 角色，例如 server、client 或 internal。
        started_at: 可持久化 UTC 开始时间。
        started_perf: 只用于本进程耗时计算的单调时间点。
    """

    run_id: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    span_name: str
    span_kind: str
    started_at: str
    started_perf: float

    def latency_ms(self) -> int:
        """返回从 span 创建到当前时刻的非负整数毫秒耗时。"""

        # 使用单调性能计数器计算时间差，避免系统时间校准造成负耗时。
        return max(0, int((time.perf_counter() - self.started_perf) * 1000))


# 保存当前同步/异步上下文中的活动 span。ContextVar 会在任务上下文中隔离值，并允许
# contextmanager 通过 token 精确恢复进入前状态。
_CURRENT_SPAN: ContextVar[Optional[TraceSpanContext]] = ContextVar(
    "agent_rag_current_trace_span",
    default=None,
)


def current_span() -> Optional[TraceSpanContext]:
    """返回当前执行上下文中的活动 span；没有时返回 ``None``。"""

    return _CURRENT_SPAN.get()


def new_span(
    *,
    run_id: str,
    span_name: str,
    span_kind: str = "internal",
    parent: Optional[TraceSpanContext] = None,
    clock: Clock | None = None,
    inherit_current: bool = True,
) -> TraceSpanHandle:
    """创建一个尚未激活的新 span handle。

    Args:
        run_id: 单次运行标识；无父 span 时用于构造顶层 trace_id。
        span_name: 当前操作的可读名称。
        span_kind: span 角色，默认 internal。
        parent: 显式父 span；未提供且 inherit_current=True 时读取当前活动 span。
        clock: 自定义墙上时钟；未提供时使用系统 UTC 时钟。
        inherit_current: 是否在 parent 未显式给出时继承当前活动 span。跨 Run
            continuation 必须设为 False，并通过显式 Trace refs 关联。

    Returns:
        包含调用链身份、开始时间和单调计时起点的 ``TraceSpanHandle``。
    """

    # 第一步：显式父级优先；只有允许继承时才读取当前活动 span。
    if parent is None and inherit_current:
        parent = current_span()

    # 第二步：Trace parent 只能来自同一个 Run。跨 Run continuation 必须创建
    # 新 Trace，并通过显式 lineage/ref 关联，不能把另一个 Run 的 span 当父级。
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("Trace span run_id must be non-empty")
    if parent is not None and parent.run_id != normalized_run_id:
        raise ValueError(
            "Trace span parent must belong to the same run_id; "
            "cross-run correlation requires explicit refs"
        )

    # 第三步：父级存在时复用 trace_id；顶层 span 按 run_id 形成稳定调用链标识。
    trace_id = parent.trace_id if parent else f"trace_{normalized_run_id}"

    # 第四步：每个新 span 使用独立随机标识，并记录父 span 关系。
    return TraceSpanHandle(
        run_id=normalized_run_id,
        trace_id=trace_id,
        span_id=f"span_{uuid.uuid4().hex[:16]}",
        parent_span_id=(parent.span_id if parent else None),
        span_name=span_name,
        span_kind=span_kind,
        # 墙上时钟用于持久化展示，单调计时器用于计算延迟。
        started_at=(clock or SystemClock()).now_iso(),
        started_perf=time.perf_counter(),
    )


@contextmanager
def activate_span(handle: TraceSpanHandle) -> Iterator[TraceSpanContext]:
    """在上下文块内激活给定 span，并在退出时恢复原上下文。

    Args:
        handle: 由 ``new_span`` 创建的 span handle。

    Yields:
        可向下游传播的最小 ``TraceSpanContext``。
    """

    # 第一步：从完整 handle 提取只需要传播的 trace/span 关联字段。
    context = TraceSpanContext(
        run_id=handle.run_id,
        trace_id=handle.trace_id,
        span_id=handle.span_id,
        parent_span_id=handle.parent_span_id,
    )

    # 第二步：激活新上下文并保存恢复 token。
    token = _CURRENT_SPAN.set(context)
    try:
        # 第三步：执行上下文块；其内部 new_span 会自动继承当前 span。
        yield context
    finally:
        # 第四步：无论业务执行是否抛出异常，都精确恢复进入前的 span 上下文。
        _CURRENT_SPAN.reset(token)
