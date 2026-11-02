"""Budget Top-up native owner cases; lifecycle/application integration is separate.

The command registry and PAUSED fence below are explicit test-owned injection
seams. They do not purport to prove production lifecycle or physical dispatch.
All capacity, CAS, replay, balances and crash checks use the actual Budget log.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    CANONICAL_BUDGET_DIMENSIONS, BudgetReservationRefSchema,
    CanonicalBudgetVectorSchema,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    BudgetManager, CanonicalBudgetCapacityExceeded, CanonicalBudgetDraining,
    CanonicalBudgetIdentityConflict, CanonicalBudgetIntegrityError,
    CanonicalBudgetRevisionConflict,
)
from core.persistence.crash_safe_jsonl import canonical_json_sha256


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _sha(value):
    return canonical_json_sha256(value)


def _limits(**changes):
    values = {name: 8 for name in CANONICAL_BUDGET_DIMENSIONS}
    values.update(logical_model_calls=44, model_tokens_total=24000)
    values.update(changes)
    return CanonicalBudgetVectorSchema(**values)


def _manager(root, **changes):
    return BudgetManager.canonical(
        task_id="bt-task", run_id="bt-run", limits=_limits(**changes),
        max_elapsed_seconds=120, storage_path=Path(root) / "budget.jsonl",
        policy_ref="budget-policy://bt-owner/frozen", policy_version="v1",
        clock=lambda: NOW,
    )


def _policy_hash(manager):
    state = manager.canonical_state()
    return _sha({"policy_ref": state.policy_ref, "policy_version": state.policy_version,
                 "limits": state.limits.model_dump(mode="json")})


def _ref(name, **changes):
    values = dict(reservation_id=name, task_id="bt-task", run_id="bt-run",
                  reservation_kind="model_provider_attempt_tokens",
                  owner_occurrence_ref=f"provider://{name}", scope_ref="DOCUMENT",
                  operation_ref="citation://semantic", attempt_id=None)
    values.update(changes)
    return BudgetReservationRefSchema(**values)


def _authorization(manager, *, preauthorized=False, delta=1000, **changes):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
        CanonicalBudgetTopupAuthorizationSchema,
    )
    values = dict(
        authorization_kind="PREAUTHORIZED" if preauthorized else "SINGLE_USE",
        authorization_id="native-user-command", authorization_ref="artifact://bt/auth/root",
        authorization_sha256=_sha("explicit-user-command"), command_source="CLI", issuer="test-user",
        task_id="bt-task", run_id="bt-run", canonical_request_ref="artifact://bt/request",
        canonical_request_sha256=_sha("request"), semantic_operation_ref="citation://semantic",
        semantic_input_sha256=_sha("semantic-input"), artifact_version="artifact-v1",
        baseline_policy_sha256=_policy_hash(manager),
        authorized_delta=CanonicalBudgetVectorSchema(model_tokens_total=delta),
        cumulative_ceiling=_limits(model_tokens_total=24000 + delta),
        single_use_wait_event_ref=None if preauthorized else "lifecycle_budget:wait-1:hash",
        single_use_native_denial_sha256=None if preauthorized else _sha("denial-1"),
        single_use_child_preimage_sha256=None if preauthorized else _sha("eight-native-children"),
    )
    values.update(changes)
    return CanonicalBudgetTopupAuthorizationSchema(**values)


def _request(manager, *, delta=1000, revision=None, **changes):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
        CanonicalBudgetTopupRequestSchema,
    )
    values = dict(
        approval_id="client-1", authorization_ref="artifact://bt/auth/root",
        authorization_sha256=_sha("explicit-user-command"), task_id="bt-task", run_id="bt-run",
        canonical_request_ref="artifact://bt/request", canonical_request_sha256=_sha("request"),
        semantic_operation_ref="citation://semantic", semantic_input_sha256=_sha("semantic-input"),
        artifact_version="artifact-v1", native_denial_ref="artifact://bt/denial/1",
        native_denial_sha256=_sha("denial-1"), phase_proof_ref="artifact://bt/phase/1",
        phase_proof_sha256=_sha("first-physical-zero-and-closed"),
        wait_event_ref="lifecycle_budget:wait-1:hash", wait_event_sha256=_sha("wait-1"),
        child_preimage_sha256=_sha("eight-native-children"), baseline_policy_sha256=_policy_hash(manager),
        expected_budget_revision=manager.canonical_state().revision if revision is None else revision,
        expected_lifecycle_revision=2,
        delta=CanonicalBudgetVectorSchema(model_tokens_total=delta),
        required=CanonicalBudgetVectorSchema(model_tokens_total=24500),
    )
    values.update(changes)
    return CanonicalBudgetTopupRequestSchema(**values)


def _apply(manager, request, authorization, *, fence=None):
    @contextmanager
    def test_owned_paused_fence(**binding):
        assert binding["run_id"] == "bt-run"
        assert binding["child_preimage_sha256"] == _sha("eight-native-children")
        yield {"test_owned_fence": True, "paused": True}
    return manager.apply_topup(
        request, authorization_resolver=lambda actual: authorization,
        paused_authorization_fence=fence or test_owned_paused_fence,
    )


def _receipt(tmp_path, name, manager, **extra):
    state = manager.canonical_state()
    payload = {"scope": "NATIVE_BUDGET_OWNER_ONLY", "task_id": state.task_id,
               "run_id": state.run_id, "state": state.model_dump(mode="json"),
               "journal_sha256": hashlib.sha256(manager.storage_path.read_bytes()).hexdigest(),
               "production_lifecycle_fence": "NOT_RUN_THIS_OWNER_FILE",
               "physical_dispatch": "UNMEASURED_THIS_OWNER_FILE", **extra}
    path = tmp_path / (name + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
    return payload


def test_bt12_v1_baseline_grant_reopen_preserves_original_prefix(tmp_path):
    manager = _manager(tmp_path)
    original = manager.storage_path.read_bytes()
    before = manager.canonical_state()
    grant = _apply(manager, _request(manager), _authorization(manager))
    reopened = _manager(tmp_path)
    after = reopened.canonical_state()
    reopened_wire = reopened.storage_path.read_bytes()
    original_first_event = json.loads(original.decode("utf-8").splitlines()[0])
    legacy_v1_native_byte_reopen_observation = {
        "schema_version": "bt12_native_legacy_wire_reopen_v1",
        "scope": "NATIVE_BUDGET_OWNER_ONLY_NO_PROVIDER_OR_LIFECYCLE_MEASUREMENT",
        "original_wire_hex": original.hex(),
        "original_wire_sha256": hashlib.sha256(original).hexdigest(),
        "original_wire_bytes": len(original),
        "original_native_first_event": original_first_event,
        "original_stored_state_contains_grant_history": "grant_history" in original_first_event["state"],
        "original_native_state": before.model_dump(mode="json"),
        "original_baseline_limits": before.limits.model_dump(mode="json"),
        "accepted_native_grant": grant.model_dump(mode="json"),
        "reopened_native_state": after.model_dump(mode="json"),
        "reopened_wire_hex": reopened_wire.hex(),
        "reopened_wire_sha256": hashlib.sha256(reopened_wire).hexdigest(),
        "reopened_wire_bytes": len(reopened_wire),
        "actual_original_byte_prefix_present": reopened_wire.startswith(original),
        "actual_baseline_limits_unchanged": after.limits == before.limits,
        "actual_used_unchanged": after.used == before.used,
        "actual_active_reserved_unchanged": after.active_reserved == before.active_reserved,
    }
    _receipt(tmp_path, "bt12", reopened, grant=grant.model_dump(mode="json"),
             original_prefix_sha256=hashlib.sha256(original).hexdigest(),
             legacy_v1_native_byte_reopen_observation=legacy_v1_native_byte_reopen_observation)
    assert manager.storage_path.read_bytes().startswith(original)
    assert before.schema_version == "canonical_budget_state_v1"
    assert json.loads(original.decode().splitlines()[0])["state"].get("grant_history") is None
    assert after.limits.model_tokens_total == 24000
    assert after.effective_limits.model_tokens_total == 25000
    assert after.used == before.used and after.active_reserved == before.active_reserved
    assert len(after.grant_history) == 1 and after.revision == 1
    with pytest.raises(CanonicalBudgetIntegrityError, match="immutable limits"):
        _manager(tmp_path, model_tokens_total=25000)


def test_bt06_exact_and_alias_replay_are_zero_append_after_reopen(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    request, auth = _request(manager), _authorization(manager)
    first = _apply(manager, request, auth)
    raw = manager.storage_path.read_bytes()
    reopened = _manager(tmp_path)
    calls = []
    monkeypatch.setattr(reopened._canonical_log, "append_serialized", lambda line: calls.append(line))
    @contextmanager
    def must_not_fence(**binding):
        raise AssertionError("exact grant read must not reacquire PAUSED mutation authority")
        yield
    assert _apply(reopened, request, auth, fence=must_not_fence) == first
    alias = request.model_copy(update={"approval_id": "client-alias"})
    assert _apply(reopened, alias, auth, fence=must_not_fence) == first
    assert calls == [] and reopened.storage_path.read_bytes() == raw
    _receipt(tmp_path, "bt06", reopened, append_calls=calls, original_grant=first.model_dump(mode="json"))


def test_exact_native_grant_read_is_passive_and_projection_isolated(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    accepted = _apply(manager, _request(manager), _authorization(manager))
    original = manager.storage_path.read_bytes()
    reopened = _manager(tmp_path)
    calls = []
    monkeypatch.setattr(reopened._canonical_log, "append_serialized", lambda line: calls.append(line))
    exact = reopened.resolve_topup_grant(accepted.grant_ref, expected_sha256=accepted.grant_sha256)
    assert exact == accepted
    exact.request.delta.model_tokens_total = 999999
    assert reopened.resolve_topup_grant(accepted.grant_ref).request.delta.model_tokens_total == 1000
    with pytest.raises(CanonicalBudgetIntegrityError, match="grant hash"):
        reopened.resolve_topup_grant(accepted.grant_ref, expected_sha256="0" * 64)
    with pytest.raises(CanonicalBudgetIntegrityError, match="grant missing"):
        reopened.resolve_topup_grant("budget_grant:unknown:unknown")
    assert calls == [] and reopened.storage_path.read_bytes() == original


@pytest.mark.parametrize("changed", [
    {"delta": {"model_tokens_total": 999}}, {"authorization_sha256": _sha("other")},
    {"run_id": "different-run"}, {"semantic_operation_ref": "citation://different"},
    {"semantic_input_sha256": _sha("different")}, {"child_preimage_sha256": _sha("different")},
    {"wait_event_sha256": _sha("different")}, {"phase_proof_sha256": _sha("different")},
])
def test_bt07_same_client_changed_semantics_conflicts(tmp_path, changed):
    manager = _manager(tmp_path)
    request, auth = _request(manager), _authorization(manager)
    _apply(manager, request, auth)
    raw = manager.storage_path.read_bytes()
    values = request.model_dump(mode="python")
    values.update(changed)
    bad = type(request).model_validate(values)
    with pytest.raises((CanonicalBudgetIdentityConflict, CanonicalBudgetIntegrityError)):
        _apply(manager, bad, auth)
    assert manager.storage_path.read_bytes() == raw


def test_bt07_single_authorization_alias_changed_delta_cannot_second_grant(tmp_path):
    manager = _manager(tmp_path)
    request, auth = _request(manager), _authorization(manager, delta=2000)
    _apply(manager, request, auth)
    bad = request.model_copy(update={"approval_id": "alias", "expected_budget_revision": 1,
                                    "delta": CanonicalBudgetVectorSchema(model_tokens_total=500)})
    with pytest.raises(CanonicalBudgetIdentityConflict):
        _apply(manager, bad, auth)
    assert manager.canonical_state().effective_limits.model_tokens_total == 25000


@pytest.mark.parametrize("decision", ["missing", "denied", "revoked", "expired", "ceiling", "scope", "insufficient"])
def test_bt04_invalid_authorization_adds_no_capacity(tmp_path, decision):
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetTopupRejected
    manager = _manager(tmp_path)
    request, auth = _request(manager), _authorization(manager)
    changes = {
        "denied": {"decision": "DENIED"}, "revoked": {"revoked": True},
        "expired": {"expires_at": NOW - timedelta(seconds=1)},
        "ceiling": {"cumulative_ceiling": _limits(model_tokens_total=24999)},
        "scope": {"semantic_input_sha256": _sha("other-input")},
        "insufficient": {"cumulative_ceiling": _limits(model_tokens_total=24200),
                         "authorized_delta": CanonicalBudgetVectorSchema(model_tokens_total=200)},
    }
    if decision == "missing":
        auth = None
    else:
        auth = auth.model_copy(update=changes[decision])
    raw = manager.storage_path.read_bytes()
    with pytest.raises(CanonicalBudgetTopupRejected):
        _apply(manager, request, auth)
    assert manager.storage_path.read_bytes() == raw
    assert manager.canonical_state().limits == manager.canonical_state().effective_limits


@pytest.mark.parametrize("invalid", [
    {"model_tokens_total": -1}, {"model_tokens_total": 1.5}, {"model_tokens_total": True},
    {"model_tokens_total": "1000"}, {"model_tokens_total": 2**63}, {"cost": 1}, {},
])
def test_bt04_topup_vector_is_strict_finite_eight_dimensional(tmp_path, invalid):
    manager = _manager(tmp_path)
    request = _request(manager)
    values = request.model_dump(mode="python")
    values["delta"] = invalid
    with pytest.raises(ValueError):
        type(request).model_validate(values)
    assert manager.canonical_state().revision == 0


def test_bt05_preauthorized_distinct_native_debits_enforce_root_cap(tmp_path):
    manager = _manager(tmp_path)
    auth = _authorization(manager, preauthorized=True, delta=1500)
    first = _request(manager, delta=1000)
    _apply(manager, first, auth)
    second = _request(manager, delta=500, approval_id="client-2", required=CanonicalBudgetVectorSchema(model_tokens_total=25500),
                      wait_event_ref="lifecycle_budget:wait-2:hash", wait_event_sha256=_sha("wait-2"),
                      native_denial_ref="artifact://bt/denial/2", native_denial_sha256=_sha("denial-2"),
                      phase_proof_ref="artifact://bt/phase/2", phase_proof_sha256=_sha("proof-2"))
    second_grant = _apply(manager, second, auth)
    assert _apply(manager, second.model_copy(update={"approval_id": "debit-2-alias"}), auth) == second_grant
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetTopupRejected
    third = _request(manager, delta=1, approval_id="client-3", required=CanonicalBudgetVectorSchema(model_tokens_total=25501),
                     wait_event_ref="lifecycle_budget:wait-3:hash", wait_event_sha256=_sha("wait-3"),
                     native_denial_sha256=_sha("denial-3"), native_denial_ref="artifact://bt/denial/3")
    with pytest.raises(CanonicalBudgetTopupRejected):
        _apply(manager, third, auth)
    state = manager.canonical_state()
    _receipt(tmp_path, "bt05", manager)
    assert len(state.grant_history) == 2 and state.effective_limits.model_tokens_total == 25500


def test_bt05_same_preauth_debit_delta_change_with_alias_conflicts(tmp_path):
    manager = _manager(tmp_path)
    auth = _authorization(manager, preauthorized=True, delta=2000)
    request = _request(manager)
    _apply(manager, request, auth)
    bad = request.model_copy(update={"approval_id": "new-id", "expected_budget_revision": 1,
                                    "delta": CanonicalBudgetVectorSchema(model_tokens_total=500)})
    with pytest.raises(CanonicalBudgetIdentityConflict):
        _apply(manager, bad, auth)
    assert len(manager.canonical_state().grant_history) == 1


def test_bt08_two_threads_same_consumption_key_append_once(tmp_path):
    a, b = _manager(tmp_path), _manager(tmp_path)
    request, auth = _request(a), _authorization(a)
    aliases = [request, request.model_copy(update={"approval_id": "thread-alias"})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_apply, manager, alias, auth) for manager, alias in zip((a, b), aliases)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert outcomes[0] == outcomes[1]
    assert len(a.canonical_state().grant_history) == 1
    _receipt(tmp_path, "bt08_thread", a)


def _process_apply(root, request_data, authorization_data, start, result_path, exit_at=None):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
        CanonicalBudgetTopupRequestSchema, CanonicalBudgetTopupAuthorizationSchema,
    )
    manager = _manager(root)
    request = CanonicalBudgetTopupRequestSchema.model_validate(request_data)
    auth = CanonicalBudgetTopupAuthorizationSchema.model_validate(authorization_data)
    if exit_at:
        original = manager._canonical_log.append_serialized
        def exit_append(line):
            if exit_at == "after":
                original(line)
            os._exit(37)
        manager._canonical_log.append_serialized = exit_append
    start.wait(15)
    result = _apply(manager, request, auth)
    Path(result_path).write_text(json.dumps(result.model_dump(mode="json"), sort_keys=True), encoding="utf-8")


def test_bt08_two_windows_spawn_processes_same_consumption_key_append_once(tmp_path):
    manager = _manager(tmp_path)
    request, auth = _request(manager), _authorization(manager)
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    processes = []
    outputs = [tmp_path / "process-1.json", tmp_path / "process-2.json"]
    for index, output in enumerate(outputs):
        alias = request.model_copy(update={"approval_id": f"process-client-{index}"})
        process = ctx.Process(target=_process_apply, args=(tmp_path, alias.model_dump(mode="json"),
                              auth.model_dump(mode="json"), start, str(output)))
        process.start()
        processes.append(process)
    start.set()
    for process in processes:
        process.join(30)
        assert not process.is_alive(), "test harness INCOMPLETE; not production terminal authority"
        assert process.exitcode == 0
    assert json.loads(outputs[0].read_text()) == json.loads(outputs[1].read_text())
    assert len(_manager(tmp_path).canonical_state().grant_history) == 1
    _receipt(tmp_path, "bt08_process", manager, process_exit_codes=[p.exitcode for p in processes])


def test_bt09_distinct_approval_commands_compete_on_native_cas(tmp_path):
    a, b = _manager(tmp_path), _manager(tmp_path)
    pairs = []
    for index in range(2):
        auth = _authorization(a, authorization_id=f"auth-{index}", authorization_ref=f"artifact://bt/auth/{index}",
                              authorization_sha256=_sha(f"authorization-{index}"))
        request = _request(a, approval_id=f"client-{index}", authorization_ref=auth.authorization_ref,
                           authorization_sha256=auth.authorization_sha256)
        pairs.append((request, auth))
    def run(manager, pair):
        try:
            return _apply(manager, *pair)
        except CanonicalBudgetRevisionConflict:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=10) for f in [pool.submit(run, a, pairs[0]), pool.submit(run, b, pairs[1])]]
    assert results.count("stale") == 1
    assert a.canonical_state().effective_limits.model_tokens_total == 25000


def test_bt10_rejected_native_currentness_fence_never_appends(tmp_path):
    manager = _manager(tmp_path)
    @contextmanager
    def cancelled_owner_fence(**binding):
        raise RuntimeError("native Run cancelled or current child set changed")
        yield
    before = manager.storage_path.read_bytes()
    with pytest.raises(RuntimeError, match="native Run cancelled"):
        _apply(manager, _request(manager), _authorization(manager), fence=cancelled_owner_fence)
    assert manager.storage_path.read_bytes() == before


@pytest.mark.parametrize("timing,expected_grants", [("before", 0), ("after", 1)])
def test_bt14_spawn_self_exit_before_after_native_append_reopen_once(tmp_path, timing, expected_grants):
    manager = _manager(tmp_path)
    request, auth = _request(manager), _authorization(manager)
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    process = ctx.Process(target=_process_apply, args=(tmp_path, request.model_dump(mode="json"),
                          auth.model_dump(mode="json"), start, str(tmp_path / "never.json"), timing))
    process.start()
    start.set()
    process.join(30)
    assert not process.is_alive(), "test harness INCOMPLETE; no production status mutation"
    assert process.exitcode == 37
    reopened = _manager(tmp_path)
    assert len(reopened.canonical_state().grant_history) == expected_grants
    _apply(reopened, request, auth)
    assert len(reopened.canonical_state().grant_history) == 1
    _receipt(tmp_path, "bt14_" + timing, reopened, actual_exit_code=process.exitcode)


def test_bt14_torn_tail_and_complete_bad_digest_are_distinct(tmp_path):
    manager = _manager(tmp_path)
    _apply(manager, _request(manager), _authorization(manager))
    committed = manager.storage_path.read_bytes()
    with manager.storage_path.open("ab") as handle:
        handle.write(b'{"schema_version":"canonical_budget_event_v2"')
    reopened = _manager(tmp_path)
    assert len(reopened.canonical_state().grant_history) == 1
    assert reopened.storage_path.read_bytes() == committed
    line = json.loads(committed.decode().splitlines()[-1])
    line["event_sha256"] = "0" * 64
    with manager.storage_path.open("ab") as handle:
        handle.write((json.dumps(line) + "\n").encode())
    with pytest.raises(CanonicalBudgetIntegrityError):
        _manager(tmp_path)


@pytest.mark.parametrize("mutation", ["removed", "changed", "duplicate", "used", "reservation", "revision", "effective"])
def test_bt13_committed_grant_and_account_tamper_fail_closed(tmp_path, mutation):
    manager = _manager(tmp_path)
    _apply(manager, _request(manager), _authorization(manager))
    lines = manager.storage_path.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    state = last["state"]
    if mutation == "removed": state["grant_history"] = []
    elif mutation == "changed": state["grant_history"][0]["request"]["delta"]["model_tokens_total"] += 1
    elif mutation == "duplicate": state["grant_history"] *= 2
    elif mutation == "used": state["used"]["model_tokens_total"] += 1
    elif mutation == "reservation": state["active_reserved"]["model_tokens_total"] += 1
    elif mutation == "revision": state["revision"] += 2
    elif mutation == "effective": state["effective_limits"] = {"model_tokens_total": 999999}
    frame = {key: value for key, value in last.items() if key != "event_sha256"}
    last["event_sha256"] = _sha(frame)
    lines[-1] = json.dumps(last, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manager.storage_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CanonicalBudgetIntegrityError):
        _manager(tmp_path)


def test_bt28_draining_is_never_cleared_by_finite_topup(tmp_path):
    manager = _manager(tmp_path)
    manager.start_draining(expected_revision=0)
    with pytest.raises(CanonicalBudgetDraining):
        _apply(manager, _request(manager), _authorization(manager))
    assert manager.canonical_state().draining is True
    assert manager.canonical_state().limits.model_tokens_total == 24000


@pytest.mark.parametrize("dimension", CANONICAL_BUDGET_DIMENSIONS)
def test_bt28_grant_does_not_erase_other_finite_caps(tmp_path, dimension):
    manager = _manager(tmp_path)
    _apply(manager, _request(manager), _authorization(manager))
    limit = getattr(manager.canonical_state().effective_limits, dimension)
    vector = CanonicalBudgetVectorSchema(**{dimension: limit + 1})
    with pytest.raises(CanonicalBudgetCapacityExceeded) as error:
        manager.reserve(_ref("over-" + dimension), vector, expected_revision=1)
    assert error.value.resource == dimension
    assert manager.canonical_state().used.is_zero()


def test_bt28_actual_overrun_is_kept_and_ordinary_events_keep_grant(tmp_path):
    manager = _manager(tmp_path)
    grant = _apply(manager, _request(manager), _authorization(manager))
    manager.reserve(_ref("actual"), CanonicalBudgetVectorSchema(model_tokens_total=100), expected_revision=1)
    manager.consume("actual", CanonicalBudgetVectorSchema(model_tokens_total=26000), expected_revision=2)
    state = _manager(tmp_path).canonical_state()
    _receipt(tmp_path, "bt28_actual", manager)
    assert state.used.model_tokens_total == 26000 and state.remaining.model_tokens_total == 0
    assert state.grant_history == [grant] and state.limits.model_tokens_total == 24000


def test_locked_native_capacity_denial_and_historical_snapshot_are_exact(tmp_path):
    manager = _manager(tmp_path)
    requested = CanonicalBudgetVectorSchema(model_tokens_total=24500)
    with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:
        manager.reserve(_ref("denied"), requested, expected_revision=0)
    denial = caught.value.native_denial
    state = manager.resolve_state_revision(denial.budget_revision, expected_sha256=denial.budget_state_sha256)
    _receipt(tmp_path, "native_denial", manager, native_denial=denial.model_dump(mode="json"))
    assert denial.requested == requested and denial.reservation_ref == _ref("denied")
    assert denial.baseline_limits.model_tokens_total == denial.effective_limits.model_tokens_total == 24000
    assert denial.required == 24500 and denial.remaining == 24000
    assert denial.denied_dimension == "model_tokens_total" and state.revision == 0
    assert state.reservations == []
    with pytest.raises(CanonicalBudgetIntegrityError):
        manager.resolve_state_revision(0, expected_sha256="0" * 64)
