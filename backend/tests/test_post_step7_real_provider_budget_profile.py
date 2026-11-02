"""Opt-in static calibration preserves the frozen default and finite admission."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyLoader
from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
from apps.enterprise_document.services.agent_rag_v1 import BudgetManager, CanonicalBudgetCapacityExceeded
from bootstrap.runtime_options import RuntimeOptions
from test_multi_agent_v3_budget_runtime import _ref


ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
PROFILE = DEFAULT.with_name("grounded_generation_real_provider_v1.json")


def test_explicit_startup_profile_preserves_all_business_and_default_fields(monkeypatch):
    loader = GroundedGenerationPolicyLoader()
    original = loader.load(DEFAULT)
    profile = loader.load(PROFILE)
    before, after = original.model_dump(), profile.model_dump()
    old_budget, new_budget = before.pop("canonical_budget"), after.pop("canonical_budget")
    assert before == after
    expected_changes = {
        "logical_model_calls": 184, "model_tokens_total": 988293,
        "max_elapsed_seconds": 800,
        "policy_ref": "budget-policy://multi-agent-v3/real-provider-eight-section-v1",
        "policy_version": "real-provider-v1",
    }
    assert {k: v for k, v in new_budget.items() if v != old_budget[k]} == expected_changes
    assert (old_budget["logical_model_calls"], old_budget["model_tokens_total"], old_budget["max_elapsed_seconds"]) == (44, 24000, 120)
    monkeypatch.delenv("GROUNDED_GENERATION_POLICY_FILE", raising=False)
    options = RuntimeOptions.resolve(SimpleNamespace(default_model_name="deepseek"), ROOT)
    assert options.grounded_generation_policy_file == DEFAULT
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(PROFILE))
    selected = RuntimeOptions.resolve(SimpleNamespace(default_model_name="deepseek"), ROOT)
    assert loader.load(selected.grounded_generation_policy_file) == profile


@pytest.mark.parametrize("calibrated", [False, True], ids=["frozen-default", "real-profile"])
def test_historical_prefix_writer_admission_and_finite_capacity(tmp_path, calibrated):
    policy = GroundedGenerationPolicyLoader().load(PROFILE if calibrated else DEFAULT).canonical_budget
    values = policy.model_dump()
    for key in ("max_elapsed_seconds", "policy_ref", "policy_version"):
        values.pop(key)
    manager = BudgetManager.canonical(task_id="task_1", run_id="run_1",
        limits=CanonicalBudgetVectorSchema(**values), max_elapsed_seconds=policy.max_elapsed_seconds,
        storage_path=tmp_path / "budget.jsonl", policy_ref=policy.policy_ref,
        policy_version=policy.policy_version, clock=lambda: datetime(2026, 9, 14, tzinfo=timezone.utc))
    prefix = CanonicalBudgetVectorSchema(logical_model_calls=19, model_tokens_total=16312)
    manager.reserve(_ref("prefix"), prefix, expected_revision=0)
    manager.consume("prefix", prefix, expected_revision=1)
    writer = CanonicalBudgetVectorSchema(logical_model_calls=1, model_tokens_total=31329)
    if not calibrated:
        with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:
            manager.reserve(_ref("writer"), writer, expected_revision=2)
        assert (caught.value.limit, caught.value.remaining, caught.value.required) == (24000, 7688, 31329)
        return
    manager.reserve(_ref("writer"), writer, expected_revision=2)
    state = manager.canonical_state()
    assert state.active_reserved.model_tokens_total == 31329
    assert state.remaining.model_tokens_total == 988293 - 16312 - 31329
    with pytest.raises(CanonicalBudgetCapacityExceeded):
        manager.reserve(_ref("finite-limit"), CanonicalBudgetVectorSchema(
            model_tokens_total=state.remaining.model_tokens_total + 1), expected_revision=state.revision)
    assert manager.canonical_state() == state
