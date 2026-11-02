"""Opt-in technical guards preserve default contracts and canonical ownership."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyLoader
from apps.enterprise_document.schemas.agent_rag_v1 import (
    CanonicalBudgetVectorSchema, RunIdentitySchema, RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    CanonicalBudgetCapacityExceeded,
)
from apps.enterprise_document.workflows.top_level_business_graph import _default_node_contracts
from bootstrap.agent_quality_factory import AgentQualityFactory
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from test_gate_v2_pre_write_config_bootstrap import _build_p5_supervisor
from test_multi_agent_v3_budget_runtime import _ref


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "backend/apps/enterprise_document/config"
V2 = CONFIG / "grounded_generation_real_provider_v2.json"
V3 = CONFIG / "grounded_generation_real_provider_v3.json"
FIELDS = (
    "execute_sections_timeout_seconds",
    "evaluate_document_gate_timeout_seconds",
)
PRIOR_BYTES = {
    "grounded_generation_v1.yaml": "031fe0f31e5865f43a51381f0ab47d0182d5a76e361c63a5a6f0d15b98c72109",
    "grounded_generation_real_provider_v1.json": "cf9c95509789faca5f4639da6ea8aba447ec2de1aefd3c699a3407928cc45b5d",
    "grounded_generation_real_provider_v2.json": "633e8e355335c94863b30a15bf501742c42fad2448f69f58a3f6858399bf6e2c",
}


@pytest.fixture(autouse=True)
def explicit_test_environment(monkeypatch):
    for field in FIELDS:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.delenv("GROUNDED_GENERATION_POLICY_FILE", raising=False)


def _options():
    return RuntimeOptions.resolve(SimpleNamespace(default_model_name="fake_llm"), ROOT)


def _start_fixture_run(engine):
    # Factory construction alone does not authorize a canonical budget write.
    with pytest.raises(KeyError, match="Run lifecycle not found"):
        engine.budget_runtime("task_1", "run_1")
    record = engine.lifecycle_service.claim_new_run(RunIdentitySchema(
        task_id="task_1", run_id="run_1", tenant_id="default",
        canonical_request_sha256="a" * 64,
    ))
    started = engine.lifecycle_service.transition_run(
        "run_1", record.lifecycle_revision, RunLifecycleStatus.RUNNING,
        reason_ref="test:explicit-profile-budget-startup",
    )
    assert started.status is RunLifecycleStatus.RUNNING


def test_default_options_and_contracts_remain_unselected():
    options = _options()
    assert all(getattr(options, field) is None for field in FIELDS)
    assert SupervisorFactory._node_contracts(options) is None
    defaults = _default_node_contracts()
    assert defaults["execute_sections"].timeout_seconds == 1800
    assert defaults["evaluate_document_gate"].timeout_seconds == 1000


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("raw", ["", "not-a-number", "true", "false", "0", "-1", "nan", "inf", "-inf", "1e309"])
def test_invalid_environment_guard_fails_at_resolution(monkeypatch, field, raw):
    monkeypatch.setenv(field.upper(), raw)
    with pytest.raises(ValueError, match=field.upper()):
        _options()


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [True, False, 0, -1, float("nan"), float("inf"), float("-inf"), "3053", [], {}, 1 + 2j],
    ids=["true", "false", "zero", "negative", "nan", "infinity", "negative-infinity", "numeric-string", "list", "dict", "complex"])
def test_invalid_programmatic_guard_fails_at_construction(field, value):
    with pytest.raises(ValueError, match=field.upper()):
        replace(_options(), **{field: value})


@pytest.mark.parametrize("field", FIELDS)
def test_guard_is_resolved_once_and_positive_fractions_are_supported(monkeypatch, field):
    monkeypatch.setenv(field.upper(), "0.25")
    options = _options()
    monkeypatch.setenv(field.upper(), "9999")
    assert getattr(options, field) == 0.25
    contracts = SupervisorFactory._node_contracts(options)
    node = field.removesuffix("_timeout_seconds")
    assert contracts[node].timeout_seconds == 0.25
    assert getattr(replace(options, **{field: 2}), field) == 2


@pytest.mark.parametrize("execute,gate", [(None, None), (3053, None), (None, 5111), (3053, 5111)],
    ids=["defaults", "sections-only", "postwrite-only", "both"])
def test_actual_factory_injects_only_selected_contracts(tmp_path, monkeypatch, execute, gate):
    before = _default_node_contracts()
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(V2))
    for field, value in zip(FIELDS, (execute, gate)):
        if value is not None:
            monkeypatch.setenv(field.upper(), str(value))
    supervisor, _, _ = _build_p5_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine
        expected = dict(before)
        for node, value in (("execute_sections", execute), ("evaluate_document_gate", gate)):
            if value is not None:
                expected[node] = replace(before[node], timeout_seconds=value)
        assert engine.node_contracts == expected
        assert _default_node_contracts() == before
        _start_fixture_run(engine)
        account = engine.budget_runtime("task_1", "run_1").canonical_state()
        assert account.limits.logical_model_calls == 248
        assert (account.deadline_at - account.started_at).total_seconds() == 1956
    finally:
        supervisor.close()


def test_programmatic_contract_overrides_are_isolated():
    defaults = _default_node_contracts()
    options = replace(_options(), execute_sections_timeout_seconds=3053, evaluate_document_gate_timeout_seconds=5111)
    selected = SupervisorFactory._node_contracts(options)
    assert selected is not defaults
    assert selected["execute_sections"] == replace(defaults["execute_sections"], timeout_seconds=3053)
    assert selected["evaluate_document_gate"] == replace(defaults["evaluate_document_gate"], timeout_seconds=5111)
    selected.pop("normalize_project_input")
    assert SupervisorFactory._node_contracts(options).keys() == defaults.keys()
    assert _default_node_contracts() == defaults


def test_v3_changes_only_the_adopted_canonical_values_and_identity(monkeypatch):
    for name, digest in PRIOR_BYTES.items():
        assert hashlib.sha256((CONFIG / name).read_bytes()).hexdigest() == digest
    loader = GroundedGenerationPolicyLoader()
    previous = loader.load(V2).model_dump()
    current = loader.load(V3).model_dump()
    old, new = previous.pop("canonical_budget"), current.pop("canonical_budget")
    assert previous == current
    assert {key: value for key, value in new.items() if value != old[key]} == {
        "logical_model_calls": 414,
        "model_tokens_total": 2316528,
        "max_elapsed_seconds": 8088,
        "policy_ref": "budget-policy://multi-agent-v3/real-provider-eight-section-v3",
        "policy_version": "real-provider-v3",
    }
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(V3))
    options = _options()
    assert options.grounded_generation_policy_file == V3
    assert SupervisorFactory._node_contracts(options) is None

    class NoDispatch:
        def __getattr__(self, name):
            raise AssertionError("profile construction dispatched " + name)

    runtime = AgentQualityFactory().build(options=options, model_gateway=NoDispatch(), model_name="fake_llm")
    assert runtime.metadata["canonical_budget"] == new
    assert runtime.metadata["max_total_llm_calls"] == 35
    assert runtime.metadata["max_total_tokens"] == 24000
    assert runtime.metadata["human_review_on_exhaustion"] is True


def test_actual_v3_factory_keeps_node_defaults_without_guard_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(V3))
    supervisor, _, _ = _build_p5_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine
        assert engine.node_contracts == _default_node_contracts()
        _start_fixture_run(engine)
        state = engine.budget_runtime("task_1", "run_1").canonical_state()
        assert state.limits.logical_model_calls == 414
        assert state.limits.model_tokens_total == 2316528
        assert state.policy_version == "real-provider-v3"
        assert (state.deadline_at - state.started_at).total_seconds() == 8088
    finally:
        supervisor.close()


def _manager(tmp_path, version, clock):
    policy = GroundedGenerationPolicyLoader().load(CONFIG / f"grounded_generation_real_provider_v{version}.json").canonical_budget
    return BudgetManager.canonical(task_id="task_1", run_id="run_1",
        limits=CanonicalBudgetVectorSchema(**policy.model_dump(exclude={"max_elapsed_seconds", "policy_ref", "policy_version"})),
        max_elapsed_seconds=policy.max_elapsed_seconds, storage_path=tmp_path / "budget.jsonl",
        policy_ref=policy.policy_ref, policy_version=policy.policy_version, clock=clock)


@pytest.mark.parametrize("version", [2, 3])
def test_r7_scenario_admission_and_settlement_remain_finite(tmp_path, version):
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    manager = _manager(tmp_path, version, lambda: now)
    # Local aggregate capacity probe of the maximum-call scenario, not provider execution.
    demand = CanonicalBudgetVectorSchema(logical_model_calls=414, model_tokens_total=2140668)
    if version == 2:
        with pytest.raises(CanonicalBudgetCapacityExceeded):
            manager.reserve(_ref("scenario"), demand, expected_revision=0)
        assert manager.canonical_state().used == CanonicalBudgetVectorSchema()
        return
    manager.reserve(_ref("scenario"), demand, expected_revision=0)
    manager.consume("scenario", demand, expected_revision=1)
    settled = manager.canonical_state()
    assert settled.used == demand
    assert settled.active_reserved == CanonicalBudgetVectorSchema()
    assert settled.remaining.logical_model_calls == 0
    assert settled.remaining.model_tokens_total == 175860
    with pytest.raises(CanonicalBudgetCapacityExceeded) as denied:
        manager.reserve(_ref("beyond-calls"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=settled.revision)
    assert denied.value.resource == "logical_model_calls"
    assert manager.canonical_state() == settled


def test_v3_token_capacity_denies_while_elapsed_deadline_is_observed(tmp_path):
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    clock = [now]
    manager = _manager(tmp_path, 3, lambda: clock[0])
    before = manager.canonical_state()
    with pytest.raises(CanonicalBudgetCapacityExceeded) as denied:
        manager.reserve(_ref("too-many-tokens"), CanonicalBudgetVectorSchema(model_tokens_total=2316529), expected_revision=0)
    assert denied.value.resource == "model_tokens_total"
    assert manager.canonical_state() == before
    clock[0] = now + timedelta(seconds=8089)
    manager.reserve(_ref("after-deadline"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=0)
    current = manager.canonical_state()
    assert current.revision == 1
    assert current.started_at == before.started_at
    assert current.deadline_at == before.deadline_at < clock[0]
    assert current.limits == before.limits
    assert current.policy_ref == before.policy_ref
    assert current.active_reserved.logical_model_calls == 1
    assert current.remaining.logical_model_calls == before.limits.logical_model_calls - 1
