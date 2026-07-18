"""Extract typed facts from canonical ProjectInput."""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from .summary_service import ProjectInputSummaryService


class StructuredFactExtractor:
    def __init__(self, summaries: ProjectInputSummaryService | None = None) -> None:
        self.summaries = summaries or ProjectInputSummaryService()

    def extract(
        self,
        state: SharedStateSchema,
        project_input: ProjectInputSchema,
        created_at: str,
    ) -> list[StructuredFactSchema]:
        facts: list[StructuredFactSchema] = []

        def add_fact(suffix: str, fact_type: str, content: str, confidence: float = 1.0) -> None:
            if not content.strip():
                return
            facts.append(
                StructuredFactSchema(
                    fact_id=f"fact_{state.run_id}_{suffix}",
                    task_id=state.task_id,
                    run_id=state.run_id,
                    fact_type=fact_type,
                    content=content,
                    source_type="project_input",
                    source_ids=[project_input.task_id],
                    confidence=confidence,
                    created_at=created_at,
                )
            )

        add_fact("business_goal", "business_goal", project_input.business_goal)
        add_fact(
            "organization_scale",
            "project_scale",
            self.summaries.organization_summary(project_input),
        )
        add_fact("hardware", "hardware_resource", self.summaries.hardware_summary(project_input))
        add_fact(
            "manual_boundary",
            "manual_boundary",
            "；".join(
                f"{item.item}由{item.handled_by}处理"
                + (f"：{item.description}" if item.description else "")
                for item in project_input.manual_boundaries
            ),
        )
        if project_input.missing_information:
            add_fact(
                "missing_information",
                "missing_information",
                "当前待补充信息：" + "；".join(project_input.missing_information),
                0.95,
            )
        return facts
