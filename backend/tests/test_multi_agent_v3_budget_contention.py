"""Same-operation canonical Budget CAS contention is not a provider retry."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest

from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    CanonicalBudgetRevisionConflict, CanonicalBudgetCapacityExceeded,
)
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from test_multi_agent_v3_model_tool_runtime import _manager, _request, _limits


def test_two_consumers_reread_revision_without_duplicate_budget(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    barrier = Barrier(2)
    seen = local()
    original = CanonicalInvocationBudgetAdapter._revision

    def race_once(self):
        revision = original(self)
        if not getattr(seen, "raced", False):
            seen.raced = True
            barrier.wait(timeout=5)
        return revision

    monkeypatch.setattr(CanonicalInvocationBudgetAdapter, "_revision", race_once)

    def consume(index):
        adapter = CanonicalInvocationBudgetAdapter(manager)
        request = _request().model_copy(update={"model_call_id": f"model-{index}"})
        reservation = adapter.reserve_model_logical_call(request)
        adapter.settle_model_logical_call(reservation)
        return reservation

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(consume, range(2)))
    assert len(set(reservations)) == 2
    assert manager.canonical_state().used.logical_model_calls == 2
    assert manager.canonical_state().used.true_retry_attempts == 0
    monkeypatch.setattr(CanonicalInvocationBudgetAdapter, "_revision", original)
    # Replaying settlement consumes nothing further.
    for ref in reservations:
        CanonicalInvocationBudgetAdapter(manager).settle_model_logical_call(ref)
    assert manager.canonical_state().used.logical_model_calls == 2


def test_contention_reattempts_are_bounded_and_keep_exact_identity(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    calls = []

    def always_conflict(ref, requested, **kwargs):
        calls.append((ref, requested, kwargs["expected_revision"]))
        raise CanonicalBudgetRevisionConflict("injected stale revision")

    monkeypatch.setattr(manager, "reserve", always_conflict)
    with pytest.raises(CanonicalBudgetRevisionConflict):
        CanonicalInvocationBudgetAdapter(manager).reserve_model_logical_call(_request())
    assert len(calls) == 8
    assert all(call == calls[0] for call in calls)
    assert manager.canonical_state().used.logical_model_calls == 0


def test_capacity_failure_is_not_cas_retry_or_provider_fallback(tmp_path, monkeypatch):
    manager = _manager(tmp_path, limits=_limits(logical_model_calls=0))
    original = manager.reserve
    calls = []

    def count(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "reserve", count)
    with pytest.raises(CanonicalBudgetCapacityExceeded):
        CanonicalInvocationBudgetAdapter(manager).reserve_model_logical_call(_request())
    assert calls == [1]
    assert manager.canonical_state().used.logical_model_calls == 0


def test_provider_guard_is_rechecked_when_competing_attempt_wins(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    request = _request()
    request.extra["provider_attempt_id"] = "provider-attempt-1"
    competitor = adapter._ref(
        reservation_kind="model_provider_attempt_tokens",
        owner_occurrence_ref="provider-attempt-2",
        scope_ref="section-1", operation_ref=request.model_call_id,
        attempt_id="attempt-1",
    )
    original = manager.admit_next_model_provider_attempt
    calls = []

    def competing_commit(ref, *, token_allowance, expected_revision):
        calls.append(1)
        original(competitor, token_allowance=token_allowance, expected_revision=expected_revision)
        return original(ref, token_allowance=token_allowance, expected_revision=expected_revision)

    monkeypatch.setattr(manager, "admit_next_model_provider_attempt", competing_commit)
    with pytest.raises(RuntimeError, match="unreconciled active provider-token"):
        adapter.reserve_provider_attempt(request)
    assert calls == [1]
    state = manager.canonical_state()
    assert len(state.reservations) == 1
    assert state.reservations[0].reservation_ref == competitor
    assert state.used.true_retry_attempts == 0


def test_tool_settlement_retries_only_stale_cas_once(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    adapter = CanonicalInvocationBudgetAdapter(manager)
    reservation = adapter.reserve_tool_logical_call(
        task_id="task_1", run_id="run_1", tool_operation_id="tool-1",
        scope_ref="section-1", parent_logical_operation_id="research-1", attempt_id="attempt-1",
    )
    original = manager.consume
    calls = []

    def stale_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise CanonicalBudgetRevisionConflict("injected contention before commit")
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "consume", stale_once)
    adapter.settle_tool_logical_call(reservation.reservation_ref.reservation_id)
    assert len(calls) == 2
    assert manager.canonical_state().used.logical_tool_calls == 1
    assert manager.canonical_state().used.true_retry_attempts == 0
