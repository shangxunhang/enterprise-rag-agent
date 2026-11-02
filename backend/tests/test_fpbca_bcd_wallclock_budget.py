"""Wall-clock observation versus native finite Budget and evaluation authority."""
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    CANONICAL_BUDGET_DIMENSIONS, BudgetReservationRefSchema, BudgetVectorSchema,
    CanonicalBudgetVectorSchema, TaskBudgetLimitsSchema,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import (
    BudgetManager, CanonicalBudgetCapacityExceeded, CanonicalBudgetDraining,
    CanonicalBudgetIdentityConflict, CanonicalBudgetIntegrityError,
    CanonicalBudgetRevisionConflict, TaskBudgetExhausted,
)


class _Clock:
    def __init__(self):
        self.current = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def expire(self):
        self.current += timedelta(days=2)


def _limits():
    return CanonicalBudgetVectorSchema(**{name: 3 for name in CANONICAL_BUDGET_DIMENSIONS})


def _canonical(tmp_path, clock, **updates):
    return BudgetManager.canonical(**{
        "task_id": "task-wallclock-budget", "run_id": "run-wallclock-budget",
        "limits": _limits(), "max_elapsed_seconds": 1,
        "storage_path": tmp_path / "canonical.jsonl",
        "policy_ref": "budget-policy://fpbca/wallclock-observation", "policy_version": "v1",
        "clock": clock, **updates,
    })


def _ref(reservation_id):
    return BudgetReservationRefSchema(
        reservation_id=reservation_id, task_id="task-wallclock-budget", run_id="run-wallclock-budget",
        reservation_kind="logical_operation", owner_occurrence_ref=f"occurrence://{reservation_id}",
        scope_ref="section:wallclock", operation_ref=f"operation://{reservation_id}", attempt_id="attempt-wallclock",
    )


def _legacy(clock):
    return BudgetManager(task_id="task-legacy-wallclock", clock=clock, limits=TaskBudgetLimitsSchema(
        max_model_calls=1, max_tool_calls=1, max_total_replans=1, max_total_rewrites=1,
        max_document_repair_tasks=1, max_wall_clock_seconds=1,
    ))


def test_expired_canonical_admits_consumes_releases_and_replays_without_policy_rewrite(tmp_path):
    clock = _Clock()
    manager = _canonical(tmp_path, clock)
    opening = manager.canonical_state()
    prefix = manager.storage_path.read_bytes()
    clock.expire()
    assert clock() > opening.deadline_at
    assert manager.canonical_state() == opening
    assert manager.storage_path.read_bytes() == prefix
    model = CanonicalBudgetVectorSchema(logical_model_calls=1, model_tokens_total=2)
    manager.reserve(_ref("model"), model, expected_revision=0)
    manager.consume("model", CanonicalBudgetVectorSchema(logical_model_calls=1, model_tokens_total=1), expected_revision=1)
    manager.reserve(_ref("tool"), CanonicalBudgetVectorSchema(logical_tool_calls=1), expected_revision=2)
    manager.release("tool", expected_revision=3)
    state = manager.canonical_state()
    assert state.revision == 4 and state.draining is False
    assert state.started_at == opening.started_at and state.deadline_at == opening.deadline_at
    assert state.policy_ref == opening.policy_ref and state.limits == opening.limits
    assert state.used.logical_model_calls == 1 and state.used.model_tokens_total == 1
    assert state.active_reserved.is_zero() and state.remaining.logical_tool_calls == 3
    assert manager.storage_path.read_bytes().startswith(prefix)
    durable = manager.storage_path.read_bytes()
    reopened = _canonical(tmp_path, clock, require_existing_account=True)
    assert reopened.canonical_state() == state
    reopened.reserve(_ref("model"), model, expected_revision=0)
    reopened.consume("model", CanonicalBudgetVectorSchema(logical_model_calls=1, model_tokens_total=1), expected_revision=1)
    reopened.release("tool", expected_revision=3)
    assert manager.storage_path.read_bytes() == durable


@pytest.mark.parametrize("settlement", ["consume", "release"])
def test_existing_reservation_settlement_after_elapsed_retains_exact_accounting(tmp_path, settlement):
    clock = _Clock()
    manager = _canonical(tmp_path, clock)
    vector = CanonicalBudgetVectorSchema(logical_tool_calls=1)
    manager.reserve(_ref("settle"), vector, expected_revision=0)
    clock.expire()
    if settlement == "consume":
        manager.consume("settle", vector, expected_revision=1)
    else:
        manager.release("settle", expected_revision=1)
    state = manager.canonical_state()
    assert state.revision == 2 and state.draining is False
    assert state.active_reserved.is_zero()
    assert state.used.logical_tool_calls == (1 if settlement == "consume" else 0)


@pytest.mark.parametrize("dimension", CANONICAL_BUDGET_DIMENSIONS)
def test_expired_canonical_preserves_each_finite_capacity_authority(tmp_path, dimension):
    clock = _Clock()
    manager = _canonical(tmp_path, clock)
    manager.reserve(_ref("full"), CanonicalBudgetVectorSchema(**{dimension: 3}), expected_revision=0)
    clock.expire()
    before = manager.storage_path.read_bytes()
    with pytest.raises(CanonicalBudgetCapacityExceeded) as rejected:
        manager.reserve(_ref("overflow"), CanonicalBudgetVectorSchema(**{dimension: 1}), expected_revision=1)
    assert rejected.value.resource == dimension and rejected.value.remaining == 0
    assert rejected.value.required == 1 and rejected.value.limit == 3
    assert manager.storage_path.read_bytes() == before
    assert manager.canonical_state().revision == 1


def test_explicit_canonical_draining_still_denies_after_elapsed(tmp_path):
    clock = _Clock()
    manager = _canonical(tmp_path, clock)
    manager.start_draining(expected_revision=0)
    clock.expire()
    before = manager.storage_path.read_bytes()
    with pytest.raises(CanonicalBudgetDraining):
        manager.reserve(_ref("drained"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=1)
    assert manager.storage_path.read_bytes() == before


def test_expired_reopen_is_read_only_and_still_rejects_immutable_policy_drift(tmp_path):
    clock = _Clock()
    manager = _canonical(tmp_path, clock)
    initial = manager.canonical_state()
    before = manager.storage_path.read_bytes()
    clock.expire()
    assert _canonical(tmp_path, clock, require_existing_account=True).canonical_state() == initial
    with pytest.raises(CanonicalBudgetIntegrityError, match="immutable deadline policy"):
        _canonical(tmp_path, clock, max_elapsed_seconds=2, require_existing_account=True)
    with pytest.raises(CanonicalBudgetIntegrityError, match="immutable limits"):
        _canonical(tmp_path, clock, limits=CanonicalBudgetVectorSchema(logical_model_calls=99), require_existing_account=True)
    assert manager.storage_path.read_bytes() == before


def test_expired_clock_cannot_bypass_revision_or_semantic_identity(tmp_path):
    clock = _Clock()
    manager = _canonical(tmp_path, clock)
    vector = CanonicalBudgetVectorSchema(logical_tool_calls=1)
    manager.reserve(_ref("exact"), vector, expected_revision=0)
    clock.expire()
    before = manager.storage_path.read_bytes()
    with pytest.raises(CanonicalBudgetRevisionConflict):
        manager.reserve(_ref("stale"), vector, expected_revision=0)
    with pytest.raises(CanonicalBudgetIdentityConflict):
        manager.reserve(_ref("exact").model_copy(update={"operation_ref": "operation://different"}), vector, expected_revision=1)
    assert manager.storage_path.read_bytes() == before


@pytest.mark.parametrize("snapshot_first", [False, True])
def test_legacy_elapsed_observation_does_not_drain_or_deny_work(snapshot_first):
    clock = _Clock()
    manager = _legacy(clock)
    clock.expire()
    if snapshot_first:
        assert manager.snapshot().elapsed_seconds == 172800
        assert manager.snapshot().draining is False
    manager.reserve("legacy", BudgetVectorSchema(model_calls=1))
    manager.settle("legacy", BudgetVectorSchema(model_calls=1))
    snapshot = manager.snapshot()
    assert snapshot.draining is False and snapshot.elapsed_seconds == 172800
    assert snapshot.used.model_calls == 1 and snapshot.reserved.model_calls == 0


@pytest.mark.parametrize("resource", ["model_calls", "tool_calls", "total_replans", "total_rewrites", "document_repair_tasks"])
def test_legacy_expired_clock_preserves_finite_count_exhaustion(resource):
    clock = _Clock()
    manager = _legacy(clock)
    clock.expire()
    with pytest.raises(TaskBudgetExhausted) as rejected:
        manager.reserve("too-many", BudgetVectorSchema(**{resource: 2}))
    assert rejected.value.resource == resource and rejected.value.limit == 1
    assert manager.snapshot().draining is True
    assert manager.snapshot().reserved == BudgetVectorSchema()


@pytest.mark.parametrize("concrete_owner_failure", [False, True])
def test_existing_evaluation_incomplete_surface_preserves_native_lifecycle_and_owner_facts(tmp_path, monkeypatch, concrete_owner_failure):
    # This validates the existing report surface with an authored observer cutoff;
    # it does not claim an automatic campaign deadline executor was implemented.
    from eval.full_chain.contracts import (
        EvaluationEvidenceRef, EvaluationRunIdentity, Finding, ObservationBatch, StageObservation,
    )
    from eval.full_chain.report import build_report
    from test_fpbca_bcd_c1_composite import _service, _run, _attempts

    lifecycle = _service(tmp_path / "lifecycle.jsonl")
    run = _run(lifecycle)
    attempts = _attempts(lifecycle, 2)
    native_bytes = lifecycle.path.read_bytes()
    raw_hash = hashlib.sha256(native_bytes).hexdigest()
    clock = _Clock()
    budget = _canonical(tmp_path, clock, task_id=run.identity.task_id, run_id=run.identity.run_id)
    findings = [Finding(code="TEST_TIME_BUDGET_EXHAUSTED", subject_id=run.identity.run_id,
        message="evaluation observation incomplete", origin="evaluation")]
    if concrete_owner_failure:
        with pytest.raises(CanonicalBudgetCapacityExceeded) as concrete:
            budget.reserve(_ref("overrun").model_copy(update={"task_id": run.identity.task_id, "run_id": run.identity.run_id}),
                CanonicalBudgetVectorSchema(logical_tool_calls=4), expected_revision=0)
        findings.append(Finding(code="CANONICAL_BUDGET_CAPACITY_EXCEEDED", subject_id=run.identity.run_id,
            message=str(concrete.value), origin="production_owner"))
    budget_bytes = budget.storage_path.read_bytes()
    monkeypatch.setattr(lifecycle, "transition_run", lambda *args, **kwargs: pytest.fail("observer mutated Run"))
    monkeypatch.setattr(lifecycle, "terminalize_noncancel_failure", lambda *args, **kwargs: pytest.fail("observer fabricated failure"))
    clock.expire()
    evidence = EvaluationEvidenceRef(locator=str(lifecycle.path), owner="RunAttemptLifecycleService",
        family="LIFECYCLE", raw_sha256=raw_hash, availability="PERSISTED", integrity="HASH_BOUND",
        completeness="INCOMPLETE_OBSERVATION")
    observation = StageObservation(unit_id="F01", subject_id=run.identity.run_id,
        observed_execution_status="E2E_INCOMPLETE", measurement_state="UNMEASURED", evidence_refs=(evidence,),
        payload={"evaluation_result": "INCOMPLETE", "reason": "TEST_TIME_BUDGET_EXHAUSTED",
            "production_failure_from_harness_clock": False, "concrete_owner_failure_observed": concrete_owner_failure,
            "run_status_at_cut": run.status.value, "attempt_statuses_at_cut": [row.status.value for row in attempts]})
    batch = ObservationBatch(domain_id="runtime", stages=[observation], findings=findings,
        limitations=["TEST_TIME_BUDGET_EXHAUSTED: observer incomplete; production result is not inferred"])
    report = build_report(EvaluationRunIdentity(evaluation_id="wallclock-observer-local", subject_id=run.identity.run_id,
        production_run_id=run.identity.run_id), [batch])
    data = report.to_dict()
    assert data["execution_coverage"]["read_status"] == "PARTIAL"
    assert data["stage_quality"][0]["observed_execution_status"] == "E2E_INCOMPLETE"
    assert data["stage_quality"][0]["payload"]["production_failure_from_harness_clock"] is False
    assert data["stage_quality"][0]["evidence_refs"][0]["raw_sha256"] == raw_hash
    assert [row["code"] for row in data["findings"]] == [row.code for row in findings]
    assert lifecycle.load_run_lifecycle(run.identity.run_id) == run
    assert [lifecycle.load_attempt(row.identity.attempt_id) for row in attempts] == attempts
    assert lifecycle.path.read_bytes() == native_bytes
    assert budget.storage_path.read_bytes() == budget_bytes
