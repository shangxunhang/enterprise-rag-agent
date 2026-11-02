"""Convenience API shared by the CLI and standalone vertical tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .application_service import MultiAgentApplicationService
from .contracts import (
    MultiAgentProjectInputSchema,
    StandaloneRunResultSchema,
    StandaloneScenario,
)
from .runtime_factory import RuntimeFactory


def run_scenario(
    scenario: StandaloneScenario,
    *,
    output_dir: str | Path,
    task_id: str | None = None,
    run_id: str | None = None,
) -> StandaloneRunResultSchema:
    return MultiAgentApplicationService(RuntimeFactory()).run(
        MultiAgentProjectInputSchema(
            scenario=scenario,
            output_dir=str(output_dir),
            task_id=task_id,
            run_id=run_id,
        )
    )


def render_result(result: StandaloneRunResultSchema) -> dict[str, Any]:
    return {
        "Task Status": result.task_status.value,
        "Document Version": result.document_version,
        "Section Results": [
            {
                "section_id": section.section_id,
                "status": section.status.value,
                "deliverable_paragraph_ids": (
                    section.deliverable_paragraph_ids
                ),
            }
            for section in result.section_results
        ],
        "Document Artifact Path": result.document_artifact_path,
        "Generation Report Path": result.generation_report_path,
        "Checkpoint Path": result.checkpoint_path,
        "Event Log Path": result.event_log_path,
    }
