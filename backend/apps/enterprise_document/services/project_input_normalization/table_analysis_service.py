"""Build the table-analysis compatibility view from canonical ProjectInput."""

from __future__ import annotations

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import TableAnalysisSchema
from .summary_service import ProjectInputSummaryService


class TableAnalysisBuilder:
    def __init__(self, summaries: ProjectInputSummaryService | None = None) -> None:
        self.summaries = summaries or ProjectInputSummaryService()

    def build(self, project_input: ProjectInputSchema) -> TableAnalysisSchema:
        missing = "；".join(project_input.missing_information) or "无明确缺失项"
        return TableAnalysisSchema(
            table_summary="已将调用方提供的 ProjectInput 校验并转换为下游兼容结构。",
            device_summary=self.summaries.hardware_summary(project_input),
            project_scale={
                "project_name": project_input.project_name,
                "project_type": project_input.project_type,
                "customer_type": project_input.customer_type,
                "business_goal": project_input.business_goal,
                "target_documents": project_input.target_documents,
                "total_staff": project_input.total_staff,
                "functional_department_count": project_input.functional_department_count,
                "business_department_count": project_input.business_department_count,
                "department_groups": [g.model_dump() for g in project_input.department_groups],
            },
            device_list=[r.model_dump() for r in project_input.hardware_resources],
            resource_config=[
                {
                    "resource_type": "generation_requirements",
                    "description": project_input.generation_requirements.model_dump(),
                },
                {
                    "resource_type": "manual_work_boundary",
                    "items": [b.model_dump() for b in project_input.manual_boundaries],
                },
            ],
            estimate_summary={
                "estimate_type": "caller_supplied_or_manual",
                "description": "系统只组织调用方明确提供的数据，不生成未提供的测算结果。",
            },
            risk_items=[
                {
                    "risk_type": "input_incompleteness",
                    "risk_level": "medium" if project_input.missing_information else "low",
                    "description": missing,
                }
            ],
            extra={
                "normalized_from": "ProjectInputSchema",
                "project_input_schema_version": project_input.schema_version,
            },
        )
