# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：TableAnalysisBuilder。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Build the table-analysis compatibility view from canonical ProjectInput."""

from __future__ import annotations

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.table_agent_schema import TableAnalysisSchema
from .summary_service import ProjectInputSummaryService


# 阅读注释（类）：封装 table analysis builder，集中封装相关状态、依赖和行为。
class TableAnalysisBuilder:
    """封装 table analysis builder，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 TableAnalysisBuilder，保存运行所需的依赖、配置或状态。
    def __init__(self, summaries: ProjectInputSummaryService | None = None) -> None:
        """初始化 TableAnalysisBuilder，保存运行所需的依赖、配置或状态。

        参数:
            summaries: summaries，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ProjectInputSummaryService。
        """
        self.summaries = summaries or ProjectInputSummaryService()

    # 阅读注释（函数）：构建 TableAnalysisBuilder。
    def build(self, project_input: ProjectInputSchema) -> TableAnalysisSchema:
        """构建 TableAnalysisBuilder。

        参数:
            project_input: 规范化后的项目输入。

        返回:
            TableAnalysisSchema

        阅读提示:
            主要直接调用：join, TableAnalysisSchema, self.summaries.hardware_summary, g.model_dump, r.model_dump, project_input.generation_requirements.model_dump, b.model_dump。
        """
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
