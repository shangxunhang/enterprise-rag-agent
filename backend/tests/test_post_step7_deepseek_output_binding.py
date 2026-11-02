"""H13 output allowance: config, actual SDK wire body and owned context budgets.

All provider responses use httpx.MockTransport below the actual factory-selected
ChatOpenAI class. Socket and DNS calls are denied; no real provider is involved.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI

from apps.enterprise_document.services.agent_rag_v1 import CanonicalInvocationBudgetAdapter
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import (
    TargetPostWriteCitationGrounding,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    CONSISTENCY_SHARED_RELATION_CONTRACT,
    TargetPostWriteConsistency,
    _instruction_profile,
)
from bootstrap.model_factory import build_default_model_routing
import core.config as config
from core.token_estimator import DeterministicTokenEstimator
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from model_gateway.provider_factory import ModelProviderFactory
from schemas.model import ModelRequestSchema
from test_multi_agent_v3_model_tool_runtime import _limits, _manager


ROOT = Path(config.__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_settings_and_no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("H13 test attempted real network or DNS")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-h13-output-binding")
    monkeypatch.delenv("DEEPSEEK_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setattr(config, "_DOTENV_VALUES", {})
    monkeypatch.setattr(config, "_SETTINGS", None)


def _settings(**updates):
    values = dict(
        default_model_name="deepseek_api",
        model_capabilities_path=ROOT / "config/model_capabilities.json",
        deepseek_api_key="test-only-h13-output-binding",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model_name="deepseek_api",
        deepseek_provider_model_name="deepseek-v4-flash",
        local_qwen_max_new_tokens=1536,
        local_qwen_1_5b_model_path=Path("D:/models/Qwen2.5-1.5B-Instruct"),
        local_qwen_3b_model_path=Path("D:/models/Qwen2.5-3B-Instruct"),
        local_qwen_7b_model_path=Path("D:/models/Qwen2.5-7B-Instruct-GPTQ-Int4"),
    )
    values.update(updates)
    return replace(config.get_settings(reload=True), **values)


def _profiles(settings):
    return {p.profile_id: p for p in build_default_model_routing(settings).profiles}


@pytest.mark.parametrize("local_limit", [1536, 4096])
def test_absent_deepseek_setting_retains_exact_legacy_output_policy(local_limit):
    settings = _settings(local_qwen_max_new_tokens=local_limit)
    assert settings.deepseek_max_output_tokens is None
    profiles = _profiles(settings)
    assert profiles["deepseek"].max_output_tokens == max(2048, local_limit)
    assert profiles["deepseek"].context_window == 1_000_000
    assert profiles["local_1_5b"].max_output_tokens == local_limit
    fake = _profiles(replace(settings, default_model_name="fake_llm"))
    assert fake["fake"].max_output_tokens == max(2048, local_limit)


def test_explicit_deepseek_setting_does_not_raise_local_or_fake_policy():
    settings = _settings(deepseek_max_output_tokens=65536)
    profiles = _profiles(settings)
    assert profiles["deepseek"].max_output_tokens == 65536
    assert profiles["deepseek"].context_window == 1_000_000
    for profile_id in ("local_1_5b", "local_3b", "local_7b"):
        assert profiles[profile_id].max_output_tokens == 1536
    assert _profiles(replace(settings, default_model_name="fake_llm"))["fake"].max_output_tokens == 2048


@pytest.mark.parametrize("value", ["", "0", "-1", "true", "1.5", "nan", "inf"])
def test_invalid_explicit_deepseek_environment_fails_closed(monkeypatch, value):
    monkeypatch.setenv("DEEPSEEK_MAX_OUTPUT_TOKENS", value)
    with pytest.raises(ValueError):
        config.get_settings(reload=True)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, float("nan"), float("inf"), "65536"])
def test_invalid_programmatic_deepseek_setting_fails_before_routing(value):
    with pytest.raises(ValueError):
        _profiles(_settings(deepseek_max_output_tokens=value))


def test_valid_environment_setting_reaches_only_deepseek_profile(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "65536")
    settings = _settings()
    assert settings.deepseek_max_output_tokens == 65536
    assert _profiles(settings)["deepseek"].max_output_tokens == 65536


def test_explicit_deepseek_setting_stays_clamped_to_catalog_capability():
    profile = _profiles(_settings(deepseek_max_output_tokens=999999))["deepseek"]
    assert profile.max_output_tokens == 384000
    assert profile.context_window == 1_000_000


@pytest.fixture
def sdk_wire(monkeypatch):
    bodies = []

    def respond(request):
        # Deliberately record no headers or credentials.
        body = json.loads(request.content)
        bodies.append({"host": request.url.host, "path": request.url.path, "body": body})
        return httpx.Response(200, json={
            "id": "chatcmpl-h13", "object": "chat.completion", "created": 0,
            "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        }, request=request)

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: transport.handle_request(request))
    yield bodies
    transport.close()


def _remote_profile(**updates):
    values = dict(profile_id="deepseek", model_name="deepseek_api", provider="openai_compatible",
                  provider_model_name="deepseek-v4-flash", context_window=1_000_000,
                  max_output_tokens=65536, timeout_seconds=120)
    values.update(updates)
    return ModelProfile(**values)


def _client(*, thinking=False, profile=None, endpoint="https://api.deepseek.com"):
    return ModelProviderFactory().build(profile or _remote_profile(), settings=SimpleNamespace(
        deepseek_api_key="test-only-h13-output-binding", deepseek_base_url=endpoint,
        deepseek_thinking_enabled=thinking,
    ))


def _request(limit, *, call_id="h13-output"):
    return ModelRequestSchema(
        model_call_id=call_id, task_id="task_1", run_id="run_1", model_role="general",
        caller_agent="H13OutputBinding", prompt="canonical prompt", system_prompt="canonical system",
        messages=[{"role": "system", "content": "policy"}, {"role": "user", "content": "hello"}],
        temperature=0.2, max_tokens=limit, created_at="2026-09-03T15:00:00+00:00",
    )


@pytest.mark.parametrize("thinking", [False, True])
def test_actual_factory_sdk_serializes_each_request_max_tokens_and_retains_controls(sdk_wire, thinking):
    client = _client(thinking=thinking)
    assert isinstance(client.chat_model, ChatOpenAI)
    assert client.chat_model.max_retries == 0
    assert client.chat_model.root_client.max_retries == 0
    assert client.chat_model.request_timeout == 120.0
    for ordinal, limit in enumerate((128, 2048, 65536)):
        request = _request(limit, call_id=f"h13-wire-{ordinal}")
        response = client.generate(request)
        assert response.success, response.error_message
        assert response.content == "ok"
        assert response.finish_reason == "stop"
        assert response.token_usage.prompt_tokens == 8
        assert response.token_usage.completion_tokens == 4
        assert response.token_usage.total_tokens == 12
        assert len(sdk_wire) == ordinal + 1
        captured = sdk_wire[-1]
        assert captured["host"] == "api.deepseek.com"
        assert captured["path"] == "/chat/completions"
        body = captured["body"]
        assert body["max_tokens"] == limit
        assert "max_completion_tokens" not in body
        assert body["model"] == "deepseek-v4-flash"
        assert body["thinking"] == {"type": "enabled" if thinking else "disabled"}
        assert body["temperature"] == 0.2
        assert body["messages"] == request.messages


@pytest.mark.parametrize("profile_id,endpoint", [
    ("other", "https://api.deepseek.com"),
    ("deepseek", "https://provider.example/v1"),
])
def test_non_owned_openai_compatible_sdk_payload_remains_unchanged(sdk_wire, profile_id, endpoint):
    client = _client(profile=_remote_profile(profile_id=profile_id), endpoint=endpoint)
    assert type(client.chat_model) is ChatOpenAI
    response = client.generate(_request(321))
    assert response.success, response.error_message
    assert len(sdk_wire) == 1
    assert sdk_wire[0]["body"]["max_completion_tokens"] == 321
    assert "max_tokens" not in sdk_wire[0]["body"]


@pytest.mark.parametrize("upstream", [
    {"model": "deepseek-v4-flash", "max_tokens": 777},
    {"model": "deepseek-v4-flash", "max_tokens": 777, "max_completion_tokens": 777},
])
def test_deepseek_payload_accepts_correct_or_equal_upstream_fields(monkeypatch, upstream):
    before = deepcopy(upstream)
    client = _client()
    monkeypatch.setattr(ChatOpenAI, "_get_request_payload", lambda *args, **kwargs: deepcopy(upstream))
    actual = client.chat_model._get_request_payload([], max_tokens=777)
    assert actual == {"model": "deepseek-v4-flash", "max_tokens": 777}
    assert upstream == before


def test_deepseek_payload_rejects_conflicting_upstream_token_fields(monkeypatch):
    client = _client()
    monkeypatch.setattr(ChatOpenAI, "_get_request_payload", lambda *args, **kwargs: {
        "model": "deepseek-v4-flash", "max_tokens": 777, "max_completion_tokens": 888,
    })
    with pytest.raises(ValueError):
        client.chat_model._get_request_payload([], max_tokens=777)


def _package_material(owner):
    if owner == "citation":
        return {
            "artifact_state_ref": {"artifact_logical_id": "section-a"},
            "claim_source_hash": "a" * 64, "citation_source_hash": "b" * 64,
            "visible_artifact": {"payload_kind": "section", "section_id": "section-a",
                                 "section_title": "A", "content": "Exact visible content."},
            "eligible_occurrence_refs": [], "occurrences": [], "candidate_claim_targets": [],
            "no_applicable_allowed": {}, "prerequisite_refs": [],
        }
    direct = {
        "validation_scope": "SECTION", "discovery_mode": "SMALL_N_EXHAUSTIVE_MEMBERS",
        "required_relation_member_sets": [["assertion-a", "assertion-b"]], "semantic_candidate_budget": 0,
        "instruction_profile": _instruction_profile(),
        "profile": {"composite_max_arity": 3},
    }
    material = {
        **direct, "source_snapshot_hash": "a" * 64,
        "assertions": [{"assertion_ref": ref, "canonical_text": "Exact claim " + ref,
                        "typed_value": None, "value_unit": None} for ref in ("assertion-a", "assertion-b")],
        "assertion_context": {ref: {"section_id": "section-a", "claim_type": "general_method"}
                              for ref in ("assertion-a", "assertion-b")},
    }
    if owner == "shared":
        material.update(contract=CONSISTENCY_SHARED_RELATION_CONTRACT,
                        logical_works=[{"logical_work_ref": "logical-work-a", "material": direct}])
    return material


@pytest.mark.parametrize("owner,calibrated_limit", [("section", 16384), ("shared", 65536), ("citation", 8192)])
@pytest.mark.parametrize("profile_limit", [2048, 65536])
def test_owned_context_package_reserves_calibrated_output_bounded_by_profile(owner, calibrated_limit, profile_limit):
    profile = ModelProfile(profile_id="fake", model_name="fake_llm", provider="fake",
                           context_window=1_000_000, max_output_tokens=profile_limit)
    gateway = ModelGateway("fake_llm", router=ModelRouter("fake_llm", profiles=[profile],
        policies=[RoutingPolicy(role=ModelRole.GENERAL, candidates=["fake"])]))
    cls = TargetPostWriteCitationGrounding if owner == "citation" else TargetPostWriteConsistency
    # Package-only seam: production context materialization and routing remain real.
    composer = cls.__new__(cls)
    composer.gateway = gateway
    composer.estimator = DeterministicTokenEstimator()
    material = _package_material(owner)
    before = deepcopy(material)
    lineage = SimpleNamespace(run_id="run_1", attempt_id="attempt-1", attempt_scope_ref="section:section-a")
    try:
        package, limit = composer._package(material, lineage, "h13-package-" + owner)
        expected = min(profile_limit, calibrated_limit)
        assert limit == expected
        assert package.budget_binding.reserved_output_tokens == expected
        assert package.model_input.budget_binding.reserved_output_tokens == expected
        assert package.budget_binding.model_capability_binding.safe_max_output_tokens == expected
        assert package.model_input.final_input_material.inline_final_input_payload["messages"]
        assert material == before
    finally:
        gateway.close()


def test_gateway_reservation_equals_effective_wire_allowance_and_settles_reported_usage(tmp_path, sdk_wire):
    profile = _remote_profile()
    manager = _manager(tmp_path, limits=_limits(model_tokens_total=200000))
    gateway = ModelGateway("deepseek_api", router=ModelRouter("deepseek_api", profiles=[profile],
        policies=[RoutingPolicy(role=ModelRole.GENERAL, candidates=["deepseek"])]),
        budget_runtime=CanonicalInvocationBudgetAdapter(manager))
    gateway.register_client(_client(profile=profile))
    request = _request(70000)
    # Canonical admission deliberately includes prompt, system, and every message
    # role/content; this is not provider-only prompt-token accounting.
    input_bytes = sum(len(text.encode("utf-8")) for text in (
        request.prompt, request.system_prompt,
        *(part for message in request.messages for part in (message["role"], message["content"])),
    ))
    try:
        response = gateway.generate(request)
        assert response.success, response.error_message
        assert len(sdk_wire) == 1
        assert sdk_wire[0]["body"]["max_tokens"] == 65536
        assert "max_completion_tokens" not in sdk_wire[0]["body"]
        state = manager.canonical_state()
        reservations = [item for item in state.reservations
                        if item.reservation_ref.reservation_kind == "model_provider_attempt_tokens"]
        assert len(reservations) == 1
        assert reservations[0].requested.model_tokens_total == input_bytes + 65536
        assert reservations[0].status == "consumed"
        assert state.used.logical_model_calls == 1
        assert state.used.model_tokens_total == 12
        assert state.active_reserved.model_tokens_total == 0
        assert request.max_tokens == 70000
    finally:
        gateway.close()
