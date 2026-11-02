"""Post-SUCCESS physical retry/replay, durable recreation and identity locking."""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch
import pytest
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from apps.enterprise_document.schemas.post_write.release import PublicationReuseState as Reuse
from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import TargetPostWriteInterrupted
from core.persistence.interprocess_lock import InterProcessFileLock
from backend.tests.bca014_i9_fixtures import (
    make_target, terminal_case, changed_request, manifests, recreate, forbid_cognition)
from backend.tests.test_bca014_i9_release_terminal_chain import call, target_request


@pytest.mark.parametrize('cut',['G','after_payload_before_fence','after_proof_before_manifest','after_manifest_before_response'])
def test_real_target_crash_after_success_recreates_and_commits_once(tmp_path,cut):
    post,request=make_target(tmp_path)
    def fault(stage):
        if stage==cut:raise OSError('crash-'+cut)
    post.release_service.fault_injector=fault
    publish=post._publish_target_release
    def crash_g(**kw):
        assert post.lifecycle_service.load_run_lifecycle(request['run_id']).status is RS.SUCCESS
        raise OSError('crash-G')
    with patch.object(post,'_publish_target_release',crash_g if cut=='G' else publish):
        with pytest.raises(TargetPostWriteInterrupted) as exc:
            post.complete_target_post_write(**request)
    completed=exc.value.completion
    assert completed is not None and completed.published_release is None
    terminal=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    assert terminal.status is RS.SUCCESS
    assessment=post.assessment_store.resolve_exact(terminal.run_completion_assessment_ref)
    assert assessment.assessment_contract_version=='run_completion_assessment_v2'
    assert len(manifests(post)) == (1 if cut=='after_manifest_before_response' else 0)
    ledger=post.lifecycle_service.path.read_bytes(); assessments=post.assessment_store.path.read_bytes()
    rows=_dump(post)
    old_refs=tuple(post.release_store.list_references())
    new=recreate(post)
    assert new is not post and new.database is not post.database
    with forbid_cognition(new), patch.object(new.lifecycle_service,'transition_run',side_effect=AssertionError('reopen')):
        result=new.complete_target_post_write(**request)
        replay=new.complete_target_post_write(**request)
    assert result.terminal_run==terminal
    assert result.run_completion_assessment_ref==assessment.as_ref()
    assert result.published_release.reuse_state is (Reuse.EXACT_REQUEST_REPLAY if cut=='after_manifest_before_response' else Reuse.NEW_COMMIT)
    assert replay.published_release.reuse_state is Reuse.EXACT_REQUEST_REPLAY
    assert len(manifests(new))==1
    assert result.published_release.manifest==replay.published_release.manifest
    assert ledger==post.lifecycle_service.path.read_bytes()
    assert assessments==post.assessment_store.path.read_bytes()
    assert rows==_dump(new)
    if cut=='after_manifest_before_response':
        assert old_refs==tuple(new.release_store.list_references())


def _dump(post):
    with post.database.read_transaction() as conn:return '\n'.join(conn.iterdump())


@pytest.fixture(scope='module')
def case(tmp_path_factory):
    return terminal_case(tmp_path_factory.mktemp('i9-races'), orphan=True)


@pytest.mark.parametrize('different_request',[False,True])
def test_actual_identity_lock_serializes_concurrent_release(case,monkeypatch,different_request):
    post,_,result,_=case
    first_request=target_request(case,'concurrent-'+str(different_request))
    second_request=first_request
    if different_request:
        second_request=changed_request(post,first_request,
            post_write_control_decision_ref=post._i9_orphan_control)
        assert post.control_store.resolve_exact(post._i9_orphan_control).as_ref()==post._i9_orphan_control
    key=post.release_service.publication_identity(first_request)[1]
    entered=threading.Event();release=threading.Event();attempted=threading.Event()
    acquire=InterProcessFileLock.acquire
    def observed(lock):
        if lock.path==post.release_service._lock_path(key) and entered.is_set():attempted.set()
        return acquire(lock)
    def fault(stage):
        if stage=='after_proof_before_manifest':
            entered.set();assert release.wait(15)
    monkeypatch.setattr(InterProcessFileLock,'acquire',observed)
    monkeypatch.setattr(post.release_service,'fault_injector',fault)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(call,case,first_request)
        try:
            assert entered.wait(15)
            second=pool.submit(call,case,second_request)
            assert attempted.wait(15)
            assert not second.done()
        finally:release.set()
        a=first.result(timeout=15);b=second.result(timeout=15)
    assert a.reuse_state is Reuse.NEW_COMMIT
    assert b.reuse_state is (Reuse.ALREADY_PUBLISHED_SAME_OUTPUT if different_request else Reuse.EXACT_REQUEST_REPLAY)
    assert a.manifest==b.manifest
    assert sum(r.artifact_ref==post.release_service._manifest_ref(key) for r in manifests(post))==1


@pytest.mark.parametrize('cut',['after_payload_before_fence','after_proof_before_manifest'])
def test_orphan_files_do_not_skip_stale_retry_eligibility(case,monkeypatch,cut):
    from apps.enterprise_document.services.post_write.release import PostWriteReleaseError
    post,_,result,_=case
    req=target_request(case,'orphan-'+cut)
    def fault(stage):
        if stage==cut:raise OSError(cut)
    monkeypatch.setattr(post.release_service,'fault_injector',fault)
    with pytest.raises(OSError):call(case,req)
    monkeypatch.setattr(post.release_service,'fault_injector',None)
    record=post.artifact_store.resolve_exact(result.final_document.artifact_state_ref)
    stale=result.final_document.active_projection.model_copy(update={'artifact_state_ref':record.parent_artifact_ref})
    with pytest.raises(PostWriteReleaseError):call(case,req,active_document_binding_supplier=lambda:stale)
    assert post.release_service._restore_existing(request=req,key=post.release_service.publication_identity(req)[1]) is None
