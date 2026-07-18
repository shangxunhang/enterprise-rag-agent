"""Map canonical ProjectInput to TaskSchema."""

from __future__ import annotations

from typing import Any, Dict, Optional

from application.project_input_factory import ProjectInputFactory
from schemas.task import TaskSchema


class TaskFactory:
    def __init__(self, project_inputs: ProjectInputFactory | None = None) -> None:
        self.project_inputs = project_inputs or ProjectInputFactory()

    def build(
        self,
        task_id: str,
        run_id: str,
        user_input: str,
        created_at: str,
        project_input: Optional[Dict[str, Any]] = None,
        *,
        allow_demo_defaults: bool = True,
    ) -> TaskSchema:
        normalized = self.project_inputs.build(
            task_id=task_id,
            user_input=user_input,
            raw_project_input=project_input,
            allow_demo_defaults=allow_demo_defaults,
        )
        file_ids = list(
            dict.fromkeys(
                file_id
                for material in normalized.source_materials
                for file_id in material.file_ids
                if file_id
            )
        )
        doc_ids = list(
            dict.fromkeys(
                doc_id
                for material in normalized.source_materials
                for doc_id in material.doc_ids
                if doc_id
            )
        )
        kb_ids = list(
            dict.fromkeys(
                str(kb_id)
                for material in normalized.source_materials
                for kb_id in (
                    material.metadata.get("kb_ids")
                    or (
                        [material.metadata.get("kb_id")]
                        if material.metadata.get("kb_id")
                        else []
                    )
                )
                if str(kb_id).strip()
            )
        )
        return TaskSchema(
            task_id=task_id,
            run_id=run_id,
            tenant_id=normalized.tenant_id,
            task_type=normalized.task_type,
            task_name=(
                str(normalized.metadata.get("task_name") or "").strip()
                or f"{normalized.task_type}:{normalized.project_name or normalized.task_id}"
            ),
            project_name=normalized.project_name,
            user_input=normalized.user_query,
            project_input=normalized.model_dump(),
            source_materials=[item.model_dump() for item in normalized.source_materials],
            generation_requirements=normalized.generation_requirements.model_dump(),
            output_schema=normalized.output_schema.model_dump(),
            metadata=normalized.metadata,
            file_ids=file_ids,
            doc_ids=doc_ids,
            kb_ids=kb_ids,
            created_at=created_at,
        )
