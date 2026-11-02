"""记录模型调用的 Trace v2 起止事件、用量、上下文血缘和失败信息。"""

from __future__ import annotations

from typing import Optional

from contracts.observability import TraceSink
from observability.trace_context import TraceSpanContext, TraceSpanHandle, current_span, new_span
from observability.trace_summary import model_request_summary, model_response_summary
from schemas.failure import FailureOrigin
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.status import ExecutionStatus
from schemas.trace import TraceCorrelationSchema


class ModelCallObserver:
    """围绕一次逻辑模型调用创建 client span，不参与模型选择或异常处理。"""
    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        """注入可选 Trace sink；禁用观测时其余调用链仍保持相同行为。"""
        self.sink = sink

    @staticmethod
    def _correlation(
        request: ModelRequestSchema,
        *,
        external_parent: TraceSpanContext | None = None,
    ) -> TraceCorrelationSchema:
        """Project already-owned Model/Agent/RAG identities into typed Trace fields."""

        extra = dict(request.extra or {})
        external_refs = []
        if external_parent is not None:
            external_refs.append(
                "trace-ref:"
                f"{external_parent.run_id}:"
                f"{external_parent.trace_id}:"
                f"{external_parent.span_id}"
            )
        return TraceCorrelationSchema(
            attempt_scope_ref=extra.get("attempt_scope_ref"),
            attempt_id=extra.get("attempt_id") or extra.get("generation_attempt"),
            section_id=extra.get("section_id"),
            agent_invocation_id=extra.get("agent_invocation_id"),
            execution_id=extra.get("execution_id"),
            logical_operation_id=extra.get("logical_operation_id"),
            model_call_id=request.model_call_id,
            provider_attempt_id=extra.get("provider_attempt_id"),
            provider_attempt_ordinal=extra.get("provider_attempt_ordinal"),
            retrieval_trace_id=extra.get("retrieval_trace_id"),
            external_trace_refs=external_refs,
        )

    def start(
        self,
        request: ModelRequestSchema,
        *,
        model_name: str,
    ) -> TraceSpanHandle:
        """创建模型调用 span，并记录有界输入摘要与选择血缘。

        不把完整 Prompt 写入 Trace；摘要只保存长度、哈希、短预览和上下文包标识，既能
        复核具体调用，又避免 Trace 成为第二份业务文档或泄露敏感内容。
        """
        active = current_span()
        cross_run_parent = (
            active if active is not None and active.run_id != request.run_id else None
        )
        handle = new_span(
            run_id=request.run_id,
            span_name=f"model:{model_name}",
            span_kind="client",
            parent=(None if cross_run_parent is not None else active),
            inherit_current=False,
        )
        correlation = self._correlation(
            request,
            external_parent=cross_run_parent,
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
                correlation=correlation,
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
                    "model_role": request.model_role,
                    "selected_profile": request.extra.get("selected_profile"),
                    "provider": request.extra.get("provider"),
                    "section_id": request.extra.get("section_id"),
                    "rag_run_id": request.extra.get("rag_run_id"),
                    "retrieval_trace_id": request.extra.get("retrieval_trace_id"),
                    "availability_fallback_from": request.extra.get("availability_fallback_from"),
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
        """关闭模型 span，补齐延迟、Token 用量、失败状态和调用血缘。

        客户端未返回延迟时使用 span 本地计时，并同步写回响应对象，保证 Trace 与上层
        数据采集看到同一数值。
        """
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
            correlation=self._correlation(
                request,
                external_parent=(
                    current_span()
                    if current_span() is not None
                    and current_span().run_id != request.run_id
                    else None
                ),
            ),
            error=error,
            failure_origin=(
                FailureOrigin.UNKNOWN if response.success else response.failure_origin
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
                "model_role": request.model_role,
                "selected_profile": request.extra.get("selected_profile"),
                "selected_model": request.extra.get("selected_model"),
                "provider": request.extra.get("provider"),
                "section_id": request.extra.get("section_id"),
                "rag_run_id": request.extra.get("rag_run_id"),
                "retrieval_trace_id": request.extra.get("retrieval_trace_id"),
                "availability_fallback_from": request.extra.get("availability_fallback_from"),
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
            correlation=self._correlation(
                request,
                external_parent=(
                    current_span()
                    if current_span() is not None
                    and current_span().run_id != request.run_id
                    else None
                ),
            ),
            tags=["trace_v2", "compatibility_event"],
        )
