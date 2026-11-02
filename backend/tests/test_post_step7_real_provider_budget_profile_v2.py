"""R1-informed static profile preserves owner policy and enforces finite capacity."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyLoader
from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
from apps.enterprise_document.services.agent_rag_v1 import BudgetManager, CanonicalBudgetCapacityExceeded
from bootstrap.agent_quality_factory import AgentQualityFactory
from bootstrap.runtime_options import RuntimeOptions
from test_multi_agent_v3_budget_runtime import _ref

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "backend/apps/enterprise_document/config"
PROFILE = CONFIG / "grounded_generation_real_provider_v2.json"


def test_r1_profile_is_explicit_and_preserves_business_and_prior_profile(monkeypatch):
    loader = GroundedGenerationPolicyLoader()
    old = loader.load(CONFIG / "grounded_generation_real_provider_v1.json").model_dump()
    new = loader.load(PROFILE).model_dump()
    previous, current = old.pop("canonical_budget"), new.pop("canonical_budget")
    assert old == new
    assert (previous["logical_model_calls"], previous["model_tokens_total"], previous["max_elapsed_seconds"]) == (184, 988293, 800)
    assert {k: v for k, v in current.items() if v != previous[k]} == {
        "logical_model_calls": 248, "model_tokens_total": 1376143,
        "max_elapsed_seconds": 1956,
        "policy_ref": "budget-policy://multi-agent-v3/real-provider-eight-section-v2",
        "policy_version": "real-provider-v2",
    }
    monkeypatch.setenv("GROUNDED_GENERATION_POLICY_FILE", str(PROFILE))
    options = RuntimeOptions.resolve(SimpleNamespace(default_model_name="deepseek"), ROOT,
        enable_agent_self_rag=True)
    assert options.grounded_generation_policy_file == PROFILE

    class NoDispatch:
        def __getattr__(self, name):
            raise AssertionError("Profile construction dispatched " + name)

    runtime = AgentQualityFactory().build(options=options, model_gateway=NoDispatch(), model_name="deepseek")
    assert runtime.metadata["canonical_budget"] == current
    assert runtime.metadata["max_total_llm_calls"] == 35
    assert runtime.metadata["max_total_tokens"] == 24000
    assert runtime.metadata["human_review_on_exhaustion"] is True


@pytest.mark.parametrize("version", [1, 2])
def test_r1_expected_workload_capacity_is_explicit_and_still_finite(tmp_path, version):
    config = GroundedGenerationPolicyLoader().load(CONFIG / f"grounded_generation_real_provider_v{version}.json").canonical_budget
    values = config.model_dump(exclude={"max_elapsed_seconds", "policy_ref", "policy_version"})
    manager = BudgetManager.canonical(task_id="task_1", run_id="run_1",
        limits=CanonicalBudgetVectorSchema(**values), max_elapsed_seconds=config.max_elapsed_seconds,
        storage_path=tmp_path / "budget.jsonl", policy_ref=config.policy_ref,
        policy_version=config.policy_version, clock=lambda: datetime(2026, 9, 14, tzinfo=timezone.utc))
    # Aggregate vectors are a local capacity probe, not fabricated provider execution.
    demand = CanonicalBudgetVectorSchema(logical_model_calls=248, model_tokens_total=1259226)
    if version == 1:
        with pytest.raises(CanonicalBudgetCapacityExceeded):
            manager.reserve(_ref("scenario"), demand, expected_revision=0)
        assert manager.canonical_state().used == CanonicalBudgetVectorSchema()
        return
    manager.reserve(_ref("scenario"), demand, expected_revision=0)
    manager.consume("scenario", demand, expected_revision=1)
    before = manager.canonical_state()
    assert before.remaining.model_tokens_total == 116917
    assert before.remaining.logical_model_calls == 0
    with pytest.raises(CanonicalBudgetCapacityExceeded) as denied:
        manager.reserve(_ref("beyond-scenario"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=before.revision)
    assert denied.value.resource == "logical_model_calls"
    assert manager.canonical_state() == before
