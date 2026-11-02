"""Read-only physical resolver contracts; existing execution mutation tests stay intact."""

from dataclasses import FrozenInstanceError
import sqlite3

import pytest

from core.runtime.pre_write_execution_store import (
    PreWriteExecutionIntegrityError,
    SQLitePreWriteExecutionStore,
)


def _snapshot(path):
    with sqlite3.connect(path) as db:
        return "\n".join(db.iterdump())


@pytest.mark.parametrize("state", ["in_progress", "expired", "released", "completed", "completed_empty"])
def test_resolve_all_legal_physical_states_is_read_only(tmp_path, state):
    store = SQLitePreWriteExecutionStore(tmp_path / "execution.sqlite3")
    claim = store.claim_recovery_dispatch("occurrence-1", semantic_fingerprint="a" * 64, owner_id="owner")
    if state == "expired":
        with sqlite3.connect(store.database_path) as db:
            db.execute("UPDATE recovery_dispatches SET lease_expires_at = 0")
    elif state == "released":
        store.release_recovery_dispatch(claim, reason="retry")
    elif state.startswith("completed"):
        store.complete_recovery_dispatch(claim, result_ref="" if state == "completed_empty" else "result")
    before = _snapshot(store.database_path)
    # SQLite authorizer rejects writes and transaction statements, including BEGIN IMMEDIATE.
    original_connect = store._connect
    statements = []

    def connect():
        db = original_connect()
        db.set_trace_callback(statements.append)
        db.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK
                          if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ)
                          else sqlite3.SQLITE_DENY)
        return db

    store._connect = connect
    record = store.resolve_recovery_dispatch("occurrence-1")
    assert record == store.resolve_recovery_dispatch("occurrence-1")
    assert store.resolve_recovery_dispatch("absent") is None
    assert all(sql.startswith("SELECT ") for sql in statements)
    assert record.occurrence_id == claim.occurrence_id
    assert record.semantic_fingerprint == claim.semantic_fingerprint
    assert record.status == ("completed" if state.startswith("completed") else "in_progress")
    with pytest.raises(FrozenInstanceError):
        record.status = "changed"
    assert _snapshot(store.database_path) == before


@pytest.mark.parametrize(("column", "value"), [
    ("status", "unknown"), ("semantic_fingerprint", ""),
    ("owner_id", ""), ("claim_token", None), ("lease_expires_at", None),
    ("lease_expires_at", "not-an-epoch"), ("lease_expires_at", float("inf")),
    ("created_at_epoch", -1), ("updated_at_epoch", "invalid"),
    ("result_ref", "premature-result"), ("release_reason", ""),
])
def test_malformed_row_fails_closed_without_repair(tmp_path, column, value):
    store = SQLitePreWriteExecutionStore(tmp_path / "execution.sqlite3")
    store.claim_recovery_dispatch("occurrence-1", semantic_fingerprint="a" * 64, owner_id="owner")
    with sqlite3.connect(store.database_path) as db:
        db.execute("PRAGMA ignore_check_constraints = ON")
        db.execute(f"UPDATE recovery_dispatches SET {column} = ?", (value,))
    before = _snapshot(store.database_path)
    with pytest.raises(PreWriteExecutionIntegrityError):
        store.resolve_recovery_dispatch("occurrence-1")
    assert _snapshot(store.database_path) == before


def test_resolver_does_not_take_over_claim_renew_complete_release_semantics(tmp_path):
    store = SQLitePreWriteExecutionStore(tmp_path / "execution.sqlite3")
    first = store.claim_recovery_dispatch("o", semantic_fingerprint="fp", owner_id="one")
    assert store.renew_recovery_claim(first)
    assert store.resolve_recovery_dispatch("o").claim_token == first.claim_token
    assert store.release_recovery_dispatch(first, reason="interrupted")
    assert store.resolve_recovery_dispatch("o").owner_id is None
    second = store.claim_recovery_dispatch("o", semantic_fingerprint="fp", owner_id="two")
    assert second.acquired and second.claim_token != first.claim_token
    assert store.complete_recovery_dispatch(second, result_ref="result")
    assert store.resolve_recovery_dispatch("o").result_ref == "result"
    assert not store.claim_recovery_dispatch("o", semantic_fingerprint="fp", owner_id="three").acquired
