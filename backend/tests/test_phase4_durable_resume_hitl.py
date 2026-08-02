"""Durable SQLite checkpoint, interrupt/resume and task reconciliation tests."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.langgraph_state import WorkflowNamespaceCheckpointSaver
from agent.runtime.official_checkpoint_saver import (
    create_sqlite_checkpoint_resource,
)
from agent.runtime.sqlite_checkpoint_saver import SQLiteCheckpointSaver
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from schemas.agent import AgentResultSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


ROUND7_REVIEW_PREFIX = [
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "initialize_generation",
    "execute_sections",
    "document_verify",
    "repair_document_conflicts",
    "incremental_document_reverify",
    "assemble_document",
    "validate_document_assembly",
    "final_artifact_review",
    "decide_document_version",
    "evaluate_document_gate",
    "build_generation_report",
]


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []
        self.workflows: list[object] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.engines.append(engine)
        self.workflows.append(kwargs["workflow"])
        return supervisor


def _require_human_review(self, graph_state, payload):
    result = _ORIGINAL_PREPARE(self, graph_state, payload)
    project_input = result.project_input
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
    return result.model_copy(
        update={
            "project_input": project_input.model_copy(
                update={"generation_requirements": requirements},
                deep=True,
            )
        },
        deep=True,
    )


_ORIGINAL_PREPARE = SchemeGenerationStageService.prepare_document_plan


def _run_paused(output_root: Path, *, run_id: str):
    settings = replace(
        get_settings(),
        data_root=output_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    factory = _InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                _require_human_review,
            )
        )
        summary = MainlineApplicationService(
            supervisor_factory=factory
        ).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成需要人工审核的企业级 RAG-Agent 建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    return summary, factory


def test_sqlite_saver_survives_new_instance(tmp_path) -> None:
    from langgraph.graph import END, START, StateGraph
    from typing import TypedDict

    class State(TypedDict):
        value: int

    database = tmp_path / "checkpoints.sqlite3"
    builder = StateGraph(State)
    builder.add_node("increment", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    config = {
        "configurable": {
            "thread_id": "durable-thread",
            "checkpoint_ns": "workflow:v1",
            "workflow_namespace": "workflow:v1",
        }
    }
    first_resource = create_sqlite_checkpoint_resource(database)
    try:
        first = builder.compile(
            checkpointer=WorkflowNamespaceCheckpointSaver(
                first_resource.saver
            )
        )
        assert first.invoke({"value": 1}, config) == {"value": 2}
    finally:
        first_resource.close()

    second_resource = create_sqlite_checkpoint_resource(database)
    try:
        restored = second_resource.saver.get_tuple(config)
        assert restored is not None
        assert restored.checkpoint["channel_values"]["value"] == 2
    finally:
        second_resource.close()


def test_human_review_interrupt_is_paused_and_task_is_not_terminal_failure(
    tmp_path,
) -> None:
    summary, _factory = _run_paused(
        tmp_path,
        run_id="phase4_human_review_pause",
    )
    execution = summary["supervisor_result"]["result"]["workflow_execution"]
    assert summary["status"] == "paused"
    assert summary["task_state"]["status"] == "paused"
    assert execution["metadata"]["workflow_complete"] is False
    assert execution["metadata"]["workflow_paused"] is True
    assert execution["metadata"]["interrupts"]
    assert execution["metadata"]["business_node_sequence"] == (
        ROUND7_REVIEW_PREFIX
    )
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
    )
    with patch.dict(
        os.environ,
        {
            "USE_REAL_RAG_TOOL": "false",
            "ENABLE_AGENT_SELF_RAG": "false",
            "ENABLE_SEMANTIC_GATE": "false",
        },
    ):
        inspection = MainlineApplicationService().inspect_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    assert inspection["interrupts"]
    assert "human_review_interrupt" in inspection["next_nodes"]


def test_new_engine_resumes_without_replaying_completed_nodes(tmp_path) -> None:
    summary, factory = _run_paused(
        tmp_path,
        run_id="phase4_cross_instance_resume",
    )
    old_engine = factory.engines[0]
    workflow = factory.workflows[0]
    model_facade = old_engine.stage_service.evidence_service.model_gateway
    rag_facade = old_engine.stage_service.evidence_service.rag_service
    model_calls_before = model_facade.runnable_invocation_count
    rag_calls_before = rag_facade.runnable_invocation_count

    checkpoint_resource = create_sqlite_checkpoint_resource(
        old_engine.checkpointer.path
    )
    new_engine = EnterpriseDocumentWorkflowEngine(
        stage_service=old_engine.stage_service,
        checkpointer=checkpoint_resource.saver,
        side_effect_commit_store=old_engine.side_effect_commit_store,
    )
    shared_state = GraphStateSchema.model_validate(
        summary["supervisor_result"]["result"]["shared_state"]
    )
    try:
        result = new_engine.resume(
            workflow,
            shared_state,
            {
                "decision": "approved",
                "feedback": "approved in durable-resume acceptance test",
            },
        )
    finally:
        checkpoint_resource.close()
    assert result.status in {
        ExecutionStatus.SUCCESS,
        ExecutionStatus.PARTIAL_SUCCESS,
    }
    assert result.metadata["workflow_complete"] is True
    assert result.metadata["resumed"] is True
    assert result.metadata["resume_count"] == 1
    sequence = result.metadata["business_node_sequence"]
    assert sequence[: len(ROUND7_REVIEW_PREFIX)] == ROUND7_REVIEW_PREFIX
    assert sequence[len(ROUND7_REVIEW_PREFIX) :] == [
        "apply_human_review",
        "persist_deliverable",
        "finalize_workflow_result",
    ]
    assert len(sequence) == len(set(sequence))
    assert model_facade.runnable_invocation_count == model_calls_before
    assert rag_facade.runnable_invocation_count == rag_calls_before
    assert shared_state.deliverable_final_result is not None
    assert shared_state.rejected_artifact is None


def test_legacy_sqlite_factory_is_explicitly_deprecated(tmp_path) -> None:
    with pytest.warns(DeprecationWarning, match="deprecated"):
        saver = SQLiteCheckpointSaver(tmp_path / "legacy-shim.sqlite3")
    saver._rag_agent_checkpoint_resource.close()


def test_task_manager_supports_paused_resume_terminal_transition(tmp_path) -> None:
    manager = JsonlTaskManager(tmp_path / "tasks")
    task = TaskSchema(
        task_id="task-paused",
        run_id="run-paused",
        task_type="scheme_generation",
        task_name="paused task",
        user_input="pause",
        project_input={"user_query": "pause"},
        created_at="2026-07-25T00:00:00+00:00",
    )
    manager.create_task(task)
    manager.mark_running(task)
    paused_result = AgentResultSchema(
        result_id="result-paused",
        task_id=task.task_id,
        run_id=task.run_id,
        agent_name="SupervisorAgent",
        agent_type="supervisor",
        status=ExecutionStatus.PAUSED,
        result_type="workflow_result",
        result={"interrupts": [{"reason": "review"}]},
        started_at=task.created_at,
        finished_at=task.created_at,
    )
    manager.mark_paused(task, paused_result)
    assert manager.get_task(task.task_id).status == ExecutionStatus.PAUSED
    manager.mark_running(task, current_step="workflow_resumed")
    final_result = paused_result.model_copy(
        update={
            "result_id": "result-complete",
            "status": ExecutionStatus.SUCCESS,
        },
        deep=True,
    )
    manager.mark_success(task, final_result)
    restored = JsonlTaskManager(tmp_path / "tasks").get_task(task.task_id)
    assert restored.status == ExecutionStatus.SUCCESS
    assert [event.current_status for event in restored.events] == [
        ExecutionStatus.PENDING,
        ExecutionStatus.RUNNING,
        ExecutionStatus.PAUSED,
        ExecutionStatus.RUNNING,
        ExecutionStatus.SUCCESS,
    ]


def test_application_service_inspects_and_approves_across_new_instance(
    tmp_path,
) -> None:
    summary, _factory = _run_paused(
        tmp_path,
        run_id="phase4_application_resume",
    )
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    service = MainlineApplicationService()
    with patch.dict(os.environ, environment):
        inspection = service.inspect_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
        resumed = service.resume_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=summary["task_id"],
            run_id=summary["run_id"],
            decision="approved",
            feedback="approved by application acceptance",
            reviewer_id="reviewer-1",
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    assert inspection["interrupts"]
    assert inspection["task_id"] == summary["task_id"]
    assert inspection["run_id"] == summary["run_id"]
    assert resumed["task_id"] == summary["task_id"]
    assert resumed["run_id"] == summary["run_id"]
    assert resumed["status"] in {"success", "partial_success"}
    assert resumed["deliverable_final_result"] is not None
    execution = resumed["supervisor_result"]["result"]["workflow_execution"]
    assert execution["metadata"]["resumed"] is True
    assert execution["metadata"]["resume_count"] == 1
    sequence = execution["metadata"]["business_node_sequence"]
    assert len(sequence) == len(set(sequence))


def test_application_service_rejects_and_cancels_through_durable_graph(
    tmp_path,
) -> None:
    settings = replace(
        get_settings(),
        data_root=tmp_path,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    service = MainlineApplicationService()
    for action in ("rejected", "cancelled"):
        run_id = f"phase4_application_{action}"
        summary, _factory = _run_paused(tmp_path, run_id=run_id)
        common = {
            "project_root": Path(__file__).resolve().parents[2],
            "task_id": summary["task_id"],
            "run_id": summary["run_id"],
            "output_root": tmp_path,
            "settings": settings,
            "allow_demo_defaults": True,
            "enable_agent_self_rag": False,
        }
        with patch.dict(os.environ, environment):
            if action == "rejected":
                result = service.resume_run(
                    decision="rejected",
                    feedback="quality owner rejected the draft",
                    reviewer_id="reviewer-2",
                    **common,
                )
            else:
                result = service.cancel_run(
                    reason="request withdrawn",
                    **common,
                )
        assert result["task_id"] == summary["task_id"]
        assert result["run_id"] == summary["run_id"]
        assert result["status"] == (
            "failed" if action == "rejected" else "cancelled"
        )
        assert result["rejected_artifact"] is not None
        assert result["deliverable_final_result"] is None
        assert (
            result["supervisor_result"]["human_review_status"]
            == action
        )
