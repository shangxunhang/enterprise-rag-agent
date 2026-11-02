"""C1 transport controls, including one existing offline Fake projection flow."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionOutcome
from core.retry_policy import RetryDecisionPolicy
from core.hashing import canonical_sha256
from core.runtime.execution_control import WorkflowExecutionControl
from apps.enterprise_document.schemas.scheme_generation_graph import SchemeGenerationGraphPayloadSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService, SchemeGenerationStageFailure,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import SchemeWriterRuntimeSupport
from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleIntegrityError
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from test_fpbca_bcd_c1_composite import _Clock, _service, _run, _attempts


@pytest.mark.parametrize("typed", [False, True])
def test_actual_top_level_error_mapping_retains_exact_dispatch_trigger(typed):
    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    engine.stage_service = SimpleNamespace(runtime_support=SchemeWriterRuntimeSupport)
    engine.retry_policy = RetryDecisionPolicy()
    exc = (SchemeGenerationStageFailure(ErrorSchema(
        error_code="C1_TYPED_FAILURE", error_type="RuntimeError", message="controlled",
        failure_category=FailureCategory.VALIDATION,
    )) if typed else RuntimeError("controlled actual dispatch failure"))
    exc.c1_triggering_attempt_id = "old-0"
    outcome = WorkflowNodeExecutionOutcome(
        kind="error", started_at=_Clock().now_iso(), finished_at=_Clock().now_iso(),
        latency_ms=0, execution_control=WorkflowExecutionControl.with_timeout(
            execution_id="c1-transport-control", timeout_seconds=60,
        ), exception=exc,
    )
    error = engine._error_from_outcome(node_name="execute_sections", outcome=outcome)
    assert error.details["c1_triggering_attempt_id"] == "old-0"
    if typed:
        assert error.error_code == "C1_TYPED_FAILURE"


@pytest.mark.parametrize("trigger", ["old-0", None])
def test_actual_stage_n_current_requires_exact_producer_trigger(tmp_path, trigger):
    service = _service(tmp_path / "lifecycle.jsonl")
    _run(service)
    _attempts(service, 3)
    stage = object.__new__(SchemeGenerationStageService)
    stage.target_workflow_runtime = SimpleNamespace(lifecycle_service=service)
    stage.target_post_write_composition = None
    stage.agent_name, stage.agent_type = "scheme_writer", "document_generation"
    stage.runtime_support = SimpleNamespace(now_iso=_Clock().now_iso)
    stage.state_writer = SimpleNamespace(add_error=lambda state, error: state.errors.append(error),
        set_scheme_artifacts=lambda *args, **kwargs: None)
    error = ErrorSchema(error_code="C1_N_FAILURE", error_type="RuntimeError", message="controlled",
        failure_category=FailureCategory.VALIDATION,
        details={"c1_triggering_attempt_id": trigger} if trigger else {})
    payload = SchemeGenerationGraphPayloadSchema(task_id="task-c1", run_id="run-c1",
        terminal_error=error, failed_business_node="execute_sections")
    state = SimpleNamespace(task_id="task-c1", run_id="run-c1", errors=[])
    before = service.path.read_bytes()
    if trigger is None:
        # The Owner still rejects an unattributed Attempt failure. The Stage
        # retains that first error and reports its cleanup refusal independently.
        with pytest.raises(LifecycleIntegrityError):
            service.terminalize_noncancel_failure(
                "run-c1", 1, RunLifecycleStatus.FAILED, "unattributed-owner-control",
            )
        result = stage.finalize_workflow_failure(state, payload)
        assert result.terminal_error.message == error.message
        assert result.terminal_agent_result is not None
        assert result.terminal_outcome.run_status.value == "running"
        diagnostics = result.terminal_error.details["r02_failure_diagnostics"]
        assert diagnostics[0]["operation"] == "lifecycle_cleanup"
        assert diagnostics[0]["error_type"] == "LifecycleIntegrityError"
        assert "lifecycle_composite_transition" not in result.terminal_error.details
        assert any(item.code == "R02_FAILURE_CLEANUP_DIAGNOSTIC"
                   for item in result.terminal_outcome.diagnostics)
        assert service.path.read_bytes() == before
    else:
        result = stage.finalize_workflow_failure(state, payload)
        assert result.terminal_outcome.run_status.value == "failed"
        assert service.load_attempt("old-0").status.value == "failed"
        assert service.load_attempt("old-1").status.value == "cancelled"
        assert service.load_attempt("old-2").status.value == "cancelled"


@pytest.mark.parametrize("count", [0, 1, 3])
def test_composite_receipt_projects_exact_result_records_and_replays(tmp_path, count):
    service = _service(tmp_path / "lifecycle.jsonl")
    _run(service)
    _attempts(service, count)
    stage = object.__new__(SchemeGenerationStageService)
    stage.target_workflow_runtime = SimpleNamespace(lifecycle_service=service)
    stage.target_post_write_composition = None
    stage.agent_name, stage.agent_type = "scheme_writer", "document_generation"
    stage.runtime_support = SimpleNamespace(now_iso=_Clock().now_iso)
    stage.state_writer = SimpleNamespace(add_error=lambda state, error: state.errors.append(error),
        set_scheme_artifacts=lambda *args, **kwargs: None)
    error = ErrorSchema(error_code="C1_PROJECTION_FAILURE", error_type="RuntimeError", message="controlled",
        failure_category=FailureCategory.VALIDATION,
        details={"c1_triggering_attempt_id": "old-0"} if count else {})
    payload = SchemeGenerationGraphPayloadSchema(task_id="task-c1", run_id="run-c1",
        terminal_error=error, failed_business_node="execute_sections")
    state = SimpleNamespace(task_id="task-c1", run_id="run-c1", errors=[])
    result = stage.finalize_workflow_failure(state, payload)
    binding = result.terminal_error.details["lifecycle_composite_transition"]
    frame = json.loads(service.path.read_text(encoding="utf-8").splitlines()[-1])
    assert binding["event_sha256"] == frame["event_sha256"]
    assert binding["operation_key"] == frame["operation_key"]
    assert binding["event_ref"].endswith(frame["event_sha256"])
    assert binding["resulting_run"] == frame["resulting_run"]
    assert binding["resulting_attempts"] == frame["resulting_attempts"]
    assert result.terminal_agent_result.result["lifecycle_composite_transition"] == binding
    assert result.terminal_agent_result.metadata["lifecycle_composite_transition"] == binding
    diagnostic_refs = [item.diagnostic_ref for item in result.terminal_outcome.diagnostics
                       if item.code == "LIFECYCLE_COMPOSITE_TRANSITION"]
    assert diagnostic_refs == ["sha256:" + canonical_sha256(binding)]
    before = service.path.read_bytes()
    replay = stage.finalize_workflow_failure(state, result)
    assert service.path.read_bytes() == before
    assert replay.terminal_error.details["lifecycle_composite_transition"] == binding
    assert replay.terminal_outcome == result.terminal_outcome


def test_actual_top_level_supervisor_task_keep_same_primary_composite_receipt(tmp_path):
    from application.terminal_outcome_projector import public_terminal_result
    from test_phase2_runtime_safety_closure import _run_production_fake

    summary, factory = _run_production_fake(tmp_path, run_id="c1_projection_primary", patchers=(
        patch.object(SchemeGenerationStageService, "retrieve_document_evidence",
            side_effect=RuntimeError("frozen C1 actual primary projection failure")),
    ))
    stage = factory.engines[0].stage_service
    lifecycle = stage.target_workflow_runtime.lifecycle_service
    frame = json.loads(lifecycle.path.read_text(encoding="utf-8").splitlines()[-1])
    assert frame["schema_version"] == "run_attempt_lifecycle_composite_event_v1"
    errors = [summary["task_state"]["error"], summary["supervisor_result"]["error"],
              summary["supervisor_result"]["result"]["workflow_execution"]["error"]]
    bindings = [error["details"]["lifecycle_composite_transition"] for error in errors]
    assert all(binding == bindings[0] for binding in bindings)
    assert bindings[0]["resulting_run"] == frame["resulting_run"]
    assert bindings[0]["resulting_attempts"] == frame["resulting_attempts"]
    assert bindings[0]["event_sha256"] == frame["event_sha256"]
    assert all(error["message"] == "frozen C1 actual primary projection failure" for error in errors)
    public = public_terminal_result(summary)
    assert [item["diagnostic_ref"] for item in public["terminal_outcome"]["diagnostics"]
            if item["code"] == "LIFECYCLE_COMPOSITE_TRANSITION"] == ["sha256:" + canonical_sha256(bindings[0])]


@pytest.mark.parametrize("terminal_status", ["success", "partial_success"])
def test_late_failure_preserves_sealed_canonical_run_without_new_event(tmp_path, monkeypatch, terminal_status):
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
    from test_multi_agent_v3_release_lineage import _assessment_fixture, _running_lifecycle, _enter_finalizing

    if terminal_status == "success":
        fx = _assessment_fixture(tmp_path / "assessment")
        assessment_ref = fx["store"].put_if_absent_or_same(fx["record"])
        lifecycle, running = _running_lifecycle(tmp_path / "lifecycle.jsonl", resolver=fx["store"].resolve_exact)
        preimage = _enter_finalizing(lifecycle, running, fx["barrier"])
        terminal = lifecycle.transition_run(running.identity.run_id, preimage.lifecycle_revision,
            RunLifecycleStatus.SUCCESS, run_completion_assessment_ref=assessment_ref)
    else:
        lifecycle, running = _running_lifecycle(tmp_path / "lifecycle.jsonl")
        terminal = lifecycle.transition_run(running.identity.run_id, running.lifecycle_revision,
            RunLifecycleStatus.PARTIAL_SUCCESS)
    before = lifecycle.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        lifecycle.terminalize_noncancel_failure(terminal.identity.run_id, terminal.lifecycle_revision,
            RunLifecycleStatus.FAILED, "illegal late owner mutation")
    assert lifecycle.path.read_bytes() == before

    stage = object.__new__(SchemeGenerationStageService)
    stage.target_workflow_runtime = SimpleNamespace(lifecycle_service=lifecycle)
    stage.target_post_write_composition = None
    stage.agent_name, stage.agent_type = "scheme_writer", "document_generation"
    stage.runtime_support = SimpleNamespace(now_iso=_Clock().now_iso)
    stage.state_writer = SimpleNamespace(add_error=lambda state, error: state.errors.append(error),
        set_scheme_artifacts=lambda *args, **kwargs: None)
    error = ErrorSchema(error_code="C1_LATE_FAILURE", error_type="RuntimeError", message="late primary failure",
        failure_category=FailureCategory.VALIDATION)
    payload = SchemeGenerationGraphPayloadSchema(task_id=terminal.identity.task_id, run_id=terminal.identity.run_id,
        terminal_error=error, failed_business_node="persist_deliverable")
    state = SimpleNamespace(task_id=terminal.identity.task_id, run_id=terminal.identity.run_id, errors=[])
    monkeypatch.setattr(lifecycle.log, "append_serialized", lambda *args, **kwargs: pytest.fail("late failure appended lifecycle"))
    first = stage.finalize_workflow_failure(state, payload)
    replay = stage.finalize_workflow_failure(state, first)
    assert first.terminal_outcome.run_status is terminal.status
    assert replay.terminal_outcome == first.terminal_outcome
    assert first.terminal_error == error == replay.terminal_error
    assert "lifecycle_composite_transition" not in first.terminal_agent_result.result
    assert "lifecycle_composite_transition" not in first.terminal_agent_result.metadata
    assert all(item.code != "LIFECYCLE_COMPOSITE_TRANSITION" for item in first.terminal_outcome.diagnostics)
    assert lifecycle.load_run_lifecycle(terminal.identity.run_id) == terminal
    assert lifecycle.path.read_bytes() == before



def _r02_transport_fixture(tmp_path, *, trigger="old-0"):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    stage = object.__new__(SchemeGenerationStageService)
    stage.target_workflow_runtime = SimpleNamespace(lifecycle_service=service)
    stage.target_post_write_composition = None
    stage.agent_name, stage.agent_type = "scheme_writer", "document_generation"
    stage.runtime_support = SimpleNamespace(now_iso=_Clock().now_iso)
    stage.state_writer = SimpleNamespace(
        add_error=lambda state, error: state.errors.append(error),
        set_scheme_artifacts=lambda *args, **kwargs: None,
    )
    error = ErrorSchema(
        error_code="R02_ORIGINAL_FAILURE", error_type="RuntimeError",
        message="R02 controlled original failure",
        failed_node="evaluate_document_gate",
        failure_category=FailureCategory.VALIDATION, retryable=False,
        details={"c1_triggering_attempt_id": trigger} if trigger is not None else {},
        stack_trace="R02 original stack retained",
    )
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task-c1", run_id="run-c1", document_id="document-c1",
        terminal_error=error, failed_business_node="evaluate_document_gate",
    )
    state = SimpleNamespace(task_id="task-c1", run_id="run-c1", errors=[])
    return service, run, rows, stage, payload, state


@pytest.mark.parametrize("cut", [
    "cas", "before_append", "after_fsync", "canonical_read", "initial_read", "status_projection",
])
def test_r02_cleanup_failure_retains_primary_and_reports_only_actual_owner_state(
    tmp_path, monkeypatch, cut,
):
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
        LifecycleRevisionConflict,
    )

    service, run, rows, stage, payload, state = _r02_transport_fixture(tmp_path)
    prefix = service.path.read_bytes()
    original_error = payload.terminal_error
    native_failure = service.terminalize_noncancel_failure
    native_append = service.log.append_serialized
    native_read = service.load_run_lifecycle
    read_calls = []

    with monkeypatch.context() as scoped:
        if cut == "cas":
            def stale_failure(run_id, revision, target, reason, **kwargs):
                return native_failure(run_id, revision + 1, target, reason, **kwargs)
            scoped.setattr(service, "terminalize_noncancel_failure", stale_failure)
        elif cut in {"before_append", "after_fsync"}:
            def interrupted_append(serialized):
                if cut == "after_fsync":
                    native_append(serialized)
                raise OSError("R02 controlled cleanup IO failure")
            scoped.setattr(service.log, "append_serialized", interrupted_append)
        elif cut in {"canonical_read", "initial_read"}:
            def interrupted_read(run_id):
                read_calls.append(run_id)
                if cut == "initial_read" or len(read_calls) > 1:
                    raise OSError("R02 controlled canonical read failure")
                return native_read(run_id)
            scoped.setattr(service, "load_run_lifecycle", interrupted_read)
        else:
            def interrupted_projection(*args, **kwargs):
                raise OSError("R02 controlled failure status projection")
            scoped.setattr(stage, "_target_status_source_mode", interrupted_projection)
        result = stage.finalize_workflow_failure(state, payload)

    # No secondary exception replaces any original Error field or source/stack.
    assert result.terminal_error.model_dump(exclude={"details"}) == original_error.model_dump(
        exclude={"details"},
    )
    assert result.terminal_agent_result.error == result.terminal_error
    assert result.terminal_agent_result.error_message == original_error.message
    assert result.terminal_agent_result.status.value == "failed"
    diagnostics = result.terminal_error.details["r02_failure_diagnostics"]
    assert diagnostics == result.terminal_agent_result.result["r02_failure_diagnostics"]
    assert diagnostics == result.terminal_agent_result.metadata["r02_failure_diagnostics"]
    projected = [item for item in result.terminal_outcome.diagnostics
                 if item.code == "R02_FAILURE_CLEANUP_DIAGNOSTIC"]
    assert len(projected) == 1
    assert projected[0].diagnostic_ref == "sha256:" + canonical_sha256(diagnostics)
    if cut == "cas":
        assert any(item["error_type"] == "LifecycleRevisionConflict" for item in diagnostics)
    else:
        assert any(item["error_type"] == "OSError" for item in diagnostics)

    committed = cut in {"after_fsync", "canonical_read", "status_projection"}
    receipt_returned = cut in {"canonical_read", "status_projection"}
    cold = _service(service.path)
    assert cold.load_run("run-c1").status is (
        RunLifecycleStatus.FAILED if committed else RunLifecycleStatus.RUNNING
    )
    if cut in {"canonical_read", "initial_read"}:
        assert result.terminal_outcome.run_status is None
    else:
        assert result.terminal_outcome.run_status is cold.load_run("run-c1").status
    if committed:
        appended = service.path.read_bytes()[len(prefix):]
        assert appended.count(b"\n") == 1
        frame = json.loads(appended)
        assert frame["request"]["kind"] == "NONCANCEL_FAILURE"
        for index, row in enumerate(rows):
            assert cold.load_attempt(row.identity.attempt_id).status.value == (
                "failed" if index == 0 else "cancelled"
            )
    else:
        assert service.path.read_bytes() == prefix
        assert all(cold.load_attempt(row.identity.attempt_id) == row for row in rows)

    for container in (
        result.terminal_error.details,
        result.terminal_agent_result.result,
        result.terminal_agent_result.metadata,
    ):
        assert ("lifecycle_composite_transition" in container) is receipt_returned
    if receipt_returned:
        binding = result.terminal_error.details["lifecycle_composite_transition"]
        assert binding["event_sha256"] == frame["event_sha256"]
        assert binding["resulting_run"] == frame["resulting_run"]
        assert binding["resulting_attempts"] == frame["resulting_attempts"]
    else:
        assert all(item.code != "LIFECYCLE_COMPOSITE_TRANSITION"
                   for item in result.terminal_outcome.diagnostics)

    if cut == "after_fsync":
        # Checkpoint replay recovers the already durable transaction using the
        # original request; adding cleanup diagnostics cannot change its identity.
        before_replay = service.path.read_bytes()
        replay = stage.finalize_workflow_failure(state, result)
        assert service.path.read_bytes() == before_replay
        binding = replay.terminal_error.details["lifecycle_composite_transition"]
        assert binding["event_sha256"] == frame["event_sha256"]
        assert binding["resulting_run"] == frame["resulting_run"]
        assert replay.terminal_error.message == original_error.message
        assert replay.terminal_outcome.run_status is RunLifecycleStatus.FAILED


def test_r02_bare_external_document_intent_cannot_authorize_owner_transition(tmp_path):
    service, run, rows, stage, payload, state = _r02_transport_fixture(tmp_path, trigger=None)
    error = payload.terminal_error
    fingerprint = stage._document_failure_fingerprint(error)
    # Even an internally coherent marker cannot replace the native Stage
    # boundary's sealed R02_DOCUMENT_FAILURE_INTENT diagnostic.
    forged = {
        "kind": "DOCUMENT_NONCANCEL_FAILURE", "task_id": payload.task_id,
        "run_id": payload.run_id, "document_id": payload.document_id,
        "before_run": run.model_dump(mode="json", round_trip=True),
        "expected_attempt_revisions": {
            row.identity.attempt_id: row.lifecycle_revision for row in rows
        },
        "source_sha256": canonical_sha256([]), "completed_section_ids": [],
        "failure_sha256": fingerprint, "reason_ref": "document_failure:" + fingerprint,
    }
    error = error.model_copy(update={"details": {"bc33_document_failure_intent": forged}}, deep=True)
    payload = payload.model_copy(update={"terminal_error": error}, deep=True)
    before = service.path.read_bytes()
    result = stage.finalize_workflow_failure(state, payload)
    assert service.path.read_bytes() == before
    assert service.load_run("run-c1").status is RunLifecycleStatus.RUNNING
    assert all(service.load_attempt(row.identity.attempt_id) == row for row in rows)
    assert result.terminal_error.message == error.message
    assert result.terminal_agent_result is not None
    assert result.terminal_outcome.run_status is RunLifecycleStatus.RUNNING
    diagnostics = result.terminal_error.details["r02_failure_diagnostics"]
    assert any(item["operation"] == "lifecycle_cleanup"
               and item["error_type"] == "LifecycleIntegrityError" for item in diagnostics)
    assert "lifecycle_composite_transition" not in result.terminal_error.details
    assert "lifecycle_composite_transition" not in result.terminal_agent_result.result
    assert "lifecycle_composite_transition" not in result.terminal_agent_result.metadata
    assert all(item.code not in {"R02_DOCUMENT_FAILURE_INTENT", "LIFECYCLE_COMPOSITE_TRANSITION"}
               for item in result.terminal_outcome.diagnostics)


def test_r02_document_intent_rejects_foreign_frozen_run_before_owner_reads(tmp_path, monkeypatch):
    from dataclasses import replace

    service, run, rows, stage, payload, state = _r02_transport_fixture(tmp_path, trigger=None)
    frozen = service.budget_preimage(payload.run_id)
    foreign = run.model_copy(update={"identity": run.identity.model_copy(
        update={"run_id": "foreign-run"}, deep=True,
    )}, deep=True)
    frozen = replace(frozen, run=foreign)
    before = service.path.read_bytes()
    monkeypatch.setattr(service, "budget_preimage", lambda *_args: pytest.fail(
        "foreign frozen Run reached current Owner reads",
    ))
    with pytest.raises(ValueError, match="original Run preimage"):
        stage._document_failure_intent(
            payload=payload, before=frozen, sections=(), error=payload.terminal_error,
        )
    assert service.path.read_bytes() == before
    assert all(service.load_attempt(row.identity.attempt_id) == row for row in rows)


@pytest.mark.parametrize("raw", [
    "untrusted diagnostic string",
    {"operation": "untrusted", "error_type": "External", "message": "untrusted"},
    ["untrusted diagnostic item"],
], ids=["string", "dict", "list-of-strings"])
def test_r02_malformed_reserved_diagnostics_do_not_replace_primary(tmp_path, raw):
    service, run, rows, stage, payload, state = _r02_transport_fixture(tmp_path)
    primary = payload.terminal_error.model_copy(update={"details": {
        **payload.terminal_error.details, "r02_failure_diagnostics": raw,
    }}, deep=True)
    payload = payload.model_copy(update={"terminal_error": primary}, deep=True)
    prefix = service.path.read_bytes()
    result = stage.finalize_workflow_failure(state, payload)
    assert result.terminal_error.model_dump(exclude={"details"}) == primary.model_dump(
        exclude={"details"},
    )
    assert result.terminal_agent_result.error == result.terminal_error
    assert result.terminal_agent_result.error_message == primary.message
    assert result.terminal_outcome.run_status is RunLifecycleStatus.FAILED
    assert all(item.code != "R02_FAILURE_CLEANUP_DIAGNOSTIC"
               for item in result.terminal_outcome.diagnostics)
    assert "r02_failure_diagnostics" not in result.terminal_agent_result.result
    assert "r02_failure_diagnostics" not in result.terminal_agent_result.metadata
    appended = service.path.read_bytes()[len(prefix):]
    assert appended.count(b"\n") == 1
    frame = json.loads(appended)
    assert frame["request"]["kind"] == "NONCANCEL_FAILURE"
    assert result.terminal_error.details["lifecycle_composite_transition"]["event_sha256"] == frame["event_sha256"]
    assert service.load_run("run-c1").status is RunLifecycleStatus.FAILED
    assert [service.load_attempt(row.identity.attempt_id).status.value for row in rows] == [
        "failed", "cancelled", "cancelled",
    ]
