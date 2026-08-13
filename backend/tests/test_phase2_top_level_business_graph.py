from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import importlib
import inspect
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from langgraph.graph import END

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.scheme_writer import HardGateResultSchema
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.services.scheme_writer.use_case import (
    SchemeGenerationUseCase,
)
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from core.config import get_settings


PHASE2_NODE_SEQUENCE = [
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
    "persist_deliverable",
    "finalize_workflow_result",
]
PHASE2_NODE_NAMES = {
    *PHASE2_NODE_SEQUENCE,
    "persist_rejected",
    "finalize_workflow_failure",
}
TECHNICAL_FAILURE_SEQUENCE = [
    "normalize_project_input",
    "plan_document",
    "retrieve_document_evidence",
    "finalize_workflow_failure",
]


def _run_production_fake(
    output_root: Path,
    *,
    run_id: str,
    patchers=(),
    gate_enforcement_mode: str = "strict",
) -> dict:
    settings = replace(
        get_settings(),
        data_root=output_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
        gate_enforcement_mode=gate_enforcement_mode,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        # These guards prove that production is executing the app graph stages,
        # not falling back to either of the old coarse orchestration entrypoints.
        stack.enter_context(
            patch.object(
                SchemeWriterAgent,
                "run",
                side_effect=AssertionError("coarse SchemeWriterAgent.run invoked"),
            )
        )
        stack.enter_context(
            patch.object(
                SchemeGenerationUseCase,
                "run",
                side_effect=AssertionError("legacy SchemeGenerationUseCase.run invoked"),
            )
        )
        for patcher in patchers:
            stack.enter_context(patcher)
        return MainlineApplicationService().run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成企业级 RAG-Agent 系统建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )


def _phase2_metadata(summary: dict) -> dict:
    return summary["supervisor_result"]["result"]["workflow_execution"]["metadata"]


def _phase2_started_nodes(summary: dict) -> list[str]:
    trace_path = Path(summary["paths"]["trace"])
    events = _trace_events(trace_path)
    return [
        str(event["component_name"])
        for event in events
        if event.get("event_type") == "agent_started"
        and "phase2_business_node" in (event.get("tags") or [])
    ]


def _trace_events(trace_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


@pytest.fixture(scope="module")
def production_success_summary(tmp_path_factory) -> dict:
    return _run_production_fake(
        tmp_path_factory.mktemp("phase2-production-success"),
        run_id="phase2_production_success",
    )


def _graph_module():
    return importlib.import_module(
        "apps.enterprise_document.workflows.top_level_business_graph"
    )


def _state_module():
    return importlib.import_module(
        "apps.enterprise_document.schemas.scheme_generation_graph"
    )


def _stage_module():
    return importlib.import_module(
        "apps.enterprise_document.services.scheme_writer.stage_service"
    )


def test_production_graph_declares_all_top_level_business_nodes() -> None:
    graph_module = _graph_module()
    builder_source = inspect.getsource(
        graph_module.EnterpriseDocumentWorkflowEngine.build_graph
    )

    assert set(graph_module.PHASE2_BUSINESS_NODE_NAMES) == PHASE2_NODE_NAMES
    for node_name in PHASE2_NODE_NAMES:
        assert f'builder.add_node(\n            "{node_name}"' in builder_source


def test_success_route_is_explicit_and_ordered() -> None:
    graph_module = _graph_module()

    assert graph_module.phase2_success_node_sequence() == PHASE2_NODE_SEQUENCE


def test_evidence_insufficient_routes_to_rejected_artifact() -> None:
    graph_module = _graph_module()

    assert graph_module.phase2_gate_routes() == {
        "deliverable": "persist_deliverable",
        "rejected": "persist_rejected",
        "technical_failure": "finalize_workflow_failure",
    }


def test_technical_failure_route_bypasses_business_artifact_nodes() -> None:
    graph_module = _graph_module()

    assert graph_module.phase2_failure_route() == [
        "finalize_workflow_failure",
        END,
    ]


def test_section_execution_defaults_to_one_and_uses_safe_langgraph_fanout() -> None:
    stage_module = _stage_module()
    source = inspect.getsource(stage_module.SchemeGenerationStageService.execute_sections)

    assert "self.section_max_concurrency > 1" in source
    assert "item.can_run_in_parallel" in source
    assert "not item.requires_global_context" in source
    assert "not item.depends_on" in source
    assert "set(item.depends_on) <= completed_ids" in source
    assert "section_parallel_execution_graph.execute_batch" in source
    assert "previous_sections=completed_sections()" in source
    assert "SectionGraphExecutionRequestSchema" in source
    assert "SectionExecutionCoordinator" in inspect.getsource(
        stage_module.SchemeGenerationStageService
    )
    assert "asyncio" not in source
    assert "ThreadPoolExecutor" not in source


def test_document_gate_has_one_independent_stage_owner() -> None:
    stage_module = _stage_module()
    gate_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.evaluate_document_gate
    )
    assemble_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.assemble_document
    )
    deliver_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.persist_deliverable
    )
    rejected_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.persist_rejected
    )

    assert gate_source.count("evaluate_scheme_draft(") == 1
    assert "evaluate_scheme_draft(" not in assemble_source
    assert "evaluate_scheme_draft(" not in deliver_source
    assert "evaluate_scheme_draft(" not in rejected_source


def test_artifact_lifecycle_routes_are_mutually_exclusive() -> None:
    stage_module = _stage_module()

    deliver_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.persist_deliverable
    )
    rejected_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.persist_rejected
    )
    failure_source = inspect.getsource(
        stage_module.SchemeGenerationStageService.finalize_workflow_failure
    )
    assert "rejected_artifact=None" in deliver_source
    assert "deliverable_final_result=None" in rejected_source
    assert "deliverable_final_result=None" in failure_source
    assert "rejected_artifact=None" in failure_source


def test_capture_is_owned_only_by_persist_stages() -> None:
    stage_module = _stage_module()
    stage_source = inspect.getsource(stage_module.SchemeGenerationStageService)

    assert stage_source.count("self.capture_service.capture(") == 1
    assert "capture_completed" in stage_source


def test_phase2_state_contract_is_versioned_and_json_safe() -> None:
    state_module = _state_module()
    payload = state_module.SchemeGenerationGraphPayloadSchema(
        task_id="task-phase2",
        run_id="run-phase2",
    )

    serialized = state_module.SchemeGenerationGraphStateCodec().dump_payload(payload)
    assert serialized["schema_version"] == "scheme_generation_graph_payload_v1"
    assert state_module.SCHEME_GENERATION_GRAPH_STATE_VERSION == 1
    assert "services" not in serialized
    assert "citation_registry" not in serialized


def test_phase2_graph_uses_phase1_checkpoint_identity() -> None:
    graph_module = _graph_module()
    workflow = build_scheme_generation_workflow(
        created_at="2026-07-25T00:00:00+00:00"
    )

    assert graph_module.phase2_checkpoint_identity(
        workflow,
        run_id="phase2-run",
    )["configurable"] == {
        "thread_id": "phase2-run",
        "checkpoint_ns": "scheme_generation_v2:v2.0",
        "workflow_namespace": "scheme_generation_v2:v2.0",
    }


def test_production_composition_root_no_longer_executes_coarse_scheme_writer() -> None:
    supervisor_factory = Path(
        "backend/bootstrap/supervisor_factory.py"
    ).read_text(encoding="utf-8")
    graph_module = _graph_module()

    assert "EnterpriseDocumentWorkflowEngine(" in supervisor_factory
    assert "workflow_engine=LangGraphWorkflowEngine(" not in supervisor_factory
    assert "agents.register(\n            SchemeWriterAgent(" not in supervisor_factory
    assert hasattr(graph_module, "EnterpriseDocumentWorkflowEngine")


def test_production_composition_executes_app_graph_without_coarse_fallback(
    production_success_summary,
) -> None:
    metadata = _phase2_metadata(production_success_summary)

    assert metadata["phase2_top_level_graph"] is True
    assert (
        production_success_summary["supervisor_result"]["metadata"][
            "workflow_engine"
        ]
        == "enterprise_document_langgraph_engine"
    )
    assert metadata["workflow_definition_topology_ignored"] is True


def test_production_trace_and_checkpoints_expose_each_business_boundary(
    production_success_summary,
) -> None:
    metadata = _phase2_metadata(production_success_summary)
    started_nodes = _phase2_started_nodes(production_success_summary)

    assert started_nodes == PHASE2_NODE_SEQUENCE
    assert metadata["business_node_sequence"] == PHASE2_NODE_SEQUENCE
    assert metadata["checkpoint_history_count"] > len(PHASE2_NODE_SEQUENCE)
    assert metadata["checkpoint_business_nodes"] == PHASE2_NODE_SEQUENCE
    assert metadata["checkpoint_thread_id"] == production_success_summary["run_id"]
    assert metadata["checkpoint_namespace"] == "scheme_generation_v2:v2.0"


def test_production_sections_preserve_document_plan_order(
    production_success_summary,
) -> None:
    output = production_success_summary["scheme_writer_output"]
    plan_titles = [
        section["section_title"]
        for section in output["document_plan"]["sections"]
    ]
    generated_titles = [
        section["section_title"]
        for section in output["generated_draft"]["sections"]
    ]

    assert generated_titles == plan_titles


def test_production_artifact_lifecycle_and_capture_are_exactly_once(
    production_success_summary,
) -> None:
    summary = production_success_summary
    run_id = summary["run_id"]
    captures = Path(summary["paths"]["raw_interactions"]).parents[1]
    raw_count = _jsonl_count(Path(summary["paths"]["raw_interactions"]))
    eval_count = _jsonl_count(Path(summary["paths"]["eval_samples"]))
    sft_count = _jsonl_count(Path(summary["paths"]["sft_candidates"]))
    rejected_count = _jsonl_count(
        captures / "rejected" / f"{run_id}_rejected.jsonl"
    )

    assert summary["generated_draft"]
    assert summary["deliverable_final_result"]
    assert summary["rejected_artifact"] is None
    assert raw_count == 1
    assert eval_count == 1
    assert sft_count + rejected_count == 1
    if summary["status"] == "success":
        assert (sft_count, rejected_count) == (1, 0)
    else:
        assert summary["status"] == "partial_success"
        assert (sft_count, rejected_count) == (0, 1)


def test_evidence_insufficient_reaches_gate_once_and_persists_rejected(
    tmp_path,
) -> None:
    gate_calls: list[dict] = []
    forced_gate = HardGateResultSchema(
        passed=False,
        failures=["evidence requirements are not satisfied"],
        checks={"evidence_sufficient": False},
    )

    def reject_gate(*args, **kwargs):
        gate_calls.append(dict(kwargs))
        return forced_gate

    gate_patcher = patch(
        "apps.enterprise_document.services.scheme_writer.stage_service."
        "evaluate_scheme_draft",
        side_effect=reject_gate,
    )
    assessment_patcher = patch.object(
        SchemeGenerationStageService,
        "_evidence_assessment",
        return_value={
            "status": "insufficient",
            "evidence_available": False,
            "details": {"forced_by_test": True},
        },
    )
    summary = _run_production_fake(
        tmp_path,
        run_id="phase2_evidence_insufficient",
        patchers=(gate_patcher, assessment_patcher),
    )

    assert _phase2_started_nodes(summary) == [
        *PHASE2_NODE_SEQUENCE[:14],
        "persist_rejected",
        "finalize_workflow_result",
    ]
    assert summary["status"] == "failed"
    assert summary["generated_draft"]
    assert summary["rejected_artifact"]
    assert summary["deliverable_final_result"] is None
    assert summary["scheme_writer_output"]["hard_gate"]["passed"] is False
    assert len(gate_calls) == 1
    gate_finished = next(
        event
        for event in _trace_events(Path(summary["paths"]["trace"]))
        if event.get("event_type") == "agent_finished"
        and event.get("component_name") == "evaluate_document_gate"
    )
    assert gate_finished["status"] == "failed"
    assert gate_finished["output_summary"]["error_code"] == (
        "DOCUMENT_HARD_GATE_FAILED"
    )
    assert gate_finished["latency_ms"] is not None


def test_shadow_gate_failure_keeps_real_failure_and_completes_run(
    tmp_path,
) -> None:
    forced_gate = HardGateResultSchema(
        passed=False,
        failures=["forced business quality failure"],
        checks={"forced_business_quality": False},
    )
    summary = _run_production_fake(
        tmp_path,
        run_id="phase2_shadow_gate_failure",
        gate_enforcement_mode="shadow",
        patchers=(
            patch(
                "apps.enterprise_document.services.scheme_writer.stage_service."
                "evaluate_scheme_draft",
                return_value=forced_gate,
            ),
        ),
    )

    assert summary["status"] == "success"
    assert summary["generated_draft"]
    assert summary["rejected_artifact"]
    assert summary["deliverable_final_result"] is None
    output = summary["scheme_writer_output"]
    assert output["hard_gate"]["passed"] is False
    assert output["hard_gate"]["metadata"]["enforcement_mode"] == "shadow"
    assert output["hard_gate"]["metadata"]["enforcement_action"] == "continue"
    assert output["hard_gate"]["metadata"]["would_block_in_strict"] is True
    assert output["extra"]["execution_status"] == "success"
    assert output["extra"]["business_quality_status"] == "failed"
    assert output["extra"]["release_status"] == "shadow_only"
    assert output["extra"]["final_artifact_generated"] is True


def test_technical_failure_routes_directly_to_failure_without_artifact(
    tmp_path,
) -> None:
    summary = _run_production_fake(
        tmp_path,
        run_id="phase2_technical_failure",
        gate_enforcement_mode="shadow",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "retrieve_document_evidence",
                side_effect=RuntimeError("forced retrieval infrastructure failure"),
            ),
        ),
    )

    assert _phase2_started_nodes(summary) == TECHNICAL_FAILURE_SEQUENCE
    assert summary["status"] in {"failed", "retryable_failed"}
    assert summary["generated_draft"] == {}
    assert summary["rejected_artifact"] is None
    assert summary["deliverable_final_result"] is None
    assert _phase2_metadata(summary)["terminal_reason"] == "technical_failure"
