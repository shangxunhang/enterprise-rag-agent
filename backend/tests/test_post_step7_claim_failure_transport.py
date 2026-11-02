"""RPH-027: preserve declared Claim failures at the workflow transport boundary."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionOutcome
from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_projector import project_terminal, public_terminal_result
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    CanonicalBudgetCapacityExceeded, CanonicalBudgetDeadlineExceeded, CanonicalBudgetDraining,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    SUPPORT_RESPONSE_CONTRACT, TargetClaimSupportComposition, TargetClaimSupportError,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    VERIFIER_RESPONSE_CONTRACT, TargetClaimVerifierComposition, TargetClaimVerifierError,
)
from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import TerminalOutcomeStageFailure
from apps.enterprise_document.services.scheme_writer.agent_integration import (
    MainlinePlannerFailure, MainlineSectionAgentFailure,
)
from apps.enterprise_document.services.scheme_writer.runtime_support import SchemeWriterRuntimeSupport
from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageFailure
from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
from core.config import get_settings
from core.error_factory import ErrorFactory
from core.retry_policy import RetryDecisionPolicy
from core.runtime.execution_control import WorkflowExecutionControl
from model_gateway.fake_llm_client import FakeLLMClient
from rag.services.rag_service import FakeRAGService
from schemas.failure import FailureCategory, FailureOrigin
from schemas.rag import EvidenceBundleSchema, RetrievalAccessScopeSchema
from schemas.status import ExecutionStatus
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests
from test_step7_resource_terminal_transport import TERMINAL_READ_GUARDS


# Exact non-secret failure fields in R9 candidate 2b542ff5...029b88.json.
# The retained envelope SHA256 is recorded in the RPH-027 repository issue.
R9_FAILURE = {
    "message": "Request timed out.",
    "error_code": "MODEL_GATEWAY_CALL_FAILED",
    "category": "timeout",
    "origin": "provider_runtime",
}


def _transport(exception, policy=None):
    engine = object.__new__(EnterpriseDocumentWorkflowEngine)
    engine.stage_service = SimpleNamespace(runtime_support=SchemeWriterRuntimeSupport())
    engine.retry_policy = policy or RetryDecisionPolicy()
    outcome = WorkflowNodeExecutionOutcome(
        kind="error", started_at="", finished_at="", latency_ms=0,
        execution_control=WorkflowExecutionControl(execution_id="claim-failure", deadline_monotonic=0),
        exception=exception,
    )
    return engine._error_from_outcome(node_name="execute_sections", outcome=outcome)


def _terminal(error):
    return project_terminal(
        task_id="task-claim-transport", run_id="run-claim-transport",
        execution_status=ExecutionStatus.RETRYABLE_FAILED if error.retryable else ExecutionStatus.FAILED,
        phase="execute_sections", error=error,
    )


def test_exact_r9_failed_support_candidate_facts_reach_terminal():
    failure = R9_FAILURE
    error = _transport(TargetClaimSupportError(
        failure["message"], error_code=failure["error_code"],
        failure_category=failure["category"], failure_origin=failure["origin"],
    ))
    terminal = _terminal(error)
    assert error.error_code == failure["error_code"]
    assert error.error_type == "TargetClaimSupportError" and error.message == failure["message"]
    assert error.failure_category.value == terminal.failure_category.value == failure["category"]
    assert error.details["failure_origin"] == terminal.failure_origin.value == failure["origin"]
    assert error.retryable and error.recoverable and error.requires_human and not error.requires_new_input
    assert terminal.status == "TECHNICAL_FAILURE" and terminal.execution_status == "retryable_failed"
    assert terminal.control_action is None and not terminal.publication_exists


@pytest.mark.parametrize("carrier", [TargetClaimSupportError, TargetClaimVerifierError], ids=["support", "verifier"])
@pytest.mark.parametrize("category,origin,retryable", [
    ("timeout", "provider_runtime", True),
    ("availability", "provider_runtime", True),
    ("transient", "provider_runtime", True),
    ("validation", "unknown", False),
    ("unknown", "unknown", False),
    ("resource_exhausted", "canonical_budget", True),
], ids=["timeout", "availability", "transient", "validation", "unknown", "canonical-budget"])
def test_typed_claim_carrier_preserves_facts_and_existing_retry_policy(carrier, category, origin, retryable):
    exception = carrier("Typed owner failure.", error_code="DECLARED_CLAIM_FAILURE",
                        failure_category=category, failure_origin=origin)
    error = _transport(exception)
    terminal = _terminal(error)
    assert error.error_code == exception.error_code
    assert error.failure_category.value == category
    assert error.details["failure_origin"] == terminal.failure_origin.value == origin
    assert error.retryable is retryable
    assert error.retryable == RetryDecisionPolicy().should_retry(category)
    assert (error.recoverable, error.requires_human, error.requires_new_input) == (True, True, False)
    assert terminal.failure_category.value == category
    assert terminal.status == ("resource_exhausted" if category == "resource_exhausted" else "TECHNICAL_FAILURE")
    assert terminal.control_action is None and not terminal.artifact_exists and not terminal.publication_exists


@pytest.mark.parametrize("carrier", [TargetClaimSupportError, TargetClaimVerifierError], ids=["support", "verifier"])
def test_claim_transport_uses_selected_graph_retry_policy(carrier):
    error = _transport(carrier("Unknown owner failure.", failure_category="unknown"),
                       RetryDecisionPolicy(unknown_retryable=True))
    assert error.retryable is True
    assert error.failure_category is FailureCategory.UNKNOWN
    assert error.details["failure_origin"] == "unknown"


@pytest.mark.parametrize("message", [
    "MODEL_GATEWAY_CALL_FAILED provider_runtime Request timed out.",
    "canonical_budget resource_exhausted CLAIM_SUPPORT_FAILED",
])
def test_untyped_message_lookalikes_do_not_gain_declared_origin(message):
    error = _transport(RuntimeError(message))
    terminal = _terminal(error)
    assert error.error_code == "SCHEME_GENERATION_STAGE_FAILED"
    assert error.failure_category is FailureCategory.UNKNOWN
    assert terminal.failure_origin is FailureOrigin.UNKNOWN
    assert not error.retryable


@pytest.mark.parametrize("exception", [
    CanonicalBudgetCapacityExceeded("logical_model_calls", limit=44, remaining=0, required=1),
    CanonicalBudgetDeadlineExceeded(datetime(2026, 9, 24, tzinfo=timezone.utc)),
    CanonicalBudgetDraining(),
], ids=["capacity", "deadline", "draining"])
def test_existing_bare_budget_transport_remains_nonretryable(exception):
    error = _transport(exception)
    terminal = _terminal(error)
    assert error.failure_category is FailureCategory.RESOURCE_EXHAUSTED
    assert terminal.failure_origin is FailureOrigin.CANONICAL_BUDGET
    assert terminal.status == "resource_exhausted" and not error.retryable


@pytest.mark.parametrize("carrier", [
    SchemeGenerationStageFailure, MainlineSectionAgentFailure, MainlinePlannerFailure,
    lambda error: TerminalOutcomeStageFailure(error, _terminal(error)),
], ids=["stage", "section-agent", "planner", "terminal-outcome"])
def test_existing_structured_carriers_are_preserved_exactly(carrier):
    original = ErrorFactory().create(
        error_code="EXACT_OWNER_FAILURE", error_type="OwnerFailure", message="Typed exact error.",
        component="ExistingOwner", failure_category="timeout", retryable=False,
        recoverable=False, requires_human=False, requires_new_input=True,
        details={"failure_origin": "provider_runtime", "original_marker": "preserve"},
    )
    assert _transport(carrier(original)).model_dump(mode="json") == original.model_dump(mode="json")


def _durable_snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix == ".lock" or path.name.endswith(("-wal", "-shm")):
            continue
        raw = path.read_bytes()
        if raw[:16] == b"SQLite format 3\x00":
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                raw = "\n".join(db.iterdump()).encode("utf-8")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    return result


@pytest.mark.parametrize("owner", ["support", "verifier"])
def test_actual_claim_owner_failed_receipt_terminal_and_committed_read(tmp_path, owner):
    """Real owner/store/Gateway/workflow; only the external provider is fake."""
    root = tmp_path / owner
    task_id, run_id = "task-h10-" + owner, "run-h10-" + owner
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    scope = RetrievalAccessScopeSchema(tenant_id=project.tenant_id, authorized_kb_ids=["technical_reference_kb"])
    factory = workflow_tests._W13InspectingFactory()
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False, trace_enabled=True,
        data_capture_enabled=True, gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    target_contract = SUPPORT_RESPONSE_CONTRACT if owner == "support" else VERIFIER_RESPONSE_CONTRACT
    target_class = TargetClaimSupportComposition if owner == "support" else TargetClaimVerifierComposition
    method = "support_bindings" if owner == "support" else "verify_claims"
    error_class = TargetClaimSupportError if owner == "support" else TargetClaimVerifierError
    original_owner = getattr(target_class, method)
    original_generate = FakeLLMClient.generate
    original_retrieve = FakeRAGService.retrieve
    original_put = FileArtifactStore.put_json
    calls, failures, observed = [], [], {}

    def evidence_fixture(service, request):
        raw = original_retrieve(service, request).model_dump(mode="json")
        for item in [*raw["item_assessments"], raw["assessment"]]:
            item["supported_claim_types"] = ["general_method"]
            item["forbidden_claim_types"] = sorted(set(item.get("forbidden_claim_types", [])) | {"current_project_quantity"})
        return EvidenceBundleSchema.model_validate(raw)

    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original_generate(client, request)
        contract = request.extra.get("response_contract")
        if request.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": support_tests.TEXT * 6}, deep=True)
        if contract == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(request)["coverage_units"]
            payload = {"units": [{"unit_id": item["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                "claims": [{"quote": support_tests.TEXT, "occurrence_index": 0, "claim_type": "general_method"}]}
                for item in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        if contract == target_contract:
            error = ErrorFactory().create(error_code=R9_FAILURE["error_code"],
                error_type="SanitizedProviderInvocationError", message=R9_FAILURE["message"],
                component="ModelGateway", failure_category=R9_FAILURE["category"],
                retryable=True, recoverable=True, requires_human=True)
            return response.model_copy(update={"success": False, "error": error,
                "error_message": error.message, "failure_origin": FailureOrigin.PROVIDER_RUNTIME}, deep=True)
        if contract == SUPPORT_RESPONSE_CONTRACT:
            refs = [item["exact_source_ref"] for item in support_tests._json_message(request, "support_evidence")["support_evidence"]]
            assert refs
            return response.model_copy(update={"content": json.dumps({"relation": "SUPPORTED",
                "supporting_refs": refs[:1], "contradicting_refs": [], "certainty_relation": "ALIGNED",
                "certainty_reason_codes": ["TEST_CERTAINTY_ALIGNED"]})}, deep=True)
        return response

    def persist(store, value, **kwargs):
        ref = original_put(store, value, **kwargs)
        if isinstance(value, dict) and value.get("outcome") == "failed" and kwargs.get("artifact_type") in {
            "claim_support_candidate", "canonical_verifier_candidate",
        }:
            failures.append((store, ref, json.loads(json.dumps(value))))
        return ref

    def observe(composer, **kwargs):
        with pytest.raises(error_class) as first:
            original_owner(composer, **kwargs)
        before = _durable_snapshot(composer.store.root)
        call_count = len(calls)
        with pytest.raises(error_class) as replay:
            original_owner(composer, **kwargs)
        assert _durable_snapshot(composer.store.root) == before
        assert len(calls) == call_count
        assert (replay.value.error_code, replay.value.failure_category, replay.value.failure_origin) == (
            first.value.error_code, first.value.failure_category, first.value.failure_origin)
        assert replay.value.failure_origin is FailureOrigin.PROVIDER_RUNTIME
        assert composer.claim_runtime.resolve_claim_head(kwargs["records"][0].claim_ref) is None
        observed["owner_replay"] = True
        raise replay.value

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeRAGService, "retrieve", evidence_fixture))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(FileArtifactStore, "put_json", persist))
        stack.enter_context(patch.object(target_class, method, observe))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=root, settings=settings,
            project_input=project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False,
            retrieval_access_scope=scope,
        )

    assert observed.get("owner_replay") is True
    assert len([request for request in calls if request.extra.get("response_contract") == target_contract]) == 1
    assert len(failures) == 1 and failures[0][2]["failure"] == R9_FAILURE
    store, ref, candidate = failures[0]
    persisted_ref, persisted = store.get_json_with_reference(
        ref.artifact_ref, task_id=ref.task_id, run_id=ref.run_id, section_id=ref.section_id,
        attempt_id=ref.attempt_id, input_snapshot_version=ref.input_snapshot_version,
    )
    assert persisted_ref == ref and persisted == candidate
    before = _durable_snapshot(root)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false"}))
        for guarded_owner, name in TERMINAL_READ_GUARDS:
            stack.enter_context(patch.object(guarded_owner, name, autospec=True,
                side_effect=AssertionError("terminal read invoked " + guarded_owner.__name__ + "." + name)))
        for _ in range(2):
            replay = MainlineApplicationService().read_terminal_result(
                project_root=Path(__file__).resolve().parents[2], task_id=task_id, run_id=run_id,
                output_root=root, settings=settings, allow_demo_defaults=False, enable_agent_self_rag=False,
                retrieval_access_scope=scope)
            assert public_terminal_result(replay) == public_terminal_result(summary)
            assert _durable_snapshot(root) == before
    error = summary["task_state"]["error"]
    terminal = summary["terminal_outcome"]
    # Save original result and replay facts even when the final transport assertions are red.
    (tmp_path / (owner + "-failure-evidence.json")).write_text(json.dumps({
        "candidate_ref": ref.model_dump(mode="json"), "failure": candidate["failure"],
        "task_error": error, "terminal_outcome": terminal, "owner_failed_replay_without_redispatch": True,
        "terminal_read_count": 2, "terminal_reads_zero_writes_and_redispatch": True,
    }, indent=2) + "\n", encoding="utf-8")
    assert error["error_code"] == R9_FAILURE["error_code"]
    assert error["failure_category"] == terminal["failure_category"] == "timeout"
    assert error["details"]["failure_origin"] == terminal["failure_origin"] == "provider_runtime"
    assert error["retryable"] is True and error["recoverable"] is True and error["requires_human"] is True
    assert error["requires_new_input"] is False
    assert terminal["status"] == "TECHNICAL_FAILURE" and terminal["execution_status"] == "retryable_failed"
    assert terminal["control_action"] is None and not terminal["publication_exists"]
    assert summary["target_release_publication"] is None
