from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import threading

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.artifacts import (
    ArtifactLogicalRefSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunIdentitySchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import (
    ArtifactCurrentnessError,
    ArtifactIdentityCollision,
    ArtifactRuntime,
    ArtifactRuntimeIntegrityError,
    ArtifactVersionCollision,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError,
    RunAttemptLifecycleService,
)
from core.runtime.side_effects import (
    CommittedResultResolutionStatus,
    InMemorySideEffectCommitStore,
    LogicalOperationRef,
    SideEffectCoordinationStatus,
)


REQUEST_HASH = hashlib.sha256(b"artifact-runtime-request").hexdigest()


class _FixedClock:
    def now_iso(self) -> str:
        return "2026-09-03T13:00:00+00:00"


class _SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}_{self.value:03d}"


class _AppendThenFailOnce:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.failed = False

    def read_complete_lines(self):  # type: ignore[no-untyped-def]
        return self.delegate.read_complete_lines()

    def append_serialized(self, serialized):  # type: ignore[no-untyped-def]
        self.delegate.append_serialized(serialized)
        if not self.failed:
            self.failed = True
            raise OSError("simulated append response loss")


class _FailMarkCommittedOnce(InMemorySideEffectCommitStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def mark_committed(self, claim, *, committed_result_refs=()):  # type: ignore[no-untyped-def]
        if not self.failed:
            self.failed = True
            raise OSError("simulated coordination acknowledgement loss")
        return super().mark_committed(
            claim,
            committed_result_refs=committed_result_refs,
        )


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _lifecycle(tmp_path) -> RunAttemptLifecycleService:
    service = RunAttemptLifecycleService(
        tmp_path / "lifecycle.jsonl",
        clock=_FixedClock(),
        id_generator=_SequentialIds(),
    )
    created = service.claim_new_run(
        RunIdentitySchema(
            run_id="run-1",
            task_id="task-1",
            tenant_id="tenant-a",
            canonical_request_sha256=REQUEST_HASH,
        )
    )
    service.transition_run(
        "run-1",
        created.lifecycle_revision,
        RunLifecycleStatus.RUNNING,
    )
    return service


def _runtime(
    tmp_path,
    lifecycle: RunAttemptLifecycleService,
    *,
    coordination_store=None,
) -> ArtifactRuntime:
    return ArtifactRuntime(
        tmp_path / "artifact-runtime.jsonl",
        lifecycle_service=lifecycle,
        coordination_store=coordination_store,
    )


def _logical(
    *,
    artifact_type: str = "paragraph",
    domain_scope_ref: str = "section:s1",
) -> ArtifactLogicalRefSchema:
    return ArtifactLogicalRefSchema(
        run_id="run-1",
        logical_artifact_id="paragraph:s1:p1",
        artifact_type=artifact_type,
        domain_scope_ref=domain_scope_ref,
    )


def _run_op(operation_id: str, kind: str) -> LogicalOperationRef:
    return LogicalOperationRef(
        logical_operation_id=operation_id,
        operation_kind=kind,
        run_id="run-1",
        operation_scope_ref="artifact:paragraph:s1:p1",
    )


def _attempt_op(
    operation_id: str,
    kind: str,
    *,
    attempt_scope_ref: str,
    attempt_id: str,
) -> LogicalOperationRef:
    return LogicalOperationRef(
        logical_operation_id=operation_id,
        operation_kind=kind,
        run_id="run-1",
        operation_scope_ref="artifact:paragraph:s1:p1",
        attempt_scope_ref=attempt_scope_ref,
        attempt_id=attempt_id,
    )


def _commit(
    runtime: ArtifactRuntime,
    logical: ArtifactLogicalRefSchema,
    *,
    operation_id: str,
    payload: object,
    fingerprint_label: str | None = None,
):
    operation = _run_op(operation_id, "ARTIFACT_VERSION_COMMIT")
    fingerprint = _fingerprint(fingerprint_label or operation_id)
    return runtime.commit_version(
        logical,
        payload,
        "paragraph_v1",
        operation,
        fingerprint,
        None,
    )


def _promote(
    runtime: ArtifactRuntime,
    logical: ArtifactLogicalRefSchema,
    version_ref,
    *,
    operation_id: str,
    expected_head_revision: int,
    expected_active_version_id: str | None,
):
    operation = _run_op(operation_id, "ARTIFACT_ACTIVE_PROMOTION")
    fingerprint = _fingerprint(operation_id)
    return runtime.promote_active(
        logical,
        version_ref,
        expected_head_revision,
        expected_active_version_id,
        operation,
        fingerprint,
        None,
    )


def test_fresh_version_commit_is_canonical_but_not_implicitly_current(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    operation = _run_op("version-1", "ARTIFACT_VERSION_COMMIT")
    fingerprint = _fingerprint("version-1")

    version = runtime.commit_version(
        logical,
        {"text": "v1"},
        "paragraph_v1",
        operation,
        fingerprint,
        None,
    )

    assert version.logical_ref == logical
    assert version.producer_attempt_scope_ref is None
    assert version.producer_attempt_id is None
    assert version.committed_by_operation_id == "version-1"
    assert runtime.read_version(version) == ({"text": "v1"}, version)

    head = runtime.resolve_active(logical)
    assert head is not None
    assert head.head_revision == 0
    assert head.active_version_ref is None

    resolution = runtime.resolve_version_commit(operation, fingerprint)
    assert resolution.resolution is CommittedResultResolutionStatus.COMMITTED_SAME
    assert len(resolution.committed_result_refs) == 1

    physical_ref, _ = runtime.artifact_store.get_bytes_with_reference(
        version.content_ref,
        task_id="task-1",
        run_id="run-1",
    )
    assert physical_ref.run_id == "run-1"
    assert physical_ref.attempt_id is None
    assert physical_ref.input_snapshot_version is None


def test_same_operation_same_fingerprint_converges_and_changed_material_fails_closed(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    operation = _run_op("version-1", "ARTIFACT_VERSION_COMMIT")
    fingerprint = _fingerprint("same-semantic-input")

    first = runtime.commit_version(
        logical,
        {"text": "stable"},
        "paragraph_v1",
        operation,
        fingerprint,
        None,
    )
    replay = runtime.commit_version(
        logical,
        {"text": "stable"},
        "paragraph_v1",
        operation,
        fingerprint,
        None,
    )
    assert replay == first

    with pytest.raises(
        ArtifactVersionCollision,
        match="different material content",
    ):
        runtime.commit_version(
            logical,
            {"text": "changed"},
            "paragraph_v1",
            operation,
            fingerprint,
            None,
        )


def test_same_logical_operation_different_fingerprint_fails_closed(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    operation = _run_op("version-1", "ARTIFACT_VERSION_COMMIT")

    runtime.commit_version(
        logical,
        {"text": "v1"},
        "paragraph_v1",
        operation,
        _fingerprint("a"),
        None,
    )

    with pytest.raises(ArtifactIdentityCollision):
        runtime.commit_version(
            logical,
            {"text": "v2"},
            "paragraph_v1",
            operation,
            _fingerprint("b"),
            None,
        )


def test_logical_identity_attributes_are_immutable_before_ledger_write(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    _commit(
        runtime,
        logical,
        operation_id="version-1",
        payload={"text": "v1"},
    )

    colliding = _logical(artifact_type="document")
    before = Path(runtime.ledger_path).read_text(encoding="utf-8")
    with pytest.raises(ArtifactIdentityCollision):
        _commit(
            runtime,
            colliding,
            operation_id="version-2",
            payload={"text": "v2"},
        )
    after = Path(runtime.ledger_path).read_text(encoding="utf-8")
    assert after == before


def test_version_sequence_and_active_head_revisions_are_monotonic(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()

    v1 = _commit(
        runtime,
        logical,
        operation_id="version-1",
        payload={"text": "v1"},
    )
    v2 = _commit(
        runtime,
        logical,
        operation_id="version-2",
        payload={"text": "v2"},
    )
    assert v1.artifact_version_id != v2.artifact_version_id

    p1 = _promote(
        runtime,
        logical,
        v1,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )
    p2 = _promote(
        runtime,
        logical,
        v2,
        operation_id="promotion-2",
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
    )
    assert (p1.resulting_head_revision, p2.resulting_head_revision) == (1, 2)

    events = [
        json.loads(line)
        for line in Path(runtime.ledger_path).read_text(encoding="utf-8").splitlines()
    ]
    ordinals = [
        event["version_ordinal"]
        for event in events
        if event["event_type"] == "version_committed"
    ]
    assert ordinals == [1, 2]


def test_promotion_supersession_and_old_promotion_replay_do_not_roll_back_head(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    v1 = _commit(runtime, logical, operation_id="version-1", payload={"text": "v1"})
    v2 = _commit(runtime, logical, operation_id="version-2", payload={"text": "v2"})

    first_receipt = _promote(
        runtime,
        logical,
        v1,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )
    _promote(
        runtime,
        logical,
        v2,
        operation_id="promotion-2",
        expected_head_revision=1,
        expected_active_version_id=v1.artifact_version_id,
    )

    assert runtime.resolve_active(logical).active_version_ref == v2  # type: ignore[union-attr]
    assert runtime.read_version(v1)[0] == {"text": "v1"}

    replay = _promote(
        runtime,
        logical,
        v1,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )
    assert replay == first_receipt
    head = runtime.resolve_active(logical)
    assert head is not None
    assert head.head_revision == 2
    assert head.active_version_ref == v2


def test_stale_expected_head_and_active_version_cannot_overwrite_currentness(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    v1 = _commit(runtime, logical, operation_id="version-1", payload={"text": "v1"})
    v2 = _commit(runtime, logical, operation_id="version-2", payload={"text": "v2"})
    _promote(
        runtime,
        logical,
        v1,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )

    with pytest.raises(ArtifactCurrentnessError, match="EXPECTED_HEAD_REVISION_STALE"):
        _promote(
            runtime,
            logical,
            v2,
            operation_id="promotion-stale-revision",
            expected_head_revision=0,
            expected_active_version_id=None,
        )

    with pytest.raises(ArtifactCurrentnessError, match="EXPECTED_ACTIVE_VERSION_STALE"):
        _promote(
            runtime,
            logical,
            v2,
            operation_id="promotion-stale-active",
            expected_head_revision=1,
            expected_active_version_id="not-v1",
        )

    head = runtime.resolve_active(logical)
    assert head is not None
    assert head.head_revision == 1
    assert head.active_version_ref == v1


def test_attempt_scoped_commit_and_stale_attempt_promotion_are_lifecycle_guarded(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()

    first_attempt = lifecycle.open_attempt(
        "run-1",
        "section:s1",
        "snapshot-v1",
    )
    first_attempt = lifecycle.activate_attempt(
        first_attempt.identity.attempt_id,
        first_attempt.lifecycle_revision,
    )
    first_lineage = lifecycle.resolve_active_lineage("run-1", "section:s1")

    version_op = _attempt_op(
        "attempt-version-1",
        "ARTIFACT_VERSION_COMMIT",
        attempt_scope_ref="section:s1",
        attempt_id=first_attempt.identity.attempt_id,
    )
    version = runtime.commit_version(
        logical,
        {"text": "attempt-v1"},
        "paragraph_v1",
        version_op,
        _fingerprint("attempt-version-1"),
        first_lineage,
    )
    assert version.producer_attempt_id == first_attempt.identity.attempt_id
    assert version.producer_attempt_scope_ref == "section:s1"

    lifecycle.open_attempt(
        "run-1",
        "section:s1",
        "snapshot-v2",
    )

    stale_promotion_op = _attempt_op(
        "attempt-promotion-stale",
        "ARTIFACT_ACTIVE_PROMOTION",
        attempt_scope_ref="section:s1",
        attempt_id=first_attempt.identity.attempt_id,
    )
    with pytest.raises(LifecycleCurrentnessError):
        runtime.promote_active(
            logical,
            version,
            0,
            None,
            stale_promotion_op,
            _fingerprint("attempt-promotion-stale"),
            first_lineage,
        )

    head = runtime.resolve_active(logical)
    assert head is not None
    assert head.head_revision == 0
    assert head.active_version_ref is None


def test_run_level_operation_rejects_fabricated_attempt_guard(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    attempt = lifecycle.open_attempt("run-1", "section:s1", "snapshot-v1")
    lifecycle.activate_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision)
    lineage = lifecycle.resolve_active_lineage("run-1", "section:s1")

    with pytest.raises(ArtifactIdentityCollision, match="must not manufacture Attempt"):
        runtime.commit_version(
            _logical(),
            {"text": "v1"},
            "paragraph_v1",
            _run_op("run-level-op", "ARTIFACT_VERSION_COMMIT"),
            _fingerprint("run-level-op"),
            lineage,
        )


def test_durable_reopen_resolves_version_head_and_historical_content(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    first = _runtime(tmp_path, lifecycle)
    logical = _logical()
    version = _commit(
        first,
        logical,
        operation_id="version-1",
        payload={"text": "persisted"},
    )
    receipt = _promote(
        first,
        logical,
        version,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )

    reopened = _runtime(tmp_path, lifecycle)
    assert reopened.read_version(version) == ({"text": "persisted"}, version)
    assert reopened.resolve_active(logical).active_version_ref == version  # type: ignore[union-attr]
    resolved = reopened.resolve_promotion(
        _run_op("promotion-1", "ARTIFACT_ACTIVE_PROMOTION"),
        _fingerprint("promotion-1"),
    )
    assert resolved.resolution is CommittedResultResolutionStatus.COMMITTED_SAME

    replay = _promote(
        reopened,
        logical,
        version,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )
    assert replay == receipt


def test_corrupted_committed_ledger_fails_closed(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    _commit(
        runtime,
        logical,
        operation_id="version-1",
        payload={"text": "v1"},
    )

    path = Path(runtime.ledger_path)
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[0]["version_ordinal"] = 99
    path.write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n",
        encoding="utf-8",
    )

    resolution = runtime.resolve_version_commit(
        _run_op("version-1", "ARTIFACT_VERSION_COMMIT"),
        _fingerprint("version-1"),
    )
    assert resolution.resolution is CommittedResultResolutionStatus.INTEGRITY_INVALID
    with pytest.raises(ArtifactRuntimeIntegrityError):
        runtime.resolve_active(logical)


def test_missing_or_corrupt_active_content_fails_closed(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    version = _commit(
        runtime,
        logical,
        operation_id="version-1",
        payload={"text": "v1"},
    )
    _promote(
        runtime,
        logical,
        version,
        operation_id="promotion-1",
        expected_head_revision=0,
        expected_active_version_id=None,
    )

    target = runtime.artifact_store._path_for_ref(version.content_ref)
    envelope = json.loads(target.read_text(encoding="utf-8"))
    envelope["content_base64"] = "bm90LXRoZS1vcmlnaW5hbC1jb250ZW50"
    target.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ArtifactRuntimeIntegrityError):
        runtime.resolve_active(logical)


def test_ambiguous_append_failure_re_resolves_canonical_source_before_claim_release(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    coordination = InMemorySideEffectCommitStore()
    runtime = _runtime(
        tmp_path,
        lifecycle,
        coordination_store=coordination,
    )
    runtime.log = _AppendThenFailOnce(runtime.log)
    logical = _logical()
    operation = _run_op("version-ambiguous-append", "ARTIFACT_VERSION_COMMIT")
    fingerprint = _fingerprint("version-ambiguous-append")

    with pytest.raises(OSError, match="append response loss"):
        runtime.commit_version(
            logical,
            {"text": "durable-before-error"},
            "paragraph_v1",
            operation,
            fingerprint,
            None,
        )

    canonical = runtime.resolve_version_commit(operation, fingerprint)
    assert canonical.resolution is CommittedResultResolutionStatus.COMMITTED_SAME

    coordination_state = coordination.resolve(
        operation.logical_operation_id,
        semantic_fingerprint=fingerprint,
    )
    assert coordination_state.status is SideEffectCoordinationStatus.IN_PROGRESS


def test_crash_after_canonical_commit_before_coordination_ack_resolves_source_first(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    failing_coordination = _FailMarkCommittedOnce()
    first = _runtime(
        tmp_path,
        lifecycle,
        coordination_store=failing_coordination,
    )
    logical = _logical()
    operation = _run_op("version-crash-window", "ARTIFACT_VERSION_COMMIT")
    fingerprint = _fingerprint("version-crash-window")

    with pytest.raises(OSError, match="acknowledgement loss"):
        first.commit_version(
            logical,
            {"text": "committed-before-ack"},
            "paragraph_v1",
            operation,
            fingerprint,
            None,
        )

    canonical = first.resolve_version_commit(operation, fingerprint)
    assert canonical.resolution is CommittedResultResolutionStatus.COMMITTED_SAME

    recovered = _runtime(
        tmp_path,
        lifecycle,
        coordination_store=InMemorySideEffectCommitStore(),
    )
    version = recovered.commit_version(
        logical,
        {"text": "committed-before-ack"},
        "paragraph_v1",
        operation,
        fingerprint,
        None,
    )
    assert recovered.read_version(version)[0] == {"text": "committed-before-ack"}


def test_fake_checkpoint_state_and_trace_cannot_override_canonical_head(tmp_path) -> None:
    lifecycle = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, lifecycle)
    logical = _logical()
    v1 = _commit(runtime, logical, operation_id="version-1", payload={"text": "v1"})
    v2 = _commit(runtime, logical, operation_id="version-2", payload={"text": "v2"})
    _promote(
        runtime,
        logical,
        v2,
        operation_id="promotion-2",
        expected_head_revision=0,
        expected_active_version_id=None,
    )

    (tmp_path / "checkpoint.json").write_text(
        json.dumps({"active_version_ref": v1.model_dump(mode="json")}),
        encoding="utf-8",
    )
    (tmp_path / "workflow-state.json").write_text(
        json.dumps({"current_artifact": v1.model_dump(mode="json")}),
        encoding="utf-8",
    )
    (tmp_path / "trace.json").write_text(
        json.dumps({"artifact_currentness": v1.model_dump(mode="json")}),
        encoding="utf-8",
    )

    head = runtime.resolve_active(logical)
    assert head is not None
    assert head.active_version_ref == v2


def test_concurrent_same_operation_has_one_canonical_version_and_converges(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    first = _runtime(tmp_path, lifecycle)
    second = _runtime(tmp_path, lifecycle)
    logical = _logical()
    operation = _run_op("version-concurrent", "ARTIFACT_VERSION_COMMIT")
    fingerprint = _fingerprint("version-concurrent")
    barrier = threading.Barrier(2)
    refs = []
    errors = []
    lock = threading.Lock()

    def commit(runtime: ArtifactRuntime) -> None:
        try:
            barrier.wait()
            ref = runtime.commit_version(
                logical,
                {"text": "same"},
                "paragraph_v1",
                operation,
                fingerprint,
                None,
            )
            with lock:
                refs.append(ref)
        except Exception as exc:  # pragma: no cover - assertion reports exact failure
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=commit, args=(first,)),
        threading.Thread(target=commit, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(refs) == 2
    assert refs[0] == refs[1]

    events = [
        json.loads(line)
        for line in Path(first.ledger_path).read_text(encoding="utf-8").splitlines()
    ]
    version_events = [
        event for event in events if event["event_type"] == "version_committed"
    ]
    assert len(version_events) == 1


def test_concurrent_distinct_promotions_from_same_head_allow_one_cas_winner(
    tmp_path,
) -> None:
    lifecycle = _lifecycle(tmp_path)
    first = _runtime(tmp_path, lifecycle)
    second = _runtime(tmp_path, lifecycle)
    logical = _logical()
    v1 = _commit(first, logical, operation_id="version-1", payload={"text": "v1"})
    v2 = _commit(first, logical, operation_id="version-2", payload={"text": "v2"})
    barrier = threading.Barrier(2)
    receipts = []
    errors = []
    lock = threading.Lock()

    def promote(
        runtime: ArtifactRuntime,
        version_ref,
        operation_id: str,
    ) -> None:
        try:
            barrier.wait()
            receipt = _promote(
                runtime,
                logical,
                version_ref,
                operation_id=operation_id,
                expected_head_revision=0,
                expected_active_version_id=None,
            )
            with lock:
                receipts.append(receipt)
        except Exception as exc:  # pragma: no cover - exact classes asserted below
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(
            target=promote,
            args=(first, v1, "promotion-race-v1"),
        ),
        threading.Thread(
            target=promote,
            args=(second, v2, "promotion-race-v2"),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(receipts) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ArtifactCurrentnessError)
    assert "EXPECTED_HEAD_REVISION_STALE" in str(errors[0])

    head = first.resolve_active(logical)
    assert head is not None
    assert head.head_revision == 1
    assert head.active_version_ref is not None
    assert head.active_version_ref.artifact_version_id in {
        v1.artifact_version_id,
        v2.artifact_version_id,
    }


def test_artifact_runtime_has_static_negative_authority_fence() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "agent_rag_v1"
        / "artifact_runtime.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")

    forbidden_fragments = {
        ".state",
        "checkpoint",
        "trace",
        "memory",
        "section_execution_graph",
    }
    assert not {
        module
        for module in imported_modules
        if any(fragment in module.lower() for fragment in forbidden_fragments)
    }

    public_methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "set_active" not in public_methods
    assert "restore_from_checkpoint" not in public_methods
    assert "project_state_as_truth" not in public_methods
