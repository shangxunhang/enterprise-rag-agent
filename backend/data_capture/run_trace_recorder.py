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


class JsonlRunTraceRecorder:
    """Append one structured Trace v2 event per JSONL line.

    One ``run_id`` corresponds to one file.  Events are assigned a monotonic
    ``event_sequence`` inside the process, while ``trace_id`` / ``span_id``
    preserve the nested runtime call chain.
    """

    def __init__(
        self,
        output_dir: str | Path = "data/runs",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sequence_by_run: Dict[str, int] = {}

    def _now_iso(self) -> str:
        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        return self.id_generator.new_id(prefix)

    def _trace_path(self, run_id: str) -> Path:
        return self.output_dir / f"{run_id}_trace.jsonl"

    def _next_sequence(self, run_id: str) -> int:
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
