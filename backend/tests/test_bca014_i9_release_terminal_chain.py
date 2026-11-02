"""Exact V2 terminal authority, publication eligibility and dedicated fence."""
from contextlib import contextmanager
import json
from unittest.mock import patch
import pytest
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from apps.enterprise_document.schemas.post_write.release import PublicationReuseState as Reuse
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService, LifecycleCurrentnessError
from apps.enterprise_document.services.post_write.release import PostWriteReleaseError
from core.hashing import canonical_sha256
from backend.tests.bca014_i9_fixtures import terminal_case, publish, changed_request, manifests


@pytest.fixture(scope='module')
def case(tmp_path_factory):
    return terminal_case(tmp_path_factory.mktemp('i9-terminal'))


def target_request(case, target):
    return changed_request(case[0], case[3], publication_target_id='target://i9/'+target)


def call(case, request, **changes):
    post, _, result, _ = case
    args=dict(request=request, run_completion_assessment_ref=result.run_completion_assessment_ref,
        run_lifecycle_revision=result.terminal_run.lifecycle_revision,
        active_document_binding_supplier=lambda:result.final_document.active_projection)
    args.update(changes)
    return post.release_service.publish(**args)


def test_real_v2_chain_release_hashes_and_assembly_preserved(case, monkeypatch):
    post, _, result, request = case
    dump = lambda: _dump(post)
    rows = dump()
    hold = post.release_service.release_commit_fence.hold
    entered=[]; calls=[]
    @contextmanager
    def fence():
        with hold() as conn:
            assert conn.in_transaction
            entered.append(True)
            yield conn
    def supplier():
        assert entered == [True]
        calls.append(True)
        return result.final_document.active_projection
    monkeypatch.setattr(post.release_service.release_commit_fence,'hold',fence)
    with patch.object(post.database,'normal_write_transaction',side_effect=AssertionError('normal transaction')), \
            patch.object(post.lifecycle_service,'assert_run_committable',side_effect=AssertionError('RUNNING guard')):
        published=call(case,request,active_document_binding_supplier=supplier)
    assert calls == [True]
    assert rows == dump()
    # SQLite page bytes may change for connection bookkeeping; canonical rows cannot.
    manifest=published.manifest
    assert published.reuse_state is Reuse.NEW_COMMIT
    lifecycle=post.lifecycle_service.load_run_lifecycle_revision(request.run_id,manifest.run_lifecycle_revision)
    assessment=post.assessment_store.resolve_exact(manifest.run_completion_assessment_ref)
    _,raw=post.release_store.get_json_with_reference(manifest.release_input_proof_artifact.artifact_ref,
        task_id=request.task_id, run_id=request.run_id)
    assert lifecycle.schema_version == 'run_lifecycle_record_v2'
    assert assessment.assessment_contract_version == 'run_completion_assessment_v2'
    assert raw['resolved_run_lifecycle_record_sha256'] == canonical_sha256(lifecycle.model_dump(mode='json'))
    assert lifecycle.run_finalization_barrier.final_control_decision_ref == assessment.post_write_control_decision_ref
    assert assessment.post_write_mutation_closure_refs[0].exact_ref_payload == lifecycle.run_finalization_barrier_ref.model_dump(mode='json')
    assert manifest.post_write_control_decision_ref == result.final_document.control_decision_ref
    control=post.control_store.resolve_exact(manifest.post_write_control_decision_ref)
    assert assessment.document_artifact_ref.exact_ref_payload == manifest.document_artifact_ref.model_dump(mode='json')
    assert assessment.document_post_write_entry_ref.exact_ref_payload == control.input_manifest.post_write_entry_ref.model_dump(mode='json')
    assert manifest.assembly_input_manifest_ref == control.input_manifest.assembly_input_manifest_ref
    assembly=post.binding_store.resolve_assembly_manifest(manifest.assembly_input_manifest_ref)
    assert assembly.schema_version == 'assembly_input_manifest_v2'
    assert assembly.assembly_contract_version == 'document_assembly_v2'
    assert raw['assembly_input_manifest_ref'] == assembly.as_ref().model_dump(mode='json')
    assert raw['run_lifecycle_status'] == 'success'
    assert raw['release_time_currentness_proof']['currentness_state'] == 'CURRENT'
    assert raw['release_time_artifact_usability_proof']['state'] == 'TRUSTED_USABLE'
    assert raw['output_conformance_proof']['state'] == 'CONFORMING'


def _dump(post):
    with post.database.read_transaction() as conn:
        return '\n'.join(conn.iterdump())


@pytest.mark.parametrize('status',[RS.RUNNING,RS.FINALIZING,RS.FAILED,RS.CANCELLED,RS.PARTIAL_SUCCESS])
def test_all_non_success_canonical_revisions_reject(case,tmp_path,status):
    post,_,result,request=case
    lifecycle=post.lifecycle_service
    history=[lifecycle.load_run_lifecycle_revision(request.run_id,r) for r in range(result.terminal_run.lifecycle_revision+1)]
    running=next(r for r in history if r.status is RS.RUNNING)
    finalizing=next(r for r in history if r.status is RS.FINALIZING)
    if status in {RS.RUNNING,RS.FINALIZING}:
        record=running if status is RS.RUNNING else finalizing
        owner=lifecycle
    else:
        # Replay canonical preterminal history into an isolated lifecycle owner,
        # then perform a legal real transition for each negative terminal state.
        path=tmp_path/'lifecycle.jsonl'
        events=[json.loads(line) for line in lifecycle.path.read_text().splitlines()]
        selected=[e for e in events if e['entity_type']!='run' or e['record']['lifecycle_revision']<=running.lifecycle_revision]
        path.write_text('\n'.join(json.dumps(e) for e in selected)+'\n')
        owner=RunAttemptLifecycleService(path)
        record=owner.transition_run(request.run_id,running.lifecycle_revision,status)
    req=target_request(case,status.value)
    with patch.object(post.release_service.input_guard,'run_lifecycle_revision_resolver',owner.load_run_lifecycle_revision):
        with pytest.raises(PostWriteReleaseError):
            call(case,req,run_lifecycle_revision=record.lifecycle_revision)
    assert all(r.artifact_ref!=post.release_service._manifest_ref(post.release_service.publication_identity(req)[1]) for r in manifests(post))


@pytest.mark.parametrize('version',['run_attempt_lifecycle_event_v1','mixed','unknown','run-v1'])
def test_release_cannot_relax_canonical_event_parser(case,tmp_path,version):
    post,_,result,request=case
    events=[json.loads(line) for line in post.lifecycle_service.path.read_text().splitlines()]
    if version=='run-v1':
        next(e for e in events if e['entity_type']=='run')['record']['schema_version']='run_lifecycle_record_v1'
    elif version=='mixed':
        events[0]['schema_version']='run_attempt_lifecycle_event_v1'
    else:
        for e in events:e['schema_version']=version
    path=tmp_path/'bad.jsonl';path.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    owner=RunAttemptLifecycleService(path)
    with patch.object(post.release_service.input_guard,'run_lifecycle_revision_resolver',owner.load_run_lifecycle_revision):
        with pytest.raises(PostWriteReleaseError):call(case,target_request(case,version))


def test_exact_revision_and_foreign_assessment_rejected(case):
    post,_,result,_=case
    with pytest.raises(PostWriteReleaseError):
        call(case,target_request(case,'wrong-revision'),run_lifecycle_revision=result.terminal_run.lifecycle_revision+1)
    foreign=result.run_completion_assessment_ref.model_copy(update={'run_id':'foreign'})
    with pytest.raises(PostWriteReleaseError):
        call(case,target_request(case,'wrong-assessment'),run_completion_assessment_ref=foreign)


def test_normal_postwrite_write_rejected_after_success_release_still_allowed(case):
    post,_,result,request=case
    rows=_dump(post)
    with pytest.raises(LifecycleCurrentnessError):
        with post.database.run_write_guard_context(request.run_id):
            with post.database.normal_write_transaction():pass
    assert _dump(post)==rows
    assert call(case,target_request(case,'after-normal-rejection')).reuse_state is Reuse.NEW_COMMIT


def test_stale_new_supplier_is_called_once_and_no_manifest(case):
    post,_,result,_=case
    record=post.artifact_store.resolve_exact(result.final_document.artifact_state_ref)
    calls=[]
    def stale():
        calls.append(True)
        return result.final_document.active_projection.model_copy(update={'artifact_state_ref':record.parent_artifact_ref})
    req=target_request(case,'stale')
    with pytest.raises(PostWriteReleaseError):call(case,req,active_document_binding_supplier=stale)
    assert calls==[True]
    assert post.release_service._restore_existing(request=req,key=post.release_service.publication_identity(req)[1]) is None
