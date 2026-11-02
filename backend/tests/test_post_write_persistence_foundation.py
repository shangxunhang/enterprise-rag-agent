from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apps.enterprise_document.services.post_write import (
    POST_WRITE_SCHEMA_VERSION,
    PostWriteConcurrencyError,
    PostWritePersistenceIntegrityError,
    PostWriteSqliteDatabase,
)


EXPECTED_TABLES = {
    "post_write_metadata",
    "artifact_states",
    "requirement_snapshots",
    "quality_delivery_contracts",
    "post_write_entries",
    "validation_runs",
    "validation_execution_events",
    "validation_facts",
    "validation_completions",
    "validator_execution_records",
    "delivery_gate_decisions",
    "correction_policies",
    "post_write_control_decisions",
    "correction_attempt_projections",
    "document_binding_values",
}


def _raw(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_fresh_current_initialization_is_complete_and_reopenable(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    database = PostWriteSqliteDatabase(path)

    with database.read_transaction() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert tables == EXPECTED_TABLES
        assert (
            connection.execute(
                "SELECT value FROM post_write_metadata WHERE key='schema_version'"
            ).fetchone()[0]
            == POST_WRITE_SCHEMA_VERSION
        )
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA integrity_check").fetchone()[0].lower() == "ok"

    PostWriteSqliteDatabase(path).verify_integrity()


def test_write_transaction_commits_and_rolls_back_atomically(tmp_path) -> None:
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")

    with database.write_transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO requirement_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            ("req-1", "v1", "contract-v1", "h1", "{}", 1.0),
        )

    with pytest.raises(RuntimeError, match="abort"):
        with database.write_transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO requirement_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                ("req-2", "v1", "contract-v1", "h2", "{}", 2.0),
            )
            raise RuntimeError("abort")

    with database.read_transaction() as connection:
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT requirement_snapshot_id FROM requirement_snapshots ORDER BY 1"
            )
        ]
    assert ids == ["req-1"]


def test_caller_owned_transaction_uses_one_connection_for_multi_table_atomicity(
    tmp_path,
) -> None:
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")

    with database.write_transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO validation_runs("
            "validation_run_id,run_schema_version,run_contract_version,subject_hash,"
            "run_binding_json,run_binding_sha256,execution_state,state_version,"
            "created_at_epoch,updated_at_epoch"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("run-1", "v1", "c1", "subject", "{}", "bind", "CREATED", 1, 1.0, 1.0),
        )
        connection.execute(
            "INSERT INTO validation_execution_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("run-1", "evt-1", 1, "RUN_CREATED", "{}", "ph", "eh", 1.0),
        )

    with database.read_transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_runs WHERE validation_run_id='run-1'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_execution_events "
            "WHERE validation_run_id='run-1'"
        ).fetchone()[0] == 1


def test_unknown_future_schema_version_fails_closed(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    connection = _raw(path)
    try:
        connection.execute(
            "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
            ("gate_v2_post_write_store_v999",),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="unknown or future"):
        PostWriteSqliteDatabase(path)


def test_missing_required_table_fails_closed_and_is_not_silently_repaired(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    connection = _raw(path)
    try:
        connection.execute("DROP TABLE validation_facts")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="table set mismatch"):
        PostWriteSqliteDatabase(path)

    connection = _raw(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "validation_facts" not in tables
    finally:
        connection.close()


def test_missing_required_index_fails_closed(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    connection = _raw(path)
    try:
        connection.execute("DROP INDEX idx_validation_facts_run")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="indexes missing"):
        PostWriteSqliteDatabase(path)


def test_same_name_wrong_index_definition_fails_closed(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    connection = _raw(path)
    try:
        connection.execute("DROP INDEX idx_validation_facts_run")
        connection.execute(
            "CREATE INDEX idx_validation_facts_run ON validation_facts(fact_id)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="index DDL shape"):
        PostWriteSqliteDatabase(path)


def test_same_column_names_with_corrupt_ddl_shape_fail_closed(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    connection = _raw(path)
    try:
        connection.execute("DROP TABLE validation_facts")
        connection.execute(
            "CREATE TABLE validation_facts ("
            "fact_id TEXT, validation_run_id TEXT, subject_hash TEXT, "
            "fact_schema_version TEXT, fact_contract_version TEXT, "
            "fact_json TEXT, fact_sha256 TEXT, created_at_epoch TEXT)"
        )
        connection.execute(
            "CREATE INDEX idx_validation_facts_run "
            "ON validation_facts(validation_run_id)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="table DDL shape"):
        PostWriteSqliteDatabase(path)


def test_nonempty_unversioned_database_fails_closed(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    connection = _raw(path)
    try:
        connection.execute("CREATE TABLE legacy_post_write(id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="no schema metadata"):
        PostWriteSqliteDatabase(path)


def test_metadata_v1_with_extra_authority_table_fails_closed(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    connection = _raw(path)
    try:
        connection.execute("CREATE TABLE artifact_head(id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="table set mismatch"):
        PostWriteSqliteDatabase(path)


def test_failed_fresh_initialization_rolls_back_all_schema_objects(
    tmp_path,
    monkeypatch,
) -> None:
    import apps.enterprise_document.services.post_write.persistence as module

    path = tmp_path / "post_write.sqlite3"
    monkeypatch.setitem(
        module._INDEX_DDL,
        "idx_forced_initialization_failure",
        "CREATE INDEX idx_forced_initialization_failure ON missing_table(id)",
    )

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        PostWriteSqliteDatabase(path)

    connection = _raw(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()
    assert tables == set()


def test_real_sqlite_writer_lock_maps_to_concurrency_error(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    owner = PostWriteSqliteDatabase(path, busy_timeout_ms=10)
    contender = PostWriteSqliteDatabase(path, busy_timeout_ms=10)

    with owner.write_transaction(immediate=True):
        with pytest.raises(PostWriteConcurrencyError):
            with contender.write_transaction(immediate=True):
                pass


def test_non_lock_operational_error_is_not_retyped_as_concurrency(tmp_path) -> None:
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with database.write_transaction() as connection:
            connection.execute("SELECT * FROM busy_missing_table")


def test_step5_integration_persistence_exposes_authorized_structural_and_validation_stores() -> None:
    import apps.enterprise_document.services.post_write.persistence as module

    exported = set(vars(module))
    assert {
        "ArtifactStateStore",
        "RequirementStore",
        "PostWriteEntryStore",
        "ValidationStore",
        "QualityDeliveryContractStore",
        "GateDecisionStore",
        "ControlDecisionStore",
        "CorrectionAttemptStore",
        "CorrectionPolicyStore",
        "DocumentBindingStore",
        "PostWriteReleaseCommitFence",
    }.issubset(exported)

    forbidden = {
        "ReleaseDecisionStore",
        "PostWriteReleaseHead",
    }
    assert forbidden.isdisjoint(exported)

    projection_store = module.CorrectionAttemptStore
    assert not hasattr(projection_store, "reserve")
    assert not hasattr(projection_store, "dispatch")
    assert not hasattr(projection_store, "remaining_budget")
    assert not hasattr(projection_store, "mark_dispatched")


def _downgrade_current_store_to_v3(path: Path) -> None:
    connection = _raw(path)
    try:
        connection.execute("DROP TABLE document_binding_values")
        connection.execute(
            "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
            ("gate_v2_post_write_store_v3",),
        )
        connection.commit()
    finally:
        connection.close()


def _downgrade_current_store_to_v2(path: Path) -> None:
    _downgrade_current_store_to_v3(path)
    connection = _raw(path)
    try:
        connection.execute("DROP INDEX idx_post_write_control_decisions_run")
        connection.execute("DROP INDEX idx_correction_attempt_projections_run")
        connection.execute("DROP TABLE correction_attempt_projections")
        connection.execute("DROP TABLE post_write_control_decisions")
        connection.execute("DROP TABLE correction_policies")
        connection.execute(
            "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
            ("gate_v2_post_write_store_v2",),
        )
        connection.commit()
    finally:
        connection.close()


def _downgrade_current_store_to_v1(path: Path) -> None:
    _downgrade_current_store_to_v2(path)
    connection = _raw(path)
    try:
        connection.execute("DROP INDEX idx_delivery_gate_decisions_run")
        connection.execute("DROP TABLE delivery_gate_decisions")
        connection.execute(
            "UPDATE post_write_metadata SET value=? WHERE key='schema_version'",
            ("gate_v2_post_write_store_v1",),
        )
        connection.commit()
    finally:
        connection.close()


def test_exact_v3_store_migrates_to_v4_without_guessing(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    _downgrade_current_store_to_v3(path)

    migrated = PostWriteSqliteDatabase(path)
    migrated.verify_integrity()
    with migrated.read_transaction() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert tables == EXPECTED_TABLES
        assert connection.execute(
            "SELECT value FROM post_write_metadata WHERE key='schema_version'"
        ).fetchone()[0] == POST_WRITE_SCHEMA_VERSION


def test_exact_v2_store_migrates_serially_to_v4_without_guessing(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    _downgrade_current_store_to_v2(path)

    migrated = PostWriteSqliteDatabase(path)
    migrated.verify_integrity()
    with migrated.read_transaction() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert tables == EXPECTED_TABLES
        assert connection.execute(
            "SELECT value FROM post_write_metadata WHERE key='schema_version'"
        ).fetchone()[0] == POST_WRITE_SCHEMA_VERSION


def test_exact_v1_store_migrates_serially_through_v2_v3_to_v4(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    _downgrade_current_store_to_v1(path)

    migrated = PostWriteSqliteDatabase(path)
    migrated.verify_integrity()
    with migrated.read_transaction() as connection:
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        } == EXPECTED_TABLES
        assert connection.execute(
            "SELECT value FROM post_write_metadata WHERE key='schema_version'"
        ).fetchone()[0] == POST_WRITE_SCHEMA_VERSION


def test_malformed_v3_shape_fails_closed_before_v4_migration(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    _downgrade_current_store_to_v3(path)
    connection = _raw(path)
    try:
        connection.execute(
            "CREATE TABLE document_binding_values(id TEXT PRIMARY KEY)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="V3 table set mismatch"):
        PostWriteSqliteDatabase(path)


def test_malformed_v2_shape_fails_closed_before_v3_migration(tmp_path) -> None:
    path = tmp_path / "post_write.sqlite3"
    PostWriteSqliteDatabase(path)
    _downgrade_current_store_to_v2(path)
    connection = _raw(path)
    try:
        connection.execute(
            "CREATE TABLE correction_attempt_projections(id TEXT PRIMARY KEY)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PostWritePersistenceIntegrityError, match="V2 table set mismatch"):
        PostWriteSqliteDatabase(path)
