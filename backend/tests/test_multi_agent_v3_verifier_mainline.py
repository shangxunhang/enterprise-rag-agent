"""342: real canonical Verifier candidate, source guards and atomic M6 commit."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.agents.agent_rag_v1.verifier import VerifierAgent
from apps.enterprise_document.schemas.agent_rag_v1.verification import VerificationVerdict
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    TargetClaimVerifierComposition, TargetClaimVerifierError, VERIFIER_RESPONSE_CONTRACT,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimBindingError
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.context import ContextPackageSchema
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests

TEXT = "应依据已核实的项目资料制定建设方案。"
_SCENARIOS = ("VERIFIED", "CONFLICTED", "UNSUPPORTED", "REJECTED", "unsupported-cannot-verify",
              "bad-json", "wrong-identity", "provider-failure", "commit-window", "intent-only", "concurrent", "budget-denied")


class _StoppedVerifier(BaseException):
    pass


@pytest.fixture(scope="module", params=_SCENARIOS)
def verifier_mainline(tmp_path_factory, request):
    scenario = request.param
    root = tmp_path_factory.mktemp("verifier342-" + scenario)
    task_id, run_id = "task-verifier-" + scenario, "run-verifier-" + scenario
    project = workflow_tests._w13_current_new_task_project_input(
        workflow_tests._w13_single_section_project_input(task_id))
    calls, observations, agent_invocations = [], [], []
    original_generate = FakeLLMClient.generate
    original_verify = TargetClaimVerifierComposition.verify_claims
    original_agent = VerifierAgent.evaluate_canonical_claim

    def agent_entry(invocation, *, evaluator):
        agent_invocations.append(invocation)
        return original_agent(invocation, evaluator=evaluator)

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        purpose = req.extra.get("response_contract")
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if purpose == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {"units": [{"unit_id": item["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                       "claims": [{"quote": TEXT, "occurrence_index": 0, "claim_type": "general_method"}]} for item in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        if purpose == support_tests.SUPPORT_RESPONSE_CONTRACT:
            refs = [item["exact_source_ref"] for item in support_tests._json_message(req, "support_evidence")["support_evidence"]]
            assert refs
            relation = "CONTRADICTED" if scenario == "CONFLICTED" else "NOT_SUPPORTED" if scenario == "unsupported-cannot-verify" else "SUPPORTED"
            payload = {"relation": relation, "supporting_refs": refs[:1] if relation == "SUPPORTED" else [],
                       "contradicting_refs": refs[:1] if relation == "CONTRADICTED" else [],
                       "certainty_relation": "ALIGNED",
                       "certainty_reason_codes": ["TEST_CERTAINTY_ALIGNED"]}
            return response.model_copy(update={"content": json.dumps(payload)}, deep=True)
        if purpose != VERIFIER_RESPONSE_CONTRACT:
            return response
        if scenario == "intent-only":
            raise _StoppedVerifier("test verifier process loss")
        if scenario == "bad-json":
            return response.model_copy(update={"content": "bad JSON"}, deep=True)
        if scenario == "wrong-identity":
            return response.model_copy(update={"task_id": "another-task"}, deep=True)
        if scenario == "provider-failure":
            return response.model_copy(update={"success": False, "error_message": "test verifier unavailable"}, deep=True)
        verdict = scenario if scenario in {item.value for item in VerificationVerdict} else "VERIFIED"
        basis = support_tests._json_message(req, "allowed_authority_refs")["allowed_authority_refs"]
        payload = {"verdict": verdict, "positive_basis_refs": [next(ref for ref in basis if not ref.startswith("writer-project-input-basis:"))] if scenario == "unsupported-cannot-verify" else [next(ref for ref in basis if ref.startswith("writer-project-input-basis:"))] if verdict == "VERIFIED" else [], "rationale": "Controlled exact Claim-validity test result.",
                   "issues": [] if verdict == "VERIFIED" else [{"issue_code": "CONTROLLED_VALIDITY_ISSUE", "severity": "MAJOR",
                   "message": "The test supplied an unresolved validity condition.", "source_refs": []}]}
        return response.model_copy(update={"content": json.dumps(payload)}, deep=True)

    def verifier_calls():
        return [req for req in calls if req.extra.get("call_purpose") == "claim_verification"]

    def observe(composer, *, records, binding_records, support_records, operation_context):
        assert len(records) == len(binding_records) == len(support_records) == 1
        kwargs = dict(records=records, binding_records=binding_records, support_records=support_records,
                      operation_context=operation_context)
        if scenario == "budget-denied":
            from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
            from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
            original_reserve = CanonicalInvocationBudgetAdapter.reserve_provider_attempt
            def deny(adapter, req):
                if req.extra.get("call_purpose") == "claim_verification":
                    raise CanonicalBudgetCapacityExceeded("model_tokens_total", limit=0, remaining=0, required=1)
                return original_reserve(adapter, req)
            with patch.object(CanonicalInvocationBudgetAdapter, "reserve_provider_attempt", deny):
                with pytest.raises(TargetClaimVerifierError) as first:
                    original_verify(composer, **kwargs)
            with pytest.raises(TargetClaimVerifierError) as replay:
                original_verify(composer, **kwargs)
            assert replay.value.failure_category.value == first.value.failure_category.value == "resource_exhausted"
            assert replay.value.failure_origin.value == first.value.failure_origin.value == "canonical_budget"
            assert not verifier_calls()
            observations.append((composer, records[0], binding_records[0], support_records[0], None))
            raise replay.value
        if scenario in {"bad-json", "wrong-identity", "provider-failure", "intent-only", "unsupported-cannot-verify"}:
            expected = _StoppedVerifier if scenario == "intent-only" else ClaimBindingError if scenario == "unsupported-cannot-verify" else TargetClaimVerifierError
            with pytest.raises(expected) as first:
                original_verify(composer, **kwargs)
            expected_replay = ClaimBindingError if scenario == "unsupported-cannot-verify" else TargetClaimVerifierError
            with pytest.raises(expected_replay) as replay:
                original_verify(composer, **kwargs)
            assert len(verifier_calls()) == 1
            assert composer.claim_runtime.resolve_claim_head(records[0].claim_ref) is None
            if scenario == "intent-only":
                assert replay.value.error_code == "VERIFIER_INDETERMINATE_EXECUTION"
            elif scenario != "unsupported-cannot-verify":
                assert str(first.value) == str(replay.value)
            observations.append((composer, records[0], binding_records[0], support_records[0], None))
            raise replay.value
        if scenario == "commit-window":
            with patch.object(composer.claim_runtime, "commit_verification", side_effect=RuntimeError("test verification commit window")):
                with pytest.raises(RuntimeError, match="commit window"):
                    original_verify(composer, **kwargs)
            assert len(verifier_calls()) == 1
            assert composer.claim_runtime.resolve_claim_head(records[0].claim_ref) is None
        if scenario == "concurrent":
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            other = TargetClaimVerifierComposition(claim_runtime=composer.claim_runtime,
                     support_composition=composer.support, model_gateway=composer.model_gateway, writer_execution_store=composer.writer_execution_store)
            barrier = Barrier(2, timeout=10)
            def run(instance):
                barrier.wait()
                return original_verify(instance, **kwargs)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(run, composer), pool.submit(run, other)
                result = a.result(timeout=30)
                assert b.result(timeout=30) == result
        else:
            result = original_verify(composer, **kwargs)
        assert len(verifier_calls()) == 1
        before = composer.claim_runtime.ledger_path.read_bytes()
        other = TargetClaimVerifierComposition(claim_runtime=composer.claim_runtime,
                 support_composition=composer.support, model_gateway=composer.model_gateway, writer_execution_store=composer.writer_execution_store)
        repeated = original_verify(other, **kwargs)
        assert repeated == result
        assert composer.claim_runtime.ledger_path.read_bytes() == before
        assert len(verifier_calls()) == 1
        assert len(agent_invocations) == 1
        # M6 invokes this guard while holding its own lock; no Claim/Support re-entry.
        with patch.object(composer.claim_runtime, "resolve_claim", side_effect=AssertionError("Claim lock re-entry")):
            with patch.object(composer.support.runtime, "resolve_support", side_effect=AssertionError("Support lock re-entry")):
                composer.validate_verification_sources(binding_records[0], support_records[0], operation_context.active_lineage)
        observations.append((composer, records[0], binding_records[0], support_records[0], result[0]))
        return result

    factory = workflow_tests._W13InspectingFactory()
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm", supervisor_model_name="fake_llm",
        enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True, gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(VerifierAgent, "evaluate_canonical_claim", staticmethod(agent_entry)))
        stack.enter_context(patch.object(TargetClaimVerifierComposition, "verify_claims", observe))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=root, clean_existing=True, settings=settings,
            project_input=project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    if not observations:
        error = workflow_tests._latest_w13_top_level_payload(factory.engines[0]).terminal_error
        raise AssertionError(error.model_dump(mode="json") if error else summary["status"])
    return scenario, observations[0], calls, agent_invocations, summary, factory.engines[0]


def test_real_verifier_commits_exact_verdict_and_preserves_failures(verifier_mainline):
    scenario, observation, calls, invocations, summary, engine = verifier_mainline
    composer, claim, binding, support, result = observation
    failed = scenario in {"bad-json", "wrong-identity", "provider-failure", "intent-only", "unsupported-cannot-verify", "budget-denied"}
    requests = [req for req in calls if req.extra.get("call_purpose") == "claim_verification"]
    assert len(requests) == (0 if scenario == "budget-denied" else 1)
    assert composer.claim_runtime.resolve_claim(claim.claim_ref) == claim
    assert composer.claim_runtime.resolve_binding_facts(binding.binding_facts_ref) == binding
    assert composer.support.runtime.resolve_support(support.support_ref) == support
    if failed:
        assert summary["status"] == ("retryable_failed" if scenario == "budget-denied" else "failed")
        assert result is None
        assert composer.claim_runtime.resolve_claim_head(claim.claim_ref) is None
        assert summary.get("target_release_publication") is None
        return
    expected = scenario if scenario in {item.value for item in VerificationVerdict} else "VERIFIED"
    assert result.verification_result.verdict.value == expected
    assert result.verification_revision.verification_revision == 1
    assert result.resulting_claim_head.head_revision == 1
    assert composer.claim_runtime.resolve_verification_result(result.verification_result.verification_result_ref) == result.verification_result
    payload = workflow_tests._latest_w13_section_payload(engine)
    assert payload.retrieval_metadata["canonical_claim_verification_refs"] == [result.verification_result.verification_result_ref.model_dump(mode="json")]
    assert payload.retrieval_metadata["canonical_claim_verifier_invoked"] is True
    assert summary["status"] == "partial_success", "Claim verification alone is not all document-quality validators"
    request = requests[0]
    raw_input = [ref for ref in composer.store.list_references() if ref.artifact_type == "canonical_verifier_input"]
    assert len(raw_input) == 1
    receipt = composer.store.get_json(raw_input[0].artifact_ref, task_id=request.task_id, run_id=request.run_id)
    package = ContextPackageSchema.model_validate(receipt["context_package"])
    actual = {"input_kind": "MESSAGES", "messages": request.messages}
    assert package.model_input.final_input_material.inline_final_input_payload == actual
    assert package.model_input.final_model_input_sha256 == canonical_sha256(actual)
    assert receipt["input_material"]["paragraph_text"] == TEXT * 6
    assert invocations[0].claim_ref == claim.claim_ref
    assert invocations[0].claim_evidence_support_ref == support.support_ref
    assert len(result.verification_result.issue_refs) == (0 if expected == "VERIFIED" else 1)


def _verifier_input(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import VerifierClaimInputSchema
    import test_multi_agent_v3_claim_verification_runtime as owner_tests
    env, _, claim, binding, _, support, _ = owner_tests._setup_verification_chain(tmp_path)
    composer = env["verifier"]
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
    return composer.build_input(claim, binding, support, ClaimOperationContextSchema(
        active_lineage=env["lineage"], authorization_ref="test:initial-claim", operation_purpose="test"))[0]



@pytest.mark.parametrize("defect", ["blank-rationale", "unknown-verdict", "extra-support", "missing-issues",
                                    "bad-severity", "blank-message", "bad-json", "duplicate-json-key"])
def test_bad_verifier_semantics_cannot_form_canonical_candidate(tmp_path, defect):
    invocation = _verifier_input(tmp_path)
    reply = {"verdict": "UNSUPPORTED", "positive_basis_refs": [], "rationale": "Not proven", "issues": []}
    if defect == "blank-rationale":
        reply["rationale"] = " "
    elif defect == "unknown-verdict":
        reply["verdict"] = "PASS"
    elif defect == "extra-support":
        reply["support_relation"] = "SUPPORTED"
    elif defect == "missing-issues":
        del reply["issues"]
    elif defect in {"bad-severity", "blank-message"}:
        reply["issues"] = [{"issue_code": "TEST", "message": " " if defect == "blank-message" else "not proven",
                            "severity": "anything" if defect == "bad-severity" else "MAJOR", "source_refs": []}]
    elif defect == "bad-json":
        reply = "broken"
    else:
        reply = '{"verdict":"VERIFIED","verdict":"UNSUPPORTED","rationale":"test","issues":[]}'
    with pytest.raises(ValueError):
        TargetClaimVerifierComposition.parse_candidate(reply, invocation=invocation, occurrence="test-verifier", receipt_ref="artifact://test-verifier")


def test_verifier_agent_rejects_candidate_for_another_claim(tmp_path):
    invocation = _verifier_input(tmp_path)
    candidate, _ = TargetClaimVerifierComposition.parse_candidate(
        {"verdict": "UNSUPPORTED", "positive_basis_refs": [], "rationale": "Not proven", "issues": []},
        invocation=invocation, occurrence="test-verifier", receipt_ref="artifact://test-verifier",
    )
    wrong_claim = candidate.claim_ref.model_copy(update={"claim_id": "another-claim"})
    with pytest.raises(ValueError, match="exact input|semantic input hash mismatch"):
        VerifierAgent.evaluate_canonical_claim(invocation, evaluator=lambda _input: candidate.model_copy(update={"claim_ref": wrong_claim}))
    assert VerifierAgent.evaluate_canonical_claim(invocation, evaluator=lambda _input: candidate) == candidate


@pytest.mark.parametrize("section_count,claims_per_section", ((4, 1), (8, 1), (8, 2)))
def test_default_budget_measures_eight_section_semantic_workload(tmp_path, section_count, claims_per_section):
    """Measure supported headroom and scarcity without changing the startup policy."""
    from test_phase7_document_closure_mainline import _run_fake
    from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyLoader
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
        BudgetManager, CanonicalBudgetCapacityExceeded,
    )
    original = FakeLLMClient.generate
    original_reserve = BudgetManager.reserve
    calls = []
    capacity_refusals = []
    policy_path = Path(__file__).resolve().parents[1] / "apps/enterprise_document/config/grounded_generation_v1.yaml"
    original_policy = policy_path.read_bytes()
    policy = GroundedGenerationPolicyLoader().load(policy_path)
    assert policy.canonical_budget.logical_model_calls == 44
    assert policy.canonical_budget.model_tokens_total == 24000

    def observe_capacity(manager, reservation, requested, *, expected_revision=None):
        try:
            return original_reserve(manager, reservation, requested, expected_revision=expected_revision)
        except CanonicalBudgetCapacityExceeded as exc:
            state = manager.canonical_state()
            capacity_refusals.append({
                "exception_type": type(exc).__name__, "message": str(exc),
                "resource": exc.resource, "limit": exc.limit,
                "remaining": exc.remaining, "required": exc.required,
                "reservation_ref": reservation.model_dump(mode="json"),
                "requested": requested.model_dump(mode="json"),
                "native_limits": state.limits.model_dump(mode="json"),
                "native_used": state.used.model_dump(mode="json"),
                "native_remaining": state.remaining.model_dump(mode="json"),
                "native_active_reserved": state.active_reserved.model_dump(mode="json"),
                "run_id": manager.run_id,
            })
            raise

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if req.extra.get("response_contract") == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {"units": [{"unit_id": unit["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                       "claims": [{"quote": TEXT, "occurrence_index": i, "claim_type": "general_method"}
                                  for i in range(claims_per_section)]} for unit in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        return response
    run_id = f"semantic-default-budget342-{section_count}-{claims_per_section}"
    project = None
    if section_count < 8:
        from application.project_input_factory import ProjectInputFactory
        from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
        raw = ProjectInputFactory().build(
            f"task_{run_id}", "生成企业级 RAG-Agent 系统建设方案", allow_demo_defaults=True
        ).model_dump(mode="json")
        titles = raw["generation_requirements"]["required_sections"][:section_count]
        raw["generation_requirements"]["required_sections"] = titles
        raw["output_schema"]["required_sections"] = titles
        raw["generation_requirements"]["citation_required_sections"] = [
            title for title in raw["generation_requirements"]["citation_required_sections"] if title in titles
        ]
        project = ProjectInputSchema.model_validate(raw)
        assert project.schema_version == "project_input_v3"
    with patch.dict(os.environ, {"GROUNDED_GENERATION_POLICY_FILE": str(policy_path)}):
        summary, factory = _run_fake(tmp_path, run_id=run_id, project_input=project,
            patchers=(patch.object(FakeLLMClient, "generate", provider),
                      patch.object(BudgetManager, "reserve", observe_capacity)))
    engine = factory.engines[0]
    top = workflow_tests._latest_w13_top_level_payload(engine)
    assert len(top.document_plan.sections) == section_count
    assert policy_path.read_bytes() == original_policy
    writers = [req for req in calls if req.extra.get("call_purpose") == "scheme_section_generation"]
    verifiers = [req for req in calls if req.extra.get("call_purpose") == "claim_verification"]
    budget = engine.budget_runtime(top.task_id, top.run_id).canonical_state()
    # Keep exact capacity diagnostics before assertions; this is fake-provider evidence.
    from collections import Counter
    capacity_diagnostic = {
        "section_count": section_count,
        "claims_per_section": claims_per_section,
        "limits": budget.limits.model_dump(mode="json"),
        "used": budget.used.model_dump(mode="json"),
        "remaining": budget.remaining.model_dump(mode="json"),
        "active_reserved": budget.active_reserved.model_dump(mode="json"),
        "capacity_refusals": capacity_refusals,
        "terminal_error": top.terminal_error.model_dump(mode="json") if top.terminal_error else None,
        "writer_count": len(writers), "verifier_count": len(verifiers),
        "calls_by_purpose": dict(Counter(req.extra.get("call_purpose", "unknown") for req in calls)),
        "calls_by_contract": dict(Counter(req.extra.get("response_contract", "unknown") for req in calls)),
        "release_exists": summary.get("target_release_publication") is not None,
        "workflow_status": summary["status"],
        "run_lifecycle_status": engine.lifecycle_service.load_run(top.run_id).status.value,
    }
    (tmp_path / "capacity_diagnostic.json").write_text(
        json.dumps(capacity_diagnostic, indent=2) + "\n", encoding="utf-8")
    print("CAPACITY_DIAGNOSTIC", json.dumps(capacity_diagnostic, sort_keys=True))
    assert budget.limits.logical_model_calls == 44
    assert budget.limits.model_tokens_total == 24000
    assert all(value == 0 for value in budget.active_reserved.model_dump().values())
    if section_count == 4:
        assert top.terminal_error is None
        assert summary["status"] == "partial_success"
        assert len(writers) == len(verifiers) == 4
        assert budget.used.logical_model_calls == 27
        assert summary.get("target_release_publication") is not None
        assert engine.lifecycle_service.load_run(top.run_id).status.value == "success"
        assert top.target_run_completion_assessment_ref is not None
    else:
        error = top.terminal_error
        assert error is not None
        matching = [item for item in capacity_refusals if item["message"] == error.message]
        assert matching, "transported scarcity must retain the exact native Budget refusal"
        refusal = matching[0]
        resource = refusal["resource"]
        assert refusal["exception_type"] == "CanonicalBudgetCapacityExceeded"
        assert error.failure_category.value == "resource_exhausted"
        assert error.details["failure_origin"] == "canonical_budget"
        assert refusal["run_id"] == top.run_id
        assert refusal["required"] == refusal["requested"][resource]
        # The exception retains the atomic reserve decision. The subsequent
        # native read is diagnostic: sibling reservations may change meanwhile.
        assert refusal["limit"] == refusal["native_limits"][resource] == getattr(budget.limits, resource)
        assert refusal["required"] > refusal["remaining"]
        assert summary["status"] != "success"
        assert summary.get("target_release_publication") is None
        assert engine.lifecycle_service.load_run(top.run_id).status.value != "success"
        assert top.target_run_completion_assessment_ref is None
        assert 0 < len(verifiers) <= section_count * claims_per_section
        assert 0 < len(writers) <= section_count
        assert 0 < budget.used.logical_model_calls <= budget.limits.logical_model_calls
        assert budget.used.logical_model_calls == len(calls)
