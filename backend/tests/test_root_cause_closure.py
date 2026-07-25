from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.runtime.shared_state_schema import SharedStateSchema
from agent.runtime.state_access import SharedStateWriter
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    DeliverableFinalResultSchema,
    DocumentAssemblyRequestSchema,
    HardGateResultSchema,
    RejectedDraftArtifactSchema,
    SchemeDraftSchema,
    SchemeSectionSchema,
    SchemeWriterOutputSchema,
    SectionEvidenceBundleSchema,
)
from apps.enterprise_document.services.scheme_writer.document_assembler import (
    DocumentAssembler,
)
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from application.project_input_factory import ProjectInputFactory
from core.runtime.execution_control import (
    WorkflowExecutionControl,
    WorkflowExecutionCancelled,
    activate_execution_control,
)
from core.runtime.side_effects import (
    JsonlSideEffectCommitStore,
    SideEffectClaimStatus,
)
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from data_capture.lifecycle import (
    DataCaptureLifecycleStore,
    DataCaptureTransitionService,
)
from data_capture.migrations import migrate_data_capture_record_v1
from eval.rag.engine.eval_runner import (
    load_eval_samples as load_canonical_eval_samples,
    load_legacy_eval_samples_v1,
)
from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory
from rag.runtime.retrieval_runtime import RetrievalRuntimeConfig
from rag.data_capture.rag_run_capture import RagRunCapture
from schemas.agent import AgentResultSchema
from schemas.context import ContextBundleSchema
from schemas.data_capture import DataCaptureRecordSchema
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from task.task_manager import JsonlTaskManager


NOW = "2026-07-24T00:00:00+00:00"


def _project_input_payload() -> dict:
    return {
        "task_id": "task_root",
        "task_type": "scheme_generation",
        "user_query": "生成方案",
        "generation_requirements": {
            "required_sections": ["章节A"],
            "need_citation": False,
        },
        "output_schema": {"required_sections": ["章节A"]},
    }


@pytest.mark.parametrize(
    "invalid",
    [
        [["task_id", "task_root"]],
        {**_project_input_payload(), "unknown_typo": True},
        {
            key: value
            for key, value in _project_input_payload().items()
            if key != "task_type"
        },
        {
            **_project_input_payload(),
            "generation_requirements": {
                "required_sections": "章节A",
                "need_citation": False,
            },
        },
        {**_project_input_payload(), "generation_requirements": None},
        {
            **_project_input_payload(),
            "generation_requirements": {
                "required_sections": [],
                "need_citation": False,
            },
            "output_schema": {"required_sections": []},
        },
    ],
)
def test_canonical_project_input_rejects_same_adversarial_inputs_everywhere(
    invalid,
) -> None:
    with pytest.raises(ValidationError):
        ProjectInputSchema.model_validate(invalid, strict=True)
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            "task_root",
            "生成方案",
            invalid,
            allow_demo_defaults=False,
        )


def test_legal_project_input_matches_direct_and_production_validation() -> None:
    payload = _project_input_payload()
    direct = ProjectInputSchema.model_validate(payload, strict=True)
    production = ProjectInputFactory().build(
        "task_root",
        "生成方案",
        payload,
    )
    assert production.model_dump() == direct.model_dump()


def test_demo_ingress_is_explicit_and_still_strict() -> None:
    result = ProjectInputFactory().build(
        "task_demo",
        "生成方案",
        {"project_name": "演示项目"},
        allow_demo_defaults=True,
    )
    assert result.task_id == "task_demo"
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            "task_demo",
            "生成方案",
            {"project_name": 123},
            allow_demo_defaults=True,
        )


def test_project_input_factory_defaults_to_canonical_fail_closed() -> None:
    with pytest.raises(ValidationError):
        ProjectInputFactory().build(
            "task_default_boundary",
            "生成方案",
            {"project_name": "不能被隐式补全的生产输入"},
        )


def test_eval_sample_migration_is_explicit_not_hidden_in_canonical_loader(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "legacy-eval.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "query": "什么是混合检索？",
                    "expected_keywords": ["向量检索", "关键词检索"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_canonical_eval_samples(dataset)

    migrated = load_legacy_eval_samples_v1(dataset)
    assert migrated[0].sample_id == "legacy_eval_00001"
    assert migrated[0].query == "什么是混合检索？"


def test_runtime_capture_cannot_claim_lifecycle_readiness(tmp_path: Path) -> None:
    recorder = JsonlDataCaptureRecorder(tmp_path)
    with pytest.raises(ValueError, match="cannot create promoted"):
        recorder.record(
            capture_type="sft_candidates",
            capture_stage="training_ready",
            task_id="task",
            run_id="run",
        )
    with pytest.raises(TypeError):
        recorder.record(
            capture_type="sft_candidates",
            capture_stage="candidate",
            task_id="task",
            run_id="run",
            is_usable_for_training=True,
        )


def test_active_index_manifest_is_fail_closed_and_legacy_is_explicit(
    tmp_path: Path,
) -> None:
    factory = ParentChildRuntimeFactory()
    missing_pointer = tmp_path / "missing-active-index.json"
    with pytest.raises(FileNotFoundError):
        factory.resolve_config(
            RetrievalRuntimeConfig(
                index_source_mode="manifest",
                active_index_pointer=str(missing_pointer),
            ),
            tmp_path,
        )

    legacy = factory.resolve_config(
        RetrievalRuntimeConfig(
            index_source_mode="legacy",
            active_index_pointer=str(missing_pointer),
        ),
        tmp_path,
    )
    assert legacy.index_lineage["status"] == "legacy_paths"


def test_partial_side_effect_commit_retries_without_duplicate_after_reload(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "side-effects.jsonl"
    observed: list[str] = []
    attempts = {"second": 0}

    def first() -> None:
        observed.append("first")

    def flaky_second() -> None:
        attempts["second"] += 1
        if attempts["second"] == 1:
            raise RuntimeError("injected failure")
        observed.append("second")

    first_control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-1",
        timeout_seconds=30,
        commit_scope_id="run:step:input",
        side_effect_store=JsonlSideEffectCommitStore(ledger_path),
    )
    first_control.defer_external_commit(
        first,
        description="first",
        side_effect_id="effect-first",
    )
    first_control.defer_external_commit(
        flaky_second,
        description="second",
        side_effect_id="effect-second",
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        first_control.finalize_external_commits(attempt_succeeded=True)

    retry_control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-2",
        timeout_seconds=30,
        commit_scope_id="run:step:input",
        side_effect_store=JsonlSideEffectCommitStore(ledger_path),
    )
    retry_control.defer_external_commit(
        first,
        description="first",
        side_effect_id="effect-first",
    )
    retry_control.defer_external_commit(
        flaky_second,
        description="second",
        side_effect_id="effect-second",
    )
    result = retry_control.finalize_external_commits(attempt_succeeded=True)

    assert observed == ["first", "second"]
    assert result == {
        "committed": 1,
        "already_committed": 1,
        "already_in_progress": 0,
        "discarded": 0,
    }


def test_external_side_effect_basic_and_duplicate_id_semantics(
    tmp_path: Path,
) -> None:
    ledger = JsonlSideEffectCommitStore(tmp_path / "side-effects.jsonl")
    observed: list[str] = []
    control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt",
        timeout_seconds=30,
        side_effect_store=ledger,
    )
    control.defer_external_commit(
        lambda: observed.append("once"),
        description="one",
        side_effect_id="same-logical-effect",
    )
    control.defer_external_commit(
        lambda: observed.append("duplicate"),
        description="duplicate",
        side_effect_id="same-logical-effect",
    )
    result = control.finalize_external_commits(attempt_succeeded=True)
    assert observed == ["once"]
    assert result == {
        "committed": 1,
        "already_committed": 1,
        "already_in_progress": 0,
        "discarded": 0,
    }

    failing = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-failing",
        timeout_seconds=30,
        side_effect_store=ledger,
    )
    failing.defer_external_commit(
        lambda: (_ for _ in ()).throw(RuntimeError("first callback failed")),
        description="failure",
        side_effect_id="effect-failure",
    )
    failing.defer_external_commit(
        lambda: observed.append("must-not-run"),
        description="after-failure",
        side_effect_id="effect-after-failure",
    )
    with pytest.raises(RuntimeError, match="first callback failed"):
        failing.finalize_external_commits(attempt_succeeded=True)
    assert observed == ["once"]
    retry_failed = ledger.claim(
        "effect-failure",
        execution_id="retry-failure",
        description="retry",
    )
    assert retry_failed.status == SideEffectClaimStatus.ACQUIRED
    assert ledger.release(retry_failed, reason="test cleanup") is True
    never_started = ledger.claim(
        "effect-after-failure",
        execution_id="retry-never-started",
        description="retry",
    )
    assert never_started.status == SideEffectClaimStatus.ACQUIRED
    assert ledger.release(never_started, reason="test cleanup") is True

    all_success: list[str] = []
    success_control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-all-success",
        timeout_seconds=30,
        side_effect_store=ledger,
    )
    success_control.defer_external_commit(
        lambda: all_success.append("first"),
        description="success-first",
        side_effect_id="effect-success-first",
    )
    success_control.defer_external_commit(
        lambda: all_success.append("second"),
        description="success-second",
        side_effect_id="effect-success-second",
    )
    assert success_control.finalize_external_commits(
        attempt_succeeded=True
    ) == {
        "committed": 2,
        "already_committed": 0,
        "already_in_progress": 0,
        "discarded": 0,
    }
    assert all_success == ["first", "second"]


def test_two_side_effect_store_instances_reconcile_same_id(tmp_path: Path) -> None:
    path = tmp_path / "side-effects.jsonl"
    store_a = JsonlSideEffectCommitStore(path)
    store_b = JsonlSideEffectCommitStore(path)
    claim_a = store_a.claim(
        "shared-effect",
        execution_id="attempt-a",
        description="a",
    )
    claim_b = store_b.claim(
        "shared-effect",
        execution_id="attempt-b",
        description="b",
    )
    assert claim_a.status == SideEffectClaimStatus.ACQUIRED
    assert claim_b.status == SideEffectClaimStatus.ALREADY_IN_PROGRESS
    assert store_a.mark_committed(claim_a) is True
    assert (
        store_b.claim(
            "shared-effect",
            execution_id="attempt-b-retry",
            description="b-retry",
        ).status
        == SideEffectClaimStatus.ALREADY_COMMITTED
    )


def test_rag_run_capture_is_attempt_telemetry_not_training_truth(
    tmp_path: Path,
) -> None:
    capture_path = tmp_path / "rag-runs.jsonl"
    capture = RagRunCapture(capture_path)
    capture.capture({"run_id": "standalone"})
    standalone = json.loads(capture_path.read_text(encoding="utf-8").splitlines()[0])
    assert standalone["capture_semantics"] == "immutable_raw_attempt_telemetry_v1"
    assert standalone["training_eligible"] is False
    assert standalone["eval_eligible"] is False
    assert standalone["parent_workflow_outcome"] == "not_joined"

    control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-rag",
        timeout_seconds=30,
        commit_scope_id="run:rag",
    )
    with activate_execution_control(control):
        deferred = capture.capture({"run_id": "joined"})
    assert deferred["deferred"] is True
    assert len(capture_path.read_text(encoding="utf-8").splitlines()) == 1
    control.finalize_external_commits(attempt_succeeded=True)
    joined = json.loads(capture_path.read_text(encoding="utf-8").splitlines()[1])
    assert joined["attempt"]["execution_id"] == "attempt-rag"
    assert joined["parent_workflow_outcome"] == "pending_join"
    assert joined["training_eligible"] is False
    assert joined["eval_eligible"] is False

    cancelled = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-cancelled",
        timeout_seconds=30,
        commit_scope_id="run:rag:cancelled",
    )
    with activate_execution_control(cancelled):
        capture.capture({"run_id": "cancelled"})
    cancelled.abort_external_commits("parent_timeout")
    with pytest.raises(WorkflowExecutionCancelled):
        cancelled.finalize_external_commits(attempt_succeeded=True)
    assert len(capture_path.read_text(encoding="utf-8").splitlines()) == 2


def _capture(record_id: str = "capture-root") -> DataCaptureRecordSchema:
    return DataCaptureRecordSchema(
        record_id=record_id,
        capture_type="eval_samples",
        capture_stage="candidate",
        task_id="task",
        run_id="run",
        created_at=NOW,
        generated_draft_output="draft",
    )


def test_legacy_capture_artifact_migration_never_infers_deliverable() -> None:
    legacy = _capture("legacy-capture").model_dump(mode="python")
    legacy["schema_version"] = "data_capture_record_v1"
    legacy["final_output"] = legacy.pop("generated_draft_output")
    migrated = migrate_data_capture_record_v1(legacy)
    canonical = DataCaptureRecordSchema.model_validate(migrated, strict=True)
    assert canonical.generated_draft_output == "draft"
    assert canonical.deliverable_output is None
    assert canonical.metadata["legacy_artifact_migration"][
        "deliverable_not_inferred"
    ] is True


def test_capture_event_is_truth_when_projection_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "capture"
    store = DataCaptureLifecycleStore(root)
    store.initialize(_capture())
    monkeypatch.setattr(
        store,
        "_write_current_atomic",
        lambda record: (_ for _ in ()).throw(OSError("projection failure")),
    )
    updated = store.transition(
        "capture-root",
        "evaluated",
        transition_service=DataCaptureTransitionService(),
        reason="evaluated",
        authority="test",
        expected_revision=0,
    )
    assert updated.lifecycle_revision == 1
    duplicate = store.transition(
        "capture-root",
        "evaluated",
        transition_service=DataCaptureTransitionService(),
        reason="duplicate retry",
        authority="test",
        expected_revision=0,
    )
    assert duplicate.lifecycle_revision == 1

    replayed = DataCaptureLifecycleStore(root).load_current("capture-root")
    assert replayed.capture_stage == "evaluated"
    assert replayed.lifecycle_revision == 1


def test_capture_append_failure_never_advances_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "capture"
    store = DataCaptureLifecycleStore(root)
    store.initialize(_capture())
    monkeypatch.setattr(
        store,
        "_append_event",
        lambda event: (_ for _ in ()).throw(OSError("append failure")),
    )
    with pytest.raises(OSError, match="append failure"):
        store.transition(
            "capture-root",
            "evaluated",
            transition_service=DataCaptureTransitionService(),
            reason="evaluated",
            authority="test",
        )
    assert DataCaptureLifecycleStore(root).load_current(
        "capture-root"
    ).capture_stage == "candidate"


def test_two_capture_store_instances_observe_revision_cas(tmp_path: Path) -> None:
    root = tmp_path / "capture"
    store_a = DataCaptureLifecycleStore(root)
    store_b = DataCaptureLifecycleStore(root)
    store_a.initialize(_capture())
    store_b.transition(
        "capture-root",
        "evaluated",
        transition_service=DataCaptureTransitionService(),
        reason="evaluation",
        authority="writer-b",
        expected_revision=0,
    )
    with pytest.raises(ValueError, match="revision conflict"):
        store_a.transition(
            "capture-root",
            "accepted",
            transition_service=DataCaptureTransitionService(),
            reason="stale acceptance",
            authority="writer-a",
            policy_decision="accepted",
            expected_revision=0,
        )


def _task() -> TaskSchema:
    return TaskSchema(
        task_id="task-root",
        run_id="run-root",
        task_type="scheme_generation",
        user_input="生成方案",
        project_input={},
        created_at=NOW,
    )


def _success_result() -> AgentResultSchema:
    return AgentResultSchema(
        result_id="result-root",
        task_id="task-root",
        run_id="run-root",
        agent_name="SupervisorAgent",
        agent_type="supervisor",
        status=ExecutionStatus.SUCCESS,
        result_type="workflow_result",
    )


def test_task_terminal_state_replays_and_cannot_regress(tmp_path: Path) -> None:
    task = _task()
    manager = JsonlTaskManager(tmp_path)
    manager.create_task(task)
    manager.mark_running(task, expected_revision=0)
    terminal = manager.mark_success(task, _success_result(), expected_revision=1)
    assert terminal.status == ExecutionStatus.SUCCESS

    restarted = JsonlTaskManager(tmp_path)
    assert restarted.get_task(task.task_id).status == ExecutionStatus.SUCCESS
    assert restarted.create_task(task).revision == 2
    with pytest.raises(ValueError, match="illegal task transition"):
        restarted.mark_running(task)
    assert len(restarted.get_task(task.task_id).events) == 3


def test_task_revision_cas_rejects_stale_writer(tmp_path: Path) -> None:
    task = _task()
    manager = JsonlTaskManager(tmp_path)
    manager.create_task(task)
    manager.mark_running(task, expected_revision=0)
    with pytest.raises(ValueError, match="revision conflict"):
        manager.mark_success(task, _success_result(), expected_revision=0)


def test_two_task_manager_instances_cannot_commit_stale_revision(
    tmp_path: Path,
) -> None:
    task = _task()
    manager_a = JsonlTaskManager(tmp_path)
    manager_b = JsonlTaskManager(tmp_path)
    manager_a.create_task(task)
    manager_b.mark_running(task, expected_revision=0)
    with pytest.raises(ValueError, match="revision conflict"):
        manager_a.mark_success(task, _success_result(), expected_revision=0)
    assert manager_a.get_task(task.task_id).status == ExecutionStatus.RUNNING


def test_task_manager_replays_requested_aggregate_only(tmp_path: Path) -> None:
    (tmp_path / "historical_state.jsonl").write_text(
        '{"schema_version":"task_state_event_v2"}\n',
        encoding="utf-8",
    )
    manager = JsonlTaskManager(tmp_path)
    created = manager.create_task(_task())
    assert created.status == ExecutionStatus.PENDING
    with pytest.raises(ValueError, match="invalid task event"):
        manager.get_task("historical")


def _section(
    title: str,
    order: int,
    *,
    blocked: bool = False,
) -> SchemeSectionSchema:
    return SchemeSectionSchema(
        section_id=f"section-{order}",
        section_title=title,
        section_order=order,
        content=f"{title}内容",
        status=ExecutionStatus.SUCCESS,
        extra=(
            {
                "generation_blocked": True,
                "generation_block_reason": "evidence_insufficient",
            }
            if blocked
            else {}
        ),
    )


def _bundle(section: SchemeSectionSchema, status: str) -> SectionEvidenceBundleSchema:
    return SectionEvidenceBundleSchema(
        section_id=section.section_id,
        section_title=section.section_title,
        query=section.section_title,
        rag_context=RAGContextSchema(context_text=""),
        metadata={"evidence_assessment_status": status},
    )


def _assemble(
    sections: list[SchemeSectionSchema],
    statuses: list[str],
    *,
    citation_required_sections: list[str],
):
    return DocumentAssembler().assemble(
        DocumentAssemblyRequestSchema(
            task_id="task",
            run_id="run",
            document_id="document",
            document_title="方案",
            required_sections=[item.section_title for item in sections],
            sections=sections,
            section_evidence=[
                _bundle(section, status)
                for section, status in zip(sections, statuses)
            ],
            document_evidence_available=True,
            document_assessment_status="sufficient",
            citation_required_sections=citation_required_sections,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def test_not_assessed_required_section_cannot_hitchhike_on_sufficient_section() -> None:
    a = _section("章节A", 1)
    b = _section("章节B", 2)
    optional = _section("可选章节", 3)
    result = _assemble(
        [a, b, optional],
        ["sufficient", "not_assessed", "not_assessed"],
        citation_required_sections=["章节A", "章节B"],
    )
    assert result.semantic_evidence_sufficient is False
    assert result.evidence_requirements_satisfied is False
    assert [item.assessment_status for item in result.section_evidence_requirements] == [
        "sufficient",
        "not_assessed",
    ]


def test_explicitly_blocked_and_sufficient_sections_satisfy_universal_policy() -> None:
    blocked = _section("章节A", 1, blocked=True)
    sufficient = _section("章节B", 2)
    result = _assemble(
        [blocked, sufficient],
        ["insufficient", "sufficient"],
        citation_required_sections=["章节A", "章节B"],
    )
    assert result.semantic_evidence_sufficient is False
    assert result.evidence_requirements_satisfied is True
    assert [item.assessment_status for item in result.section_evidence_requirements] == [
        "blocked",
        "sufficient",
    ]


@pytest.mark.parametrize(
    (
        "statuses",
        "blocked_indexes",
        "citation_required_indexes",
        "expected_semantic",
        "expected_requirements",
    ),
    [
        (["sufficient", "sufficient"], set(), [0, 1], True, True),
        (["sufficient", "insufficient"], set(), [0, 1], False, False),
        (["sufficient", "not_assessed"], set(), [0, 1], False, False),
        (["not_assessed", "not_assessed"], set(), [0, 1], False, False),
        (["insufficient", "sufficient"], {0}, [0, 1], False, True),
        (["insufficient", "not_assessed"], {0}, [0, 1], False, False),
        (["sufficient", "not_assessed"], set(), [0], True, True),
        (["not_assessed", "sufficient"], set(), [0], False, False),
    ],
)
def test_evidence_aggregation_matrix(
    statuses: list[str],
    blocked_indexes: set[int],
    citation_required_indexes: list[int],
    expected_semantic: bool,
    expected_requirements: bool,
) -> None:
    sections = [
        _section(f"章节{index + 1}", index + 1, blocked=index in blocked_indexes)
        for index in range(len(statuses))
    ]
    result = _assemble(
        sections,
        statuses,
        citation_required_sections=[
            sections[index].section_title for index in citation_required_indexes
        ],
    )
    assert result.semantic_evidence_sufficient is expected_semantic
    assert result.evidence_requirements_satisfied is expected_requirements


def _draft() -> SchemeDraftSchema:
    section = _section("章节A", 1)
    return SchemeDraftSchema(
        draft_id="draft-root",
        document_id="document-root",
        task_id="task-root",
        run_id="run-root",
        title="方案",
        full_text="章节A内容",
        sections=[section],
        required_sections=["章节A"],
        created_at=NOW,
        updated_at=NOW,
    )


def test_artifact_lifecycle_type_prevents_failed_draft_becoming_deliverable() -> None:
    draft = _draft()
    failed_gate = HardGateResultSchema(
        passed=False,
        failures=["evidence insufficient"],
    )
    rejected = RejectedDraftArtifactSchema(
        draft=draft,
        rejection_code="DOCUMENT_HARD_GATE_FAILED",
        rejection_reasons=["evidence insufficient"],
        hard_gate=failed_gate,
    )
    output = SchemeWriterOutputSchema(
        task_id="task-root",
        run_id="run-root",
        status=ExecutionStatus.FAILED,
        generated_draft=draft,
        rejected_artifact=rejected,
        hard_gate=failed_gate,
    )
    assert output.deliverable_final_result is None

    with pytest.raises(ValidationError, match="failed scheme output"):
        SchemeWriterOutputSchema(
            task_id="task-root",
            run_id="run-root",
            status=ExecutionStatus.FAILED,
            generated_draft=draft,
            deliverable_final_result=DeliverableFinalResultSchema(draft=draft),
        )
    with pytest.raises(ValidationError):
        SchemeWriterOutputSchema(
            task_id="task-root",
            run_id="run-root",
            status=ExecutionStatus.FAILED,
            scheme_draft=draft,
        )

    passed_gate = HardGateResultSchema(passed=True)
    deliverable = DeliverableFinalResultSchema(draft=draft)
    successful = SchemeWriterOutputSchema(
        task_id="task-root",
        run_id="run-root",
        status=ExecutionStatus.SUCCESS,
        generated_draft=draft,
        deliverable_final_result=deliverable,
        hard_gate=passed_gate,
    )
    assert successful.deliverable_final_result is not None
    assert successful.rejected_artifact is None


def test_failed_workflow_commit_path_cannot_write_deliverable() -> None:
    workflow = build_scheme_generation_workflow(NOW)
    writer_step = workflow.steps[1]
    assert "generated_draft" in writer_step.failure_write_paths
    assert "rejected_artifact" in writer_step.failure_write_paths
    assert "deliverable_final_result" not in writer_step.failure_write_paths
    assert "final_result" not in writer_step.failure_write_paths

    state = SharedStateSchema(
        task_id="task",
        run_id="run",
        task_type="scheme_generation",
        user_input="生成方案",
        context_bundle=ContextBundleSchema(
            user={"user_query": "生成方案"},
            task={
                "task_id": "task",
                "run_id": "run",
                "task_type": "scheme_generation",
            },
        ),
        created_at=NOW,
    )
    SharedStateWriter().set_scheme_artifacts(
        state,
        generated_draft={"draft_id": "draft"},
        rejected_artifact={"artifact_type": "rejected_draft"},
        deliverable_final_result=None,
    )
    assert state.generated_draft is not None
    assert state.rejected_artifact is not None
    assert state.deliverable_final_result is None
    assert state.final_result is None
