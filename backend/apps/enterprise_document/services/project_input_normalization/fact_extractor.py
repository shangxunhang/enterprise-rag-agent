"""从规范化 ProjectInput 提取可追溯的类型化业务事实。"""

from __future__ import annotations

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from .summary_service import ProjectInputSummaryService
from .fact_contract import (
    project_input_fact_field,
    project_input_fact_normalization_version,
)


class StructuredFactExtractor:
    """把项目目标、组织、硬件、人工边界和缺失项投影为统一事实记录。"""
    def __init__(self, summaries: ProjectInputSummaryService | None = None) -> None:
        """注入纯文本摘要服务，避免在事实提取器中重复组织表达逻辑。"""
        self.summaries = summaries or ProjectInputSummaryService()

    def extract(
        self,
        state: SharedStateSchema,
        project_input: ProjectInputSchema,
        created_at: str,
    ) -> list[StructuredFactSchema]:
        """生成稳定 fact_id、来源和置信度，空内容不会形成伪事实。"""
        project_input_fact_normalization_version(project_input.schema_version)
        facts: list[StructuredFactSchema] = []

        def add_fact(fact_key: str, content: str, confidence: float = 1.0) -> None:
            """仅在文本非空时追加一条来自当前 ProjectInput 的事实。"""
            if not content.strip():
                return
            field = project_input_fact_field(fact_key)
            facts.append(
                StructuredFactSchema(
                    fact_id=field.record_id(state.run_id),
                    task_id=state.task_id,
                    run_id=state.run_id,
                    fact_type=field.fact_type,
                    content=content,
                    source_type="project_input",
                    source_ids=[project_input.task_id],
                    confidence=confidence,
                    created_at=created_at,
                )
            )

        add_fact("project_input.business_goal", project_input.business_goal)
        add_fact(
            "project_input.organization_scale",
            self.summaries.organization_summary(project_input),
        )
        add_fact("project_input.hardware_resources", self.summaries.hardware_summary(project_input))
        add_fact(
            "project_input.manual_boundaries",
            "；".join(
                f"{item.item}由{item.handled_by}处理"
                + (f"：{item.description}" if item.description else "")
                for item in project_input.manual_boundaries
            ),
        )
        if project_input.missing_information:
            add_fact(
                "project_input.missing_information",
                "当前待补充信息：" + "；".join(project_input.missing_information),
                0.95,
            )
        return facts
