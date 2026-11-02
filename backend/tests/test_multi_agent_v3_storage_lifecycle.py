from __future__ import annotations

from dataclasses import replace
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import core.config as config
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAggregateResult,
    RunIdentitySchema,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.agent_rag_v1.disk_guard import (
    DiskBudgetGuard,
    DiskMeasurement,
    DiskPressureState,
    GIB,
    LocalStorageAdmissionError,
)
from apps.enterprise_document.services.agent_rag_v1.storage_lifecycle import (
    CanonicalStorageSubtype,
    CompositeStorageCatalog,
    FileArtifactStorageAdapter,
    GCDeletionState,
    GCRevalidationState,
    RetentionClass,
    SQLiteCheckpointStorageAdapter,
    StorageIntegrityError,
    StorageLifecycleService,
    StorageObjectClass,
    StorageObjectRecord,
    StorageReference,
    StorageReferenceKind,
)
from apps.enterprise_document.services.scheme_writer.capture_service import (
    SchemeCaptureService,
)
from apps.enterprise_document.workflows.scheme_generation import (
    build_scheme_generation_workflow,
)
from bootstrap.runtime_options import RuntimeOptions
from bootstrap.supervisor_factory import SupervisorFactory
from task.task_manager import JsonlTaskManager


class _MutableCatalog:
    def __init__(
        self,
        records,
        *,
        terminal=True,
        roots=(),
        delete_fail_refs=(),
        delete_owned_children=True,
    ):
        self.records = {item.object_ref: item for item in records}
        self.terminal = terminal
        self.roots = tuple(roots)
        self.deleted = []
        self.delete_fail_refs = set(delete_fail_refs)
        self.delete_owned_children = bool(delete_owned_children)

    def scope_is_terminal_or_gc_closed(self, scope_ref: str) -> bool:
        del scope_ref
        return self.terminal

    def enumerate_objects(self, scope_ref: str):
        return tuple(
            item for item in self.records.values() if item.scope_ref == scope_ref
        )

    def resolve_object(self, object_ref: str):
        return self.records.get(object_ref)

    def resolve_protected_roots(self, scope_ref: str):
        del scope_ref
        return self.roots

    def can_delete(self, record: StorageObjectRecord) -> bool:
        return bool(record.managed_by_local_sif13)

    def delete_object_group(self, record: StorageObjectRecord) -> int:
        if record.object_ref in self.delete_fail_refs:
            raise OSError("injected delete failure")
        self.deleted.append(record.object_ref)
        self.records.pop(record.object_ref, None)
        freed = record.estimated_bytes
        if self.delete_owned_children:
            for edge in record.references:
                if edge.kind is not StorageReferenceKind.OWNED_CHILD:
                    continue
                child = self.records.pop(edge.target_ref, None)
                if child is not None:
                    freed += child.estimated_bytes
        return freed


def _record(
    ref: str,
    *,
    storage_class=StorageObjectClass.AUDIT,
    retention=RetentionClass.R3_AUDIT_DEBUG_WINDOW,
    scope="run:r1",
    expires=1.0,
    current=False,
    recovery=False,
    release=False,
    pinned=False,
    held=False,
    refs=(),
    managed=True,
    subtype=None,
    size=10,
):
    if storage_class is StorageObjectClass.CANONICAL and subtype is None:
        subtype = CanonicalStorageSubtype.DOMAIN
    family_by_class = {
        StorageObjectClass.PROJECTION: "generation_report",
        StorageObjectClass.RECOVERY: "langgraph_checkpoint",
        StorageObjectClass.AUDIT: "run_trace_event",
        StorageObjectClass.EPHEMERAL: "context_package",
    }
    if storage_class is StorageObjectClass.CANONICAL:
        family = {
            CanonicalStorageSubtype.DOMAIN: "artifact_version",
            CanonicalStorageSubtype.CONTROL: "gate_control",
            CanonicalStorageSubtype.COORDINATION: "sif02_coordination",
        }[subtype]
    else:
        family = family_by_class[storage_class]
    return StorageObjectRecord(
        object_ref=ref,
        object_family=family,
        storage_class=storage_class,
        canonical_subtype=subtype,
        retention_class=retention,
        scope_ref=scope,
        retention_expires_at_epoch=expires,
        managed_by_local_sif13=managed,
        current_protected=current,
        recovery_protected=recovery,
        release_protected=release,
        explicitly_pinned=pinned,
        investigation_or_reconciliation_held=held,
        references=tuple(refs),
        estimated_bytes=size,
        physical_identity=f"hash:{ref}",
    )


def test_w14_all_five_storage_classes_are_classifiable() -> None:
    records = (
        _record(
            "canonical",
            storage_class=StorageObjectClass.CANONICAL,
            retention=RetentionClass.R1_LONG_HISTORY,
            subtype=CanonicalStorageSubtype.DOMAIN,
        ),
        _record("projection", storage_class=StorageObjectClass.PROJECTION),
        _record(
            "recovery",
            storage_class=StorageObjectClass.RECOVERY,
            retention=RetentionClass.R2_RECOVERY_WINDOW,
        ),
        _record("audit", storage_class=StorageObjectClass.AUDIT),
        _record(
            "ephemeral",
            storage_class=StorageObjectClass.EPHEMERAL,
            retention=RetentionClass.R4_DISPOSABLE,
            expires=None,
        ),
    )
    service = StorageLifecycleService(_MutableCatalog(records))
    assert {
        service.classify_object(item.object_ref)[0] for item in records
    } == set(StorageObjectClass)


def test_w14_gc_rejects_unclassified_duplicate_or_cross_scope_inventory() -> None:
    unclassified = _record("bad-family")
    unclassified = replace(unclassified, object_family="not-frozen")
    with pytest.raises(StorageIntegrityError, match="unclassified managed storage family"):
        StorageLifecycleService(_MutableCatalog((unclassified,))).plan_gc("run:r1")

    cross_scope = _record("cross-scope", scope="run:r2")

    class _CrossScopeCatalog(_MutableCatalog):
        def enumerate_objects(self, scope_ref: str):
            del scope_ref
            return (cross_scope,)

    with pytest.raises(StorageIntegrityError, match="outside requested scope"):
        StorageLifecycleService(_CrossScopeCatalog((cross_scope,))).plan_gc("run:r1")

    first = _record("duplicate")
    second = replace(first, estimated_bytes=99)

    class _DuplicateCatalog(_MutableCatalog):
        def enumerate_objects(self, scope_ref: str):
            del scope_ref
            return (first, second)

    with pytest.raises(StorageIntegrityError, match="duplicate object refs"):
        StorageLifecycleService(_DuplicateCatalog((first,))).plan_gc("run:r1")


@pytest.mark.parametrize(
    ("retention", "expires", "expected_eligible"),
    [
        (RetentionClass.R0_PROTECTED, None, False),
        (RetentionClass.R1_LONG_HISTORY, 0.0, True),
        (RetentionClass.R2_RECOVERY_WINDOW, 0.0, True),
        (RetentionClass.R3_AUDIT_DEBUG_WINDOW, 0.0, True),
        (RetentionClass.R4_DISPOSABLE, None, True),
    ],
)
def test_w14_retention_class_matrix_is_exact(
    retention: RetentionClass,
    expires: float | None,
    expected_eligible: bool,
) -> None:
    storage_class = (
        StorageObjectClass.CANONICAL
        if retention in {RetentionClass.R0_PROTECTED, RetentionClass.R1_LONG_HISTORY}
        else StorageObjectClass.RECOVERY
        if retention is RetentionClass.R2_RECOVERY_WINDOW
        else StorageObjectClass.AUDIT
        if retention is RetentionClass.R3_AUDIT_DEBUG_WINDOW
        else StorageObjectClass.EPHEMERAL
    )
    record = _record(
        f"retention-{retention.value}",
        storage_class=storage_class,
        retention=retention,
        expires=expires,
    )
    service = StorageLifecycleService(
        _MutableCatalog((record,)),
        clock=lambda: 100.0,
    )
    candidate = service.plan_gc("run:r1").candidates[0]
    assert candidate.eligible is expected_eligible
    if retention is RetentionClass.R0_PROTECTED:
        assert "STRONGLY_REACHABLE" in candidate.eligibility_reasons
        assert "RETENTION_HOLD_ACTIVE" in candidate.eligibility_reasons


def test_w14_unreachable_or_expired_alone_never_authorizes_delete() -> None:
    expired_active_scope = _record("expired-active", expires=0.0)
    active_catalog = _MutableCatalog((expired_active_scope,), terminal=False)
    active_plan = StorageLifecycleService(active_catalog, clock=lambda: 100.0).plan_gc(
        "run:r1"
    )
    assert active_plan.candidates[0].eligible is False
    assert "SCOPE_NOT_TERMINAL_OR_GC_CLOSED" in (
        active_plan.candidates[0].eligibility_reasons
    )

    future_retention = _record("unreachable-not-expired", expires=1000.0)
    terminal_plan = StorageLifecycleService(
        _MutableCatalog((future_retention,), terminal=True),
        clock=lambda: 100.0,
    ).plan_gc("run:r1")
    assert terminal_plan.candidates[0].eligible is False
    assert "RETENTION_HOLD_ACTIVE" in terminal_plan.candidates[0].eligibility_reasons


def test_w14_reachability_traverses_strong_and_owned_child_but_not_weak_or_external() -> None:
    old_audit = _record("old-audit", expires=0.0)
    strong_child = _record("strong-child", expires=0.0)
    owned_child = _record("owned-child", expires=0.0)
    root = _record(
        "root",
        storage_class=StorageObjectClass.CANONICAL,
        retention=RetentionClass.R0_PROTECTED,
        subtype=CanonicalStorageSubtype.CONTROL,
        expires=None,
        refs=(
            StorageReference("strong-child", StorageReferenceKind.STRONG_PROTECT),
            StorageReference("owned-child", StorageReferenceKind.OWNED_CHILD),
            StorageReference("old-audit", StorageReferenceKind.WEAK_PROVENANCE),
            StorageReference("external://rag/source", StorageReferenceKind.EXTERNAL_REF),
        ),
    )
    service = StorageLifecycleService(
        _MutableCatalog((root, strong_child, owned_child, old_audit), roots=("root",)),
        clock=lambda: 100.0,
    )
    plan = service.plan_gc("run:r1")
    assert set(plan.graph.strongly_reachable_refs) == {
        "root",
        "strong-child",
        "owned-child",
    }
    candidates = {item.object_ref: item for item in plan.candidates}
    assert candidates["strong-child"].eligible is False
    assert candidates["owned-child"].eligible is False
    assert candidates["old-audit"].eligible is True
    assert ("root", "old-audit") in plan.graph.weak_edges
    assert ("root", "external://rag/source") in plan.graph.external_edges


def test_w14_owned_child_is_deleted_only_through_parent_semantic_group() -> None:
    child = _record("child", expires=0.0, size=7)
    parent = _record(
        "parent",
        expires=0.0,
        size=11,
        refs=(StorageReference("child", StorageReferenceKind.OWNED_CHILD),),
    )
    catalog = _MutableCatalog((parent, child))
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)
    plan = service.plan_gc("run:r1")
    by_ref = {item.object_ref: item for item in plan.candidates}
    assert by_ref["parent"].eligible is True
    assert by_ref["parent"].planned_owned_children == ("child",)
    assert by_ref["child"].eligible is False
    assert "OWNED_CHILD_DELETE_THROUGH_PARENT" in by_ref["child"].eligibility_reasons
    deleted = service.delete_gc_candidate(by_ref["parent"])
    assert deleted.state is GCDeletionState.DELETED
    assert deleted.bytes_freed == 18
    assert "parent" not in catalog.records
    assert "child" not in catalog.records


def test_w14_post_delete_exact_check_detects_incomplete_owned_child_group() -> None:
    child = _record("child", expires=0.0, size=7)
    parent = _record(
        "parent",
        expires=0.0,
        size=11,
        refs=(StorageReference("child", StorageReferenceKind.OWNED_CHILD),),
    )
    catalog = _MutableCatalog(
        (parent, child),
        delete_owned_children=False,
    )
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)
    candidate = next(
        item for item in service.plan_gc("run:r1").candidates if item.object_ref == "parent"
    )
    result = service.delete_gc_candidate(candidate)
    assert result.state is GCDeletionState.FAILED
    assert result.reasons == ("POST_DELETE_OWNED_CHILD_STILL_PRESENT:child",)
    assert "parent" not in catalog.records
    assert "child" in catalog.records


def test_w14_unresolved_strong_reference_blocks_affected_scope_fail_closed() -> None:
    root = _record(
        "root",
        storage_class=StorageObjectClass.CANONICAL,
        retention=RetentionClass.R0_PROTECTED,
        subtype=CanonicalStorageSubtype.CONTROL,
        expires=None,
        refs=(
            StorageReference("missing", StorageReferenceKind.STRONG_PROTECT),
        ),
    )
    victim = _record("otherwise-eligible", expires=0.0)
    service = StorageLifecycleService(
        _MutableCatalog((root, victim), roots=("root",)),
        clock=lambda: 100.0,
    )
    plan = service.plan_gc("run:r1")
    assert "UNRESOLVED_STRONG_REFERENCE" in plan.blocked_reasons
    assert all(item.eligible is False for item in plan.candidates)
    audit = service.run_terminal_scope_gc("run:r1", dry_run=False)
    assert audit.deleted_count == 0
    assert audit.blocked_reasons == ("UNRESOLVED_STRONG_REFERENCE",)


def test_w14_stale_plan_revalidation_preserves_newly_current_object() -> None:
    original = _record("candidate", expires=0.0)
    catalog = _MutableCatalog((original,))
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)
    candidate = next(item for item in service.plan_gc("run:r1").candidates if item.eligible)
    catalog.records["candidate"] = replace(original, current_protected=True)
    revalidated = service.revalidate_gc_candidate(candidate)
    assert revalidated.state is GCRevalidationState.REVOKED
    assert "STRONGLY_REACHABLE" in revalidated.reasons
    deleted = service.delete_gc_candidate(candidate)
    assert deleted.state is GCDeletionState.SKIPPED
    assert "candidate" in catalog.records


def test_w14_release_protection_added_after_plan_revokes_delete() -> None:
    original = _record("release-candidate", expires=0.0)
    catalog = _MutableCatalog((original,))
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)
    candidate = next(item for item in service.plan_gc("run:r1").candidates if item.eligible)
    catalog.records["release-candidate"] = replace(
        original,
        release_protected=True,
    )
    revalidated = service.revalidate_gc_candidate(candidate)
    assert revalidated.state is GCRevalidationState.REVOKED
    assert "RELEASE_PROTECTED" in revalidated.reasons
    deleted = service.delete_gc_candidate(candidate)
    assert deleted.state is GCDeletionState.SKIPPED
    assert "release-candidate" in catalog.records


def test_w14_partial_delete_retry_is_idempotent() -> None:
    first = _record("first", expires=0.0, size=11)
    second = _record("second", expires=0.0, size=12)
    catalog = _MutableCatalog((first, second), delete_fail_refs={"second"})
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)
    audit = service.run_terminal_scope_gc("run:r1", dry_run=False)
    assert audit.deleted_count == 1
    assert audit.failed_count == 1
    assert "first" not in catalog.records
    assert "second" in catalog.records
    second_audit = service.run_terminal_scope_gc("run:r1", dry_run=False)
    assert second_audit.deleted_count == 0
    assert second_audit.failed_count == 1


def _seed_official_checkpoint_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            );
            CREATE TABLE writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT,
                value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            );
            """
        )
        rows = (
            ("r1", "top", "cp-top-1", None, "json", b"top-1", b"meta"),
            ("r1", "top", "cp-top-2", "cp-top-1", "json", b"top-2", b"meta"),
            ("r1", "section:a", "cp-a-1", None, "json", b"a-1", b"meta"),
        )
        connection.executemany(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.executemany(
            "INSERT INTO writes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ("r1", "top", "cp-top-1", "task-a", 0, "x", "json", b"w1"),
                ("r1", "section:a", "cp-a-1", "task-b", 0, "x", "json", b"wa"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_w14_checkpoint_active_scope_is_recovery_protected_and_parent_history_is_weak(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoint.sqlite3"
    _seed_official_checkpoint_database(database)
    adapter = SQLiteCheckpointStorageAdapter(
        database,
        scope_terminal_resolver=lambda _scope: False,
    )
    service = StorageLifecycleService(
        CompositeStorageCatalog(
            scope_terminal_resolver=lambda _scope: False,
            adapters=(adapter,),
        ),
        clock=lambda: 100.0,
    )
    plan = service.plan_gc("run:r1")
    checkpoints = {
        item.object_ref: item
        for item in adapter.enumerate_objects("run:r1")
        if item.object_family == "langgraph_checkpoint"
    }
    assert len(checkpoints) == 3
    assert all(item.recovery_protected for item in checkpoints.values())
    assert plan.destructive_allowed is False
    assert all(item.eligible is False for item in plan.candidates)
    assert any(
        edge.kind is StorageReferenceKind.WEAK_PROVENANCE
        for record in checkpoints.values()
        for edge in record.references
    )
    assert any(
        edge.kind is StorageReferenceKind.OWNED_CHILD
        for record in checkpoints.values()
        for edge in record.references
    )


def test_w14_terminal_checkpoint_delete_is_exact_namespace_group_not_whole_thread(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpoint.sqlite3"
    _seed_official_checkpoint_database(database)
    adapter = SQLiteCheckpointStorageAdapter(
        database,
        scope_terminal_resolver=lambda _scope: True,
    )
    service = StorageLifecycleService(
        CompositeStorageCatalog(
            scope_terminal_resolver=lambda _scope: True,
            adapters=(adapter,),
        ),
        clock=lambda: 100.0,
    )
    records = adapter.enumerate_objects("run:r1")
    target = next(
        item
        for item in records
        if item.object_family == "langgraph_checkpoint"
        and item.metadata["checkpoint_ns"] == "section:a"
    )
    plan = service.plan_gc("run:r1")
    candidate = next(item for item in plan.candidates if item.object_ref == target.object_ref)
    assert candidate.eligible is True
    assert candidate.planned_owned_children
    result = service.delete_gc_candidate(candidate)
    assert result.state is GCDeletionState.DELETED

    connection = sqlite3.connect(database)
    try:
        remaining = connection.execute(
            "SELECT checkpoint_ns, checkpoint_id FROM checkpoints "
            "WHERE thread_id='r1' ORDER BY checkpoint_ns, checkpoint_id"
        ).fetchall()
        writes = connection.execute(
            "SELECT checkpoint_ns, checkpoint_id FROM writes "
            "WHERE thread_id='r1' ORDER BY checkpoint_ns, checkpoint_id"
        ).fetchall()
    finally:
        connection.close()
    assert remaining == [("top", "cp-top-1"), ("top", "cp-top-2")]
    assert writes == [("top", "cp-top-1")]


def test_w14_file_artifact_store_delete_occurs_only_after_semantic_revalidation(
    tmp_path: Path,
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    ref = store.put_bytes(
        b"expired-audit",
        artifact_type="raw_model_capture",
        schema_version="v1",
        created_by="test",
        task_id="task",
        run_id="r1",
        artifact_ref="artifact://audit/expired",
    )
    adapter = FileArtifactStorageAdapter(
        store,
        object_family="raw_model_capture",
        storage_class=StorageObjectClass.AUDIT,
        retention_class_resolver=lambda _ref: RetentionClass.R3_AUDIT_DEBUG_WINDOW,
        scope_ref_resolver=lambda item: f"run:{item.run_id}",
        retention_expires_resolver=lambda _ref: 0.0,
        delete_supported=True,
    )
    catalog = CompositeStorageCatalog(
        scope_terminal_resolver=lambda scope: scope == "run:r1",
        adapters=(adapter,),
    )
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)
    plan = service.plan_gc("run:r1")
    candidate = next(item for item in plan.candidates if item.object_ref == ref.artifact_ref)
    assert candidate.eligible is True
    result = service.delete_gc_candidate(candidate)
    assert result.state is GCDeletionState.DELETED
    assert result.bytes_freed > 0
    with pytest.raises(FileNotFoundError):
        store.get_bytes(ref.artifact_ref, task_id="task", run_id="r1")
    replay = service.delete_gc_candidate(candidate)
    assert replay.state is GCDeletionState.ALREADY_ABSENT


@pytest.mark.parametrize(
    ("ratio", "free_gib", "expected"),
    [
        (0.20, 30.0, DiskPressureState.NORMAL),
        (0.149, 19.0, DiskPressureState.SOFT_PRESSURE),
        (0.099, 9.0, DiskPressureState.HARD_PRESSURE),
        (0.049, 4.0, DiskPressureState.CRITICAL_PRESSURE),
        (0.049, 6.0, DiskPressureState.HARD_PRESSURE),
        (0.20, 4.0, DiskPressureState.NORMAL),
    ],
)
def test_w14_disk_pressure_thresholds_use_frozen_and_semantics(
    ratio: float,
    free_gib: float,
    expected: DiskPressureState,
) -> None:
    guard = DiskBudgetGuard()
    measurement = DiskMeasurement(
        filesystem_root=Path("E:/"),
        total_bytes=100 * GIB,
        used_bytes=int((100 - free_gib) * GIB),
        free_bytes=int(free_gib * GIB),
        free_ratio=ratio,
        filesystem_identity="device:test",
    )
    assert guard.classify_pressure(measurement) is expected


def test_w14_disk_policy_suppresses_only_optional_capture_and_never_provider_fallback() -> None:
    guard = DiskBudgetGuard()
    hard = guard.apply_pressure_policy(
        DiskPressureState.HARD_PRESSURE,
        "run:r1",
        filesystem_root=Path("E:/"),
    )
    assert hard.allow_new_durable_run is True
    assert hard.suppress_optional_capture is True
    assert hard.required_canonical_writes_allowed is True
    assert hard.required_recovery_writes_allowed is True
    assert hard.required_release_writes_allowed is True
    assert hard.failure_origin == "local_storage"
    assert hard.provider_fallback_allowed is False
    assert hard.gate_route_changed is False
    assert hard.sif12_budget_changed is False

    critical = guard.apply_pressure_policy(
        DiskPressureState.CRITICAL_PRESSURE,
        "run:r2",
        filesystem_root=Path("E:/"),
    )
    assert critical.allow_new_durable_run is False
    assert critical.suppress_optional_capture is True
    assert critical.provider_fallback_allowed is False


class _CaptureRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


def test_w14_hard_pressure_suppresses_optional_scheme_capture() -> None:
    recorder = _CaptureRecorder()
    service = SchemeCaptureService(
        data_capture_recorder=recorder,
        agent_name="SchemeWriterAgent",
    )
    guard = DiskBudgetGuard()
    hard_policy = guard.apply_pressure_policy(
        DiskPressureState.HARD_PRESSURE,
        "run:r1",
        filesystem_root=Path("E:/"),
    )
    service.attach_storage_pressure_policy(lambda _scope: hard_policy)
    captured = service.capture(
        SimpleNamespace(
            task_id="task_w14",
            run_id="r1",
            user_input="test",
            generated_outputs={},
        ),
        SimpleNamespace(),
        SimpleNamespace(),
        rag_output={},
    )
    assert captured is False
    assert recorder.calls == []
    assert service.last_storage_pressure_policy == hard_policy
    assert hard_policy.observability_status == "OBSERVABILITY_DEGRADED"
    assert hard_policy.provider_fallback_allowed is False


def _build_w14_supervisor(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[2]
    settings = replace(
        config.get_settings(),
        data_root=tmp_path / "different-data-root",
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=False,
        data_capture_enabled=False,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=2,
        gate_no_progress_rounds_limit=1,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    runs_dir = tmp_path / "effective-runs"
    captures_dir = tmp_path / "captures"
    with patch.dict(os.environ, environment):
        options = RuntimeOptions.resolve(
            settings,
            project_root,
            enable_agent_self_rag=False,
        )
        supervisor = SupervisorFactory().build(
            workflow=build_scheme_generation_workflow(
                created_at="2026-09-06T00:00:00+08:00"
            ),
            runs_dir=runs_dir,
            captures_dir=captures_dir,
            task_manager=JsonlTaskManager(tmp_path / "tasks"),
            settings=settings,
            options=options,
            allow_legacy_unscoped_rag=True,
        )
    return supervisor, settings, runs_dir.resolve(), captures_dir.resolve()


def test_w14_factory_critical_pressure_rejects_new_durable_run(tmp_path: Path) -> None:
    supervisor, _settings, runs_dir, captures_dir = _build_w14_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine

        def critical_measure(root):
            resolved = Path(root).resolve()
            return DiskMeasurement(
                filesystem_root=resolved,
                total_bytes=100 * GIB,
                used_bytes=96 * GIB,
                free_bytes=4 * GIB,
                free_ratio=0.04,
                filesystem_identity=f"root:{resolved}",
            )

        with patch.object(engine.disk_budget_guard, "measure", side_effect=critical_measure):
            with pytest.raises(LocalStorageAdmissionError) as exc_info:
                engine._assert_new_run_storage_admission("w14-critical-new-run")
        error = exc_info.value
        assert error.failure_origin.value == "local_storage"
        assert error.provider_fallback_allowed is False
        assert set(error.filesystem_roots) == {runs_dir, captures_dir}
    finally:
        supervisor.close()


def test_w14_success_before_release_manifest_is_not_gc_closed(tmp_path: Path) -> None:
    supervisor, _settings, _runs_dir, _captures_dir = _build_w14_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine
        composition = engine.stage_service.target_post_write_composition
        resolver = engine.storage_lifecycle_service.catalog.scope_terminal_resolver
        fake_ref = object()
        lifecycle = SimpleNamespace(
            status=RunLifecycleStatus.SUCCESS,
            run_completion_assessment_ref=fake_ref,
            identity=SimpleNamespace(task_id="task_w14_success_before_release"),
            lifecycle_revision=7,
        )
        assessment = SimpleNamespace(
            run_id="w14_success_before_release",
            aggregate_result=RunCompletionAggregateResult.SUCCESS,
        )
        with patch.object(
            engine.lifecycle_service,
            "load_run_lifecycle",
            return_value=lifecycle,
        ), patch.object(
            composition.assessment_store,
            "resolve_exact",
            return_value=assessment,
        ):
            assert resolver("run:w14_success_before_release") is False
    finally:
        supervisor.close()


def test_w14_success_with_exact_assessment_and_manifest_is_gc_closed(
    tmp_path: Path,
) -> None:
    # I9 closure validates manifest/proof/payload together. A schema-bypassing
    # SimpleNamespace manifest is insufficient; use real terminal authority.
    from bootstrap.supervisor_factory import SupervisorFactory
    from backend.tests.bca014_i9_fixtures import terminal_case, publish
    captured = {}
    validate = SupervisorFactory._validate_target_startup
    def capture(**kwargs):
        validate(**kwargs)
        captured.update(kwargs)
    with patch.object(SupervisorFactory, "_validate_target_startup", staticmethod(capture)):
        case = terminal_case(tmp_path)
    composition, request, _, _ = case
    resolver = captured["storage_lifecycle_service"].catalog.scope_terminal_resolver
    scope = "run:" + request["run_id"]
    assert captured["target_post_write_composition"] is composition
    assert resolver(scope) is False
    publish(case)
    assert resolver(scope) is True


def test_w14_factory_composes_single_storage_owner_and_release_root_is_never_cross_deleted(
    tmp_path: Path,
) -> None:
    supervisor, settings, runs_dir, captures_dir = _build_w14_supervisor(tmp_path)
    try:
        engine = supervisor.workflow_engine
        assert isinstance(engine.storage_lifecycle_service, StorageLifecycleService)
        assert isinstance(engine.disk_budget_guard, DiskBudgetGuard)
        assert engine.storage_filesystem_roots == (runs_dir, captures_dir)
        assert engine.disk_budget_guard.soft_free_ratio == settings.disk_soft_free_ratio
        assert engine.disk_budget_guard.hard_free_ratio == settings.disk_hard_free_ratio
        assert (
            engine.disk_budget_guard.critical_free_ratio
            == settings.disk_critical_free_ratio
        )

        run_id = "w14_terminal_release_root"
        identity = RunIdentitySchema(
            run_id=run_id,
            task_id="task_w14",
            tenant_id="tenant_default",
            canonical_request_sha256="1" * 64,
        )
        claimed = engine.lifecycle_service.claim_new_run(identity)
        running = engine.lifecycle_service.transition_run(
            run_id,
            claimed.lifecycle_revision,
            RunLifecycleStatus.RUNNING,
            reason_ref="test:start",
        )
        terminal = engine.lifecycle_service.transition_run(
            run_id,
            running.lifecycle_revision,
            RunLifecycleStatus.CANCELLED,
            reason_ref="test:cancelled",
        )
        assert terminal.status is RunLifecycleStatus.CANCELLED
        cancelled_plan = engine.storage_lifecycle_service.plan_gc(
            f"run:{run_id}",
            now=100.0,
        )
        assert cancelled_plan.terminal_or_gc_closed is False
        assert cancelled_plan.blocked_reasons == (
            "SCOPE_NOT_TERMINAL_OR_GC_CLOSED",
        )

        release_store = engine.stage_service.target_post_write_composition.release_store
        release_ref = release_store.put_json(
            {"release": "integrity-root"},
            artifact_type="post_write_release_manifest",
            schema_version="post_write_release_manifest_v1",
            created_by="test",
            task_id="task_w14",
            run_id=run_id,
            artifact_ref="artifact://w14/release-root",
        )
        plan = engine.storage_lifecycle_service.plan_gc(f"run:{run_id}", now=100.0)
        release_candidate = next(
            item for item in plan.candidates if item.object_ref == release_ref.artifact_ref
        )
        assert release_candidate.eligible is False
        assert "STRONGLY_REACHABLE" in release_candidate.eligibility_reasons
        assert "EXTERNAL_OR_UNMANAGED" in release_candidate.eligibility_reasons
        audit = engine.storage_lifecycle_service.run_terminal_scope_gc(
            f"run:{run_id}",
            dry_run=False,
        )
        assert audit.deleted_count == 0
        assert release_store.get_json(
            release_ref.artifact_ref,
            task_id="task_w14",
            run_id=run_id,
        ) == {"release": "integrity-root"}
    finally:
        supervisor.close()
