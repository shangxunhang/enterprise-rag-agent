"""P1 strict binding tests for durable human-review decisions."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.official_checkpoint_saver import create_sqlite_checkpoint_resource
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from task.task_manager import JsonlTaskManager


_ORIGINAL_PREPARE = SchemeGenerationStageService.prepare_document_plan
_ENV = {
    "USE_REAL_RAG_TOOL": "false",
    "FAKE_RAG_SCENARIO": "default",
    "FAKE_LLM_SCENARIO": "default",
    "ENABLE_AGENT_SELF_RAG": "false",
    "ENABLE_SEMANTIC_GATE": "false",
    "SECTION_MAX_CONCURRENCY": "1",
}


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []
        self.workflows: list[object] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        self.engines.append(supervisor.workflow_engine)
        self.workflows.append(kwargs["workflow"])
        return supervisor


def _require_review(self, graph_state, payload):
    planned = _ORIGINAL_PREPARE(self, graph_state, payload)
    project_input = planned.project_input
    assert project_input is not None
    requirements = project_input.generation_requirements.model_copy(
        update={
            "extra": {
                **dict(project_input.generation_requirements.extra),
                "require_human_approval": True,
            }
        },
        deep=True,
    )
    return planned.model_copy(
        update={
            "project_input": project_input.model_copy(
                update={"generation_requirements": requirements},
                deep=True,
            )
        },
        deep=True,
    )


def _paused(root: Path, run_id: str):
    settings = replace(
        get_settings(),
        data_root=root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
    )
    factory = _InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, _ENV))
        stack.enter_context(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                _require_review,
            )
        )
        summary = MainlineApplicationService(
            supervisor_factory=factory
        ).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="Generate a snapshot-bound enterprise proposal",
            run_id=run_id,
            output_root=root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    request = summary["supervisor_result"]["human_review_request"]
    return summary, request["metadata"]["input_snapshot_version"], settings, factory


def _common(root: Path, summary: dict, settings) -> dict:
    return {
        "project_root": Path(__file__).resolve().parents[2],
        "task_id": summary["task_id"],
        "run_id": summary["run_id"],
        "output_root": root,
        "settings": settings,
        "allow_demo_defaults": True,
        "enable_agent_self_rag": False,
    }


def test_missing_snapshot_is_not_auto_filled_by_application_api(
    tmp_path: Path,
) -> None:
    summary, _snapshot, settings, _factory = _paused(tmp_path, "snapshot-missing")
    service = MainlineApplicationService()
    with patch.dict(os.environ, _ENV), pytest.raises(
        TypeError,
        match="input_snapshot_version",
    ):
        service.resume_run(decision="approved", **_common(tmp_path, summary, settings))


def test_empty_stale_and_other_run_snapshots_do_not_mutate_task_revision(
    tmp_path: Path,
) -> None:
    first, current, settings, _factory = _paused(tmp_path, "snapshot-primary")
    second, other, _settings2, _factory2 = _paused(tmp_path, "snapshot-other-run")
    service = MainlineApplicationService()
    common = _common(tmp_path, first, settings)
    manager = JsonlTaskManager(tmp_path / "tasks")
    before = manager.get_task(first["task_id"])
    before_event_count = len(before.events)

    with patch.dict(os.environ, _ENV):
        with pytest.raises(ValueError, match="required"):
            service.resume_run(
                decision="approved",
                input_snapshot_version="",
                **common,
            )
        for stale in ("sha256:stale", other):
            with pytest.raises(ValueError, match="snapshot is stale"):
                service.resume_run(
                    decision="approved",
                    input_snapshot_version=stale,
                    **common,
                )
        with pytest.raises(ValueError, match="snapshot is stale"):
            list(
                service.stream_resume_run(
                    decision="approved",
                    input_snapshot_version=other,
                    **common,
                )
            )

    after = manager.get_task(first["task_id"])
    assert after.revision == before.revision
    assert len(after.events) == before_event_count
    assert after.status == before.status
    assert current != other
    assert second["run_id"] != first["run_id"]


def test_top_level_requires_task_and_run_identity_with_exact_snapshot(
    tmp_path: Path,
) -> None:
    summary, snapshot, _settings, factory = _paused(tmp_path, "snapshot-identities")
    engine = factory.engines[0]
    workflow = factory.workflows[0]
    resource = create_sqlite_checkpoint_resource(engine.checkpointer.path)
    active_engine = EnterpriseDocumentWorkflowEngine(
        stage_service=engine.stage_service,
        checkpointer=resource.saver,
        side_effect_commit_store=engine.side_effect_commit_store,
    )
    state = GraphStateSchema.model_validate(
        summary["supervisor_result"]["result"]["shared_state"]
    )

    try:
        for decision in (
            {"decision": "approved", "run_id": summary["run_id"], "input_snapshot_version": snapshot},
            {"decision": "approved", "task_id": summary["task_id"], "input_snapshot_version": snapshot},
            {
                "decision": "approved",
                "task_id": "task-other",
                "run_id": summary["run_id"],
                "input_snapshot_version": snapshot,
            },
            {
                "decision": "approved",
                "task_id": summary["task_id"],
                "run_id": "run-other",
                "input_snapshot_version": snapshot,
            },
        ):
            with pytest.raises(ValueError, match="identity|required"):
                active_engine.resume(workflow, state, decision)
    finally:
        resource.close()


def test_current_exact_snapshot_resumes_sync_and_stream(tmp_path: Path) -> None:
    sync_summary, sync_snapshot, settings, _factory = _paused(
        tmp_path,
        "snapshot-current-sync",
    )
    stream_summary, stream_snapshot, _settings2, _factory2 = _paused(
        tmp_path,
        "snapshot-current-stream",
    )
    service = MainlineApplicationService()
    with patch.dict(os.environ, _ENV):
        sync_result = service.resume_run(
            decision="approved",
            input_snapshot_version=sync_snapshot,
            **_common(tmp_path, sync_summary, settings),
        )
        stream_result = list(
            service.stream_resume_run(
                decision="approved",
                input_snapshot_version=stream_snapshot,
                **_common(tmp_path, stream_summary, settings),
            )
        )

    assert sync_result["status"] in {"success", "partial_success"}
    assert stream_result[-1].event_type == "workflow_completed"
