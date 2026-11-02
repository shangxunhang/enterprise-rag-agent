"""H1: classify at the initial Writer owner, then preserve the committed error."""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import pytest

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionOutcome
from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_projector import project_terminal, public_terminal_result
from apps.enterprise_document.schemas.pre_write_writer import WriterExecutionFailureKind, WriterExecutionStatus
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import SchemeWriterRuntimeSupport
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import PreWriteWriterExecutionFailed
from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
from core.model_input_accounting import ModelContextBudgetExceeded
from core.retry_policy import RetryDecisionPolicy
from core.runtime.execution_control import WorkflowExecutionControl
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, WriterExecutionState
from schemas.common import ErrorSchema
from schemas.status import ExecutionStatus
import test_gate_v2_pre_write_writer_invocation as writer
import test_multi_agent_v3_parallel_budget_acceptance as capacity
from test_multi_agent_v3_parallel_budget_acceptance import capacity_workload
from test_step7_resource_terminal_transport import TERMINAL_READ_GUARDS


@pytest.mark.parametrize("allow", [True, False], ids=["normal", "fallback"])
@pytest.mark.parametrize("exception,category,kind,details", [
    (CanonicalBudgetCapacityExceeded("model_tokens_total", limit=24000, remaining=7688, required=31329),
     "resource_exhausted", "runtime_resource_failure",
     {"failure_origin": "canonical_budget", "resource": "model_tokens_total", "limit": 24000,
      "remaining": 7688, "required": 31329}),
    (CanonicalBudgetCapacityExceeded("logical_model_calls", limit=44, remaining=0, required=1),
     "resource_exhausted", "runtime_resource_failure",
     {"failure_origin": "canonical_budget", "resource": "logical_model_calls", "limit": 44,
      "remaining": 0, "required": 1}),
    (CanonicalBudgetDeadlineExceeded(datetime(2026, 9, 13, tzinfo=timezone.utc)),
     "resource_exhausted", "runtime_resource_failure",
     {"failure_origin": "canonical_budget", "deadline_at": "2026-09-13T00:00:00+00:00"}),
    (CanonicalBudgetDraining(), "resource_exhausted", "runtime_resource_failure",
     {"failure_origin": "canonical_budget"}),
    (ModelContextBudgetExceeded(profile_id="test-profile", input_tokens=200, output_tokens=100, window=250),
     "validation", "model_invocation_failure", {}),
    (ValueError("provider response decoder defect"), "unknown", "model_invocation_failure", {}),
    (RuntimeError("canonical budget capacity exceeded: logical_model_calls required=1 remaining=0 limit=44"),
     "unknown", "model_invocation_failure", {}),
    (RuntimeError("MODEL_CONTEXT_BUDGET_EXCEEDED: profile=test input=200 output=100 window=250"),
     "unknown", "model_invocation_failure", {}),
], ids=["token-capacity", "call-capacity", "deadline", "draining", "typed-context-validation",
        "ordinary-provider-value-error", "resource-message-lookalike", "context-message-lookalike"])
def test_initial_writer_error_survives_durable_result_and_workflow_carrier(
    tmp_path, allow, exception, category, kind, details,
):
    chain = writer._current_chain(allow=allow)
    calls, admission_checks = [], []
    section = writer._section_service(calls, responses=[writer._success_response()])

    def deny_at_model_boundary(self, shared_state, **kwargs):
        admission_checks.append(kwargs)
        raise exception

    section.model_service.call_model = MethodType(deny_at_model_boundary, section.model_service)
    store = SQLitePreWriteExecutionStore(tmp_path / "writer.sqlite3")
    service = writer._service(store=store, section_service=section)
    result = writer._execute(service, chain)
    persisted = SQLitePreWriteExecutionStore(store.database_path).get_writer_execution(result.logical_execution_key)
    assert persisted.state is WriterExecutionState.CONFIRMED_FAILED
    assert persisted.result == result
    assert writer._execute(service, chain) == result
    assert len(admission_checks) == 1 and calls == []
    assert result.status is WriterExecutionStatus.TECHNICAL_FAILURE and result.artifact is None
    assert result.failure_kind is WriterExecutionFailureKind(kind)
    error = ErrorSchema.model_validate(result.model_dump(mode="json")["error"])
    assert error.error_code == "TARGET_WRITER_EXECUTION_EXCEPTION"
    assert error.error_type == type(exception).__name__ and error.message == str(exception)
    assert error.failure_category.value == category
    assert error.details == details
    assert error.source.component == "PreWriteWriterInvocationService"
    assert error.retryable is False

    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    engine.stage_service = SimpleNamespace(runtime_support=SchemeWriterRuntimeSupport())
    engine.retry_policy = RetryDecisionPolicy()
    outcome = WorkflowNodeExecutionOutcome(kind="error", started_at="", finished_at="", latency_ms=0,
        execution_control=WorkflowExecutionControl(execution_id="prewrite-carrier", deadline_monotonic=0),
        exception=PreWriteWriterExecutionFailed(persisted.result))
    transported = engine._error_from_outcome(node_name="execute_sections", outcome=outcome)
    assert transported.model_dump(mode="json") == error.model_dump(mode="json")
    terminal = project_terminal(task_id=writer.TASK_ID, run_id=writer.RUN_ID,
        execution_status=ExecutionStatus.FAILED, phase="execute_sections", error=transported)
    assert terminal.status == ("resource_exhausted" if category == "resource_exhausted" else "TECHNICAL_FAILURE")
    assert terminal.failure_category.value == category
    assert terminal.failure_origin.value == details.get("failure_origin", "unknown")
    assert terminal.control_action is None and not terminal.publication_exists


def _durable_snapshot(root):
    files, databases = {}, {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix == ".lock" or path.name.endswith(("-wal", "-shm")):
            continue
        if path.read_bytes()[:16] == b"SQLite format 3\x00":
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                content = "\n".join(db.iterdump()).encode()
            databases[str(path.relative_to(root))] = hashlib.sha256(content).hexdigest()
        else:
            files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files, databases


def test_real_prewrite_capacity_admission_terminal_and_committed_read(
    tmp_path, monkeypatch, capacity_workload,
):
    """Natural scarce-capacity producer, real Writer/store/workflow, fake provider only."""
    observed = {}
    original = MainlineApplicationService.run

    def capture_run(service, **kwargs):
        observed["request"] = kwargs
        observed["summary"] = original(service, **kwargs)
        return observed["summary"]

    monkeypatch.setattr(MainlineApplicationService, "run", capture_run)
    capacity.test_default_policy_rejects_overlapping_capacity_before_second_provider(
        tmp_path, monkeypatch, 0, capacity_workload)
    summary, request = observed["summary"], observed["request"]
    root = request["output_root"]
    before = _durable_snapshot(root)
    with ExitStack() as stack:
        for owner, name in TERMINAL_READ_GUARDS:
            stack.enter_context(patch.object(owner, name, autospec=True,
                side_effect=AssertionError("committed read invoked " + owner.__name__ + "." + name)))
        for _ in range(2):
            replay = MainlineApplicationService().read_terminal_result(
                project_root=Path(__file__).resolve().parents[2], task_id=request["task_id"],
                run_id=request["run_id"], output_root=root, settings=request["settings"],
                allow_demo_defaults=True, enable_agent_self_rag=False)
            assert public_terminal_result(replay) == public_terminal_result(summary)
            assert _durable_snapshot(root) == before
    terminal = summary["terminal_outcome"]
    task_state = json.loads((root / "tasks" / (request["task_id"] + "_state.jsonl"))
                            .read_text(encoding="utf-8").splitlines()[-1])
    error = task_state["error"]
    # Preserve the actual result even during red runs; tests never relabel history.
    (tmp_path / "prewrite-natural-capacity-evidence.json").write_text(json.dumps({
        "terminal": terminal, "error": error, "terminal_reads": 2,
        "zero_write": True, "zero_redispatch": True,
    }, indent=2) + "\n", encoding="utf-8")
    assert terminal["status"] == terminal["failure_category"] == "resource_exhausted"
    assert terminal["failure_origin"] == "canonical_budget"
    assert error["details"]["failure_origin"] == "canonical_budget"
    assert error["details"]["resource"] == "model_tokens_total"
    assert error["details"]["limit"] == 24000
    assert error["details"]["required"] > error["details"]["remaining"]
    assert terminal["retryable"] is False
    assert terminal["control_action"] is None and not terminal["publication_exists"]
    assert summary["target_release_publication"] is None
