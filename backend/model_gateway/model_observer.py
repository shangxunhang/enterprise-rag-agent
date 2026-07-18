"""Trace v2 model-call observer."""

from __future__ import annotations

from typing import Optional

from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from observability.trace_summary import model_request_summary, model_response_summary
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.status import ExecutionStatus


class ModelCallObserver:
    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        self.sink = sink

    def start(
        self,
        request: ModelRequestSchema,
        *,
        model_name: str,
    ) -> TraceSpanHandle:
        handle = new_span(
            run_id=request.run_id,
            span_name=f"model:{model_name}",
            span_kind="client",
            parent=current_span(),
        )
        if self.sink is not None:
            self.sink.record(
                task_id=request.task_id,
                run_id=request.run_id,
                event_type="model_started",
                component_type="model",
                component_name=model_name,
                model_name=model_name,
                agent_name=request.caller_agent,
                call_id=request.model_call_id,
                caller=request.caller_agent,
                callee=model_name,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                input_summary=model_request_summary(request, model_name),
                tags=["trace_v2", "model"],
                metadata={
                    "call_purpose": request.extra.get("call_purpose"),
                    "prompt_id": request.extra.get("prompt_id"),
                    "prompt_version": request.extra.get("prompt_version"),
                    "context_package_id": (request.extra.get("llm_context_summary") or {}).get("package_id"),
                    "context_sha256": (request.extra.get("llm_context_summary") or {}).get("context_sha256"),
                    "context_managed": bool((request.extra.get("llm_context_summary") or {}).get("managed")),
                },
            )
        return handle

    def finish(
        self,
        request: ModelRequestSchema,
        response: ModelResponseSchema,
        *,
        model_name: str,
        handle: TraceSpanHandle,
    ) -> None:
        if self.sink is None:
            return
        error = response.error
        latency_ms = response.latency_ms
        if latency_ms is None:
            latency_ms = handle.latency_ms()
            response.latency_ms = latency_ms
        usage = response.token_usage.model_dump(mode="json")
        self.sink.record(
            task_id=request.task_id,
            run_id=request.run_id,
            event_type="model_finished",
            component_type="model",
            component_name=model_name,
            model_name=model_name,
            agent_name=request.caller_agent,
            call_id=request.model_call_id,
            caller=request.caller_agent,
            callee=model_name,
            status=(
                ExecutionStatus.SUCCESS.value
                if response.success
                else ExecutionStatus.FAILED.value
            ),
            error_message=(error.message if error else response.error_message),
            latency_ms=latency_ms,
            token_usage=usage,
            phase=("error" if error or not response.success else "end"),
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            output_summary=model_response_summary(response),
            lineage={
                "model_name": model_name,
                "prompt_id": request.extra.get("prompt_id"),
                "prompt_version": request.extra.get("prompt_version"),
                "call_purpose": request.extra.get("call_purpose"),
                "context_package_id": (request.extra.get("llm_context_summary") or {}).get("package_id"),
                "context_sha256": (request.extra.get("llm_context_summary") or {}).get("context_sha256"),
                "context_managed": bool((request.extra.get("llm_context_summary") or {}).get("managed")),
            },
            tags=["trace_v2", "model"],
        )

    def record(
        self,
        request: ModelRequestSchema,
        *,
        event_type: str,
        status: str,
        payload: dict,
        model_name: str,
    ) -> None:
        """Compatibility API for legacy callers."""
        if self.sink is None:
            return
        self.sink.record(
            task_id=request.task_id,
            run_id=request.run_id,
            event_type=event_type,
            component_type="model",
            component_name=model_name,
            model_name=model_name,
            agent_name=request.caller_agent,
            call_id=request.model_call_id,
            payload=payload,
            status=status,
            tags=["trace_v2", "compatibility_event"],
        )
