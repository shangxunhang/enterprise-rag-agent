# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：WorkflowTraceService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Workflow/run-level Trace v2 service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from observability.trace_summary import bounded_summary
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


# 阅读注释（类）：封装 工作流 Trace 服务，封装一组可复用的业务能力。
class WorkflowTraceService:
    """封装 工作流 Trace 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：初始化 WorkflowTraceService，保存运行所需的依赖、配置或状态。
    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        """初始化 WorkflowTraceService，保存运行所需的依赖、配置或状态。

        参数:
            sink: sink，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.sink = sink

    # 阅读注释（函数）：启动 run。
    def start_run(self, *, task: TaskSchema, component_name: str) -> TraceSpanHandle:
        """启动 run。

        参数:
            task: 待执行的任务对象。
            component_name: component 名称，具体约束请结合类型标注和调用方确认。

        返回:
            TraceSpanHandle

        阅读提示:
            主要直接调用：new_span, current_span, self.sink.record, len, bool。
        """
        handle = new_span(
            run_id=task.run_id,
            span_name=f"run:{component_name}",
            span_kind="server",
            parent=current_span(),
        )
        if self.sink is not None:
            self.sink.record(
                task_id=task.task_id,
                run_id=task.run_id,
                event_type="run_started",
                component_type="runtime",
                component_name=component_name,
                agent_name=component_name,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                input_summary={
                    "task_id": task.task_id,
                    "run_id": task.run_id,
                    "task_type": task.task_type,
                    "user_input_chars": len(task.user_input or ""),
                    "project_input_present": bool(task.project_input),
                },
                tags=["trace_v2", "run"],
            )
        return handle

    # 阅读注释（函数）：处理 finish run 相关逻辑。
    def finish_run(
        self,
        *,
        task: TaskSchema,
        component_name: str,
        handle: TraceSpanHandle,
        result: AgentResultSchema,
    ) -> None:
        """处理 finish run 相关逻辑。

        参数:
            task: 待执行的任务对象。
            component_name: component 名称，具体约束请结合类型标注和调用方确认。
            handle: handle，具体约束请结合类型标注和调用方确认。
            result: 待处理的结果对象。

        返回:
            None

        阅读提示:
            主要直接调用：self.sink.record, handle.latency_ms。
        """
        if self.sink is None:
            return
        error = result.error
        self.sink.record(
            task_id=task.task_id,
            run_id=task.run_id,
            event_type="run_finished",
            component_type="runtime",
            component_name=component_name,
            agent_name=component_name,
            status=result.status.value,
            error_message=(error.message if error else result.error_message),
            phase=("error" if error else "end"),
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            finished_at=None,
            latency_ms=handle.latency_ms(),
            output_summary={
                "status": result.status.value,
                "result_type": result.result_type,
                "need_human_review": result.need_human_review,
                "error_code": error.error_code if error else None,
                "error_type": error.error_type if error else None,
            },
            tags=["trace_v2", "run"],
        )

    # 阅读注释（函数）：启动 工作流。
    def start_workflow(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        payload: Dict[str, Any],
    ) -> TraceSpanHandle:
        """启动 工作流。

        参数:
            task: 待执行的任务对象。
            workflow: 工作流，具体约束请结合类型标注和调用方确认。
            payload: 跨层传递的数据载荷。

        返回:
            TraceSpanHandle

        阅读提示:
            主要直接调用：new_span, current_span, self.sink.record, len, bounded_summary, payload.get。
        """
        handle = new_span(
            run_id=task.run_id,
            span_name=f"workflow:{workflow.workflow_id}",
            span_kind="internal",
            parent=current_span(),
        )
        if self.sink is not None:
            self.sink.record(
                task_id=task.task_id,
                run_id=task.run_id,
                event_type="workflow_started",
                component_type="workflow",
                component_name=workflow.workflow_id,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                payload=payload,
                input_summary={
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.workflow_version,
                    "step_count": len(workflow.steps),
                    "task_type": task.task_type,
                    "routing": bounded_summary(payload.get("routing") or {}),
                },
                tags=["trace_v2", "workflow"],
            )
        return handle

    # 阅读注释（函数）：处理 finish 工作流 相关逻辑。
    def finish_workflow(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        handle: TraceSpanHandle,
        result: AgentResultSchema,
    ) -> None:
        """处理 finish 工作流 相关逻辑。

        参数:
            task: 待执行的任务对象。
            workflow: 工作流，具体约束请结合类型标注和调用方确认。
            handle: handle，具体约束请结合类型标注和调用方确认。
            result: 待处理的结果对象。

        返回:
            None

        阅读提示:
            主要直接调用：get, self.sink.record, handle.latency_ms, len, sum, lower, str, item.get。
        """
        if self.sink is None:
            return
        error = result.error
        sub_results = (result.result or {}).get("sub_agent_results") or []
        self.sink.record(
            task_id=task.task_id,
            run_id=task.run_id,
            event_type="workflow_finished",
            component_type="workflow",
            component_name=workflow.workflow_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            status=result.status.value,
            error_message=(error.message if error else result.error_message),
            phase=("error" if error else "end"),
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            span_name=handle.span_name,
            span_kind=handle.span_kind,
            started_at=handle.started_at,
            latency_ms=handle.latency_ms(),
            output_summary={
                "status": result.status.value,
                "workflow_complete": (result.result or {}).get("workflow_complete"),
                "sub_agent_count": len(sub_results),
                "failed_sub_agent_count": sum(
                    1
                    for item in sub_results
                    if str(item.get("status") or "").lower() not in {"success", "partial_success", "executionstatus.success", "executionstatus.partial_success"}
                ),
                "error_code": error.error_code if error else None,
            },
            tags=["trace_v2", "workflow"],
        )

    # 阅读注释（函数）：记录 WorkflowTraceService。
    def record(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        event_type: str,
        status: ExecutionStatus,
        payload: Dict[str, Any],
    ) -> None:
        """Compatibility event API retained for callers outside Step 13."""
        if self.sink is None:
            return
        self.sink.record(
            task_id=task.task_id,
            run_id=task.run_id,
            event_type=event_type,
            component_type="workflow",
            component_name=workflow.workflow_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            payload=payload,
            status=status.value,
            tags=["trace_v2", "compatibility_event"],
        )
