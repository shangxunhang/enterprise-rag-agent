from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from agent.runtime.official_checkpoint_saver import CompressedJsonPlusSerializer
from agent.runtime.state_access import SharedStateWriter
from application.mainline_service import MainlineApplicationService

from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyLoader
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
)
from apps.enterprise_document.schemas.scheme_writer.section_execution import (
    SectionExecutionResultSchema,
)
from apps.enterprise_document.schemas.table_agent_schema import (
    StructuredFactSchema,
    TableAgentOutputSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.schemas.scheme_writer.planning import (
    DocumentPlanSchema,
    SectionPlanSchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.services.agent_rag_v1.fact_snapshot_runtime import (
    FactSnapshotRuntime,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.plan_runtime import SectionPlanRuntime
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from apps.enterprise_document.services.agent_rag_v1.research_runtime import ResearchRuntime
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import (
    TaskRequestFactSourceResolver,
    TargetWorkflowRuntime,
    WorkflowCutoverIntegrityError,
)
from apps.enterprise_document.services.project_input_normalization.fact_extractor import (
    StructuredFactExtractor,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    MainlineSectionAgentAdapter,
)
from apps.enterprise_document.workflows.section_parallel_execution_graph import (
    SectionParallelExecutionGraph,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    EvidenceRequirementsMaterializer,
    scheme_generation_minimal_profile,
    scheme_generation_minimal_task_contract,
)
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


def test_w13_section_result_exposes_exact_canonical_owner_projections() -> None:
    fields = SectionExecutionResultSchema.model_fields
    expected = {
        "canonical_plan_binding",
        "active_execution_lineage",
        "active_evidence_package_binding",
        "current_research_round_binding",
        "writer_execution_ref",
        "writer_generation_invocation_ref",
        "section_paragraph_manifest",
    }
    assert expected <= set(fields)


def test_w13_section_graph_projects_canonical_refs_without_reconstruction() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "apps/enterprise_document/workflows/section_execution_graph.py"
    ).read_text(encoding="utf-8")
    required_assignments = (
        "canonical_plan_binding=payload.canonical_plan_binding",
        "active_execution_lineage=payload.active_execution_lineage",
        "active_evidence_package_binding=payload.active_evidence_package_binding",
        "current_research_round_binding=payload.current_research_round_binding",
        "writer_execution_ref=payload.writer_execution_ref",
        "payload.writer_generation_invocation_ref",
        "section_paragraph_manifest=payload.section_paragraph_manifest",
    )
    for assignment in required_assignments:
        assert assignment in source


def _w13_runtime(tmp_path):
    task_manager = JsonlTaskManager(tmp_path / "tasks")
    task = TaskSchema(
        task_id="task-w13",
        run_id="run-w13",
        tenant_id="tenant-w13",
        task_type="scheme_generation",
        user_input="generate exact document",
        project_input={"user_query": "generate exact document"},
        created_at="2026-09-06T00:00:00+00:00",
    )
    task_manager.create_task(task)
    lifecycle = RunAttemptLifecycleService(tmp_path / "lifecycle.jsonl")
    artifact = ArtifactRuntime(
        tmp_path / "artifact-runtime.jsonl",
        lifecycle_service=lifecycle,
    )
    fact = FactSnapshotRuntime(
        artifact_runtime=artifact,
        source_binding_resolver=TaskRequestFactSourceResolver(task_manager),
    )
    requirements = EvidenceRequirementsMaterializer.materialize_profile_task(
        profile=scheme_generation_minimal_profile(),
        task_contract=scheme_generation_minimal_task_contract(),
    )
    plan = SectionPlanRuntime(
        artifact_runtime=artifact,
        fact_snapshot_runtime=fact,
        requirements_resolver=lambda ref: requirements,
    )
    runtime = TargetWorkflowRuntime(
        task_manager=task_manager,
        lifecycle_service=lifecycle,
        fact_snapshot_runtime=fact,
        section_plan_runtime=plan,
        requirements=requirements,
    )
    return task_manager, lifecycle, artifact, fact, plan, runtime, requirements


def _w13_facts(*, content="100 cores"):
    return [
        StructuredFactSchema(
            fact_id="fact_run-w13_project_scale",
            task_id="task-w13",
            run_id="run-w13",
            fact_type="project_scale",
            content=content,
            source_type="project_input",
            source_ids=["task-w13"],
            confidence=1.0,
            created_at="2026-09-06T00:00:01+00:00",
        )
    ]


def _w13_document_plan():
    return DocumentPlanSchema(
        plan_id="plan_run-w13",
        document_id="doc-w13",
        document_title="Wave 13",
        sections=[
            SectionPlanSchema(
                section_id="section-run-w13-001",
                section_title="Evidence Section",
                section_order=1,
                citation_required=True,
            ),
            SectionPlanSchema(
                section_id="section-run-w13-002",
                section_title="Technical Section",
                section_order=2,
                citation_required=False,
            ),
        ],
        planning_source="project_input",
        created_at="2026-09-06T00:00:02+00:00",
    )


def test_w13_materializes_task_bound_fact_and_canonical_section_plan(tmp_path) -> None:
    _, lifecycle, _, fact, plan, runtime, requirements = _w13_runtime(tmp_path)
    projection = runtime.ensure_section_plan(
        task_id="task-w13",
        run_id="run-w13",
        document_plan=_w13_document_plan(),
        structured_facts=_w13_facts(),
    )

    run = lifecycle.load_run("run-w13")
    assert run.status.value == "running"
    fact_content = fact.read_binding(
        projection.fact_snapshot_binding,
        require_current=True,
    )
    assert [item.fact_id for item in fact_content.facts] == [
        "fact_run-w13_project_scale"
    ]
    assert fact_content.facts[0].normalized_value == "100 cores"
    source = fact_content.source_bindings[0]
    assert source.source_ref == "project-input://task/task-w13"
    assert source.source_version_ref.startswith("task-request://sha256/")

    raw_plan, exact_ref = plan.artifact_runtime.read_version(
        projection.plan_version_ref
    )
    assert exact_ref == projection.plan_version_ref
    sections = raw_plan["sections"]
    assert sections[0]["strategy"] == SectionStrategyCode.RAG_GENERATE.value
    assert sections[0]["evidence_requirement_ids"] == [
        item.requirement_id for item in requirements.requirements
    ]
    assert sections[1]["strategy"] == SectionStrategyCode.TECHNICAL_GENERATE.value
    assert sections[1]["evidence_requirement_ids"] == [
        item.requirement_id for item in requirements.requirements
    ]
    assert [item.section_id for item in projection.section_plan_bindings] == [
        "section-run-w13-001",
        "section-run-w13-002",
    ]


def test_w13_planning_replay_reuses_exact_canonical_sources(tmp_path) -> None:
    *_, artifact, fact, plan, runtime, _ = _w13_runtime(tmp_path)
    first = runtime.ensure_section_plan(
        task_id="task-w13",
        run_id="run-w13",
        document_plan=_w13_document_plan(),
        structured_facts=_w13_facts(),
    )
    first_ledger = artifact.ledger_path.read_text(encoding="utf-8")

    second = runtime.ensure_section_plan(
        task_id="task-w13",
        run_id="run-w13",
        document_plan=_w13_document_plan(),
        structured_facts=_w13_facts(),
    )

    assert second == first
    assert artifact.ledger_path.read_text(encoding="utf-8") == first_ledger
    assert fact.resolve_current_binding(first.fact_snapshot_binding.version_ref.logical_ref) == (
        first.fact_snapshot_binding
    )
    assert plan.resolve_current_plan_binding(
        run_id="run-w13",
        document_scope_ref="document:doc-w13",
        section_id="section-run-w13-001",
    ) == first.section_plan_bindings[0]


def test_w13_replay_fails_closed_if_same_task_source_projects_different_facts(tmp_path) -> None:
    *_, runtime, _ = _w13_runtime(tmp_path)
    runtime.ensure_section_plan(
        task_id="task-w13",
        run_id="run-w13",
        document_plan=_w13_document_plan(),
        structured_facts=_w13_facts(),
    )
    with pytest.raises(WorkflowCutoverIntegrityError, match="FactSnapshot differs"):
        runtime.ensure_section_plan(
            task_id="task-w13",
            run_id="run-w13",
            document_plan=_w13_document_plan(),
            structured_facts=_w13_facts(content="tampered 999 cores"),
        )


def test_w13_section_attempt_and_start_research_authorization_are_replay_stable(
    tmp_path,
) -> None:
    *_, lifecycle, _artifact, _fact, _plan, runtime, _requirements = (
        _w13_runtime(tmp_path)
    )
    projection = runtime.ensure_section_plan(
        task_id="task-w13",
        run_id="run-w13",
        document_plan=_w13_document_plan(),
        structured_facts=_w13_facts(),
    )
    binding = projection.section_plan_bindings[0]
    first_lineage, first_auth = runtime.ensure_section_execution_prerequisites(binding)
    first_log = lifecycle.path.read_text(encoding="utf-8")

    replay_lineage, replay_auth = runtime.ensure_section_execution_prerequisites(binding)
    assert replay_lineage == first_lineage
    assert replay_auth == first_auth
    assert lifecycle.path.read_text(encoding="utf-8") == first_log

    round_ref = ResearchRuntime.derive_round_ref(
        run_id="run-w13",
        section_id=binding.section_id,
        authorization_binding=first_auth,
        active_attempt=first_lineage,
    )
    runtime.validate_research_authorization(first_auth, round_ref)


def test_w13_research_authorization_rejects_noncanonical_occurrence(tmp_path) -> None:
    *_, runtime, _ = _w13_runtime(tmp_path)
    projection = runtime.ensure_section_plan(
        task_id="task-w13",
        run_id="run-w13",
        document_plan=_w13_document_plan(),
        structured_facts=_w13_facts(),
    )
    binding = projection.section_plan_bindings[0]
    lineage, authorization = runtime.ensure_section_execution_prerequisites(binding)
    round_ref = ResearchRuntime.derive_round_ref(
        run_id="run-w13",
        section_id=binding.section_id,
        authorization_binding=authorization,
        active_attempt=lineage,
    )
    tampered = authorization.model_copy(
        update={"authorization_ref": "workflow-start-research://sha256/tampered"},
        deep=True,
    )
    with pytest.raises(WorkflowCutoverIntegrityError, match="authorization_ref"):
        runtime.validate_research_authorization(tampered, round_ref)


def test_w13_rejects_structured_fact_without_exact_project_input_source(tmp_path) -> None:
    *_, runtime, _ = _w13_runtime(tmp_path)
    invalid = _w13_facts()[0].model_copy(
        update={"source_type": "generated", "source_ids": ["opaque"]},
        deep=True,
    )
    with pytest.raises(WorkflowCutoverIntegrityError, match="source"):
        runtime.ensure_section_plan(
            task_id="task-w13",
            run_id="run-w13",
            document_plan=_w13_document_plan(),
            structured_facts=[invalid],
        )


def _w13_current_new_task_project_input(project: ProjectInputSchema) -> ProjectInputSchema:
    """Caller-approved test conversion; preserve the historical V2 helpers."""
    raw = project.model_dump(mode="json")
    raw["schema_version"] = "project_input_v3"
    return ProjectInputSchema.model_validate(raw)


def _w13_single_section_project_input(task_id: str) -> ProjectInputSchema:
    return ProjectInputSchema(
        schema_version="project_input_v2",
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成 Wave13 单章节验证文档",
        generation_requirements={
            "required_sections": ["Wave13 验证章节"],
            "need_citation": False,
            "citation_required_sections": [],
            "need_human_review": False,
            "min_section_chars": 80,
        },
        output_schema={
            "document_title": "Wave13 验证文档",
            "required_sections": ["Wave13 验证章节"],
        },
    )


def _w13_two_section_project_input(task_id: str) -> ProjectInputSchema:
    return ProjectInputSchema(
        schema_version="project_input_v2",
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成 Wave13 双章节并行验证文档",
        generation_requirements={
            "required_sections": ["Wave13 并行章节A", "Wave13 并行章节B"],
            "need_citation": False,
            "citation_required_sections": [],
            "need_human_review": False,
            "min_section_chars": 80,
        },
        output_schema={
            "document_title": "Wave13 双章节验证文档",
            "required_sections": ["Wave13 并行章节A", "Wave13 并行章节B"],
        },
    )


class _W13SingleSectionNormalization:
    def execute(self, state):
        from apps.enterprise_document.services.project_input_normalization.input_contract import (
            read_saved_project_input,
        )

        project_input = read_saved_project_input(
            state.context_bundle.business.project_input
        )
        fact = StructuredFactSchema(
            fact_id=f"fact_{state.run_id}_scope",
            task_id=state.task_id,
            run_id=state.run_id,
            fact_type="project_scale",
            content="Wave13 single-section production composition smoke",
            source_type="project_input",
            source_ids=[state.task_id],
            confidence=1.0,
            created_at="2026-09-06T00:00:00+00:00",
        )
        structured_facts = [
            *StructuredFactExtractor().extract(
                state, project_input, "2026-09-06T00:00:00+00:00"
            ),
            fact,
        ]
        output = TableAgentOutputSchema(
            task_id=state.task_id,
            run_id=state.run_id,
            status=ExecutionStatus.SUCCESS,
            project_input=project_input,
            structured_facts=structured_facts,
        )
        SharedStateWriter().set_project_input_normalization(
            state,
            project_input=project_input.model_dump(),
            table_agent_output=output.model_dump(),
            structured_facts=[item.model_dump() for item in structured_facts],
            source_materials=[],
            missing_information=[],
            conflicting_information=[],
            manual_boundaries=[],
        )
        return output


class _W13TwoSectionNormalization:
    def execute(self, state):
        from apps.enterprise_document.services.project_input_normalization.input_contract import (
            read_saved_project_input,
        )

        project_input = read_saved_project_input(
            state.context_bundle.business.project_input
        )
        fact = StructuredFactSchema(
            fact_id=f"fact_{state.run_id}_parallel_scope",
            task_id=state.task_id,
            run_id=state.run_id,
            fact_type="project_scale",
            content="Wave13 two-section parallel synchronization smoke",
            source_type="project_input",
            source_ids=[state.task_id],
            confidence=1.0,
            created_at="2026-09-06T00:00:00+00:00",
        )
        structured_facts = [
            *StructuredFactExtractor().extract(
                state, project_input, "2026-09-06T00:00:00+00:00"
            ),
            fact,
        ]
        output = TableAgentOutputSchema(
            task_id=state.task_id,
            run_id=state.run_id,
            status=ExecutionStatus.SUCCESS,
            project_input=project_input,
            structured_facts=structured_facts,
        )
        SharedStateWriter().set_project_input_normalization(
            state,
            project_input=project_input.model_dump(),
            table_agent_output=output.model_dump(),
            structured_facts=[item.model_dump() for item in structured_facts],
            source_materials=[],
            missing_information=[],
            conflicting_information=[],
            manual_boundaries=[],
        )
        return output


class _W13ParallelPlannerAdapter:
    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def plan(self, **kwargs):
        outcome = self.delegate.plan(**kwargs)
        sections = [
            item.model_copy(
                update={
                    "execution_group": "w13-parallel",
                    "can_run_in_parallel": True,
                    "requires_global_context": False,
                    "depends_on": [],
                },
                deep=True,
            )
            for item in outcome.document_plan.sections
        ]
        plan = outcome.document_plan.model_copy(
            update={"sections": sections},
            deep=True,
        )
        return replace(outcome, document_plan=plan)


class _W13ParallelSpy(SectionParallelExecutionGraph):
    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    def execute_batch(self, section_plans, *, max_concurrency, execute):
        self.batches.append(tuple(item.section_id for item in section_plans))
        return super().execute_batch(
            section_plans,
            max_concurrency=max_concurrency,
            execute=execute,
        )


class _W13InspectingFactory(SupervisorFactory):
    def __init__(self, normalization=None, *, force_parallel: bool = False) -> None:
        super().__init__()
        self.engines = []
        self.normalization = normalization or _W13SingleSectionNormalization()
        self.force_parallel = bool(force_parallel)
        self.parallel_spies: list[_W13ParallelSpy] = []

    def build(self, **kwargs):
        supervisor = super().build(**kwargs)
        engine = supervisor.workflow_engine
        engine.normalization_use_case = self.normalization
        if self.force_parallel:
            engine.stage_service.planner_adapter = _W13ParallelPlannerAdapter(
                engine.stage_service.planner_adapter
            )
            spy = _W13ParallelSpy()
            engine.stage_service.section_parallel_execution_graph = spy
            self.parallel_spies.append(spy)
        self.engines.append(engine)
        return supervisor


def _latest_w13_section_payload(engine) -> SectionExecutionGraphPayloadSchema:
    serializer = CompressedJsonPlusSerializer()
    with sqlite3.connect(Path(engine.checkpointer.path)) as connection:
        rows = connection.execute(
            "SELECT checkpoint_ns, type, checkpoint FROM checkpoints "
            "WHERE checkpoint_ns LIKE '%/sections/%' "
            "ORDER BY checkpoint_id DESC"
        ).fetchall()
    for _namespace, payload_type, checkpoint in rows:
        decoded = serializer.loads_typed((str(payload_type), checkpoint))
        raw = (decoded.get("channel_values") or {}).get("section_payload")
        if not isinstance(raw, dict):
            continue
        payload = SectionExecutionGraphPayloadSchema.model_validate(raw)
        if payload.result is not None:
            return payload
    raise AssertionError("Wave13 focused smoke produced no completed Section payload")


def _w13_top_level_payload_after_node(
    engine,
    node_name: str | None = None,
) -> SchemeGenerationGraphPayloadSchema:
    serializer = CompressedJsonPlusSerializer()
    with sqlite3.connect(Path(engine.checkpointer.path)) as connection:
        rows = connection.execute(
            "SELECT type, checkpoint FROM checkpoints "
            "WHERE checkpoint_ns=? ORDER BY checkpoint_id DESC",
            ("scheme_generation_v2:v2.0",),
        ).fetchall()
    for payload_type, checkpoint in rows:
        decoded = serializer.loads_typed((str(payload_type), checkpoint))
        channels = decoded.get("channel_values") or {}
        if node_name is not None and channels.get("current_business_node") != node_name:
            continue
        raw = channels.get("scheme_generation_payload")
        if isinstance(raw, dict):
            return SchemeGenerationGraphPayloadSchema.model_validate(raw)
    label = node_name or "latest"
    raise AssertionError(f"Wave13 produced no top-level payload for {label}")


def _latest_w13_top_level_payload(engine) -> SchemeGenerationGraphPayloadSchema:
    return _w13_top_level_payload_after_node(engine)


def test_w13_single_section_live_cutover_uses_context_paragraph_claim_owners(
    tmp_path,
) -> None:
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
    factory = _W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(
            patch.object(
                MainlineSectionAgentAdapter,
                "verify",
                side_effect=AssertionError(
                    "legacy projected VerifierAgent is reachable from Target live path"
                ),
            )
        )
        stack.enter_context(
            patch(
                "apps.enterprise_document.services.scheme_writer.stage_service."
                "evaluate_scheme_draft",
                side_effect=AssertionError(
                    "legacy HardGate is reachable from Target live path"
                ),
            )
        )
        stack.enter_context(
            patch(
                "apps.enterprise_document.services.scheme_writer.stage_service."
                "ReleasePublicationService.publish",
                side_effect=AssertionError(
                    "legacy ReleasePublicationService is reachable from Target live path"
                ),
            )
        )
        stack.enter_context(
            patch(
                "apps.enterprise_document.workflows.top_level_business_graph."
                "TaskDrainingBudget.from_snapshot",
                side_effect=AssertionError(
                    "TaskDrainingBudget is reachable from Target live path"
                ),
            )
        )
        stack.enter_context(
            patch(
                "apps.enterprise_document.workflows.top_level_business_graph."
                "EnterpriseDocumentWorkflowEngine._human_review_interrupt",
                side_effect=AssertionError(
                    "Human Review is reachable from Target live path"
                ),
            )
        )
        stack.enter_context(
            patch(
                "apps.enterprise_document.services.scheme_writer.stage_service."
                "VersionPolicyService.decide",
                side_effect=AssertionError(
                    "legacy VersionDecision is reachable from Target live path"
                ),
            )
        )
        stack.enter_context(
            patch(
                "apps.enterprise_document.services.scheme_writer.stage_service."
                "GenerationReportService.build_mainline",
                side_effect=AssertionError(
                    "legacy GenerationReport is reachable from Target live path"
                ),
            )
        )
        task_id = "task_w13_single_section_live_cutover"
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成 Wave13 单章节验证文档",
            run_id="w13_single_section_live_cutover",
            task_id=task_id,
            output_root=tmp_path,
            clean_existing=True,
            settings=settings,
            project_input=_w13_current_new_task_project_input(_w13_single_section_project_input(task_id)).model_dump(
                mode="json"
            ),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )

    engine = factory.engines[0]
    payload = _latest_w13_section_payload(engine)
    result = payload.result
    assert result is not None
    assert result.canonical_plan_binding is not None
    assert result.active_execution_lineage is not None
    assert result.active_evidence_package_binding is not None
    assert result.current_research_round_binding is not None
    assert result.writer_execution_ref is not None
    assert result.writer_generation_invocation_ref is not None
    assert result.section_paragraph_manifest is not None
    assert result.section_paragraph_manifest.members
    assert len(result.canonical_claim_coverage_proof_refs) == 1

    claim_runtime = engine.section_execution_graph.claim_verification_runtime
    assert claim_runtime is not None
    coverage = claim_runtime.resolve_claim_coverage(
        result.canonical_claim_coverage_proof_refs[0]
    )
    assert coverage.accounting_state.value == "INDETERMINATE"
    assert all(not unit.claim_occurrence_refs for unit in coverage.coverage_units)

    trace_path = Path(summary["paths"]["trace"])
    events = [
        __import__("json").loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert not any(
        event.get("component_name") == "VerifierAgent" for event in events
    )
    writer_starts = [
        event
        for event in events
        if event.get("event_type") == "model_started"
        and (event.get("input_summary") or {}).get("call_purpose")
        == "scheme_section_generation"
    ]
    assert writer_starts
    input_summary = writer_starts[0]["input_summary"]
    assert input_summary["prompt_chars"] == 0
    assert input_summary["message_count"] > 0
    assert input_summary["llm_context"]["managed"] is True

    top_level = _latest_w13_top_level_payload(engine)
    assert len(top_level.section_execution_results) == 1
    projected = top_level.section_execution_results[0]
    assert projected.writer_execution_ref == result.writer_execution_ref
    assert (
        projected.writer_generation_invocation_ref
        == result.writer_generation_invocation_ref
    )
    assert projected.section_paragraph_manifest == result.section_paragraph_manifest
    assert (
        projected.canonical_claim_coverage_proof_refs
        == result.canonical_claim_coverage_proof_refs
    )

    composition = engine.stage_service.target_post_write_composition
    assert composition is not None
    assert top_level.target_document_artifact_ref is not None
    assert top_level.target_post_write_control_decision_ref is not None
    assert top_level.target_run_completion_assessment_ref is not None
    assert top_level.target_release_publication is not None
    assert top_level.release_publication is None
    assert top_level.artifact_route == "deliverable"
    assert top_level.output_status is ExecutionStatus.PARTIAL_SUCCESS
    assert top_level.hard_gate is not None
    assert top_level.hard_gate.metadata["authority"] == (
        "compatibility_projection_only"
    )
    assert "decide_document_version" not in top_level.executed_nodes
    assert "build_generation_report" not in top_level.executed_nodes
    assert "human_review_interrupt" not in top_level.executed_nodes
    assert "apply_human_review" not in top_level.executed_nodes
    budget_state = engine.budget_runtime(
        task_id,
        "w13_single_section_live_cutover",
    ).canonical_state()
    assert budget_state.used.logical_model_calls >= 1
    assert budget_state.used.model_tokens_total > 0
    assert top_level.document_validation.metadata["budget_authority"] == (
        "canonical_sif12"
    )
    assert top_level.document_validation.metadata[
        "legacy_task_draining_budget_active"
    ] is False

    final_control = composition.control_store.resolve_exact(
        top_level.target_post_write_control_decision_ref
    )
    assert final_control.quality_state_passthrough.value == "UNRESOLVED"
    assert final_control.enforcement_action.value == "OUTPUT_BEST_EFFORT"
    final_gate = composition.gate_store.resolve_exact(
        final_control.delivery_gate_decision_ref
    )
    completion = composition.validation_store.resolve_completion(
        final_gate.gate_input_manifest.validation_completion_ref
    )
    completion_states = {
        item.expected_validation_item_ref.split(":", 1)[0]: item.state.value
        for item in completion.items
        if ":item:" in item.expected_validation_item_ref
    }
    shared_items = [item for item in completion.items
                    if item.expected_validation_item_ref.startswith("faithfulness:shared:")]
    assert len(shared_items) == 1
    assert shared_items[0].state.value == "INDETERMINATE"
    assert shared_items[0].fact_refs
    assert completion_states == {
        "faithfulness": "INDETERMINATE",
        "citation": "INDETERMINATE",
        "relevancy": "INDETERMINATE",
        "coverage": "PASS",
        "consistency": "PASS",
    }
    fth03_item = next(
        item for item in completion.items
        if item.expected_validation_item_ref.startswith("faithfulness:item:")
    )
    assert fth03_item.indeterminate_proof_refs and not fth03_item.fact_refs
    consistency_item = next(
        item for item in completion.items
        if item.expected_validation_item_ref.startswith("consistency:item:")
    )
    assert not consistency_item.fact_refs
    proof_payloads = tuple(
        composition.validation_store.resolve_intermediate(proof_ref)
        for proof_ref in consistency_item.positive_proof_refs
    )
    assert sorted(payload["schema_version"] for payload in proof_payloads) == sorted((
        "consistency_discovery_plan_v1",
        "consistency_discovery_plan_v1",
        "consistency_candidate_discovery_result_v1",
        "consistency_candidate_discovery_result_v1",
        "consistency_plan_conformance_result_v1",
        "consistency_plan_conformance_result_v1",
        "consistency_procedure_lowering_v1",
    ))
    consistency_record = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=consistency_item.positive_proof_refs[0].validation_run_id,
        validator_id="post_write_consistency",
        expected_item_ref=consistency_item.expected_validation_item_ref,
    )
    assert consistency_record is not None
    assert consistency_record.expected_item_accounting[0].positive_proof_refs == consistency_item.positive_proof_refs
    for proof_ref in consistency_item.positive_proof_refs:
        assert composition.validation_store.resolve_intermediate(proof_ref)
    consistency_condition = next(
        result for result in final_gate.quality_policy_projection.condition_results
        if result.dimension_id == "consistency"
    )
    assert consistency_condition.state.value == "SATISFIED"
    assert consistency_condition.positive_proof_refs == consistency_item.positive_proof_refs
    final_facts = composition.validation_store.list_facts_for_run(
        final_gate.validation_run_id
    )
    assert len(final_facts) == 7
    grounding_facts=[fact for fact in final_facts if fact.family.value=="CITATION_GROUNDING"]
    assert len(grounding_facts)==1
    assert grounding_facts[0].semantic_state.value=="INDETERMINATE"
    assert grounding_facts[0].payload["uncertainty_kind"]=="ASSOCIATION_INDETERMINATE"
    citation_items = [item for item in completion.items
                      if item.expected_validation_item_ref.startswith("citation:integrity:")]
    assert len(citation_items) == 1 and citation_items[0].required
    assert citation_items[0].state.value == "PASS"
    citation_facts = [fact for fact in final_facts if fact.family.value == "CITATION_INTEGRITY"]
    assert len(citation_facts) == 3
    assert {composition.validation_store.resolve_fact(ref).fact_id for ref in citation_items[0].fact_refs} == {fact.fact_id for fact in citation_facts}
    rules = {fact.payload["rule_id"]: fact for fact in citation_facts}
    assert set(rules) == {"PW-R-CIT-I-01", "PW-R-CIT-I-02", "PW-R-CIT-I-03"}
    assert all(fact.semantic_state.value == "PASS" and fact.subject_hash == completion.subject_hash
               and fact.validation_run_id == final_gate.validation_run_id for fact in citation_facts)
    execution = composition.validation_store.find_validator_execution_for_item(
        validation_run_id=final_gate.validation_run_id, validator_id=citation_facts[0].producer_validator_id,
        expected_item_ref=citation_items[0].expected_validation_item_ref)
    assert execution is not None and len(execution.intermediate_refs) == 1
    snapshot = composition.validation_store.resolve_intermediate(execution.intermediate_refs[0])
    assert snapshot["validation_subject_hash"] == completion.subject_hash
    assert snapshot["catalog"]["scope"] == "DOCUMENT"
    assert len(snapshot["occurrences"]) == 1
    occurrence = snapshot["occurrences"][0]["target"]
    assert occurrence["artifact_state_ref"]["artifact_hash"] == top_level.target_document_artifact_ref.artifact_hash
    assert occurrence["validation_subject_hash"] == completion.subject_hash
    assert {fact.target_ref for fact in citation_facts} == {occurrence["occurrence_ref"]}
    assert {fact.payload["alias_catalog_ref"] for fact in citation_facts} == {
        "citation-alias-catalog://sha256/" + snapshot["catalog"]["catalog_hash"]}
    assert rules["PW-R-CIT-I-01"].payload["citation_target_ref"] is None
    target = rules["PW-R-CIT-I-02"].payload["citation_target_ref"]
    assert target == rules["PW-R-CIT-I-03"].payload["citation_target_ref"]
    entry = next(entry for entry in snapshot["catalog"]["entries"] if entry["alias"] == occurrence["citation_alias"])
    assert target["exact_evidence_ref"] == entry["exact_evidence_ref"]
    assert entry["origin_refs"]
    assert all(origin["evidence_snapshot_ref"]["writer_invocation_ref"] ==
               top_level.section_execution_results[0].writer_generation_invocation_ref.model_dump(mode="json")
               for origin in entry["origin_refs"])
    relevancy_facts = [fact for fact in final_facts if fact.family.value == "REQUIREMENT_ADHERENCE"]
    assert len(relevancy_facts) == 1
    assert relevancy_facts[0].semantic_state.value == "INDETERMINATE"
    assert relevancy_facts[0].payload["requirement_id"] == "task-request"
    assert relevancy_facts[0].payload["adherence_observation"] == "INDETERMINATE"
    coverage_facts = [fact for fact in final_facts if fact.family.value == "COVERAGE_COMPLETENESS"]
    assert len(coverage_facts) == 1
    assert coverage_facts[0].semantic_state.value == "PASS"
    shared_facts = [fact for fact in final_facts if fact.family.value == "FAITHFULNESS"]
    assert len(shared_facts) == 1
    assert shared_facts[0].semantic_state.value == "INDETERMINATE"
    assert shared_facts[0].payload["checked_scope"] == "exact_current_paragraph_body"
    assert final_gate.overall_quality_state.value == "UNRESOLVED"
    assert top_level.target_document_artifact_ref.artifact_version == 2
    final_record = composition.artifact_store.resolve_exact(
        top_level.target_document_artifact_ref
    )
    assert len(final_record.validated_output_bindings) == 1
    assert len(final_record.output_conformance_proofs) == 1
    assert final_record.output_conformance_proofs[0].state.value == "CONFORMING"

    assessment = composition.assessment_store.resolve_exact(
        top_level.target_run_completion_assessment_ref
    )
    assert assessment.aggregate_result.value == "SUCCESS"
    terminal = engine.lifecycle_service.load_run_lifecycle(
        "w13_single_section_live_cutover"
    )
    assert terminal.status.value == "success"
    assert terminal.run_completion_assessment_ref == (
        top_level.target_run_completion_assessment_ref
    )
    assert top_level.target_release_publication.manifest.release_mode.value == (
        "BEST_EFFORT"
    )
    assert top_level.target_release_publication.reuse_state.value == "NEW_COMMIT"

    completed_replay = composition.complete_target_post_write(
        task_id=task_id,
        run_id="w13_single_section_live_cutover",
        document_id=top_level.document_id,
        project_input=top_level.project_input,
        section_execution_results=tuple(top_level.section_execution_results),
    )
    assert completed_replay.run_completion_assessment_ref == (
        top_level.target_run_completion_assessment_ref
    )
    assert completed_replay.terminal_run == terminal
    assert completed_replay.published_release.manifest == (
        top_level.target_release_publication.manifest
    )
    assert completed_replay.published_release.reuse_state.value == (
        "EXACT_REQUEST_REPLAY"
    )


def _w15_parallel_budget_policy_file(root: Path) -> Path:
    """Explicit test-only headroom; leave production policy and all other limits intact."""
    source = Path(__file__).resolve().parents[1] / "apps/enterprise_document/config/grounded_generation_v1.yaml"
    policy = GroundedGenerationPolicyLoader().load(source).model_dump(mode="json")
    policy["canonical_budget"] = {
        **policy["canonical_budget"],
        "model_tokens_total": 64000,
        "policy_ref": "budget-policy://multi-agent-v3/test-w15-parallel-headroom-v1",
    }
    root.mkdir(parents=True, exist_ok=True)
    target = root / "w15-parallel-budget-policy.json"
    target.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def test_w13_two_section_parallel_target_synchronizes_one_completion_and_release(
    tmp_path,
) -> None:
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
        "SECTION_MAX_CONCURRENCY": "2",
        "GROUNDED_GENERATION_POLICY_FILE": str(_w15_parallel_budget_policy_file(tmp_path)),
    }
    factory = _W13InspectingFactory(
        _W13TwoSectionNormalization(),
        force_parallel=True,
    )
    task_id = "task_w13_parallel_cutover"
    run_id = "w13_parallel_cutover"
    with patch.dict(os.environ, environment):
        MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input=_w13_two_section_project_input(task_id).user_query,
            run_id=run_id,
            task_id=task_id,
            output_root=tmp_path,
            clean_existing=True,
            settings=settings,
            project_input=_w13_current_new_task_project_input(_w13_two_section_project_input(task_id)).model_dump(
                mode="json"
            ),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )

    engine = factory.engines[0]
    top_level = _latest_w13_top_level_payload(engine)
    assert len(top_level.section_execution_results) == 2
    assert len(factory.parallel_spies) == 1
    assert len(factory.parallel_spies[0].batches) == 1
    assert len(factory.parallel_spies[0].batches[0]) == 2
    assert all(
        item.can_run_in_parallel and not item.requires_global_context
        for item in top_level.document_plan.sections
    )

    section_ids = {
        item.section.section_id for item in top_level.section_execution_results
    }
    attempt_ids = {
        item.active_execution_lineage.attempt_id
        for item in top_level.section_execution_results
    }
    assert len(section_ids) == 2
    assert len(attempt_ids) == 2
    assert all(
        item.writer_execution_ref is not None
        and item.writer_generation_invocation_ref is not None
        and item.section_paragraph_manifest is not None
        and item.canonical_claim_coverage_proof_refs
        for item in top_level.section_execution_results
    )

    composition = engine.stage_service.target_post_write_composition
    assert composition is not None
    assessment = composition.assessment_store.resolve_exact(
        top_level.target_run_completion_assessment_ref
    )
    assert len(assessment.section_completion_refs) == 2
    assert {
        item.source_scope_ref for item in assessment.section_completion_refs
    } == {f"section:{section_id}" for section_id in section_ids}
    assert top_level.target_release_publication is not None
    assert top_level.target_release_publication.manifest.run_id == run_id
    assert top_level.target_release_publication.manifest.release_mode.value == (
        "BEST_EFFORT"
    )
    terminal = engine.lifecycle_service.load_run_lifecycle(run_id)
    assert terminal.status.value == "success"
    assert terminal.run_completion_assessment_ref == (
        top_level.target_run_completion_assessment_ref
    )

    budget = engine.budget_runtime(task_id, run_id).canonical_state()
    assert budget.limits.model_tokens_total == 64000
    assert budget.policy_ref == "budget-policy://multi-agent-v3/test-w15-parallel-headroom-v1"
    assert budget.used.logical_model_calls >= 2
    assert budget.used.model_tokens_total > 0
    assert top_level.document_validation.metadata["budget_authority"] == (
        "canonical_sif12"
    )


def test_w13_reopen_from_stale_checkpoint_reuses_canonical_release_without_budget_reconsume(
    tmp_path,
) -> None:
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
    factory = _W13InspectingFactory()
    task_id = "task_w13_stale_checkpoint_reopen"
    run_id = "w13_stale_checkpoint_reopen"
    with patch.dict(os.environ, environment):
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2],
            user_input="生成 Wave13 单章节验证文档",
            run_id=run_id,
            task_id=task_id,
            output_root=tmp_path,
            clean_existing=True,
            settings=settings,
            project_input=_w13_current_new_task_project_input(_w13_single_section_project_input(task_id)).model_dump(
                mode="json"
            ),
            allow_demo_defaults=False,
            enable_agent_self_rag=False,
        )

    engine = factory.engines[0]
    stale = _w13_top_level_payload_after_node(
        engine,
        "final_artifact_review",
    )
    final = _latest_w13_top_level_payload(engine)
    assert stale.target_release_publication is None
    assert stale.target_run_completion_assessment_ref is None
    assert final.target_release_publication is not None
    assert final.target_run_completion_assessment_ref is not None

    budget_manager = engine.budget_runtime(task_id, run_id)
    budget_before = budget_manager.canonical_state()
    lifecycle_before = engine.lifecycle_service.path.read_text(encoding="utf-8")
    trace_path = Path(summary["paths"]["trace"])
    trace_before = trace_path.read_text(encoding="utf-8")

    runs_dir = Path(engine.checkpointer.path).parent
    coordinator = engine.stage_service.section_execution_coordinator
    execution_store = coordinator._pre_write_services.writer_invocation.execution_store
    reopened_lifecycle = RunAttemptLifecycleService(engine.lifecycle_service.path)
    reopened = TargetPostWriteComposition.create(
        runs_dir=runs_dir,
        research_runtime=engine.research_runtime,
        writer_execution_store=execution_store,
        budget_manager_provider=engine.budget_runtime,
        lifecycle_service=reopened_lifecycle,
    )
    from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import TargetPostWriteClaimProjection
    original_post_write = engine.stage_service.target_post_write_composition
    if original_post_write.requirement_adherence is not None:
        reopened.attach_requirement_adherence(
            model_gateway=original_post_write.requirement_adherence.gateway
        )
    if original_post_write.coverage_composition is not None:
        reopened.attach_coverage(
            model_gateway=original_post_write.coverage_composition.gateway
        )
    if original_post_write.consistency_composition is not None:
        reopened.attach_consistency(
            model_gateway=original_post_write.consistency_composition.gateway
        )
    reopened.attach_claim_quality_projection(TargetPostWriteClaimProjection(
        claim_runtime=coordinator.claim_verification_runtime,
        verifier_composition=coordinator.claim_verifier_composition,
    ))
    replay = reopened.complete_target_post_write(
        task_id=task_id,
        run_id=run_id,
        document_id=stale.document_id,
        project_input=stale.project_input,
        section_execution_results=tuple(stale.section_execution_results),
    )

    assert replay.run_completion_assessment_ref == (
        final.target_run_completion_assessment_ref
    )
    assert replay.published_release.manifest == (
        final.target_release_publication.manifest
    )
    assert replay.published_release.reuse_state.value == "EXACT_REQUEST_REPLAY"
    assert reopened_lifecycle.load_run_lifecycle(run_id) == replay.terminal_run
    assert budget_manager.canonical_state() == budget_before
    assert engine.lifecycle_service.path.read_text(encoding="utf-8") == lifecycle_before
    assert trace_path.read_text(encoding="utf-8") == trace_before
