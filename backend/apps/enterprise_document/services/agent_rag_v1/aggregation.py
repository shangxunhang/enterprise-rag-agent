"""SectionGroup 的确定性状态聚合。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
    SectionGroupResultSchema,
    SectionStatus,
    SectionStrategy,
)


class SectionGroupAggregator:
    _DELIVERABLE = {
        SectionStatus.ACCEPTED,
        SectionStatus.PARTIALLY_ACCEPTED,
    }

    def aggregate(
        self,
        state: MultiAgentSharedStateSchema,
        *,
        group_id: str,
        section_ids: list[str],
        waiting_human_downgraded: bool = False,
    ) -> SectionGroupResultSchema:
        unknown = set(section_ids) - set(state.section_states)
        if unknown:
            raise ValueError(
                "section group contains unknown sections: "
                + ", ".join(sorted(unknown))
            )
        applicable = [
            section_id
            for section_id in section_ids
            if state.section_states[section_id].applicable
            and state.section_states[section_id].strategy != SectionStrategy.SKIP
            and state.section_states[section_id].execution.status
            != SectionStatus.SKIPPED
        ]
        if not applicable:
            return SectionGroupResultSchema(
                group_id=group_id,
                status=SectionStatus.SKIPPED,
                reason_codes=["no_applicable_sections"],
            )

        statuses = {
            section_id: state.section_states[section_id].execution.status
            for section_id in applicable
        }
        waiting = [
            section_id
            for section_id, status in statuses.items()
            if status == SectionStatus.WAITING_HUMAN
        ]
        deliverable = [
            section_id
            for section_id, status in statuses.items()
            if status in self._DELIVERABLE
        ]
        unavailable = [
            section_id for section_id in applicable if section_id not in deliverable
        ]

        if waiting and not waiting_human_downgraded:
            status = SectionStatus.BLOCKED
            reasons = ["waiting_human_not_downgraded"]
        elif all(
            child_status == SectionStatus.ACCEPTED
            for child_status in statuses.values()
        ):
            status = SectionStatus.ACCEPTED
            reasons = []
        elif deliverable:
            status = SectionStatus.PARTIALLY_ACCEPTED
            reasons = ["some_sections_unavailable"]
        else:
            status = SectionStatus.FAILED
            reasons = ["no_deliverable_sections"]

        return SectionGroupResultSchema(
            group_id=group_id,
            status=status,
            applicable_section_ids=applicable,
            deliverable_section_ids=deliverable,
            unavailable_section_ids=unavailable,
            reason_codes=reasons,
        )
