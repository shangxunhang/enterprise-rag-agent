"""Frozen C1: one native journal frame for failure and retry/successor."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    AttemptLifecycleStatus, RunFinalizationBarrierSchema, RunIdentitySchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError, LifecycleIntegrityError, LifecycleRevisionConflict,
    RunAttemptLifecycleService,
)
from core.hashing import canonical_sha256


class _Clock:
    def now_iso(self):
        return "2026-10-07T07:00:00+00:00"


def _service(path):
    return RunAttemptLifecycleService(path, clock=_Clock())


def _run(service, status=RunLifecycleStatus.RUNNING):
    run = service.claim_new_run(RunIdentitySchema(
        run_id="run-c1", task_id="task-c1", tenant_id="tenant-c1",
        canonical_request_sha256=hashlib.sha256(b"c1-request").hexdigest(),
    ))
    if status is not RunLifecycleStatus.PENDING:
        run = service.transition_run(run.identity.run_id, 0, RunLifecycleStatus.RUNNING)
    return run


def _attempts(service, count):
    rows = []
    for index in range(count):
        row = service.open_attempt("run-c1", f"section:{index}", "snapshot-c1", attempt_id=f"old-{index}")
        if index != 1:
            row = service.activate_attempt(row.identity.attempt_id, 0)
        rows.append(row)
    return rows


def _barrier():
    exact = {"schema_version": "post_write_control_decision_ref_v1", "control_decision_id": "c1-control", "control_decision_hash": canonical_sha256("c1-control")}
    envelope = {"schema_version": "run_completion_exact_ref_envelope_v1", "source_authority": "POST_WRITE_CONTROL", "source_kind": "POST_WRITE_CONTROL_DECISION", "source_scope_ref": "c1-control", "exact_ref_payload": exact, "exact_ref_schema_version": exact["schema_version"], "exact_ref_sha256": canonical_sha256(exact)}
    payload = {"schema_version": "run_finalization_barrier_v1", "run_id": "run-c1", "final_control_decision_ref": envelope, "barrier_contract_version": "v1"}
    return RunFinalizationBarrierSchema(**payload, barrier_sha256=canonical_sha256(payload))


def _failure(service, run, *, trigger=None, target=RunLifecycleStatus.RETRYABLE_FAILED, key="failure-c1", expected=None, reason="authority://c1-failure"):
    return service.terminalize_noncancel_failure(
        "run-c1", run.lifecycle_revision, target, reason,
        triggering_attempt_id=trigger, expected_attempt_revisions=expected, operation_key=key,
    )


def _resume(service, run, *, key="resume-c1", scope="section:0", snapshot="snapshot-next", attempt_id="successor-c1"):
    return service.resume_retryable_with_successor(
        "run-c1", run.lifecycle_revision, scope, snapshot, "authority://c1-retry",
        attempt_id=attempt_id, operation_key=key,
    )


@pytest.mark.parametrize("initial,count,target", [
    (RunLifecycleStatus.PENDING, 0, RunLifecycleStatus.FAILED),
    (RunLifecycleStatus.RUNNING, 0, RunLifecycleStatus.FAILED),
    (RunLifecycleStatus.RUNNING, 0, RunLifecycleStatus.RETRYABLE_FAILED),
    (RunLifecycleStatus.RUNNING, 1, RunLifecycleStatus.FAILED),
    (RunLifecycleStatus.RUNNING, 3, RunLifecycleStatus.RETRYABLE_FAILED),
    (RunLifecycleStatus.FINALIZING, 0, RunLifecycleStatus.FAILED),
])
def test_failure_0_1_n_and_finalizing_one_durable_frame(tmp_path, initial, count, target):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service, initial)
    rows = _attempts(service, count)
    if initial is RunLifecycleStatus.FINALIZING:
        run = service.transition_run("run-c1", run.lifecycle_revision, initial, run_finalization_barrier=_barrier())
    prefix = service.path.read_bytes()
    result = _failure(service, run, trigger=rows[0].identity.attempt_id if rows else None, target=target)
    appended = service.path.read_bytes()[len(prefix):]
    assert appended.endswith(b"\n") and appended.count(b"\n") == 1
    frame = json.loads(appended)
    assert frame["schema_version"] == "run_attempt_lifecycle_composite_event_v1"
    assert frame["event_sha256"] == canonical_sha256({key: value for key, value in frame.items() if key != "event_sha256"})
    assert result.resulting_run.status is target
    assert result.resulting_run.lifecycle_revision == run.lifecycle_revision + 1
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == result.resulting_run
    assert len(result.resulting_attempts) == count
    for index, row in enumerate(rows):
        changed = reopened.load_attempt(row.identity.attempt_id)
        assert changed.status is (AttemptLifecycleStatus.FAILED if index == 0 else AttemptLifecycleStatus.CANCELLED)
        assert changed.lifecycle_revision == row.lifecycle_revision + 1
        assert changed.finished_at is not None
        if index:
            assert changed.failure_ref == "run_terminal_failure_sibling_cancel"
    if initial is RunLifecycleStatus.FINALIZING:
        assert result.resulting_run.run_finalization_barrier == run.run_finalization_barrier
        assert result.resulting_run.run_finalization_barrier_ref == run.run_finalization_barrier_ref


def test_finalizing_with_current_attempt_rejects_without_repair(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 1)
    run = service.transition_run("run-c1", run.lifecycle_revision, RunLifecycleStatus.FINALIZING, run_finalization_barrier=_barrier())
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _failure(service, run, trigger=rows[0].identity.attempt_id, target=RunLifecycleStatus.FAILED)
    assert service.path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "stale", "foreign", "wrong_trigger"])
def test_failure_full_set_cas_and_trigger_exactness(tmp_path, change):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    expected = {row.identity.attempt_id: row.lifecycle_revision for row in rows}
    trigger = "old-0"
    if change == "missing":
        expected.pop("old-1")
    elif change == "stale":
        expected["old-0"] -= 1
    elif change == "foreign":
        expected["foreign"] = 0
    else:
        trigger = "not-current"
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _failure(service, run, trigger=trigger, expected=expected)
    assert service.path.read_bytes() == before


def test_failure_replay_is_exact_and_changed_request_conflicts(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 3)
    first = _failure(service, run, trigger="old-0")
    before = service.path.read_bytes()
    assert _failure(_service(service.path), run, trigger="old-0") == first
    with pytest.raises(LifecycleIntegrityError):
        _failure(service, run, trigger="old-0", reason="authority://changed")
    assert service.path.read_bytes() == before


def test_frame_fsynced_before_lost_return_can_replay(tmp_path, monkeypatch):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 3)
    original = service.log.append_serialized

    def append_then_lose_return(serialized):
        original(serialized)
        raise OSError("simulated return loss after native append/fsync")

    monkeypatch.setattr(service.log, "append_serialized", append_then_lose_return)
    with pytest.raises(OSError):
        _failure(service, run, trigger="old-0")
    reopened = _service(service.path)
    before = service.path.read_bytes()
    recovered = _failure(reopened, run, trigger="old-0")
    assert recovered.resulting_run.status is RunLifecycleStatus.RETRYABLE_FAILED
    assert len(recovered.resulting_attempts) == 3
    assert service.path.read_bytes() == before


@pytest.mark.parametrize("cut", ["no_newline", "half", "utf8"])
def test_torn_composite_never_exposes_partial_members(tmp_path, cut):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    prefix = service.path.read_bytes()
    _failure(service, run, trigger="old-0", reason="authority://失败")
    frame = service.path.read_bytes()[len(prefix):]
    if cut == "no_newline":
        tail = frame[:-1]
    elif cut == "half":
        tail = frame[:len(frame) // 2]
    else:
        index = frame.index("失".encode("utf-8"))
        tail = frame[:index + 1]
    service.path.write_bytes(prefix + tail)
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == run
    assert all(reopened.load_attempt(row.identity.attempt_id) == row for row in rows)
    assert service.path.read_bytes() == prefix


@pytest.mark.parametrize("tamper", ["omitted_sibling", "wrong_disposition", "wrong_preimage", "wrong_hash"])
def test_complete_bad_frame_rejected_before_any_result_is_exposed(tmp_path, tamper):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 3)
    prefix = service.path.read_bytes()
    _failure(service, run, trigger="old-0")
    frame = json.loads(service.path.read_bytes()[len(prefix):])
    if tamper == "omitted_sibling":
        frame["resulting_attempts"].pop()
    elif tamper == "wrong_disposition":
        frame["resulting_attempts"][1]["status"] = "failed"
    elif tamper == "wrong_preimage":
        frame["before_attempts"][0]["lifecycle_revision"] += 1
    if tamper == "wrong_hash":
        frame["event_sha256"] = "0" * 64
    else:
        frame["event_sha256"] = canonical_sha256({key: value for key, value in frame.items() if key != "event_sha256"})
    service.path.write_bytes(prefix + (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _service(service.path).load_run("run-c1")
    with pytest.raises(LifecycleIntegrityError):
        _service(service.path).load_attempt("old-0")
    assert service.path.read_bytes() == before


def test_two_failure_contenders_have_one_cas_winner(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 3)
    start = threading.Barrier(2)

    def contender(key):
        start.wait(timeout=10)
        return _failure(_service(service.path), run, trigger="old-0", key=key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender, f"contender-{index}") for index in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=15))
            except LifecycleRevisionConflict:
                outcomes.append(None)
    assert sum(item is not None for item in outcomes) == 1
    assert sum(json.loads(line).get("schema_version") == "run_attempt_lifecycle_composite_event_v1" for line in service.path.read_text(encoding="utf-8").splitlines()) == 1


def test_open_before_snapshot_is_included_and_open_after_terminal_is_denied(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 1)
    opened = service.open_attempt("run-c1", "section:late", "late-snapshot", attempt_id="late")
    result = _failure(service, run, trigger="old-0")
    assert {row.identity.attempt_id for row in result.resulting_attempts} == {"old-0", opened.identity.attempt_id}
    with pytest.raises(LifecycleCurrentnessError):
        service.open_attempt("run-c1", "section:new", "later")


@pytest.mark.parametrize("has_prior", [False, True])
def test_resume_atomically_opens_pending_successor_and_old_never_regains_authority(tmp_path, has_prior):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 1) if has_prior else []
    failed = _failure(service, run, trigger="old-0" if rows else None).resulting_run
    prefix = service.path.read_bytes()
    result = _resume(service, failed)
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1
    successor = result.resulting_attempts[0]
    assert successor.status is AttemptLifecycleStatus.PENDING
    assert successor.identity.attempt_ordinal == (2 if has_prior else 1)
    assert successor.supersedes_attempt_id == ("old-0" if has_prior else None)
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == result.resulting_run
    assert reopened.load_attempt("successor-c1") == successor
    with pytest.raises(LifecycleCurrentnessError):
        reopened.assert_attempt_committable(run_id="run-c1", attempt_scope_ref="section:0", attempt_id="successor-c1", input_snapshot_version="snapshot-next")
    if rows:
        with pytest.raises(LifecycleIntegrityError):
            reopened.transition_attempt("old-0", rows[0].lifecycle_revision + 1, AttemptLifecycleStatus.RUNNING)
        with pytest.raises(LifecycleCurrentnessError):
            reopened.assert_attempt_committable(run_id="run-c1", attempt_scope_ref="section:0", attempt_id="old-0", input_snapshot_version="snapshot-c1")
    reopened.activate_attempt("successor-c1", 0)
    reopened.assert_attempt_committable(run_id="run-c1", attempt_scope_ref="section:0", attempt_id="successor-c1", input_snapshot_version="snapshot-next")
    assert reopened.load_run_lifecycle_revision("run-c1", failed.lifecycle_revision) == failed


def test_resume_replay_conflict_and_mixed_v2_history(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 1)
    failed = _failure(service, run, trigger="old-0").resulting_run
    first = _resume(service, failed)
    before = service.path.read_bytes()
    assert _resume(_service(service.path), failed) == first
    with pytest.raises(LifecycleIntegrityError):
        _resume(service, failed, snapshot="changed")
    assert service.path.read_bytes() == before
    service.activate_attempt("successor-c1", 0)
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == first.resulting_run
    assert reopened.load_attempt("successor-c1").status is AttemptLifecycleStatus.RUNNING
    assert reopened.load_run_lifecycle_revision("run-c1", run.lifecycle_revision) == run


def test_resume_with_unresolved_current_attempt_fails_closed(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    _attempts(service, 1)
    # Existing v2 reader can represent this inconsistent preimage; composite cannot repair it.
    failed = service.transition_run("run-c1", run.lifecycle_revision, RunLifecycleStatus.RETRYABLE_FAILED)
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _resume(service, failed)
    assert service.path.read_bytes() == before


def _actual_stage_failure(service, *, retryable=False, cancelled=False):
    from types import SimpleNamespace
    from apps.enterprise_document.schemas.scheme_generation_graph import SchemeGenerationGraphPayloadSchema
    from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
    from schemas.common import ErrorSchema
    from schemas.failure import FailureCategory

    # Execute the real public stage method with only unrelated projection sinks stubbed.
    stage = object.__new__(SchemeGenerationStageService)
    stage.target_workflow_runtime = SimpleNamespace(lifecycle_service=service)
    stage.target_post_write_composition = None
    stage.agent_name, stage.agent_type = "scheme_writer", "document_generation"
    stage.runtime_support = SimpleNamespace(now_iso=_Clock().now_iso)
    stage.state_writer = SimpleNamespace(
        add_error=lambda state, error: state.errors.append(error),
        set_scheme_artifacts=lambda *args, **kwargs: None,
    )
    error = ErrorSchema(error_code="C1_CALLER_FAILURE", error_type="ControlledLocalFailure",
        message="Frozen C1 directed caller control", retryable=retryable,
        failure_category=FailureCategory.CANCELLED if cancelled else FailureCategory.VALIDATION)
    payload = SchemeGenerationGraphPayloadSchema(task_id="task-c1", run_id="run-c1",
        terminal_error=error, failed_business_node="prepare_document_plan")
    shared = SimpleNamespace(task_id="task-c1", run_id="run-c1", errors=[])
    return stage.finalize_workflow_failure(shared, payload)


@pytest.mark.parametrize("initial,count,retryable", [
    (RunLifecycleStatus.PENDING, 0, False),
    (RunLifecycleStatus.RUNNING, 0, False),
    (RunLifecycleStatus.RUNNING, 1, True),
    (RunLifecycleStatus.FINALIZING, 0, False),
])
def test_actual_stage_noncancel_failure_uses_composite_owner(tmp_path, initial, count, retryable):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service, initial)
    rows = _attempts(service, count)
    if initial is RunLifecycleStatus.FINALIZING:
        run = service.transition_run("run-c1", run.lifecycle_revision, initial, run_finalization_barrier=_barrier())
    prefix = service.path.read_bytes()
    result = _actual_stage_failure(service, retryable=retryable)
    expected = RunLifecycleStatus.RETRYABLE_FAILED if retryable else RunLifecycleStatus.FAILED
    assert service.load_run("run-c1").status is expected
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1
    assert json.loads(service.path.read_bytes()[len(prefix):])["schema_version"] == "run_attempt_lifecycle_composite_event_v1"
    assert result.terminal_outcome.run_status is expected
    if rows:
        assert service.load_attempt(rows[0].identity.attempt_id).status is AttemptLifecycleStatus.FAILED


def test_actual_stage_user_cancel_keeps_existing_a4_path(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    _run(service)
    prefix = service.path.read_bytes()
    result = _actual_stage_failure(service, cancelled=True)
    assert service.load_run("run-c1").status is RunLifecycleStatus.CANCELLED
    assert result.terminal_outcome.run_status is RunLifecycleStatus.CANCELLED
    assert json.loads(service.path.read_bytes()[len(prefix):])["schema_version"] == "run_attempt_lifecycle_event_v2"



def _document_failure(service, run, rows, *, key="r02-document-failure",
                      reason="authority://r02-document-failure", **kwargs):
    return service.terminalize_document_failure(
        run.identity.run_id, run.lifecycle_revision, RunLifecycleStatus.FAILED, reason,
        expected_attempt_revisions={
            row.identity.attempt_id: row.lifecycle_revision for row in rows
        },
        operation_key=key, **kwargs,
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_r02_document_failure_cancels_exact_current_set_one_frame(tmp_path, count):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    history = service.open_attempt(
        "run-c1", "section:completed-history", "historical-snapshot",
        attempt_id="completed-history",
    )
    history = service.activate_attempt(history.identity.attempt_id, 0)
    history = service.transition_attempt(
        history.identity.attempt_id, history.lifecycle_revision,
        AttemptLifecycleStatus.COMPLETED,
    )
    rows = _attempts(service, count)
    prefix = service.path.read_bytes()
    result = _document_failure(service, run, rows)
    appended = service.path.read_bytes()[len(prefix):]
    assert appended.endswith(b"\n") and appended.count(b"\n") == 1
    frame = json.loads(appended)
    assert frame["request"]["kind"] == "DOCUMENT_NONCANCEL_FAILURE"
    assert frame["request"]["triggering_attempt_id"] is None
    assert frame["successor_attempt_id"] is None
    assert frame["event_sha256"] == canonical_sha256({
        key: value for key, value in frame.items() if key != "event_sha256"
    })
    assert result.resulting_run.status is RunLifecycleStatus.FAILED
    assert result.resulting_run.lifecycle_revision == run.lifecycle_revision + 1
    assert {row.identity.attempt_id for row in result.resulting_attempts} == {
        row.identity.attempt_id for row in rows
    }
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == result.resulting_run
    for previous in rows:
        current = reopened.load_attempt(previous.identity.attempt_id)
        assert current.status is AttemptLifecycleStatus.CANCELLED
        assert current.lifecycle_revision == previous.lifecycle_revision + 1
        assert current.finished_at is not None
        assert current.failure_ref == "run_document_failure_cancel"
    assert reopened.load_attempt("completed-history") == history



@pytest.mark.parametrize("count", [0, 1, 3])
def test_r02_document_failure_finalizing_keeps_exact_barrier(tmp_path, count):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, count)
    run = service.transition_run(
        "run-c1", run.lifecycle_revision, RunLifecycleStatus.FINALIZING,
        run_finalization_barrier=_barrier(),
    )
    prefix = service.path.read_bytes()
    result = _document_failure(service, run, rows)
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1
    assert result.resulting_run.status is RunLifecycleStatus.FAILED
    assert result.resulting_run.run_finalization_barrier == run.run_finalization_barrier
    assert result.resulting_run.run_finalization_barrier_ref == run.run_finalization_barrier_ref
    assert all(row.status is AttemptLifecycleStatus.CANCELLED for row in result.resulting_attempts)
    assert _service(service.path).load_run("run-c1") == result.resulting_run
    with pytest.raises(LifecycleIntegrityError):
        service.resume_retryable_with_successor(
            "run-c1", result.resulting_run.lifecycle_revision,
            "section:0", "new-input", "authority://forbidden-document-retry",
        )


@pytest.mark.parametrize("invalid", ["retryable", "trigger", "successor", "missing_map"])
def test_r02_document_request_rejects_unauthorized_fields(tmp_path, invalid):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    values = dict(
        kind="DOCUMENT_NONCANCEL_FAILURE", run_id="run-c1",
        expected_revision=run.lifecycle_revision,
        target_status=RunLifecycleStatus.FAILED, reason_ref="authority://r02",
        expected_attempt_revisions={
            row.identity.attempt_id: row.lifecycle_revision for row in rows
        },
    )
    if invalid == "retryable":
        values["target_status"] = RunLifecycleStatus.RETRYABLE_FAILED
    elif invalid == "trigger":
        values["triggering_attempt_id"] = rows[0].identity.attempt_id
    elif invalid == "successor":
        values.update(attempt_scope_ref="section:0", input_snapshot_version="next", attempt_id="next")
    else:
        values["expected_attempt_revisions"] = None
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        service._composite_request(**values)
    assert service.path.read_bytes() == before


@pytest.mark.parametrize("change", [
    "run_revision", "missing", "stale", "foreign", "wrong_run", "opened_after_snapshot",
    "finalizing_stale_run_revision",
])
def test_r02_document_fixed_preimage_rejects_drift(tmp_path, change):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    expected = {row.identity.attempt_id: row.lifecycle_revision for row in rows}
    run_id, revision = "run-c1", run.lifecycle_revision
    if change == "run_revision":
        revision -= 1
    elif change == "missing":
        expected.pop("old-1")
    elif change == "stale":
        expected["old-0"] -= 1
    elif change == "foreign":
        expected["foreign-attempt"] = 0
    elif change == "wrong_run":
        run_id = "foreign-run"
    elif change == "opened_after_snapshot":
        service.open_attempt("run-c1", "section:late", "late-input", attempt_id="late")
    else:
        service.transition_run(
            "run-c1", run.lifecycle_revision, RunLifecycleStatus.FINALIZING,
            run_finalization_barrier=_barrier(),
        )
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        service.terminalize_document_failure(
            run_id, revision, RunLifecycleStatus.FAILED, "authority://r02-drift",
            expected_attempt_revisions=expected, operation_key="r02-fixed-preimage",
        )
    assert service.path.read_bytes() == before


def test_r02_document_replay_is_exact_without_refreshing_preimage(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    first = _document_failure(service, run, rows)
    before = service.path.read_bytes()
    assert _document_failure(_service(service.path), run, rows) == first
    with pytest.raises(LifecycleRevisionConflict):
        _document_failure(_service(service.path), run, rows, reason="authority://changed")
    with pytest.raises(LifecycleRevisionConflict):
        _document_failure(_service(service.path), run, rows, key="new-operation-same-old-preimage")
    assert service.path.read_bytes() == before
    assert _service(service.path).load_run("run-c1") == first.resulting_run


def test_r02_late_document_intent_cannot_cancel_resumed_successor(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    original_run = _run(service)
    original_rows = _attempts(service, 3)
    # A competing old-kind retryable failure wins after the document caller
    # captured its preimage. A FAILED document Run itself is never resumed.
    failed = _failure(service, original_run, trigger="old-0").resulting_run
    resumed = _resume(service, failed)
    successor = service.activate_attempt("successor-c1", 0)
    before = service.path.read_bytes()
    with pytest.raises(LifecycleRevisionConflict):
        _document_failure(service, original_run, original_rows)
    assert service.path.read_bytes() == before
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == resumed.resulting_run
    assert reopened.load_attempt("successor-c1") == successor
    reopened.assert_attempt_committable(
        run_id="run-c1", attempt_scope_ref="section:0", attempt_id="successor-c1",
        input_snapshot_version="snapshot-next",
    )


@pytest.mark.parametrize("cut", ["before_append", "after_fsync", "readback"])
def test_r02_document_owner_io_failure_has_only_durable_owner_facts(tmp_path, monkeypatch, cut):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    prefix = service.path.read_bytes()
    append = service.log.append_serialized
    decode = service._decode_history_unlocked
    decode_count = 0

    def append_with_cut(serialized):
        if cut == "before_append":
            raise OSError("controlled pre-append IO failure")
        append(serialized)
        if cut == "after_fsync":
            raise OSError("controlled lost return after native append/fsync")

    def decode_with_cut():
        nonlocal decode_count
        decode_count += 1
        if cut == "readback" and decode_count == 2:
            raise OSError("controlled native durable readback failure")
        return decode()

    monkeypatch.setattr(service.log, "append_serialized", append_with_cut)
    monkeypatch.setattr(service, "_decode_history_unlocked", decode_with_cut)
    with pytest.raises(OSError):
        _document_failure(service, run, rows)
    reopened = _service(service.path)
    if cut == "before_append":
        assert service.path.read_bytes() == prefix
        assert reopened.load_run("run-c1") == run
        assert all(reopened.load_attempt(row.identity.attempt_id) == row for row in rows)
    else:
        assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1
        assert reopened.load_run("run-c1").status is RunLifecycleStatus.FAILED
        assert all(reopened.load_attempt(row.identity.attempt_id).status is AttemptLifecycleStatus.CANCELLED for row in rows)
    # Retry uses exactly the original request. An ack lost after fsync replays
    # the durable frame; a pre-append failure creates the first and only frame.
    recovered = _document_failure(reopened, run, rows)
    before_replay = service.path.read_bytes()
    assert recovered.resulting_run.status is RunLifecycleStatus.FAILED
    assert _document_failure(_service(service.path), run, rows) == recovered
    assert service.path.read_bytes() == before_replay
    assert service.path.read_bytes()[len(prefix):].count(b"\n") == 1


@pytest.mark.parametrize("cut", ["no_newline", "half", "utf8"])
def test_r02_document_torn_frame_exposes_no_partial_members(tmp_path, cut):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    prefix = service.path.read_bytes()
    _document_failure(service, run, rows, reason="authority://文档故障")
    frame = service.path.read_bytes()[len(prefix):]
    if cut == "no_newline":
        tail = frame[:-1]
    elif cut == "half":
        tail = frame[:len(frame) // 2]
    else:
        index = frame.index("文".encode("utf-8"))
        tail = frame[:index + 1]
    service.path.write_bytes(prefix + tail)
    reopened = _service(service.path)
    assert reopened.load_run("run-c1") == run
    assert all(reopened.load_attempt(row.identity.attempt_id) == row for row in rows)
    assert service.path.read_bytes() == prefix


@pytest.mark.parametrize("tamper", [
    "missing_member", "extra_member", "wrong_disposition", "wrong_preimage", "wrong_hash",
])
def test_r02_document_complete_tampered_frame_is_rejected(tmp_path, tamper):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    prefix = service.path.read_bytes()
    _document_failure(service, run, rows)
    frame = json.loads(service.path.read_bytes()[len(prefix):])
    if tamper == "missing_member":
        frame["resulting_attempts"].pop()
    elif tamper == "extra_member":
        frame["resulting_attempts"].append(dict(frame["resulting_attempts"][0]))
    elif tamper == "wrong_disposition":
        frame["resulting_attempts"][0]["status"] = "failed"
    elif tamper == "wrong_preimage":
        frame["before_attempts"][0]["lifecycle_revision"] += 1
    if tamper == "wrong_hash":
        frame["event_sha256"] = "0" * 64
    else:
        frame["event_sha256"] = canonical_sha256({
            key: value for key, value in frame.items() if key != "event_sha256"
        })
    service.path.write_bytes(prefix + (json.dumps(frame) + "\n").encode("utf-8"))
    before = service.path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _service(service.path).load_run("run-c1")
    for row in rows:
        with pytest.raises(LifecycleIntegrityError):
            _service(service.path).load_attempt(row.identity.attempt_id)
    assert service.path.read_bytes() == before


def test_r02_document_failure_races_attempt_open_with_one_winner(tmp_path):
    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    start = threading.Barrier(2)

    def terminalize():
        start.wait(timeout=10)
        return _document_failure(_service(service.path), run, rows)

    def open_late():
        start.wait(timeout=10)
        return _service(service.path).open_attempt(
            "run-c1", "section:late", "late-input", attempt_id="late",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(terminalize), pool.submit(open_late)]
        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=15))
            except (LifecycleRevisionConflict, LifecycleCurrentnessError):
                results.append(None)
    assert sum(result is not None for result in results) == 1
    reopened = _service(service.path)
    if results[0] is not None:
        assert reopened.load_run("run-c1").status is RunLifecycleStatus.FAILED
        assert all(reopened.load_attempt(row.identity.attempt_id).status is AttemptLifecycleStatus.CANCELLED for row in rows)
        with pytest.raises(KeyError):
            reopened.load_attempt("late")
    else:
        assert reopened.load_run("run-c1") == run
        assert reopened.load_attempt("late") == results[1]
        assert all(reopened.load_attempt(row.identity.attempt_id) == row for row in rows)


@pytest.mark.parametrize("relative_path,expected_sha256,expected_kinds,expected_event_hashes", [
    (
        "batch_017/temp/test_failure_0_1_n_and_finaliz4/lifecycle.jsonl",
        "d51a58bc3dd967bd1fcbbf199ff01ecef5c9b81b51b236ee3aa96cbe94cfd0f1",
        ["NONCANCEL_FAILURE"],
        ["c9bb76beca83b0554981a9ccc58fc0e0a4407502e795c851e588ff0da814279d"],
    ),
    (
        "batch_017/temp/test_resume_replay_conflict_an0/lifecycle.jsonl",
        "9fe103e709faeeae05eb4f28e680eabb6f82e01e6bd13646cc829ac66b3d3117",
        ["NONCANCEL_FAILURE", "RETRY_WITH_SUCCESSOR"],
        ["c3a6c43fae5b1dffcf0846de361f89a3ec8f006a634967d3c7e069a5b30840c8",
         "2aa6ba7f2b61eba09c7ef8fc898f6e64362d6e7e3c68a8b4bb87d6e370b9955b"],
    ),
], ids=["historical-failure-v1", "historical-retry-v1"])
def test_r02_native_decoder_reads_frozen_old_frames_without_disk_mutation(
    relative_path, expected_sha256, expected_kinds, expected_event_hashes,
):
    from pathlib import Path
    from types import SimpleNamespace

    source = Path("E:/rag-agent-tmp/BC33_FULL_OFFLINE_SWEEP_20261028") / relative_path
    if not source.is_file():
        pytest.skip(f"R02 required frozen historical journal absent: {source}")
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected_sha256
    # The constructor creates locks/recovery effects; use the canonical decoder
    # with a complete in-memory line reader, exactly as the independent audit.
    owner = object.__new__(RunAttemptLifecycleService)
    owner.path = source
    owner.log = SimpleNamespace(read_complete_lines=lambda: tuple(raw.decode("utf-8").splitlines()))
    records, frames = owner._decode_history_unlocked()
    runs, attempts = owner._load_all_unlocked(records)
    assert [frame["request"]["kind"] for frame in frames] == expected_kinds
    assert [frame["event_sha256"] for frame in frames] == expected_event_hashes
    assert all(frame["schema_version"] == "run_attempt_lifecycle_composite_event_v1" for frame in frames)
    assert runs["run-c1"].status is (
        RunLifecycleStatus.RUNNING if len(frames) == 2 else RunLifecycleStatus.RETRYABLE_FAILED
    )
    assert attempts["old-0"].status is AttemptLifecycleStatus.FAILED
    assert source.read_bytes() == raw



@pytest.mark.parametrize("barrier_case", ["missing", "stale_ref"])
def test_r02_document_derive_rejects_missing_or_stale_finalizing_barrier(tmp_path, barrier_case):
    from pydantic import ValidationError

    service = _service(tmp_path / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    run = service.transition_run(
        "run-c1", run.lifecycle_revision, RunLifecycleStatus.FINALIZING,
        run_finalization_barrier=_barrier(),
    )
    request = service._composite_request(
        kind="DOCUMENT_NONCANCEL_FAILURE", run_id="run-c1",
        expected_revision=run.lifecycle_revision, target_status=RunLifecycleStatus.FAILED,
        reason_ref="authority://r02-barrier-check",
        expected_attempt_revisions={
            row.identity.attempt_id: row.lifecycle_revision for row in rows
        },
    )
    if barrier_case == "missing":
        invalid_run = run.model_copy(update={
            "run_finalization_barrier": None, "run_finalization_barrier_ref": None,
        })
    else:
        invalid_run = run.model_copy(update={
            "run_finalization_barrier_ref": run.run_finalization_barrier_ref.model_copy(
                update={"barrier_sha256": "0" * 64},
            ),
        })
    before = service.path.read_bytes()
    # model_copy deliberately bypasses validation only in memory. The real
    # journal retains the legal FINALIZING record and its exact barrier.
    with pytest.raises(ValidationError, match="finalization barrier"):
        service._derive_composite(
            request, {"run-c1": invalid_run},
            {row.identity.attempt_id: row for row in rows}, run.updated_at, None,
        )
    assert service.path.read_bytes() == before
    assert _service(service.path).load_run("run-c1") == run


@pytest.mark.parametrize("barrier_case", ["missing", "stale_barrier"])
def test_r02_document_decoder_rejects_rehashed_finalizing_barrier_substitution(tmp_path, barrier_case):
    service = _service(tmp_path / "original" / "lifecycle.jsonl")
    run = _run(service)
    rows = _attempts(service, 3)
    run = service.transition_run(
        "run-c1", run.lifecycle_revision, RunLifecycleStatus.FINALIZING,
        run_finalization_barrier=_barrier(),
    )
    prefix = service.path.read_bytes()
    result = _document_failure(service, run, rows)
    original = service.path.read_bytes()
    frame = json.loads(original[len(prefix):])
    if barrier_case == "missing":
        frame["before_run"]["run_finalization_barrier"] = None
        frame["before_run"]["run_finalization_barrier_ref"] = None
    else:
        # This alternative barrier is internally valid and has fresh hashes;
        # it is stale relative to the exact canonical FINALIZING prefix.
        alternate = run.run_finalization_barrier.model_dump(mode="json")
        envelope = alternate["final_control_decision_ref"]
        envelope["exact_ref_payload"]["control_decision_id"] = "r02-other-control"
        envelope["source_scope_ref"] = "r02-other-control"
        envelope["exact_ref_sha256"] = canonical_sha256(envelope["exact_ref_payload"])
        alternate["barrier_sha256"] = canonical_sha256({
            key: value for key, value in alternate.items() if key != "barrier_sha256"
        })
        alternate = RunFinalizationBarrierSchema.model_validate(alternate)
        for name in ("before_run", "resulting_run"):
            frame[name]["run_finalization_barrier"] = alternate.model_dump(mode="json")
            frame[name]["run_finalization_barrier_ref"] = alternate.as_ref().model_dump(mode="json")
    authority = {
        key: frame[key] for key in (
            "schema_version", "request", "before_run", "before_attempts",
            "previous_scope_attempt", "successor_attempt_id",
        )
    }
    frame["operation_key"] = canonical_sha256(authority)
    frame["event_sha256"] = canonical_sha256({
        key: value for key, value in frame.items() if key != "event_sha256"
    })
    copy_path = tmp_path / "tampered-copy" / "lifecycle.jsonl"
    copy_path.parent.mkdir(parents=True)
    copy_path.write_bytes(prefix + (json.dumps(frame) + "\n").encode("utf-8"))
    tampered = copy_path.read_bytes()
    with pytest.raises(LifecycleIntegrityError):
        _service(copy_path).load_run("run-c1")
    for row in rows:
        with pytest.raises(LifecycleIntegrityError):
            _service(copy_path).load_attempt(row.identity.attempt_id)
    assert copy_path.read_bytes() == tampered
    assert service.path.read_bytes() == original
    assert _service(service.path).load_run("run-c1") == result.resulting_run
