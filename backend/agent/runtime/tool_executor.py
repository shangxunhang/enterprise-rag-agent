# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：ToolExecutor。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Tool execution orchestrator with structured failures and Trace v2 spans."""
from __future__ import annotations

import traceback

from contracts.observability import TraceSink
from core.error_factory import ErrorFactory
from observability.trace_context import activate_span, current_span, new_span
from observability.trace_summary import (
    extract_tool_lineage,
    tool_call_summary,
    tool_result_summary,
)
from schemas.status import ExecutionStatus
from schemas.tool import ToolCallSchema, ToolResultSchema
from tools.tool_registry import ToolRegistry


# 阅读注释（类）：封装 工具 executor，集中封装相关状态、依赖和行为。
class ToolExecutor:
    """封装 工具 executor，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ToolExecutor，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        tool_registry: ToolRegistry,
        run_trace_recorder: TraceSink | None = None,
        *,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        """初始化 ToolExecutor，保存运行所需的依赖、配置或状态。

        参数:
            tool_registry: 工具 注册表，具体约束请结合类型标注和调用方确认。
            run_trace_recorder: run Trace recorder，具体约束请结合类型标注和调用方确认。
            error_factory: 错误 工厂，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ErrorFactory。
        """
        self.tool_registry = tool_registry
        self.run_trace_recorder = run_trace_recorder
        self.error_factory = error_factory or ErrorFactory()

    # 阅读注释（函数）：处理 Trace 相关逻辑。
    def _trace(self, **kwargs) -> None:
        """处理 Trace 相关逻辑。

        参数:
            **kwargs: 额外关键字参数。

        返回:
            None

        阅读提示:
            主要直接调用：self.run_trace_recorder.record。
        """
        if self.run_trace_recorder is not None:
            self.run_trace_recorder.record(**kwargs)

    # 阅读注释（函数）：执行 ToolExecutor。
    def execute(self, tool_call: ToolCallSchema) -> ToolResultSchema:
        """执行 ToolExecutor。

        参数:
            tool_call: 工具 call，具体约束请结合类型标注和调用方确认。

        返回:
            ToolResultSchema

        阅读提示:
            主要直接调用：new_span, current_span, self._trace, tool_call.model_dump, tool_call_summary, activate_span, self.tool_registry.get, tool.run。
        """
        span = new_span(
            run_id=tool_call.run_id,
            span_name=f"tool:{tool_call.tool_name}",
            span_kind="client",
            parent=current_span(),
        )
        self._trace(
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            event_type="tool_started",
            component_type="tool",
            component_name=tool_call.tool_name,
            step_id=tool_call.step_id,
            step_name=tool_call.step_name,
            call_id=tool_call.tool_call_id,
            caller=tool_call.caller_agent,
            callee=tool_call.tool_name,
            tool_name=tool_call.tool_name,
            payload={"tool_call": tool_call.model_dump(mode="json")},
            input_summary=tool_call_summary(tool_call),
            status=ExecutionStatus.RUNNING.value,
            phase="start",
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_name=span.span_name,
            span_kind=span.span_kind,
            started_at=span.started_at,
            tags=["trace_v2", "tool"],
        )

        with activate_span(span):
            try:
                tool = self.tool_registry.get(tool_call.tool_name)
                result = tool.run(tool_call)
            except Exception as exc:
                error = self.error_factory.create(
                    error_code="TOOL_EXECUTION_EXCEPTION",
                    error_type=exc.__class__.__name__,
                    message=str(exc),
                    user_visible_message=f"工具 {tool_call.tool_name} 执行失败。",
                    recoverable=True,
                    retryable=False,
                    failed_node=tool_call.step_id or tool_call.tool_name,
                    component="ToolExecutor",
                    tool_name=tool_call.tool_name,
                    step_name=tool_call.step_name,
                    stack_trace=traceback.format_exc(),
                )
                result = ToolResultSchema(
                    tool_call_id=tool_call.tool_call_id,
                    task_id=tool_call.task_id,
                    run_id=tool_call.run_id,
                    tool_name=tool_call.tool_name,
                    success=False,
                    status=ExecutionStatus.FAILED,
                    result={},
                    error=error,
                    error_message=error.message,
                    created_at=tool_call.created_at,
                )

        latency_ms = result.latency_ms
        if latency_ms is None:
            latency_ms = span.latency_ms()
        result.latency_ms = latency_ms
        summary = tool_result_summary(result)
        lineage = extract_tool_lineage(result)
        error = result.error
        self._trace(
            task_id=tool_call.task_id,
            run_id=tool_call.run_id,
            event_type="tool_finished",
            component_type="tool",
            component_name=tool_call.tool_name,
            step_id=tool_call.step_id,
            step_name=tool_call.step_name,
            call_id=tool_call.tool_call_id,
            caller=tool_call.caller_agent,
            callee=tool_call.tool_name,
            tool_name=tool_call.tool_name,
            payload={"tool_result": result.model_dump(mode="json")},
            output_summary=summary,
            lineage=lineage,
            status=result.status.value,
            error_message=(error.message if error else result.error_message),
            latency_ms=latency_ms,
            phase=("error" if error else "end"),
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_name=span.span_name,
            span_kind=span.span_kind,
            started_at=span.started_at,
            tags=["trace_v2", "tool"],
        )
        return result
