# =============================================================================
# 中文阅读说明：运行数据沉淀模块，用于记录后训练与评测所需样本。
# 主要定义：JsonlRunTraceRecorder。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Trace v2 JSONL recorder with span hierarchy and bounded summaries."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from observability.trace_context import current_span
from observability.trace_summary import bounded_summary
from schemas.trace import RunTraceEventSchema


# 阅读注释（类）：封装 jsonl run Trace recorder，集中封装相关状态、依赖和行为。
class JsonlRunTraceRecorder:
    """Append one structured Trace v2 event per JSONL line.

    One ``run_id`` corresponds to one file.  Events are assigned a monotonic
    ``event_sequence`` inside the process, while ``trace_id`` / ``span_id``
    preserve the nested runtime call chain.
    """

    # 阅读注释（函数）：初始化 JsonlRunTraceRecorder，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        output_dir: str | Path = "data/runs",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        """初始化 JsonlRunTraceRecorder，保存运行所需的依赖、配置或状态。

        参数:
            output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。
            id_generator: 标识 generator，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path, SystemClock, UuidIdGenerator, self.output_dir.mkdir, threading.RLock。
        """
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sequence_by_run: Dict[str, int] = {}

    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def _now_iso(self) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：self.clock.now_iso。
        """
        return self.clock.now_iso()

    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def _new_id(self, prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：self.id_generator.new_id。
        """
        return self.id_generator.new_id(prefix)

    # 阅读注释（函数）：处理 Trace 路径 相关逻辑。
    def _trace_path(self, run_id: str) -> Path:
        """处理 Trace 路径 相关逻辑。

        参数:
            run_id: 本次运行唯一标识。

        返回:
            Path
        """
        return self.output_dir / f"{run_id}_trace.jsonl"

    # 阅读注释（函数）：处理 next sequence 相关逻辑。
    def _next_sequence(self, run_id: str) -> int:
        """处理 next sequence 相关逻辑。

        参数:
            run_id: 本次运行唯一标识。

        返回:
            int

        阅读提示:
            主要直接调用：self._trace_path, path.exists, path.open, sum, line.strip。
        """
        with self._lock:
            if run_id not in self._sequence_by_run:
                path = self._trace_path(run_id)
                count = 0
                if path.exists():
                    with path.open("r", encoding="utf-8") as handle:
                        count = sum(1 for line in handle if line.strip())
                self._sequence_by_run[run_id] = count
            self._sequence_by_run[run_id] += 1
            return self._sequence_by_run[run_id]

    # 阅读注释（函数）：记录 JsonlRunTraceRecorder。
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
    ) -> RunTraceEventSchema:
        """记录 JsonlRunTraceRecorder。

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
            RunTraceEventSchema

        阅读提示:
            主要直接调用：self._now_iso, current_span, self._new_id, RunTraceEventSchema, self._next_sequence, bounded_summary, self._trace_path, json.dumps。
        """
        now = self._now_iso()
        active = current_span()
        resolved_trace_id = trace_id or (active.trace_id if active else f"trace_{run_id}")
        resolved_span_id = span_id or (active.span_id if active else self._new_id("span"))
        resolved_parent_span_id = (
            parent_span_id
            if parent_span_id is not None
            else (active.parent_span_id if active and span_id is None else None)
        )

        raw_input = input_payload or {}
        raw_output = output_payload or {}
        event = RunTraceEventSchema(
            event_id=self._new_id("event"),
            event_sequence=self._next_sequence(run_id),
            trace_id=resolved_trace_id,
            task_id=task_id,
            run_id=run_id,
            span_id=resolved_span_id,
            parent_span_id=resolved_parent_span_id,
            span_name=span_name or component_name,
            span_kind=span_kind,
            phase=phase,
            event_type=event_type,
            component_type=component_type,
            component_name=component_name,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            step_id=step_id,
            step_name=step_name,
            step_order=step_order,
            call_id=call_id,
            caller=caller,
            callee=callee,
            status=status,
            error_message=error_message,
            input_payload=raw_input,
            output_payload=raw_output,
            input_summary=input_summary or bounded_summary(raw_input),
            output_summary=output_summary or bounded_summary(raw_output),
            lineage=lineage or {},
            payload=payload or {},
            latency_ms=latency_ms,
            token_usage=token_usage or {},
            cost=cost or {},
            metrics=metrics or {},
            started_at=started_at or (now if phase == "start" else None),
            finished_at=finished_at or (now if phase in {"end", "error"} else None),
            created_at=now,
            model_name=model_name,
            tool_name=tool_name,
            agent_name=agent_name,
            tags=tags or [],
            metadata=metadata or {},
            extra=extra or {},
        )

        path = self._trace_path(run_id)
        row = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(row + "\n")
        return event
