"""Cancellation at real section/application boundaries; no real providers."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_projector import outcome_from_result
from apps.enterprise_document.services.scheme_writer import stage_service as stages
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
from core.config import get_settings
from core.runtime.execution_control import current_execution_control
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from schemas.status import ExecutionStatus
from test_phase2_runtime_safety_closure import _run_production_fake


def test_serial_service_stops_before_next_section_admission(tmp_path, monkeypatch):
    """The first real section returns late; the service must not admit another."""
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "1")
    original = SectionExecutionGraph.execute
    returned = threading.Event()
    graph_calls = []
    late_events = []
    late_prerequisites = []

    def execute(graph, request, **kwargs):
        graph_calls.append(request.section_plan.section_id)
        if len(graph_calls) != 1:
            raise AssertionError("cancelled service admitted another section")
        result = original(graph, request, **kwargs)
        control = current_execution_control()
        assert control is not None
        control.cancel("test_parent_cancel_after_first_section")
        returned.set()
        return result

    def event(name, **kwargs):
        if returned.is_set():
            late_events.append((name, kwargs.get("section_id")))

    def configure(engine):
        runtime = engine.stage_service.target_workflow_runtime
        original_prerequisites = runtime.ensure_section_execution_prerequisites

        def prerequisites(*args, **kwargs):
            if returned.is_set():
                late_prerequisites.append(args)
            return original_prerequisites(*args, **kwargs)

        runtime.ensure_section_execution_prerequisites = prerequisites

    summary, factory = _run_production_fake(
        tmp_path, run_id="h9_service_cancel",
        mutate_engine=configure,
        patchers=(patch.object(SectionExecutionGraph, "execute", execute),
                  patch.object(stages, "emit_workflow_custom_event", event)),
    )
    assert returned.is_set(), "fixture never reached the first real section result"
    assert factory.engines[0].wait_for_idle(timeout_seconds=1.0)
    assert len(graph_calls) == 1
    assert late_prerequisites == []
    assert late_events == []
    assert summary["terminal_outcome"]["failure_category"] == "cancelled"
    assert summary["task_state"]["status"] != "running"


def test_real_langgraph_node_observes_parent_cancel_before_next_node(tmp_path, monkeypatch):
    """Cancellation after prepare must stop retrieval/finalization in the graph."""
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "1")
    original_prepare = SectionExecutionGraph._prepare_section
    cancelled = threading.Event()
    expected_controls = []
    observed_controls = []
    after_cancel_retrieval = []
    after_cancel_state_reads = []
    finalizations = []

    def configure(engine):
        graph = engine.section_execution_graph
        original_execute = graph.execute
        original_retrieve = graph.evidence_service.retrieve
        original_finalize = graph._finalize_section
        original_restore_payload = graph.codec.restore_payload

        def execute(*args, **kwargs):
            expected_controls.append(current_execution_control())
            return original_execute(*args, **kwargs)

        def retrieve(*args, **kwargs):
            if cancelled.is_set():
                after_cancel_retrieval.append(True)
            return original_retrieve(*args, **kwargs)

        def finalize(*args, **kwargs):
            finalizations.append(True)
            return original_finalize(*args, **kwargs)

        def restore_payload(*args, **kwargs):
            if cancelled.is_set():
                after_cancel_state_reads.append(True)
            return original_restore_payload(*args, **kwargs)

        graph.execute = execute
        graph.evidence_service.retrieve = retrieve
        graph._finalize_section = finalize
        graph.codec.restore_payload = restore_payload

    def prepare(graph, state):
        result = original_prepare(graph, state)
        control = current_execution_control()
        observed_controls.append(control)
        assert control is expected_controls[-1] and control is not None
        control.cancel("test_parent_cancel_between_graph_nodes")
        cancelled.set()
        return result

    summary, factory = _run_production_fake(
        tmp_path, run_id="h9_graph_node_cancel", mutate_engine=configure,
        patchers=(patch.object(SectionExecutionGraph, "_prepare_section", prepare),),
    )
    assert cancelled.is_set()
    assert observed_controls == expected_controls
    assert factory.engines[0].wait_for_idle(timeout_seconds=1.0)
    assert after_cancel_state_reads == []
    assert after_cancel_retrieval == []
    assert finalizations == []
    assert summary["terminal_outcome"]["failure_category"] == "cancelled"


def test_section_graph_does_not_project_result_after_invoke_cancel(tmp_path, monkeypatch):
    """The actual graph completes, then cancellation precedes projection."""
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "1")
    cancelled = threading.Event()
    projections = []

    def configure(engine):
        graph = engine.section_execution_graph
        build_graph = graph.build_graph
        project = graph.graph_state_codec.project_onto

        def build():
            compiled = build_graph()

            def invoke(*args, **kwargs):
                if cancelled.is_set():
                    raise AssertionError("late second graph dispatched")
                result = compiled.invoke(*args, **kwargs)
                control = current_execution_control()
                assert control is not None
                control.cancel("test_parent_cancel_before_projection")
                cancelled.set()
                return result

            return SimpleNamespace(invoke=invoke)

        def project_onto(*args, **kwargs):
            if cancelled.is_set():
                projections.append(True)
            return project(*args, **kwargs)

        graph.build_graph = build
        graph.graph_state_codec.project_onto = project_onto

    summary, factory = _run_production_fake(
        tmp_path, run_id="h9_graph_projection_cancel", mutate_engine=configure,
    )
    assert cancelled.is_set()
    assert factory.engines[0].wait_for_idle(timeout_seconds=1.0)
    assert projections == []
    assert summary["terminal_outcome"]["failure_category"] == "cancelled"


def test_finalization_cancel_does_not_complete_canonical_source(tmp_path, monkeypatch):
    """Cancellation during finalization must precede the source-owner commit."""
    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "1")
    cancelled = threading.Event()
    source_completions = []

    def configure(engine):
        graph = engine.section_execution_graph
        add_generated = graph.state_writer.add_generated_section
        dependencies = graph.coordinator.section_dependencies
        complete_source = dependencies.complete_source

        def add(*args, **kwargs):
            result = add_generated(*args, **kwargs)
            control = current_execution_control()
            assert control is not None
            control.cancel("test_cancel_during_finalization")
            cancelled.set()
            return result

        def complete(result):
            source_completions.append(result)
            return complete_source(result)

        graph.state_writer.add_generated_section = add
        dependencies.complete_source = complete

    summary, factory = _run_production_fake(
        tmp_path, run_id="h9_finalize_cancel", mutate_engine=configure,
    )
    assert cancelled.is_set(), "fixture never entered real section finalization"
    assert factory.engines[0].wait_for_idle(timeout_seconds=1.0)
    assert source_completions == []
    assert summary["terminal_outcome"]["failure_category"] == "cancelled"
    assert summary["terminal_outcome"]["artifact_exists"] is False


class _ResultFactory:
    """Replace only the runtime collaborator; exercise the real application run."""

    def __init__(self, status, *, typed_error=True, close_error=None, primary_error=None):
        self.status = status
        self.typed_error = typed_error
        self.close_error = close_error
        self.primary_error = primary_error
        self.result = None
        self.close_calls = 0

    def build(self, **kwargs):
        manager = kwargs["task_manager"]

        def run(task):
            if self.primary_error is not None:
                raise self.primary_error
            manager.mark_running(task)
            error = (ErrorSchema(
                error_code="WORKFLOW_NODE_TIMEOUT", error_type="RuntimeValidationError",
                message="business node execute_sections exceeded its timeout",
                failed_node="execute_sections", failure_category=FailureCategory.TIMEOUT,
                retryable=self.status is ExecutionStatus.RETRYABLE_FAILED,
                recoverable=True,
            ) if self.typed_error and self.status in {
                ExecutionStatus.FAILED, ExecutionStatus.RETRYABLE_FAILED,
            } else None)
            self.result = AgentResultSchema(
                result_id="h9-primary-result", task_id=task.task_id, run_id=task.run_id,
                agent_name="SupervisorAgent", agent_type="supervisor", status=self.status,
                result_type="workflow_result", error=error,
            )
            if error is not None:
                manager.mark_failed(task, error, retryable=error.retryable, result=self.result)
            return self.result

        def close():
            self.close_calls += 1
            if self.close_error is not None:
                raise self.close_error

        return SimpleNamespace(run=run, close=close)


def _run_application(tmp_path, monkeypatch, factory):
    monkeypatch.setenv("USE_REAL_RAG_TOOL", "false")
    settings = replace(get_settings(), data_root=tmp_path,
                       default_model_name="fake_llm", supervisor_model_name="fake_llm",
                       enable_llm_routing=False)
    return MainlineApplicationService(supervisor_factory=factory).run(
        project_root=Path(__file__).resolve().parents[2],
        user_input="test structured failure close precedence", run_id="h9_close_precedence",
        output_root=tmp_path, settings=settings, allow_demo_defaults=True,
    )


@pytest.mark.parametrize("status", [ExecutionStatus.FAILED, ExecutionStatus.RETRYABLE_FAILED])
@pytest.mark.parametrize("close_error", [
    TimeoutError("workflow workers did not become idle; resource closure was deferred until idle"),
    RuntimeError("owned resource close failed"),
])
def test_mainline_preserves_structured_primary_failure(tmp_path, monkeypatch, status, close_error):
    factory = _ResultFactory(status, close_error=close_error)
    summary = _run_application(tmp_path, monkeypatch, factory)
    assert factory.close_calls == 1
    assert summary["status"] == status.value
    assert summary["supervisor_result"] == factory.result.model_dump(mode="json")
    assert summary["terminal_outcome"] == outcome_from_result(factory.result)
    assert summary["terminal_outcome"]["failure_category"] == "timeout"
    assert summary["task_state"]["status"] == status
    assert summary["shutdown_diagnostics"] == [{
        "event_type": "supervisor_close_failed",
        "error_type": type(close_error).__name__,
        "error_message": str(close_error),
    }]


@pytest.mark.parametrize("status", [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS])
def test_mainline_does_not_suppress_standalone_close_failure(tmp_path, monkeypatch, status):
    error = TimeoutError("standalone close failed")
    factory = _ResultFactory(status, close_error=error)
    with pytest.raises(TimeoutError) as caught:
        _run_application(tmp_path, monkeypatch, factory)
    assert caught.value is error
    assert factory.close_calls == 1


def test_mainline_does_not_treat_untyped_failed_result_as_primary_error(tmp_path, monkeypatch):
    error = RuntimeError("standalone close failed")
    factory = _ResultFactory(ExecutionStatus.FAILED, typed_error=False, close_error=error)
    with pytest.raises(RuntimeError) as caught:
        _run_application(tmp_path, monkeypatch, factory)
    assert caught.value is error


def test_mainline_preserves_raised_primary_exception(tmp_path, monkeypatch):
    primary = KeyboardInterrupt("primary runtime interruption")
    factory = _ResultFactory(ExecutionStatus.FAILED,
                             primary_error=primary, close_error=RuntimeError("close failed"))
    with pytest.raises(KeyboardInterrupt) as caught:
        _run_application(tmp_path, monkeypatch, factory)
    assert caught.value is primary
    assert factory.close_calls == 1


@pytest.mark.parametrize("recovery", ["resume", "completed_reuse", "replan_before_dispatch"])
def test_durable_recovery_observes_cancellation_before_shared_projection(
    tmp_path, monkeypatch, recovery,
):
    """Use actual Target checkpoints; cancel at three distinct recovery boundaries."""
    from apps.enterprise_document.runtime.agent_rag_v1.mainline_recovery import MainlineReplanAttempt
    from core.runtime.execution_control import WorkflowExecutionCancelled
    from model_gateway.fake_llm_client import FakeLLMClient

    monkeypatch.setenv("SECTION_MAX_CONCURRENCY", "1")
    graph_calls, reached, checks, errors = [], [], [], []

    def configure(engine):
        graph = engine.section_execution_graph
        original_execute = graph.execute
        original_build = graph.build_graph

        def execute(request, **kwargs):
            graph_calls.append(request.section_plan.section_id)
            assert len(graph_calls) == 1, "cancelled recovery admitted another section"
            shared = kwargs["shared_state"]
            registry = kwargs["citation_registry"]
            identity = dict(workflow_namespace=kwargs["workflow_namespace"],
                            run_id=shared.run_id, section_id=request.section_plan.section_id)
            control = current_execution_control()
            assert control is not None
            try:
                if recovery == "resume":
                    # The real compiled graph writes its durable cursor immediately
                    # before finalization; no fabricated checkpoint or result is used.
                    compiled = original_build()

                    def interrupt_before_finalize(*args, **call_kwargs):
                        return compiled.invoke(*args, interrupt_before=["finalize_section"], **call_kwargs)

                    with patch.object(graph, "build_graph", return_value=SimpleNamespace(invoke=interrupt_before_finalize)):
                        with pytest.raises(RuntimeError, match="section subgraph completed without result"):
                            original_execute(request, **kwargs)
                    history = graph.checkpoint_history(**identity)
                    persisted = next(graph.codec.restore_payload(row.checkpoint["channel_values"])
                                     for row in history
                                     if row.checkpoint.get("channel_values", {}).get("section_payload"))
                    assert not persisted.completed and persisted.result is None
                    assert persisted.current_node == "evaluate_generation"
                    assert persisted.route == "finalize"
                    assert persisted.active_execution_lineage is not None
                    assert persisted.current_pre_write_route_decision is not None
                else:
                    first = original_execute(request, **kwargs)
                    assert first.canonical_section_source_binding is not None
                    history = graph.checkpoint_history(**identity)
                    persisted = next(graph.codec.restore_payload(row.checkpoint["channel_values"])
                                     for row in history
                                     if row.checkpoint.get("channel_values", {}).get("section_payload", {}).get("completed"))
                    assert persisted.result == first
                    assert persisted.active_execution_lineage is not None

                before_state = shared.model_dump(mode="json")
                before_citations = [c.model_dump(mode="json") for c in registry.all()]
                project = graph.graph_state_codec.project_onto
                restore_citations = registry.restore
                projections, citation_restores = [], []

                def observe_project(*args, **call_kwargs):
                    projections.append(True)
                    return project(*args, **call_kwargs)

                def observe_citations(*args, **call_kwargs):
                    citation_restores.append(True)
                    return restore_citations(*args, **call_kwargs)

                with patch.object(graph.graph_state_codec, "project_onto", observe_project), \
                        patch.object(registry, "restore", observe_citations):
                    with pytest.raises(WorkflowExecutionCancelled):
                        if recovery == "resume":
                            def resume_then_cancel(*args, **call_kwargs):
                                assert args[0] is None
                                final = compiled.invoke(*args, **call_kwargs)
                                completed = graph.codec.restore_payload(final)
                                assert completed.completed and completed.result is not None
                                assert completed.result.canonical_section_source_binding is not None
                                reached.append("actual_durable_resume_completed")
                                control.cancel("h9_resume_completed_before_projection")
                                return final

                            with patch.object(graph, "build_graph", return_value=SimpleNamespace(invoke=resume_then_cancel)):
                                graph.resume(**identity, shared_state=shared, citation_registry=registry)
                        elif recovery == "completed_reuse":
                            restore_state = graph.codec.restore_graph_state
                            validate_sources = graph._validate_target_recovery_sources
                            validated = []

                            def validate(*args, **call_kwargs):
                                value = validate_sources(*args, **call_kwargs)
                                validated.append(True)
                                return value

                            def restore_then_cancel(payload):
                                restored = restore_state(payload)
                                assert payload.completed and validated == [True]
                                reached.append("actual_completed_reuse_validated_and_restored")
                                control.cancel("h9_reuse_restored_before_projection")
                                return restored

                            with patch.object(graph, "_validate_target_recovery_sources", validate), \
                                    patch.object(graph.codec, "restore_graph_state", restore_then_cancel), \
                                    patch.object(graph, "resume", side_effect=AssertionError("completed reuse resumed graph")), \
                                    patch.object(FakeLLMClient, "generate", side_effect=AssertionError("completed reuse redispatched model")):
                                graph.recover_or_reuse(**identity, task_id=shared.task_id,
                                    document_id=request.document_id, project_input=request.project_input,
                                    section_plan=request.section_plan, shared_state=shared,
                                    citation_registry=registry,
                                    expected_canonical_plan_binding=request.canonical_plan_binding)
                        else:
                            prepare = MainlineReplanAttempt.prepare
                            revised = persisted.section_plan.model_copy(update={"metadata": {
                                **dict(persisted.section_plan.metadata), "replan_reason": "h9_cancel_before_dispatch",
                            }}, deep=True)

                            def prepare_then_cancel(**call_kwargs):
                                replanned = prepare(**call_kwargs)
                                assert replanned.generation_attempt == persisted.generation_attempt + 1
                                assert replanned.section_state.attempt.attempt_id != persisted.section_state.attempt.attempt_id
                                reached.append("actual_replan_prepared_before_dispatch")
                                control.cancel("h9_replan_prepared_before_dispatch")
                                return replanned

                            with patch.object(MainlineReplanAttempt, "prepare", side_effect=prepare_then_cancel), \
                                    patch.object(graph, "build_graph", side_effect=AssertionError("cancelled replan dispatched graph")), \
                                    patch.object(FakeLLMClient, "generate", side_effect=AssertionError("cancelled replan dispatched model")):
                                graph.replan(**identity, revised_plan=revised,
                                             shared_state=shared, citation_registry=registry)

                assert projections == []
                assert citation_restores == []
                assert shared.model_dump(mode="json") == before_state
                assert [c.model_dump(mode="json") for c in registry.all()] == before_citations
                checks.append(recovery)
                # Return control to the real owner with its original cancellation signal.
                control.checkpoint()
                raise AssertionError("cancelled recovery returned normally")
            except Exception as exc:
                errors.append(repr(exc))
                raise

        graph.execute = execute

    summary, factory = _run_production_fake(
        tmp_path, run_id="h9_recovery_cancel_" + recovery, mutate_engine=configure,
    )
    assert errors == [], errors
    assert len(reached) == 1, reached
    assert checks == [recovery]
    assert len(graph_calls) == 1
    assert factory.engines[0].wait_for_idle(timeout_seconds=1.0)
    assert summary["terminal_outcome"]["failure_category"] == "cancelled"
