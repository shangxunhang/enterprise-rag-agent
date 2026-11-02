"""I1 contracts, durable replay and deterministic lifecycle-owner races."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RUN_TERMINAL_STATUSES,
    AttemptLifecycleStatus,
    RunFinalizationBarrierSchema,
    RunLifecycleRecordSchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
    LifecycleIntegrityError,
    LifecycleRevisionConflict,
    RunAttemptLifecycleService,
)
from core.hashing import canonical_sha256
from backend.tests.test_multi_agent_v3_lifecycle_commit import (
    _running_run, _service,
)
from backend.tests.test_multi_agent_v3_release_lineage import (
    _assessment_fixture, _barrier_closure,
    _enter_finalizing, _running_lifecycle,
)


def _barrier(run_id="run-1", control_id="control-1"):
    exact = {
        "schema_version": "post_write_control_decision_ref_v1",
        "control_decision_id": control_id,
        "control_decision_hash": canonical_sha256(control_id),
    }
    envelope = {
        "schema_version": "run_completion_exact_ref_envelope_v1",
        "source_authority": "POST_WRITE_CONTROL",
        "source_kind": "POST_WRITE_CONTROL_DECISION",
        "source_scope_ref": control_id,
        "exact_ref_payload": exact,
        "exact_ref_schema_version": exact["schema_version"],
        "exact_ref_sha256": canonical_sha256(exact),
    }
    payload = {
        "schema_version": "run_finalization_barrier_v1",
        "run_id": run_id,
        "final_control_decision_ref": envelope,
        "barrier_contract_version": "v1",
    }
    return RunFinalizationBarrierSchema(
        **payload, barrier_sha256=canonical_sha256(payload)
    )


def _finalize(service, run):
    return _enter_finalizing(service, run, _barrier(run.identity.run_id))


def test_barrier_public_contract_and_content_addressed_integrity():
    from apps.enterprise_document.schemas import agent_rag_v1

    assert agent_rag_v1.RunFinalizationBarrierSchema is RunFinalizationBarrierSchema
    barrier = _barrier()
    assert agent_rag_v1.RunFinalizationBarrierRefSchema is type(barrier.as_ref())
    assert barrier.schema_version == "run_finalization_barrier_v1"
    assert barrier.as_ref().schema_version == "run_finalization_barrier_ref_v1"
    assert barrier == _barrier()
    assert barrier.as_ref() != _barrier(control_id="other").as_ref()
    tampered = barrier.model_dump(mode="json")
    tampered["final_control_decision_ref"]["exact_ref_payload"]["control_decision_id"] = "tampered"
    with pytest.raises(ValidationError, match="hash mismatch"):
        RunFinalizationBarrierSchema.model_validate(tampered)


def test_finalizing_and_abort_do_not_invoke_assessment_resolver(tmp_path):
    service = _service(tmp_path)
    running = _running_run(service)

    def forbidden(_ref):
        pytest.fail("non-SUCCESS lifecycle transition entered assessment resolver")

    service.bind_run_completion_assessment_resolver(forbidden)
    finalizing = _finalize(service, running)
    aborted = service.transition_run("run-1", finalizing.lifecycle_revision, RunLifecycleStatus.RUNNING)
    assert aborted.run_finalization_barrier_ref is None


def test_run_v2_finalizing_is_nonterminal_and_exactly_bound(tmp_path):
    service = _service(tmp_path)
    running = _running_run(service)
    finalizing = _finalize(service, running)
    assert RunLifecycleStatus.FINALIZING.value == "finalizing"
    assert RunLifecycleStatus.FINALIZING not in RUN_TERMINAL_STATUSES
    assert finalizing.schema_version == "run_lifecycle_record_v2"
    assert finalizing.finished_at is None
    assert finalizing.run_completion_assessment_ref is None
    assert finalizing.run_finalization_barrier.as_ref() == finalizing.run_finalization_barrier_ref
    assert RunAttemptLifecycleService(service.path).load_run("run-1") == finalizing
    assert service.load_run_lifecycle_revision("run-1", running.lifecycle_revision) == running
    assert service.load_run_lifecycle_revision("run-1", finalizing.lifecycle_revision) == finalizing
    with pytest.raises(LifecycleIntegrityError):
        service.load_run_lifecycle_revision("run-1", 99)


@pytest.mark.parametrize("change", [
    "no_material", "no_ref", "wrong_run", "wrong_hash", "wrong_ref",
    "running_barrier", "finalizing_assessment", "finalizing_finished", "run_v1",
])
def test_run_v2_rejects_invalid_finalization_bindings(tmp_path, change):
    service = _service(tmp_path)
    finalizing = _finalize(service, _running_run(service))
    payload = finalizing.model_dump(mode="json")
    if change == "no_material":
        payload["run_finalization_barrier"] = None
    elif change == "no_ref":
        payload["run_finalization_barrier_ref"] = None
    elif change == "wrong_run":
        payload["run_finalization_barrier"] = _barrier("other-run").model_dump(mode="json")
    elif change == "wrong_hash":
        payload["run_finalization_barrier"]["barrier_sha256"] = "0" * 64
    elif change == "wrong_ref":
        payload["run_finalization_barrier_ref"]["barrier_sha256"] = "0" * 64
    elif change == "running_barrier":
        payload["status"] = "running"
    elif change == "finalizing_assessment":
        payload["run_completion_assessment_ref"] = {
            "assessment_id": "assessment", "run_id": "run-1",
            "aggregate_result": "SUCCESS", "assessment_contract_version": "v1",
            "semantic_fingerprint": "a" * 64, "assessment_sha256": "b" * 64,
        }
    elif change == "finalizing_finished":
        payload["finished_at"] = payload["updated_at"]
    else:
        payload["schema_version"] = "run_lifecycle_record_v1"
    with pytest.raises(ValidationError):
        RunLifecycleRecordSchema.model_validate(payload)


def test_enter_finalizing_requires_same_run_barrier_and_revision_cas(tmp_path):
    service = _service(tmp_path)
    running = _running_run(service)
    with pytest.raises(LifecycleIntegrityError, match="requires exact"):
        service.transition_run("run-1", running.lifecycle_revision, RunLifecycleStatus.FINALIZING)
    with pytest.raises(LifecycleIntegrityError, match="requires exact"):
        _enter_finalizing(service, running, _barrier("other"))
    with pytest.raises(LifecycleIntegrityError, match="illegal Run transition"):
        service.transition_run("run-1", running.lifecycle_revision, RunLifecycleStatus.SUCCESS)
    finalizing = _finalize(service, running)
    with pytest.raises(LifecycleRevisionConflict):
        _enter_finalizing(service, running, _barrier(control_id="competing"))
    assert service.load_run("run-1") == finalizing


@pytest.mark.parametrize("target", [
    RunLifecycleStatus.PAUSED, RunLifecycleStatus.RETRYABLE_FAILED,
    RunLifecycleStatus.PARTIAL_SUCCESS, RunLifecycleStatus.FINALIZING,
])
def test_finalizing_does_not_expand_transition_graph(tmp_path, target):
    service = _service(tmp_path)
    finalizing = _finalize(service, _running_run(service))
    with pytest.raises(LifecycleIntegrityError, match="illegal Run transition"):
        service.transition_run("run-1", finalizing.lifecycle_revision, target)


@pytest.mark.parametrize("target", [RunLifecycleStatus.FAILED, RunLifecycleStatus.CANCELLED])
def test_finalizing_terminal_failure_keeps_exact_history(tmp_path, target):
    service = _service(tmp_path)
    finalizing = _finalize(service, _running_run(service))
    terminal = service.transition_run("run-1", finalizing.lifecycle_revision, target)
    assert terminal.finished_at is not None
    assert terminal.run_completion_assessment_ref is None
    assert terminal.run_finalization_barrier_ref == finalizing.run_finalization_barrier_ref
    assert RunAttemptLifecycleService(service.path).load_run("run-1") == terminal


@pytest.mark.parametrize("target", [
    AttemptLifecycleStatus.COMPLETED, AttemptLifecycleStatus.PARTIAL,
    AttemptLifecycleStatus.FAILED, AttemptLifecycleStatus.CANCELLED,
    AttemptLifecycleStatus.SUPERSEDED,
])
def test_finalizing_blocks_all_attempt_successors_without_advancing_ledger(tmp_path, target):
    service = _service(tmp_path)
    running = _running_run(service)
    attempt = service.open_attempt("run-1", "section:s", "snapshot")
    attempt = service.activate_attempt(attempt.identity.attempt_id, 0)
    pending = service.open_attempt("run-1", "section:pending", "snapshot")
    finalizing = _finalize(service, running)
    before = service.path.read_bytes()
    with pytest.raises(LifecycleCurrentnessError):
        service.open_attempt("run-1", "section:new", "snapshot")
    with pytest.raises(LifecycleCurrentnessError):
        service.activate_attempt(pending.identity.attempt_id, 0)
    with pytest.raises(LifecycleCurrentnessError):
        service.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision, target)
    with pytest.raises(LifecycleCurrentnessError):
        service.transition_attempt(pending.identity.attempt_id, 0, AttemptLifecycleStatus.CANCELLED)
    with pytest.raises(LifecycleCurrentnessError):
        service.assert_run_committable("run-1")
    with pytest.raises(LifecycleCurrentnessError):
        service.assert_attempt_committable(
            run_id="run-1", attempt_scope_ref="section:s",
            attempt_id=attempt.identity.attempt_id, input_snapshot_version="snapshot",
        )
    assert service.path.read_bytes() == before
    assert service.load_attempt(attempt.identity.attempt_id) == attempt
    assert service.load_run("run-1") == finalizing


def test_abort_finalization_clears_active_barrier_and_restores_all_mutations(tmp_path):
    service = _service(tmp_path)
    running = _running_run(service)
    attempt = service.open_attempt("run-1", "section:s", "snapshot")
    attempt = service.activate_attempt(attempt.identity.attempt_id, 0)
    pending = service.open_attempt("run-1", "section:p", "snapshot")
    finalizing = _finalize(service, running)
    with pytest.raises(LifecycleRevisionConflict):
        service.transition_run("run-1", running.lifecycle_revision, RunLifecycleStatus.RUNNING)
    aborted = service.transition_run("run-1", finalizing.lifecycle_revision, RunLifecycleStatus.RUNNING)
    assert aborted.run_finalization_barrier is None
    assert aborted.run_finalization_barrier_ref is None
    assert aborted.finished_at is None
    service.assert_run_committable("run-1")
    service.assert_attempt_committable(
        run_id="run-1", attempt_scope_ref="section:s", attempt_id=attempt.identity.attempt_id,
        input_snapshot_version="snapshot",
    )
    assert service.open_attempt("run-1", "section:new", "snapshot").lifecycle_revision == 0
    assert service.activate_attempt(pending.identity.attempt_id, 0).status is AttemptLifecycleStatus.RUNNING
    completed = service.transition_attempt(attempt.identity.attempt_id, 1, AttemptLifecycleStatus.COMPLETED)
    reopened = RunAttemptLifecycleService(service.path)
    assert reopened.load_run("run-1") == aborted
    assert reopened.load_attempt(attempt.identity.attempt_id) == completed
    assert reopened.load_run_lifecycle_revision("run-1", finalizing.lifecycle_revision) == finalizing
    assert _finalize(reopened, aborted).status is RunLifecycleStatus.FINALIZING


@pytest.mark.parametrize("change", ["no_assessment", "no_barrier", "no_finished", "other_run"])
def test_success_schema_requires_assessment_and_exact_barrier_lineage(tmp_path, change):
    fx = _assessment_fixture(tmp_path / "completion")
    service, running = _running_lifecycle(tmp_path / "lifecycle.jsonl", resolver=fx["store"].resolve_exact)
    finalizing = _enter_finalizing(service, running, fx["barrier"])
    ref = fx["store"].put_if_absent_or_same(fx["record"])
    terminal = service.transition_run("run-wave11", finalizing.lifecycle_revision, RunLifecycleStatus.SUCCESS,
                                      run_completion_assessment_ref=ref)
    payload = terminal.model_dump(mode="json")
    if change == "no_assessment":
        payload["run_completion_assessment_ref"] = None
    elif change == "no_barrier":
        payload["run_finalization_barrier"] = None
        payload["run_finalization_barrier_ref"] = None
    elif change == "no_finished":
        payload["finished_at"] = None
    else:
        payload["run_completion_assessment_ref"]["run_id"] = "other"
    with pytest.raises(ValidationError):
        RunLifecycleRecordSchema.model_validate(payload)
    reopened = RunAttemptLifecycleService(service.path)
    assert reopened.load_run("run-wave11") == terminal
    assert reopened.load_run_lifecycle_revision("run-wave11", finalizing.lifecycle_revision) == finalizing
    assert reopened.load_run_lifecycle_revision("run-wave11", terminal.lifecycle_revision) == terminal


@pytest.mark.parametrize("mismatch", ["missing", "other_barrier", "other_control"])
def test_success_rejects_assessment_with_wrong_barrier_or_control(tmp_path, mismatch):
    fx = _assessment_fixture(tmp_path / "completion")
    service, running = _running_lifecycle(tmp_path / "lifecycle.jsonl", resolver=fx["store"].resolve_exact)
    finalizing = _enter_finalizing(service, running, fx["barrier"])
    if mismatch == "missing":
        closures = ()
    elif mismatch == "other_barrier":
        closures = (_barrier_closure(_barrier("run-wave11", "other-control")),)
    else:
        # Correct barrier ref, but different final Control envelope in lifecycle.
        closures = (_barrier_closure(fx["barrier"]),)
        service.transition_run("run-wave11", finalizing.lifecycle_revision, RunLifecycleStatus.RUNNING)
        running = service.load_run("run-wave11")
        finalizing = _enter_finalizing(service, running, _barrier("run-wave11", "other-control"))
        closures = (_barrier_closure(finalizing.run_finalization_barrier),)
    record = fx["builder"].build_success(
        run_id="run-wave11", section_completion_refs=fx["section_refs"],
        document_artifact_ref=fx["post_write"]["document"].document_artifact_ref,
        document_post_write_entry_ref=fx["post_write"]["document_entry_ref"],
        post_write_control_decision=fx["final_control"],
        fresh_document_currentness_proof=fx["post_write"]["currentness"],
        post_write_mutation_closure_refs=closures, active_repair_attempt_refs=(),
    )
    ref = fx["store"].put_if_absent_or_same(record)
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError, match="barrier binding"):
        service.transition_run("run-wave11", finalizing.lifecycle_revision, RunLifecycleStatus.SUCCESS,
                               run_completion_assessment_ref=ref)
    assert service.path.read_bytes() == before


REPLAY_PATHS = [
    "load_run", "load_run_identity", "load_run_lifecycle", "revision", "load_attempt",
    "load_latest_attempt", "load_active_attempt", "resolve_read_lineage", "resolve_active_lineage",
]


@pytest.mark.parametrize("reader", REPLAY_PATHS)
@pytest.mark.parametrize("bad_version", [
    "event_v1", "mixed_events", "unknown_event", "run_v1", "unknown_run",
    "unknown_attempt", "missing_run_version", "missing_attempt_version",
])
def test_all_replay_paths_share_strict_event_v2_policy(tmp_path, reader, bad_version):
    service = _service(tmp_path)
    _running_run(service)
    attempt = service.open_attempt("run-1", "section:s", "snapshot", attempt_id="attempt-1")
    service.activate_attempt(attempt.identity.attempt_id, 0)
    events = [json.loads(line) for line in service.path.read_text(encoding="utf-8").splitlines()]
    assert {e["schema_version"] for e in events} == {"run_attempt_lifecycle_event_v2"}
    assert {e["record"]["schema_version"] for e in events} == {
        "run_lifecycle_record_v2", "attempt_lifecycle_record_v1",
    }
    if bad_version == "event_v1":
        for event in events:
            event["schema_version"] = "run_attempt_lifecycle_event_v1"
    elif bad_version in {"mixed_events", "unknown_event"}:
        events[-1]["schema_version"] = (
            "run_attempt_lifecycle_event_v1" if bad_version == "mixed_events" else "unknown"
        )
    elif bad_version in {"run_v1", "unknown_run", "missing_run_version"}:
        if bad_version == "missing_run_version":
            events[0]["record"].pop("schema_version")
        else:
            events[0]["record"]["schema_version"] = "run_lifecycle_record_v1" if bad_version == "run_v1" else "unknown"
    else:
        if bad_version == "missing_attempt_version":
            events[-1]["record"].pop("schema_version")
        else:
            events[-1]["record"]["schema_version"] = "attempt_lifecycle_record_v2"
    service.path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    reopened = RunAttemptLifecycleService(service.path)
    with pytest.raises(LifecycleIntegrityError):
        if reader == "revision":
            reopened.load_run_lifecycle_revision("run-1", 0)
        elif reader == "load_attempt":
            reopened.load_attempt("attempt-1")
        elif reader in {"load_latest_attempt", "load_active_attempt", "resolve_active_lineage"}:
            getattr(reopened, reader)("run-1", "section:s")
        elif reader == "resolve_read_lineage":
            reopened.resolve_read_lineage("run-1", "section:s", expected_attempt_id="attempt-1")
        else:
            getattr(reopened, reader)("run-1")
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        reopened.open_attempt("run-1", "section:new", "snapshot")
    assert service.path.read_bytes() == before


class _ObservedLock:
    """Signal an actual acquisition attempt before waiting on the shared owner."""

    def __init__(self, lock, entered):
        self.lock = lock
        self.entered = entered

    def __enter__(self):
        self.entered.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


def _attempt_mutation(service, kind, attempt):
    if kind == "open":
        return service.open_attempt("run-1", "section:s", "next-snapshot", attempt_id="successor")
    if kind == "activate":
        return service.activate_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision)
    return service.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision,
                                      AttemptLifecycleStatus.COMPLETED)


@pytest.mark.parametrize("kind", ["open", "activate", "transition"])
@pytest.mark.parametrize("pattern", ["A", "B"])
def test_attempt_finalization_races_use_same_owner_lock(tmp_path, monkeypatch, kind, pattern):
    holder = _service(tmp_path)
    running = _running_run(holder)
    attempt = holder.open_attempt("run-1", "section:s", "snapshot", attempt_id="attempt-1")
    if kind != "activate":
        attempt = holder.activate_attempt(attempt.identity.attempt_id, 0)
    waiter = RunAttemptLifecycleService(holder.path)
    assert waiter._lock is holder._lock
    in_commit = threading.Event()
    release = threading.Event()
    waiting = threading.Event()
    waiter_appended = threading.Event()
    append = holder._append
    waiter_append = waiter._append

    def held_append(**kwargs):
        # Both paths have already loaded Run and passed their commit checks.
        in_commit.set()
        assert release.wait(10), "test coordinator did not release owner"
        append(**kwargs)

    def observed_append(**kwargs):
        waiter_appended.set()
        waiter_append(**kwargs)

    monkeypatch.setattr(holder, "_append", held_append)
    monkeypatch.setattr(waiter, "_lock", _ObservedLock(waiter._lock, waiting))
    monkeypatch.setattr(waiter, "_append", observed_append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_finalize, holder, running) if pattern == "A" else pool.submit(
            _attempt_mutation, holder, kind, attempt
        )
        try:
            assert in_commit.wait(10)
            second = pool.submit(_attempt_mutation, waiter, kind, attempt) if pattern == "A" else pool.submit(
                _finalize, waiter, running
            )
            assert waiting.wait(10)
            assert not waiter_appended.is_set()
            assert not second.done()
        finally:
            release.set()
        committed = first.result(timeout=10)
        if pattern == "A":
            with pytest.raises(LifecycleCurrentnessError):
                second.result(timeout=10)
            finalizing = committed
            assert not waiter_appended.is_set()
        else:
            finalizing = second.result(timeout=10)
            assert waiter_appended.is_set()
    reopened = RunAttemptLifecycleService(holder.path)
    assert reopened.load_run("run-1") == finalizing
    assert finalizing.status is RunLifecycleStatus.FINALIZING
    latest = reopened.load_latest_attempt("run-1", "section:s")
    assert latest == (attempt if pattern == "A" else committed)
    events = [json.loads(line) for line in holder.path.read_text(encoding="utf-8").splitlines()]
    finalizing_index = next(i for i, e in enumerate(events) if e["entity_type"] == "run"
                            and e["record"]["status"] == "finalizing")
    assert all(e["entity_type"] != "attempt" for e in events[finalizing_index + 1:])
    before = holder.path.read_bytes()
    with pytest.raises(LifecycleCurrentnessError):
        reopened.open_attempt("run-1", "section:new", "snapshot")
    assert holder.path.read_bytes() == before


def test_concurrent_finalizers_have_one_cas_winner(tmp_path, monkeypatch):
    first_service = _service(tmp_path)
    running = _running_run(first_service)
    second_service = RunAttemptLifecycleService(first_service.path)
    in_commit, release, waiting = threading.Event(), threading.Event(), threading.Event()
    append = first_service._append

    def held_append(**kwargs):
        in_commit.set()
        assert release.wait(10)
        append(**kwargs)

    monkeypatch.setattr(first_service, "_append", held_append)
    monkeypatch.setattr(second_service, "_lock", _ObservedLock(second_service._lock, waiting))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_enter_finalizing, first_service, running, _barrier(control_id="one"))
        try:
            assert in_commit.wait(10)
            second = pool.submit(_enter_finalizing, second_service, running, _barrier(control_id="two"))
            assert waiting.wait(10)
            assert not second.done()
        finally:
            release.set()
        winner = first.result(timeout=10)
        with pytest.raises(LifecycleRevisionConflict):
            second.result(timeout=10)
    assert second_service.load_run("run-1") == winner
    assert winner.run_finalization_barrier == _barrier(control_id="one")
