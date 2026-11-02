from __future__ import annotations

import hashlib
import threading

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    AttemptLifecycleStatus,
    RunIdentitySchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
    LifecycleIntegrityError,
    LifecycleRevisionConflict,
    RunAttemptLifecycleService,
)
from core.runtime.side_effects import (
    CanonicalCommittedResultResolver,
    CommittedResultResolution,
    CommittedResultResolutionStatus,
    InMemorySideEffectCommitStore,
    JsonlSideEffectCommitStore,
    LogicalOperationRef,
    SideEffectClaimStatus,
    SideEffectCoordinationStatus,
    SideEffectIdentityConflict,
    resolve_canonical_committed_result,
)


FINGERPRINT_A = hashlib.sha256(b"semantic-a").hexdigest()
FINGERPRINT_B = hashlib.sha256(b"semantic-b").hexdigest()
REQUEST_HASH = hashlib.sha256(b"material-request").hexdigest()


class _FixedClock:
    def now_iso(self) -> str:
        return "2026-09-02T15:00:00+00:00"


class _SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}_{self.value:03d}"


def _service(tmp_path) -> RunAttemptLifecycleService:
    return RunAttemptLifecycleService(
        tmp_path / "lifecycle.jsonl",
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
    )


def _running_run(service: RunAttemptLifecycleService, *, run_id: str = "run-1"):
    created = service.claim_new_run(
        RunIdentitySchema(
            run_id=run_id,
            task_id=f"task-{run_id}",
            tenant_id="tenant-a",
            canonical_request_sha256=REQUEST_HASH,
        )
    )
    return service.transition_run(
        run_id,
        created.lifecycle_revision,
        RunLifecycleStatus.RUNNING,
    )


def test_run_identity_claim_is_new_run_only_and_request_identity_is_immutable(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.claim_new_run(
        RunIdentitySchema(
            run_id="run-1",
            task_id="task-1",
            tenant_id="tenant-a",
            canonical_request_sha256=REQUEST_HASH,
        )
    )

    assert created.status is RunLifecycleStatus.PENDING
    assert created.lifecycle_revision == 0
    assert service.load_run_identity("run-1") == created.identity

    with pytest.raises(LifecycleIntegrityError, match="already claimed"):
        service.claim_new_run(created.identity)

    changed = created.identity.model_copy(
        update={"canonical_request_sha256": hashlib.sha256(b"other").hexdigest()}
    )
    with pytest.raises(LifecycleIntegrityError, match="already claimed"):
        service.claim_new_run(changed)


def test_run_lifecycle_is_revision_guarded_and_terminal_has_no_outgoing_transition(tmp_path) -> None:
    service = _service(tmp_path)
    running = _running_run(service)

    with pytest.raises(LifecycleRevisionConflict):
        service.transition_run(
            "run-1",
            running.lifecycle_revision - 1,
            RunLifecycleStatus.FAILED,
        )

    terminal = service.transition_run(
        "run-1",
        running.lifecycle_revision,
        RunLifecycleStatus.CANCELLED,
        reason_ref="cancel:test-terminal",
    )
    assert terminal.finished_at is not None
    assert terminal.lifecycle_revision == running.lifecycle_revision + 1

    with pytest.raises(LifecycleIntegrityError, match="illegal Run transition"):
        service.transition_run(
            "run-1",
            terminal.lifecycle_revision,
            RunLifecycleStatus.RUNNING,
        )


def test_attempt_active_lineage_is_scoped_and_only_running_attempt_is_commit_eligible(tmp_path) -> None:
    service = _service(tmp_path)
    _running_run(service)

    opened = service.open_attempt("run-1", "section:s1", "snapshot-v1")
    pending_lineage = service.resolve_active_lineage("run-1", "section:s1")
    assert pending_lineage.attempt_id == opened.identity.attempt_id
    assert pending_lineage.commit_eligible is False

    running = service.activate_attempt(
        opened.identity.attempt_id,
        opened.lifecycle_revision,
    )
    lineage = service.assert_attempt_committable(
        run_id="run-1",
        attempt_scope_ref="section:s1",
        attempt_id=running.identity.attempt_id,
        input_snapshot_version="snapshot-v1",
    )
    assert lineage.commit_eligible is True

    with pytest.raises(LifecycleCurrentnessError):
        service.assert_attempt_committable(
            run_id="run-1",
            attempt_scope_ref="section:s1",
            attempt_id="stale-attempt",
            input_snapshot_version="snapshot-v1",
        )


def test_different_attempt_scopes_may_be_running_concurrently(tmp_path) -> None:
    service = _service(tmp_path)
    _running_run(service)

    a = service.open_attempt("run-1", "section:s1", "snapshot-a")
    b = service.open_attempt("run-1", "section:s2", "snapshot-b")
    a = service.activate_attempt(a.identity.attempt_id, a.lifecycle_revision)
    b = service.activate_attempt(b.identity.attempt_id, b.lifecycle_revision)

    assert service.resolve_active_lineage("run-1", "section:s1").attempt_id == a.identity.attempt_id
    assert service.resolve_active_lineage("run-1", "section:s2").attempt_id == b.identity.attempt_id


def test_same_scope_true_successor_supersedes_old_attempt_before_new_activation(tmp_path) -> None:
    service = _service(tmp_path)
    _running_run(service)

    first = service.open_attempt("run-1", "section:s1", "snapshot-v1")
    first = service.activate_attempt(first.identity.attempt_id, first.lifecycle_revision)

    successor = service.open_attempt("run-1", "section:s1", "snapshot-v2")
    retired = service.load_attempt(first.identity.attempt_id)

    assert retired.status is AttemptLifecycleStatus.SUPERSEDED
    assert successor.supersedes_attempt_id == first.identity.attempt_id
    assert service.resolve_active_lineage("run-1", "section:s1").attempt_id == successor.identity.attempt_id

    with pytest.raises(LifecycleCurrentnessError):
        service.assert_attempt_committable(
            run_id="run-1",
            attempt_scope_ref="section:s1",
            attempt_id=first.identity.attempt_id,
            input_snapshot_version="snapshot-v1",
        )

    successor = service.activate_attempt(
        successor.identity.attempt_id,
        successor.lifecycle_revision,
    )
    assert successor.status is AttemptLifecycleStatus.RUNNING


def test_lifecycle_reopens_from_durable_history_with_exact_currentness(tmp_path) -> None:
    path = tmp_path / "lifecycle.jsonl"
    first = RunAttemptLifecycleService(
        path,
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
    )
    _running_run(first)
    attempt = first.open_attempt("run-1", "section:s1", "snapshot-v1")
    attempt = first.activate_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision)

    reopened = RunAttemptLifecycleService(
        path,
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
    )
    lineage = reopened.resolve_active_lineage("run-1", "section:s1")
    assert lineage.attempt_id == attempt.identity.attempt_id
    assert lineage.input_snapshot_version == "snapshot-v1"
    assert lineage.commit_eligible is True


def test_corrupted_lifecycle_history_fails_closed(tmp_path) -> None:
    path = tmp_path / "lifecycle.jsonl"
    path.write_text(
        '{"schema_version":"run_attempt_lifecycle_event_v1",'
        '"entity_type":"run","event_type":"run_claimed","record":{}}\n',
        encoding="utf-8",
    )
    service = RunAttemptLifecycleService(
        path,
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
    )

    with pytest.raises(LifecycleIntegrityError):
        service.load_run_lifecycle("run-1")


def test_run_level_commit_needs_no_fake_attempt_and_terminal_run_rejects_late_attempt(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    running = _running_run(service)

    assert service.assert_run_committable("run-1").status is RunLifecycleStatus.RUNNING

    attempt = service.open_attempt("run-1", "section:s1", "snapshot-v1")
    attempt = service.activate_attempt(
        attempt.identity.attempt_id,
        attempt.lifecycle_revision,
    )
    service.transition_run(
        "run-1",
        running.lifecycle_revision,
        RunLifecycleStatus.CANCELLED,
        reason_ref="cancel:test",
    )

    with pytest.raises(LifecycleCurrentnessError, match="run_status"):
        service.assert_attempt_committable(
            run_id="run-1",
            attempt_scope_ref="section:s1",
            attempt_id=attempt.identity.attempt_id,
            input_snapshot_version="snapshot-v1",
        )


def test_side_effect_same_operation_same_fingerprint_converges_with_result_refs() -> None:
    store = InMemorySideEffectCommitStore()
    first = store.claim(
        "op-1",
        execution_id="physical-exec-1",
        description="commit artifact",
        semantic_fingerprint=FINGERPRINT_A,
    )
    assert first.status is SideEffectClaimStatus.ACQUIRED
    assert store.mark_committed(
        first,
        committed_result_refs=("artifact:v1", "artifact:v1"),
    )

    replay = store.claim(
        "op-1",
        execution_id="physical-exec-2",
        description="commit artifact",
        semantic_fingerprint=FINGERPRINT_A,
    )
    assert replay.status is SideEffectClaimStatus.ALREADY_COMMITTED
    assert replay.committed_result_refs == ("artifact:v1",)

    resolution = store.resolve("op-1", semantic_fingerprint=FINGERPRINT_A)
    assert resolution.status is SideEffectCoordinationStatus.COMMITTED
    assert resolution.committed_result_refs == ("artifact:v1",)


def test_side_effect_same_operation_different_fingerprint_fails_closed() -> None:
    store = InMemorySideEffectCommitStore()
    first = store.claim(
        "op-1",
        execution_id="exec-1",
        description="commit artifact",
        semantic_fingerprint=FINGERPRINT_A,
    )
    store.mark_committed(first, committed_result_refs=("artifact:v1",))

    with pytest.raises(SideEffectIdentityConflict):
        store.claim(
            "op-1",
            execution_id="exec-2",
            description="commit artifact changed",
            semantic_fingerprint=FINGERPRINT_B,
        )


def test_two_concurrent_claimants_have_at_most_one_executor() -> None:
    store = InMemorySideEffectCommitStore()
    barrier = threading.Barrier(2)
    statuses: list[SideEffectClaimStatus] = []
    lock = threading.Lock()

    def claim(execution_id: str) -> None:
        barrier.wait()
        result = store.claim(
            "op-concurrent",
            execution_id=execution_id,
            description="same operation",
            semantic_fingerprint=FINGERPRINT_A,
        )
        with lock:
            statuses.append(result.status)

    threads = [
        threading.Thread(target=claim, args=("exec-a",)),
        threading.Thread(target=claim, args=("exec-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count(SideEffectClaimStatus.ACQUIRED) == 1
    assert statuses.count(SideEffectClaimStatus.ALREADY_IN_PROGRESS) == 1


def test_jsonl_coordination_result_refs_survive_restart(tmp_path) -> None:
    path = tmp_path / "side-effects.jsonl"
    store = JsonlSideEffectCommitStore(path)
    claim = store.claim(
        "op-persisted",
        execution_id="exec-a",
        description="persisted op",
        semantic_fingerprint=FINGERPRINT_A,
    )
    store.mark_committed(claim, committed_result_refs=("artifact:v9",))

    reopened = JsonlSideEffectCommitStore(path)
    resolution = reopened.resolve(
        "op-persisted",
        semantic_fingerprint=FINGERPRINT_A,
    )
    assert resolution.status is SideEffectCoordinationStatus.COMMITTED
    assert resolution.committed_result_refs == ("artifact:v9",)


class _CanonicalResolver(CanonicalCommittedResultResolver):
    def __init__(self, resolution: CommittedResultResolution) -> None:
        self.resolution = resolution
        self.calls = 0

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        del operation_ref, semantic_fingerprint
        self.calls += 1
        return self.resolution


def test_crash_after_canonical_commit_is_resolved_before_retry_even_if_ledger_lags() -> None:
    operation = LogicalOperationRef(
        logical_operation_id="artifact-commit:run-1:s1:v1",
        operation_kind="ARTIFACT_VERSION_COMMIT",
        run_id="run-1",
        operation_scope_ref="artifact:section:s1",
        attempt_scope_ref="section:s1",
        attempt_id="attempt-1",
    )
    resolver = _CanonicalResolver(
        CommittedResultResolution(
            logical_operation_id=operation.logical_operation_id,
            semantic_fingerprint=FINGERPRINT_A,
            resolution=CommittedResultResolutionStatus.COMMITTED_SAME,
            committed_result_refs=("artifact:v1",),
        )
    )

    resolved = resolve_canonical_committed_result(
        resolver,
        operation,
        FINGERPRINT_A,
    )

    assert resolver.calls == 1
    assert resolved.resolution is CommittedResultResolutionStatus.COMMITTED_SAME
    assert resolved.committed_result_refs == ("artifact:v1",)


def test_canonical_result_resolver_identity_mismatch_fails_closed() -> None:
    operation = LogicalOperationRef(
        logical_operation_id="op-expected",
        operation_kind="TEST_COMMIT",
        run_id="run-1",
        operation_scope_ref="scope-1",
    )
    resolver = _CanonicalResolver(
        CommittedResultResolution(
            logical_operation_id="op-wrong",
            semantic_fingerprint=FINGERPRINT_A,
            resolution=CommittedResultResolutionStatus.COMMITTED_SAME,
            committed_result_refs=("result:1",),
        )
    )

    with pytest.raises(ValueError, match="wrong operation"):
        resolve_canonical_committed_result(
            resolver,
            operation,
            FINGERPRINT_A,
        )


def test_coordination_committed_record_cannot_manufacture_missing_canonical_result() -> None:
    store = InMemorySideEffectCommitStore()
    claim = store.claim(
        "op-ledger-only",
        execution_id="exec-a",
        description="ledger coordination only",
        semantic_fingerprint=FINGERPRINT_A,
    )
    assert store.mark_committed(
        claim,
        committed_result_refs=("ledger:projection-only",),
    )
    assert (
        store.resolve(
            "op-ledger-only",
            semantic_fingerprint=FINGERPRINT_A,
        ).status
        is SideEffectCoordinationStatus.COMMITTED
    )

    operation = LogicalOperationRef(
        logical_operation_id="op-ledger-only",
        operation_kind="TEST_COMMIT",
        run_id="run-1",
        operation_scope_ref="scope-1",
    )
    canonical = resolve_canonical_committed_result(
        _CanonicalResolver(
            CommittedResultResolution(
                logical_operation_id=operation.logical_operation_id,
                semantic_fingerprint=FINGERPRINT_A,
                resolution=CommittedResultResolutionStatus.ABSENT,
            )
        ),
        operation,
        FINGERPRINT_A,
    )

    assert canonical.resolution is CommittedResultResolutionStatus.ABSENT
    assert canonical.committed_result_refs == ()
