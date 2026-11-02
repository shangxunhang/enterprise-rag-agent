"""现有 Supervisor 初始化新版状态树时使用的无编排适配器。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    MultiAgentSharedStateSchema,
    SectionExecutionStateSchema,
    SectionStateSchema,
    SectionStatus,
    SectionStrategy,
    SourceSnapshotStateSchema,
    StateMetaSchema,
    TaskContextStateSchema,
)
from apps.enterprise_document.schemas.scheme_writer import DocumentPlanSchema


class SupervisorStateAdapter:
    """只构造初态；不复制 Supervisor 的生命周期、路由或终态职责。"""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(
        self,
        *,
        task_id: str,
        run_id: str,
        input_snapshot_version: str,
        task_context_ref: str,
        source_snapshot_ref: str,
        fact_snapshot_ref: str,
        template_schema_ref: str,
        source_inventory_ref: str,
        plan: DocumentPlanSchema,
    ) -> MultiAgentSharedStateSchema:
        now = self.clock()
        section_states: dict[str, SectionStateSchema] = {}
        for item in plan.sections:
            metadata = dict(item.metadata)
            strategy = SectionStrategy(
                metadata.get("strategy", SectionStrategy.RAG_GENERATE.value)
            )
            consistency_references = [
                str(value)
                for value in metadata.get("consistency_references", [])
            ]
            section_states[item.section_id] = SectionStateSchema(
                section_id=item.section_id,
                section_title=item.section_title,
                section_order=item.section_order,
                applicable=strategy != SectionStrategy.SKIP,
                strategy=strategy,
                execution_group=item.execution_group,
                can_run_in_parallel=item.can_run_in_parallel,
                requires_global_context=item.requires_global_context,
                execution=SectionExecutionStateSchema(
                    status=(
                        SectionStatus.SKIPPED
                        if strategy == SectionStrategy.SKIP
                        else SectionStatus.PLANNED
                    ),
                    active_plan_version=1,
                    input_snapshot_version=input_snapshot_version,
                    hard_dependencies=item.depends_on,
                    consistency_references=consistency_references,
                ),
            )
        return MultiAgentSharedStateSchema(
            state_meta=StateMetaSchema(
                task_id=task_id,
                run_id=run_id,
                input_snapshot_version=input_snapshot_version,
                created_at=now,
                updated_at=now,
            ),
            task_context=TaskContextStateSchema(
                task_context_ref=task_context_ref,
            ),
            source_snapshot=SourceSnapshotStateSchema(
                source_snapshot_ref=source_snapshot_ref,
                fact_snapshot_ref=fact_snapshot_ref,
                template_schema_ref=template_schema_ref,
                source_inventory_ref=source_inventory_ref,
            ),
            section_states=section_states,
        )
