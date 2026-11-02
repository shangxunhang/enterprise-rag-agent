"""Offline per-Run Verifier response-limit and historical occurrence probes.

An explicitly configured external counterexample adds the frozen real a2 wire
case without copying model/business text into a repository fixture.
"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetVectorSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerifierClaimInputSchema,
    build_verifier_semantic_input_sha256,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    TargetClaimVerifierComposition,
    TargetClaimVerifierError,
)
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from backend.tests.test_bca014_i5_verifier_project_input_basis import (
    Gateway,
    fixture as owner_only_fixture,
    model_material,
)
from backend.tests.test_bca014_verifier_prompt_provenance_clarification import fixture
from bootstrap.runtime_options import RuntimeOptions
from core.hashing import canonical_sha256
from schemas.context import ContextPackageSchema
from schemas.model import ModelResponseSchema, TokenUsageSchema


CAP_KEY = "verifier_output_cap_v1"
UNSET = object()
A2_COUNTEREXAMPLE_SHA256 = "2e6974ba60788c537ad40ce1c11e265e2e9e1c6853d8b698df78215980423479"
A2_CONTENT_SHA256 = "fb7780d36a7478b9c3819bbcaf532a84727e7fb244e2d0f91d2a769ff667bdaa"


class CapabilityGateway(Gateway):
    """In-process test responses; no provider registry or network is involved."""

    def __init__(self, safe_max_output_tokens=8192, *, invalid_json=False):
        super().__init__()
        self.safe_max_output_tokens = safe_max_output_tokens
        self.invalid_json = invalid_json

    def routing_capabilities(self, **kwargs):
        return {"safe_context_window": 65536,
                "safe_max_output_tokens": self.safe_max_output_tokens}

    def generate(self, request):
        response = super().generate(request)
        if self.invalid_json:
            return response.model_copy(update={"content": '{"verdict":"UNSUPPORTED"'})
        return response


def _configure(fx, requested=UNSET, *, safe=8192, invalid_json=False):
    gateway = CapabilityGateway(safe, invalid_json=invalid_json)
    arguments = {"claim_runtime": fx.runtime, "support_composition": fx.composer.support,
                 "model_gateway": gateway, "writer_execution_store": fx.composer.writer_execution_store}
    if requested is not UNSET:
        arguments["verifier_max_output_tokens"] = requested
    fx.composer = TargetClaimVerifierComposition(**arguments)
    fx.runtime.verification_identity_validator = fx.composer.validate_v2_identity
    fx.invocation, fx.project = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    return gateway


def _dispatch_offline(fx):
    invocation, material = model_material(fx)
    with patch.object(fx.composer, "_material", return_value=(invocation, material)):
        candidate = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
    return candidate


def _runtime_options(tmp_path):
    # Only the model name is used by resolve; no credentials/settings file read.
    return RuntimeOptions.resolve(SimpleNamespace(default_model_name="offline-cap-test"), tmp_path)


def test_runtime_override_is_optional_and_default_remains_1024(tmp_path, monkeypatch):
    monkeypatch.delenv("VERIFIER_MAX_OUTPUT_TOKENS", raising=False)
    assert _runtime_options(tmp_path).verifier_max_output_tokens is None
    fx = fixture(tmp_path / "owners")
    gateway = _configure(fx)
    assert fx.composer.effective_output_cap() == 1024
    assert fx.invocation.deterministic_validation_facts[CAP_KEY] == {"effective_max_output_tokens": 1024}
    assert gateway.calls == []
    monkeypatch.setenv("VERIFIER_MAX_OUTPUT_TOKENS", "4096")
    assert _runtime_options(tmp_path).verifier_max_output_tokens == 4096


@pytest.mark.parametrize("invalid", ["0", "-1", "1.5", "true", "not-an-int", ""])
def test_invalid_environment_override_fails_at_runtime_resolution(tmp_path, monkeypatch, invalid):
    monkeypatch.setenv("VERIFIER_MAX_OUTPUT_TOKENS", invalid)
    with pytest.raises(ValueError):
        _runtime_options(tmp_path)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "4096"])
def test_invalid_typed_runtime_override_is_not_coerced(tmp_path, monkeypatch, invalid):
    monkeypatch.delenv("VERIFIER_MAX_OUTPUT_TOKENS", raising=False)
    options = _runtime_options(tmp_path)
    with pytest.raises(ValueError):
        replace(options, verifier_max_output_tokens=invalid)


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "4096", None])
def test_invalid_constructor_override_fails_before_dispatch_or_receipt_write(tmp_path, invalid):
    fx = fixture(tmp_path)
    gateway = CapabilityGateway()
    before = fx.composer.store.list_references()
    with pytest.raises(TargetClaimVerifierError, match="positive integer"):
        TargetClaimVerifierComposition(claim_runtime=fx.runtime,
            support_composition=fx.composer.support, model_gateway=gateway,
            writer_execution_store=fx.composer.writer_execution_store,
            verifier_max_output_tokens=invalid)
    assert gateway.calls == []
    assert fx.composer.store.list_references() == before


@pytest.mark.parametrize("requested,safe,expected", [
    (UNSET, 8192, 1024), (4096, 8192, 4096), (4096, 2048, 2048), (1024, 512, 512),
])
def test_effective_cap_is_identical_in_context_request_and_canonical_token_reservation(tmp_path, requested, safe, expected):
    fx = fixture(tmp_path / "owners")
    gateway = _configure(fx, requested, safe=safe)
    _dispatch_offline(fx)
    assert len(gateway.calls) == 1
    request = gateway.calls[0]
    inputs = [r for r in fx.composer.store.list_references() if r.artifact_type == "canonical_verifier_input"]
    assert len(inputs) == 1
    receipt = fx.composer.store.get_json(inputs[0].artifact_ref, task_id=request.task_id, run_id=request.run_id)
    package = ContextPackageSchema.model_validate(receipt["context_package"])
    assert request.max_tokens == expected
    assert package.budget_binding.reserved_output_tokens == expected
    assert package.budget_binding.model_capability_binding.safe_max_output_tokens == expected
    assert receipt["input_material"]["invocation"]["deterministic_validation_facts"][CAP_KEY] == {
        "effective_max_output_tokens": expected}
    assert package.model_input.final_input_material.inline_final_input_payload["messages"] == request.messages

    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    manager = BudgetManager.canonical(task_id=request.task_id, run_id=request.run_id,
        limits=CanonicalBudgetVectorSchema(logical_model_calls=2, logical_tool_calls=2,
            model_tokens_total=1_000_000, research_rounds=1, repair_attempts=1,
            generation_attempts=2, replan_attempts=1, true_retry_attempts=1),
        max_elapsed_seconds=60, storage_path=tmp_path / "budget.jsonl",
        policy_ref="budget-policy://offline-verifier-cap", policy_version="v1", clock=lambda: now)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    provider_request = request.model_copy(update={"extra": {
        **request.extra, "provider_attempt_id": "offline-cap-provider-occurrence"}}, deep=True)
    reservation_id = adapter.reserve_provider_attempt(provider_request)
    input_material = [request.prompt or "", request.system_prompt or ""]
    for message in request.messages:
        input_material.extend([str(message.get("role") or ""), str(message.get("content") or "")])
    input_upper_bound = sum(len(value.encode("utf-8")) for value in input_material)
    assert manager.resolve_reservation(reservation_id).requested.model_tokens_total == input_upper_bound + expected
    response = ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
        run_id=request.run_id, model_name="offline-cap-test", success=True, created_at="test",
        token_usage=TokenUsageSchema(prompt_tokens=11, completion_tokens=7, total_tokens=18))
    adapter.settle_provider_attempt(reservation_id, provider_request, response)
    settled = manager.resolve_reservation(reservation_id)
    assert settled.status == "consumed" and settled.actual.model_tokens_total == 18
    assert manager.canonical_state().used.model_tokens_total == 18


def test_effective_cap_binds_identity_but_larger_requests_with_same_clamp_do_not(tmp_path):
    fx = fixture(tmp_path)
    _configure(fx, 1024)
    baseline = fx.invocation
    _configure(fx, 4096)
    raised = fx.invocation
    assert baseline.verifier_semantic_input_sha256 != raised.verifier_semantic_input_sha256
    assert baseline.authority_universe_sha256 == raised.authority_universe_sha256
    assert baseline.verifier_project_input_basis == raised.verifier_project_input_basis
    _configure(fx, 4096, safe=2048)
    clamped = fx.invocation
    _configure(fx, 8192, safe=2048)
    same_effective = fx.invocation
    assert clamped.deterministic_validation_facts[CAP_KEY] == {"effective_max_output_tokens": 2048}
    assert same_effective.verifier_semantic_input_sha256 == clamped.verifier_semantic_input_sha256
    assert clamped.verifier_semantic_input_sha256 != raised.verifier_semantic_input_sha256
    gateway = fx.composer.model_gateway
    candidate = _dispatch_offline(fx)
    key = canonical_sha256({"contract": "verifier-physical-occurrence-v2",
        "verifier_semantic_input_sha256": same_effective.verifier_semantic_input_sha256})
    assert gateway.calls[0].model_call_id == "model_call_verifier_" + key
    assert candidate.verification_occurrence_ref == "claim-verification-v2://sha256/" + key


@pytest.mark.parametrize("add_gateway", [False, True])
def test_owner_only_projection_has_no_invented_cap_and_cannot_be_packaged(tmp_path, add_gateway):
    fx = owner_only_fixture(tmp_path)
    assert fx.composer.model_gateway is None
    assert CAP_KEY not in fx.invocation.deterministic_validation_facts
    invocation, material = model_material(fx)
    if add_gateway:
        fx.composer.model_gateway = gateway = CapabilityGateway()
    with pytest.raises((ValueError, TargetClaimVerifierError)):
        fx.composer._package(material, occurrence="offline-cap://owner-only",
            invocation=invocation, validate=lambda: None)
    if add_gateway:
        assert gateway.calls == []


def test_changed_effective_cap_rejects_previously_bound_private_package(tmp_path):
    fx = fixture(tmp_path)
    _configure(fx, 1024)
    invocation, material = model_material(fx)
    gateway = _configure(fx, 4096)
    with pytest.raises(TargetClaimVerifierError):
        fx.composer._package(material, occurrence="offline-cap://old-cap",
            invocation=invocation, validate=lambda: None)
    assert gateway.calls == []


@pytest.mark.parametrize("state", ["completed", "failed", "intent_only"])
def test_changed_cap_never_redispatches_same_claim_attempt_or_rewrites_old_receipts(tmp_path, state):
    fx = fixture(tmp_path)
    old_gateway = _configure(fx, 1024, invalid_json=state == "failed")
    old_gateway.stop = state == "intent_only"
    if state == "completed":
        _dispatch_offline(fx)
    else:
        with pytest.raises(KeyboardInterrupt if state == "intent_only" else TargetClaimVerifierError):
            _dispatch_offline(fx)
    assert len(old_gateway.calls) == 1
    references = fx.composer.store.list_references()
    old_payloads = {r.artifact_ref: fx.composer.store.get_json(r.artifact_ref,
        task_id=r.task_id, run_id=r.run_id) for r in references if r.artifact_type.startswith("canonical_verifier_")}
    assert old_payloads
    new_gateway = _configure(fx, 4096)
    with pytest.raises(TargetClaimVerifierError):
        _dispatch_offline(fx)
    assert new_gateway.calls == []
    assert fx.composer.store.list_references() == references
    for ref, payload in old_payloads.items():
        assert fx.composer.store.get_json(ref, task_id=fx.env["lifecycle"].load_run_identity(
            fx.context.active_lineage.run_id).task_id, run_id=fx.context.active_lineage.run_id) == payload


def _old_input(invocation, *, without_cap=False, without_prompt=False):
    data = invocation.model_dump(mode="json")
    if without_cap:
        data["deterministic_validation_facts"].pop(CAP_KEY, None)
    if without_prompt:
        data["deterministic_validation_facts"].pop("verifier_prompt_profile_sha256", None)
    if without_cap or without_prompt:
        data["deterministic_validation_facts"].pop("verifier_reply_ref_order_v1", None)
    data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
    return VerifierClaimInputSchema.model_validate(data)


@pytest.mark.parametrize("old_shape", ["cap1024", "cap4096", "before_cap", "before_prompt_and_cap"])
def test_typed_old_result_reads_exact_recorded_identity_but_old_candidate_cannot_upgrade(tmp_path, old_shape):
    fx = fixture(tmp_path)
    _configure(fx, 4096 if old_shape == "cap4096" else 1024)
    old_composer = fx.composer
    invocation = _old_input(fx.invocation, without_cap=old_shape.startswith("before_"),
        without_prompt=old_shape == "before_prompt_and_cap")
    candidate, _ = TargetClaimVerifierComposition.parse_candidate({"verdict": "UNSUPPORTED",
        "positive_basis_refs": [], "rationale": "Controlled historical semantics", "issues": []},
        invocation=invocation, occurrence="offline-cap://historical-result", receipt_ref="artifact://historical-result")
    old_builder = old_composer.build_input
    def historical_builder(*args, **kwargs):
        original, project = old_builder(*args, **kwargs)
        return _old_input(original, without_cap=old_shape.startswith("before_"),
            without_prompt=old_shape == "before_prompt_and_cap"), project
    with patch.object(old_composer, "build_input", side_effect=historical_builder):
        committed = fx.runtime.commit_verification(fx.claim.claim_ref, fx.binding.binding_facts_ref,
            fx.support.support_ref, candidate, None, fx.context)
    result = committed.verification_result
    before = fx.runtime.ledger_path.read_bytes()
    frozen = result.model_dump(mode="json")
    _configure(fx, 2048 if old_shape == "cap4096" else 4096)
    fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, result, fx.context.active_lineage)
    assert result.model_dump(mode="json") == frozen
    assert fx.runtime.ledger_path.read_bytes() == before
    with pytest.raises(TargetClaimVerifierError):
        fx.composer.validate_v2_identity(fx.claim, fx.binding, fx.support, candidate, fx.context.active_lineage)


def _truncated_cases():
    cases = [pytest.param(None, id="portable-unfinished-source-ref")]
    explicit = os.environ.get("BCA014_VERIFIER_TRUNCATION_COUNTEREXAMPLE")
    if explicit:
        cases.append(pytest.param(Path(explicit), id="frozen-real-attempt2-wire"))
    return cases


@pytest.mark.parametrize("counterexample", _truncated_cases())
def test_strict_parser_rejects_truncated_wire_without_repair_or_candidate(tmp_path, counterexample):
    fx = fixture(tmp_path)
    frozen_bytes = None
    if counterexample is None:
        content = '{"verdict":"UNSUPPORTED","positive_basis_refs":[],"rationale":"Incomplete wire","issues":[{"issue_code":"I","message":"Incomplete","severity":"MINOR","source_refs":["evidence-item://sha256/'
    else:
        frozen_bytes = counterexample.read_bytes()
        assert hashlib.sha256(frozen_bytes).hexdigest() == A2_COUNTEREXAMPLE_SHA256
        saved = json.loads(frozen_bytes)
        response = saved["response_event"]["response"]
        assert response["finish_reason"] == "length"
        assert response["token_usage"]["completion_tokens"] == 1024
        assert saved["request_event"]["request"]["max_tokens"] == 1024
        content = response["content"]
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == A2_CONTENT_SHA256
    original = content
    references = fx.composer.store.list_references()
    with pytest.raises(json.JSONDecodeError, match="Unterminated string"):
        TargetClaimVerifierComposition.parse_candidate(content, invocation=fx.invocation,
            occurrence="offline-cap://strict-truncated", receipt_ref="artifact://truncated-wire")
    assert content == original
    assert fx.composer.store.list_references() == references
    if counterexample is not None:
        assert counterexample.read_bytes() == frozen_bytes
