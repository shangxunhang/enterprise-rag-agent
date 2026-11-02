"""记录工作流节点级 Agent Trace v2 事件。

``WorkflowObserver`` 在节点每次尝试开始和结束时创建、闭合 Agent span，并把节点
输入哈希、声明读取键、状态增量哈希、提交决定和重试信息写入轨迹。它只观察执行，
不修改 GraphState、不选择路由，也不决定是否重试或提交增量。
"""

from __future__ import annotations

from typing import Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from contracts.observability import TraceSink
from observability.trace_context import TraceSpanHandle, current_span, new_span
from schemas.agent import AgentResultSchema
from schemas.graph import GraphNodeInputSchema, GraphNodeOutputSchema
from schemas.status import ExecutionStatus


class WorkflowObserver:
    """把节点尝试生命周期转换为结构化 Agent 轨迹事件。

    Attributes:
        sink: 可选结构化轨迹接收器；为空时仍创建 span handle，但不写出事件。
    """

    def __init__(self, sink: Optional[TraceSink] = None) -> None:
        """创建节点观察器。

        Args:
            sink: Trace 输出端口或 ``None``。
        """

        self.sink = sink

    def step_started(
        self,
        state: SharedStateSchema,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> TraceSpanHandle:
        """为一次节点尝试创建 Agent span 并记录开始摘要。

        Args:
            state: 节点执行前的共享状态。
            workflow: 当前业务工作流定义。
            step: 当前步骤定义。
            node_input: 由状态投影器生成的受控节点输入；旧路径可为空。
            attempt: 当前尝试序号，从 1 开始。
            max_attempts: 包含首次执行在内的最大尝试次数。

        Returns:
            结束该次尝试时必须传回 ``step_finished`` 的 span handle。
        """

        # 第一步：在当前活动工作流 span 下创建当前 Agent 的 internal 子 span。
        handle = new_span(
            run_id=state.run_id,
            span_name=f"agent:{step.target_name}",
            span_kind="internal",
            parent=current_span(),
        )

        # 第二步：没有 sink 时仅返回关联 handle，保持执行路径和有轨迹模式一致。
        if self.sink is not None:
            # 第三步：记录节点身份、状态修订和受控输入摘要，不复制完整 GraphState。
            self.sink.record(
                task_id=state.task_id,
                run_id=state.run_id,
                event_type="agent_started",
                component_type="agent",
                component_name=step.target_name,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                step_id=step.step_id,
                step_name=step.step_name,
                step_order=step.order,
                agent_name=step.target_name,
                caller=workflow.workflow_id,
                callee=step.target_name,
                status=ExecutionStatus.RUNNING.value,
                phase="start",
                trace_id=handle.trace_id,
                span_id=handle.span_id,
                parent_span_id=handle.parent_span_id,
                span_name=handle.span_name,
                span_kind=handle.span_kind,
                started_at=handle.started_at,
                payload={
                    "workflow_id": workflow.workflow_id,
                    "workflow_version": workflow.workflow_version,
                },
                input_summary={
                    "task_type": state.task_type,
                    "step_id": step.step_id,
                    "step_name": step.step_name,
                    "step_order": step.order,
                    "target_name": step.target_name,
                    "current_state_status": getattr(state.status, "value", state.status),
                    "graph_state_schema": getattr(state, "schema_version", None),
                    "graph_revision": getattr(state, "graph_revision", None),
                    # 输入哈希允许重放时确认节点看到的投影是否一致。
                    "node_input_sha256": (
                        node_input.input_sha256 if node_input is not None else None
                    ),
                    "declared_read_keys": (
                        list(node_input.declared_read_keys)
                        if node_input is not None
                        else []
                    ),
                    "missing_read_keys": (
                        list(node_input.missing_keys) if node_input is not None else []
                    ),
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
                tags=["trace_v2", "agent", "workflow_step"],
            )

        return handle

    def step_finished(
        self,
        state: SharedStateSchema,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        result: AgentResultSchema,
        handle: TraceSpanHandle,
        node_output: GraphNodeOutputSchema | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
        will_retry: bool = False,
    ) -> None:
        """记录一次节点尝试的结果、状态增量和后续重试决定。

        Args:
            state: 当前真实共享状态；若增量已提交，其修订号已经更新。
            workflow: 当前业务工作流定义。
            step: 当前步骤定义。
            result: Agent 返回的标准结果。
            handle: ``step_started`` 为本次尝试创建的 span handle。
            node_output: 节点执行器生成的标准输出和状态增量；旧路径可为空。
            attempt: 当前尝试序号。
            max_attempts: 包含首次执行在内的最大尝试次数。
            will_retry: 当前失败后执行器是否还会再次尝试该节点。
        """

        # 第一步：无 sink 模式不产生轨迹副作用。
        if self.sink is None:
            return

        # 第二步：优先使用结果中的结构化错误，供 phase 和错误摘要共同判断。
        error = result.error

        # 第三步：从节点输出提取状态 delta 和提交元数据；不存在时使用兼容空值。
        delta_sha256 = (
            node_output.state_delta.delta_sha256
            if node_output is not None
            else None
        )
        changed_path_count = (
            len(node_output.state_delta.changed_paths)
            if node_output is not None
            else 0
        )
        commit_decision = (
            (node_output.metadata or {}).get("commit_decision")
            if node_output is not None
            else None
        )

        # 第四步：记录与开始事件相同 span 的结束事件及有限结果摘要。
        self.sink.record(
            task_id=state.task_id,
            run_id=state.run_id,
            event_type="agent_finished",
            component_type="agent",
            component_name=result.agent_name,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            step_id=step.step_id,
            step_name=step.step_name,
            step_order=step.order,
            agent_name=result.agent_name,
            caller=workflow.workflow_id,
            callee=result.agent_name,
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
            payload={"agent_result": result.model_dump(mode="json")},
            output_summary={
                "status": result.status.value,
                "result_type": result.result_type,
                "need_human_review": result.need_human_review,
                "warning_count": len(result.warnings or []),
                "citation_count": len(result.citations or []),
                "error_code": error.error_code if error else None,
                "error_type": error.error_type if error else None,
                # 这里记录结束时真实状态修订，而不是节点副本中的预期修订。
                "graph_revision": getattr(state, "graph_revision", None),
                "state_delta_sha256": delta_sha256,
                "changed_path_count": changed_path_count,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "will_retry": will_retry,
                "commit_decision": commit_decision,
            },
            tags=["trace_v2", "agent", "workflow_step"],
        )
