"""Real Target FINALIZING/Assessment/SUCCESS chain, isolating I9 publisher."""
import pytest
from contextlib import ExitStack
from unittest.mock import patch
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from backend.tests.bca014_i7_fixtures import make_section


def target_fixture(tmp_path, monkeypatch):
    fx = make_section(tmp_path)
    post = fx['post_write']
    post._i8_test_fixture = fx
    source = fx['arguments']['source_sections'][0]
    invocation = post.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
    subject = invocation.writer_subject_ref
    request = dict(task_id=post.lifecycle_service.load_run_identity(subject.run_id).task_id,
        run_id=subject.run_id, document_id=subject.document_id,
        project_input=ProjectInputSchema.model_validate(invocation.model_dump(mode='json')['unified_writer_input_snapshot']['project_input']),
        section_execution_results=(source,))
    monkeypatch.setattr(post, '_publish_target_release', lambda **kwargs: None)
    return post, request


def stopped_at_barrier(tmp_path, monkeypatch):
    post, request = target_fixture(tmp_path, monkeypatch)
    finish = post._finish_finalization
    monkeypatch.setattr(post, '_finish_finalization', lambda *a, **k: (_ for _ in ()).throw(OSError('crash-C')))
    with pytest.raises(Exception):
        post.complete_target_post_write(**request)
    monkeypatch.setattr(post, '_finish_finalization', finish)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']).status is RS.FINALIZING
    return post, request


def no_normal_writes(post):
    stack = ExitStack()
    for method in ('materialize_sections_and_document', '_section', '_finalize_document_output_and_control',
            '_complete_validation', '_control'):
        stack.enter_context(patch.object(post, method, side_effect=AssertionError('FINALIZING normal write '+method)))
    stack.enter_context(patch.object(post.database, 'normal_write_transaction', side_effect=AssertionError('normal SQLite write')))
    return stack


def test_real_target_full_chain_binds_barrier_assessment_success(tmp_path, monkeypatch):
    post, request = target_fixture(tmp_path, monkeypatch)
    result = post.complete_target_post_write(**request)
    lifecycle = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    assert lifecycle.status is RS.SUCCESS
    assert lifecycle.run_finalization_barrier.final_control_decision_ref.exact_ref_payload == result.final_document.control_decision_ref.model_dump(mode='json')
    assessment = post.assessment_store.resolve_exact(result.run_completion_assessment_ref)
    assert len(assessment.post_write_mutation_closure_refs) == 1
    assert assessment.post_write_mutation_closure_refs[0].exact_ref_payload == lifecycle.run_finalization_barrier_ref.model_dump(mode='json')


@pytest.mark.parametrize('cut', ['C', 'D', 'E', 'F'])
def test_crash_resume_same_barrier_no_normal_writes(tmp_path, monkeypatch, cut):
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    lifecycle = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    if cut in {'D', 'E', 'F'}:
        if cut == 'D':
            target, name = post.research_runtime, 'validate_evidence_package_binding_current'
        elif cut == 'E':
            target, name = post.assessment_store, 'put_if_absent_or_same'
        else:
            target, name = post.lifecycle_service, 'transition_run'
        with patch.object(target, name, side_effect=OSError('crash-'+cut)):
            with no_normal_writes(post), pytest.raises(OSError):
                post.complete_target_post_write(**request)
        assert post.lifecycle_service.load_run_lifecycle(request['run_id']) == lifecycle
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
    recreated = TargetPostWriteComposition.create(runs_dir=post.runs_dir,
        research_runtime=post.research_runtime, writer_execution_store=post.writer_execution_store,
        budget_manager_provider=post.budget_manager_provider,
        lifecycle_service=RunAttemptLifecycleService(post.lifecycle_service.path), require_run_commit_guard=True)
    for name in ('claim_quality_projection', 'faithfulness_fth03', 'citation_grounding',
            'requirement_adherence', 'coverage_composition', 'consistency_composition', 'memory_composition'):
        if hasattr(post, name):
            setattr(recreated, name, getattr(post, name))
    monkeypatch.setattr(recreated, '_publish_target_release', lambda **kw: None)
    with no_normal_writes(recreated):
        result = recreated.complete_target_post_write(**request)
    assert result.terminal_run.status is RS.SUCCESS
    assert result.terminal_run.run_finalization_barrier == lifecycle.run_finalization_barrier
    assert result.terminal_run.lifecycle_revision == lifecycle.lifecycle_revision+1


def test_success_cas_uses_only_immutable_assessment_under_lifecycle_lock(tmp_path, monkeypatch):
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    transition = post.lifecycle_service.transition_run
    seen = []
    def immutable_transition(run_id, revision, status, **kwargs):
        if status is not RS.SUCCESS:
            return transition(run_id, revision, status, **kwargs)
        with ExitStack() as stack:
            for owner, name in ((post.currentness_resolver, 'prove'), (post, '_terminalization_snapshot'),
                    (post.database, '_connect'), (post.research_runtime, 'resolve_progress'),
                    (post.writer_execution_store, 'resolve_writer_execution'),
                    (post.claim_quality_projection.claim_runtime, 'resolve_claim'),
                    (post.claim_quality_projection.paragraph_runtime, 'load_manifest'),
                    (post.artifact_store, 'resolve_exact')):
                stack.enter_context(patch.object(owner, name, side_effect=AssertionError('mutable owner at SUCCESS')))
            result = transition(run_id, revision, status, **kwargs)
            seen.append(True)
            return result
    monkeypatch.setattr(post.lifecycle_service, 'transition_run', immutable_transition)
    result = post.complete_target_post_write(**request)
    assert result.terminal_run.status is RS.SUCCESS and seen == [True]


def test_technical_currentness_failure_retains_finalizing_and_resumes(tmp_path, monkeypatch):
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    before = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    with patch.object(post.claim_quality_projection.paragraph_runtime.artifact_runtime, 'resolve_active', side_effect=OSError('temporary read')):
        with pytest.raises(OSError):
            post.complete_target_post_write(**request)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']) == before
    assert not post.assessment_store.path.exists()
    assert post.complete_target_post_write(**request).terminal_run.status is RS.SUCCESS


def test_supplied_request_mismatch_fails_closed_without_abort(tmp_path, monkeypatch):
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    before = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    with pytest.raises(PostWritePersistenceIntegrityError, match='Document differs'):
        post.complete_target_post_write(**{**request, 'document_id': 'foreign-document'})
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']) == before


def test_project_input_mismatch_retains_exact_barrier(tmp_path,monkeypatch):
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    post,request=stopped_at_barrier(tmp_path,monkeypatch)
    before=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    changed=request['project_input'].model_copy(update={'user_query':'a different valid ProjectInput request'})
    with no_normal_writes(post),pytest.raises(PostWritePersistenceIntegrityError,match='RunIdentity'):
        post.complete_target_post_write(**{**request,'project_input':changed})
    assert post.lifecycle_service.load_run_lifecycle(request['run_id'])==before


def test_unselected_late_writer_history_does_not_move_selected_proof(tmp_path,monkeypatch):
    from apps.enterprise_document.schemas.pre_write_writer import WriterExecutionResultSchema,WriterExecutionStatus
    post,request=target_fixture(tmp_path,monkeypatch)
    store=post.writer_execution_store;source=request['section_execution_results'][0]
    selected_key=source.writer_execution_ref.logical_execution_key
    late_key=selected_key.model_copy(update={'write_instance_id':'i8-unselected-late'})
    store.reserve_writer_execution(late_key,semantic_fingerprint='a'*64)
    assert store.mark_writer_dispatched(late_key,semantic_fingerprint='a'*64)
    enter=post._enter_finalization
    def late_history(*a,**k):
        current=enter(*a,**k)
        result=WriterExecutionResultSchema(logical_execution_key=late_key,status=WriterExecutionStatus.SUCCESS,
            artifact=source.section.model_copy(update={'content':'late unselected historical text'}),
            generation_provenance={'generation_class':'normal_write'})
        store.persist_writer_result(late_key,semantic_fingerprint='a'*64,result=result)
        return current
    monkeypatch.setattr(post,'_enter_finalization',late_history)
    result=post.complete_target_post_write(**request)
    assert result.terminal_run.status is RS.SUCCESS
    assert store.resolve_writer_execution(source.writer_execution_ref).artifact.content==source.section.content
