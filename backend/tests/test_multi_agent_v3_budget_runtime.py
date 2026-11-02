from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import inspect
import json
import multiprocessing
import os
from pathlib import Path
from queue import Empty

import pytest

from apps.enterprise_document.quality.budget import WorkflowBudget
from apps.enterprise_document.schemas.agent_rag_v1 import (
    CANONICAL_BUDGET_DIMENSIONS,
    BudgetReservationRefSchema,
    CanonicalBudgetStateSchema,
    CanonicalBudgetVectorSchema,
    BudgetVectorSchema,
    TaskBudgetLimitsSchema,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    CanonicalBudgetCapacityExceeded,
    CanonicalBudgetDraining,
    CanonicalBudgetIdentityConflict,
    CanonicalBudgetIntegrityError,
    CanonicalBudgetRevisionConflict,
)
from core.persistence.crash_safe_jsonl import canonical_json_sha256


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _limits(**updates: int) -> CanonicalBudgetVectorSchema:
    values = {
        "logical_model_calls": 2,
        "logical_tool_calls": 2,
        "model_tokens_total": 100,
        "research_rounds": 1,
        "repair_attempts": 1,
        "generation_attempts": 2,
        "replan_attempts": 1,
        "true_retry_attempts": 1,
    }
    values.update(updates)
    return CanonicalBudgetVectorSchema(**values)


def _manager(
    tmp_path,
    *,
    limits: CanonicalBudgetVectorSchema | None = None,
    clock=None,
) -> BudgetManager:
    return BudgetManager.canonical(
        task_id="task_1",
        run_id="run_1",
        limits=limits or _limits(),
        max_elapsed_seconds=60,
        storage_path=tmp_path / "canonical_budget.jsonl",
        policy_ref="budget-policy://tests/canonical-v1",
        policy_version="v1",
        clock=clock or (lambda: NOW),
    )


def _ref(
    reservation_id: str,
    *,
    operation_ref: str | None = None,
    reservation_kind: str = "logical_model_call",
) -> BudgetReservationRefSchema:
    return BudgetReservationRefSchema(
        reservation_id=reservation_id,
        task_id="task_1",
        run_id="run_1",
        reservation_kind=reservation_kind,
        owner_occurrence_ref=f"occurrence://{reservation_id}",
        scope_ref="attempt-scope://section-1",
        operation_ref=operation_ref or f"operation://{reservation_id}",
        attempt_id="attempt_1",
    )


def test_fresh_canonical_budget_has_exact_frozen_dimensions(tmp_path) -> None:
    manager = _manager(tmp_path)
    state = manager.canonical_state()

    assert tuple(CANONICAL_BUDGET_DIMENSIONS) == (
        "logical_model_calls",
        "logical_tool_calls",
        "model_tokens_total",
        "research_rounds",
        "repair_attempts",
        "generation_attempts",
        "replan_attempts",
        "true_retry_attempts",
    )
    assert tuple(CanonicalBudgetVectorSchema.model_fields) == tuple(
        CANONICAL_BUDGET_DIMENSIONS
    )
    assert state.revision == 0
    assert state.used == CanonicalBudgetVectorSchema()
    assert state.active_reserved == CanonicalBudgetVectorSchema()
    assert state.remaining == _limits()
    assert state.draining is False
    assert state.reservations == []


def test_reserve_consume_release_and_remaining_are_canonical(tmp_path) -> None:
    manager = _manager(tmp_path)

    first = manager.reserve(
        _ref("r1"),
        CanonicalBudgetVectorSchema(
            logical_model_calls=1,
            model_tokens_total=20,
        ),
        expected_revision=0,
    )
    assert first.status == "active"
    state = manager.canonical_state()
    assert state.revision == 1
    assert state.active_reserved.logical_model_calls == 1
    assert state.remaining.logical_model_calls == 1
    assert state.remaining.model_tokens_total == 80

    settled = manager.consume(
        "r1",
        CanonicalBudgetVectorSchema(
            logical_model_calls=1,
            model_tokens_total=12,
        ),
        expected_revision=1,
    )
    assert settled.status == "consumed"
    state = manager.canonical_state()
    assert state.revision == 2
    assert state.used.logical_model_calls == 1
    assert state.used.model_tokens_total == 12
    assert state.active_reserved == CanonicalBudgetVectorSchema()
    assert state.remaining.model_tokens_total == 88

    manager.reserve(
        _ref("r2", reservation_kind="research_round"),
        CanonicalBudgetVectorSchema(research_rounds=1),
        expected_revision=2,
    )
    released = manager.release("r2", expected_revision=3)
    assert released.status == "released"
    state = manager.canonical_state()
    assert state.revision == 4
    assert state.used.research_rounds == 0
    assert state.active_reserved.research_rounds == 0
    assert state.remaining.research_rounds == 1


def test_budget_identity_is_task_run_attempt_operation_scoped_not_trace_scoped(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    fields = set(BudgetReservationRefSchema.model_fields)
    assert {
        "task_id",
        "run_id",
        "attempt_id",
        "operation_ref",
        "scope_ref",
    }.issubset(fields)
    assert "trace_id" not in fields
    assert "invocation_id" not in fields

    wrong_run = _ref("wrong-run").model_copy(update={"run_id": "run_other"})
    with pytest.raises(CanonicalBudgetIntegrityError, match="run_id"):
        manager.reserve(
            wrong_run,
            CanonicalBudgetVectorSchema(logical_model_calls=1),
            expected_revision=0,
        )

    assert manager.canonical_state().revision == 0


def test_same_reservation_same_fingerprint_is_replay_safe(tmp_path) -> None:
    manager = _manager(tmp_path)
    ref = _ref("same")
    vector = CanonicalBudgetVectorSchema(logical_model_calls=1)

    first = manager.reserve(ref, vector, expected_revision=0)
    replay = manager.reserve(ref, vector, expected_revision=0)

    assert replay == first
    state = manager.canonical_state()
    assert state.revision == 1
    assert state.active_reserved.logical_model_calls == 1
    assert len(state.reservations) == 1


def test_same_reservation_different_fingerprint_fails_closed(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("collision"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )

    with pytest.raises(CanonicalBudgetIdentityConflict):
        manager.reserve(
            _ref(
                "collision",
                operation_ref="operation://different-semantic-work",
            ),
            CanonicalBudgetVectorSchema(logical_model_calls=1),
            expected_revision=1,
        )

    assert manager.canonical_state().revision == 1


def test_duplicate_consume_never_double_charges(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("consume-once"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )
    actual = CanonicalBudgetVectorSchema(logical_model_calls=1)

    first = manager.consume("consume-once", actual, expected_revision=1)
    replay = manager.consume("consume-once", actual, expected_revision=1)

    assert replay == first
    state = manager.canonical_state()
    assert state.revision == 2
    assert state.used.logical_model_calls == 1
    assert state.active_reserved.logical_model_calls == 0

    with pytest.raises(CanonicalBudgetIdentityConflict):
        manager.consume(
            "consume-once",
            CanonicalBudgetVectorSchema(
                logical_model_calls=1,
                model_tokens_total=1,
            ),
            expected_revision=2,
        )


def test_stale_revision_cannot_mutate_active_consumption_or_release(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("stale"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )

    with pytest.raises(CanonicalBudgetRevisionConflict):
        manager.consume(
            "stale",
            CanonicalBudgetVectorSchema(logical_model_calls=1),
            expected_revision=0,
        )
    with pytest.raises(CanonicalBudgetRevisionConflict):
        manager.release("stale", expected_revision=0)

    state = manager.canonical_state()
    assert state.revision == 1
    assert state.used.logical_model_calls == 0
    assert state.active_reserved.logical_model_calls == 1


def test_over_budget_rejects_without_global_draining(tmp_path) -> None:
    manager = _manager(
        tmp_path,
        limits=_limits(logical_model_calls=1, logical_tool_calls=1),
    )
    manager.reserve(
        _ref("model-full"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )

    with pytest.raises(CanonicalBudgetCapacityExceeded) as captured:
        manager.reserve(
            _ref("model-over"),
            CanonicalBudgetVectorSchema(logical_model_calls=1),
            expected_revision=1,
        )
    assert captured.value.resource == "logical_model_calls"

    state = manager.canonical_state()
    assert "logical_model_calls" in state.exhausted_dimensions
    assert state.draining is False

    manager.reserve(
        _ref("tool-still-legal", reservation_kind="logical_tool_call"),
        CanonicalBudgetVectorSchema(logical_tool_calls=1),
        expected_revision=1,
    )
    assert manager.canonical_state().revision == 2


def test_draining_rejects_new_expensive_work_but_allows_legal_cleanup(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("already-authorized"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )
    manager.start_draining(expected_revision=1)

    with pytest.raises(CanonicalBudgetDraining):
        manager.reserve(
            _ref("new-work"),
            CanonicalBudgetVectorSchema(logical_tool_calls=1),
            expected_revision=2,
        )

    settled = manager.consume(
        "already-authorized",
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=2,
    )
    assert settled.status == "consumed"
    state = manager.canonical_state()
    assert state.draining is True
    assert state.used.logical_model_calls == 1


def test_concurrent_replay_of_same_semantic_reservation_charges_once(
    tmp_path,
) -> None:
    manager_a = _manager(tmp_path)
    manager_b = _manager(tmp_path)
    ref = _ref("concurrent-same")
    vector = CanonicalBudgetVectorSchema(logical_model_calls=1)

    def reserve(manager: BudgetManager):
        return manager.reserve(ref, vector, expected_revision=0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, [manager_a, manager_b]))

    assert results[0] == results[1]
    state = manager_a.canonical_state()
    assert state.revision == 1
    assert state.active_reserved.logical_model_calls == 1
    assert len(state.reservations) == 1


def test_concurrent_different_reservations_use_revision_cas(tmp_path) -> None:
    manager_a = _manager(
        tmp_path,
        limits=_limits(logical_model_calls=2),
    )
    manager_b = _manager(
        tmp_path,
        limits=_limits(logical_model_calls=2),
    )

    def reserve(manager: BudgetManager, reservation_id: str):
        try:
            return manager.reserve(
                _ref(reservation_id),
                CanonicalBudgetVectorSchema(logical_model_calls=1),
                expected_revision=0,
            )
        except CanonicalBudgetRevisionConflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(reserve, manager_a, "concurrent-a"),
            executor.submit(reserve, manager_b, "concurrent-b"),
        ]
        results = [future.result() for future in futures]

    assert sum(item == "stale" for item in results) == 1
    state = manager_a.canonical_state()
    assert state.revision == 1
    assert state.active_reserved.logical_model_calls == 1


def test_durable_reopen_preserves_canonical_state(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("durable"),
        CanonicalBudgetVectorSchema(
            logical_model_calls=1,
            model_tokens_total=20,
        ),
        expected_revision=0,
    )
    manager.consume(
        "durable",
        CanonicalBudgetVectorSchema(
            logical_model_calls=1,
            model_tokens_total=13,
        ),
        expected_revision=1,
    )

    reopened = _manager(tmp_path)
    state = reopened.canonical_state()
    assert state.revision == 2
    assert state.used.logical_model_calls == 1
    assert state.used.model_tokens_total == 13
    assert reopened.resolve_reservation("durable").status == "consumed"


def test_durable_reopen_rejects_deadline_policy_drift(tmp_path) -> None:
    _manager(tmp_path)

    with pytest.raises(
        CanonicalBudgetIntegrityError,
        match="immutable deadline policy",
    ):
        BudgetManager.canonical(
            task_id="task_1",
            run_id="run_1",
            limits=_limits(),
            max_elapsed_seconds=61,
            storage_path=tmp_path / "canonical_budget.jsonl",
            policy_ref="budget-policy://tests/canonical-v1",
            policy_version="v1",
            clock=lambda: NOW,
        )


def test_crash_after_durable_consumption_replay_cannot_charge_twice(
    tmp_path,
) -> None:
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("crash-window"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )
    manager.consume(
        "crash-window",
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=1,
    )

    recovered = _manager(tmp_path)
    recovered.consume(
        "crash-window",
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=1,
    )
    state = recovered.canonical_state()
    assert state.revision == 2
    assert state.used.logical_model_calls == 1


def test_actual_usage_overrun_is_accounted_not_discarded(tmp_path) -> None:
    manager = _manager(
        tmp_path,
        limits=_limits(model_tokens_total=20),
    )
    manager.reserve(
        _ref("token-overrun", reservation_kind="provider_token_attempt"),
        CanonicalBudgetVectorSchema(model_tokens_total=10),
        expected_revision=0,
    )

    manager.consume(
        "token-overrun",
        CanonicalBudgetVectorSchema(model_tokens_total=12),
        expected_revision=1,
    )
    state = manager.canonical_state()
    assert state.used.model_tokens_total == 12
    assert state.remaining.model_tokens_total == 8


def test_elapsed_deadline_observes_without_blocking_finite_operation_start(tmp_path) -> None:
    current = [NOW]
    manager = _manager(tmp_path, clock=lambda: current[0])
    current[0] = NOW + timedelta(seconds=60)

    manager.reserve(
        _ref("late"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )
    state = manager.canonical_state()
    assert state.revision == 1
    assert state.deadline_at == current[0]
    assert state.remaining.logical_model_calls == 1


def test_returned_state_projection_cannot_manufacture_capacity(tmp_path) -> None:
    manager = _manager(
        tmp_path,
        limits=_limits(logical_model_calls=1),
    )
    manager.reserve(
        _ref("only-call"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )

    projection = manager.snapshot()
    assert isinstance(projection, CanonicalBudgetStateSchema)
    projection.remaining.logical_model_calls = 999
    projection.active_reserved.logical_model_calls = 0

    with pytest.raises(CanonicalBudgetCapacityExceeded):
        manager.reserve(
            _ref("forged-extra"),
            CanonicalBudgetVectorSchema(logical_model_calls=1),
            expected_revision=1,
        )
    assert manager.canonical_state().active_reserved.logical_model_calls == 1


def test_checkpoint_projection_has_no_canonical_restore_authority(tmp_path) -> None:
    limits = _limits(logical_model_calls=1)
    manager = _manager(
        tmp_path,
        limits=limits,
    )
    manager.reserve(
        _ref("checkpoint-bound"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )

    forged_checkpoint = {
        "budget_state": {
            "revision": 999,
            "remaining": {"logical_model_calls": 999},
            "used": {"logical_model_calls": 0},
        }
    }
    assert forged_checkpoint["budget_state"]["remaining"]["logical_model_calls"] == 999

    reopened = _manager(tmp_path, limits=limits)
    state = reopened.canonical_state()
    assert state.revision == 1
    assert state.remaining.logical_model_calls == 0
    assert "checkpoint" not in inspect.signature(BudgetManager.canonical).parameters
    assert reopened.canonical_limits == limits


def test_workflow_budget_cannot_override_canonical_budget(tmp_path) -> None:
    manager = _manager(
        tmp_path,
        limits=_limits(logical_model_calls=1),
    )
    manager.reserve(
        _ref("canonical-one"),
        CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )

    compatibility_projection = WorkflowBudget(
        max_retrieval_rounds=100,
        max_rewrite_rounds=100,
        max_total_llm_calls=100,
        max_total_tokens=100000,
    )
    compatibility_projection.reserve_llm_call(max_tokens=1000)

    with pytest.raises(CanonicalBudgetCapacityExceeded):
        manager.reserve(
            _ref("canonical-two"),
            CanonicalBudgetVectorSchema(logical_model_calls=1),
            expected_revision=1,
        )
    assert manager.canonical_state().revision == 1


def test_repair_budget_has_one_canonical_hard_dimension() -> None:
    assert "repair_attempts" in CANONICAL_BUDGET_DIMENSIONS
    assert "rewrite_rounds" not in CANONICAL_BUDGET_DIMENSIONS
    assert "total_rewrites" not in CANONICAL_BUDGET_DIMENSIONS
    assert "document_repair_tasks" not in CANONICAL_BUDGET_DIMENSIONS


# Phase 1 owner controls use only native temporary ledgers. The old public
# sequence is deliberately retained as the stable, non-concurrent A oracle.
def _old_state_with_reservations(manager, reservation_ids):
    records = tuple(manager.resolve_reservation(value) for value in reservation_ids)
    return manager.canonical_state(), records


def _seed_read_consolidation(manager):
    manager.reserve(
        _ref("logical"), CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )
    manager.consume(
        "logical", CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=1,
    )
    manager.reserve(
        _ref("tokens", reservation_kind="model_provider_attempt_tokens"),
        CanonicalBudgetVectorSchema(model_tokens_total=20), expected_revision=2,
    )
    manager.consume(
        "tokens", CanonicalBudgetVectorSchema(model_tokens_total=13),
        expected_revision=3,
    )


def _read_error_chain(error):
    """Keep exception identity, args, text and explicit/implicit cause layers."""
    layers = []
    while error is not None:
        layers.append((
            type(error).__module__, type(error).__qualname__, error.args,
            str(error), error.__suppress_context__,
        ))
        error = error.__cause__ or (
            error.__context__ if not error.__suppress_context__ else None
        )
    return tuple(layers)


def _observe_owner_read(manager, reservation_ids, *, aggregate):
    try:
        state, records = (
            manager.read_state_with_reservations(reservation_ids)
            if aggregate else _old_state_with_reservations(manager, reservation_ids)
        )
    except Exception as error:
        return "error", _read_error_chain(error)
    wire = state.model_dump(mode="json")
    return (
        "ok", wire, tuple(item.model_dump(mode="json") for item in records),
        canonical_json_sha256(wire),
    )


def _verification_cache(manager):
    return (
        list(manager._canonical_verified_lines),
        {key: value.model_copy(deep=True)
         for key, value in manager._canonical_verified_accounts.items()},
    )


def _restore_verification_cache(manager, cache):
    lines, accounts = cache
    manager._canonical_verified_lines = list(lines)
    manager._canonical_verified_accounts = {
        key: value.model_copy(deep=True) for key, value in accounts.items()
    }


@pytest.mark.parametrize("ids", [
    ("logical", "tokens"), ("tokens", "logical"), ("tokens",),
    (" logical ", "tokens", "logical"),
])
def test_aggregate_owner_order_complete_values_hash_and_no_business_write(
    tmp_path, ids,
) -> None:
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    before = manager.storage_path.read_bytes()
    expected = _observe_owner_read(manager, ids, aggregate=False)
    actual = _observe_owner_read(manager, ids, aggregate=True)
    assert actual == expected
    assert actual[0] == "ok"
    assert manager.storage_path.read_bytes() == before
    state, records = manager.read_state_with_reservations(("logical", "tokens"))
    assert [row.terminal_budget_revision for row in records] == [2, 4]
    assert state.revision == 4
    assert records[0].terminal_budget_revision != state.revision
    for record in records:
        assert record == next(
            row for row in state.reservations
            if row.reservation_ref.reservation_id == record.reservation_ref.reservation_id
        )


@pytest.mark.parametrize("container", [(), [], "logical", b"logical", iter(("logical",))])
def test_aggregate_owner_rejects_non_sequence_and_empty_containers_before_load(
    tmp_path, monkeypatch, container,
) -> None:
    manager = _manager(tmp_path)
    def unexpected_load():
        pytest.fail("invalid aggregate container reached the ledger")
    monkeypatch.setattr(manager, "_canonical_current_unlocked", unexpected_load)
    with pytest.raises(ValueError) as captured:
        manager.read_state_with_reservations(container)
    assert captured.value.args == ("reservation_ids must be a non-empty sequence",)


@pytest.mark.parametrize("blank", [None, "", " \t ", 0, False])
def test_aggregate_owner_first_blank_preserves_exact_error_without_read(
    tmp_path, monkeypatch, blank,
) -> None:
    manager = _manager(tmp_path)
    expected = _observe_owner_read(manager, (blank, "tokens"), aggregate=False)
    def unexpected_load():
        pytest.fail("blank first ID reached the ledger")
    monkeypatch.setattr(manager, "_canonical_current_unlocked", unexpected_load)
    assert _observe_owner_read(manager, (blank, "tokens"), aggregate=True) == expected
    assert expected[1][0][2] == ("reservation_id is required",)


@pytest.mark.parametrize("ids", [
    (" missing-first ", "tokens"), ("logical", " missing-second "),
    ("missing-first", "missing-second"), ("missing-first", " "),
    ("logical", " "),
])
def test_aggregate_owner_sequential_missing_and_blank_error_equivalence(tmp_path, ids):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    before = manager.storage_path.read_bytes()
    expected = _observe_owner_read(manager, ids, aggregate=False)
    actual = _observe_owner_read(manager, ids, aggregate=True)
    assert actual == expected
    assert actual[0] == "error"
    assert manager.storage_path.read_bytes() == before


def test_aggregate_owner_normalizes_each_id_once_after_prior_lookup(tmp_path):
    manager = _manager(tmp_path)
    manager.reserve(
        _ref("7"), CanonicalBudgetVectorSchema(logical_model_calls=1),
        expected_revision=0,
    )
    calls = []
    class Id:
        def __init__(self, text):
            self.text = text
        def __str__(self):
            calls.append(self.text)
            return self.text
    state, records = manager.read_state_with_reservations((Id(" 7 "), 7, Id("7")))
    assert calls == [" 7 ", "7"]
    assert len(records) == 3
    assert all(row == state.reservations[0] for row in records)
    calls.clear()
    with pytest.raises(KeyError) as captured:
        manager.read_state_with_reservations(("absent", Id("later")))
    assert captured.value.args == ("absent",)
    assert calls == []


def test_aggregate_owner_deep_copies_state_records_duplicates_and_cache(tmp_path):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    before = manager.storage_path.read_bytes()
    expected = manager.canonical_state()
    state, records = manager.read_state_with_reservations(("logical", "logical", "tokens"))
    first = next(row for row in state.reservations
                 if row.reservation_ref.reservation_id == "logical")
    assert records[0] == records[1] == first
    assert records[0] is not records[1] and records[0] is not first
    assert records[0].reservation_ref is not records[1].reservation_ref
    assert records[0].requested is not records[1].requested
    assert records[0].actual is not first.actual
    records[0].requested.logical_model_calls = 999
    records[0].reservation_ref.operation_ref = "operation://forged"
    assert records[1] == first == expected.reservations[0]
    first.actual.logical_model_calls = 777
    state.used.logical_model_calls = 777
    state.reservations.clear()
    assert records[1] == expected.reservations[0]
    assert manager.canonical_state() == expected
    assert manager.read_state_with_reservations(("logical",))[1][0] == expected.reservations[0]
    assert manager.storage_path.read_bytes() == before


@pytest.mark.parametrize("cold", [False, True])
def test_aggregate_owner_single_load_read_and_selection_map_preserve_loader_maps(
    tmp_path, monkeypatch, cold,
):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    if cold:
        _restore_verification_cache(manager, ([], {}))
    calls = {"current": 0, "lines": 0, "maps": []}
    current = manager._canonical_current_unlocked
    lines = manager._canonical_log.read_complete_lines
    reservation_map = BudgetManager._reservation_map
    def traced_current():
        calls["current"] += 1
        return current()
    def traced_lines():
        calls["lines"] += 1
        return lines()
    def traced_map(state):
        calls["maps"].append(inspect.currentframe().f_back.f_code.co_name)
        return reservation_map(state)
    def forbidden_public(*args, **kwargs):
        pytest.fail("aggregate recursively used a public locking read")
    monkeypatch.setattr(manager, "_canonical_current_unlocked", traced_current)
    monkeypatch.setattr(manager._canonical_log, "read_complete_lines", traced_lines)
    monkeypatch.setattr(BudgetManager, "_reservation_map", staticmethod(traced_map))
    monkeypatch.setattr(manager, "canonical_state", forbidden_public)
    monkeypatch.setattr(manager, "resolve_reservation", forbidden_public)
    state, records = manager.read_state_with_reservations(("logical", "tokens", "logical"))
    assert state.revision == 4 and len(records) == 3
    assert calls["current"] == calls["lines"] == 1
    assert calls["maps"].count("read_state_with_reservations") == 1
    transition_maps = calls["maps"].count("_validate_canonical_transition")
    assert transition_maps == (8 if cold else 0)
    assert len(calls["maps"]) == 1 + transition_maps


def test_aggregate_owner_is_canonical_only_and_legacy_resolution_remains_valid():
    manager = BudgetManager(
        task_id="legacy", clock=lambda: NOW,
        limits=TaskBudgetLimitsSchema(
            max_model_calls=1, max_tool_calls=1, max_total_replans=0,
            max_total_rewrites=0, max_document_repair_tasks=0,
            max_wall_clock_seconds=60,
        ),
    )
    expected = manager.reserve("legacy-one", BudgetVectorSchema(model_calls=1))
    with pytest.raises(RuntimeError) as captured:
        manager.read_state_with_reservations(("legacy-one",))
    assert captured.value.args == ("read_state_with_reservations is canonical Budget only",)
    assert manager.resolve_reservation(" legacy-one ") == expected


@pytest.mark.parametrize("remove_file", [False, True])
def test_aggregate_owner_missing_account_and_existing_only_never_recreate(tmp_path, remove_file):
    manager = _manager(tmp_path)
    path = manager.storage_path
    if remove_file:
        path.unlink()
    else:
        path.write_bytes(b"")
    expected = _observe_owner_read(manager, ("logical",), aggregate=False)
    assert _observe_owner_read(manager, ("logical",), aggregate=True) == expected
    assert expected[1][0][2] == ("canonical Budget account not found: task_1/run_1",)
    assert path.exists() is not remove_file
    if path.exists():
        assert path.read_bytes() == b""
    with pytest.raises(CanonicalBudgetIntegrityError) as captured:
        BudgetManager.canonical(
            task_id="task_1", run_id="run_1", limits=_limits(),
            max_elapsed_seconds=60, storage_path=path,
            policy_ref="budget-policy://tests/canonical-v1", policy_version="v1",
            clock=lambda: NOW, require_existing_account=True,
        )
    assert captured.value.args == ("exact canonical Budget account does not exist",)
    assert path.exists() is not remove_file


def test_aggregate_owner_initializes_only_coordination_lock_when_absent(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    path = manager.storage_path
    before = path.read_bytes()
    manager._canonical_lock_path.unlink()
    import core.persistence.interprocess_lock as lock_module
    fsyncs = []
    fsync = lock_module.os.fsync
    def traced_fsync(fd):
        fsyncs.append(fd)
        return fsync(fd)
    monkeypatch.setattr(lock_module.os, "fsync", traced_fsync)
    manager.read_state_with_reservations(("logical", "tokens"))
    assert manager._canonical_lock_path.read_bytes() == b"\0"
    assert len(fsyncs) == 1
    assert path.read_bytes() == before
    manager.read_state_with_reservations(("logical", "tokens"))
    assert len(fsyncs) == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("tail", [b'{"half":', b'{"complete":true}', b'\xff'])
@pytest.mark.parametrize("cold", [False, True])
def test_aggregate_owner_torn_tail_matches_old_recovery_and_fsync(
    tmp_path, monkeypatch, tail, cold,
):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    committed = manager.storage_path.read_bytes()
    cache = ([], {}) if cold else _verification_cache(manager)
    import core.persistence.crash_safe_jsonl as jsonl_module
    fsync = jsonl_module.os.fsync
    fsync_count = []
    def traced_fsync(fd):
        fsync_count.append(fd)
        return fsync(fd)
    monkeypatch.setattr(jsonl_module.os, "fsync", traced_fsync)
    observations = []
    for aggregate in (False, True):
        manager.storage_path.write_bytes(committed + tail)
        _restore_verification_cache(manager, cache)
        before_count = len(fsync_count)
        observations.append(_observe_owner_read(manager, ("logical", "tokens"), aggregate=aggregate))
        assert manager.storage_path.read_bytes() == committed
        assert len(fsync_count) - before_count == 1
    assert observations[0] == observations[1]
    assert observations[0][0] == "ok"


def _corrupt_owner_history(data, corruption):
    lines = data.splitlines(keepends=True)
    if corruption == "bad-json":
        return data + b'{"bad":}\n'
    if corruption == "non-object":
        return data + b'[]\n'
    if corruption == "utf8":
        return data + b'\xff\n'
    if corruption == "delete-middle":
        return b"".join(lines[:1] + lines[2:])
    payload = json.loads(lines[-1])
    if corruption == "unknown-version":
        payload["schema_version"] = "canonical_budget_event_unknown"
    elif corruption == "identity":
        payload["run_id"] = "wrong-run"
    elif corruption == "revision-gap":
        payload["state"]["revision"] += 1
    elif corruption == "derived":
        payload["state"]["remaining"]["model_tokens_total"] += 1
    elif corruption == "terminal-schema":
        payload["state"]["reservations"][0]["terminal_budget_revision"] = None
    elif corruption == "immutable-reservation":
        payload["state"]["reservations"][0]["reservation_ref"]["operation_ref"] = "operation://rewritten"
    else:
        raise AssertionError(corruption)
    return b"".join(lines[:-1]) + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"


@pytest.mark.parametrize("corruption", [
    "bad-json", "non-object", "utf8", "unknown-version", "identity",
    "revision-gap", "derived", "terminal-schema", "immutable-reservation", "delete-middle",
])
@pytest.mark.parametrize("cold", [False, True])
def test_aggregate_owner_committed_corruption_exact_old_error_chain_and_no_repair(
    tmp_path, corruption, cold,
):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    cache = ([], {}) if cold else _verification_cache(manager)
    corrupt = _corrupt_owner_history(manager.storage_path.read_bytes(), corruption)
    manager.storage_path.write_bytes(corrupt)
    observations = []
    for aggregate in (False, True):
        _restore_verification_cache(manager, cache)
        observations.append(_observe_owner_read(manager, ("logical", " "), aggregate=aggregate))
        assert manager.storage_path.read_bytes() == corrupt
    assert observations[0] == observations[1]
    assert observations[0][0] == "error"
    expected_type = ValueError if corruption == "utf8" else CanonicalBudgetIntegrityError
    assert observations[0][1][0][:2] == (expected_type.__module__, expected_type.__qualname__)


@pytest.mark.parametrize("cold", [False, True])
def test_aggregate_owner_v2_native_grant_hash_rejection_preserves_history(tmp_path, cold):
    # These helpers commit an actual owner Top-up with its explicit test-owned
    # authorization fence; no fabricated grant-bearing state is accepted here.
    from test_fpbca_bcd_budget_topup_owner import (
        _apply, _authorization, _manager as topup_manager,
        _ref as topup_ref, _request,
    )
    manager = topup_manager(tmp_path)
    manager.reserve(topup_ref("hash-target"), CanonicalBudgetVectorSchema(model_tokens_total=1), expected_revision=0)
    _apply(manager, _request(manager), _authorization(manager))
    state, records = manager.read_state_with_reservations(("hash-target",))
    assert state.schema_version == "canonical_budget_state_v2"
    assert len(state.grant_history) == 1 and len(records) == 1
    cache = ([], {}) if cold else _verification_cache(manager)
    lines = manager.storage_path.read_bytes().splitlines(keepends=True)
    payload = json.loads(lines[-1])
    payload["event_sha256"] = "0" * 64
    corrupt = b"".join(lines[:-1]) + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    manager.storage_path.write_bytes(corrupt)
    observations = []
    for aggregate in (False, True):
        _restore_verification_cache(manager, cache)
        observations.append(_observe_owner_read(manager, ("hash-target",), aggregate=aggregate))
    assert observations[0] == observations[1]
    assert observations[0][0] == "error"
    assert observations[0][1][0][2] == ("committed v2 Budget event hash mismatch",)
    assert manager.storage_path.read_bytes() == corrupt


@pytest.mark.parametrize("keep_lines,ids", [
    (4, ("logical", "tokens")), (3, ("logical", "tokens")),
    (1, ("logical",)), (0, ("logical",)),
])
def test_aggregate_owner_legal_historical_truncation_retains_actual_old_behavior(
    tmp_path, keep_lines, ids,
):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    cache = _verification_cache(manager)
    shortened = b"".join(manager.storage_path.read_bytes().splitlines(keepends=True)[:keep_lines])
    manager.storage_path.write_bytes(shortened)
    expected = _observe_owner_read(manager, ids, aggregate=False)
    _restore_verification_cache(manager, cache)
    actual = _observe_owner_read(manager, ids, aggregate=True)
    assert actual == expected
    assert actual[0] == ("ok" if keep_lines == 4 else "error")
    if keep_lines == 4:
        assert actual[1]["revision"] == 3
        assert actual[2][1]["status"] == "active"
    assert manager.storage_path.read_bytes() == shortened


def test_aggregate_owner_revalidates_same_size_mtime_rewrite_and_external_append(tmp_path):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    original = manager.storage_path.read_bytes()
    original_stat = manager.storage_path.stat()
    rewritten = original.replace(b'"policy_version":"v1"', b'"policy_version":"v2"')
    assert len(rewritten) == len(original) and rewritten != original
    manager.storage_path.write_bytes(rewritten)
    os.utime(manager.storage_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    expected = _observe_owner_read(manager, ("logical", "tokens"), aggregate=False)
    assert expected[0] == "ok"
    assert _observe_owner_read(manager, ("logical", "tokens"), aggregate=True) == expected
    assert expected[1]["policy_version"] == "v2"
    assert manager.storage_path.read_bytes() == rewritten
    # Restore the original valid history, then append through another real owner.
    manager.storage_path.write_bytes(original)
    other = _manager(tmp_path)
    other.reserve(_ref("external", reservation_kind="research_round"),
                  CanonicalBudgetVectorSchema(research_rounds=1), expected_revision=4)
    state, records = manager.read_state_with_reservations(("external", "logical"))
    assert state.revision == 5 and records[0].status == "active"
    assert state == other.canonical_state()
    assert manager.read_state_with_reservations(("external",))[0].revision == 5


def test_aggregate_owner_checks_other_accounts_and_selects_exact_task_run(tmp_path):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    other = BudgetManager.canonical(
        task_id="other-task", run_id="other-run", limits=_limits(),
        max_elapsed_seconds=60, storage_path=manager.storage_path,
        clock=lambda: NOW,
    )
    state, records = manager.read_state_with_reservations(("logical", "tokens"))
    assert state.task_id == "task_1" and state.run_id == "run_1"
    assert all(row.reservation_ref.task_id == "task_1" for row in records)
    assert len(manager._canonical_verified_accounts) == 2
    lines = other.storage_path.read_bytes().splitlines(keepends=True)
    payload = json.loads(lines[-1])
    payload["state"]["remaining"]["logical_model_calls"] += 1
    other.storage_path.write_bytes(b"".join(lines[:-1]) + json.dumps(payload).encode() + b"\n")
    expected = _observe_owner_read(manager, ("logical",), aggregate=False)
    assert expected[0] == "error"
    assert _observe_owner_read(manager, ("logical",), aggregate=True) == expected


@pytest.mark.parametrize("fault", ["read", "lock"])
def test_aggregate_owner_propagates_os_errors_exactly_and_releases_locks(tmp_path, monkeypatch, fault):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    import apps.enterprise_document.services.agent_rag_v1.budget_manager as owner_module
    failure = OSError(5, "injected owner observation failure")
    def fail(*args, **kwargs):
        raise failure
    with monkeypatch.context() as patcher:
        if fault == "read":
            patcher.setattr(manager._canonical_log, "read_complete_lines", fail)
        else:
            patcher.setattr(owner_module.InterProcessFileLock, "acquire", fail)
        expected = _observe_owner_read(manager, ("logical", "tokens"), aggregate=False)
        actual = _observe_owner_read(manager, ("logical", "tokens"), aggregate=True)
        assert actual == expected
        assert actual[1][0][2] == failure.args
    assert _manager(tmp_path).read_state_with_reservations(("logical",))[0].revision == 4


def test_aggregate_owner_preserves_historical_revision_hash_authority(tmp_path):
    manager = _manager(tmp_path)
    _seed_read_consolidation(manager)
    before = manager.storage_path.read_bytes()
    historical = manager.resolve_state_revision(2)
    digest = canonical_json_sha256(historical.model_dump(mode="json"))
    manager.read_state_with_reservations(("logical", "tokens"))
    assert manager.resolve_state_revision(2, digest) == historical
    with pytest.raises(CanonicalBudgetIntegrityError) as captured:
        manager.resolve_state_revision(2, "0" * 64)
    assert captured.value.args == ("native Budget historical state hash mismatch",)
    assert manager.storage_path.read_bytes() == before


def test_post_durable_append_readback_io_failure_reopen_replay_charges_exactly_once(
    tmp_path, monkeypatch,
):
    manager = _manager(tmp_path)
    manager.reserve(_ref("readback-fault"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=0)
    original_wire = manager.storage_path.read_bytes()
    log = manager._canonical_log
    original_append = log.append_serialized
    original_read = log.read_complete_lines
    events = []
    fail_next_read = [False]
    def durable_append(serialized):
        original_append(serialized)  # native write/newline/flush/fsync complete
        events.append("durable-append-returned")
        assert manager.storage_path.read_bytes().endswith(b"\n")
        assert len(manager.storage_path.read_bytes().splitlines()) == 3
        fail_next_read[0] = True
    def readback():
        if fail_next_read[0]:
            fail_next_read[0] = False
            events.append("post-append-readback-failed")
            raise OSError(5, "injected post-durable-append readback failure")
        return original_read()
    monkeypatch.setattr(log, "append_serialized", durable_append)
    monkeypatch.setattr(log, "read_complete_lines", readback)
    actual = CanonicalBudgetVectorSchema(logical_model_calls=1)
    with pytest.raises(OSError) as captured:
        manager.consume("readback-fault", actual, expected_revision=1)
    assert captured.value.args == (5, "injected post-durable-append readback failure")
    assert events == ["durable-append-returned", "post-append-readback-failed"]
    committed_wire = manager.storage_path.read_bytes()
    assert committed_wire.startswith(original_wire) and committed_wire != original_wire
    reopened = _manager(tmp_path)
    state, records = reopened.read_state_with_reservations(("readback-fault",))
    assert state.revision == 2 and state.used.logical_model_calls == 1
    assert records[0].status == "consumed"
    replay = reopened.consume("readback-fault", actual, expected_revision=1)
    assert replay.status == "consumed"
    assert reopened.storage_path.read_bytes() == committed_wire
    with pytest.raises(CanonicalBudgetIdentityConflict):
        reopened.consume("readback-fault", CanonicalBudgetVectorSchema(logical_model_calls=2), expected_revision=2)
    assert reopened.storage_path.read_bytes() == committed_wire


def _spawn_owner_reader(root, ready, begin, loaded, release, results):
    """Delay the real loader while aggregate still owns its production locks."""
    try:
        manager = _manager(Path(root))
        original = manager._canonical_current_unlocked
        def observed_current():
            state = original()
            loaded.set()
            if not release.wait(15):
                raise TimeoutError("reader release handshake timed out")
            return state
        manager._canonical_current_unlocked = observed_current
        ready.set()
        if not begin.wait(15):
            raise TimeoutError("reader start handshake timed out")
        state, records = manager.read_state_with_reservations(("first", "other"))
        results.put(("reader-unlocked", state.model_dump(mode="json"),
                     [row.model_dump(mode="json") for row in records]))
    except BaseException as error:
        results.put(("reader-error", type(error).__name__, str(error)))


def _native_lock_is_contended(path):
    """A genuine nonblocking OS probe; file presence is never lock evidence."""
    import errno
    with Path(path).open("r+b", buffering=0) as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                return True
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def _spawn_owner_writer(root, ready, begin, committed, results, *, probe=False):
    try:
        manager = _manager(Path(root))
        ready.set()
        if not begin.wait(15):
            raise TimeoutError("writer start handshake timed out")
        if probe:
            results.put(("native-lock-probe", _native_lock_is_contended(manager._canonical_lock_path)))
        manager.consume("first", CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=2)
        state = manager.canonical_state()
        committed.set()
        results.put(("writer-committed", state.model_dump(mode="json")))
    except BaseException as error:
        results.put(("writer-error", type(error).__name__, str(error)))


def _seed_spawn_owner(tmp_path):
    manager = _manager(tmp_path)
    for revision, name in enumerate(("first", "other")):
        manager.reserve(_ref(name), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=revision)
    return manager


def _spawn_message(results, processes):
    try:
        return results.get(timeout=20)
    except Empty:
        pytest.fail(f"spawn handshake timed out; PID/exitcode={[(p.pid, p.exitcode) for p in processes]}")


def _finish_spawn_owners(processes, events, results):
    # All cleanup is limited to processes created by this test. A timeout is a
    # failure, never a retry or permission to lengthen the frozen handshake.
    for event in events:
        event.set()
    unfinished = []
    for process in processes:
        process.join(timeout=5)
        if process.is_alive():
            unfinished.append(process.pid)
            process.terminate()
            process.join(timeout=5)
    results.close()
    results.join_thread()
    assert not unfinished, f"spawn owner process did not exit: {unfinished}"
    assert all(process.exitcode == 0 for process in processes), [
        (process.pid, process.exitcode) for process in processes
    ]


def test_aggregate_spawn_reader_holds_native_lock_and_returns_one_revision(tmp_path):
    manager = _seed_spawn_owner(tmp_path)
    expected_before = manager.canonical_state().model_dump(mode="json")
    context = multiprocessing.get_context("spawn")
    reader_ready, reader_begin, loaded, release = [context.Event() for _ in range(4)]
    writer_ready, writer_begin, committed = [context.Event() for _ in range(3)]
    results = context.Queue()
    reader = context.Process(target=_spawn_owner_reader,
        args=(str(tmp_path), reader_ready, reader_begin, loaded, release, results))
    writer = context.Process(target=_spawn_owner_writer,
        args=(str(tmp_path), writer_ready, writer_begin, committed, results),
        kwargs={"probe": True})
    processes = [reader, writer]
    for process in processes:
        process.start()
    try:
        assert reader_ready.wait(20) and writer_ready.wait(20), [p.pid for p in processes]
        reader_begin.set()
        assert loaded.wait(20), "real reader loader did not report its locked observation"
        writer_begin.set()
        # The OS rejects a real nonblocking byte/range lock while the aggregate
        # loader is paused. This proves contention, unlike event order alone.
        assert _spawn_message(results, processes) == ("native-lock-probe", True)
        assert not committed.is_set()
        assert manager.storage_path.read_bytes().count(b"\n") == 3
        release.set()
        messages = [_spawn_message(results, processes) for _ in range(2)]
        by_phase = {message[0]: message for message in messages}
        assert set(by_phase) == {"reader-unlocked", "writer-committed"}, messages
        _, observed_state, records = by_phase["reader-unlocked"]
        assert observed_state == expected_before
        assert observed_state["revision"] == 2
        assert all(row["status"] == "active" for row in records)
        state_rows = {row["reservation_ref"]["reservation_id"]: row for row in observed_state["reservations"]}
        assert records == [state_rows["first"], state_rows["other"]]
        assert by_phase["writer-committed"][1]["revision"] == 3
        after, after_records = manager.read_state_with_reservations(("first", "other"))
        assert after.revision == 3 and after_records[0].status == "consumed"
    finally:
        _finish_spawn_owners(processes, [release, reader_begin, writer_begin], results)


def test_aggregate_spawn_writer_first_reader_observes_complete_new_revision(tmp_path):
    _seed_spawn_owner(tmp_path)
    context = multiprocessing.get_context("spawn")
    reader_ready, reader_begin, loaded, release = [context.Event() for _ in range(4)]
    writer_ready, writer_begin, committed = [context.Event() for _ in range(3)]
    results = context.Queue()
    reader = context.Process(target=_spawn_owner_reader,
        args=(str(tmp_path), reader_ready, reader_begin, loaded, release, results))
    writer = context.Process(target=_spawn_owner_writer,
        args=(str(tmp_path), writer_ready, writer_begin, committed, results))
    processes = [reader, writer]
    for process in processes:
        process.start()
    try:
        assert reader_ready.wait(20) and writer_ready.wait(20)
        writer_begin.set()
        writer_result = _spawn_message(results, processes)
        assert writer_result[0] == "writer-committed", writer_result
        assert committed.is_set()
        release.set()
        reader_begin.set()
        reader_result = _spawn_message(results, processes)
        assert reader_result[0] == "reader-unlocked", reader_result
        assert reader_result[1] == writer_result[1]
        assert reader_result[1]["revision"] == 3
        assert [row["status"] for row in reader_result[2]] == ["consumed", "active"]
        state_rows = {row["reservation_ref"]["reservation_id"]: row for row in reader_result[1]["reservations"]}
        assert reader_result[2] == [state_rows["first"], state_rows["other"]]
    finally:
        _finish_spawn_owners(processes, [release, reader_begin, writer_begin], results)


def test_old_spawn_mixed_revision_witness_and_aggregate_exception_unlock(tmp_path):
    manager = _seed_spawn_owner(tmp_path)
    # A failed lookup must release the real locks before another process writes.
    with pytest.raises(KeyError) as captured:
        manager.read_state_with_reservations(("absent", "other"))
    assert captured.value.args == ("absent",)
    first_old = manager.resolve_reservation("first")
    assert first_old.status == "active"
    context = multiprocessing.get_context("spawn")
    ready, begin, committed = [context.Event() for _ in range(3)]
    results = context.Queue()
    writer = context.Process(target=_spawn_owner_writer,
        args=(str(tmp_path), ready, begin, committed, results))
    writer.start()
    try:
        assert ready.wait(20)
        begin.set()
        written = _spawn_message(results, [writer])
        assert written[0] == "writer-committed", written
        second_old = manager.resolve_reservation("other")
        final_old = manager.canonical_state()
        old_rows = {row.reservation_ref.reservation_id: row for row in final_old.reservations}
        assert first_old != old_rows["first"]
        assert second_old == old_rows["other"]
        new_state, new_records = manager.read_state_with_reservations(("first", "other"))
        assert new_state == final_old and new_state.revision == 3
        assert new_records == (old_rows["first"], old_rows["other"])
        # A permits a mixed observation; B's approved contract chooses one
        # complete observation. Concurrent verdict equivalence is not asserted.
    finally:
        _finish_spawn_owners([writer], [begin], results)
