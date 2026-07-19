# =============================================================================
# 中文阅读说明：模型网关模块，用于屏蔽不同 LLM 提供方和本地模型调用差异。
# 主要定义：ModelCallObserver。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Trace v2 model-call observer."""

from __future__ import annotations

from typing import Optional

from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from observability.trace_summary import model_request_summary, model_response_summary
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.status import ExecutionStatus


# 阅读注释（类）：封装 模型 call observer，集中封装相关状态、依赖和行为。
class ModelCallObserver:
    """封装 模型 call observer，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ModelCallObserver，保存运行所需的依赖、配置或状态。
    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        """初始化 ModelCallObserver，保存运行所需的依赖、配置或状态。

        参数:
            sink: sink，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.sink = sink

    # 阅读注释（函数）：启动 ModelCallObserver。
    def start(
        self,
        request: ModelRequestSchema,
        *,
        model_name: str,
    ) -> TraceSpanHandle:
        """启动 ModelCallObserver。

        参数:
            request: 当前请求对象。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。

        返回:
            TraceSpanHandle

        阅读提示:
            主要直接调用：new_span, current_span, self.sink.record, model_request_summary, request.extra.get, get, bool。
        """
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

    # 阅读注释（函数）：处理 finish 相关逻辑。
    def finish(
        self,
        request: ModelRequestSchema,
        response: ModelResponseSchema,
        *,
        model_name: str,
        handle: TraceSpanHandle,
    ) -> None:
        """处理 finish 相关逻辑。

        参数:
            request: 当前请求对象。
            response: 下游返回的响应对象。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            handle: handle，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：handle.latency_ms, response.token_usage.model_dump, self.sink.record, model_response_summary, request.extra.get, get, bool。
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

    # 阅读注释（函数）：记录 ModelCallObserver。
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
