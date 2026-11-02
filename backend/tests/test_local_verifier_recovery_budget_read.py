"""Existing-account reads over real Budget owners and actual Fake bootstrap.

Only new isolated fixture storage is used. No Provider calls or settlement
semantics are patched; admission still uses the unchanged default constructor.
"""
from pathlib import Path

import pytest

from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    BudgetManager, CanonicalBudgetIntegrityError,
)
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import (
    CanonicalInvocationBudgetRouter,
)
from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetVectorSchema
from backend.tests.test_multi_agent_v3_budget_runtime import _limits, _ref, NOW
from backend.tests.test_gate_v2_pre_write_config_bootstrap import _build_p5_supervisor
from backend.tests.test_multi_agent_v3_lifecycle_commit import _running_run


def _open(path, *, task="task_1", run="run_1", existing=False, **updates):
    material = dict(task_id=task, run_id=run, limits=_limits(),
        max_elapsed_seconds=60, storage_path=path,
        policy_ref="budget-policy://existing-read", policy_version="v1",
        clock=lambda: NOW, require_existing_account=existing)
    material.update(updates)
    return BudgetManager.canonical(**material)


def _router(path):
    return CanonicalInvocationBudgetRouter(
        lambda task, run: _open(path, task=task, run=run),
        existing_manager_provider=lambda task, run: _open(path, task=task, run=run, existing=True))


def test_missing_storage_existing_read_creates_no_file_or_directory(tmp_path):
    path = tmp_path / "absent-directory" / "budget.jsonl"
    with pytest.raises(CanonicalBudgetIntegrityError, match="does not exist"):
        _open(path, existing=True)
    assert not path.parent.exists()


def test_missing_exact_account_preserves_other_accounts(tmp_path):
    path = tmp_path / "budget.jsonl"
    _open(path)
    before = path.read_bytes()
    with pytest.raises(CanonicalBudgetIntegrityError, match="does not exist"):
        _open(path, run="foreign-run", existing=True)
    assert path.read_bytes() == before


def test_existing_terminal_receipt_reopen_is_zero_write(tmp_path):
    path = tmp_path / "budget.jsonl"
    manager = _open(path)
    manager.reserve(_ref("logical-read-proof"), CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=manager.snapshot().revision)
    settlement = manager.consume("logical-read-proof", CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=manager.snapshot().revision)
    receipt = manager.resolve_reservation("logical-read-proof")
    before = path.read_bytes()
    reopened = _open(path, existing=True)
    assert reopened.resolve_reservation("logical-read-proof") == receipt
    assert receipt.terminal_budget_revision == settlement.terminal_budget_revision
    assert path.read_bytes() == before


@pytest.mark.parametrize("changed", ["limits", "policy_ref", "policy_version", "max_elapsed_seconds"])
def test_existing_only_reopen_preserves_original_immutable_configuration_guards(tmp_path, changed):
    path = tmp_path / "budget.jsonl"
    _open(path)
    before = path.read_bytes()
    values = {"limits": _limits(logical_model_calls=3), "policy_ref": "foreign-policy",
        "policy_version": "v2", "max_elapsed_seconds": 61}
    with pytest.raises(CanonicalBudgetIntegrityError, match="reopen changed"):
        _open(path, existing=True, **{changed: values[changed]})
    assert path.read_bytes() == before


def test_router_without_existing_seam_never_calls_admission_provider(tmp_path):
    calls = []
    def admission(task, run):
        calls.append((task, run))
        return _open(tmp_path / "budget.jsonl", task=task, run=run)
    router = CanonicalInvocationBudgetRouter(admission)
    with pytest.raises(RuntimeError, match="not configured"):
        router.resolve_existing_budget_manager("task_1", "run_1")
    assert calls == []
    assert not (tmp_path / "budget.jsonl").exists()


def test_fresh_router_resolves_durable_account_without_reservation_owner_cache(tmp_path):
    path = tmp_path / "budget.jsonl"
    manager = _open(path)
    manager.reserve(_ref("held"), CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=manager.snapshot().revision)
    before = path.read_bytes()
    fresh = _router(path)
    owner = fresh.resolve_existing_budget_manager("task_1", "run_1")
    assert owner is not manager
    assert owner.resolve_reservation("held") == manager.resolve_reservation("held")
    assert path.read_bytes() == before


def test_router_missing_account_never_uses_default_admission(tmp_path):
    path = tmp_path / "missing" / "budget.jsonl"
    with pytest.raises(CanonicalBudgetIntegrityError, match="does not exist"):
        _router(path).resolve_existing_budget_manager("task_1", "run_1")
    assert not path.parent.exists()


def test_router_rejects_foreign_canonical_owner(tmp_path):
    path = tmp_path / "budget.jsonl"
    foreign = _open(path, task="foreign-task")
    before = path.read_bytes()
    router = CanonicalInvocationBudgetRouter(lambda *_: foreign,
        existing_manager_provider=lambda *_: foreign)
    with pytest.raises(RuntimeError, match="another account"):
        router.resolve_existing_budget_manager("task_1", "run_1")
    assert path.read_bytes() == before


def test_cached_manager_cannot_replace_removed_durable_account(tmp_path):
    path = tmp_path / "budget.jsonl"
    manager = _open(path)
    # Retain original bytes in this isolated corruption fixture before loss.
    (tmp_path / "budget.before-loss").write_bytes(path.read_bytes())
    path.unlink()
    router = CanonicalInvocationBudgetRouter(lambda *_: manager,
        existing_manager_provider=lambda *_: manager)
    with pytest.raises(KeyError, match="account not found"):
        router.resolve_existing_budget_manager("task_1", "run_1")
    assert not path.exists()


def _production_router(supervisor):
    coordinator = supervisor.workflow_engine.stage_service.section_execution_coordinator
    return coordinator.claim_verifier_composition.model_gateway.delegate.budget_runtime


def test_actual_fake_bootstrap_missing_budget_read_creates_no_account(tmp_path):
    supervisor, _, runs_dir = _build_p5_supervisor(tmp_path)
    try:
        router = _production_router(supervisor)
        assert isinstance(router, CanonicalInvocationBudgetRouter)
        path = runs_dir / "canonical_budget.jsonl"
        assert not path.exists()
        with pytest.raises(CanonicalBudgetIntegrityError, match="does not exist"):
            router.resolve_existing_budget_manager("absent-task", "absent-run")
        assert not path.exists()
    finally:
        supervisor.close()


def test_actual_fake_bootstrap_default_admission_and_restart_existing_read(tmp_path):
    supervisor, _, runs_dir = _build_p5_supervisor(tmp_path)
    try:
        run = _running_run(supervisor.workflow_engine.lifecycle_service, run_id="budget-read-run")
        task_id, run_id = run.identity.task_id, run.identity.run_id
        router = _production_router(supervisor)
        # The original default admission path still creates this real account.
        manager = router.provider(task_id, run_id)
        original = manager.canonical_state()
        path = runs_dir / "canonical_budget.jsonl"
        before = path.read_bytes()
        assert router.resolve_existing_budget_manager(task_id, run_id).canonical_state() == original
        assert path.read_bytes() == before
    finally:
        supervisor.close()
    reopened, _, _ = _build_p5_supervisor(tmp_path)
    try:
        fresh = _production_router(reopened)
        assert fresh.resolve_existing_budget_manager(task_id, run_id).canonical_state() == original
        assert path.read_bytes() == before
        with pytest.raises(CanonicalBudgetIntegrityError, match="does not exist"):
            fresh.resolve_existing_budget_manager(task_id, "foreign-run")
        assert path.read_bytes() == before
    finally:
        reopened.close()
