"""只根据已验证 Paragraph 计算 Task 终态。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
    SectionStatus,
    SectionStrategy,
    TaskStatus,
)


class TaskStatusCalculator:
    def calculate(self, state: MultiAgentSharedStateSchema) -> TaskStatus:
        applicable = [
            section
            for section in state.section_states.values()
            if section.applicable
            and section.strategy != SectionStrategy.SKIP
            and section.execution.status != SectionStatus.SKIPPED
        ]
        deliverable_count = sum(
            len(
                set(section.verification.accepted_paragraph_ids)
                & set(section.writing.paragraph_refs)
            )
            for section in applicable
        )
        fully_delivered = bool(applicable) and all(
            section.execution.status == SectionStatus.ACCEPTED
            and bool(
                set(section.verification.accepted_paragraph_ids)
                & set(section.writing.paragraph_refs)
            )
            for section in applicable
        )
        if fully_delivered:
            return TaskStatus.SUCCESS
        if deliverable_count > 0:
            return TaskStatus.PARTIAL_SUCCESS
        return TaskStatus.FAILED
