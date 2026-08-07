"""Round-7 document closure acceptance on the production mainline."""

from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

from agent.runtime.official_checkpoint_saver import CompressedJsonPlusSerializer
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.agent_rag_v1 import (
    ConflictRepairPlanSchema,
    DocumentConflictSchema,
    DocumentVerificationResultSchema,
    RepairAction,
    RepairTaskSchema,
    RepairTaskStatus,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer import (
    SchemeSectionSchema,
    SectionEvalSchema,
    SectionLifecycleStatus,
    SectionStateSchema,
    VerificationProjectionStatus,
)
from apps.enterprise_document.services.agent_rag_v1.conflict_repair import (
    ConflictRepairPlanService,
)
from apps.enterprise_document.services.agent_rag_v1.document_verifier import (
    DocumentVerifierService,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from schemas.status import ExecutionStatus


class _InspectingFactory(SupervisorFactory):
    def __init__(self) -> None:
        super().__init__()
        self.engines: list[EnterpriseDocumentWorkflowEngine] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
        self.engines.append(engine)
        return supervisor


def _run_fake(output_root: Path, *, run_id: str, patchers=()):
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
        for patcher in patchers:
            stack.enter_context(patcher)
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成企业级 RAG-Agent 系统建设方案",
            run_id=run_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    return summary, factory


def _latest_top_payload(database: Path) -> dict:
    serializer = CompressedJsonPlusSerializer()
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT type, checkpoint FROM checkpoints "
            "WHERE checkpoint_ns = ? ORDER BY checkpoint_id DESC LIMIT 1",
            ("scheme_generation_v2:v2.0",),
        ).fetchone()
    assert row is not None
    checkpoint = serializer.loads_typed((str(row[0]), row[1]))
    return dict(
        (checkpoint.get("channel_values") or {}).get(
            "scheme_generation_payload"
        )
        or {}
    )


def _checkpoint_namespaces(database: Path) -> set[str]:
    with closing(sqlite3.connect(database)) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT checkpoint_ns FROM checkpoints"
            )
        }


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id="task-round7-verifier",
        task_type="scheme_generation",
        user_query="核对服务器数量并生成方案",
        generation_requirements={
            "required_sections": ["建设范围", "计算资源"],
            "need_citation": False,
            "citation_required_sections": [],
        },
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["建设范围", "计算资源"],
        },
        metadata={"server_count": 8},
    )


def _accepted_state(section_id: str, title: str, order: int) -> SectionStateSchema:
    state = SectionStateSchema(
        section_id=section_id,
        section_title=title,
        section_order=order,
        lifecycle_status=SectionLifecycleStatus.ACCEPTED,
    )
    return state.model_copy(
        update={
            "verification": state.verification.model_copy(
                update={
                    "status": VerificationProjectionStatus.COMPLETED,
                    "execution_mode": "section_verify",
                    "recommended_action": "accept",
                    "checks": {"section_valid": True},
                },
                deep=True,
            )
        },
        deep=True,
    )


def _section(section_id: str, title: str, order: int, content: str):
    return SchemeSectionSchema(
        section_id=section_id,
        section_title=title,
        section_order=order,
        content=content,
        model_output=content,
        status=ExecutionStatus.SUCCESS,
        eval_result=SectionEvalSchema(
            passed=True,
            checks={"content_nonempty": True},
        ),
        started_at="2026-08-01T00:00:00+00:00",
        finished_at="2026-08-01T00:00:01+00:00",
    )


def test_round7_success_closure_is_checkpointed_without_new_authority(
    tmp_path: Path,
) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="round7_success_document_closure",
    )
    assert summary["status"] == "success"
    closure = summary["scheme_writer_output"]["extra"]["document_closure"]
    assert closure["document_verification"]["passed"] is True
    assert closure["incremental_document_verification"]["passed"] is True
    assert closure["conflict_repair_history"] == []
    assert closure["assembly_validation"]["passed"] is True
    assert closure["final_artifact_review"]["passed"] is True
    assert closure["document_version"] == "v1.0"
    assert closure["version_decision"]["change_level"] == "initial"
    assert closure["generation_report"]["task_status"] == "success"

    hard_gate = summary["scheme_writer_output"]["hard_gate"]
    assert hard_gate["passed"] is True
    for name in (
        "document_verification_passed",
        "conflict_repairs_resolved",
        "assembly_validation_passed",
        "final_artifact_review_passed",
        "document_version_decided",
    ):
        assert hard_gate["checks"][name] is True

    database = Path(factory.engines[0].checkpointer.path)
    payload = _latest_top_payload(database)
    assert "multi_agent_shared_state" not in payload
    assert payload["document_version"] == "v1.0"
    assert payload["generation_report"]["document_version"] == "v1.0"
    validation = payload["document_validation"]
    assert validation["document_version"] == "v1.0"
    assert validation["verification_ref"].startswith(
        "checkpoint://document-verification/"
    )
    assert validation["assembly_validation_ref"].startswith(
        "checkpoint://assembly-validation/"
    )
    namespaces = _checkpoint_namespaces(database)
    assert "scheme_generation_v2:v2.0" in namespaces
    assert not any(
        value.startswith("document_verification")
        or value.startswith("generation_report")
        or value.startswith("document_closure")
        for value in namespaces
    )


def test_document_verifier_builds_cross_section_conflict_and_serial_plan() -> None:
    sections = [
        _section("scope", "建设范围", 1, "本项目配置服务器 6 台。"),
        _section("compute", "计算资源", 2, "本项目配置服务器 8 台。"),
    ]
    states = {
        "scope": _accepted_state("scope", "建设范围", 1),
        "compute": _accepted_state("compute", "计算资源", 2),
    }
    result = DocumentVerifierService().verify_mainline(
        sections=sections,
        section_states=states,
        project_input=_project_input(),
    )
    assert result.passed is False
    conflict = next(
        item
        for item in result.conflicts
        if item.conflict_type == "cross_section_fact_conflict"
    )
    assert conflict.section_ids == ["compute", "scope"]
    assert conflict.target_section_ids == ["scope"]
    tasks = result.repair_plan.repair_tasks
    assert tasks
    assert tasks[0].target_section_ids == ["scope"]
    assert all(
        task.prerequisite_repair_ids == ([tasks[index - 1].repair_id] if index else [])
        for index, task in enumerate(tasks)
    )


def test_conflict_repair_executor_never_runs_tasks_concurrently() -> None:
    plan = ConflictRepairPlanSchema(
        repair_tasks=[
            RepairTaskSchema(
                repair_id="repair-1",
                priority=1,
                conflict_ids=["conflict-1"],
                target_section_ids=["scope"],
                action=RepairAction.REWRITE,
            ),
            RepairTaskSchema(
                repair_id="repair-2",
                priority=2,
                conflict_ids=["conflict-2"],
                target_section_ids=["compute"],
                action=RepairAction.REWRITE,
                prerequisite_repair_ids=["repair-1"],
            ),
        ]
    )
    completed, history = ConflictRepairPlanService().execute_serially(
        plan,
        resolver=lambda _task: RepairTaskStatus.RESOLVED,
    )
    assert [item["repair_id"] for item in history] == ["repair-1", "repair-2"]
    assert all(item["concurrent_running_count"] == 1 for item in history)
    assert all(
        task.status == RepairTaskStatus.RESOLVED
        for task in completed.repair_tasks
    )


def test_unresolved_document_conflict_is_rejected_by_final_hard_gate(
    tmp_path: Path,
) -> None:
    conflict = DocumentConflictSchema(
        conflict_id="conflict_round7_forced",
        conflict_type="cross_section_fact_conflict",
        severity="blocking",
        section_ids=["section_round7_forced_conflict_001"],
        target_section_ids=["section_round7_forced_conflict_001"],
        claim_ids=["claim_round7_forced"],
        message="forced cross-section conflict",
    )
    plan = ConflictRepairPlanSchema(
        repair_tasks=[
            RepairTaskSchema(
                repair_id="repair_round7_forced",
                priority=1,
                conflict_ids=[conflict.conflict_id],
                target_section_ids=conflict.target_section_ids,
                action=RepairAction.REWRITE,
            )
        ]
    )

    def forced_verify(self, *, mode="full_logic", **kwargs):
        del self, kwargs
        return DocumentVerificationResultSchema(
            mode=mode,
            checked_section_ids=list(conflict.section_ids),
            conflicts=[conflict],
            repair_plan=plan,
            passed=False,
        )

    summary, _factory = _run_fake(
        tmp_path,
        run_id="round7_forced_conflict",
        patchers=(
            patch.object(
                DocumentVerifierService,
                "verify_mainline",
                forced_verify,
            ),
        ),
    )
    assert summary["status"] == "failed"
    assert summary["deliverable_final_result"] is None
    assert summary["rejected_artifact"] is not None
    output = summary["scheme_writer_output"]
    assert output["hard_gate"]["passed"] is False
    assert "document_verification_failed" in output["hard_gate"]["failures"]
    assert "document_conflict_repairs_unresolved" in output["hard_gate"]["failures"]
    closure = output["extra"]["document_closure"]
    assert closure["conflict_repair_history"] == [
        {
            "sequence": 1,
            "repair_id": "repair_round7_forced",
            "target_section_ids": ["section_round7_forced_conflict_001"],
            "action": "rewrite",
            "status": "blocked",
            "concurrent_running_count": 1,
        }
    ]
    assert closure["generation_report"]["task_status"] == "failed"
