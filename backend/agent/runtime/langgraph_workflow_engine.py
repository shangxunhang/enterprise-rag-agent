"""LangGraph-backed workflow engine preserving enterprise runtime contracts."""

from __future__ import annotations

from contextvars import copy_context
from queue import Empty, Queue
import threading
import time
import traceback
from typing import Any, Callable, Optional

from typing_extensions import TypedDict
from langgraph.graph import END, START, StateGraph

from agent.agent_registry import AgentRegistry
from core.runtime.execution_control import (
    WorkflowExecutionControl,
    activate_execution_control,
)
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


class _LangGraphRuntimeState(TypedDict, total=False):
    """Internal orchestration envelope; business state remains GraphStateSchema."""

    graph_state: GraphStateSchema
    node_inputs: list[GraphNodeInputSchema]
    node_outputs: list[GraphNodeOutputSchema]
    node_results: list[AgentResultSchema]
    transition_count: int
    retry_count: int
    recovered_failure_count: int
    workflow_complete: bool
    terminal_reason: str
    terminal_failure_status: ExecutionStatus | None
    next_node: str


class LangGraphWorkflowEngine:
    """Execute ``WorkflowDefinitionSchema`` with LangGraph ``StateGraph``.

    LangGraph owns topology and routing. Existing enterprise contracts remain
    framework-neutral and are intentionally preserved:
    - Agents execute against isolated GraphState copies;
    - write paths are validated before commit;
    - retry attempts never commit business state;
    - failure commit policy remains explicit;
    - timeout workers cannot mutate canonical state after the deadline;
    - node input/output, revision and trace schemas stay unchanged.
    """

    engine_name = "langgraph_workflow_engine"
    engine_version = "v1"
    _END_ROUTE = "__workflow_end__"

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
        self.agent_registry = agent_registry
        self.state_controller = state_controller or WorkflowStateController()
        self.observer = observer or WorkflowObserver(run_trace_recorder)
        self.projector = projector or GraphStateProjector()
        self.applier = applier or GraphStateApplier()
        self.node_adapter = node_adapter or AgentNodeAdapter(agent_registry)
        self.differ = self.node_adapter.differ
        self.clock = clock or SystemClock()
        self.error_factory = error_factory or ErrorFactory(self.clock)
        self._worker_condition = threading.Condition()
        self._active_worker_controls: dict[str, WorkflowExecutionControl] = {}
        self._idle_callbacks: list[Callable[[], None]] = []
        self._idle_finalizer_active = False

    @property
    def active_worker_count(self) -> int:
        """Return node workers that have not physically exited yet."""

        with self._worker_condition:
            return len(self._active_worker_controls)

    def cancel_active_workers(self, reason: str = "workflow_shutdown") -> int:
        """Cooperatively cancel all active workers and return their count."""

        with self._worker_condition:
            controls = tuple(self._active_worker_controls.values())
        for control in controls:
            control.cancel(reason)
        return len(controls)

    def wait_for_idle(self, timeout_seconds: float | None = None) -> bool:
        """Wait until all workers exit; primarily useful for bounded shutdown/tests."""

        timeout = None if timeout_seconds is None else max(0.0, timeout_seconds)
        with self._worker_condition:
            return self._worker_condition.wait_for(
                lambda: (
                    not self._active_worker_controls
                    and not self._idle_finalizer_active
                    and not self._idle_callbacks
                ),
                timeout=timeout,
            )

    def defer_until_idle(self, callback: Callable[[], None]) -> bool:
        """Run ``callback`` now when idle, otherwise once the last worker exits.

        The boolean return value is ``True`` when execution was deferred.  The
        callback is best-effort because shutdown failures must not replace the
        already-produced workflow result.
        """

        with self._worker_condition:
            if self._active_worker_controls or self._idle_finalizer_active:
                self._idle_callbacks.append(callback)
                return True
            self._idle_callbacks.append(callback)
            self._idle_finalizer_active = True
        self._finalize_idle_callbacks()
        return False

    def _register_worker(
        self,
        worker_id: str,
        control: WorkflowExecutionControl,
    ) -> None:
        with self._worker_condition:
            while self._idle_finalizer_active:
                self._worker_condition.wait()
            self._active_worker_controls[worker_id] = control

    def _worker_finished(self, worker_id: str) -> None:
        should_finalize = False
        with self._worker_condition:
            self._active_worker_controls.pop(worker_id, None)
            if not self._active_worker_controls and not self._idle_finalizer_active:
                self._idle_finalizer_active = True
                should_finalize = True
        if should_finalize:
            self._finalize_idle_callbacks()

    def _finalize_idle_callbacks(self) -> None:
        """Drain finalizers before publishing the observable idle state."""

        while True:
            with self._worker_condition:
                if self._active_worker_controls:
                    self._idle_finalizer_active = False
                    self._worker_condition.notify_all()
                    return
                callbacks = self._idle_callbacks
                self._idle_callbacks = []
                if not callbacks:
                    self._idle_finalizer_active = False
                    self._worker_condition.notify_all()
                    return
            for callback in callbacks:
                self._run_idle_callback(callback)

    @staticmethod
    def _run_idle_callback(callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException:
            # Finalizers are best-effort and must never leave the engine's idle
            # state permanently latched, including on cancellation control flow.
            pass

    def execute(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
    ) -> WorkflowEngineResultSchema:
        """Compile the declared workflow to a StateGraph and execute it."""

        initial_revision = graph_state.graph_revision
        graph_state.workflow_engine_name = self.engine_name
        graph_state.workflow_engine_version = self.engine_version
        self.state_controller.start_workflow(graph_state)

        ordered_steps = sorted(workflow.steps, key=lambda item: item.order)
        max_transitions = max(16, len(ordered_steps) * 8)
        runtime: _LangGraphRuntimeState = {
            "graph_state": graph_state,
            "node_inputs": [],
            "node_outputs": [],
            "node_results": [],
            "transition_count": 0,
            "retry_count": 0,
            "recovered_failure_count": 0,
            "workflow_complete": False,
            "terminal_reason": "not_started",
            "terminal_failure_status": None,
            "next_node": self._END_ROUTE,
        }

        if not ordered_steps:
            runtime["workflow_complete"] = True
            runtime["terminal_reason"] = "empty_workflow"
        else:
            compiled = self._compile(workflow, ordered_steps, max_transitions)
            runtime = compiled.invoke(
                runtime,
                config={"recursion_limit": max_transitions + 4},
            )

        node_results = list(runtime.get("node_results", []))
        terminal_failure_status = runtime.get("terminal_failure_status")
        if terminal_failure_status is not None:
            self.state_controller.finish_workflow(graph_state, terminal_failure_status)
        else:
            final_status = (
                ExecutionStatus.PARTIAL_SUCCESS
                if int(runtime.get("recovered_failure_count", 0)) > 0
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

        node_inputs = list(runtime.get("node_inputs", []))
        node_outputs = list(runtime.get("node_outputs", []))
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
            final_state_sha256=stable_graph_hash(graph_state.model_dump(mode="python")),
            error=error,
            metadata={
                "state_schema_version": graph_state.schema_version,
                "contract_mode": "graph_state_node_delta_v2",
                "orchestration_runtime": "langgraph_state_graph",
                "workflow_complete": bool(runtime.get("workflow_complete", False)),
                "terminal_reason": str(runtime.get("terminal_reason", "not_started")),
                "transition_count": int(runtime.get("transition_count", 0)),
                "retry_count": int(runtime.get("retry_count", 0)),
                "recovered_failure_count": int(
                    runtime.get("recovered_failure_count", 0)
                ),
                "write_contract_enforced": True,
                "failure_commit_policy_enforced": True,
                "timeout_guard": "cooperative_cancel_tracked_worker_v2",
                "active_background_workers": self.active_worker_count,
                "cooperative_cancellation": True,
            },
        )

    def _compile(
        self,
        workflow: WorkflowDefinitionSchema,
        ordered_steps: list[WorkflowStepSchema],
        max_transitions: int,
    ):
        """Compile framework-neutral workflow declarations into LangGraph."""

        builder = StateGraph(_LangGraphRuntimeState)
        step_ids = [step.step_id for step in ordered_steps]
        all_routes = {step_id: step_id for step_id in step_ids}
        all_routes[self._END_ROUTE] = END

        for index, step in enumerate(ordered_steps):
            next_step_id = (
                ordered_steps[index + 1].step_id
                if index + 1 < len(ordered_steps)
                else None
            )
            builder.add_node(
                step.step_id,
                self._build_step_node(
                    workflow=workflow,
                    step=step,
                    next_step_id=next_step_id,
                    max_transitions=max_transitions,
                ),
            )
            builder.add_conditional_edges(
                step.step_id,
                self._next_node,
                all_routes,
            )

        builder.add_edge(START, ordered_steps[0].step_id)
        return builder.compile()

    def _build_step_node(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        next_step_id: str | None,
        max_transitions: int,
    ):
        def execute_step(workflow_state: _LangGraphRuntimeState) -> dict[str, Any]:
            graph_state = workflow_state["graph_state"]
            transition_count = int(workflow_state.get("transition_count", 0)) + 1
            if transition_count > max_transitions:
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
                return {
                    "graph_state": graph_state,
                    "transition_count": transition_count,
                    "terminal_failure_status": ExecutionStatus.FAILED,
                    "terminal_reason": "routing_cycle_guard",
                    "next_node": self._END_ROUTE,
                }

            graph_state.current_node_id = step.step_id
            execution = self._execute_step(workflow, step, graph_state)
            result = execution["node_output"].result
            failed = is_failure(result.status)
            route = step.on_failure if failed else step.on_success
            recovered_failure_count = int(workflow_state.get("recovered_failure_count", 0))
            if failed and route != "fail_task":
                recovered_failure_count += 1

            workflow_complete = bool(workflow_state.get("workflow_complete", False))
            terminal_reason = str(workflow_state.get("terminal_reason", "not_started"))
            terminal_failure_status = workflow_state.get("terminal_failure_status")

            if route == "fail_task":
                terminal_failure_status = result.status
                terminal_reason = "node_failure"
                destination = self._END_ROUTE
            elif route in {"end", "complete", "stop"}:
                workflow_complete = True
                terminal_reason = f"route_{route}"
                destination = self._END_ROUTE
            elif route == "next":
                if next_step_id is None:
                    workflow_complete = True
                    terminal_reason = "all_steps_completed"
                    destination = self._END_ROUTE
                else:
                    destination = next_step_id
            else:
                destination = route
                terminal_reason = f"route_{route}"

            return {
                "graph_state": graph_state,
                "node_inputs": [*workflow_state.get("node_inputs", []), execution["node_input"]],
                "node_outputs": [
                    *workflow_state.get("node_outputs", []),
                    execution["node_output"],
                ],
                "node_results": [*workflow_state.get("node_results", []), result],
                "transition_count": transition_count,
                "retry_count": int(workflow_state.get("retry_count", 0))
                + int(execution["retry_count"]),
                "recovered_failure_count": recovered_failure_count,
                "workflow_complete": workflow_complete,
                "terminal_reason": terminal_reason,
                "terminal_failure_status": terminal_failure_status,
                "next_node": destination,
            }

        return execute_step

    def _execute_step(
        self,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        graph_state: GraphStateSchema,
    ) -> dict[str, Any]:
        """Execute one node while preserving existing enterprise contracts."""

        step_state = self.state_controller.start_step(graph_state, step)
        attempt_history: list[dict[str, Any]] = []
        final_node_input: GraphNodeInputSchema | None = None
        final_node_output: GraphNodeOutputSchema | None = None
        max_attempts = 1 + step.max_retries
        retry_count = 0
        span_handle = None

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

        if final_node_input is None or final_node_output is None or span_handle is None:
            raise RuntimeError(f"workflow step {step.step_id} produced no output")

        proposed_delta_sha256 = final_node_output.state_delta.delta_sha256
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
                    "declared_write_paths": list(committed_delta.declared_write_paths),
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
        return {
            "node_input": final_node_input,
            "node_output": final_node_output,
            "retry_count": retry_count,
        }

    @staticmethod
    def _next_node(workflow_state: _LangGraphRuntimeState) -> str:
        return str(workflow_state.get("next_node", LangGraphWorkflowEngine._END_ROUTE))

    def _execute_with_timeout(
        self,
        *,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema,
        graph_state: GraphStateSchema,
        attempt: int,
        max_attempts: int,
    ) -> GraphNodeOutputSchema:
        """Execute an isolated node with a tracked cooperative timeout boundary."""

        result_queue: Queue[tuple[str, Any, float]] = Queue(maxsize=1)
        context = copy_context()
        started_at = self.clock.now_iso()
        started = time.monotonic()
        worker_id = (
            f"{graph_state.run_id}:{step.step_id}:{attempt}:{time.monotonic_ns()}"
        )
        execution_control = WorkflowExecutionControl.with_timeout(
            execution_id=worker_id,
            timeout_seconds=float(step.timeout_seconds),
        )

        def invoke() -> None:
            try:
                def execute_node() -> GraphNodeOutputSchema:
                    with activate_execution_control(execution_control):
                        return self.node_adapter.execute(
                            step=step,
                            node_input=node_input,
                            state=graph_state,
                            execution_control=execution_control,
                        )

                output = context.run(execute_node)
                result_queue.put(("ok", output, time.monotonic()))
            except BaseException as exc:
                result_queue.put(
                    ("error", (exc, traceback.format_exc()), time.monotonic())
                )
            finally:
                self._worker_finished(worker_id)

        worker = threading.Thread(
            target=invoke,
            name=f"workflow-{step.step_id}-attempt-{attempt}",
            daemon=True,
        )
        self._register_worker(worker_id, execution_control)
        try:
            worker.start()
        except BaseException:
            self._worker_finished(worker_id)
            raise
        worker.join(timeout=float(step.timeout_seconds))
        latency_ms = int(round((time.monotonic() - started) * 1000))

        worker_still_active = worker.is_alive()
        try:
            kind, payload, completed_monotonic = result_queue.get_nowait()
        except Empty:
            if worker_still_active:
                execution_control.cancel("deadline_exceeded")
                return self._build_timeout_output(
                    step=step,
                    node_input=node_input,
                    state=graph_state,
                    started_at=started_at,
                    latency_ms=latency_ms,
                    execution_control=execution_control,
                    worker_still_active=True,
                )
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

        if (
            completed_monotonic >= execution_control.deadline_monotonic
            and not execution_control.is_cancelled
        ):
            execution_control.cancel("deadline_exceeded")
        if execution_control.cancel_reason == "deadline_exceeded":
            return self._build_timeout_output(
                step=step,
                node_input=node_input,
                state=graph_state,
                started_at=started_at,
                latency_ms=latency_ms,
                execution_control=execution_control,
                worker_still_active=False,
            )
        if execution_control.is_cancelled:
            return self._build_failure_output(
                step=step,
                node_input=node_input,
                state=graph_state,
                error_code="WORKFLOW_NODE_CANCELLED",
                error_type="WorkflowExecutionCancelled",
                message=(
                    f"workflow node {step.step_id} cancelled: "
                    f"{execution_control.cancel_reason or 'cancelled'}"
                ),
                user_message=f"workflow node {step.step_name} was cancelled.",
                retryable=False,
                started_at=started_at,
                latency_ms=latency_ms,
                metadata={
                    "execution_control": execution_control.metadata(),
                    "retry_suppressed_after_cancellation": True,
                },
            )

        if kind == "ok":
            output: GraphNodeOutputSchema = payload
            output.metadata.update(
                {
                    "timeout_seconds": step.timeout_seconds,
                    "timeout_guard": "cooperative_cancel_tracked_worker_v2",
                    "execution_control": execution_control.metadata(),
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

    def _build_timeout_output(
        self,
        *,
        step: WorkflowStepSchema,
        node_input: GraphNodeInputSchema,
        state: GraphStateSchema,
        started_at: str,
        latency_ms: int,
        execution_control: WorkflowExecutionControl,
        worker_still_active: bool,
    ) -> GraphNodeOutputSchema:
        """Build one deterministic, non-retryable timeout result."""

        return self._build_failure_output(
            step=step,
            node_input=node_input,
            state=state,
            error_code="WORKFLOW_NODE_TIMEOUT",
            error_type="TimeoutError",
            message=(
                f"workflow node {step.step_id} exceeded "
                f"timeout_seconds={step.timeout_seconds}"
            ),
            user_message=f"工作流节点 {step.step_name} 执行超时。",
            # Retrying may overlap the original native/RAG call. The current
            # process cannot safely prove that its side effects have ended.
            retryable=False,
            started_at=started_at,
            latency_ms=latency_ms,
            metadata={
                "timeout_seconds": step.timeout_seconds,
                "late_result_discarded": True,
                "timeout_guard": "cooperative_cancel_tracked_worker_v2",
                "cooperative_cancel_requested": True,
                "timeout_completed_cooperatively": not worker_still_active,
                "retry_suppressed_after_timeout": True,
                "worker_still_active_at_timeout": worker_still_active,
                "active_background_workers": self.active_worker_count,
                "execution_control": execution_control.metadata(),
            },
        )

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
            ExecutionStatus.RETRYABLE_FAILED if retryable else ExecutionStatus.FAILED
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

    def _select_committed_delta(
        self,
        *,
        step: WorkflowStepSchema,
        state: GraphStateSchema,
        node_output: GraphNodeOutputSchema,
    ):
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

    @staticmethod
    def _is_retryable(node_output: GraphNodeOutputSchema) -> bool:
        if node_output.status == ExecutionStatus.RETRYABLE_FAILED:
            return True
        return bool(
            node_output.result.error is not None
            and node_output.result.error.retryable
        )

    @staticmethod
    def _attempt_summary(node_output: GraphNodeOutputSchema) -> dict[str, Any]:
        error = node_output.result.error
        return {
            "status": node_output.status.value,
            "latency_ms": node_output.latency_ms,
            "error_code": error.error_code if error is not None else None,
            "error_type": error.error_type if error is not None else None,
            "proposed_delta_sha256": node_output.state_delta.delta_sha256,
            "changed_paths": list(node_output.state_delta.changed_paths),
        }
