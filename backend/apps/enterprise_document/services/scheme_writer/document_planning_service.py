"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from typing import List

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import DocumentPlanSchema, SectionPlanSchema
from .base import RuntimeBoundService


class DocumentPlanningService(RuntimeBoundService):
    @staticmethod
    def _build_document_plan(
        *,
        run_id: str,
        document_id: str,
        project_input: ProjectInputSchema,
        required_sections: List[str],
        created_at: str,
    ) -> DocumentPlanSchema:
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
