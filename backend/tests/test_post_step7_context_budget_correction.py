"""Behavioral regressions for post-Step7 complete-input budgeting and ordering."""
from types import SimpleNamespace

import pytest

from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime, ContextBudgetExceeded
from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from schemas.context import ContextSourceFamily, ContextRoleVisibility, ContextPriorityTier
from schemas.model import ModelRequestSchema
from test_multi_agent_v3_context_runtime import _candidate, _register, _rule, _policy, _capability, _materialize
from test_model_gateway_multi_model_runtime import _StubClient, _success


def _compiler(candidates, *, mandatory=True):
    runtime = ApplicationContextRuntime()
    state = {"candidates": tuple(candidates)}
    families = tuple(dict.fromkeys(c.source_binding.source_family for c in candidates))
    for family in families:
        _register(runtime, family, state)
    policy = _policy(*(_rule(family, visibility=ContextRoleVisibility.MUST if mandatory else ContextRoleVisibility.MAY,
                            mandatory=mandatory, tier=ContextPriorityTier.P0_MANDATORY_AUTHORITY)
                       for family in families))
    return runtime, policy


def _build(runtime, policy, window):
    return _materialize(runtime, policy=policy, capability=_capability(window=window),
                        reserved_instruction_tokens=64, reserved_output_tokens=64, safety_margin_tokens=16)


def _escaped_candidate(family=ContextSourceFamily.EVIDENCE_PACKAGE, candidate_id="escaped"):
    return _candidate(family, candidate_id=candidate_id, ref=candidate_id,
                      exact_payload={str(i): {'quoted': '\\"x\\"'} for i in range(90)})


def test_final_serialization_overflow_is_typed_budget_failure():
    runtime, policy = _compiler([_escaped_candidate()])
    initial = _build(runtime, policy, 20000)
    required = initial.budget_binding.estimated_final_input_tokens + 64 + 16
    # The complete final input is one token over; the unescaped selected payload fits.
    assert initial.budget_binding.estimated_selected_tokens + 64 + 64 + 16 < required - 1
    with pytest.raises(ContextBudgetExceeded, match="CONTEXT_BUDGET_EXCEEDED"):
        _build(runtime, policy, required - 1)


@pytest.mark.parametrize("headroom", [0, 1])
def test_complete_serialized_input_exact_and_below_boundary(headroom):
    runtime, policy = _compiler([_escaped_candidate()])
    initial = _build(runtime, policy, 20000)
    required = initial.budget_binding.estimated_final_input_tokens + 64 + 16
    final = _build(runtime, policy, required + headroom)
    assert final.budget_binding.estimated_final_input_tokens + 64 + 16 == required
    assert len(final.selected_materializations) == 1


def test_optional_final_envelope_overflow_degrades_before_return():
    runtime, policy = _compiler([_escaped_candidate()], mandatory=False)
    initial = _build(runtime, policy, 20000)
    required = initial.budget_binding.estimated_final_input_tokens + 64 + 16
    final = _build(runtime, policy, required - 1)
    assert not final.selected_materializations
    assert "DROP_OPTIONAL:escaped" in final.budget_binding.degradation_actions


def test_runtime_and_renderer_preserve_family_scope_candidate_order():
    candidates = [
        _candidate(ContextSourceFamily.EVIDENCE_PACKAGE, candidate_id="evidence", ref="e", exact_payload={"text": "evidence"}),
        _candidate(ContextSourceFamily.FACT_SNAPSHOT, candidate_id="a-last", ref="f2", scope="section:Z", exact_payload={"fact": "last"}),
        _candidate(ContextSourceFamily.FACT_SNAPSHOT, candidate_id="z-first", ref="f1", scope="section:A", exact_payload={"fact": "first"}),
    ]
    runtime, policy = _compiler(candidates)
    package = _build(runtime, policy, 12000)
    expected = ["z-first", "a-last", "evidence"]
    assert [m.candidate_id for m in package.selected_materializations] == expected
    content = package.model_input.final_input_material.inline_final_input_payload["messages"][-1]["content"]
    assert [content.index(":" + name + "]") for name in expected] == sorted(content.index(":" + name + "]") for name in expected)


def _gateway(*, window=256, provider_model="underlying-v1", provider="test_provider"):
    profile = ModelProfile(profile_id="p", model_name="logical-model", provider=provider,
                           provider_model_name=provider_model, context_window=window, max_output_tokens=64)
    router = ModelRouter("logical-model", profiles=[profile], policies=[RoutingPolicy(role=ModelRole.SECTION_GENERATION, candidates=["p"])])
    gateway = ModelGateway("logical-model", router=router)
    client = _StubClient("logical-model", _success("logical-model"))
    gateway.register_client(client)
    return gateway, client


def test_gateway_rejects_overflow_before_any_invocation():
    gateway, client = _gateway()
    request = ModelRequestSchema(model_call_id="oversized", task_id="t", run_id="r", created_at="now",
                                 model_role="section_generation", prompt="长" * 300, max_tokens=64)
    try:
        with pytest.raises(ValueError, match="[Cc]ontext|CONTEXT"):
            gateway.generate(request)
        assert client.calls == []
        assert gateway.usage_snapshot().get("logical_model_calls", 0) == 0
    finally:
        gateway.close()


def test_writer_capability_fingerprint_tracks_concrete_provider_model():
    project = SimpleNamespace(generation_requirements=SimpleNamespace(extra={}, max_tokens_per_section=64))
    fingerprints = []
    for model in ("underlying-v1", "underlying-v2"):
        gateway, _ = _gateway(window=32768, provider_model=model)
        composer = object.__new__(TargetWriterContextComposer)
        composer.model_gateway = gateway
        fingerprints.append(composer._model_capability(project_input=project).capability_semantic_fingerprint)
        gateway.close()
    assert fingerprints[0] != fingerprints[1]
