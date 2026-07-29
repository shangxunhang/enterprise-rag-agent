"""Public application entry for the standalone Multi-Agent V1 runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from apps.enterprise_document.schemas.scheme_writer import (
    DocumentPlanSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    SupervisorStateAdapter,
)

from .contracts import (
    MultiAgentProjectInputSchema,
    StandaloneRunResultSchema,
)
from .runtime_factory import RuntimeComponents, RuntimeFactory


class MultiAgentApplicationService:
    """Initializes immutable input artifacts, then delegates to the runtime."""

    def __init__(self, factory: RuntimeFactory | None = None) -> None:
        self.factory = factory or RuntimeFactory()

    def run(
        self,
        project_input: MultiAgentProjectInputSchema,
    ) -> StandaloneRunResultSchema:
        resolved = self._resolve_identity(
            MultiAgentProjectInputSchema.model_validate(project_input)
        )
        session = self.factory.create_session(resolved)
        initial_state = self._initialize_state(session.components)
        return session.orchestrator.run(initial_state)

    @staticmethod
    def _resolve_identity(
        project_input: MultiAgentProjectInputSchema,
    ) -> MultiAgentProjectInputSchema:
        return project_input.model_copy(
            update={
                "task_id": project_input.task_id or f"task-{uuid4().hex[:12]}",
                "run_id": project_input.run_id or f"run-{uuid4().hex[:12]}",
            }
        )

    @staticmethod
    def _initialize_state(components: RuntimeComponents):
        project_input = components.project_input
        assert project_input.task_id is not None
        assert project_input.run_id is not None
        task_id = project_input.task_id
        run_id = project_input.run_id
        snapshot_version = components.fact_snapshot.snapshot_version
        initialization_attempt = f"initialization:{run_id}"
        template_ref = (
            f"artifact://{task_id}/template/"
            f"{components.template.template_version}"
        )
        inventory_ref = f"artifact://{task_id}/source-inventory/{run_id}"
        task_context_ref = f"artifact://{task_id}/task-context/{run_id}"
        source_snapshot_ref = f"artifact://{task_id}/source-snapshot/{run_id}"
        components.artifact_store.put_json(
            components.fact_snapshot.model_dump(mode="json"),
            artifact_type="fact_snapshot",
            schema_version=components.fact_snapshot.schema_version,
            created_by="fact_snapshot_provider",
            task_id=task_id,
            attempt_id=initialization_attempt,
            input_snapshot_version=snapshot_version,
            artifact_ref=components.fact_snapshot.artifact_ref,
        )
        components.artifact_store.put_json(
            components.template.model_dump(mode="json"),
            artifact_type="template_schema",
            schema_version=components.template.schema_version,
            created_by="template_provider",
            task_id=task_id,
            attempt_id=initialization_attempt,
            input_snapshot_version=snapshot_version,
            artifact_ref=template_ref,
        )
        components.artifact_store.put_json(
            {
                "source_ids": [
                    components.fact_snapshot.artifact_ref,
                    template_ref,
                ],
                "scenario": project_input.scenario,
            },
            artifact_type="source_inventory",
            schema_version="source_inventory_v1",
            created_by="multi_agent_application_service",
            task_id=task_id,
            attempt_id=initialization_attempt,
            input_snapshot_version=snapshot_version,
            artifact_ref=inventory_ref,
        )
        components.artifact_store.put_json(
            {
                "project_name": project_input.project_name,
                "scenario": project_input.scenario,
                "markdown_only": True,
            },
            artifact_type="task_context",
            schema_version="task_context_v1",
            created_by="multi_agent_application_service",
            task_id=task_id,
            attempt_id=initialization_attempt,
            input_snapshot_version=snapshot_version,
            artifact_ref=task_context_ref,
        )
        components.artifact_store.put_json(
            {
                "fact_snapshot_ref": components.fact_snapshot.artifact_ref,
                "template_schema_ref": template_ref,
                "source_inventory_ref": inventory_ref,
                "input_snapshot_version": snapshot_version,
            },
            artifact_type="source_snapshot",
            schema_version="source_snapshot_v1",
            created_by="multi_agent_application_service",
            task_id=task_id,
            attempt_id=initialization_attempt,
            input_snapshot_version=snapshot_version,
            artifact_ref=source_snapshot_ref,
        )
        plan = DocumentPlanSchema(
            plan_id=f"standalone-plan:{run_id}",
            document_id=f"standalone-document:{task_id}",
            document_title=components.template.document_title,
            sections=[
                SectionPlanSchema(
                    section_id=section.section_id,
                    section_title=section.section_title,
                    section_order=section.section_order,
                    depends_on=section.hard_dependencies,
                    execution_group=section.execution_group,
                    can_run_in_parallel=section.can_run_in_parallel,
                    requires_global_context=section.requires_global_context,
                    metadata={
                        "strategy": section.strategy.value,
                        "consistency_references": (
                            section.consistency_references
                        ),
                    },
                )
                for section in components.template.sections
            ],
            planning_source="agent_rag_v1_standalone",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state = SupervisorStateAdapter().initialize(
            task_id=task_id,
            run_id=run_id,
            input_snapshot_version=snapshot_version,
            task_context_ref=task_context_ref,
            source_snapshot_ref=source_snapshot_ref,
            fact_snapshot_ref=components.fact_snapshot.artifact_ref,
            template_schema_ref=template_ref,
            source_inventory_ref=inventory_ref,
            plan=plan,
        )
        # These are initial snapshot fields set before the first StateGateway
        # commit. Every runtime mutation after this point goes through Gateway.
        state.task_context.project_name = project_input.project_name
        state.task_context.template_version = (
            components.template.template_version
        )
        state.source_snapshot.source_versions = {
            "fact_snapshot": snapshot_version,
            "template": components.template.template_version,
        }
        components.checkpoint_store.save(state)
        return state
