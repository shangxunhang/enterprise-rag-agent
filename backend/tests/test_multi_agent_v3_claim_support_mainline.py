"""341: real shared support integration, distinct from a Verification verdict."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimSupportAuthorityKind, ClaimSupportSubjectRefSchema, SupportRelation,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    TargetClaimSupportComposition, TargetClaimSupportError,
    SUPPORT_RESPONSE_CONTRACT, SUPPORT_SEMANTIC_CONTRACT, TARGET_SET_CONTRACT,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import ClaimSupportRuntime
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from rag.services.rag_service import FakeRAGService
from schemas.context import ContextPackageSchema
from schemas.rag import EvidenceBundleSchema
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests


TEXT = "应依据已核实的项目资料制定建设方案。"


def _target():
    subject = ClaimSupportSubjectRefSchema(authority_kind=ClaimSupportAuthorityKind.SIF07_CLAIM,
                                          subject_ref="claim-unit-fixture")
    return subject, ClaimSupportRuntime.materialize_evidence_target_set(subject, ("evidence:test:1",), TARGET_SET_CONTRACT)


@pytest.mark.parametrize("relation", [item.value for item in SupportRelation])
def test_support_vocabulary_preserves_distinct_relations(relation):
    subject, target = _target()
    supporting = ["evidence:test:1"] if relation in {"SUPPORTED", "PARTIALLY_SUPPORTED"} else []
    contradicting = ["evidence:test:1"] if relation == "CONTRADICTED" else []
    candidate = TargetClaimSupportComposition.parse_reply(
        {"relation": relation, "supporting_refs": supporting, "contradicting_refs": contradicting},
        subject=subject, target=target, occurrence="test-occurrence",
    )
    assert candidate.relation.value == relation
    assert candidate.evidence_target_set_ref == target


@pytest.mark.parametrize("defect", ["unknown-ref", "duplicate-ref", "both", "supported-empty", "partial-empty",
                                     "contradicted-empty", "unsupported-positive", "verdict", "unknown-relation",
                                     "missing-field", "invalid-json", "duplicate-json-key"])
def test_invalid_support_output_cannot_form_candidate(defect):
    subject, target = _target()
    raw = {"relation": "SUPPORTED", "supporting_refs": ["evidence:test:1"], "contradicting_refs": []}
    if defect == "unknown-ref":
        raw["supporting_refs"] = ["evidence:another"]
    elif defect == "duplicate-ref":
        raw["supporting_refs"] *= 2
    elif defect == "both":
        raw["contradicting_refs"] = raw["supporting_refs"]
    elif defect == "supported-empty":
        raw["supporting_refs"] = []
    elif defect == "partial-empty":
        raw.update(relation="PARTIALLY_SUPPORTED", supporting_refs=[])
    elif defect == "contradicted-empty":
        raw.update(relation="CONTRADICTED", supporting_refs=[])
    elif defect == "unsupported-positive":
        raw["relation"] = "NOT_SUPPORTED"
    elif defect == "verdict":
        raw["verified"] = True
    elif defect == "unknown-relation":
        raw["relation"] = "PASS"
    elif defect == "missing-field":
        del raw["contradicting_refs"]
    elif defect == "invalid-json":
        raw = "broken"
    else:
        raw = '{"relation":"SUPPORTED","relation":"INDETERMINATE","supporting_refs":[],"contradicting_refs":[]}'
    with pytest.raises(ValueError):
        TargetClaimSupportComposition.parse_reply(raw, subject=subject, target=target, occurrence="test-occurrence")


def _json_message(req, key):
    for message in req.messages:
        text = message["content"]
        start = text.find("{")
        while start >= 0:
            try:
                payload, consumed = json.JSONDecoder().raw_decode(text[start:])
            except (ValueError, TypeError):
                start = text.find("{", start + 1)
                continue
            if isinstance(payload, dict) and key in payload:
                return payload
            start = text.find("{", start + consumed)
    raise AssertionError("actual request lacks " + key)


class _StoppedProcess(BaseException):
    """Simulate process loss outside ordinary caught-provider exceptions."""


_SCENARIOS = tuple(item.value for item in SupportRelation) + (
    "empty-target", "bad-json", "bad-ref", "wrong-identity", "provider-failure",
    "commit-window", "intent-only", "concurrent", "budget-denied", "corrupt-candidate", "stale-after-model",
)


@pytest.fixture(scope="module", params=_SCENARIOS)
def support_mainline(tmp_path_factory, request):
    scenario = request.param
    root = tmp_path_factory.mktemp("support341-" + scenario)
    task_id, run_id = "task-support-" + scenario, "run-support-" + scenario
    project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    calls, observations = [], []
    original_generate = FakeLLMClient.generate
    original_retrieve = FakeRAGService.retrieve
    original_support = TargetClaimSupportComposition.support_bindings

    def evidence_fixture(service, req):
        raw = original_retrieve(service, req).model_dump(mode="json")
        for item in raw["item_assessments"]:
            item["supported_claim_types"] = ["general_method"]
            item["forbidden_claim_types"] = sorted(set(item.get("forbidden_claim_types", [])) | {"current_project_quantity"})
        raw["assessment"]["supported_claim_types"] = ["general_method"]
        raw["assessment"]["forbidden_claim_types"] = sorted(set(raw["assessment"].get("forbidden_claim_types", [])) | {"current_project_quantity"})
        return EvidenceBundleSchema.model_validate(raw)

    def provider(client, req):
        calls.append(req.model_copy(deep=True))
        response = original_generate(client, req)
        if req.extra.get("call_purpose") == "scheme_section_generation":
            return response.model_copy(update={"content": TEXT * 6}, deep=True)
        if req.extra.get("response_contract") == extraction_tests.CONTRACT:
            units = extraction_tests._task_contract(req)["coverage_units"]
            payload = {"units": [{"unit_id": item["unit_id"], "accounting_state": "FACTUAL_CLAIMS_MATERIALIZED",
                       "claims": [{"quote": TEXT, "occurrence_index": 0,
                                   "claim_type": "current_project_quantity" if scenario == "empty-target" else "general_method"}]}
                                  for item in units]}
            return response.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)}, deep=True)
        if req.extra.get("response_contract") != SUPPORT_RESPONSE_CONTRACT:
            return response
        support_evidence = _json_message(req, "support_evidence")["support_evidence"]
        refs = [item["exact_source_ref"] for item in support_evidence]
        assert refs
        assert all("match_text" not in item for item in support_evidence)
        assert all(
            item["context_text_sha256"] == canonical_sha256(item["context_text"])
            for item in support_evidence
        )
        assert all(
            "metadata" not in item.get("assessment", {})
            for item in support_evidence
        )
        if scenario == "intent-only":
            raise _StoppedProcess("test-only process loss")
        if scenario == "bad-json":
            return response.model_copy(update={"content": "not JSON"}, deep=True)
        if scenario == "wrong-identity":
            return response.model_copy(update={"task_id": "different-task"}, deep=True)
        if scenario == "provider-failure":
            return response.model_copy(update={"success": False, "error_message": "test provider unavailable"}, deep=True)
        relation = scenario if scenario in {item.value for item in SupportRelation} else "SUPPORTED"
        payload = {"relation": relation,
                   "supporting_refs": refs[:1] if relation in {"SUPPORTED", "PARTIALLY_SUPPORTED"} else [],
                   "contradicting_refs": refs[:1] if relation == "CONTRADICTED" else [],
                   "certainty_relation": "ALIGNED",
                   "certainty_reason_codes": ["TEST_CERTAINTY_ALIGNED"]}
        if scenario == "bad-ref":
            payload["supporting_refs"] = ["evidence-item://unrelated"]
        return response.model_copy(update={"content": json.dumps(payload)}, deep=True)

    def support_calls():
        return [req for req in calls if req.extra.get("call_purpose") == "claim_support"]

    def observe(composer, *, records, binding_records, operation_context):
        assert len(records) == len(binding_records) == 1
        kwargs = dict(records=records, binding_records=binding_records, operation_context=operation_context)
        if scenario == "budget-denied":
            from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
            from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
            original_reserve = CanonicalInvocationBudgetAdapter.reserve_provider_attempt
            def deny_support(adapter, req):
                if req.extra.get("call_purpose") == "claim_support":
                    raise CanonicalBudgetCapacityExceeded("model_tokens_total", limit=0, remaining=0, required=1)
                return original_reserve(adapter, req)
            with patch.object(CanonicalInvocationBudgetAdapter, "reserve_provider_attempt", deny_support):
                with pytest.raises(TargetClaimSupportError) as first:
                    original_support(composer, **kwargs)
            with pytest.raises(TargetClaimSupportError) as replay:
                original_support(composer, **kwargs)
            assert first.value.failure_category.value == replay.value.failure_category.value == "resource_exhausted"
            assert first.value.failure_origin.value == replay.value.failure_origin.value == "canonical_budget"
            assert len(support_calls()) == 0, "Budget refusal must happen before the external provider"
            assert not composer.runtime.ledger_path.exists()
            observations.append((composer, records[0], binding_records[0], None))
            raise replay.value
        if scenario == "stale-after-model":
            original_scope = composer._scope
            def freshness(target, context):
                if support_calls():
                    raise TargetClaimSupportError("test source changed during evaluation")
                return original_scope(target, context)
            with patch.object(composer, "_scope", freshness):
                with pytest.raises(TargetClaimSupportError, match="source changed"):
                    original_support(composer, **kwargs)
            with pytest.raises(TargetClaimSupportError, match="source changed") as replay:
                original_support(composer, **kwargs)
            assert len(support_calls()) == 1
            assert not composer.runtime.ledger_path.exists()
            observations.append((composer, records[0], binding_records[0], None))
            raise replay.value
        if scenario in {"commit-window", "corrupt-candidate"}:
            # Durable model candidate exists but canonical support append did not happen.
            with patch.object(composer.runtime, "commit_support", side_effect=RuntimeError("test commit window")):
                with pytest.raises(RuntimeError, match="test commit window"):
                    original_support(composer, **kwargs)
            assert not composer.runtime.ledger_path.exists()
            assert len(support_calls()) == 1
            if scenario == "corrupt-candidate":
                stored = [ref for ref in composer.store.list_references() if ref.artifact_type == "claim_support_candidate"]
                assert len(stored) == 1
                composer.store._path_for_ref(stored[0].artifact_ref).write_text("corrupt", encoding="utf-8")
                with pytest.raises(ValueError) as failure:
                    original_support(composer, **kwargs)
                assert len(support_calls()) == 1
                assert not composer.runtime.ledger_path.exists()
                observations.append((composer, records[0], binding_records[0], None))
                raise failure.value
        elif scenario in {"bad-json", "bad-ref", "wrong-identity", "provider-failure", "intent-only"}:
            expected = _StoppedProcess if scenario == "intent-only" else TargetClaimSupportError
            with pytest.raises(expected) as first:
                original_support(composer, **kwargs)
            assert len(support_calls()) == 1
            with pytest.raises(TargetClaimSupportError) as repeated:
                original_support(composer, **kwargs)
            assert len(support_calls()) == 1
            assert not composer.runtime.ledger_path.exists(), "technical error cannot create semantic support"
            if scenario != "intent-only":
                assert str(repeated.value) == str(first.value)
                assert repeated.value.failure_category == first.value.failure_category
                assert repeated.value.failure_origin == first.value.failure_origin
            else:
                assert repeated.value.error_code == "CLAIM_SUPPORT_INDETERMINATE_EXECUTION"
            observations.append((composer, records[0], binding_records[0], None))
            raise repeated.value
        if scenario == "concurrent":
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            other = TargetClaimSupportComposition(claim_runtime=composer.claim_runtime,
                    source_composition=composer.sources, model_gateway=composer.model_gateway)
            barrier = Barrier(2, timeout=10)
            def run(instance):
                barrier.wait()
                return original_support(instance, **kwargs)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(run, composer), pool.submit(run, other)
                result = a.result(timeout=30)
                assert b.result(timeout=30) == result
        else:
            result = original_support(composer, **kwargs)
        before_calls, before_ledger = len(support_calls()), composer.runtime.ledger_path.read_bytes()
        other = TargetClaimSupportComposition(claim_runtime=composer.claim_runtime,
                    source_composition=composer.sources, model_gateway=composer.model_gateway)
        assert original_support(other, **kwargs) == result
        assert composer.runtime.ledger_path.read_bytes() == before_ledger
        assert len(support_calls()) == before_calls
        target = result[0].evidence_target_set_ref
        foreign = composer.runtime.materialize_evidence_target_set(
            target.claim_support_subject_ref, ("evidence-item://unrelated",), TARGET_SET_CONTRACT)
        with pytest.raises(TargetClaimSupportError, match="escapes"):
            composer.validate_target(foreign, operation_context)
        assert len(support_calls()) == before_calls
        assert composer.claim_runtime.resolve_claim_head(records[0].claim_ref) is None
        observations.append((composer, records[0], binding_records[0], result[0]))
        return result

    factory = workflow_tests._W13InspectingFactory()
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm", supervisor_model_name="fake_llm",
                       enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True,
                       gate_enforcement_mode="strict", gate_recovery_rounds_limit=0)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
            "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false", "ENABLE_SEMANTIC_GATE": "false"}))
        stack.enter_context(patch.object(FakeRAGService, "retrieve", evidence_fixture))
        stack.enter_context(patch.object(FakeLLMClient, "generate", provider))
        stack.enter_context(patch.object(TargetClaimSupportComposition, "support_bindings", observe))
        summary = MainlineApplicationService(supervisor_factory=factory).run(
            project_root=Path(__file__).resolve().parents[2], user_input=project.user_query,
            task_id=task_id, run_id=run_id, output_root=root, clean_existing=True, settings=settings,
            project_input=project.model_dump(mode="json"), allow_demo_defaults=False, enable_agent_self_rag=False,
        )
    if not observations:
        error = workflow_tests._latest_w13_top_level_payload(factory.engines[0]).terminal_error
        raise AssertionError(error.model_dump(mode="json") if error else summary["status"])
    return scenario, calls, observations[0], factory.engines[0], summary


def test_real_support_mainline_persists_exact_relation_or_preserves_technical_failure(support_mainline):
    scenario, calls, observation, engine, summary = support_mainline
    composer, claim, binding, result = observation
    failed = scenario in {"bad-json", "bad-ref", "wrong-identity", "provider-failure", "intent-only", "budget-denied", "corrupt-candidate", "stale-after-model"}
    support_calls = [req for req in calls if req.extra.get("call_purpose") == "claim_support"]
    assert len(support_calls) == (0 if scenario in {"empty-target", "budget-denied"} else 1)
    head = composer.claim_runtime.resolve_claim_head(claim.claim_ref)
    assert (head is None) == failed
    if failed:
        # Existing top-level policy projects resource failures as retryable_failed.
        # This does not authorize this exact failed support occurrence to redispatch.
        assert summary["status"] == ("retryable_failed" if scenario == "budget-denied" else "failed")
        assert not composer.runtime.ledger_path.exists()
        assert summary.get("target_release_publication") is None
        return
    assert summary["status"] == "partial_success", "support success is not complete quality verification"
    expected = scenario if scenario in {item.value for item in SupportRelation} else "NOT_SUPPORTED" if scenario == "empty-target" else "SUPPORTED"
    assert result.relation.value == expected
    assert composer.runtime.resolve_support(result.support_ref) == result
    payload = workflow_tests._latest_w13_section_payload(engine)
    assert payload.retrieval_metadata["canonical_claim_support_refs"] == [result.support_ref.model_dump(mode="json")]
    assert payload.retrieval_metadata["canonical_claim_support_count"] == 1
    assert payload.retrieval_metadata["canonical_claim_verifier_invoked"] is True
    assert payload.retrieval_metadata["canonical_claim_verification_count"] == 1
    assert set(result.evidence_target_set_ref.evidence_member_refs) == {
        ref.exact_source_ref for ref in binding.exact_authorized_source_refs if ref.source_kind == "EVIDENCE"
    }
    if not support_calls:
        assert result.supporting_refs == result.contradicting_refs == ()
        return
    request = support_calls[0]
    target_json = _json_message(request, "claim_target")["claim_target"]
    assert target_json["text"] == TEXT
    assert '"writer_admission_decision"' not in "\n".join(msg["content"] for msg in request.messages)
    input_refs = [ref for ref in composer.store.list_references() if ref.artifact_type == "claim_support_input"]
    assert len(input_refs) == 1
    stored = composer.store.get_json(input_refs[0].artifact_ref, task_id=request.task_id, run_id=request.run_id)
    package = ContextPackageSchema.model_validate(stored["context_package"])
    actual = {"input_kind": "MESSAGES", "messages": request.messages}
    assert actual == package.model_input.final_input_material.inline_final_input_payload
    assert canonical_sha256(actual) == package.model_input.final_model_input_sha256


def test_shared_semantic_support_can_verify_without_duplicate_binding_cognition(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimBindingRelation, VerificationVerdict
    import test_multi_agent_v3_claim_verification_runtime as owner_tests
    env, runtime, claim, binding, _, support, _ = owner_tests._setup_verification_chain(
        tmp_path, binding_relation=ClaimBindingRelation.CONTEXT_ONLY,
    )
    candidate = owner_tests._verifier_candidate(claim, binding, support)
    committed = runtime.commit_verification(
        claim.claim_ref, binding.binding_facts_ref, support.support_ref, candidate, None,
        owner_tests._verification_context(env, candidate.verification_occurrence_ref),
    )
    assert committed.verification_result.verdict is VerificationVerdict.VERIFIED
    assert binding.facts[0].relation is ClaimBindingRelation.CONTEXT_ONLY
    assert runtime.resolve_binding_facts(binding.binding_facts_ref) == binding


@pytest.mark.parametrize("relation", ["CONTRADICTS", "NO_MATERIAL_SUPPORT"])
def test_shared_support_never_overrides_deterministic_binding_rejection(tmp_path, relation):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimBindingRelation
    from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimBindingError
    import test_multi_agent_v3_claim_verification_runtime as owner_tests
    env, runtime, claim, binding, _, support, _ = owner_tests._setup_verification_chain(
        tmp_path, binding_relation=ClaimBindingRelation(relation),
    )
    candidate = owner_tests._verifier_candidate(claim, binding, support)
    with pytest.raises(ClaimBindingError):
        runtime.commit_verification(claim.claim_ref, binding.binding_facts_ref, support.support_ref, candidate, None,
                                    owner_tests._verification_context(env, candidate.verification_occurrence_ref))
    assert runtime.resolve_claim_head(claim.claim_ref) is None
