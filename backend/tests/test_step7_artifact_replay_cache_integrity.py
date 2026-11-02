"""Warm repeated reads retain canonical Artifact integrity and live currentness."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import (
    ArtifactRuntimeIntegrityError, ArtifactVersionNotFound,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError
from core.persistence.interprocess_lock import InterProcessFileLock
from test_multi_agent_v3_artifact_runtime import _commit, _lifecycle, _logical, _runtime


def _warm(tmp_path):
    life = _lifecycle(tmp_path)
    runtime = _runtime(tmp_path, life)
    logical = _logical()
    version = _commit(runtime, logical, operation_id='version-1', payload={'text': 'v1'})
    assert runtime.read_version(version) == ({'text': 'v1'}, version)
    assert runtime.resolve_active(logical).head_revision == 0
    return life, runtime, logical, version


def test_warm_reads_observe_commit_and_promotion_from_another_process(tmp_path):
    _, runtime, logical, _ = _warm(tmp_path)
    child = '''from pathlib import Path
import sys
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from test_multi_agent_v3_artifact_runtime import _commit, _logical, _promote, _runtime
root=Path(sys.argv[1]); runtime=_runtime(root,RunAttemptLifecycleService(root/'lifecycle.jsonl'))
logical=_logical(); version=_commit(runtime,logical,operation_id='version-2',payload={'text':'v2'})
_promote(runtime,logical,version,operation_id='promote-2',expected_head_revision=0,expected_active_version_id=None)
'''
    completed = subprocess.run([sys.executable, '-B', '-c', child, str(tmp_path)],
                               capture_output=True, text=True, timeout=30,
                               env={**os.environ, 'PYTHONPATH': os.pathsep.join((
                                   str(Path(__file__).resolve().parent), os.environ.get('PYTHONPATH', ''),
                               ))})
    assert completed.returncode == 0, completed.stderr
    head = runtime.resolve_active(logical)
    assert head.head_revision == 1
    assert runtime.read_version(head.active_version_ref)[0] == {'text': 'v2'}


def test_warm_reads_revalidate_same_size_same_mtime_rewrite(tmp_path):
    _, runtime, logical, _ = _warm(tmp_path)
    path = runtime.ledger_path
    original = path.read_bytes()
    metadata = path.stat()
    changed = original.replace(b'artifact_runtime_event_v1', b'artifact_runtime_event_v9')
    assert changed != original and len(changed) == len(original)
    path.write_bytes(changed)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    with pytest.raises(ArtifactRuntimeIntegrityError, match='schema version'):
        runtime.resolve_active(logical)
    path.write_bytes(original)
    assert runtime.resolve_active(logical).head_revision == 0


def test_warm_reads_reject_duplicate_committed_record_then_accept_repaired_history(tmp_path):
    _, runtime, logical, _ = _warm(tmp_path)
    original = runtime.ledger_path.read_bytes()
    runtime.ledger_path.write_bytes(original + original)
    with pytest.raises(ArtifactRuntimeIntegrityError, match='duplicate Artifact logical operation'):
        runtime.resolve_active(logical)
    runtime.ledger_path.write_bytes(original)
    assert runtime.resolve_active(logical).head_revision == 0


def test_warm_reads_recover_torn_tail_and_observe_valid_history_truncation(tmp_path):
    _, runtime, logical, version = _warm(tmp_path)
    original = runtime.ledger_path.read_bytes()
    with runtime.ledger_path.open('ab') as handle:
        handle.write(b'{"uncommitted":')
    assert runtime.read_version(version)[0] == {'text': 'v1'}
    assert runtime.ledger_path.read_bytes() == original
    runtime.ledger_path.write_bytes(b'')
    assert runtime.resolve_active(logical) is None
    with pytest.raises(ArtifactVersionNotFound):
        runtime.read_version(version)


def test_returned_replay_containers_are_isolated_and_leaves_are_frozen(tmp_path):
    _, runtime, logical, version = _warm(tmp_path)
    with runtime._lock, InterProcessFileLock(runtime.lock_path):
        state = runtime._load_state_unlocked()
    stored = next(iter(state.versions.values()))
    with pytest.raises(FrozenInstanceError):
        stored.result_ref = 'changed'
    with pytest.raises(ValidationError):
        stored.ref.content_hash = 'sha256:' + '0' * 64
    with pytest.raises(ValidationError):
        stored.ref.logical_ref.run_id = 'other-run'
    next(iter(state.version_ids_by_logical.values())).clear()
    for value in vars(state).values():
        value.clear()
    assert runtime.resolve_active(logical).head_revision == 0
    assert runtime.read_version(version) == ({'text': 'v1'}, version)
    second = _commit(runtime, logical, operation_id='version-2', payload={'text': 'v2'})
    assert runtime.read_version(second)[0] == {'text': 'v2'}


def test_warm_ledger_does_not_cache_physical_artifact_content(tmp_path):
    _, runtime, _, version = _warm(tmp_path)
    runtime.artifact_store._path_for_ref(version.content_ref).write_bytes(b'corrupt')
    with pytest.raises(ArtifactRuntimeIntegrityError, match='missing or corrupt'):
        runtime.read_version(version)


def test_warm_ledger_does_not_cache_live_run_commit_eligibility(tmp_path):
    life, runtime, logical, _ = _warm(tmp_path)
    current = life.load_run('run-1')
    life.transition_run('run-1', current.lifecycle_revision, RunLifecycleStatus.CANCELLED)
    before = runtime.ledger_path.read_bytes()
    with pytest.raises(LifecycleCurrentnessError):
        _commit(runtime, logical, operation_id='after-terminal', payload={'text': 'denied'})
    assert runtime.ledger_path.read_bytes() == before


def test_legacy_mutable_operation_scope_remains_isolated_between_reads(tmp_path):
    _, runtime, _, _ = _warm(tmp_path)
    event = json.loads(runtime.ledger_path.read_text(encoding='utf-8'))
    event['operation_ref']['operation_scope_ref'] = ['legacy-scope']
    runtime.ledger_path.write_text(json.dumps(event) + '\n', encoding='utf-8')
    with runtime._lock, InterProcessFileLock(runtime.lock_path):
        first = runtime._load_state_unlocked()
    first.all_operations['version-1'].operation_ref.operation_scope_ref.append('caller-mutation')
    with runtime._lock, InterProcessFileLock(runtime.lock_path):
        second = runtime._load_state_unlocked()
    assert second.all_operations['version-1'].operation_ref.operation_scope_ref == ['legacy-scope']
    assert json.loads(runtime.ledger_path.read_text(encoding='utf-8')) == event
