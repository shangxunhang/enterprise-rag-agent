"""Actual Target startup identity checks and read-only storage GC closure."""
from unittest.mock import patch
import pytest
from bootstrap.supervisor_factory import SupervisorFactory
from backend.tests.test_gate_v2_pre_write_config_bootstrap import _build_p5_supervisor
from backend.tests.bca014_i9_fixtures import terminal_case, publish, manifests
from backend.tests.test_bca014_i9_release_replay_scope import rehashed_manifest


def test_actual_target_startup_shares_all_release_owners(tmp_path):
    supervisor,_,_=_build_p5_supervisor(tmp_path)
    try:
        engine=supervisor.workflow_engine
        post=engine.stage_service.target_post_write_composition
        service=post.release_service;guard=service.input_guard
        assert post.lifecycle_service is engine.lifecycle_service
        assert guard.run_completion_assessment_resolver==post.assessment_store.resolve_exact
        assert guard.run_lifecycle_revision_resolver==post._resolve_run_lifecycle_revision
        assert service.release_commit_fence.database is post.database
        assert service.publication_store is post.release_store
        assert guard.artifact_state_store is post.artifact_store
        assert guard.control_store is post.control_store
        assert guard.currentness_resolver is post.currentness_resolver
        assert guard.artifact_usability_resolver is post.usability_resolver
        assert guard.output_conformance_verifier is post.output_conformance_verifier
        assert guard.output_conformance_verifier.artifact_store is post.output_store
    finally:supervisor.close()


@pytest.mark.parametrize('missing',['publication_store','database','assessment','lifecycle',
    'artifact_state_store','control_store','currentness_resolver','artifact_usability_resolver','output_conformance_verifier','output_store'])
def test_startup_poisoned_release_owner_fails_closed(tmp_path,monkeypatch,missing):
    validate=SupervisorFactory._validate_target_startup
    def broken(**kwargs):
        post=kwargs['target_post_write_composition'];service=post.release_service
        if missing=='publication_store':service.publication_store=object()
        elif missing=='database':service.release_commit_fence.database=object()
        elif missing=='assessment':service.input_guard.run_completion_assessment_resolver=lambda ref:None
        elif missing=='lifecycle':service.input_guard.run_lifecycle_revision_resolver=lambda *a:None
        elif missing=='output_store':service.input_guard.output_conformance_verifier.artifact_store=object()
        else:setattr(service.input_guard,missing,object())
        return validate(**kwargs)
    monkeypatch.setattr(SupervisorFactory,'_validate_target_startup',staticmethod(broken))
    with pytest.raises(RuntimeError,match='Release'):_build_p5_supervisor(tmp_path)


def test_real_gc_closure_requires_exact_integral_release_and_never_publishes(tmp_path):
    captured={};validate=SupervisorFactory._validate_target_startup
    def capture(**kw):
        validate(**kw);captured.update(kw)
    with patch.object(SupervisorFactory,'_validate_target_startup',staticmethod(capture)):
        case=terminal_case(tmp_path)
    post,request,result,release_request=case
    resolver=captured['storage_lifecycle_service'].catalog.scope_terminal_resolver
    scope='run:'+request['run_id']
    assert captured['target_post_write_composition'] is post
    with patch.object(post.release_service,'publish',side_effect=AssertionError('GC published')):
        assert resolver(scope) is False
        history=[post.lifecycle_service.load_run_lifecycle_revision(request['run_id'],r) for r in range(result.terminal_run.lifecycle_revision+1)]
        for record in history:
            if record.status.value in {'running','finalizing'}:
                with patch.object(post.lifecycle_service,'load_run_lifecycle',return_value=record):assert resolver(scope) is False
        impossible=result.terminal_run.model_copy(update={'run_completion_assessment_ref':None})
        with patch.object(post.lifecycle_service,'load_run_lifecycle',return_value=impossible):assert resolver(scope) is False
    published=publish(case)
    refs=tuple(post.release_store.list_references())
    with patch.object(post.release_service,'publish',side_effect=AssertionError('GC published')):
        assert resolver(scope) is True
        for part in (published.manifest_artifact,published.manifest.release_input_proof_artifact,published.manifest.release_payload_artifact):
            path=post.release_store._path_for_ref(part.artifact_ref);raw=path.read_bytes()
            try:
                path.write_bytes(b'corrupt')
                assert resolver(scope) is False
            finally:path.write_bytes(raw)
        for changes in ({'lifecycle_revision':result.terminal_run.lifecycle_revision+1},
                {'run_completion_assessment_ref':result.run_completion_assessment_ref.model_copy(update={'assessment_sha256':'a'*64})}):
            with patch.object(post.lifecycle_service,'load_run_lifecycle',return_value=result.terminal_run.model_copy(update=changes)):
                assert resolver(scope) is False
        assert resolver('run:foreign') is False
        assert resolver(scope) is True
    assert refs==tuple(post.release_store.list_references())
    assert len(manifests(post))==1


def test_real_gc_rejects_rehashed_logical_scope_without_repair_or_store_mutation(tmp_path):
    captured = {}
    validate = SupervisorFactory._validate_target_startup
    def capture(**kwargs):
        validate(**kwargs)
        captured.update(kwargs)
    with patch.object(SupervisorFactory, '_validate_target_startup', staticmethod(capture)):
        case = terminal_case(tmp_path)
    post, request, _, _ = case
    first = publish(case)
    resolver = captured['storage_lifecycle_service'].catalog.scope_terminal_resolver
    scope = 'run:' + request['run_id']
    with patch.object(post.release_service, 'publish', side_effect=AssertionError('GC published')), \
            patch.object(post, 'complete_target_post_write', side_effect=AssertionError('GC repaired')), \
            patch.object(post.database, 'normal_write_transaction', side_effect=AssertionError('GC wrote')), \
            patch.object(post.currentness_resolver, 'prove', side_effect=AssertionError('GC currentness')), \
            patch.object(post.release_store, 'put_json', side_effect=AssertionError('GC wrote JSON')), \
            patch.object(post.release_store, 'put_bytes', side_effect=AssertionError('GC wrote bytes')):
        assert resolver(scope) is True
        with rehashed_manifest(post, first, document_logical_id='document-logical:TAMPERED'):
            before = {p: p.read_bytes() for p in post.release_store.root.rglob('*') if p.is_file()}
            assert resolver(scope) is False
            assert before == {p: p.read_bytes() for p in post.release_store.root.rglob('*') if p.is_file()}
        assert resolver(scope) is True
