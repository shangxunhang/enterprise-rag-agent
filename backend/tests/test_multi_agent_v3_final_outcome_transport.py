"""352: actual production composition and bounded terminal transport."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_projector import public_terminal_result, project_terminal
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_requirement_adherence import RequirementAdherenceCompositionError
from apps.enterprise_document.services.post_write.control import ArtifactUsabilityIntegrityResolver
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from apps.enterprise_document.schemas.agent_rag_v1.budgets import BudgetReservationRefSchema, CanonicalBudgetVectorSchema
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory
from task.task_manager import JsonlTaskManager
import test_multi_agent_v3_workflow_recovery as w13
from test_phase7_document_closure_mainline import _run_fake


def test_real_application_retains_best_effort_terminal_outcome(tmp_path):
    summary, factory = _run_fake(tmp_path, run_id="lane352_best_effort")
    outcome = summary["terminal_outcome"]
    assert outcome["schema_version"] == "terminal_outcome_v1"
    assert outcome["control_action"] == "OUTPUT_BEST_EFFORT"
    assert outcome["quality_state"] == "UNRESOLVED"
    assert outcome["is_best_effort"] and outcome["is_partial"]
    assert outcome["publication_exists"] is True
    assert outcome["run_status"] == "success"
    assert summary["status"] == "partial_success"
    assert factory.engines[0].stage_service.target_post_write_composition is not None


@pytest.fixture(scope="module", params=["success", "block", "partial_resource", "resource", "technical", "fallback", "recovery", "release_failure", "persist_failure", "validation_incomplete"])
def actual_path(request, tmp_path_factory):
    scenario = request.param
    root = tmp_path_factory.mktemp("lane352-" + scenario)
    task_id, run_id = "task352-" + scenario, "run352-" + scenario
    project = w13._w13_current_new_task_project_input(w13._w13_single_section_project_input(task_id))
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False, trace_enabled=True,
        data_capture_enabled=True, gate_recovery_rounds_limit=0)
    factory = w13._W13InspectingFactory()
    original_generate = FakeLLMClient.generate
    original_usability = ArtifactUsabilityIntegrityResolver.resolve
    original_finalize = TargetPostWriteComposition._finalize_document_output_and_control
    original_section = TargetPostWriteComposition._section
    calls = []
    first_failure = []

    def provider(client, req):
        calls.append(req.model_call_id)
        response = original_generate(client, req)
        contract = req.extra.get("response_contract")
        if scenario == "validation_incomplete" and contract == "requirement_adherence_batch_v1":
            return response.model_copy(update={"content": "SECRET invalid structured validation response"})
        if scenario == "fallback" and contract == "criterion_support_evaluator_v1":
            value = json.loads(response.content)
            value.update(support_state="not_supported", evidence_ids=[], conflicts=[])
            return response.model_copy(update={"content": json.dumps(value)})
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": "应依据已核实的项目资料制定建设方案。" * 6 + "[C1]"})
        if contract in {"canonical_claim_extraction_v1", "requirement_adherence_batch_v1"}:
            value = json.loads(response.content)
            for row in value.get("units", []):
                row["accounting_state"] = "FACTUAL_CLAIMS_MATERIALIZED"
                row["claims"] = [{"quote": "应依据已核实的项目资料制定建设方案。", "occurrence_index": 0, "claim_type": "general_method"}]
            for row in value.get("results", []):
                row.update(observation="SATISFIED", reason_codes=[])
            return response.model_copy(update={"content": json.dumps(value)})
        if contract in {"canonical_claim_evidence_support_v1", "canonical_claim_evidence_support_v2"}:
            import test_multi_agent_v3_claim_support_mainline as support
            refs = [row["exact_source_ref"] for row in support._json_message(req, "support_evidence")["support_evidence"]]
            return response.model_copy(update={"content": json.dumps({"relation": "SUPPORTED",
                "supporting_refs": refs[:1], "contradicting_refs": [], "certainty_relation": "ALIGNED",
                "certainty_reason_codes": ["TEST_ALIGNED"]})})
        if contract == "citation_claim_association_v1":
            from test_multi_agent_v3_citation_grounding_mainline import _association_input
            material = _association_input(req)
            ids = [row["claim_target_id"] for row in material["candidate_claim_targets"]]
            return response.model_copy(update={"content": json.dumps({"associations": [
                {"occurrence_ref": ref, "state": "ASSOCIATED", "associated_claim_target_ids": ids}
                for ref in material["eligible_occurrence_refs"]]})})
        if contract == "canonical_claim_verifier_v2":
            import test_multi_agent_v3_claim_support_mainline as support
            shared = support._json_message(req, "shared_support")["shared_support"]
            allowed = support._json_message(req, "allowed_authority_refs")["allowed_authority_refs"]
            basis = shared["supporting_refs"][:1]
            assert shared["relation"] == "SUPPORTED" and basis
            assert all(ref in allowed for ref in basis)
            return response.model_copy(update={"content": json.dumps({
                "verdict": "VERIFIED", "positive_basis_refs": basis,
                "rationale": "Controlled independent validity oracle for terminal transport.",
                "issues": []})})
        if contract in {"artifact_consistency_relation_batch_v1", "artifact_consistency_shared_relation_batch_v1"}:
            value = json.loads(response.content)
            works = value.get("work_results", [value])
            for work in works:
                for row in work["results"]:
                    row.update(relation_materiality="NO_MATERIAL_RELATION", relation=None, ambiguity_codes=[])
            return response.model_copy(update={"content": json.dumps(value)})
        return response

    def usability(owner, **kwargs):
        if scenario == "block" and kwargs["artifact_state_ref"].artifact_scope.value == "DOCUMENT":
            # Controlled storage refusal. Real usability owner produces the proof;
            # real Gate/Control/Completion decide the terminal business outcome.
            with patch.object(owner.artifact_store, "resolve_exact",
                    side_effect=PostWritePersistenceIntegrityError("controlled exact artifact read refusal")):
                return original_usability(owner, **kwargs)
        return original_usability(owner, **kwargs)

    def exhaust(composition, task_id, run_id):
        manager = composition.budget_manager_provider(task_id, run_id)
        state = manager.canonical_state()
        ref = BudgetReservationRefSchema(reservation_id="352-controlled-denial", task_id=task_id,
            run_id=run_id, reservation_kind="logical_model_call", owner_occurrence_ref="352-denial",
            scope_ref="document", operation_ref="352-denial")
        manager.reserve(ref, CanonicalBudgetVectorSchema(logical_model_calls=state.limits.logical_model_calls + 1),
            expected_revision=state.revision)
        raise AssertionError("real Budget should have denied the reservation")

    def finalize(composition, **kwargs):
        if scenario == "partial_resource":
            exhaust(composition, kwargs["task_id"], kwargs["run_id"])
        if scenario == "technical":
            raise RequirementAdherenceCompositionError("SECRET C:/config/token stack trace",
                error_code="CONTROLLED_VALIDATION_FAILURE", failure_category=FailureCategory.VALIDATION)
        return original_finalize(composition, **kwargs)

    def section(composition, **kwargs):
        if scenario == "resource":
            exhaust(composition, kwargs["task_id"], kwargs["run_id"])
        return original_section(composition, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(ArtifactUsabilityIntegrityResolver, "resolve", usability))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_finalize_document_output_and_control", finalize))
        stack.enter_context(patch.object(TargetPostWriteComposition, "_section", section))
        if scenario == "release_failure":
            stack.enter_context(patch.object(TargetPostWriteComposition, "_publish_target_release",
                side_effect=RequirementAdherenceCompositionError("SECRET release interruption")))
        if scenario == "persist_failure":
            from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
            stack.enter_context(patch.object(SchemeGenerationStageService, "persist_deliverable",
                side_effect=RuntimeError("SECRET postpublication persistence failure")))
        if scenario == "recovery":
            from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
            original_stage = EnterpriseDocumentWorkflowEngine._run_stage

            def interrupt_once(engine, *args, **kwargs):
                if kwargs.get("node_name") == "evaluate_document_gate" and not first_failure:
                    first_failure.append("interrupted")
                    raise TimeoutError("controlled process interruption before terminal evaluation")
                return original_stage(engine, *args, **kwargs)
            stack.enter_context(patch.object(EnterpriseDocumentWorkflowEngine, "_run_stage", interrupt_once))
        service = MainlineApplicationService(supervisor_factory=factory)
        summary = service.run(project_root=Path(__file__).resolve().parents[2],
            task_id=task_id, run_id=run_id, user_input=project.user_query, output_root=root,
            clean_existing=True, settings=settings, project_input=project.model_dump(mode="json"),
            allow_demo_defaults=False, enable_agent_self_rag=False)
        if scenario == "recovery":
            assert first_failure and summary["supervisor_result"]["error"]
            first_failure.append(summary["supervisor_result"]["error"])
            summary = service.recover_run(project_root=Path(__file__).resolve().parents[2], task_id=task_id,
                run_id=run_id, output_root=root, settings=settings, allow_demo_defaults=True,
                enable_agent_self_rag=False, recovery_reason="controlled_process_interruption")
            from application.terminal_outcome_projector import opaque_ref
            assert opaque_ref(first_failure[1]) in summary["terminal_outcome"]["recovery_refs"]
    return scenario, root, summary, factory.engines[0], calls, settings


def test_actual_application_terminal_distinctions(actual_path):
    scenario, _, summary, engine, _, _ = actual_path
    outcome = summary["terminal_outcome"]
    assert engine.stage_service.target_post_write_composition is not None
    if scenario in {"success", "fallback", "recovery"}:
        assert outcome["status"] == "success", outcome
        assert outcome["quality_state"] == "PASS"
        assert outcome["publication_exists"]
        assert outcome["publication_ref"] == summary["target_release_publication"]["manifest"]["release_id"]
        if scenario == "fallback":
            assert outcome["fallback_occurred"]
        if scenario == "recovery":
            assert outcome["recovery_attempts"] == 1
            assert len(outcome["recovery_refs"]) >= 2
    elif scenario == "validation_incomplete":
        assert outcome["status"] == "OUTPUT_BEST_EFFORT"
        assert outcome["quality_state"] == "UNRESOLVED"
        assert outcome["is_partial"] and outcome["publication_exists"]
        assert any(d["failure_category"] == "validation" for d in outcome["diagnostics"])
    elif scenario == "block":
        assert outcome["status"] == "BLOCK", outcome
        assert outcome["run_status"] == "success"
        assert outcome["control_action"] == "BLOCK"
        assert summary["supervisor_result"]["error"] is None
    elif scenario in {"partial_resource", "resource"}:
        assert outcome["status"] == "resource_exhausted", outcome
        assert outcome["failure_origin"] == "canonical_budget"
        assert outcome["is_partial"] == (scenario == "partial_resource")
        assert bool(outcome["artifacts"]) == (scenario == "partial_resource")
        if outcome["artifacts"]:
            assert outcome["artifacts"][0]["content"]
            assert outcome["completed_scope_refs"]
        assert outcome["unfinished_scope_refs"]
    else:
        assert outcome["status"] == "TECHNICAL_FAILURE", outcome
        assert outcome["failure_category"] == ("unknown" if scenario == "persist_failure" else "validation")
        assert not outcome["artifacts"]
    if scenario == "release_failure":
        assert outcome["run_status"] == "success"
        assert outcome["control_action"] == "OUTPUT"
        assert outcome["execution_status"] == "failed"
        top = w13._latest_w13_top_level_payload(engine)
        assessment = engine.stage_service.target_post_write_composition.assessment_store.resolve_exact(
            top.target_run_completion_assessment_ref)
        assert assessment.post_write_control_decision_ref.exact_ref_payload == top.target_post_write_control_decision_ref.model_dump(mode="json")
    if scenario == "persist_failure":
        assert outcome["publication_exists"]
        assert outcome["run_status"] == "success"
        assert outcome["execution_status"] == "failed"
        assert "persist_deliverable" in outcome["unfinished_scope_refs"]
    elif scenario not in {"success", "fallback", "recovery", "validation_incomplete"}:
        assert not outcome["publication_exists"]
        assert summary["target_release_publication"] is None


def test_actual_continuation_wrapper_and_safe_boundary(actual_path):
    _, root, summary, _, _, _ = actual_path
    result = AgentResultSchema.model_validate(summary["supervisor_result"])
    continuation = MainlineApplicationService()._continuation_summary(
        workspace=SimpleNamespace(paths=lambda: summary["paths"]),
        task_manager=JsonlTaskManager(root / "tasks"), result=result)
    assert continuation["terminal_outcome"] == summary["terminal_outcome"]
    assert continuation["target_release_publication"] == summary["target_release_publication"]
    public = public_terminal_result(continuation)
    raw = json.dumps(public)
    for forbidden in ("SECRET", "C:/config", "stack_trace", "supervisor_result", "settings", "input_manifest", "control_decision_hash"):
        assert forbidden not in raw
    assert set(public) == {"task_id", "run_id", "status", "terminal_outcome"}


def test_terminal_checkpoint_replay_no_business_redispatch(actual_path):
    _, root, summary, engine, calls, settings = actual_path
    before = len(calls)
    from test_multi_agent_v3_citation_integrity_mainline import _durable_state
    from apps.enterprise_document.services.post_write.release import PostWriteReleasePublicationService
    durable_before = _durable_state(engine)
    with patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false"}), patch.object(
            FakeLLMClient, "generate", side_effect=AssertionError("terminal redispatch")), patch.object(
            PostWriteReleasePublicationService, "publish", side_effect=AssertionError("terminal republish")):
        replay = MainlineApplicationService().read_terminal_result(
            project_root=Path(__file__).resolve().parents[2], task_id=summary["task_id"], run_id=summary["run_id"],
            output_root=root, settings=settings, allow_demo_defaults=True, enable_agent_self_rag=False)
    assert len(calls) == before
    assert _durable_state(engine) == durable_before
    assert replay["terminal_outcome"] == summary["terminal_outcome"]
    assert replay["target_release_publication"] == summary["target_release_publication"]
    top = w13._latest_w13_top_level_payload(engine)
    assert top.terminal_agent_result.result["terminal_outcome"] == replay["terminal_outcome"]
    assert top.terminal_outcome.model_dump(mode="json") == replay["terminal_outcome"]


@pytest.mark.parametrize("category", list(FailureCategory))
def test_safe_diagnostics_preserve_typed_category_never_raw_text(category):
    error = ErrorSchema(error_code="SAFE_TEST", error_type="SECRET", message="SECRET C:/config/token",
        stack_trace="SECRET traceback", details={"failure_origin": "canonical_budget", "SECRET": "config"},
        failure_category=category, requires_human=True, requires_new_input=True, retryable=False)
    outcome = project_terminal(task_id="task", run_id="run", execution_status="failed", phase="validation", error=error)
    assert outcome.failure_category is category
    assert outcome.requires_human and outcome.requires_new_input
    assert "SECRET" not in outcome.model_dump_json()


def test_terminal_identity_detects_corrupt_replay_payload():
    from application.terminal_outcome_schema import TerminalOutcomeResultV1
    from pydantic import ValidationError
    outcome = project_terminal(task_id="t", run_id="r", execution_status="failed", phase="workflow")
    raw = outcome.model_dump(mode="json")
    raw["is_partial"] = True
    with pytest.raises(ValidationError, match="identity mismatch"):
        TerminalOutcomeResultV1.model_validate(raw)


def test_cli_consumes_actual_terminal_without_internal_dump(actual_path, capsys, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import run_demo
    _, _, summary, _, _, settings = actual_path
    def internal_run(**kwargs):
        print("SECRET C:/config/internal")
        return summary
    def persist(*args, **kwargs):
        assert kwargs["raise_on_validation_error"] is False
        return {"validation": {}, "validation_error": {"message": "SECRET traceback"}}
    monkeypatch.setattr(sys, "argv", ["run_demo", "--fake-runtime", "--strict", "--user-input", "test"])
    monkeypatch.setattr(run_demo, "_configure_runtime", lambda **kwargs: None)
    monkeypatch.setattr(run_demo, "get_settings", lambda **kwargs: settings)
    monkeypatch.setattr(run_demo, "run_demo", internal_run)
    monkeypatch.setattr(run_demo, "persist_end_to_end_artifacts", persist)
    with pytest.raises(SystemExit) as stopped:
        run_demo.main()
    assert stopped.value.code == 1
    console = capsys.readouterr()
    assert "SECRET" not in console.out + console.err
    public = json.loads(console.out)
    assert public == public_terminal_result(summary)


def test_terminal_read_enforces_existing_tenant_authorization(tmp_path, monkeypatch):
    from test_p0_existing_run_tenant_authorization import test_cross_tenant_is_rejected_before_runtime_or_sensitive_usage_load
    test_cross_tenant_is_rejected_before_runtime_or_sensitive_usage_load(
        tmp_path, monkeypatch, "read_terminal_result", {})


@pytest.mark.parametrize("change", [{"is_partial": False}, {"phase": "C:/config/token"}])
def test_schema_rejects_unsafe_or_flattened_content_even_if_rehashed(change):
    from application.terminal_outcome_schema import TerminalOutcomeResultV1
    from pydantic import ValidationError
    raw = project_terminal(task_id="t", run_id="r", execution_status="partial_success", phase="workflow").model_dump(mode="json")
    raw.update(change)
    raw["result_id"] = "sha256:" + canonical_sha256({k: v for k, v in raw.items() if k != "result_id"})
    with pytest.raises(ValidationError):
        TerminalOutcomeResultV1.model_validate(raw)
