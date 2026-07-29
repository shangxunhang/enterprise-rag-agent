"""按 Architecture Spec 第 30 节固定优先级选择唯一合法路由。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
    RouterAction,
    RouterDecisionSchema,
    RouterSignalKind,
    RouterSignalSchema,
    TaskStatus,
)


class WorkflowRouter:
    _PRIORITIES = {
        RouterSignalKind.TASK_CANCELLED: 1,
        RouterSignalKind.STALE_RESULT: 1,
        RouterSignalKind.INPUT_SNAPSHOT_UPDATED: 1,
        RouterSignalKind.PERMISSION_ERROR: 2,
        RouterSignalKind.SCHEMA_ERROR: 2,
        RouterSignalKind.ARTIFACT_ERROR: 2,
        RouterSignalKind.SECURITY_ERROR: 2,
        RouterSignalKind.TASK_BUDGET_EXHAUSTED: 3,
        RouterSignalKind.TASK_DRAINING: 3,
        RouterSignalKind.TASK_HUMAN_REVIEW: 4,
        RouterSignalKind.SECTION_HUMAN_REVIEW: 5,
        RouterSignalKind.HARD_DEPENDENCY_BLOCK: 5,
        RouterSignalKind.REPLAN: 6,
        RouterSignalKind.RESEARCH_MORE: 7,
        RouterSignalKind.REWRITE: 8,
        RouterSignalKind.VERIFY: 9,
        RouterSignalKind.ACCEPT: 10,
        RouterSignalKind.PARTIAL_ACCEPT: 10,
        RouterSignalKind.REJECT: 10,
        RouterSignalKind.AGGREGATE_EXPORT: 11,
    }
    _ACTIONS = {
        RouterSignalKind.TASK_CANCELLED: RouterAction.CANCEL_TASK,
        RouterSignalKind.STALE_RESULT: RouterAction.DISCARD_STALE,
        RouterSignalKind.INPUT_SNAPSHOT_UPDATED: RouterAction.DISCARD_STALE,
        RouterSignalKind.PERMISSION_ERROR: RouterAction.REJECT_PROTOCOL,
        RouterSignalKind.SCHEMA_ERROR: RouterAction.REJECT_PROTOCOL,
        RouterSignalKind.ARTIFACT_ERROR: RouterAction.REJECT_PROTOCOL,
        RouterSignalKind.SECURITY_ERROR: RouterAction.REJECT_PROTOCOL,
        RouterSignalKind.TASK_BUDGET_EXHAUSTED: RouterAction.START_DRAINING,
        RouterSignalKind.TASK_DRAINING: RouterAction.START_DRAINING,
        RouterSignalKind.TASK_HUMAN_REVIEW: RouterAction.WAIT_TASK_HUMAN,
        RouterSignalKind.SECTION_HUMAN_REVIEW: RouterAction.WAIT_SECTION_HUMAN,
        RouterSignalKind.HARD_DEPENDENCY_BLOCK: RouterAction.BLOCK_SECTION,
        RouterSignalKind.REPLAN: RouterAction.REPLAN,
        RouterSignalKind.RESEARCH_MORE: RouterAction.RESEARCH_MORE,
        RouterSignalKind.REWRITE: RouterAction.REWRITE,
        RouterSignalKind.VERIFY: RouterAction.VERIFY,
        RouterSignalKind.ACCEPT: RouterAction.ACCEPT,
        RouterSignalKind.PARTIAL_ACCEPT: RouterAction.PARTIAL_ACCEPT,
        RouterSignalKind.REJECT: RouterAction.REJECT,
        RouterSignalKind.AGGREGATE_EXPORT: RouterAction.AGGREGATE_EXPORT,
    }

    def decide(
        self,
        state: MultiAgentSharedStateSchema,
        signals: list[RouterSignalSchema],
    ) -> RouterDecisionSchema | None:
        candidates = list(signals)
        existing_kinds = {signal.kind for signal in candidates}
        if (
            state.runtime_control.cancelled
            or state.task_context.task_status == TaskStatus.CANCELLED
        ) and RouterSignalKind.TASK_CANCELLED not in existing_kinds:
            candidates.append(
                RouterSignalSchema(
                    signal_id="runtime:task_cancelled",
                    kind=RouterSignalKind.TASK_CANCELLED,
                )
            )
        if (
            state.runtime_control.draining
            or state.task_context.task_status == TaskStatus.DRAINING
        ) and RouterSignalKind.TASK_DRAINING not in existing_kinds:
            candidates.append(
                RouterSignalSchema(
                    signal_id="runtime:task_draining",
                    kind=RouterSignalKind.TASK_DRAINING,
                )
            )
        if (
            state.task_context.task_status == TaskStatus.WAITING_HUMAN
            and RouterSignalKind.TASK_HUMAN_REVIEW not in existing_kinds
        ):
            candidates.append(
                RouterSignalSchema(
                    signal_id="runtime:task_human_review",
                    kind=RouterSignalKind.TASK_HUMAN_REVIEW,
                )
            )
        if not candidates:
            return None
        candidates.sort(
            key=lambda signal: (
                self._PRIORITIES[signal.kind],
                signal.signal_id,
            )
        )
        selected = candidates[0]
        return RouterDecisionSchema(
            action=self._ACTIONS[selected.kind],
            selected_signal_id=selected.signal_id,
            priority=self._PRIORITIES[selected.kind],
            section_id=selected.section_id,
            discarded_signal_ids=[
                signal.signal_id for signal in candidates[1:]
            ],
            reason=f"selected {selected.kind.value} by router priority",
        )
