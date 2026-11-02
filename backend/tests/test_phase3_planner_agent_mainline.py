"""Round-3 acceptance for PlannerAgent on the production mainline."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from agent.runtime.official_checkpoint_saver import (
    create_sqlite_checkpoint_resource,
)
from apps.enterprise_document.agents.agent_rag_v1 import PlannerAgent
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.agent_rag_v1 import (
    PlannerInvocationInputSchema,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
    phase2_checkpoint_identity,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings


class _InspectingFactory(SupervisorFactory):
    def __init__(self, *, planner_reasoner=None) -> None:
        super().__init__()
        self.planner_reasoner = planner_reasoner
        self.engine: EnterpriseDocumentWorkflowEngine | None = None
        self.workflow = None

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        assert engine.stage_service.planner_adapter is not None
        if self.planner_reasoner is not None:
            engine.stage_service.planner_adapter.planner_agent.reasoner = (
                self.planner_reasoner
            )
        self.engine = engine
        self.workflow = kwargs["workflow"]
        return supervisor


class _InvalidCyclicPlannerReasoner:
    def invoke(self, *, context, invocation_input, tools):
        del tools
        invocation = PlannerInvocationInputSchema.model_validate(invocation_input)
        section_id = f"section_{context.run_id}_001"
        return {
            "plan_ref": f"state://invalid-plan/{context.run_id}",
            "plan_revision": invocation.plan_revision,
            "section_task_ids": [section_id],
            "affected_section_ids": [section_id],
            "document_plan": {
                "plan_id": f"plan_{context.run_id}",
                "document_id": invocation.document_id,
                "document_title": "invalid cyclic plan",
                "sections": [
                    {
                        "section_id": section_id,
                        "section_title": invocation.required_sections[0],
                        "section_order": 1,
                        "citation_required": False,
                        "depends_on": [section_id],
                        "can_run_in_parallel": False,
                        "requires_global_context": True,
                    }
                ],
                "planning_source": "planner_agent",
                "created_at": invocation.created_at,
            },
        }


def _run_mainline(
    output_root: Path,
    *,
    run_id: str,
    factory: SupervisorFactory,
) -> dict:
    from bca014_i10_capacity_fixtures import explicit_fake_startup_policy
    profile,default_path,original=explicit_fake_startup_policy(output_root,run_id)
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
        "GROUNDED_GENERATION_POLICY_FILE": str(profile),
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(
            patch.object(
                SchemeWriterAgent,
                "run",
                side_effect=AssertionError("legacy SchemeWriterAgent.run invoked"),
            )
        )
        stack.enter_context(
            patch.object(
                SchemeGenerationUseCase,
                "run",
                side_effect=AssertionError("legacy SchemeGenerationUseCase.run invoked"),
            )
        )
        summary = MainlineApplicationService(
            supervisor_factory=factory
        ).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成企业级 RAG-Agent 系统建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
        assert default_path.read_bytes()==original
        return summary


def _trace_events(summary: dict) -> list[dict]:
    path = Path(summary["paths"]["trace"])
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _checkpoint_payload(
    output_root: Path,
    *,
    factory: _InspectingFactory,
    run_id: str,
) -> dict:
    assert factory.workflow is not None
    resource = create_sqlite_checkpoint_resource(
        output_root / "runs" / "langgraph_checkpoints.sqlite3"
    )
    try:
        history = list(
            resource.saver.list(
                phase2_checkpoint_identity(factory.workflow, run_id=run_id)
            )
        )
        for checkpoint_tuple in history:
            checkpoint = checkpoint_tuple.checkpoint
            values = checkpoint.get("channel_values", {})
            payload = values.get("scheme_generation_payload")
            if (
                isinstance(payload, dict)
                and payload.get("plan_state", {}).get("status") == "ready"
            ):
                return payload
    finally:
        resource.close()
    raise AssertionError("checkpoint history does not contain a ready plan")


def test_planner_agent_projected_mode_never_returns_state_patches(tmp_path) -> None:
    factory = _InspectingFactory()
    summary = _run_mainline(
        tmp_path,
        run_id="round3_planner_projected_no_patches",
        factory=factory,
    )
    assert summary["status"] in {"success", "partial_success"}
    assert factory.engine is not None
    adapter = factory.engine.stage_service.planner_adapter
    assert adapter is not None
    assert isinstance(adapter.planner_agent, PlannerAgent)

    planner_finished = next(
        event
        for event in _trace_events(summary)
        if event.get("event_type") == "agent_finished"
        and event.get("component_name") == "PlannerAgent"
    )
    assert planner_finished["status"] == "success"
    assert planner_finished["output_summary"]["proposed_state_patch_count"] == 0


def test_mainline_planner_is_nested_under_plan_document_and_checkpointed(
    tmp_path,
) -> None:
    factory = _InspectingFactory()
    run_id = "round3_planner_trace_checkpoint"
    summary = _run_mainline(tmp_path, run_id=run_id, factory=factory)
    from bca014_i10_capacity_fixtures import assert_native_publication
    assert_native_publication(summary,factory.engine)
    assert summary["deliverable_final_result"] is not None

    events = _trace_events(summary)
    plan_started = next(
        event
        for event in events
        if event.get("event_type") == "agent_started"
        and event.get("component_name") == "plan_document"
    )
    planner_started = next(
        event
        for event in events
        if event.get("event_type") == "agent_started"
        and event.get("component_name") == "PlannerAgent"
    )
    planner_finished = next(
        event
        for event in events
        if event.get("event_type") == "agent_finished"
        and event.get("component_name") == "PlannerAgent"
    )
    assert planner_started["parent_span_id"] == plan_started["span_id"]
    assert planner_finished["output_summary"]["plan_revision"] == 1
    assert planner_finished["output_summary"]["section_count"] == 8

    payload = _checkpoint_payload(tmp_path, factory=factory, run_id=run_id)
    plan = payload["document_plan"]
    plan_state = payload["plan_state"]
    assert plan["planning_source"] == "planner_agent"
    assert plan_state["status"] == "ready"
    assert plan_state["plan_revision"] == 1
    assert plan_state["input_snapshot_version"].startswith("sha256:")
    assert plan_state["metadata"]["planner_agent_attached"] is True
    assert plan_state["metadata"]["agent_role"] == "planner"
    assert plan_state["metadata"]["agent_status"] == "completed"
    assert plan_state["metadata"]["proposed_state_patch_count"] == 0
    assert len(plan_state["section_order"]) == 8


def test_invalid_planner_plan_stops_before_section_execution(tmp_path) -> None:
    factory = _InspectingFactory(
        planner_reasoner=_InvalidCyclicPlannerReasoner()
    )
    summary = _run_mainline(
        tmp_path,
        run_id="round3_invalid_cyclic_plan",
        factory=factory,
    )
    assert summary["status"] in {"failed", "retryable_failed"}
    assert summary["deliverable_final_result"] is None
    assert summary["rejected_artifact"] is None

    events = _trace_events(summary)
    started_business_nodes = [
        event["component_name"]
        for event in events
        if event.get("event_type") == "agent_started"
        and "phase2_business_node" in (event.get("tags") or [])
    ]
    assert started_business_nodes == [
        "normalize_project_input",
        "plan_document",
        "finalize_workflow_failure",
    ]
    planner_finished = next(
        event
        for event in events
        if event.get("event_type") == "agent_finished"
        and event.get("component_name") == "PlannerAgent"
    )
    assert planner_finished["status"] == "failed"
    assert planner_finished["output_summary"]["error_code"] == (
        "AGENT_OUTPUT_INVALID"
    )
    assert not any(
        event.get("component_name") == "execute_sections"
        for event in events
    )


def test_round3_production_sources_do_not_import_standalone_control_plane() -> None:
    project_root = Path(__file__).resolve().parents[2]
    paths = [
        project_root
        / "backend/apps/enterprise_document/services/scheme_writer/agent_integration/planner_adapter.py",
        project_root
        / "backend/apps/enterprise_document/services/scheme_writer/stage_service.py",
        project_root / "backend/bootstrap/supervisor_factory.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "MultiAgentSharedStateSchema",
        "WorkflowOrchestrator",
        "StateGateway",
        "JsonCheckpointStore",
        "TaskStatusCalculator",
    ):
        assert forbidden not in source
