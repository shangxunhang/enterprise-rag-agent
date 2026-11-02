"""351 real PostgreSQL acceptance. Set LANE351_TEST_PG_DSN to an isolated test DB.

Each test creates its own schema; no existing schemas/data are touched. Drivers
are provided by the durable-memory extra. No provider calls or SQLite substitutes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from apps.enterprise_document.services.agent_rag_v1.memory_composition import (
    TargetDurableMemoryComposition, DurableMemoryCapabilityError,
)
from apps.enterprise_document.services.agent_rag_v1.memory_runtime import DurableMemoryRuntime
from apps.enterprise_document.services.agent_rag_v1.memory_store import PostgresDurableMemoryStore
from test_step7_durable_memory_persistence import _commit, _production
from test_multi_agent_v3_fact_snapshot_runtime import _runtime


@pytest.fixture
def pg_store():
    dsn = os.getenv('LANE351_TEST_PG_DSN')
    if not dsn:
        pytest.skip('explicit isolated PostgreSQL test DSN required')
    import psycopg
    from psycopg.conninfo import make_conninfo
    schema = 'lane351_' + uuid.uuid4().hex
    with psycopg.connect(dsn) as connection:
        connection.execute('CREATE SCHEMA ' + schema)
    scoped = make_conninfo(dsn, options='-c search_path='+schema, connect_timeout=3)
    store = PostgresDurableMemoryStore(database_url=scoped)
    store.validate_schema()
    return store


def _memory(fact, store):
    return TargetDurableMemoryComposition(required=True, runtime=DurableMemoryRuntime(store=store),
        fact_snapshot_runtime=fact, lifecycle_service=fact.artifact_runtime.lifecycle_service)


def _rows(store):
    with store._transaction() as cursor:
        cursor.execute('SELECT memory_id, record_json FROM durable_memory_record ORDER BY memory_id')
        return dict(cursor.fetchall())


@pytest.mark.parametrize('scenario', ['VERIFIED', 'CONFLICTED'])
def test_postgres_production_six_types_and_current_writer(tmp_path_factory, monkeypatch, pg_store, scenario):
    _production(tmp_path_factory, monkeypatch, scenario, live=pg_store)


def test_postgres_capability_missing_config_schema_and_connection_fail_closed(pg_store):
    from psycopg.conninfo import make_conninfo
    settings = SimpleNamespace(memory_database_url='', memory_database_statement_timeout_ms=3000,
                               memory_database_schema_version='memory_v1')
    assert TargetDurableMemoryComposition.from_settings(required=False, settings=settings).runtime is None
    with pytest.raises(DurableMemoryCapabilityError, match='CONFIGURATION_REQUIRED'):
        TargetDurableMemoryComposition.from_settings(required=True, settings=settings)
    settings.memory_database_url = make_conninfo(pg_store.database_url, options='-c search_path=absent_351')
    with pytest.raises(DurableMemoryCapabilityError, match='CAPABILITY_UNAVAILABLE') as schema_error:
        TargetDurableMemoryComposition.from_settings(required=True, settings=settings)
    assert 'schema is unavailable' in str(schema_error.value.__cause__)
    settings.memory_database_url = make_conninfo(pg_store.database_url, port=1, connect_timeout=1)
    with pytest.raises(DurableMemoryCapabilityError, match='CAPABILITY_UNAVAILABLE') as connection_error:
        TargetDurableMemoryComposition.from_settings(required=True, settings=settings)
    assert 'unable to connect' in str(connection_error.value.__cause__)


def test_postgres_transaction_rollback_and_concurrent_dedup(tmp_path, pg_store):
    from concurrent.futures import ThreadPoolExecutor
    fact = _runtime(tmp_path)
    source = _commit(fact)
    memory = _memory(fact, pg_store)
    # PostgreSQL abort after record insert, before transaction COMMIT.
    with pg_store._transaction() as cursor:
        cursor.execute("CREATE FUNCTION reject_memory() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '351 injected rollback'; END $$")
        cursor.execute('CREATE TRIGGER reject_memory AFTER INSERT ON durable_memory_record FOR EACH ROW EXECUTE FUNCTION reject_memory()')
    with pytest.raises(DurableMemoryCapabilityError, match='MEMORY_PERSISTENCE_UNAVAILABLE'):
        memory.project_artifact(source)
    assert not _rows(pg_store)
    with pg_store._transaction() as cursor:
        cursor.execute('DROP TRIGGER reject_memory ON durable_memory_record')
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda _: _memory(fact, pg_store).project_artifact(source), range(4)))
    assert all(item == refs[0] for item in refs) and len(_rows(pg_store)) == 1


def test_postgres_process_crash_reopen_terminal_zero_writes_and_corruption(tmp_path, pg_store):
    fact = _runtime(tmp_path)
    source = _commit(fact)
    # A fresh interpreter has no workflow checkpoint or Memory objects. Source
    # owners reopen their canonical ledgers; deterministic reconciliation alone
    # completes source-committed/Memory-not-yet-written work.
    child = tmp_path/'recover.py'
    child.write_text('''import json, os, sys
from pathlib import Path
from apps.enterprise_document.services.agent_rag_v1 import ArtifactRuntime, FactSnapshotRuntime
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.memory_store import PostgresDurableMemoryStore
from apps.enterprise_document.services.agent_rag_v1.memory_runtime import DurableMemoryRuntime
from apps.enterprise_document.services.agent_rag_v1.memory_composition import TargetDurableMemoryComposition
root=Path(sys.argv[1])
life=RunAttemptLifecycleService(root/'lifecycle.jsonl')
fact=FactSnapshotRuntime(artifact_runtime=ArtifactRuntime(root/'artifact-runtime.jsonl',lifecycle_service=life),source_binding_resolver=lambda b: b.source_version_ref.startswith('source-version://'))
memory=TargetDurableMemoryComposition(required=True,runtime=DurableMemoryRuntime(store=PostgresDurableMemoryStore(database_url=os.environ['LANE351_CHILD_DSN'],ensure_schema=False)),fact_snapshot_runtime=fact,lifecycle_service=life)
refs=memory.reconcile_run('run-fact-1')
(root/'refs.json').write_text(json.dumps([r.model_dump(mode='json') for r in refs]))
os._exit(71)
''', encoding='utf-8')
    env = {**os.environ, 'LANE351_CHILD_DSN': pg_store.database_url}
    def reopen():
        result = subprocess.run([sys.executable,str(child),str(tmp_path)],env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode == 71, result.stderr
        return json.loads((tmp_path/'refs.json').read_text())
    assert not _rows(pg_store)
    first = reopen()
    before = _rows(pg_store)
    assert len(before) == 1
    assert reopen() == first and _rows(pg_store) == before
    memory = _memory(fact, pg_store)
    # Real canonical Run terminal transition; prohibit any SQL record/index DML.
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
    run = fact.artifact_runtime.lifecycle_service.load_run('run-fact-1')
    fact.artifact_runtime.lifecycle_service.transition_run('run-fact-1',run.lifecycle_revision,RunLifecycleStatus.FAILED)
    with pg_store._transaction() as cursor:
        cursor.execute('DELETE FROM memory_selector_index')
        cursor.execute("CREATE FUNCTION deny_memory_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'terminal Memory write'; END $$")
        for table in ('durable_memory_record','memory_selector_index'):
            cursor.execute(f'CREATE TRIGGER terminal_no_write BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION deny_memory_write()')
    assert reopen() == first and _rows(pg_store) == before
    assert memory.project_artifact(source)[0].model_dump(mode='json') == first[0]
    with pg_store._transaction() as cursor:
        cursor.execute('DROP TRIGGER terminal_no_write ON durable_memory_record')
        cursor.execute("UPDATE durable_memory_record SET record_json=jsonb_set(record_json,'{projection,projection_payload}', '{\"forged\":true}'::jsonb)")
    source_bytes = fact.artifact_runtime.ledger_path.read_bytes()
    with pytest.raises(Exception):
        memory.reconcile_run('run-fact-1')
    assert fact.artifact_runtime.ledger_path.read_bytes() == source_bytes


def test_required_postwrite_stale_checkpoint_reopen_is_read_only(tmp_path, monkeypatch, pg_store):
    import test_multi_agent_v3_workflow_recovery as recovery
    from application.mainline_service import MainlineApplicationService
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
    from dataclasses import replace
    original_run = MainlineApplicationService.run
    original_complete = TargetPostWriteComposition.complete_target_post_write
    composed = []
    def run(self, **kwargs):
        kwargs['settings'] = replace(kwargs['settings'],memory_database_url=pg_store.database_url)
        return original_run(self,**kwargs,durable_memory_required=True)
    def complete(self, **kwargs):
        if not composed:
            composed.append(self.memory_composition)
        else:
            old = composed[0]
            self.memory_composition = TargetDurableMemoryComposition(required=True,
                runtime=DurableMemoryRuntime(store=PostgresDurableMemoryStore(database_url=pg_store.database_url,ensure_schema=False)),
                fact_snapshot_runtime=old.facts,research_runtime=old.research,claim_runtime=old.claims,
                lifecycle_service=self.lifecycle_service)
            with pg_store._transaction() as cursor:
                cursor.execute("CREATE FUNCTION reject_replay_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'terminal rewrite'; END $$")
                for table in ('durable_memory_record','memory_selector_index'):
                    cursor.execute(f'CREATE TRIGGER no_replay_write BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_replay_write()')
        before = _rows(pg_store)
        result = original_complete(self,**kwargs)
        if len(composed) > 1:
            assert _rows(pg_store) == before
        composed.append(self.memory_composition)
        return result
    monkeypatch.setattr(MainlineApplicationService,'run',run)
    monkeypatch.setattr(TargetPostWriteComposition,'complete_target_post_write',complete)
    recovery.test_w13_reopen_from_stale_checkpoint_reuses_canonical_release_without_budget_reconsume(tmp_path)
    assert len(composed) == 3


def test_required_application_recovery_preserves_capability_and_committed_paragraphs(tmp_path, monkeypatch, pg_store):
    import test_p1_application_recovery as recovery
    from application.mainline_service import MainlineApplicationService
    from dataclasses import replace
    original_run = MainlineApplicationService.run
    original_recover = MainlineApplicationService.recover_run
    original_reconcile = TargetDurableMemoryComposition.reconcile_run
    reconciliations = []
    def run(self, **kwargs):
        kwargs['settings'] = replace(kwargs['settings'],memory_database_url=pg_store.database_url)
        return original_run(self,**kwargs,durable_memory_required=True)
    def recover(self, **kwargs):
        kwargs['settings'] = replace(kwargs['settings'],memory_database_url=pg_store.database_url)
        return original_recover(self,**kwargs)
    def reconcile(self, run_id):
        assert self.required and self.runtime is not None
        reconciliations.append(run_id)
        return original_reconcile(self,run_id)
    monkeypatch.setattr(MainlineApplicationService,'run',run)
    monkeypatch.setattr(MainlineApplicationService,'recover_run',recover)
    monkeypatch.setattr(TargetDurableMemoryComposition,'reconcile_run',reconcile)
    recovery.test_recover_run_resumes_after_committed_paragraph_without_duplication(tmp_path)
    assert reconciliations and _rows(pg_store)
