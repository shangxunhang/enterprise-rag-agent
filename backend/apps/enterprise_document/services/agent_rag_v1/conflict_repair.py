"""ConflictRepairPlan 的严格串行执行状态机。"""

from __future__ import annotations

from collections.abc import Callable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ConflictRepairPlanSchema,
    RepairTaskSchema,
    RepairTaskStatus,
)


class ConflictRepairPlanService:
    def start_next(
        self,
        plan: ConflictRepairPlanSchema,
    ) -> tuple[ConflictRepairPlanSchema, RepairTaskSchema | None]:
        updated = plan.model_copy(deep=True)
        running = [
            task
            for task in updated.repair_tasks
            if task.status == RepairTaskStatus.RUNNING
        ]
        if len(running) > 1:
            raise ValueError("conflict repair plan has concurrent running tasks")
        if running:
            return updated, running[0]
        terminal = {
            task.repair_id: task.status
            for task in updated.repair_tasks
        }
        for task in sorted(
            updated.repair_tasks,
            key=lambda item: (item.priority, item.repair_id),
        ):
            if task.status != RepairTaskStatus.PENDING:
                continue
            prerequisite_statuses = [
                terminal.get(repair_id)
                for repair_id in task.prerequisite_repair_ids
            ]
            if all(
                status
                in {
                    RepairTaskStatus.RESOLVED,
                    RepairTaskStatus.OBSOLETE,
                }
                for status in prerequisite_statuses
            ):
                task.status = RepairTaskStatus.RUNNING
                return updated, task.model_copy(deep=True)
        return updated, None

    def execute_serially(
        self,
        plan: ConflictRepairPlanSchema,
        *,
        resolver: Callable[[RepairTaskSchema], RepairTaskStatus],
    ) -> tuple[ConflictRepairPlanSchema, list[dict[str, object]]]:
        """Execute one repair at a time and preserve an auditable history."""

        current = plan.model_copy(deep=True)
        history: list[dict[str, object]] = []
        while True:
            current, task = self.start_next(current)
            if task is None:
                break
            active_count = sum(
                item.status == RepairTaskStatus.RUNNING
                for item in current.repair_tasks
            )
            if active_count != 1:
                raise ValueError("serial repair requires exactly one running task")
            status = resolver(task.model_copy(deep=True))
            if status not in {
                RepairTaskStatus.RESOLVED,
                RepairTaskStatus.OBSOLETE,
                RepairTaskStatus.FAILED,
                RepairTaskStatus.BLOCKED,
            }:
                raise ValueError("repair resolver returned a non-terminal status")
            history.append(
                {
                    "sequence": len(history) + 1,
                    "repair_id": task.repair_id,
                    "target_section_ids": list(task.target_section_ids),
                    "action": task.action.value,
                    "status": status.value,
                    "concurrent_running_count": active_count,
                }
            )
            current = self.complete(
                current,
                repair_id=task.repair_id,
                status=status,
            )
            if status in {RepairTaskStatus.FAILED, RepairTaskStatus.BLOCKED}:
                # Dependent tasks remain pending because their prerequisite did
                # not resolve; stop rather than fabricating concurrent progress.
                break
        return current, history

    def complete(
        self,
        plan: ConflictRepairPlanSchema,
        *,
        repair_id: str,
        status: RepairTaskStatus,
    ) -> ConflictRepairPlanSchema:
        if status not in {
            RepairTaskStatus.RESOLVED,
            RepairTaskStatus.OBSOLETE,
            RepairTaskStatus.FAILED,
            RepairTaskStatus.BLOCKED,
        }:
            raise ValueError("repair completion requires a terminal status")
        updated = plan.model_copy(deep=True)
        matches = [
            task for task in updated.repair_tasks if task.repair_id == repair_id
        ]
        if len(matches) != 1:
            raise KeyError(repair_id)
        if matches[0].status != RepairTaskStatus.RUNNING:
            raise ValueError("only running repair task can complete")
        matches[0].status = status
        return updated
