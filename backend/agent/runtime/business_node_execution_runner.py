"""Framework-neutral worker boundary shared by workflow engines."""

from __future__ import annotations

from contextvars import copy_context
from dataclasses import dataclass, field
from queue import Empty, Queue
import threading
import time
import traceback
from typing import Any, Callable, Literal

from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    WorkflowExecutionControl,
    activate_execution_control,
)
from core.runtime.side_effects import (
    InMemorySideEffectCommitStore,
    SideEffectCommitStore,
)


NodeExecutionOutcomeKind = Literal[
    "ok",
    "error",
    "timeout",
    "cancelled",
    "no_result",
    "external_commit_error",
]


@dataclass(frozen=True)
class WorkflowNodeExecutionOutcome:
    """Result of one isolated, deadline-bound node attempt."""

    kind: NodeExecutionOutcomeKind
    started_at: str
    finished_at: str
    latency_ms: int
    execution_control: WorkflowExecutionControl = field(repr=False)
    value: Any = None
    exception: BaseException | None = field(default=None, repr=False)
    stack_trace: str | None = None
    worker_still_active: bool = False
    external_commit_summary: dict[str, int] = field(default_factory=dict)


class WorkflowNodeExecutionRunner:
    """Own worker, deadline, cancellation, side-effect and idle lifecycles."""

    def __init__(
        self,
        *,
        side_effect_commit_store: SideEffectCommitStore | None = None,
        now_iso: Callable[[], str],
    ) -> None:
        self.side_effect_commit_store = (
            side_effect_commit_store or InMemorySideEffectCommitStore()
        )
        self.now_iso = now_iso
        self._worker_condition = threading.Condition()
        self._active_worker_controls: dict[str, WorkflowExecutionControl] = {}
        self._idle_callbacks: list[Callable[[], None]] = []
        self._idle_finalizer_active = False

    @property
    def active_worker_count(self) -> int:
        with self._worker_condition:
            return len(self._active_worker_controls)

    def cancel_active_workers(self, reason: str = "workflow_shutdown") -> int:
        with self._worker_condition:
            controls = tuple(self._active_worker_controls.values())
        for control in controls:
            control.cancel(reason)
        return len(controls)

    def wait_for_idle(self, timeout_seconds: float | None = None) -> bool:
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
                try:
                    callback()
                except BaseException:
                    pass

    def execute(
        self,
        callback: Callable[[WorkflowExecutionControl], Any],
        *,
        execution_id: str,
        commit_scope_id: str,
        timeout_seconds: float,
        thread_name: str,
        attempt_succeeded: Callable[[Any], bool],
    ) -> WorkflowNodeExecutionOutcome:
        """Run one callback without allowing a late worker to commit."""

        timeout = max(0.001, float(timeout_seconds))
        started_at = self.now_iso()
        started = time.monotonic()
        result_queue: Queue[tuple[str, Any, float]] = Queue(maxsize=1)
        context = copy_context()
        control = WorkflowExecutionControl.with_timeout(
            execution_id=execution_id,
            timeout_seconds=timeout,
            commit_scope_id=commit_scope_id,
            side_effect_store=self.side_effect_commit_store,
        )

        def invoke() -> None:
            try:
                def run_callback():
                    with activate_execution_control(control):
                        return callback(control)

                value = context.run(run_callback)
                result_queue.put(("ok", value, time.monotonic()))
            except BaseException as exc:
                result_queue.put(
                    ("error", (exc, traceback.format_exc()), time.monotonic())
                )
            finally:
                self._worker_finished(execution_id)

        worker = threading.Thread(
            target=invoke,
            name=thread_name,
            daemon=True,
        )
        self._register_worker(execution_id, control)
        try:
            worker.start()
        except BaseException:
            self._worker_finished(execution_id)
            raise
        worker.join(timeout=timeout)
        latency_ms = int(round((time.monotonic() - started) * 1000))
        worker_still_active = worker.is_alive()

        try:
            kind, payload, completed_monotonic = result_queue.get_nowait()
        except Empty:
            if worker_still_active:
                control.cancel("deadline_exceeded")
                return self._outcome(
                    "timeout",
                    started_at=started_at,
                    latency_ms=latency_ms,
                    control=control,
                    worker_still_active=True,
                )
            return self._outcome(
                "no_result",
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
            )

        if completed_monotonic >= control.deadline_monotonic:
            control.cancel("deadline_exceeded")
        if control.cancel_reason == "deadline_exceeded":
            return self._outcome(
                "timeout",
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
                worker_still_active=worker_still_active,
            )
        if control.is_cancelled:
            return self._outcome(
                "cancelled",
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
                worker_still_active=worker_still_active,
            )

        succeeded = bool(kind == "ok" and attempt_succeeded(payload))
        try:
            commit_summary = control.finalize_external_commits(
                attempt_succeeded=succeeded
            )
        except WorkflowExecutionCancelled as exc:
            return self._outcome(
                (
                    "timeout"
                    if control.cancel_reason == "deadline_exceeded"
                    else "cancelled"
                ),
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
                exception=exc,
            )
        except Exception as exc:
            return self._outcome(
                "external_commit_error",
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
                exception=exc,
                stack_trace=traceback.format_exc(),
            )

        if kind == "ok":
            return self._outcome(
                "ok",
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
                value=payload,
                external_commit_summary=commit_summary,
            )
        exc, stack_trace = payload
        if isinstance(exc, WorkflowExecutionCancelled):
            control.cancel(control.cancel_reason or "cancelled")
            return self._outcome(
                "cancelled",
                started_at=started_at,
                latency_ms=latency_ms,
                control=control,
                exception=exc,
                stack_trace=stack_trace,
                external_commit_summary=commit_summary,
            )
        return self._outcome(
            "error",
            started_at=started_at,
            latency_ms=latency_ms,
            control=control,
            exception=exc,
            stack_trace=stack_trace,
            external_commit_summary=commit_summary,
        )

    def _outcome(
        self,
        kind: NodeExecutionOutcomeKind,
        *,
        started_at: str,
        latency_ms: int,
        control: WorkflowExecutionControl,
        value: Any = None,
        exception: BaseException | None = None,
        stack_trace: str | None = None,
        worker_still_active: bool = False,
        external_commit_summary: dict[str, int] | None = None,
    ) -> WorkflowNodeExecutionOutcome:
        return WorkflowNodeExecutionOutcome(
            kind=kind,
            started_at=started_at,
            finished_at=self.now_iso(),
            latency_ms=latency_ms,
            execution_control=control,
            value=value,
            exception=exception,
            stack_trace=stack_trace,
            worker_still_active=worker_still_active,
            external_commit_summary=dict(external_commit_summary or {}),
        )
