"""Round-8 Mainline recovery, draining budget, and retirement acceptance."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.official_checkpoint_saver import (
    create_sqlite_checkpoint_resource,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentRole,
    InvocationRecoveryAction,
    RunningInvocationRecoveryInputSchema,
    UnderlyingInvocationStatus,
    VerifierAction,
    VerifierExecutionMode,
    VerifierResultPayloadSchema,
)
from apps.enterprise_document.runtime.agent_rag_v1.mainline_recovery import (
    MainlineAttemptGuard,
    MainlineRunningInvocationRecovery,
    StaleMainlineResultError,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema
from apps.enterprise_document.services.scheme_writer.agent_integration import (
    MainlineSectionAgentAdapter,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_reasoners import (
    MainlineVerifierModelReasoner,
)
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.model_service import (
    SectionModelService,
)
from apps.enterprise_document.services.scheme_writer.section_generation_service import (
    SectionGenerationService,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from schemas.model import ModelResponseSchema
from schemas.status import ExecutionStatus
from task.task_manager import JsonlTaskManager
from apps.enterprise_document.runtime import agent_rag_v1 as runtime_package
from test_phase4_durable_resume_hitl import _run_paused
from test_phase4_section_agents_mainline import (
    _latest_section_payloads,
    _run_fake,
    _trace_events,
)


_ORIGINAL_SECTION_INVOKE = MainlineSectionAgentAdapter._invoke
_ORIGINAL_PREPARE_PLAN = SchemeGenerationStageService.prepare_document_plan
_ORIGINAL_PRECHECK_PARAGRAPH = SectionExecutionGraph._precheck_paragraph
_ORIGINAL_VERIFIER_INVOKE = MainlineVerifierModelReasoner.invoke
_ORIGINAL_MODEL_CALL = SectionModelService.call_model
_ORIGINAL_SECTION_GENERATION = SectionGenerationService.generate_section


def _workflow():
    return build_scheme_generation_workflow(
        created_at="2026-08-01T18:13:00+08:00"
    )


def test_stale_agent_result_is_rejected_before_section_payload_commit(
    tmp_path: Path,
) -> None:
    """PRESERVE stale-result rejection on the current Target Agent boundary.

    P5 retires the legacy ResearchAgent initial business/Gate topology, so this
    regression adapts its injection point to the WriterAgent technical occurrence
    that remains downstream of the canonical Target Writer path.  The protected
    invariant is unchanged: an Agent result carrying an obsolete input snapshot
    must be rejected before it can mutate the Section checkpoint payload.
    """

    stale_snapshot = "sha256:stale-result"
    injected = {"done": False}

    def stale_once(self, *, agent, context, invocation, node_name):
        result = _ORIGINAL_SECTION_INVOKE(
            self,
            agent=agent,
            context=context,
            invocation=invocation,
            node_name=node_name,
        )
        if not injected["done"] and result.agent_role == AgentRole.WRITER:
            injected["done"] = True
            return result.model_copy(
                update={"input_snapshot_version": stale_snapshot},
                deep=True,
            )
        return result

    summary, factory = _run_fake(
        tmp_path,
        run_id="round8_stale_agent_result",
        patchers=(
            patch.object(
                MainlineSectionAgentAdapter,
                "_invoke",
                stale_once,
            ),
        ),
    )

    assert injected["done"] is True
    assert summary["status"] in {"failed", "retryable_failed"}
    assert summary["deliverable_final_result"] is None
    assert summary["rejected_artifact"] is None
    assert summary["generated_draft"] == {}
    metadata = summary["supervisor_result"]["result"]["workflow_execution"][
        "metadata"
    ]
    assert metadata["terminal_reason"] == "technical_failure"
    assert metadata["business_node_sequence"][-1] == "finalize_workflow_failure"

    events = _trace_events(summary)
    assert any(
        event.get("component_name") == "WriterAgent"
        for event in events
        if event.get("event_type") == "agent_started"
    )

    engine = factory.engines[0]
    persisted_payloads = _latest_section_payloads(Path(engine.checkpointer.path))
    assert persisted_payloads
    persisted_writer_metadata = [
        dict(writer_payload.get("writer_metadata") or {})
        for payload in persisted_payloads.values()
        for writer_payload in payload.get("writer_agent_payloads") or []
    ]
    assert all(
        item.get("input_snapshot_version") != stale_snapshot
        for item in persisted_writer_metadata
    )
    assert engine.active_worker_count == 0


def test_running_invocation_recovery_uses_latest_section_checkpoint(
    tmp_path: Path,
) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="round8_running_invocation_recovery",
    )
    engine = factory.engines[0]
    payloads = _latest_section_payloads(Path(engine.checkpointer.path))
    _namespace, payload = next(
        (namespace, payload)
        for namespace, payload in payloads.items()
        if payload["writer_agent_payloads"]
    )
    metadata = payload["writer_agent_payloads"][-1]["writer_metadata"]
    common = {
        "invocation_id": metadata["invocation_id"],
        "task_id": payload["task_id"],
        "run_id": payload["run_id"],
        "execution_id": metadata["execution_id"],
        "attempt_id": metadata["attempt_id"],
        "input_snapshot_version": metadata["input_snapshot_version"],
        "idempotency_key": f"idem:{metadata['invocation_id']}",
    }
    persisted_payload = SectionExecutionGraphPayloadSchema.model_validate(payload)
    recovery = MainlineRunningInvocationRecovery()

    supplemented = recovery.decide(
        payload=persisted_payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **common,
            result_persisted=True,
            result_ref="artifact://writer/result",
        ),
    )
    assert supplemented.action == InvocationRecoveryAction.SUPPLEMENT_EVENT

    monitoring = recovery.decide(
        payload=persisted_payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **common,
            underlying_queryable=True,
            underlying_status=UnderlyingInvocationStatus.RUNNING,
        ),
    )
    assert monitoring.action == InvocationRecoveryAction.RESUME_MONITORING

    retry = recovery.decide(
        payload=persisted_payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **common,
            underlying_queryable=True,
            underlying_status=UnderlyingInvocationStatus.NOT_FOUND,
        ),
    )
    assert retry.action == InvocationRecoveryAction.IDEMPOTENT_RETRY
    assert retry.starts_new_call is True
    assert retry.idempotency_key == common["idempotency_key"]

    stale = recovery.decide(
        payload=persisted_payload,
        invocation=RunningInvocationRecoveryInputSchema(
            **{
                **common,
                "attempt_id": f"{common['attempt_id']}:superseded",
            }
        ),
    )
    assert stale.action == InvocationRecoveryAction.DISCARD_STALE
    assert summary["deliverable_final_result"] is not None


def test_stale_human_review_decision_keeps_durable_interrupt_paused(
    tmp_path: Path,
) -> None:
    summary, factory = _run_paused(
        tmp_path,
        run_id="round8_human_review_stale",
    )
    old_engine = factory.engines[0]
    workflow = factory.workflows[0]
    graph_state = GraphStateSchema.model_validate(
        summary["supervisor_result"]["result"]["shared_state"]
    )
    resource = create_sqlite_checkpoint_resource(old_engine.checkpointer.path)
    engine = EnterpriseDocumentWorkflowEngine(
        stage_service=old_engine.stage_service,
        checkpointer=resource.saver,
        side_effect_commit_store=old_engine.side_effect_commit_store,
    )
    try:
        inspection = engine.inspect(workflow, run_id=summary["run_id"])
        snapshot = inspection["interrupts"][0]["value"]["metadata"][
            "input_snapshot_version"
        ]

        with pytest.raises(ValueError, match="snapshot is stale"):
            engine.resume(
                workflow,
                graph_state,
                {
                    "decision": "approved",
                    "task_id": summary["task_id"],
                    "run_id": summary["run_id"],
                    "input_snapshot_version": "sha256:stale-human-decision",
                },
            )

        still_paused = engine.inspect(workflow, run_id=summary["run_id"])
        assert still_paused["human_review_required"] is True
        assert still_paused["interrupts"]

        resumed = engine.resume(
            workflow,
            graph_state,
            {
                "decision": "approved",
                "task_id": summary["task_id"],
                "run_id": summary["run_id"],
                "input_snapshot_version": snapshot,
                "reviewer_id": "round8-reviewer",
            },
        )
        assert resumed.metadata["workflow_complete"] is True
        assert resumed.metadata["resumed"] is True
        assert resumed.metadata["resume_count"] == 1
    finally:
        resource.close()


def test_task_budget_draining_finishes_first_section_and_starts_no_more_agents(
    tmp_path: Path,
) -> None:
    def one_section_budget(self, graph_state, payload):
        planned = _ORIGINAL_PREPARE_PLAN(self, graph_state, payload)
        project_input = planned.project_input
        assert project_input is not None
        requirements = project_input.generation_requirements.model_copy(
            update={
                "extra": {
                    **dict(project_input.generation_requirements.extra),
                    "task_budget": {"max_section_starts": 1},
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

    summary, factory = _run_fake(
        tmp_path,
        run_id="round8_task_budget_draining",
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                one_section_budget,
            ),
        ),
    )
    engine = factory.engines[0]
    payloads = _latest_section_payloads(Path(engine.checkpointer.path))
    assert len(payloads) >= 2
    admitted = [
        payload
        for payload in payloads.values()
        if payload["budget_exhaustion_resource"] is None
    ]
    blocked = [
        payload
        for payload in payloads.values()
        if payload["budget_exhaustion_resource"] == "task_section_starts"
    ]
    assert len(admitted) == 1
    assert len(blocked) == len(payloads) - 1
    assert admitted[0]["completed"] is True
    assert admitted[0]["writer_agent_payloads"]
    assert all(item["completed"] is True for item in blocked)
    assert all(not item["research_agent_payloads"] for item in blocked)
    assert all(not item["writer_agent_payloads"] for item in blocked)
    assert all(not item["verifier_agent_payloads"] for item in blocked)

    resource = create_sqlite_checkpoint_resource(engine.checkpointer.path)
    restored_engine = EnterpriseDocumentWorkflowEngine(
        stage_service=engine.stage_service,
        checkpointer=resource.saver,
        side_effect_commit_store=engine.side_effect_commit_store,
    )
    try:
        top_payload = restored_engine._latest_checkpoint_payload(
            _workflow(),
            run_id=summary["run_id"],
        )
    finally:
        resource.close()
    assert top_payload is not None
    task_budget = top_payload.document_validation.metadata["task_budget"]
    assert task_budget["draining"] is True
    assert task_budget["draining_reason"] == "section_starts"
    assert len(task_budget["started_section_ids"]) == 1
    assert task_budget["started_section_ids"] == task_budget[
        "completed_section_ids"
    ]
    assert task_budget["active_section_ids"] == []


def test_mainline_partial_success_publishes_deliverable(
    tmp_path: Path,
) -> None:
    forced = {"done": False}

    def one_partial_section(self, shared_state, **kwargs):
        section = _ORIGINAL_SECTION_GENERATION(
            self,
            shared_state,
            **kwargs,
        )
        if not forced["done"] and int(kwargs["section_order"]) == 1:
            forced["done"] = True
            return section.model_copy(
                update={"status": ExecutionStatus.PARTIAL_SUCCESS},
                deep=True,
            )
        return section

    summary, _factory = _run_fake(
        tmp_path,
        run_id="round8_mainline_partial_success",
        patchers=(
            patch.object(
                SectionGenerationService,
                "generate_section",
                one_partial_section,
            ),
        ),
    )
    assert forced["done"] is True
    assert summary["status"] == "partial_success"
    assert summary["deliverable_final_result"] is not None
    assert summary["rejected_artifact"] is None
    assert any(
        section["status"] == "partial_success"
        for section in summary["generated_draft"]["sections"]
    )


def test_mainline_rewrite_scenario_creates_new_generation_attempt(
    tmp_path: Path,
) -> None:
    forced: set[str] = set()

    def rewrite_once(self, *, context, invocation_input, tools):
        section_id = str(context.section_id or "")
        if section_id.endswith("_001") and section_id not in forced:
            forced.add(section_id)
            return VerifierResultPayloadSchema(
                execution_mode=VerifierExecutionMode.SECTION_VERIFY,
                action=VerifierAction.REWRITE,
                verification_ref=f"state://round8-rewrite/{section_id}",
                checks={"forced_rewrite": True},
                decision_metadata={
                    "reasoner": "round8_mainline_rewrite",
                    "retrieval_route": None,
                    "fake_payload_only": False,
                    "production_adapter": True,
                },
            )
        return _ORIGINAL_VERIFIER_INVOKE(
            self,
            context=context,
            invocation_input=invocation_input,
            tools=tools,
        )

    summary, factory = _run_fake(
        tmp_path,
        run_id="round8_mainline_rewrite",
        patchers=(
            patch.object(
                MainlineVerifierModelReasoner,
                "invoke",
                rewrite_once,
            ),
        ),
    )
    assert summary["status"] == "success"
    payloads = _latest_section_payloads(
        Path(factory.engines[0].checkpointer.path)
    )
    first = next(
        item
        for item in payloads.values()
        if str(item["section_id"]).endswith("_001")
    )
    assert first["generation_attempt"] == 2
    assert [
        item["action"] for item in first["verifier_agent_payloads"]
    ] == ["rewrite", "accept"]
    assert first["executed_nodes"].count("generate_section") == 2
    assert first["completed"] is True


def test_mainline_paragraph_repair_replaces_conflicting_version(
    tmp_path: Path,
) -> None:
    run_id = "round8_mainline_paragraph_repair"

    def trusted_server_fact(self, graph_state, payload):
        planned = _ORIGINAL_PREPARE_PLAN(self, graph_state, payload)
        project_input = planned.project_input
        assert project_input is not None
        fact = StructuredFactSchema(
            fact_id="round8-server-count",
            task_id=planned.task_id,
            run_id=planned.run_id,
            fact_type="device_summary",
            content="服务器",
            source_type="table",
            source_ids=["excel://round8/server-count"],
            created_at="2026-08-01T18:13:00+08:00",
            extra={
                "device_name": "服务器",
                "quantity": "2",
                "unit": "台",
            },
        )
        return planned.model_copy(
            update={
                "project_input": project_input.model_copy(
                    update={
                        "metadata": {
                            **dict(project_input.metadata),
                            "server_count": 2,
                        }
                    },
                    deep=True,
                ),
                "structured_facts": [fact],
            },
            deep=True,
        )

    def conflict_then_repair(
        self,
        shared_state,
        *,
        section_id,
        **kwargs,
    ):
        first_id = f"section_{shared_state.run_id}_001"
        if section_id in {first_id, f"{first_id}_attempt_2"}:
            quantity = 3 if section_id == first_id else 2
            content = (
                f"本项目部署服务器{quantity}台，用于承载业务应用、知识检索、"
                "模型服务和运行监控。设备数量以经过确认的结构化设备清单为准，"
                "配置变更必须保留来源、版本和审批记录，避免未经确认的数量进入正式文档。"
            )
            return ModelResponseSchema(
                model_call_id=f"model_call_{section_id}",
                task_id=shared_state.task_id,
                run_id=shared_state.run_id,
                model_name="round8_repair_stub",
                success=True,
                content=content,
                created_at="2026-08-01T18:13:00+08:00",
                finish_reason="stop",
            )
        return _ORIGINAL_MODEL_CALL(
            self,
            shared_state,
            section_id=section_id,
            **kwargs,
        )

    summary, factory = _run_fake(
        tmp_path,
        run_id=run_id,
        patchers=(
            patch.object(
                SchemeGenerationStageService,
                "prepare_document_plan",
                trusted_server_fact,
            ),
            patch.object(
                SectionModelService,
                "call_model",
                conflict_then_repair,
            ),
        ),
    )
    assert summary["status"] == "success"
    payloads = _latest_section_payloads(
        Path(factory.engines[0].checkpointer.path)
    )
    first = next(
        item
        for item in payloads.values()
        if str(item["section_id"]).endswith("_001")
    )
    assert first["generation_attempt"] == 2
    assert first["paragraph_blocked_id"] is None
    assert first["completed"] is True
    assert "服务器2台" in first["section"]["content"]
    assert "服务器3台" not in first["section"]["content"]
    assert first["paragraph_artifacts"]
    assert all(
        item["paragraph_version"] == 2
        for item in first["paragraph_artifacts"]
    )
    assert all(
        item["blocking"] is False
        for item in first["paragraph_precheck_results"]
    )
    assert first["executed_nodes"].count("generate_section") == 2


def test_mainline_replan_supersedes_attempt_and_rejects_old_result(
    tmp_path: Path,
) -> None:
    summary, factory = _run_fake(
        tmp_path,
        run_id="round8_mainline_replan",
    )
    assert summary["status"] == "success"
    old_engine = factory.engines[0]
    database = Path(old_engine.checkpointer.path)
    before_payloads = _latest_section_payloads(database)
    namespace, before = next(
        (namespace, payload)
        for namespace, payload in before_payloads.items()
        if str(payload["section_id"]).endswith("_001")
    )
    before_model = SectionExecutionGraphPayloadSchema.model_validate(before)
    old_writer_metadata = dict(
        before_model.writer_agent_payloads[-1].writer_metadata
    )
    old_snapshot = before_model.section_state.attempt.input_snapshot_version
    revised_plan = before_model.section_plan.model_copy(
        update={
            "metadata": {
                **dict(before_model.section_plan.metadata),
                "replan_reason": "round8_mainline_replan_acceptance",
                "revision_marker": "v2",
            }
        },
        deep=True,
    )
    workflow_namespace = namespace.split("/sections/", 1)[0]

    project_root = Path(__file__).resolve().parents[2]
    settings = replace(
        get_settings(),
        data_root=tmp_path,
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
    with patch.dict(os.environ, environment):
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=False,
        )
        supervisor = SupervisorFactory().build(
            workflow=_workflow(),
            runs_dir=database.parent,
            captures_dir=tmp_path / "captures",
            task_manager=JsonlTaskManager(tmp_path / "tasks"),
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=True,
        )
    engine = supervisor.workflow_engine
    assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
    graph = engine.section_execution_graph
    assert graph is not None
    shared_state = GraphStateSchema.model_validate(
        summary["supervisor_result"]["result"]["shared_state"]
    )
    registry = DocumentCitationRegistry.from_citations(
        before_model.citation_registry
    )
    try:
        result = graph.replan(
            workflow_namespace=workflow_namespace,
            run_id=before_model.run_id,
            section_id=str(before_model.section_id),
            revised_plan=revised_plan,
            shared_state=shared_state,
            citation_registry=registry,
        )
        after = _latest_section_payloads(database)[namespace]
    finally:
        supervisor.close()

    after_model = SectionExecutionGraphPayloadSchema.model_validate(after)
    assert result.section.status.value == "success"
    assert after_model.completed is True
    assert after_model.generation_attempt == 2
    assert after_model.section_state.attempt.attempt_number == 2
    assert after_model.section_state.attempt.plan_revision == 2
    assert after_model.section_state.attempt.input_snapshot_version != old_snapshot
    superseded = after_model.section_state.attempt.metadata[
        "superseded_attempts"
    ]
    assert superseded[-1]["attempt_id"] == (
        before_model.section_state.attempt.attempt_id
    )
    assert superseded[-1]["status"] == "superseded"
    assert all(
        item.paragraph_version == 2
        for item in after_model.paragraph_artifacts
    )

    with pytest.raises(StaleMainlineResultError, match="stale agent result"):
        MainlineAttemptGuard.validate_result_metadata(
            run_id=after_model.run_id,
            section_id=str(after_model.section_id),
            section_state=after_model.section_state,
            project_input=after_model.project_input,
            section_plan=after_model.section_plan,
            metadata=old_writer_metadata,
        )


def test_section_crash_resume_reuses_committed_paragraph_without_duplication(
    tmp_path: Path,
) -> None:
    crashed = {"done": False}

    def crash_after_first_commit(self, state):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("round8 injected crash after paragraph commit")
        return _ORIGINAL_PRECHECK_PARAGRAPH(self, state)

    summary, factory = _run_fake(
        tmp_path,
        run_id="round8_section_paragraph_crash_resume",
        patchers=(
            patch.object(
                SectionExecutionGraph,
                "_precheck_paragraph",
                crash_after_first_commit,
            ),
        ),
    )
    assert crashed["done"] is True
    assert summary["status"] in {"failed", "retryable_failed"}
    old_engine = factory.engines[0]
    database = Path(old_engine.checkpointer.path)
    before_payloads = _latest_section_payloads(database)
    namespace, before = next(
        (namespace, payload)
        for namespace, payload in before_payloads.items()
        if payload["paragraph_artifacts"] and not payload["completed"]
    )
    committed_ids = [
        item["paragraph_id"] for item in before["paragraph_artifacts"]
    ]
    assert committed_ids
    workflow_namespace = namespace.split("/sections/", 1)[0]

    project_root = Path(__file__).resolve().parents[2]
    settings = replace(
        get_settings(),
        data_root=tmp_path,
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
    with patch.dict(os.environ, environment):
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=False,
        )
        supervisor = SupervisorFactory().build(
            workflow=_workflow(),
            runs_dir=database.parent,
            captures_dir=tmp_path / "captures",
            task_manager=JsonlTaskManager(tmp_path / "tasks"),
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=True,
        )
    engine = supervisor.workflow_engine
    assert isinstance(engine, EnterpriseDocumentWorkflowEngine)
    graph = engine.section_execution_graph
    assert graph is not None
    shared_state = GraphStateSchema.model_validate(
        summary["supervisor_result"]["result"]["shared_state"]
    )
    before_model = SectionExecutionGraphPayloadSchema.model_validate(before)
    registry = DocumentCitationRegistry.from_citations(
        before_model.citation_registry
    )
    try:
        result = graph.resume(
            workflow_namespace=workflow_namespace,
            run_id=before["run_id"],
            section_id=before["section_id"],
            shared_state=shared_state,
            citation_registry=registry,
        )
        after_payloads = _latest_section_payloads(database)
        after = after_payloads[namespace]
    finally:
        supervisor.close()

    assert result.section.section_id == before["section_id"]
    assert after["completed"] is True
    final_ids = [item["paragraph_id"] for item in after["paragraph_artifacts"]]
    assert len(final_ids) == len(set(final_ids))
    assert final_ids[: len(committed_ids)] == committed_ids
    assert all(
        final_ids.count(paragraph_id) == 1 for paragraph_id in committed_ids
    )
    assert after["paragraph_cursor"] == len(after["paragraph_candidates"])


def test_standalone_runtime_is_lazy_fixture_only_and_absent_from_production() -> None:
    assert runtime_package.LEGACY_STANDALONE_FIXTURE_ONLY is True
    root = Path(__file__).resolve().parents[1]
    production_files = [
        root / "apps/enterprise_document/workflows/top_level_business_graph.py",
        root / "apps/enterprise_document/workflows/section_execution_graph.py",
        root / "bootstrap/supervisor_factory.py",
    ]
    forbidden = (
        "WorkflowOrchestrator",
        "RuntimeFactory",
        "MultiAgentApplicationService",
        "JsonCheckpointStore",
        "TaskStatusCalculator",
    )
    for path in production_files:
        content = path.read_text(encoding="utf-8")
        assert not any(symbol in content for symbol in forbidden)
