"""Draft only until the ordered D2 gate; Target local native publication cuts."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from threading import Lock

import pytest

from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.post_write import PostWriteReleasePublicationService, PostWriteReleaseCommitFence
from apps.enterprise_document.schemas.post_write import PublicationReuseState
from backend.tests.test_post_write_release_publication import _release_fixture, _publish


def _envelopes(root):
    return {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*.json')}


def _cold_owner(fx):
    prior = fx['service']
    return PostWriteReleasePublicationService(
        publication_store=FileArtifactStore(fx['publication_store'].root),
        input_guard=fx['guard'],
        release_commit_fence=PostWriteReleaseCommitFence(fx['post_write']['env']['database']),
        publisher=prior.publisher, release_contract_version=prior.release_contract_version,
        clock=prior.clock)


def _publish_cold(fx, owner):
    return owner.publish(request=fx['request'],
        run_completion_assessment_ref=fx['assessment_ref'],
        run_lifecycle_revision=fx['terminal'].lifecycle_revision,
        active_document_binding_supplier=lambda: fx['post_write']['active_projection'])


def _record(tmp_path, name, value):
    with (tmp_path / (name + '.json')).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)


@pytest.mark.parametrize('cut,expected_count', [
    ('after_payload_before_fence', 1), ('after_proof_before_manifest', 2),
    ('after_manifest_before_response', 3)])
def test_target_native_cut_independent_cold_owner_counts_and_exact_bytes(tmp_path, monkeypatch, cut, expected_count):
    def fault(stage):
        if stage == cut:
            raise OSError('authored local response loss ' + cut)
    fx = _release_fixture(tmp_path / 'native', fault_injector=fault)
    publication_root = fx['publication_store'].root
    calls = []
    original = FileArtifactStore.put_bytes
    def counted(store, content, **kwargs):
        if store.root == publication_root:
            calls.append({'artifact_ref': kwargs.get('artifact_ref'),
                'artifact_type': kwargs['artifact_type'], 'payload_sha256': sha256(content).hexdigest()})
        return original(store, content, **kwargs)
    monkeypatch.setattr(FileArtifactStore, 'put_bytes', counted)
    before_run = fx['lifecycle'].load_run('run-wave11')
    original_terminal = fx['lifecycle'].load_run_lifecycle_revision('run-wave11', fx['terminal'].lifecycle_revision)
    with pytest.raises(OSError, match='authored local response loss'):
        _publish(fx)
    assert len(calls) == expected_count
    committed = _envelopes(publication_root)
    assert len(committed) == expected_count
    cold_count_before = len(calls)
    cold = _cold_owner(fx)
    assert cold is not fx['service'] and cold.publication_store is not fx['publication_store']
    _, key = cold.publication_identity(fx['request'])
    manifest_ref = cold._manifest_ref(key)
    if cut != 'after_manifest_before_response':
        with pytest.raises(FileNotFoundError):
            cold.publication_store.get_json(manifest_ref, task_id=fx['request'].task_id)
        disposition = 'INDETERMINATE_NO_COMMITTED_MANIFEST_NO_REDISPATCH'
        restored = None
    else:
        manifest = cold.publication_store.get_json(manifest_ref, task_id=fx['request'].task_id)
        def forbidden(*args, **kwargs):
            raise AssertionError('exact committed replay must not generate fresh input proof')
        monkeypatch.setattr(cold.input_guard, 'resolve_preflight_output', forbidden)
        monkeypatch.setattr(cold.input_guard, 'validate_new_publication', forbidden)
        restored = _publish_cold(fx, cold)
        assert restored.reuse_state is PublicationReuseState.EXACT_REQUEST_REPLAY
        assert restored.manifest.model_dump(mode='json') == manifest
        for reference in (restored.manifest.release_payload_artifact, restored.manifest.release_input_proof_artifact):
            actual_ref, content = cold.publication_store.get_bytes_with_reference(
                reference.artifact_ref, task_id=reference.task_id, run_id=reference.run_id)
            assert actual_ref == reference and actual_ref.content_hash == 'sha256:' + sha256(content).hexdigest()
        disposition = 'RECOVERED_EXACT_COMMITTED_MANIFEST'
    assert len(calls) == expected_count and _envelopes(publication_root) == committed
    assert fx['lifecycle'].load_run('run-wave11') == before_run
    assert fx['lifecycle'].load_run_lifecycle_revision('run-wave11', fx['terminal'].lifecycle_revision) == original_terminal
    _record(tmp_path, 'native-publication-cut', {'cut': cut, 'request': fx['request'].model_dump(mode='json'),
        'puts': calls, 'committed_envelope_hashes': committed, 'cold_puts': len(calls) - cold_count_before,
        'disposition': disposition, 'manifest': restored.manifest.model_dump(mode='json') if restored else None,
        'run': before_run.model_dump(mode='json'), 'proof_scope': 'local existing native owners; no real publication'})


def test_two_independent_publishers_share_native_lock_and_single_manifest(tmp_path, monkeypatch):
    fx = _release_fixture(tmp_path / 'native')
    owners = [_cold_owner(fx), _cold_owner(fx)]
    calls, lock = [], Lock()
    original = FileArtifactStore.put_bytes
    def counted(store, content, **kwargs):
        if store.root == fx['publication_store'].root:
            with lock:
                calls.append({'ref': kwargs.get('artifact_ref'), 'sha256': sha256(content).hexdigest()})
        return original(store, content, **kwargs)
    monkeypatch.setattr(FileArtifactStore, 'put_bytes', counted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda owner: _publish_cold(fx, owner), owners))
    assert sorted(row.reuse_state.value for row in results) == sorted([
        PublicationReuseState.NEW_COMMIT.value, PublicationReuseState.EXACT_REQUEST_REPLAY.value])
    assert results[0].manifest == results[1].manifest
    assert len(calls) == 3 and len(_envelopes(fx['publication_store'].root)) == 3
    reopened = _cold_owner(fx)
    before = _envelopes(fx['publication_store'].root)
    cold_count_before = len(calls)
    assert _publish_cold(fx, reopened).manifest == results[0].manifest
    assert len(calls) == 3 and _envelopes(fx['publication_store'].root) == before
    _record(tmp_path, 'native-independent-publication-concurrency', {'puts': calls,
        'envelopes': before, 'request': fx['request'].model_dump(mode='json'),
        'manifest': results[0].manifest.model_dump(mode='json'),
        'owner_count': 2, 'cold_replay_puts': len(calls) - cold_count_before})
