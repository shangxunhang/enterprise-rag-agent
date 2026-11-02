"""Real WAL owner-drain races and same-connection validated exact reads (I4 only)."""
from concurrent.futures import ThreadPoolExecutor
import ast
import inspect
import sqlite3
import threading
import textwrap

import pytest

from apps.enterprise_document.services.post_write import persistence as pw
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError
from backend.tests.test_bca014_finalizing_lifecycle import _finalize
from backend.tests.test_bca014_i3_mutation_owner_guards import (
    _live, _dump, _sql_case, SQL_KEYS, sql_samples,
)
from backend.tests.test_post_write_structural_currentness import _requirement_ref


class ObservedConnection:
    def __init__(self, real, *, attempted=None, acquired=None):
        self.real = real
        self.attempted, self.acquired = attempted, acquired

    def __getattr__(self, name):
        return getattr(self.real, name)

    def execute(self, sql, *args):
        if sql == "BEGIN IMMEDIATE" and self.attempted is not None:
            self.attempted.set()
        result = self.real.execute(sql, *args)
        if sql == "BEGIN IMMEDIATE" and self.acquired is not None:
            self.acquired.set()
        return result


def _pending_requirement(tmp_path):
    seed = pw.PostWriteSqliteDatabase(tmp_path / "seed.sqlite3")
    ref = _requirement_ref(seed)
    return ref, pw.RequirementStore(seed).resolve_exact(ref)


@pytest.mark.parametrize("rollback", [False, True])
def test_pattern_b_real_writer_drained_after_durable_finalizing(tmp_path, rollback):
    lifecycle, run = _live(tmp_path)
    db = pw.PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3",
        run_commit_guard=lifecycle.assert_run_committable, require_run_commit_guard=True)
    ref, snapshot = _pending_requirement(tmp_path)
    holding, release = threading.Event(), threading.Event()
    attempted, acquired = threading.Event(), threading.Event()
    guard_passed = threading.Event()

    def guard(run_id):
        lifecycle.assert_run_committable(run_id)
        guard_passed.set()

    db.run_commit_guard = guard

    def writer():
        with db.normal_write_transaction(run_id=run.identity.run_id) as connection:
            connection.execute("INSERT INTO requirement_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot.requirement_snapshot_id, snapshot.schema_version,
                 snapshot.semantic_contract_version, snapshot.requirement_hash,
                 snapshot.model_dump_json(), 1.0))
            holding.set()
            assert release.wait(10)
            if rollback:
                raise RuntimeError("writer rollback")

    reader = pw.PostWriteSqliteDatabase(db.path)
    reader_connect = reader._connect
    reader._connect = lambda: ObservedConnection(reader_connect(), attempted=attempted, acquired=acquired)
    observed = []

    def final_read():
        with reader.finalization_read_transaction() as connection:
            observed.append(connection)
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            return pw.RequirementStore(reader).resolve_exact(ref, connection=connection)

    with ThreadPoolExecutor(max_workers=2) as pool:
        old_writer = pool.submit(writer)
        try:
            assert holding.wait(10) and guard_passed.is_set()
            _finalize(lifecycle, run)  # CAS returns and releases lifecycle owner before reader starts.
            # Negative control: a genuine ordinary WAL BEGIN obtains OLD committed state.
            with reader.read_transaction() as ordinary:
                assert ordinary.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                assert ordinary.execute("SELECT COUNT(*) FROM requirement_snapshots").fetchone()[0] == 0
                fenced = pool.submit(final_read)
                assert attempted.wait(10)
                assert not acquired.is_set() and not fenced.done()
                # Public lifecycle access succeeds while finalizer waits: no lifecycle lock spans fence.
                assert lifecycle.load_run(run.identity.run_id).status.value == "finalizing"
                release.set()
                if rollback:
                    with pytest.raises(RuntimeError, match="writer rollback"):
                        old_writer.result(timeout=10)
                    with pytest.raises(pw.PostWritePersistenceIntegrityError, match="missing"):
                        fenced.result(timeout=10)
                else:
                    old_writer.result(timeout=10)
                    assert fenced.result(timeout=10) == snapshot
                # Ordinary transaction still holds its old WAL snapshot after commit.
                assert ordinary.execute("SELECT COUNT(*) FROM requirement_snapshots").fetchone()[0] == 0
                assert acquired.is_set() and len(observed) == 1
        finally:
            release.set()
    with pytest.raises(LifecycleCurrentnessError):
        with db.normal_write_transaction(run_id=run.identity.run_id):
            pytest.fail("late I3 writer bypassed FINALIZING")


READ_TARGETS = {
    "ArtifactStateStore": "resolve_exact", "RequirementStore": "resolve_exact",
    "PostWriteEntryStore": "resolve_exact", "DocumentBindingStore": None,
    "QualityDeliveryContractStore": "resolve_exact", "CorrectionPolicyStore": "resolve_exact",
    "GateDecisionStore": "resolve_exact", "ControlDecisionStore": "resolve_exact",
    "CorrectionAttemptStore": "resolve_exact",
}


def _read_for_sample(owner, name, args, result, connection):
    if isinstance(owner, pw.DocumentBindingStore):
        method = ("resolve_assembly_manifest" if "assembly" in name else "resolve_document_evidence_snapshot")
        return getattr(owner, method)(result, connection=connection)
    if isinstance(owner, pw.ValidationStore):
        if name == "put_fact_if_absent_or_same":
            return owner.resolve_fact(result, connection=connection)
        if name == "put_completion_if_absent_or_same":
            return owner.resolve_completion(result, connection=connection)
        if name == "append_intermediate_if_absent_or_same":
            return owner.resolve_intermediate(result, connection=connection)
        if name == "finalize_validator_execution":
            return owner.resolve_validator_execution_record(result.validator_execution_ref, connection=connection)
        return owner.resolve_run_exact(args[0] if name == "transition_run" else result.validation_run_id
            if name == "create_run_with_event_if_absent_or_same" else args[0].validation_run_id,
            connection=connection)
    return getattr(owner, READ_TARGETS[type(owner).__name__])(result, connection=connection)


@pytest.mark.parametrize("key", SQL_KEYS)
def test_all_terminal_targets_exact_reads_borrow_same_connection(tmp_path, sql_samples, key, monkeypatch):
    db, owner, call = _sql_case(tmp_path, sql_samples, key, lambda run_id: None)
    result = call()  # Actual domain write using captured valid prerequisites, not fabricated JSON.
    args = sql_samples[key][2]
    before = _dump(db.path)
    statements = []
    with db.finalization_read_transaction() as connection:
        connection.set_trace_callback(statements.append)
        # Any recursive resolver that reopens even ONE connection fails mechanically.
        monkeypatch.setattr(db, "_connect", lambda: pytest.fail("exact final read reopened SQLite"))
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        actual = _read_for_sample(owner, sql_samples[key][1], args, result, connection)
        assert actual is not None
        assert connection.total_changes == 0
    assert any(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for sql in statements)
    assert _dump(db.path) == before


@pytest.mark.parametrize("sql", [
    "INSERT INTO post_write_metadata VALUES ('forbidden', 'value')",
    "UPDATE post_write_metadata SET value='forbidden'",
    "DELETE FROM post_write_metadata",
    "CREATE TABLE forbidden (id INTEGER)",
])
def test_fence_sqlite_enforces_read_only(tmp_path, sql):
    db = pw.PostWriteSqliteDatabase(tmp_path / "pw.sqlite3")
    before = _dump(db.path)
    with db.finalization_read_transaction() as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(sql)
        assert connection.total_changes == 0
    assert _dump(db.path) == before


def test_exception_releases_reservation_without_mutation(tmp_path):
    db = pw.PostWriteSqliteDatabase(tmp_path / "pw.sqlite3")
    before = _dump(db.path)
    with pytest.raises(RuntimeError, match="abort read"):
        with db.finalization_read_transaction() as connection:
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            raise RuntimeError("abort read")
    with db.normal_write_transaction() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 0
    with db.finalization_read_transaction():
        pass
    assert _dump(db.path) == before


def test_busy_fails_closed_using_existing_concurrency_error(tmp_path):
    db = pw.PostWriteSqliteDatabase(tmp_path / "pw.sqlite3", busy_timeout_ms=1)
    with db.write_transaction(immediate=True):
        with pytest.raises(pw.PostWriteConcurrencyError):
            with db.finalization_read_transaction():
                pytest.fail("reservation acquired despite an existing writer")
    with db.finalization_read_transaction():
        pass


def test_two_finalization_fences_serialize_and_release(tmp_path):
    db = pw.PostWriteSqliteDatabase(tmp_path / "pw.sqlite3")
    second = pw.PostWriteSqliteDatabase(db.path)
    attempted, acquired = threading.Event(), threading.Event()
    connect = second._connect
    second._connect = lambda: ObservedConnection(connect(), attempted=attempted, acquired=acquired)

    def read():
        with second.finalization_read_transaction() as connection:
            return connection.execute("SELECT COUNT(*) FROM post_write_metadata").fetchone()[0]

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.finalization_read_transaction():
            future = pool.submit(read)
            assert attempted.wait(10)
            assert not acquired.is_set() and not future.done()
        assert future.result(timeout=10) == 1
        assert acquired.is_set()


def test_exact_ref_and_hash_checks_preserved_under_fence(tmp_path):
    db = pw.PostWriteSqliteDatabase(tmp_path / "pw.sqlite3")
    ref = _requirement_ref(db)
    bad_ref = ref.model_copy(update={"requirement_hash": "f" * 64})
    store = pw.RequirementStore(db)
    with db.finalization_read_transaction() as connection:
        with pytest.raises(pw.PostWritePersistenceIntegrityError, match="mismatch"):
            store.resolve_exact(bad_ref, connection=connection)
    with db.write_transaction(immediate=True) as connection:
        connection.execute("UPDATE requirement_snapshots SET requirement_hash=?", ("f" * 64,))
    for fenced in (False, True):
        if fenced:
            with db.finalization_read_transaction() as connection:
                with pytest.raises(pw.PostWritePersistenceIntegrityError, match="mismatch"):
                    store.resolve_exact(ref, connection=connection)
        else:
            with pytest.raises(pw.PostWritePersistenceIntegrityError, match="mismatch"):
                store.resolve_exact(ref)


def test_fence_has_no_lifecycle_or_business_mutation_calls():
    source = inspect.getsource(pw.PostWriteSqliteDatabase.finalization_read_transaction)
    assert 'immediate=True' in source
    tree = ast.parse(textwrap.dedent(source))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names.intersection({'transition_run', '_append', 'normal_write_transaction', 'put_if_absent_or_same'})


@pytest.mark.parametrize("indeterminate", [False, True])
@pytest.mark.parametrize("damage", [None, "missing", "corrupt"])
def test_completion_nested_proof_chain_uses_fenced_connection(tmp_path, monkeypatch, indeterminate, damage):
    from backend.tests.test_multi_agent_v3_consistency_proof_contract import _proof_gate
    fx = _proof_gate(tmp_path, indeterminate=indeterminate)
    db, store = fx["database"], fx["validation_store"]
    completion = fx["completion"]
    item = completion.items[0]
    proof = (item.indeterminate_proof_refs if indeterminate else item.positive_proof_refs)[0]
    if damage:
        with db.write_transaction(immediate=True) as connection:
            if damage == "missing":
                connection.execute("DELETE FROM validation_execution_events WHERE event_id=?", (proof.event_id,))
            else:
                connection.execute("UPDATE validation_execution_events SET event_json='{}' WHERE event_id=?", (proof.event_id,))
    statements = []
    with db.finalization_read_transaction() as connection:
        connection.set_trace_callback(statements.append)
        monkeypatch.setattr(db, "_connect", lambda: pytest.fail("nested proof read reopened connection"))
        if damage:
            with pytest.raises(pw.PostWritePersistenceIntegrityError):
                store.resolve_completion(store._completion_ref(completion), connection=connection)
        else:
            assert store.resolve_completion(store._completion_ref(completion), connection=connection) == completion
            assert store.list_facts_for_run(completion.validation_run_id, connection=connection) == ()
        assert connection.total_changes == 0
    assert any("FROM validator_execution_records" in sql for sql in statements)
    assert any("FROM validation_execution_events" in sql for sql in statements)
