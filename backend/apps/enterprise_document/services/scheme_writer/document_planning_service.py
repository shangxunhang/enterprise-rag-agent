# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：DocumentPlanningService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import List

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import DocumentPlanSchema, SectionPlanSchema


# 阅读注释（类）：封装 文档 planning 服务，封装一组可复用的业务能力。
class DocumentPlanningService:
    """封装 文档 planning 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：构建 文档 计划。
    @staticmethod
    def build_document_plan(
        *,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        required_sections: List[str],
        created_at: str,
    ) -> DocumentPlanSchema:
        """构建 文档 计划。

        参数:
            run_id: 本次运行唯一标识。
            document_id: 文档 标识，具体约束请结合类型标注和调用方确认。
            project_input: 规范化后的项目输入。
            required_sections: required sections，具体约束请结合类型标注和调用方确认。
            created_at: created at，具体约束请结合类型标注和调用方确认。

        返回:
            DocumentPlanSchema

        阅读提示:
            主要直接调用：set, DocumentPlanSchema, SectionPlanSchema, enumerate。
        """
        citation_required = set(
            project_input.generation_requirements.citation_required_sections
        )
        return DocumentPlanSchema(
            plan_id=f"plan_{run_id}",
            document_id=document_id,
            document_title=(
                project_input.output_schema.document_title or "项目建设方案"
            ),
            sections=[
                SectionPlanSchema(
                    section_id=f"section_{run_id}_{order:03d}",
                    section_title=title,
                    section_order=order,
                    citation_required=title in citation_required,
                )
                for order, title in enumerate(required_sections, start=1)
            ],
            planning_source="project_input",
            created_at=created_at,
            metadata={
                "project_input_schema_version": project_input.schema_version,
            },
        )
