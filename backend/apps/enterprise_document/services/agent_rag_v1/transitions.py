"""Section 与 Task 状态迁移白名单。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import SectionStatus, TaskStatus


class StateTransitionError(ValueError):
    pass


class StateTransitionValidator:
    _SECTION_TRANSITIONS = {
        SectionStatus.PENDING: {
            SectionStatus.PLANNED,
            SectionStatus.SKIPPED,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.FAILED,
        },
        SectionStatus.PLANNED: {
            SectionStatus.RESEARCHING,
            SectionStatus.GENERATING,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.BLOCKED,
            SectionStatus.SKIPPED,
            SectionStatus.FAILED,
        },
        SectionStatus.RESEARCHING: {
            SectionStatus.EVIDENCE_READY,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.BLOCKED,
            SectionStatus.FAILED,
        },
        SectionStatus.EVIDENCE_READY: {
            SectionStatus.GENERATING,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.BLOCKED,
            SectionStatus.FAILED,
        },
        SectionStatus.GENERATING: {
            SectionStatus.PAUSED_FOR_PRECHECK,
            SectionStatus.GENERATED,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.BLOCKED,
            SectionStatus.FAILED,
        },
        SectionStatus.PAUSED_FOR_PRECHECK: {
            SectionStatus.GENERATING,
            SectionStatus.GENERATED,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.BLOCKED,
            SectionStatus.FAILED,
        },
        SectionStatus.GENERATED: {
            SectionStatus.VERIFYING,
            SectionStatus.FAILED,
        },
        SectionStatus.VERIFYING: {
            SectionStatus.ACCEPTED,
            SectionStatus.PARTIALLY_ACCEPTED,
            SectionStatus.RESEARCHING,
            SectionStatus.GENERATING,
            SectionStatus.PLANNED,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.BLOCKED,
            SectionStatus.FAILED,
        },
        SectionStatus.WAITING_HUMAN: {
            SectionStatus.PLANNED,
            SectionStatus.RESEARCHING,
            SectionStatus.GENERATING,
            SectionStatus.VERIFYING,
            SectionStatus.BLOCKED,
            SectionStatus.FAILED,
        },
        SectionStatus.BLOCKED: {
            SectionStatus.PLANNED,
            SectionStatus.RESEARCHING,
            SectionStatus.GENERATING,
            SectionStatus.VERIFYING,
            SectionStatus.WAITING_HUMAN,
            SectionStatus.FAILED,
        },
        SectionStatus.ACCEPTED: set(),
        SectionStatus.PARTIALLY_ACCEPTED: set(),
        SectionStatus.FAILED: set(),
        SectionStatus.SKIPPED: set(),
    }

    _TASK_TRANSITIONS = {
        TaskStatus.PENDING: {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.WAITING_HUMAN,
            TaskStatus.DRAINING,
            TaskStatus.SUCCESS,
            TaskStatus.PARTIAL_SUCCESS,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.WAITING_HUMAN: {
            TaskStatus.RUNNING,
            TaskStatus.DRAINING,
            TaskStatus.PARTIAL_SUCCESS,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.DRAINING: {
            TaskStatus.SUCCESS,
            TaskStatus.PARTIAL_SUCCESS,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.SUCCESS: set(),
        TaskStatus.PARTIAL_SUCCESS: set(),
        TaskStatus.FAILED: set(),
        TaskStatus.CANCELLED: set(),
    }

    def validate_section(
        self,
        before: SectionStatus,
        after: SectionStatus,
    ) -> None:
        if before == after:
            return
        if after not in self._SECTION_TRANSITIONS[before]:
            raise StateTransitionError(
                f"invalid section transition: {before.value} -> {after.value}"
            )

    def validate_task(self, before: TaskStatus, after: TaskStatus) -> None:
        if before == after:
            return
        if after not in self._TASK_TRANSITIONS[before]:
            raise StateTransitionError(
                f"invalid task transition: {before.value} -> {after.value}"
            )
