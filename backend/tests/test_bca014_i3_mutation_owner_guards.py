"""Real owner transactions/ledgers, frozen A/B races, and exact historical reads."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import wraps
import ast
import inspect
from pathlib import Path
import sqlite3
import threading

import pytest

from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetVectorSchema
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    LifecycleCurrentnessError, RunAttemptLifecycleService,
)
from apps.enterprise_document.services.post_write import persistence as pw
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, WriterExecutionState
from backend.tests.test_bca014_finalizing_lifecycle import _finalize
from backend.tests.test_multi_agent_v3_lifecycle_commit import _service, _running_run
from backend.tests.test_gate_v2_pre_write_execution_control import _writer_key, _success_result
from backend.tests.test_multi_agent_v3_budget_runtime import _limits, _ref, NOW


def _live(tmp_path, run_id="run-1"):
    lifecycle = _service(tmp_path)
    return lifecycle, _running_run(lifecycle, run_id=run_id)


def _dump(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.iterdump())


class _Connection:
    def __init__(self, real, *, begin=None, commit=None):
        self.real, self.begin, self.commit_hook = real, begin, commit

    def __getattr__(self, name):
        return getattr(self.real, name)

    def __enter__(self):
        self.real.__enter__()
        return self

    def __exit__(self, *args):
        return self.real.__exit__(*args)

    def execute(self, sql, *args):
        if sql == "BEGIN IMMEDIATE" and self.begin is not None:
            self.begin.set()
        return self.real.execute(sql, *args)

    def commit(self):
        if self.commit_hook is not None:
            self.commit_hook()
        return self.real.commit()


@pytest.mark.parametrize("mutation", ["reserve", "dispatch"])
def test_writer_transaction_acquired_before_finalizing_check(tmp_path, mutation):
    lifecycle, run = _live(tmp_path)
    store = SQLitePreWriteExecutionStore(tmp_path / "writer.db", run_commit_guard=lifecycle.assert_run_committable,
                                        require_run_commit_guard=True)
    key = _writer_key()
    if mutation == "dispatch":
        store.reserve_writer_execution(key, semantic_fingerprint="a" * 64)
    before = _dump(store.database_path)
    blocker = sqlite3.connect(store.database_path)
    blocker.execute("BEGIN IMMEDIATE")
    begin = threading.Event()
    connect = store._connect
    store._connect = lambda: _Connection(connect(), begin=begin)
    method = store.reserve_writer_execution if mutation == "reserve" else store.mark_writer_dispatched
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(method, key, semantic_fingerprint="a" * 64)
        try:
            assert begin.wait(10)
            _finalize(lifecycle, run)
        finally:
            blocker.rollback()
            blocker.close()
        with pytest.raises(LifecycleCurrentnessError):
            future.result(timeout=10)
    assert _dump(store.database_path) == before


@pytest.mark.parametrize("mutation", ["reserve", "dispatch"])
def test_writer_prebarrier_transaction_is_drainable_and_late_writer_rejects(tmp_path, mutation):
    lifecycle, run = _live(tmp_path)
    store = SQLitePreWriteExecutionStore(tmp_path / "writer.db", run_commit_guard=lifecycle.assert_run_committable,
                                        require_run_commit_guard=True)
    key = _writer_key()
    if mutation == "dispatch":
        store.reserve_writer_execution(key, semantic_fingerprint="a" * 64)
    before = _dump(store.database_path)
    entered, release, late_begin = threading.Event(), threading.Event(), threading.Event()
    connect = store._connect

    def hold_commit():
        entered.set()
        assert release.wait(10)

    store._connect = lambda: _Connection(connect(), commit=hold_commit)
    late = SQLitePreWriteExecutionStore(store.database_path, run_commit_guard=lifecycle.assert_run_committable,
                                       require_run_commit_guard=True)
    late_connect = late._connect
    late._connect = lambda: _Connection(late_connect(), begin=late_begin)
    method = store.reserve_writer_execution if mutation == "reserve" else store.mark_writer_dispatched
    late_method = late.reserve_writer_execution if mutation == "reserve" else late.mark_writer_dispatched
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(method, key, semantic_fingerprint="a" * 64)
        try:
            assert entered.wait(10)
            _finalize(lifecycle, run)
            blocked = pool.submit(late_method, key, semantic_fingerprint="a" * 64)
            assert late_begin.wait(10)
            assert not blocked.done()
        finally:
            release.set()
        writer.result(timeout=10)
        with pytest.raises(LifecycleCurrentnessError):
            blocked.result(timeout=10)
    assert _dump(store.database_path) != before
    store._connect = connect
    assert store.get_writer_execution(key).state is (
        WriterExecutionState.RESERVED if mutation == "reserve" else WriterExecutionState.DISPATCHED
    )


def _budget(tmp_path, lifecycle):
    return BudgetManager.canonical(task_id="task_1", run_id="run_1", limits=_limits(),
        max_elapsed_seconds=60, storage_path=tmp_path / "budget.jsonl", clock=lambda: NOW,
        run_commit_guard=lifecycle.assert_run_committable, require_run_commit_guard=True)


def test_one_finalizing_fixture_blocks_all_budget_normal_mutations(tmp_path):
    lifecycle, run = _live(tmp_path, "run_1")
    manager = _budget(tmp_path, lifecycle)
    manager.reserve(_ref("active"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=0)
    state = manager.snapshot()
    before = manager.storage_path.read_bytes()
    _finalize(lifecycle, run)
    calls = [
        lambda: manager.reserve(_ref("new"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=1),
        lambda: manager.consume("active", CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=1),
        lambda: manager.settle("active", CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=1),
        lambda: manager.release("active", expected_revision=1),
        lambda: manager.start_draining(expected_revision=1),
    ]
    for call in calls:
        with pytest.raises(LifecycleCurrentnessError):
            call()
        assert manager.storage_path.read_bytes() == before
        assert manager.snapshot() == state
    # Exact historical account reopen is a read; no account creation allowed.
    assert _budget(tmp_path, lifecycle).snapshot() == state


class _ObservedLock:
    def __init__(self, real, observed):
        self.real, self.observed = real, observed

    def __enter__(self):
        self.observed.set()
        self.real.acquire()
        return self

    def __exit__(self, *args):
        self.real.release()


@pytest.mark.parametrize("mutation", ["reserve", "consume", "settle", "release", "drain"])
def test_budget_real_file_owner_prebarrier_commit_then_late_reject(tmp_path, mutation):
    lifecycle, run = _live(tmp_path, "run_1")
    manager = _budget(tmp_path, lifecycle)
    if mutation in {"consume", "settle", "release"}:
        manager.reserve(_ref("active"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=0)
    before = manager.snapshot()
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    append = manager._canonical_log.append_serialized

    def held(value):
        entered.set()
        assert release.wait(10)
        append(value)

    manager._canonical_log.append_serialized = held
    late = _budget(tmp_path, lifecycle)
    late._lock = _ObservedLock(late._lock, attempted)
    calls = {
        "reserve": lambda: manager.reserve(_ref("new"), CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=0),
        "consume": lambda: manager.consume("active", CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=1),
        "settle": lambda: manager.settle("active", CanonicalBudgetVectorSchema(logical_model_calls=1), expected_revision=1),
        "release": lambda: manager.release("active", expected_revision=1),
        "drain": lambda: manager.start_draining(expected_revision=0),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(calls[mutation])
        try:
            assert entered.wait(10)
            _finalize(lifecycle, run)
            rejected = pool.submit(late.start_draining, expected_revision=before.revision)
            assert attempted.wait(10)
            assert not rejected.done()
        finally:
            release.set()
        writer.result(timeout=10)
        with pytest.raises(LifecycleCurrentnessError):
            rejected.result(timeout=10)
    assert manager.snapshot().revision == before.revision + 1


def test_finalizing_cannot_create_a_new_canonical_budget_account(tmp_path):
    lifecycle, run = _live(tmp_path, "run_1")
    _finalize(lifecycle, run)
    with pytest.raises(LifecycleCurrentnessError):
        _budget(tmp_path, lifecycle)
    path = tmp_path / "budget.jsonl"
    assert not path.exists() or path.read_bytes() == b""


SQL_PATHS = {
    "ArtifactStateStore": ("create_initial_if_absent_or_same", "append_next_version"),
    "RequirementStore": ("put_if_absent_or_same",),
    "PostWriteEntryStore": ("put_if_absent_or_same",),
    "DocumentBindingStore": ("put_assembly_manifest_if_absent_or_same", "put_document_evidence_snapshot_if_absent_or_same"),
    "QualityDeliveryContractStore": ("put_if_absent_or_same",),
    "CorrectionPolicyStore": ("put_if_absent_or_same",),
    "ValidationStore": ("create_run_with_event_if_absent_or_same", "transition_run", "append_execution_event_if_absent_or_same",
        "put_fact_if_absent_or_same", "put_completion_if_absent_or_same", "append_intermediate_if_absent_or_same", "finalize_validator_execution"),
    "GateDecisionStore": ("put_if_absent_or_same",),
    "ControlDecisionStore": ("put_if_absent_or_same",),
    "CorrectionAttemptStore": ("put_if_absent_or_same",),
}
SQL_KEYS = tuple(f"{owner}.{method}" for owner, methods in SQL_PATHS.items() for method in methods)


@pytest.fixture(scope="module")
def sql_samples(tmp_path_factory):
    """Capture valid calls AND their real pre-commit database prerequisites.

    Reusing existing domain fixtures avoids fabricated schemas/SQL. Each test
    clones a pre-call backup, so the tested call really INSERTs/UPDATEs rather
    than merely resolving a row that already exists.
    """
    from backend.tests import test_post_write_structural_currentness as structural
    from backend.tests import test_post_write_section_document_lifecycle as document
    from backend.tests import test_post_write_validation_foundation as validation
    from backend.tests import test_post_write_correction_control as control
    from apps.enterprise_document.schemas.post_write.control import CorrectionAttemptRecordSchema
    from apps.enterprise_document.schemas.pre_write_writer import WriterGenerationClass
    from core.hashing import canonical_sha256
    root = tmp_path_factory.mktemp("i3-sql-samples")
    samples = {}
    with pytest.MonkeyPatch.context() as patch:
        for owner, methods in SQL_PATHS.items():
            cls = getattr(pw, owner)
            for name in methods:
                original = getattr(cls, name)
                key = f"{owner}.{name}"

                def wrap(original=original, key=key, cls=cls, name=name):
                    @wraps(original)
                    def capture(self, *args, **kwargs):
                        backup = root / (key + ".sqlite3")
                        if key not in samples:
                            with sqlite3.connect(self.database.path) as source, sqlite3.connect(backup) as dest:
                                source.backup(dest)
                        result = original(self, *args, **kwargs)
                        if key not in samples and _dump(backup) != _dump(self.database.path):
                            samples[key] = (cls, name, args, kwargs, backup)
                        return result
                    return capture
                patch.setattr(cls, name, wrap())

        structural.test_artifact_expected_parent_cas_and_same_hash_noop(root / "structural")
        document.test_mixed_normal_and_fallback_sections_assemble_without_document_generation_class(root / "document")
        validation.test_run_transition_cas_and_terminal_prerequisites(root / "validation")
        validation.test_intermediate_is_event_backed_and_exact_hash_resolvable(root / "intermediate")
        validation.test_validator_terminal_finalization_validates_refs_and_exact_pair(root / "validator")
        db, store, _, run = validation._created_run(root / "event")
        event = store._event(validation_run_id=run.validation_run_id, event_id="i3-event", sequence=2,
                             event_kind="VALIDATOR_STARTED", payload={}, occurred_at_epoch=2000.0)
        store.append_execution_event_if_absent_or_same(event)
        fx = control._fixture(root / "control", generation_class=WriterGenerationClass.NORMAL_WRITE,
                              quality="PASS")
        decision = control._decide(fx)
        material = dict(schema_version="correction_attempt_projection_v1", projection_id="i3-correction",
            run_id="run-1", target_artifact_ref=fx["artifact_ref"].model_dump(mode="json"),
            control_decision_ref=decision.as_ref().model_dump(mode="json"), embedded_authorization_id="auth-i3",
            embedded_authorization_hash="a" * 64, paragraph_repair_authorization_ref=None,
            canonical_result_refs=[], outcome="TECHNICAL_FAILURE")
        record = CorrectionAttemptRecordSchema(**material, projection_hash=canonical_sha256(material), created_at_epoch=0)
        pw.CorrectionAttemptStore(fx["database"]).put_if_absent_or_same(record)
    assert set(samples) == set(SQL_KEYS), set(SQL_KEYS) - set(samples)
    return samples


def _sql_case(tmp_path, sql_samples, key, guard):
    cls, name, args, kwargs, backup = sql_samples[key]
    path = tmp_path / "post_write.db"
    with sqlite3.connect(backup) as source, sqlite3.connect(path) as dest:
        source.backup(dest)
    db = pw.PostWriteSqliteDatabase(path, run_commit_guard=guard, require_run_commit_guard=True)
    owner = cls(db)
    def call():
        with db.run_write_guard_context(_sample_run_id(sql_samples, key)):
            return getattr(owner, name)(*args, **kwargs)
    return db, owner, call


def _sample_run_id(samples, key):
    args = samples[key][2]
    if key.startswith("PostWriteEntryStore."):
        return args[0].identity.run_id
    if key.startswith("CorrectionAttemptStore."):
        return args[0].run_id
    return "run-1"


@pytest.mark.parametrize("key", SQL_KEYS)
def test_postwrite_all_actual_mutation_paths_finalizing_first_no_rows_change(tmp_path, sql_samples, key):
    lifecycle, run = _live(tmp_path, _sample_run_id(sql_samples, key))
    db, owner, call = _sql_case(tmp_path, sql_samples, key, lifecycle.assert_run_committable)
    before = _dump(db.path)
    _finalize(lifecycle, run)
    with pytest.raises(LifecycleCurrentnessError):
        call()
    assert _dump(db.path) == before


@pytest.mark.parametrize("key", SQL_KEYS)
def test_postwrite_all_actual_mutation_paths_drainable_prebarrier_commit(tmp_path, sql_samples, key):
    lifecycle, run = _live(tmp_path, _sample_run_id(sql_samples, key))
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()

    def guard(run_id):
        lifecycle.assert_run_committable(run_id)
        entered.set()
        assert release.wait(10)

    db, owner, call = _sql_case(tmp_path, sql_samples, key, guard)
    before = _dump(db.path)
    late = pw.PostWriteSqliteDatabase(db.path, run_commit_guard=lifecycle.assert_run_committable,
                                    require_run_commit_guard=True)
    connect = late._connect
    late._connect = lambda: _Connection(connect(), begin=attempted)

    def late_call():
        with late.run_write_guard_context(run.identity.run_id), late.normal_write_transaction():
            pytest.fail("late writer was allowed past its lifecycle fence")

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(call)
        try:
            assert entered.wait(10)
            _finalize(lifecycle, run)
            rejected = pool.submit(late_call)
            assert attempted.wait(10)
            assert not rejected.done()
        finally:
            release.set()
        writer.result(timeout=10)
        with pytest.raises(LifecycleCurrentnessError):
            rejected.result(timeout=10)
    assert _dump(db.path) != before


def test_postwrite_strict_missing_context_and_nested_identity_fail_closed(tmp_path):
    lifecycle, run = _live(tmp_path)
    db = pw.PostWriteSqliteDatabase(tmp_path / "pw.db", run_commit_guard=lifecycle.assert_run_committable,
                                    require_run_commit_guard=True)
    before = _dump(db.path)
    with pytest.raises(pw.PostWritePersistenceIntegrityError, match="missing exact"):
        with db.normal_write_transaction():
            pytest.fail("missing run identity accepted")
    with db.run_write_guard_context("run-1"):
        with pytest.raises(pw.PostWritePersistenceIntegrityError, match="crossed Run"):
            with db.run_write_guard_context("another-run"):
                pass
    assert _dump(db.path) == before


def test_target_guard_constructors_reject_missing_callback(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.repair_authorization_runtime import ParagraphRepairAuthorizationStore
    for construct in [
        lambda: SQLitePreWriteExecutionStore(tmp_path / "writer.db", require_run_commit_guard=True),
        lambda: pw.PostWriteSqliteDatabase(tmp_path / "pw.db", require_run_commit_guard=True),
        lambda: ParagraphRepairAuthorizationStore(tmp_path / "repair.jsonl", require_run_commit_guard=True),
        lambda: BudgetManager.canonical(task_id="task_1", run_id="run_1", limits=_limits(), max_elapsed_seconds=60,
                                       storage_path=tmp_path / "budget.jsonl", require_run_commit_guard=True),
    ]:
        with pytest.raises(ValueError, match="requires a Run commit guard"):
            construct()


def test_no_lifecycle_to_owner_lock_path_added_and_all_sql_owners_fenced():
    from apps.enterprise_document.services.agent_rag_v1 import lifecycle_runtime
    source = inspect.getsource(lifecycle_runtime.RunAttemptLifecycleService.transition_run)
    assert "Budget" not in source and "BEGIN" not in source
    # Existing immutable assessment resolver remains an explicitly deferred I8
    # obligation; I3 does not add any mutable owner acquisition to lifecycle.
    for owner, methods in SQL_PATHS.items():
        for name in methods:
            source = inspect.getsource(getattr(getattr(pw, owner), name))
            assert "normal_write_transaction" in source or (owner == "DocumentBindingStore" and "self._put" in source)
    tree = ast.parse(inspect.getsource(pw))
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "finalization_read_fence" for node in ast.walk(tree))
