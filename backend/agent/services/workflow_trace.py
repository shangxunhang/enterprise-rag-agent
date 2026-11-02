"""记录运行级和工作流级 Trace v2 span。

本服务负责创建 span 关联信息和构造稳定摘要，再把事件交给可注入 ``TraceSink``。
它不决定轨迹持久化格式；未配置 sink 时仍返回 span handle，使调用结构不需要分叉。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from observability.trace_summary import bounded_summary
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema


class WorkflowTraceService:
    """管理一次主链运行及其工作流子 span 的开始和结束事件。

    Attributes:
        sink: 可选结构化轨迹接收器；为空时不执行持久化副作用。
    """

    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        """创建工作流轨迹服务。

        Args:
            sink: 结构化 Trace 输出端口或 ``None``。
        """

        self.sink = sink

    def start_run(self, *, task: TaskSchema, component_name: str) -> TraceSpanHandle:
        """创建主链运行 span，并在可用时记录开始事件。

        Args:
            task: 当前执行任务。
            component_name: 运行入口或顶层组件名称。

        Returns:
            后续 ``finish_run`` 使用的 span handle。
        """

        # 第一步：以当前活动 span 为父级创建 server 类型顶层运行 span。
        handle = new_span(
            run_id=task.run_id,
            span_name=f"run:{component_name}",
            span_kind="server",
            parent=current_span(),
        )

        # 第二步：没有 sink 时只保留内存 handle，不改变主链调用方式。
        if self.sink is not None:
            # 第三步：记录有限输入摘要，不把完整用户文本和项目输入写入顶层事件。
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

    def finish_run(
        self,
        *,
        task: TaskSchema,
        component_name: str,
        handle: TraceSpanHandle,
        result: AgentResultSchema,
    ) -> None:
        """记录主链运行终态和输出摘要。

        Args:
            task: 当前执行任务。
            component_name: 与开始事件一致的顶层组件名称。
            handle: ``start_run`` 创建的 span handle。
            result: 顶层 Agent 最终结果。
        """

        # 第一步：无 sink 模式不产生任何轨迹副作用。
        if self.sink is None:
            return

        # 第二步：优先使用结构化错误，只有缺失时才读取兼容 error_message。
        error = result.error

        # 第三步：用同一 trace/span 标识结束运行，并计算 handle 自身记录的耗时。
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

    def start_workflow(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        payload: Dict[str, Any],
    ) -> TraceSpanHandle:
        """创建业务工作流子 span，并记录路由与步骤摘要。

        Args:
            task: 当前执行任务。
            workflow: 已选中的业务工作流定义。
            payload: 工作流开始事件的结构化业务载荷。

        Returns:
            后续 ``finish_workflow`` 使用的 span handle。
        """

        # 第一步：在当前运行 span 下创建 internal 类型工作流子 span。
        handle = new_span(
            run_id=task.run_id,
            span_name=f"workflow:{workflow.workflow_id}",
            span_kind="internal",
            parent=current_span(),
        )

        # 第二步：有 sink 时记录工作流版本、步骤数和有界路由摘要。
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

    def finish_workflow(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        handle: TraceSpanHandle,
        result: AgentResultSchema,
    ) -> None:
        """记录业务工作流终态、子 Agent 数量和失败摘要。

        Args:
            task: 当前执行任务。
            workflow: 已执行的业务工作流定义。
            handle: ``start_workflow`` 创建的 span handle。
            result: 工作流执行后的标准 Agent 结果。
        """

        # 第一步：未配置 sink 时直接返回。
        if self.sink is None:
            return

        # 第二步：提取结构化错误和 Supervisor 汇总的子 Agent 结果。
        error = result.error
        sub_results = (result.result or {}).get("sub_agent_results") or []

        # 第三步：统计不属于成功或部分成功的子 Agent 结果。
        failed_sub_agent_count = sum(
            1
            for item in sub_results
            if str(item.get("status") or "").lower()
            not in {
                "success",
                "partial_success",
                "executionstatus.success",
                "executionstatus.partial_success",
            }
        )

        # 第四步：写入工作流结束事件和有限输出摘要。
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
                "failed_sub_agent_count": failed_sub_agent_count,
                "error_code": error.error_code if error else None,
            },
            tags=["trace_v2", "workflow"],
        )

    def record(
        self,
        *,
        task: TaskSchema,
        workflow: WorkflowDefinitionSchema,
        event_type: str,
        status: ExecutionStatus,
        payload: Dict[str, Any],
    ) -> None:
        """为尚未迁移到 span 生命周期 API 的调用方记录兼容事件。

        Args:
            task: 事件所属任务。
            workflow: 事件所属工作流定义。
            event_type: 兼容事件类型。
            status: 事件发生时的规范状态。
            payload: 结构化事件载荷。
        """

        # 兼容接口同样遵守可选 sink 语义。
        if self.sink is None:
            return

        # 只记录单个事件，不创建或结束新的 span。
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
