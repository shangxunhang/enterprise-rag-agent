# =============================================================================
# 中文阅读说明：原生 Workflow 状态机：按节点执行 Agent/Tool，维护共享状态、重试、提交与失败传播。
# 主要定义：NativeWorkflowEngine。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Framework-neutral native workflow engine with explicit runtime contracts."""

from __future__ import annotations

from contextvars import copy_context
from queue import Empty, Queue
import threading
import time
import traceback
from typing import Any, Optional

from agent.agent_registry import AgentRegistry
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.graph_state_ops import GraphStateApplier, GraphStateProjector
from agent.runtime.node_adapter import AgentNodeAdapter
from agent.runtime.workflow_observer import WorkflowObserver
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from agent.runtime.workflow_state import WorkflowStateController
from contracts.observability import TraceSink
from core.error_factory import ErrorFactory
from core.runtime.clock import Clock, SystemClock
from observability.trace_context import activate_span
from schemas.agent import AgentResultSchema
from schemas.graph import (
    GraphNodeExecutionRecordSchema,
    GraphNodeInputSchema,
    GraphNodeOutputSchema,
    WorkflowEngineResultSchema,
    stable_graph_hash,
)
from schemas.status import ExecutionStatus, is_failure


# 阅读注释（类）：封装 native 工作流 engine，负责驱动实际运行流程并维护执行状态。
class NativeWorkflowEngine:
    """Current in-process implementation of ``WorkflowEnginePort``.

    Runtime guarantees:
    - every Agent executes against an isolated state copy;
    - physical state writes are checked against the step contract;
    - retry attempts do not commit business state;
    - failed nodes commit only the explicitly configured partial outputs;
    - timeout results cannot mutate canonical state after the deadline;
    - on_success/on_failure routes are interpreted by the engine.
    """

    engine_name = "native_workflow_engine"
    engine_version = "v2"

    # 阅读注释（函数）：初始化 NativeWorkflowEngine，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        agent_registry: AgentRegistry,
        run_trace_recorder: Optional[TraceSink] = None,
        *,
        state_controller: WorkflowStateController | None = None,
        observer: WorkflowObserver | None = None,
        projector: GraphStateProjector | None = None,
        applier: GraphStateApplier | None = None,
        node_adapter: AgentNodeAdapter | None = None,
        clock: Clock | None = None,
        error_factory: ErrorFactory | None = None,
    ) -> None:
        """初始化 NativeWorkflowEngine，保存运行所需的依赖、配置或状态。

        参数:
            agent_registry: Agent 注册表，具体约束请结合类型标注和调用方确认。
            run_trace_recorder: run Trace recorder，具体约束请结合类型标注和调用方确认。
            dispatcher: dispatcher，具体约束请结合类型标注和调用方确认。
            state_controller: 状态 controller，具体约束请结合类型标注和调用方确认。
            observer: observer，具体约束请结合类型标注和调用方确认。
            projector: projector，具体约束请结合类型标注和调用方确认。
            applier: applier，具体约束请结合类型标注和调用方确认。
            node_adapter: node 适配器，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。
            error_factory: 错误 工厂，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：WorkflowStateController, WorkflowObserver, GraphStateProjector, GraphStateApplier, AgentNodeAdapter, SystemClock。
        """
        self.agent_registry = agent_registry
        self.state_controller = state_controller or WorkflowStateController()
        self.observer = observer or WorkflowObserver(run_trace_recorder)
        self.projector = projector or GraphStateProjector()
        self.applier = applier or GraphStateApplier()
        self.node_adapter = node_adapter or AgentNodeAdapter(agent_registry)
        self.differ = self.node_adapter.differ
        self.clock = clock or SystemClock()
        self.error_factory = error_factory or ErrorFactory(self.clock)

    # 阅读注释（函数）：执行 NativeWorkflowEngine。
    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """执行 NativeWorkflowEngine。

        参数:
            workflow: 工作流，具体约束请结合类型标注和调用方确认。
            graph_state: graph 状态，具体约束请结合类型标注和调用方确认。

        返回:
            WorkflowEngineResultSchema

        阅读提示:
            主要直接调用：self.state_controller.start_workflow, sorted, enumerate, max, len, self.error_factory.create, self.state_controller.writer.add_error, self.state_controller.start_step。
        """
        node_inputs: list[GraphNodeInputSchema] = []
        node_outputs: list[GraphNodeOutputSchema] = []
        node_results: list[AgentResultSchema] = []
        # 记录工作流版本号
        initial_revision = graph_state.graph_revision
        graph_state.workflow_engine_name = self.engine_name
        graph_state.workflow_engine_version = self.engine_version
        self.state_controller.start_workflow(graph_state)

        ordered_steps = sorted(workflow.steps, key=lambda item: item.order)
        step_indexes = {item.step_id: index for index, item in enumerate(ordered_steps)}
        current_index = 0
        transition_count = 0
        retry_count = 0
        recovered_failure_count = 0
        workflow_complete = False
        terminal_reason = "not_started"
        terminal_failure_status: ExecutionStatus | None = None

        #设置一个随 Workflow 大小增长、同时又有最低下限的“保险丝”
        max_transitions = max(16, len(ordered_steps) * 8)

        # Workflow 主循环：按步骤顺序推进，每轮只处理一个节点。
        while 0 <= current_index < len(ordered_steps):
            #  路由死循环保护
            transition_count += 1
            if transition_count > max_transitions:
                terminal_failure_status = ExecutionStatus.FAILED
                terminal_reason = "routing_cycle_guard"
                error = self.error_factory.create(
                    error_code="WORKFLOW_ROUTING_CYCLE",
                    error_type="WorkflowRoutingError",
                    message=(
                        f"workflow exceeded {max_transitions} node transitions; "
                        "possible route cycle"
                    ),
                    user_visible_message="工作流路由出现循环，任务已终止。",
                    recoverable=False,
                    retryable=False,
                    failed_node=graph_state.current_node_id,
                    component=self.__class__.__name__,
                    step_name=graph_state.current_step,
                )
                self.state_controller.writer.add_error(graph_state, error)
                break
            # 获取当前要执行的 WorkflowStepSchema
            step = ordered_steps[current_index]
            # 将当前 step_id 写入 GraphState，标记当前正在执行哪个节点
            graph_state.current_node_id = step.step_id
            # 启动当前 step，并初始化/返回该 step 的运行状态
            step_state = self.state_controller.start_step(graph_state, step)
            # 初始化step尝试执行的历史
            attempt_history: list[dict[str, Any]] = []
            final_node_input: GraphNodeInputSchema | None = None
            final_node_output: GraphNodeOutputSchema | None = None

            max_attempts = 1 + step.max_retries

            for attempt in range(1, max_attempts + 1):
                step_state.attempt = attempt
                self.state_controller.writer.set_step_state(graph_state, step_state)
                node_input = self.projector.build_node_input(
                    workflow=workflow,
                    step=step,
                    state=graph_state,
                )
                final_node_input = node_input
                print(
                    f"[Workflow] START step={step.step_name} "
                    f"target={step.target_name} attempt={attempt}/{max_attempts}",
                    flush=True,
                )
                span_handle = self.observer.step_started(
                    graph_state,
                    workflow,
                    step,
                    node_input=node_input,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                with activate_span(span_handle):
                    # 执行节点并施加超时控制；节点异常会被转换为结构化失败结果。
                    node_output = self._execute_with_timeout(
                        step=step,
                        node_input=node_input,
                        graph_state=graph_state,
                        attempt=attempt,
                        max_attempts=max_attempts,
                    )

                will_retry = (
                    is_failure(node_output.status)
                    and attempt < max_attempts
                    and self._is_retryable(node_output)
                )
                # 判断重试 以及记录重试的状态
                if will_retry:
                    retry_count += 1
                    graph_state.context_bundle.runtime.retry_count += 1
                    node_output.metadata.update(
                        {
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "commit_decision": "retry_discarded",
                            "will_retry": True,
                        }
                    )
                    attempt_history.append(self._attempt_summary(node_output))
                    self.observer.step_finished(
                        graph_state,
                        workflow,
                        step,
                        node_output.result,
                        span_handle,
                        node_output=node_output,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        will_retry=True,
                    )
                    print(
                        f"[Workflow] RETRY step={step.step_name} "
                        f"status={node_output.status.value} next_attempt={attempt + 1}",
                        flush=True,
                    )
                    continue

                final_node_output = node_output
                break

            if final_node_input is None or final_node_output is None:
                raise RuntimeError(f"workflow step {step.step_id} produced no output")

            proposed_delta_sha256 = final_node_output.state_delta.delta_sha256
            # 根据节点状态和提交策略筛选可写回共享状态的数据增量。
            committed_delta, commit_decision = self._select_committed_delta(
                step=step,
                state=graph_state,
                node_output=final_node_output,
            )
            self.applier.apply(graph_state, committed_delta)
            final_node_output.state_delta = committed_delta
            final_node_output.metadata.update(
                {
                    "attempt": step_state.attempt,
                    "attempt_count": step_state.attempt,
                    "max_attempts": max_attempts,
                    "retry_count": step_state.attempt - 1,
                    "attempt_history": attempt_history,
                    "proposed_delta_sha256": proposed_delta_sha256,
                    "committed_delta_sha256": committed_delta.delta_sha256,
                    "commit_decision": commit_decision,
                    "will_retry": False,
                }
            )

            result = final_node_output.result
            self.state_controller.finish_step(graph_state, step_state, result)
            graph_state.completed_node_ids.append(step.step_id)
            graph_state.node_history.append(
                GraphNodeExecutionRecordSchema(
                    node_id=step.step_id,
                    node_name=step.step_name,
                    target_name=step.target_name,
                    status=final_node_output.status,
                    input_sha256=final_node_input.input_sha256,
                    delta_sha256=committed_delta.delta_sha256,
                    base_revision=committed_delta.base_revision,
                    next_revision=committed_delta.next_revision,
                    changed_paths=list(committed_delta.changed_paths),
                    started_at=final_node_output.started_at,
                    finished_at=final_node_output.finished_at,
                    latency_ms=final_node_output.latency_ms,
                    metadata={
                        "declared_read_keys": list(step.input_keys),
                        "declared_write_keys": list(step.output_keys),
                        "declared_write_paths": list(
                            committed_delta.declared_write_paths
                        ),
                        "missing_read_keys": list(final_node_input.missing_keys),
                        "attempt_count": step_state.attempt,
                        "retry_count": step_state.attempt - 1,
                        "commit_decision": commit_decision,
                    },
                )
            )
            step_state.metadata.update(
                {
                    "node_input_sha256": final_node_input.input_sha256,
                    "state_delta_sha256": committed_delta.delta_sha256,
                    "proposed_delta_sha256": proposed_delta_sha256,
                    "graph_revision": graph_state.graph_revision,
                    "changed_paths": list(committed_delta.changed_paths),
                    "attempt_count": step_state.attempt,
                    "retry_count": step_state.attempt - 1,
                    "attempt_history": attempt_history,
                    "commit_decision": commit_decision,
                }
            )
            self.state_controller.writer.set_step_state(graph_state, step_state)

            print(
                f"[Workflow] END   step={step.step_name} target={step.target_name} "
                f"status={result.status.value} revision={graph_state.graph_revision} "
                f"commit={commit_decision}",
                flush=True,
            )
            node_inputs.append(final_node_input)
            node_outputs.append(final_node_output)
            node_results.append(result)
            self.observer.step_finished(
                graph_state,
                workflow,
                step,
                result,
                span_handle,
                node_output=final_node_output,
                attempt=step_state.attempt,
                max_attempts=max_attempts,
                will_retry=False,
            )
            # 根据结果选择 success/failure 路由，然后决定是失败终止、正常结束，还是进入下一步。
            failed = is_failure(result.status)
            route = step.on_failure if failed else step.on_success
            if failed and route != "fail_task":
                recovered_failure_count += 1

            if route == "fail_task":
                terminal_failure_status = result.status
                terminal_reason = "node_failure"
                break
            if route in {"end", "complete", "stop"}:
                workflow_complete = True
                terminal_reason = f"route_{route}"
                break
            if route == "next":
                current_index += 1
                if current_index >= len(ordered_steps):
                    workflow_complete = True
                    terminal_reason = "all_steps_completed"
                continue

            # Explicit step-id route, validated by WorkflowDefinitionSchema.
            current_index = step_indexes[route]
            terminal_reason = f"route_{route}"

        if terminal_failure_status is not None:
            self.state_controller.finish_workflow(
                graph_state,
                terminal_failure_status,
            )
        else:
            if not ordered_steps:
                workflow_complete = True
                terminal_reason = "empty_workflow"
            final_status = (
                ExecutionStatus.PARTIAL_SUCCESS
                if recovered_failure_count > 0
                or any(
                    result.status == ExecutionStatus.PARTIAL_SUCCESS
                    for result in node_results
                )
                else ExecutionStatus.SUCCESS
            )
            self.state_controller.finish_workflow(graph_state, final_status)

        error = None
        if is_failure(graph_state.status):
            error = next(
                (item.error for item in reversed(node_results) if item.error is not None),
                None,
            )
            if error is None and graph_state.errors:
                error = graph_state.errors[-1]

        return WorkflowEngineResultSchema(
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            task_id=graph_state.task_id,
            run_id=graph_state.run_id,
            status=graph_state.status,
            node_inputs=node_inputs,
            node_outputs=node_outputs,
            node_results=node_results,
            completed_node_ids=[item.node_id for item in node_outputs],
            initial_revision=initial_revision,
            final_revision=graph_state.graph_revision,
            final_state_sha256=stable_graph_hash(
                graph_state.model_dump(mode="python")
            ),
            error=error,
            metadata={
                "state_schema_version": graph_state.schema_version,
                "contract_mode": "graph_state_node_delta_v2",
                "legacy_agent_adapter": True,
                "workflow_complete": workflow_complete,
                "terminal_reason": terminal_reason,
                "transition_count": transition_count,
                "retry_count": retry_count,
                "recovered_failure_count": recovered_failure_count,
                "write_contract_enforced": True,
                "failure_commit_policy_enforced": True,
                "timeout_guard": "isolated_daemon_thread_v1",
            },
        )

    # 阅读注释（函数）：执行 with timeout。
    def _execute_with_timeout(
        self,
        *,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema,
        graph_state: GraphStateSchema,
        attempt: int,
        max_attempts: int,
    ) -> GraphNodeOutputSchema:
        """Execute one isolated node under a cross-platform timeout guard.

        Python cannot safely kill arbitrary model code. The worker therefore
        runs as a daemon against an isolated GraphState copy. A late result is
        ignored and can never be committed to canonical state.
        """

        result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)
        context = copy_context()
        started_at = self.clock.now_iso()
        started = time.monotonic()

        # 阅读注释（函数）：处理 invoke 相关逻辑。
        def invoke() -> None:
            """处理 invoke 相关逻辑。

            返回:
                None

            阅读提示:
                主要直接调用：context.run, result_queue.put, traceback.format_exc。
            """
            try:
                output = context.run(
                    self.node_adapter.execute,
                    step=step,
                    node_input=node_input,
                    state=graph_state,
                )
                result_queue.put(("ok", output))
            except BaseException as exc:  # absolute runtime boundary
                result_queue.put(("error", (exc, traceback.format_exc())))

        worker = threading.Thread(
            target=invoke,
            name=f"workflow-{step.step_id}-attempt-{attempt}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=float(step.timeout_seconds))
        latency_ms = int(round((time.monotonic() - started) * 1000))

        if worker.is_alive():
            retryable = attempt < max_attempts
            return self._build_failure_output(
                step=step,
                node_input=node_input,
                state=graph_state,
                error_code="WORKFLOW_NODE_TIMEOUT",
                error_type="TimeoutError",
                message=(
                    f"workflow node {step.step_id} exceeded "
                    f"timeout_seconds={step.timeout_seconds}"
                ),
                user_message=f"工作流节点 {step.step_name} 执行超时。",
                retryable=retryable,
                started_at=started_at,
                latency_ms=latency_ms,
                metadata={
                    "timeout_seconds": step.timeout_seconds,
                    "late_result_discarded": True,
                    "timeout_guard": "isolated_daemon_thread_v1",
                },
            )

        try:
            kind, payload = result_queue.get_nowait()
        except Empty:
            return self._build_failure_output(
                step=step,
                node_input=node_input,
                state=graph_state,
                error_code="WORKFLOW_NODE_NO_RESULT",
                error_type="WorkflowRuntimeError",
                message="workflow node worker exited without a result",
                user_message=f"工作流节点 {step.step_name} 未返回结果。",
                retryable=attempt < max_attempts,
                started_at=started_at,
                latency_ms=latency_ms,
            )

        if kind == "ok":
            output: GraphNodeOutputSchema = payload
            output.metadata.update(
                {
                    "timeout_seconds": step.timeout_seconds,
                    "timeout_guard": "isolated_daemon_thread_v1",
                }
            )
            return output

        exc, stack_trace = payload
        return self._build_failure_output(
            step=step,
            node_input=node_input,
            state=graph_state,
            error_code="WORKFLOW_NODE_RUNTIME_EXCEPTION",
            error_type=exc.__class__.__name__,
            message=str(exc),
            user_message=f"工作流节点 {step.step_name} 执行失败。",
            retryable=attempt < max_attempts,
            started_at=started_at,
            latency_ms=latency_ms,
            stack_trace=stack_trace,
        )

    # 阅读注释（函数）：构建 failure 输出。
    def _build_failure_output(
        self,
        *,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema,
        state: GraphStateSchema,
        error_code: str,
        error_type: str,
        message: str,
        user_message: str,
        retryable: bool,
        started_at: str,
        latency_ms: int,
        stack_trace: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GraphNodeOutputSchema:
        """构建 failure 输出。

        参数:
            step: step，具体约束请结合类型标注和调用方确认。
            node_input: node 输入，具体约束请结合类型标注和调用方确认。
            state: 工作流共享状态。
            error_code: 错误 code，具体约束请结合类型标注和调用方确认。
            error_type: 错误 类型，具体约束请结合类型标注和调用方确认。
            message: 消息，具体约束请结合类型标注和调用方确认。
            user_message: user 消息，具体约束请结合类型标注和调用方确认。
            retryable: retryable，具体约束请结合类型标注和调用方确认。
            started_at: started at，具体约束请结合类型标注和调用方确认。
            latency_ms: latency ms，具体约束请结合类型标注和调用方确认。
            stack_trace: stack Trace，具体约束请结合类型标注和调用方确认。
            metadata: 随对象传递的元数据。

        返回:
            GraphNodeOutputSchema

        阅读提示:
            主要直接调用：self.error_factory.create, AgentResultSchema, error_code.lower, self.differ.diff, state.model_copy, GraphNodeOutputSchema, self.clock.now_iso, dict。
        """
        error = self.error_factory.create(
            error_code=error_code,
            error_type=error_type,
            message=message,
            user_visible_message=user_message,
            recoverable=retryable,
            retryable=retryable,
            failed_node=step.step_id,
            component=self.__class__.__name__,
            agent_name=step.target_name,
            step_name=step.step_name,
            stack_trace=stack_trace,
        )
        status = (
            ExecutionStatus.RETRYABLE_FAILED
            if retryable
            else ExecutionStatus.FAILED
        )
        result = AgentResultSchema(
            result_id=f"result_{state.run_id}_{step.step_id}_{error_code.lower()}",
            task_id=state.task_id,
            run_id=state.run_id,
            agent_name=step.target_name,
            agent_type="sub_agent",
            status=status,
            result_type="workflow_node_error",
            result={},
            error=error,
            error_message=error.message,
            need_human_review=True,
        )
        empty_delta = self.differ.diff(
            node_id=step.step_id,
            before=state,
            after=state.model_copy(deep=True),
            declared_write_keys=step.output_keys,
            declared_write_paths=step.write_paths,
        )
        return GraphNodeOutputSchema(
            node_id=step.step_id,
            node_name=step.step_name,
            node_type=step.step_type,
            target_name=step.target_name,
            status=status,
            result=result,
            state_delta=empty_delta,
            started_at=started_at,
            finished_at=self.clock.now_iso(),
            latency_ms=latency_ms,
            error=error,
            metadata={
                "node_input_sha256": node_input.input_sha256,
                "isolated_state_execution": True,
                **dict(metadata or {}),
            },
        )

    # 阅读注释（函数）：选择 committed delta。
    def _select_committed_delta(
        self,
        *,
        step: WorkflowStepSchema,
        state: GraphStateSchema,
        node_output: GraphNodeOutputSchema,
    ):
        """选择 committed delta。

        参数:
            step: step，具体约束请结合类型标注和调用方确认。
            state: 工作流共享状态。
            node_output: node 输出，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：is_failure, set, self.differ.restrict_delta, self.differ.diff, state.model_copy。
        """
        proposed = node_output.state_delta
        if not is_failure(node_output.status):
            return proposed, "success_commit"

        if step.commit_policy == "always":
            return proposed, "failure_commit_all"

        error_code = (
            node_output.result.error.error_code
            if node_output.result.error is not None
            else ""
        )
        if (
            step.commit_policy == "allow_partial_on_failure"
            and (
                not step.failure_commit_error_codes
                or error_code in set(step.failure_commit_error_codes)
            )
        ):
            restricted = self.differ.restrict_delta(
                before=state,
                proposed=proposed,
                declared_write_keys=[],
                declared_write_paths=step.failure_write_paths,
            )
            return restricted, "failure_commit_partial"

        empty = self.differ.diff(
            node_id=step.step_id,
            before=state,
            after=state.model_copy(deep=True),
            declared_write_keys=step.output_keys,
            declared_write_paths=step.write_paths,
        )
        return empty, "failure_discarded"

    # 阅读注释（函数）：判断 retryable。
    @staticmethod
    def _is_retryable(node_output: GraphNodeOutputSchema) -> bool:
        """判断 retryable。

        参数:
            node_output: node 输出，具体约束请结合类型标注和调用方确认。

        返回:
            bool

        阅读提示:
            主要直接调用：bool。
        """
        if node_output.status == ExecutionStatus.RETRYABLE_FAILED:
            return True
        return bool(
            node_output.result.error is not None
            and node_output.result.error.retryable
        )

    # 阅读注释（函数）：处理 attempt summary 相关逻辑。
    @staticmethod
    def _attempt_summary(node_output: GraphNodeOutputSchema) -> dict[str, Any]:
        """处理 attempt summary 相关逻辑。

        参数:
            node_output: node 输出，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：list。
        """
        error = node_output.result.error
        return {
            "status": node_output.status.value,
            "latency_ms": node_output.latency_ms,
            "error_code": error.error_code if error is not None else None,
            "error_type": error.error_type if error is not None else None,
            "proposed_delta_sha256": node_output.state_delta.delta_sha256,
            "changed_paths": list(node_output.state_delta.changed_paths),
        }
