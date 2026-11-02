"""P2-B verification for Gate V2 pre-write execution-control infrastructure."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from pathlib import Path

import pytest

from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRouteDecisionRefSchema
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionFailureKind,
    WriterExecutionRefSchema,
    WriterExecutionResultSchema,
    WriterExecutionStatus,
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from core.runtime.execution_control import WorkflowExecutionControl
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import (
    EXECUTION_CONTROL_DATABASE_FILENAME,
    EXECUTION_STORE_SCHEMA_VERSION,
    EXECUTION_STORE_SCHEMA_VERSION_V1,
    PreWriteExecutionIntegrityError,
    RecoveryDispatchStatus,
    SQLitePreWriteExecutionStore,
    WriterExecutionState,
    WriterRestoreCondition,
)
from core.runtime.pre_write_route_decision_store import (
    PreWriteRouteDecisionPersistenceIntegrityError,
    SQLitePreWriteRouteDecisionStore,
)
from core.runtime.side_effects import InMemorySideEffectCommitStore
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory


FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64


def _writer_key() -> WriterLogicalExecutionKey:
    return WriterLogicalExecutionKey(
        writer_subject_ref=WriterSubjectRefSchema(
            run_id="run-1",
            document_id="doc-1",
            section_id="section-1",
        ),
        write_instance_id="write-1",
        route_decision_ref=PreWriteRouteDecisionRefSchema(
            route_decision_id="route-1"
        ),
    )


def _success_result() -> WriterExecutionResultSchema:
    return WriterExecutionResultSchema(
        logical_execution_key=_writer_key(),
        status=WriterExecutionStatus.SUCCESS,
        artifact=SchemeSectionSchema(
            section_id="section-1",
            section_title="Section",
            section_order=1,
            content="generated",
        ),
        generation_provenance={"generation_class": "normal_write"},
    )


def _failure_result() -> WriterExecutionResultSchema:
    return WriterExecutionResultSchema(
        logical_execution_key=_writer_key(),
        status=WriterExecutionStatus.TECHNICAL_FAILURE,
        failure_kind=WriterExecutionFailureKind.MODEL_TIMEOUT,
        error=ErrorSchema(
            error_code="MODEL_TIMEOUT",
            error_type="TimeoutError",
            message="provider timed out",
            failure_category=FailureCategory.TIMEOUT,
        ),
        generation_provenance={"generation_class": "normal_write"},
    )


def test_execution_store_uses_dedicated_database_and_no_business_counter_columns(
    tmp_path: Path,
) -> None:
    store = SQLitePreWriteExecutionStore.for_data_root(tmp_path)
    assert store.database_path == (
        tmp_path / "runs" / EXECUTION_CONTROL_DATABASE_FILENAME
    ).resolve()
    assert store.database_path.name == "execution_control.sqlite3"
    assert store.database_path.name not in {
        "run_trace.sqlite3",
        "langgraph_checkpoints.sqlite3",
    }

    with sqlite3.connect(store.database_path) as connection:
        recovery_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(recovery_dispatches)"
            ).fetchall()
        }
        writer_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(writer_executions)"
            ).fetchall()
        }
    assert "recovery_rounds_used" not in recovery_columns
    assert "recovery_rounds_limit" not in recovery_columns
    assert "admission_outcome" not in recovery_columns
    assert "selected_route" not in recovery_columns
    assert "selected_route" not in writer_columns
    assert "fallback" not in writer_columns


def test_same_recovery_occurrence_two_workers_have_one_physical_owner(
    tmp_path: Path,
) -> None:
    store = SQLitePreWriteExecutionStore(
        tmp_path / "execution_control.sqlite3",
        recovery_claim_lease_seconds=30,
    )
    barrier = threading.Barrier(2)

    def claim(owner_id: str):
        barrier.wait(timeout=5)
        return store.claim_recovery_dispatch(
            "recovery-occurrence-1",
            semantic_fingerprint=FINGERPRINT_A,
            owner_id=owner_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ("worker-a", "worker-b")))

    assert sum(item.status is RecoveryDispatchStatus.ACQUIRED for item in claims) == 1
    assert sum(item.status is RecoveryDispatchStatus.IN_PROGRESS for item in claims) == 1
    acquired = next(item for item in claims if item.acquired)
    assert store.complete_recovery_dispatch(acquired, result_ref="candidate-ref-1") is True
    completed = store.claim_recovery_dispatch(
        "recovery-occurrence-1",
        semantic_fingerprint=FINGERPRINT_A,
        owner_id="worker-c",
    )
    assert completed.status is RecoveryDispatchStatus.COMPLETED
    assert completed.result_ref == "candidate-ref-1"


def test_recovery_release_reclaims_same_occurrence_without_new_business_identity(
    tmp_path: Path,
) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    first = store.claim_recovery_dispatch(
        "recovery-occurrence-1",
        semantic_fingerprint=FINGERPRINT_A,
        owner_id="worker-a",
    )
    assert first.acquired
    assert store.release_recovery_dispatch(first, reason="worker_lost") is True
    second = store.claim_recovery_dispatch(
        "recovery-occurrence-1",
        semantic_fingerprint=FINGERPRINT_A,
        owner_id="worker-b",
    )
    assert second.acquired
    assert second.occurrence_id == first.occurrence_id
    assert second.semantic_fingerprint == first.semantic_fingerprint
    assert second.claim_token != first.claim_token

    with pytest.raises(PreWriteExecutionIntegrityError):
        store.claim_recovery_dispatch(
            "recovery-occurrence-1",
            semantic_fingerprint=FINGERPRINT_B,
            owner_id="worker-c",
        )


def test_writer_dispatched_is_durable_before_provider_and_ambiguous_restore_never_redispatches(
    tmp_path: Path,
) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    key = _writer_key()
    reserved = store.reserve_writer_execution(
        key,
        semantic_fingerprint=FINGERPRINT_A,
    )
    assert reserved.state is WriterExecutionState.RESERVED

    provider_calls = 0
    if store.mark_writer_dispatched(key, semantic_fingerprint=FINGERPRINT_A):
        # The external provider would only be called after this durable state is visible.
        persisted_before_provider = store.get_writer_execution(key)
        assert persisted_before_provider is not None
        assert persisted_before_provider.state is WriterExecutionState.DISPATCHED
        provider_calls += 1

    restored = SQLitePreWriteExecutionStore(
        tmp_path / "execution_control.sqlite3"
    ).get_writer_execution(key)
    assert restored is not None
    assert restored.restore_condition is WriterRestoreCondition.INDETERMINATE_EXTERNAL_EXECUTION
    assert store.mark_writer_dispatched(key, semantic_fingerprint=FINGERPRINT_A) is False
    assert provider_calls == 1


def test_same_writer_key_concurrency_permits_only_one_first_dispatch_and_conflict_fails(
    tmp_path: Path,
) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    key = _writer_key()
    store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    barrier = threading.Barrier(2)

    def dispatch() -> bool:
        barrier.wait(timeout=5)
        return store.mark_writer_dispatched(
            key,
            semantic_fingerprint=FINGERPRINT_A,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: dispatch(), range(2)))
    assert sorted(outcomes) == [False, True]

    with pytest.raises(PreWriteExecutionIntegrityError):
        store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_B)


def test_writer_success_and_confirmed_failure_are_durable_terminal_results(
    tmp_path: Path,
) -> None:
    success_store = SQLitePreWriteExecutionStore(tmp_path / "success.sqlite3")
    key = _writer_key()
    success_store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    assert success_store.mark_writer_dispatched(
        key, semantic_fingerprint=FINGERPRINT_A
    )
    succeeded = success_store.persist_writer_result(
        key,
        semantic_fingerprint=FINGERPRINT_A,
        result=_success_result(),
    )
    assert succeeded.state is WriterExecutionState.SUCCEEDED
    assert succeeded.restore_condition is WriterRestoreCondition.SUCCEEDED
    assert success_store.mark_writer_dispatched(
        key, semantic_fingerprint=FINGERPRINT_A
    ) is False

    failure_store = SQLitePreWriteExecutionStore(tmp_path / "failure.sqlite3")
    failure_store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    assert failure_store.mark_writer_dispatched(
        key, semantic_fingerprint=FINGERPRINT_A
    )
    failed = failure_store.persist_writer_result(
        key,
        semantic_fingerprint=FINGERPRINT_A,
        result=_failure_result(),
    )
    assert failed.state is WriterExecutionState.CONFIRMED_FAILED
    assert failed.restore_condition is WriterRestoreCondition.CONFIRMED_FAILED
    assert failure_store.mark_writer_dispatched(
        key, semantic_fingerprint=FINGERPRINT_A
    ) is False


def test_writer_dispatch_persistence_failure_prevents_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "execution_control.sqlite3")
    key = _writer_key()
    store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    provider_calls = 0

    def fail_connect():
        raise sqlite3.OperationalError("simulated execution store I/O failure")

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError):
        if store.mark_writer_dispatched(key, semantic_fingerprint=FINGERPRINT_A):
            provider_calls += 1
    assert provider_calls == 0


def test_option_d_route_store_binds_exact_authorization_snapshot(tmp_path: Path) -> None:
    from backend.tests import test_gate_v2_pre_write_writer_invocation as writer_fixtures
    from apps.enterprise_document.services.scheme_writer.pre_write_router import PreWriteRouter

    chain = writer_fixtures._current_chain(allow=True)
    auth_hash = PreWriteRouter.authorization_snapshot_sha256(
        writer_subject_ref=chain.route_decision.writer_subject_ref,
        write_instance_id=chain.route_decision.write_instance_id,
        decision=chain.decision,
        binding=chain.binding,
        applicability=chain.applicability,
        enforcement=chain.enforcement,
        recovery_control=chain.recovery_control,
    )
    store = SQLitePreWriteRouteDecisionStore(tmp_path / "execution_control.sqlite3")
    ref = store.put_if_absent_or_same(
        chain.route_decision,
        authorization_snapshot_sha256=auth_hash,
    )
    assert store.resolve_exact(ref) == chain.route_decision
    assert store.resolve_exact_for_snapshot(
        ref, authorization_snapshot_sha256=auth_hash
    ) == chain.route_decision
    with pytest.raises(PreWriteRouteDecisionPersistenceIntegrityError):
        store.put_if_absent_or_same(
            chain.route_decision,
            authorization_snapshot_sha256="f" * 64,
        )
    with pytest.raises(PreWriteRouteDecisionPersistenceIntegrityError):
        store.resolve_exact_for_snapshot(
            ref,
            authorization_snapshot_sha256="f" * 64,
        )


def test_option_d_v1_to_v2_preserves_writer_rows_and_wal(tmp_path: Path) -> None:
    path = tmp_path / "migration.sqlite3"
    original = SQLitePreWriteExecutionStore(path)
    key = _writer_key()
    original.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    original.claim_recovery_dispatch(
        "migration-recovery",
        semantic_fingerprint=FINGERPRINT_A,
        owner_id="migration-owner",
    )
    with sqlite3.connect(path) as connection:
        before_writer = connection.execute("SELECT * FROM writer_executions").fetchall()
        before_recovery = connection.execute("SELECT * FROM recovery_dispatches").fetchall()
        connection.execute("DROP TABLE pre_write_route_decisions")
        connection.execute(
            "UPDATE pre_write_execution_metadata SET value = ? WHERE key = 'schema_version'",
            (EXECUTION_STORE_SCHEMA_VERSION_V1,),
        )
        connection.commit()

    SQLitePreWriteRouteDecisionStore(path)
    with sqlite3.connect(path) as connection:
        after_writer = connection.execute("SELECT * FROM writer_executions").fetchall()
        after_recovery = connection.execute("SELECT * FROM recovery_dispatches").fetchall()
        version = connection.execute(
            "SELECT value FROM pre_write_execution_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        columns = [row[1] for row in connection.execute(
            "PRAGMA table_info(pre_write_route_decisions)"
        ).fetchall()]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert before_writer == after_writer
    assert before_recovery == after_recovery
    assert version == EXECUTION_STORE_SCHEMA_VERSION
    assert "authorization_snapshot_sha256" in columns
    assert str(journal_mode).lower() == "wal"


def test_option_d_constructor_order_and_concurrent_migration(tmp_path: Path) -> None:
    first = tmp_path / "execution-first.sqlite3"
    SQLitePreWriteExecutionStore(first)
    SQLitePreWriteRouteDecisionStore(first)
    second = tmp_path / "route-first.sqlite3"
    SQLitePreWriteRouteDecisionStore(second)
    SQLitePreWriteExecutionStore(second)

    concurrent = tmp_path / "concurrent.sqlite3"
    barrier = threading.Barrier(2)

    def construct(kind: str) -> str:
        barrier.wait(timeout=5)
        if kind == "route":
            SQLitePreWriteRouteDecisionStore(concurrent)
        else:
            SQLitePreWriteExecutionStore(concurrent)
        return kind

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(construct, ("route", "execution"))) == {"route", "execution"}


def test_option_d_malformed_v1_and_v2_fail_closed(tmp_path: Path) -> None:
    v1 = tmp_path / "malformed-v1.sqlite3"
    SQLitePreWriteExecutionStore(v1)
    with sqlite3.connect(v1) as connection:
        connection.execute("DROP TABLE pre_write_route_decisions")
        connection.execute(
            "UPDATE pre_write_execution_metadata SET value = ? WHERE key = 'schema_version'",
            (EXECUTION_STORE_SCHEMA_VERSION_V1,),
        )
        connection.execute("ALTER TABLE writer_executions RENAME TO writer_old")
        connection.execute(
            "CREATE TABLE writer_executions ("
            "logical_execution_key TEXT PRIMARY KEY, "
            "semantic_fingerprint INTEGER NOT NULL, "
            "state TEXT NOT NULL, result_json TEXT, "
            "created_at_epoch REAL NOT NULL, updated_at_epoch REAL NOT NULL)"
        )
        connection.commit()
    with pytest.raises(ValueError):
        SQLitePreWriteExecutionStore(v1)
    with sqlite3.connect(v1) as connection:
        assert connection.execute(
            "SELECT value FROM pre_write_execution_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == EXECUTION_STORE_SCHEMA_VERSION_V1

    v2 = tmp_path / "malformed-v2.sqlite3"
    SQLitePreWriteExecutionStore(v2)
    with sqlite3.connect(v2) as connection:
        connection.execute("ALTER TABLE pre_write_route_decisions RENAME TO route_old")
        connection.execute(
            "CREATE TABLE pre_write_route_decisions ("
            "route_decision_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL)"
        )
        connection.commit()
    with pytest.raises(ValueError):
        SQLitePreWriteRouteDecisionStore(v2)


def test_option_d_v1_metadata_with_precreated_route_table_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "precreated.sqlite3"
    original = SQLitePreWriteExecutionStore(path)
    key = _writer_key()
    original.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    original.claim_recovery_dispatch(
        "rollback-recovery",
        semantic_fingerprint=FINGERPRINT_A,
        owner_id="rollback-owner",
    )
    with sqlite3.connect(path) as connection:
        before_writer = connection.execute("SELECT * FROM writer_executions").fetchall()
        before_recovery = connection.execute("SELECT * FROM recovery_dispatches").fetchall()
        connection.execute(
            "UPDATE pre_write_execution_metadata SET value = ? WHERE key = 'schema_version'",
            (EXECUTION_STORE_SCHEMA_VERSION_V1,),
        )
        connection.commit()
    with pytest.raises(ValueError):
        SQLitePreWriteRouteDecisionStore(path)
    with sqlite3.connect(path) as connection:
        after_writer = connection.execute("SELECT * FROM writer_executions").fetchall()
        after_recovery = connection.execute("SELECT * FROM recovery_dispatches").fetchall()
        version = connection.execute(
            "SELECT value FROM pre_write_execution_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert before_writer == after_writer
    assert before_recovery == after_recovery
    assert version == EXECUTION_STORE_SCHEMA_VERSION_V1


def test_option_d_exact_writer_execution_ref(tmp_path: Path) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "result-ref.sqlite3")
    key = _writer_key()
    result = _success_result()
    store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    assert store.mark_writer_dispatched(key, semantic_fingerprint=FINGERPRINT_A)
    store.persist_writer_result(
        key,
        semantic_fingerprint=FINGERPRINT_A,
        result=result,
    )
    ref = WriterExecutionRefSchema(
        logical_execution_key=key,
        result_sha256=canonical_sha256(result.model_dump(mode="json")),
    )
    assert store.resolve_writer_execution(ref) == result


def test_option_d_legacy_success_has_no_invocation_reconstruction(tmp_path: Path) -> None:
    store = SQLitePreWriteExecutionStore(tmp_path / "legacy-ref.sqlite3")
    key = _writer_key()
    store.reserve_writer_execution(key, semantic_fingerprint=FINGERPRINT_A)
    assert store.mark_writer_dispatched(key, semantic_fingerprint=FINGERPRINT_A)
    store.persist_writer_result(
        key,
        semantic_fingerprint=FINGERPRINT_A,
        result=_success_result(),
    )
    with pytest.raises(PreWriteExecutionIntegrityError):
        store.resolve_writer_generation_invocation(
            WriterGenerationInvocationRefSchema(
                logical_execution_key=key,
                invocation_sha256=FINGERPRINT_A,
            )
        )


def test_workflow_execution_control_remains_generic_technical_mechanism() -> None:
    control = WorkflowExecutionControl.with_timeout(
        execution_id="attempt-1",
        timeout_seconds=1,
    )
    assert isinstance(control.side_effect_store, InMemorySideEffectCommitStore)
    assert control.metadata()["cooperative_cancellation"] is True
    assert not hasattr(control, "reserve_writer_execution")
    assert not hasattr(control, "claim_recovery_dispatch")
