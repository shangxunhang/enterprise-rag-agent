"""依据 ProjectInput 的确定性章节列表构造文档计划。"""


from typing import List

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import DocumentPlanSchema, SectionPlanSchema
from core.runtime.ids import IdGenerator, UuidIdGenerator


class DocumentPlanningService:
    """不调用模型，只把章节顺序和引用要求映射为可校验计划。"""

    @staticmethod
    def allocate_new_section_id(
        *,
        id_generator: IdGenerator | None = None,
    ) -> str:
        """Allocate identity for a true new/split/replacement Section.

        The identity is collision-safe and intentionally independent of section title,
        ordinal, or any reused ordering slot.  Local replans must retain their existing
        ``section_id`` and therefore do not call this allocator.
        """

        return (id_generator or UuidIdGenerator()).new_id("section")

    @staticmethod
    def build_document_plan(
        *,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        required_sections: List[str],
        created_at: str,
    ) -> DocumentPlanSchema:
        """按调用方声明顺序生成稳定 section_id，并投影逐章节引用要求。

        若开启引用但未单独列出必需章节，则默认所有章节都需要引用；该服务不会推断章节
        依赖或擅自增删章节。
        """
        citation_required = set(
            project_input.generation_requirements.citation_required_sections
            or (
                required_sections
                if project_input.generation_requirements.need_citation
                else []
            )
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
