"""Native owner mutations before barrier; exact reads after write closure."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from contextlib import ExitStack
import pytest
from core.hashing import canonical_sha256
from core.runtime.side_effects import LogicalOperationRef
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import FinalizationStaleError
from backend.tests.test_bca014_i8_finalization_recovery import target_fixture, stopped_at_barrier, no_normal_writes


def move_native_head(post, version, label):
    runtime = post.research_runtime.artifact_runtime
    logical = version.logical_ref
    payload, exact = runtime.read_version(version)
    head = runtime.resolve_active(logical)
    def operation(kind):
        return LogicalOperationRef(logical_operation_id='i8:'+label+':'+kind,
            operation_kind=kind, run_id=logical.run_id,
            operation_scope_ref='artifact:'+logical.logical_artifact_id)
    fresh = runtime.commit_version(logical, payload, version.content_schema_version,
        operation('ARTIFACT_VERSION_COMMIT'), canonical_sha256({'i8':label,'commit':True}), None)
    runtime.promote_active(logical, fresh, head.head_revision, head.active_version_ref.artifact_version_id,
        operation('ARTIFACT_ACTIVE_PROMOTION'), canonical_sha256({'i8':label,'promote':True}), None)


@pytest.mark.parametrize('owner', ['claim_result', 'paragraph', 'evidence', 'fact_snapshot', 'budget', 'artifact', 'pipeline', 'control_contract'])
def test_native_stale_before_barrier_aborts_original_revision(tmp_path, monkeypatch, owner):
    post, request = target_fixture(tmp_path, monkeypatch)
    enter = post._enter_finalization
    def mutate(run_id, barrier):
        source = request['section_execution_results'][0]
        if owner == 'claim_result':
            from backend.tests.test_bca014_i7_pre_gate_verification_currentness import reverify
            reverify(post._i8_test_fixture)
        elif owner == 'paragraph':
            move_native_head(post, source.section_paragraph_manifest.members[0], owner)
        elif owner == 'evidence':
            move_native_head(post, source.active_evidence_package_binding.version_ref, owner)
        elif owner == 'fact_snapshot':
            from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanContentSchema
            raw, _ = post.research_runtime.artifact_runtime.read_version(source.canonical_plan_binding.plan_version_ref)
            plan = SectionPlanContentSchema.model_validate(raw)
            move_native_head(post, plan.fact_snapshot_version_ref, owner)
        elif owner == 'budget':
            from apps.enterprise_document.schemas.agent_rag_v1.budgets import BudgetReservationRefSchema, CanonicalBudgetVectorSchema
            manager = post.budget_manager_provider(request['task_id'], run_id)
            manager.reserve(BudgetReservationRefSchema(reservation_id='i8-active-repair', task_id=request['task_id'],
                run_id=run_id,reservation_kind='PARAGRAPH_SEMANTIC_REPAIR',owner_occurrence_ref='repair:i8',
                scope_ref='section:'+source.section.section_id,operation_ref='repair:i8'),
                CanonicalBudgetVectorSchema(repair_attempts=1), expected_revision=manager.canonical_state().revision)
        elif owner == 'artifact':
            from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema
            control = post.control_store.resolve_exact(PostWriteControlDecisionRefSchema.model_validate(
                barrier.final_control_decision_ref.exact_ref_payload))
            record = post.artifact_store.resolve_exact(control.input_manifest.artifact_state_ref)
            post.artifact_service.append_next_version(expected_parent_ref=record.artifact_ref,
                canonical_payload_ref=record.canonical_payload_ref, origin=record.origin)
        elif owner == 'pipeline':
            monkeypatch.setattr(post.claim_quality_projection.verifier, 'POLICY_VERSION', 'i8-policy-changed')
        else:
            monkeypatch.setattr(post.control_service,'control_contract_version','i8-control-contract-changed')
        return enter(run_id, barrier)
    monkeypatch.setattr(post, '_enter_finalization', mutate)
    with pytest.raises(Exception) as failure:
        post.complete_target_post_write(**request)
    root = failure.value
    while root.__cause__ is not None:
        root = root.__cause__
    # Public transport can wrap the stale result; lifecycle proves the actual outcome.
    lifecycle = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    assert lifecycle.status is RS.RUNNING, repr(failure.value)
    assert lifecycle.run_finalization_barrier is None
    assert not post.assessment_store.path.exists()


def test_two_same_barrier_finalizers_share_one_assessment(tmp_path, monkeypatch):
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    before = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    ready, release = Event(), Event()
    original = post.assessment_store.put_if_absent_or_same
    calls = []
    def rendezvous(record):
        calls.append(record)
        if len(calls) == 2:
            ready.set(); release.set()
        assert release.wait(30)
        return original(record)
    monkeypatch.setattr(post.assessment_store, 'put_if_absent_or_same', rendezvous)
    with no_normal_writes(post), ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(post.complete_target_post_write, **request) for _ in range(2)]
        results = [f.result(timeout=60) for f in futures]
    assert ready.is_set() and calls[0] == calls[1]
    assert results[0].run_completion_assessment_ref == results[1].run_completion_assessment_ref
    assert results[0].terminal_run == results[1].terminal_run
    assert results[0].terminal_run.lifecycle_revision == before.lifecycle_revision+1


def test_different_barrier_cannot_reuse_finalizing(tmp_path, monkeypatch):
    from apps.enterprise_document.services.agent_rag_v1.run_completion_runtime import build_run_finalization_barrier
    from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    current = post.lifecycle_service.load_run_lifecycle(request['run_id'])
    exact = current.run_finalization_barrier.final_control_decision_ref.exact_ref_payload
    ref = PostWriteControlDecisionRefSchema.model_validate(exact)
    other = ref.model_copy(update={'control_decision_id':'control:other'})
    barrier = build_run_finalization_barrier(run_id=request['run_id'], final_control_ref=other)
    with pytest.raises(Exception, match='another barrier'):
        post._enter_finalization(request['run_id'], barrier)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']) == current


def test_postwrite_final_reads_share_quiescing_connection(tmp_path, monkeypatch):
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import read_postwrite_finalization
    barrier = post.lifecycle_service.load_run_lifecycle(request['run_id']).run_finalization_barrier
    connections=[]
    with ExitStack() as stack:
        for owner,name in ((post.control_store,'resolve_exact'),(post.entry_store,'resolve_exact'),
                (post.artifact_store,'resolve_exact'),(post.gate_store,'resolve_exact'),
                (post.validation_store,'resolve_fact'),(post.validation_store,'resolve_completion'),
                (post.binding_store,'resolve_assembly_manifest')):
            real=getattr(owner,name)
            def capture(*a,_real=real,**k):
                conn=k.get('connection'); assert conn is not None
                assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
                connections.append(conn)
                return _real(*a,**k)
            stack.enter_context(patch.object(owner,name,side_effect=capture))
        for owner,name in ((post.research_runtime,'resolve_progress'),(post.writer_execution_store,'resolve_writer_execution'),
                (post.claim_quality_projection.claim_runtime,'resolve_claim')):
            stack.enter_context(patch.object(owner,name,side_effect=AssertionError('external owner under SQLite fence')))
        read_postwrite_finalization(post,barrier)
    assert connections and all(c is connections[0] for c in connections)


def test_entire_finish_uses_no_ordinary_postwrite_read_transaction(tmp_path,monkeypatch):
    post,request=stopped_at_barrier(tmp_path,monkeypatch)
    current=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    with no_normal_writes(post),patch.object(post.database,'read_transaction',
            side_effect=AssertionError('ordinary PostWrite final currentness read')):
        ref,terminal,_,_=post._finish_finalization(current,task_id=request['task_id'],document_id=request['document_id'],
            project_input=request['project_input'],source_sections=request['section_execution_results'])
    assert terminal.status is RS.SUCCESS and terminal.run_completion_assessment_ref==ref


@pytest.mark.parametrize('winner', [RS.RUNNING, RS.CANCELLED])
def test_success_cas_loses_to_abort_or_cancel_at_original_revision(tmp_path, monkeypatch, winner):
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleRevisionConflict
    post, request = stopped_at_barrier(tmp_path, monkeypatch)
    current=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    transition=post.lifecycle_service.transition_run
    def race(run_id, revision, status, **kwargs):
        if status is RS.SUCCESS:
            transition(run_id, revision, winner, reason_ref='i8-race-winner')
        return transition(run_id, revision, status, **kwargs)
    monkeypatch.setattr(post.lifecycle_service,'transition_run',race)
    with no_normal_writes(post),pytest.raises(LifecycleRevisionConflict):
        post.complete_target_post_write(**request)
    after=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    assert after.status is winner and after.lifecycle_revision==current.lifecycle_revision+1
    assert after.run_completion_assessment_ref is None
    # A proven-current immutable Assessment may be orphaned when its CAS loses.
    assert post.assessment_store.path.exists()


def test_inflight_file_owner_is_drained_after_barrier(tmp_path, monkeypatch):
    post, request = target_fixture(tmp_path, monkeypatch)
    runtime=post.research_runtime.artifact_runtime
    version=request['section_execution_results'][0].active_evidence_package_binding.version_ref
    payload,_=runtime.read_version(version);logical=version.logical_ref
    fresh=runtime.commit_version(logical,payload,version.content_schema_version,
        LogicalOperationRef(logical_operation_id='i8:drain-commit',operation_kind='ARTIFACT_VERSION_COMMIT',
            run_id=logical.run_id,operation_scope_ref='artifact:'+logical.logical_artifact_id),
        canonical_sha256({'i8':'drain-commit'}),None)
    head=runtime.resolve_active(logical)
    held,finalized,reading,release=Event(),Event(),Event(),Event()
    append=runtime.log.append_serialized;enter=post._enter_finalization;resolve=runtime.resolve_active
    def blocked_append(serialized):
        held.set();assert release.wait(30);return append(serialized)
    def enter_barrier(*a,**k):
        assert held.wait(30)
        result=enter(*a,**k);finalized.set();return result
    def tracked_read(*a,**k):
        if finalized.is_set(): reading.set()
        return resolve(*a,**k)
    # Begin the legal pre-barrier owner write only once Phase A is complete.
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer=[]
        def enter_with_writer(*a,**k):
            monkeypatch.setattr(runtime.log,'append_serialized',blocked_append)
            writer.append(pool.submit(runtime.promote_active,logical,fresh,head.head_revision,
                head.active_version_ref.artifact_version_id,
                LogicalOperationRef(logical_operation_id='i8:drain-promote',operation_kind='ARTIFACT_ACTIVE_PROMOTION',
                    run_id=logical.run_id,operation_scope_ref='artifact:'+logical.logical_artifact_id),
                canonical_sha256({'i8':'drain-promote'}),None))
            return enter_barrier(*a,**k)
        monkeypatch.setattr(post,'_enter_finalization',enter_with_writer)
        monkeypatch.setattr(runtime,'resolve_active',tracked_read)
        finalizer=pool.submit(post.complete_target_post_write,**request)
        try:
            assert finalized.wait(30) and reading.wait(30)
            assert not writer[0].done() and not finalizer.done()
            assert post.lifecycle_service.load_run_lifecycle(request['run_id']).status is RS.FINALIZING
        finally:
            release.set()
        writer[0].result(timeout=30)
        with pytest.raises(Exception): finalizer.result(timeout=30)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']).status is RS.RUNNING
    assert not post.assessment_store.path.exists()


def test_corrupt_postwrite_fact_retains_finalizing(tmp_path,monkeypatch):
    from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
    post,request=stopped_at_barrier(tmp_path,monkeypatch)
    before=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    with patch.object(post.validation_store,'resolve_fact',side_effect=PostWritePersistenceIntegrityError('corrupt Fact')):
        with no_normal_writes(post),pytest.raises(PostWritePersistenceIntegrityError):
            post.complete_target_post_write(**request)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id'])==before
    assert not post.assessment_store.path.exists()


def test_inflight_postwrite_writer_is_drained_by_integrated_finalizer(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema
    post,request=target_fixture(tmp_path,monkeypatch)
    held,finalized,reading,release=Event(),Event(),Event(),Event()
    enter=post._enter_finalization;normal=post.database.normal_write_transaction
    finalread=post.database.finalization_read_transaction
    @contextmanager
    def blocked_write():
        with normal() as connection:
            held.set();assert release.wait(30);yield connection
    @contextmanager
    def tracked_finalread():
        reading.set()
        with finalread() as connection:yield connection
    with ThreadPoolExecutor(max_workers=2) as pool:
        writers=[]
        def enter_with_writer(run_id,barrier):
            control=post.control_store.resolve_exact(PostWriteControlDecisionRefSchema.model_validate(
                barrier.final_control_decision_ref.exact_ref_payload))
            record=post.artifact_store.resolve_exact(control.input_manifest.artifact_state_ref)
            monkeypatch.setattr(post.database,'normal_write_transaction',blocked_write)
            def write():
                with post.database.run_write_guard_context(run_id):
                    return post.artifact_service.append_next_version(expected_parent_ref=record.artifact_ref,
                        canonical_payload_ref={**record.canonical_payload_ref,'i8_postwrite_race':True},origin=record.origin)
            writers.append(pool.submit(write))
            assert held.wait(30)
            result=enter(run_id,barrier);finalized.set();return result
        monkeypatch.setattr(post,'_enter_finalization',enter_with_writer)
        monkeypatch.setattr(post.database,'finalization_read_transaction',tracked_finalread)
        future=pool.submit(post.complete_target_post_write,**request)
        try:
            assert finalized.wait(30) and reading.wait(30)
            assert not future.done() and not writers[0].done()
        finally:release.set()
        writers[0].result(timeout=30)
        with pytest.raises(Exception):future.result(timeout=30)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']).status is RS.RUNNING
    assert not post.assessment_store.path.exists()


@pytest.mark.parametrize('malformed',['missing','duplicate','authority','foreign_barrier'])
def test_malformed_assessment_cannot_win_success_cas(tmp_path,monkeypatch,malformed):
    post,request=stopped_at_barrier(tmp_path,monkeypatch)
    before=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    build=post.assessment_builder.build_success
    def malformed_build(**kwargs):
        refs=kwargs['post_write_mutation_closure_refs'];ref=refs[0]
        if malformed=='missing':refs=()
        elif malformed=='duplicate':refs=refs+refs
        elif malformed=='authority':refs=(ref.model_copy(update={'source_authority':'OTHER_OWNER'}),)
        else:
            from apps.enterprise_document.services.agent_rag_v1.run_completion_runtime import exact_ref_envelope
            payload=dict(ref.exact_ref_payload);payload['run_id']='foreign-run'
            refs=(exact_ref_envelope(source_authority='RUN_LIFECYCLE',source_kind='RUN_FINALIZATION_BARRIER',
                source_scope_ref=ref.source_scope_ref,exact_ref=payload),)
        return build(**{**kwargs,'post_write_mutation_closure_refs':refs})
    monkeypatch.setattr(post.assessment_builder,'build_success',malformed_build)
    with no_normal_writes(post),pytest.raises(Exception):post.complete_target_post_write(**request)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id'])==before


def test_live_finish_lock_order_has_no_mutable_owner_nesting(tmp_path,monkeypatch):
    from core.persistence.interprocess_lock import InterProcessFileLock
    from contextlib import contextmanager
    post,request=stopped_at_barrier(tmp_path,monkeypatch)
    current=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    held=[];trace=[]
    acquire=InterProcessFileLock.acquire;release=InterProcessFileLock.release
    assessment=str(post.assessment_store.lock_path.resolve())
    lifecycle=str(post.lifecycle_service.lock_path.resolve())
    def acquiring(lock):
        key=str(lock.path.resolve())
        if held:
            assert key==held[-1] or (held==[lifecycle] and key==assessment), ('nested owner',held,key)
        result=acquire(lock);held.append(key);trace.append(('acquire',key));return result
    def releasing(lock):
        key=str(lock.path.resolve());assert held and held[-1]==key
        release(lock);held.pop();trace.append(('release',key))
    fenced=post.database.finalization_read_transaction
    @contextmanager
    def sql_fence():
        assert not held
        with fenced() as connection:
            held.append('PostWrite');trace.append(('acquire','PostWrite'))
            try:yield connection
            finally:
                assert held==['PostWrite'];held.pop();trace.append(('release','PostWrite'))
    monkeypatch.setattr(InterProcessFileLock,'acquire',acquiring)
    monkeypatch.setattr(InterProcessFileLock,'release',releasing)
    monkeypatch.setattr(post.database,'finalization_read_transaction',sql_fence)
    with no_normal_writes(post):
        _,terminal,_,_=post._finish_finalization(current,task_id=request['task_id'],document_id=request['document_id'],
            project_input=request['project_input'],source_sections=request['section_execution_results'])
    assert terminal.status is RS.SUCCESS and not held and any(key=='PostWrite' for _,key in trace)


def test_corrupt_assessment_ref_fails_closed_at_success(tmp_path,monkeypatch):
    post,request=stopped_at_barrier(tmp_path,monkeypatch)
    current=post.lifecycle_service.load_run_lifecycle(request['run_id']);put=post.assessment_store.put_if_absent_or_same
    def corrupt(record):
        ref=put(record)
        return ref.model_copy(update={'semantic_fingerprint':'0'*64})
    monkeypatch.setattr(post.assessment_store,'put_if_absent_or_same',corrupt)
    with no_normal_writes(post),pytest.raises(Exception):post.complete_target_post_write(**request)
    assert post.lifecycle_service.load_run_lifecycle(request['run_id'])==current
