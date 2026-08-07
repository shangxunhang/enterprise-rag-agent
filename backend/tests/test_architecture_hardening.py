"""Architecture closure tests for lifecycle, streaming and parallel isolation."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event, Lock
from unittest.mock import patch

import pytest

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.graph_state_ops import StateWriteContractViolation
from agent.runtime.official_checkpoint_saver import (
    SQLiteCheckpointResource,
    create_sqlite_checkpoint_resource,
)
from agent.supervisor_agent import SupervisorAgent
from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.quality.budget import WorkflowBudgetExceeded
from apps.enterprise_document.schemas.scheme_writer import (
    ParallelSectionExecutionEnvelopeSchema,
    ParallelSectionStateDeltaSchema,
    SchemeSectionSchema,
    SectionEvidenceBundleSchema,
    SectionExecutionResultSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.workflows.section_parallel_execution_graph import (
    ParallelSectionBudgetAllocator,
    SectionParallelExecutionGraph,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)
from core.config import get_settings
from core.retry_policy import classify_failure
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    WorkflowExecutionControl,
    activate_execution_control,
)
from schemas.common import ErrorSchema, WarningSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.failure import FailureCategory
from schemas.rag import RAGContextSchema
from schemas.status import ExecutionStatus
from model_gateway.usage_ledger import merge_model_usage_snapshots
from task.task_manager import JsonlTaskManager


_ORIGINAL_PREPARE = SchemeGenerationStageService.prepare_document_plan
_ORIGINAL_STREAM_EVENTS = EnterpriseDocumentWorkflowEngine.stream_events
_ENVIRONMENT = {
    "USE_REAL_RAG_TOOL": "false",
    "FAKE_RAG_SCENARIO": "default",
    "FAKE_LLM_SCENARIO": "default",
    "ENABLE_AGENT_SELF_RAG": "false",
    "ENABLE_SEMANTIC_GATE": "false",
    "SECTION_MAX_CONCURRENCY": "1",
}


def _settings(output_root: Path):
    return replace(
        get_settings(),
        data_root=output_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
    )


def _require_human_review(self, graph_state, payload):
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


def _parallel_human_review_plan(self, graph_state, payload):
    planned = _require_human_review(self, graph_state, payload)
    document_plan = planned.document_plan
    assert document_plan is not None
    first_two_ids = [
        item.section_id for item in document_plan.sections[:2]
    ]
    sections = [
        item.model_copy(
            update={
                "can_run_in_parallel": item.section_order <= 2,
                "requires_global_context": item.section_order > 2,
                "depends_on": (
                    [] if item.section_order <= 2 else first_two_ids
                ),
                "execution_group": "hardening_resume",
            }
        )
        for item in document_plan.sections
    ]
    return planned.model_copy(
        update={
            "document_plan": document_plan.model_copy(
                update={"sections": sections},
                deep=True,
            )
        },
        deep=True,
    )


def _stream_to_pause(output_root: Path, *, run_id: str):
    settings = _settings(output_root)
    with patch.dict(os.environ, _ENVIRONMENT), patch.object(
        SchemeGenerationStageService,
        "prepare_document_plan",
        _require_human_review,
    ):
        events = list(
            MainlineApplicationService().stream_run(
                project_root=Path(__file__).resolve().parents[2],
                user_input="Generate a durable enterprise RAG proposal",
                run_id=run_id,
                output_root=output_root,
                clean_existing=True,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )
    assert events[-1].event_type == "human_review_required"
    assert events[-1].payload["result"]["status"] == "paused"
    return events, settings


def _checkpoint_path(output_root: Path) -> Path:
    return output_root / "runs" / "langgraph_checkpoints.sqlite3"


def _review_snapshot(events) -> str:
    request = events[-1].payload["agent_result"]["human_review_request"]
    return request["metadata"]["input_snapshot_version"]


def _assert_immediate_rename(path: Path) -> None:
    moved = path.with_name(path.name + ".renamed")
    path.replace(moved)
    moved.replace(path)


def test_checkpoint_resource_close_is_idempotent_and_releases_windows_file(
    tmp_path,
) -> None:
    database = tmp_path / "resource.sqlite3"
    resource = create_sqlite_checkpoint_resource(database)
    resource.saver.get_tuple(
        {"configurable": {"thread_id": "lifecycle", "checkpoint_ns": ""}}
    )
    resource.close()
    resource.close()
    resource.close()
    assert resource.closed is True
    moved = database.with_suffix(".moved")
    database.replace(moved)
    moved.unlink()
    assert not moved.exists()


def test_repeated_cross_instance_inspect_closes_every_checkpoint_connection(
    tmp_path,
) -> None:
    initial_events, settings = _stream_to_pause(
        tmp_path,
        run_id="hardening_repeated_inspect",
    )
    task_id = initial_events[-1].task_id
    for _ in range(20):
        with patch.dict(os.environ, _ENVIRONMENT):
            inspection = MainlineApplicationService().inspect_run(
                project_root=Path(__file__).resolve().parents[2],
                task_id=task_id,
                run_id="hardening_repeated_inspect",
                output_root=tmp_path,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        assert inspection["interrupts"]
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_stream_resume_cross_instance_has_unique_ids_and_no_business_replay(
    tmp_path,
) -> None:
    initial_events, settings = _stream_to_pause(
        tmp_path,
        run_id="hardening_stream_resume",
    )
    _assert_immediate_rename(_checkpoint_path(tmp_path))
    with patch.dict(os.environ, _ENVIRONMENT):
        resumed_events = list(
            MainlineApplicationService().stream_resume_run(
                project_root=Path(__file__).resolve().parents[2],
                task_id=initial_events[-1].task_id,
                run_id="hardening_stream_resume",
                decision="approved",
                input_snapshot_version=_review_snapshot(initial_events),
                feedback="approved by architecture hardening",
                reviewer_id="reviewer-hardening",
                output_root=tmp_path,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )

    event_types = [event.event_type for event in resumed_events]
    assert event_types[:2] == ["workflow_resumed", "human_review_resumed"]
    assert event_types[-1] == "workflow_completed"
    replayed = {
        event.node_name
        for event in resumed_events
        if event.event_type == "node_started"
    } & {
        "normalize_project_input",
        "plan_document",
        "retrieve_document_evidence",
        "initialize_generation",
        "execute_sections",
        "assemble_document",
        "evaluate_document_gate",
    }
    assert replayed == set()
    assert {event.resume_count for event in initial_events} == {0}
    assert {event.resume_count for event in resumed_events} == {1}
    assert {event.stream_kind for event in initial_events} == {"initial"}
    assert {event.stream_kind for event in resumed_events} == {"resume"}
    assert [event.sequence for event in resumed_events] == list(
        range(1, len(resumed_events) + 1)
    )
    assert initial_events[0].stream_session_id != resumed_events[0].stream_session_id
    all_ids = [
        event.event_id for event in [*initial_events, *resumed_events]
    ]
    assert len(all_ids) == len(set(all_ids))
    assert resumed_events[-1].payload["result"]["task_state"]["status"] in {
        "success",
        "partial_success",
    }
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_parallel_sections_are_not_replayed_or_recounted_after_durable_resume(
    tmp_path,
) -> None:
    run_id = "hardening_parallel_durable_resume"
    settings = _settings(tmp_path)
    environment = {**_ENVIRONMENT, "SECTION_MAX_CONCURRENCY": "2"}
    original_execute = SectionExecutionGraph.execute
    calls = {"count": 0}
    calls_lock = Lock()

    def counted_execute(self, *args, **kwargs):
        with calls_lock:
            calls["count"] += 1
        return original_execute(self, *args, **kwargs)

    with patch.dict(os.environ, environment), patch.object(
        SchemeGenerationStageService,
        "prepare_document_plan",
        _parallel_human_review_plan,
    ), patch.object(
        SectionExecutionGraph,
        "execute",
        counted_execute,
    ):
        initial_events = list(
            MainlineApplicationService().stream_run(
                project_root=Path(__file__).resolve().parents[2],
                user_input="Generate a durable parallel RAG proposal",
                run_id=run_id,
                output_root=tmp_path,
                clean_existing=True,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )
        assert initial_events[-1].event_type == "human_review_required"
        calls_before_resume = calls["count"]
        usage_before_resume = initial_events[-1].payload["result"][
            "model_usage"
        ]
        resumed_events = list(
            MainlineApplicationService().stream_resume_run(
                project_root=Path(__file__).resolve().parents[2],
                task_id=initial_events[-1].task_id,
                run_id=run_id,
                decision="approved",
                input_snapshot_version=_review_snapshot(initial_events),
                output_root=tmp_path,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )

    terminal = resumed_events[-1]
    assert terminal.event_type == "workflow_completed"
    assert calls_before_resume > 0
    assert calls["count"] == calls_before_resume
    assert terminal.payload["result"]["model_usage"] == usage_before_resume
    shared_state = terminal.payload["agent_result"]["result"]["shared_state"]
    generated_ids = shared_state["context_bundle"]["generation"][
        "generated_section_ids"
    ]
    assert len(generated_ids) == len(set(generated_ids))
    citation_ids = [
        item["citation_id"]
        for item in shared_state["contexts"]["scheme_writer_output"][
            "citations"
        ]
    ]
    assert len(citation_ids) == len(set(citation_ids))
    _assert_immediate_rename(_checkpoint_path(tmp_path))


@pytest.mark.parametrize(
    ("continuation", "terminal_event", "task_status", "review_status"),
    [
        ("rejected", "workflow_failed", "failed", "rejected"),
        ("cancelled", "workflow_cancelled", "cancelled", "cancelled"),
    ],
)
def test_stream_reject_and_cancel_preserve_artifact_and_lifecycle(
    tmp_path,
    continuation,
    terminal_event,
    task_status,
    review_status,
) -> None:
    run_id = f"hardening_stream_{continuation}"
    initial_events, settings = _stream_to_pause(tmp_path, run_id=run_id)
    service = MainlineApplicationService()
    with patch.dict(os.environ, _ENVIRONMENT):
        if continuation == "cancelled":
            events = list(
                service.stream_cancel_run(
                    project_root=Path(__file__).resolve().parents[2],
                    task_id=initial_events[-1].task_id,
                    run_id=run_id,
                    reason="cancelled by architecture hardening",
                    output_root=tmp_path,
                    settings=settings,
                    allow_demo_defaults=True,
                    enable_agent_self_rag=False,
                )
            )
        else:
            events = list(
                service.stream_resume_run(
                    project_root=Path(__file__).resolve().parents[2],
                    task_id=initial_events[-1].task_id,
                    run_id=run_id,
                    decision="rejected",
                    input_snapshot_version=_review_snapshot(initial_events),
                    feedback="rejected by architecture hardening",
                    reviewer_id="reviewer-hardening",
                    output_root=tmp_path,
                    settings=settings,
                    allow_demo_defaults=True,
                    enable_agent_self_rag=False,
                )
            )
    terminal = events[-1]
    assert terminal.event_type == terminal_event
    assert terminal.stream_kind == (
        "cancel" if continuation == "cancelled" else "resume"
    )
    assert terminal.payload["result"]["task_state"]["status"] == task_status
    assert (
        terminal.payload["agent_result"]["human_review_status"]
        == review_status
    )
    assert terminal.payload["result"]["rejected_artifact"] is not None
    assert terminal.payload["result"]["deliverable_final_result"] is None
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_stream_continuation_exception_still_releases_checkpoint(tmp_path) -> None:
    initial_events, settings = _stream_to_pause(
        tmp_path,
        run_id="hardening_stream_exception",
    )

    def fail_before_stream(*args, **kwargs):
        raise RuntimeError("intentional continuation failure")

    with patch.dict(os.environ, _ENVIRONMENT), patch.object(
        SupervisorAgent,
        "stream_resume",
        fail_before_stream,
    ), pytest.raises(RuntimeError, match="intentional continuation failure"):
        list(
            MainlineApplicationService().stream_resume_run(
                project_root=Path(__file__).resolve().parents[2],
                task_id=initial_events[-1].task_id,
                run_id="hardening_stream_exception",
                decision="approved",
                input_snapshot_version=_review_snapshot(initial_events),
                output_root=tmp_path,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def _fail_after_first_engine_event(self, *args, **kwargs):
    iterator = iter(_ORIGINAL_STREAM_EVENTS(self, *args, **kwargs))
    try:
        yield next(iterator)
        raise RuntimeError("intentional engine stream failure")
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


def _trace_event_types(output_root: Path, run_id: str) -> list[str]:
    trace_path = output_root / "runs" / f"{run_id}_trace.jsonl"
    return [
        json.loads(line)["event_type"]
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_stream_engine_exception_after_mark_running_reconciles_to_pause(
    tmp_path,
) -> None:
    run_id = "hardening_engine_stream_failure"
    initial_events, settings = _stream_to_pause(tmp_path, run_id=run_id)
    task_id = initial_events[-1].task_id

    with patch.dict(os.environ, _ENVIRONMENT), patch.object(
        EnterpriseDocumentWorkflowEngine,
        "stream_events",
        _fail_after_first_engine_event,
    ), pytest.raises(RuntimeError, match="intentional engine stream failure"):
        list(
            MainlineApplicationService().stream_resume_run(
                project_root=Path(__file__).resolve().parents[2],
                task_id=task_id,
                run_id=run_id,
                decision="approved",
                input_snapshot_version=_review_snapshot(initial_events),
                output_root=tmp_path,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )

    task_state = JsonlTaskManager(tmp_path / "tasks").get_task(task_id)
    assert task_state.status == ExecutionStatus.PAUSED
    with patch.dict(os.environ, _ENVIRONMENT):
        inspection = MainlineApplicationService().inspect_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id=run_id,
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    assert inspection["interrupts"]
    event_types = _trace_event_types(tmp_path, run_id)
    assert "stream_interrupted" in event_types
    assert event_types.count("workflow_finished") >= 2
    assert event_types.count("run_finished") >= 2
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_invalid_terminal_payload_reconciles_durable_terminal_state(
    tmp_path,
) -> None:
    run_id = "hardening_invalid_terminal_payload"
    initial_events, settings = _stream_to_pause(tmp_path, run_id=run_id)
    task_id = initial_events[-1].task_id

    def invalid_terminal_payload(self, *args, **kwargs):
        for stream_event in _ORIGINAL_STREAM_EVENTS(self, *args, **kwargs):
            if stream_event.payload.get("result") is None:
                yield stream_event
                continue
            yield stream_event.model_copy(
                update={
                    "payload": {
                        **stream_event.payload,
                        "result": {"invalid": "workflow-engine-result"},
                    }
                }
            )

    with patch.dict(os.environ, _ENVIRONMENT), patch.object(
        EnterpriseDocumentWorkflowEngine,
        "stream_events",
        invalid_terminal_payload,
    ), pytest.raises(Exception) as captured:
        list(
            MainlineApplicationService().stream_resume_run(
                project_root=Path(__file__).resolve().parents[2],
                task_id=task_id,
                run_id=run_id,
                decision="approved",
                input_snapshot_version=_review_snapshot(initial_events),
                output_root=tmp_path,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )

    assert "WorkflowEngineResultSchema" in str(captured.value)
    task_state = JsonlTaskManager(tmp_path / "tasks").get_task(task_id)
    assert task_state.status in {
        ExecutionStatus.SUCCESS,
        ExecutionStatus.PARTIAL_SUCCESS,
    }
    event_types = _trace_event_types(tmp_path, run_id)
    assert "stream_interrupted" in event_types
    assert event_types[-2:] == ["workflow_finished", "run_finished"]
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_partial_stream_consumption_close_restores_durable_pause(
    tmp_path,
) -> None:
    run_id = "hardening_partial_stream_close"
    initial_events, settings = _stream_to_pause(tmp_path, run_id=run_id)
    task_id = initial_events[-1].task_id
    service = MainlineApplicationService()

    with patch.dict(os.environ, _ENVIRONMENT):
        stream = service.stream_resume_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id=run_id,
            decision="approved",
            input_snapshot_version=_review_snapshot(initial_events),
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
        first_event = next(stream)
        assert first_event.event_type == "workflow_resumed"
        stream.close()

    task_state = JsonlTaskManager(tmp_path / "tasks").get_task(task_id)
    assert task_state.status == ExecutionStatus.PAUSED
    with patch.dict(os.environ, _ENVIRONMENT):
        inspection = service.inspect_run(
            project_root=Path(__file__).resolve().parents[2],
            task_id=task_id,
            run_id=run_id,
            output_root=tmp_path,
            settings=settings,
            allow_demo_defaults=True,
            enable_agent_self_rag=False,
        )
    assert inspection["interrupts"]
    assert "stream_abandoned" in _trace_event_types(tmp_path, run_id)
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_initial_stream_exception_does_not_leave_task_running(tmp_path) -> None:
    run_id = "hardening_initial_stream_failure"
    task_id = f"task_{run_id}"
    settings = _settings(tmp_path)

    with patch.dict(os.environ, _ENVIRONMENT), patch.object(
        EnterpriseDocumentWorkflowEngine,
        "stream_events",
        _fail_after_first_engine_event,
    ), pytest.raises(RuntimeError, match="intentional engine stream failure"):
        list(
            MainlineApplicationService().stream_run(
                project_root=Path(__file__).resolve().parents[2],
                user_input="Generate an initial stream failure proposal",
                run_id=run_id,
                task_id=task_id,
                output_root=tmp_path,
                clean_existing=True,
                settings=settings,
                allow_demo_defaults=True,
                enable_agent_self_rag=False,
            )
        )

    task_state = JsonlTaskManager(tmp_path / "tasks").get_task(task_id)
    assert task_state.status != ExecutionStatus.RUNNING
    assert task_state.status in {
        ExecutionStatus.FAILED,
        ExecutionStatus.RETRYABLE_FAILED,
    }
    event_types = _trace_event_types(tmp_path, run_id)
    assert "stream_interrupted" in event_types
    assert event_types[-2:] == ["workflow_finished", "run_finished"]
    _assert_immediate_rename(_checkpoint_path(tmp_path))


def test_checkpoint_resource_close_failure_remains_retryable(tmp_path) -> None:
    class FlakyConnection:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise OSError("intentional close failure")

    connection = FlakyConnection()
    resource = SQLiteCheckpointResource(
        saver=object(),  # type: ignore[arg-type]
        connection=connection,  # type: ignore[arg-type]
        path=tmp_path / "flaky.sqlite3",
    )
    assert resource._finalizer.alive is True
    with pytest.raises(OSError, match="intentional close failure"):
        resource.close()
    assert resource.closed is False
    assert resource._finalizer.alive is True
    resource.close()
    assert connection.close_calls == 2
    assert resource.closed is True
    assert resource._finalizer.alive is False


def test_supervisor_resource_close_failure_is_observable_and_continues(
    caplog,
) -> None:
    class Resource:
        def __init__(self, *, fail: bool) -> None:
            self.fail = fail
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if self.fail:
                raise OSError("owned resource close failed")

    successful = Resource(fail=False)
    failing = Resource(fail=True)
    supervisor = object.__new__(SupervisorAgent)
    supervisor._owned_resources = (successful, failing)
    supervisor._resource_close_lock = Lock()
    supervisor._resources_closed = False
    supervisor._shutdown_diagnostics = []

    with caplog.at_level("WARNING"):
        supervisor._close_owned_resources()

    assert failing.close_calls == 1
    assert successful.close_calls == 1
    assert supervisor._resources_closed is False
    assert supervisor.shutdown_diagnostics == (
        {
            "event_type": "owned_resource_close_failed",
            "resource_type": "Resource",
            "error_type": "OSError",
            "error_message": "owned resource close failed",
        },
    )
    assert "owned resource close failed" in caplog.text


def _shared_state() -> GraphStateSchema:
    return GraphStateSchema(
        task_id="task-parallel-delta",
        run_id="run-parallel-delta",
        task_type="scheme_generation",
        user_input="parallel delta",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="parallel delta"),
            task=TaskContextSchema(
                task_id="task-parallel-delta",
                run_id="run-parallel-delta",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-07-26T00:00:00+00:00",
    )


def test_parallel_state_deltas_merge_additive_warnings_in_section_order() -> None:
    before = _shared_state()
    deltas = []
    for section_id in ("section-002", "section-001"):
        after = before.model_copy(deep=True)
        after.context_bundle.generation.generated_section_ids.append(section_id)
        after.generated_outputs[f"model-{section_id}"] = {"section": section_id}
        after.warnings.append(
            WarningSchema(
                warning_code=f"WARNING_{section_id}",
                message=section_id,
            )
        )
        deltas.append(
            (
                section_id,
                SchemeGenerationStageService._parallel_state_delta(
                    before,
                    after,
                    section_id=section_id,
                ),
            )
        )
    merged = before.model_copy(deep=True)
    for _, delta in sorted(deltas):
        SchemeGenerationStageService._apply_parallel_state_delta(merged, delta)
    assert [item.message for item in merged.warnings] == [
        "section-001",
        "section-002",
    ]
    assert merged.context_bundle.generation.generated_section_ids == [
        "section-001",
        "section-002",
    ]
    assert sorted(merged.generated_outputs) == [
        "model-section-001",
        "model-section-002",
    ]


def test_parallel_state_delta_rejects_global_field_mutation() -> None:
    before = _shared_state()
    after = before.model_copy(deep=True)
    after.final_result = {"forbidden": True}
    after.context_bundle.generation.current_section_id = "forbidden"
    with pytest.raises(StateWriteContractViolation) as captured:
        SchemeGenerationStageService._parallel_state_delta(
            before,
            after,
            section_id="section-001",
        )
    assert "final_result" in captured.value.changed_paths
    assert (
        "context_bundle.generation.current_section_id"
        in captured.value.changed_paths
    )


def _parallel_plan(section_id: str, order: int) -> SectionPlanSchema:
    return SectionPlanSchema(
        section_id=section_id,
        section_title=section_id,
        section_order=order,
        execution_group="hardening",
        can_run_in_parallel=True,
        requires_global_context=False,
    )


def _parallel_envelope(plan: SectionPlanSchema):
    context = RAGContextSchema(
        context_text="",
        used_context_chunk_ids=[],
        matched_chunk_ids=[],
        used_doc_ids=[],
        used_context_chars=0,
        context_item_count=0,
    )
    section = SchemeSectionSchema(
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        content="completed",
        status=ExecutionStatus.SUCCESS,
    )
    result = SectionExecutionResultSchema(
        section=section,
        evidence=SectionEvidenceBundleSchema(
            section_id=plan.section_id,
            section_title=plan.section_title,
            query="",
            rag_context=context,
        ),
    )
    return ParallelSectionExecutionEnvelopeSchema(
        section_id=plan.section_id,
        result=result,
        state_delta=ParallelSectionStateDeltaSchema(
            generated_section_ids=[plan.section_id]
        ),
    )


@pytest.mark.parametrize(
    ("exception", "expected_category"),
    [
        (RuntimeError("fatal branch"), FailureCategory.UNKNOWN),
        (TimeoutError("branch timeout"), FailureCategory.TIMEOUT),
        (
            WorkflowExecutionCancelled("branch cancelled"),
            FailureCategory.CANCELLED,
        ),
    ],
)
def test_parallel_batch_discards_results_when_one_branch_fails(
    exception,
    expected_category,
) -> None:
    plans = [_parallel_plan("section-001", 1), _parallel_plan("section-002", 2)]
    normal_finished = Event()

    def execute(plan):
        if plan.section_id == "section-002":
            raise exception
        normal_finished.set()
        return _parallel_envelope(plan)

    with pytest.raises(type(exception)) as captured:
        SectionParallelExecutionGraph().execute_batch(
            plans,
            max_concurrency=2,
            execute=execute,
        )
    assert isinstance(captured.value, type(exception))
    assert classify_failure(captured.value) == expected_category
    assert normal_finished.is_set() or isinstance(
        exception,
        WorkflowExecutionCancelled,
    )


def test_parallel_cancel_starts_no_new_branch_and_revokes_side_effects() -> None:
    plans = [_parallel_plan("section-001", 1), _parallel_plan("section-002", 2)]
    started: list[str] = []
    committed: list[str] = []
    control = WorkflowExecutionControl.with_timeout(
        execution_id="parallel-cancel-hardening",
        timeout_seconds=10,
    )
    control.defer_external_commit(
        lambda: committed.append("forbidden"),
        description="parallel cancelled side effect",
    )
    control.cancel("cancelled before parallel scheduling")

    with activate_execution_control(control), pytest.raises(
        WorkflowExecutionCancelled
    ):
        SectionParallelExecutionGraph().execute_batch(
            plans,
            max_concurrency=2,
            execute=lambda plan: (
                started.append(plan.section_id) or _parallel_envelope(plan)
            ),
        )

    assert started == []
    assert committed == []
    assert control.metadata()["pending_external_commits"] == 0
    assert control.metadata()["external_commit_state"] == "aborted"


def test_parallel_budget_reservation_is_atomic_and_never_oversubscribes() -> None:
    allocator = ParallelSectionBudgetAllocator(
        max_total_llm_calls=2,
        max_total_tokens=512,
    )
    reservations = allocator.reserve_batch(
        ["section-001", "section-002"],
        llm_calls_per_section=1,
        tokens_per_section=256,
    )
    assert set(reservations) == {"section-001", "section-002"}
    before_failure = allocator.snapshot()
    with pytest.raises(WorkflowBudgetExceeded):
        allocator.reserve_batch(
            ["section-003"],
            llm_calls_per_section=1,
            tokens_per_section=256,
        )
    assert allocator.snapshot() == before_failure


def test_model_usage_snapshot_merge_preserves_prior_and_adds_only_delta() -> None:
    prior = {
        "budget_semantics": "logical_model_call_v1",
        "token_usage_semantics": "actual_provider_response_v1",
        "logical_calls": 3,
        "unique_logical_call_ids": 3,
        "provider_attempts": 4,
        "calls_by_model": {"model-a": 4},
        "calls_by_profile": {"profile-a": 4},
        "calls_by_provider": {"provider-a": 4},
        "prompt_tokens": 30,
        "completion_tokens": 12,
        "total_tokens": 42,
        "latency_ms": 90,
        "failures": 1,
        "cancelled_attempts": 0,
        "incomplete_usage_attempts": 0,
        "availability_fallback_count": 1,
        "quality_escalation_count": 0,
        "cost_if_available": None,
    }
    assert merge_model_usage_snapshots(prior, {}) == prior
    merged = merge_model_usage_snapshots(
        prior,
        {
            **prior,
            "logical_calls": 1,
            "unique_logical_call_ids": 1,
            "provider_attempts": 1,
            "calls_by_model": {"model-b": 1},
            "calls_by_profile": {"profile-b": 1},
            "calls_by_provider": {"provider-b": 1},
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 7,
            "latency_ms": 10,
            "failures": 0,
            "availability_fallback_count": 0,
        },
    )
    assert merged["logical_calls"] == 4
    assert merged["provider_attempts"] == 5
    assert merged["total_tokens"] == 49
    assert merged["calls_by_model"] == {"model-a": 4, "model-b": 1}
