"""Finite local crash cuts at the current Target publication/artifact owners.

These are isolated native stores. They do not publish a production document.
"""
from pathlib import Path
import pytest
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.schemas.post_write import PublicationReuseState
from test_post_write_release_publication import _release_fixture, _publish

@pytest.mark.parametrize('cut',['after_payload_before_fence','after_proof_before_manifest','after_manifest_before_response'])
def test_target_release_exact_native_cut_reopen_and_replay(tmp_path,cut):
    def crash(stage):
        if stage==cut:raise OSError('local cut '+cut)
    fx=_release_fixture(tmp_path,fault_injector=crash)
    _,key=fx['service'].publication_identity(fx['request'])
    manifest_ref=fx['service']._manifest_ref(key)
    with pytest.raises(OSError,match='local cut'):_publish(fx)
    reopened=FileArtifactStore(fx['publication_store'].root)
    if cut!='after_manifest_before_response':
        with pytest.raises(FileNotFoundError):reopened.get_json(manifest_ref,task_id='task-wave12')
    else:
        before=reopened.get_json(manifest_ref,task_id='task-wave12')
        fx['service'].fault_injector=None
        restored=_publish(fx)
        assert restored.reuse_state is PublicationReuseState.EXACT_REQUEST_REPLAY
        assert reopened.get_json(manifest_ref,task_id='task-wave12')==before

@pytest.mark.parametrize('after_replace',[False,True])
def test_file_artifact_native_temp_replace_cut_has_no_partial_visibility(tmp_path,monkeypatch,after_replace):
    import apps.enterprise_document.services.agent_rag_v1.artifact_store as owner
    store=FileArtifactStore(tmp_path/'artifact');stable='artifact://local/cut'
    real_replace=owner.os.replace
    def crash(source,target):
        assert Path(source).is_file()
        if after_replace:real_replace(source,target)
        raise OSError('local replace response cut')
    with monkeypatch.context() as patch:
        patch.setattr(owner.os,'replace',crash)
        with pytest.raises(OSError,match='replace response cut'):
            store.put_json({'body':'complete'},artifact_type='local',schema_version='local_v1',created_by='offline',task_id='local',artifact_ref=stable)
    reopened=FileArtifactStore(store.root)
    if after_replace:assert reopened.get_json(stable,task_id='local')=={'body':'complete'}
    else:
        with pytest.raises(FileNotFoundError):reopened.get_json(stable,task_id='local')
    assert not list(store.root.rglob('*.tmp'))
    first=reopened.put_json({'body':'complete'},artifact_type='local',schema_version='local_v1',created_by='offline',task_id='local',artifact_ref=stable)
    assert reopened.get_json(first.artifact_ref,task_id='local')=={'body':'complete'}
