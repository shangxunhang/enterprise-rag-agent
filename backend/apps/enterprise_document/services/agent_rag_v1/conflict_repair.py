"""ConflictRepairPlan 的严格串行执行状态机。"""

from __future__ import annotations

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
