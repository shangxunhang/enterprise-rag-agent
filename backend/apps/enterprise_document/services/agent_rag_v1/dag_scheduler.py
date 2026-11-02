"""Section DAG 校验与确定性并行批次选择。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
    ScheduledSectionBatchSchema,
    SectionStatus,
)


class SectionDagError(ValueError):
    pass


class SectionDagScheduler:
    _DEPENDENCY_COMPLETE = {
        SectionStatus.ACCEPTED,
        SectionStatus.PARTIALLY_ACCEPTED,
        SectionStatus.SKIPPED,
    }
    _DEPENDENCY_BLOCKING = {
        SectionStatus.BLOCKED,
        SectionStatus.FAILED,
        SectionStatus.WAITING_HUMAN,
    }

    def validate(self, state: MultiAgentSharedStateSchema) -> None:
        known = set(state.section_states)
        dependencies: dict[str, set[str]] = {}
        for section_id, section in state.section_states.items():
            deps = set(section.execution.hard_dependencies)
            if section_id in deps:
                raise SectionDagError(f"section depends on itself: {section_id}")
            unknown = deps - known
            if unknown:
                raise SectionDagError(
                    f"section {section_id} has unknown dependencies: "
                    + ", ".join(sorted(unknown))
                )
            dependencies[section_id] = deps

        completed: set[str] = set()
        remaining = dict(dependencies)
        while remaining:
            ready = sorted(
                section_id
                for section_id, deps in remaining.items()
                if deps <= completed
            )
            if not ready:
                raise SectionDagError("section dependency graph contains a cycle")
            completed.update(ready)
            for section_id in ready:
                remaining.pop(section_id)

    def next_batch(
        self,
        state: MultiAgentSharedStateSchema,
        *,
        max_parallel_sections: int,
    ) -> ScheduledSectionBatchSchema:
        if max_parallel_sections < 1:
            raise ValueError("max_parallel_sections must be at least one")
        self.validate(state)
        if state.runtime_control.cancelled or state.runtime_control.draining:
            return ScheduledSectionBatchSchema(
                section_ids=[],
                max_parallel_sections=max_parallel_sections,
            )

        ready: list[str] = []
        blocked: list[str] = []
        for section_id, section in sorted(
            state.section_states.items(),
            key=lambda item: (item[1].section_order, item[0]),
        ):
            if section.execution.status != SectionStatus.PLANNED:
                continue
            if (
                section.execution.execution_id
                or section.execution.active_attempt_id
            ):
                continue
            dependency_statuses = [
                state.section_states[dependency].execution.status
                for dependency in section.execution.hard_dependencies
            ]
            if any(
                status in self._DEPENDENCY_BLOCKING
                for status in dependency_statuses
            ):
                blocked.append(section_id)
                continue
            if all(
                status in self._DEPENDENCY_COMPLETE
                for status in dependency_statuses
            ):
                ready.append(section_id)

        if not ready:
            return ScheduledSectionBatchSchema(
                section_ids=[],
                blocked_section_ids=blocked,
                max_parallel_sections=max_parallel_sections,
            )

        first = state.section_states[ready[0]]
        if (
            max_parallel_sections == 1
            or not first.can_run_in_parallel
            or first.requires_global_context
        ):
            selected = ready[:1]
        else:
            selected = [
                section_id
                for section_id in ready
                if state.section_states[section_id].can_run_in_parallel
                and not state.section_states[section_id].requires_global_context
                and state.section_states[section_id].execution_group
                == first.execution_group
            ][:max_parallel_sections]
        return ScheduledSectionBatchSchema(
            section_ids=selected,
            blocked_section_ids=blocked,
            max_parallel_sections=max_parallel_sections,
        )
