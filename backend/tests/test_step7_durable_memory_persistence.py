"""351 production source adapter tests; DB-API harness is explicitly controlled."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.facts import FactSnapshotContentSchema
from apps.enterprise_document.schemas.agent_rag_v1.memory import MemoryFreshnessEligibility
from apps.enterprise_document.services.agent_rag_v1.memory_composition import (
    DurableMemoryCapabilityError, TargetDurableMemoryComposition,
)
from apps.enterprise_document.services.agent_rag_v1.memory_runtime import DurableMemoryRuntime
from apps.enterprise_document.services.agent_rag_v1.memory_store import PostgresDurableMemoryStore
from test_multi_agent_v3_memory_runtime import _FakePgDatabase, _FakePgConnection
from test_multi_agent_v3_fact_snapshot_runtime import _runtime, _source, _fact, _op


def _setup(tmp_path):
    fact = _runtime(tmp_path)
    db = _FakePgDatabase()
    store = PostgresDurableMemoryStore(database_url='', connection_factory=lambda: _FakePgConnection(db))
    memory = TargetDurableMemoryComposition(
        required=True, runtime=DurableMemoryRuntime(store=store),
        fact_snapshot_runtime=fact, lifecycle_service=fact.artifact_runtime.lifecycle_service,
    )
    return fact, db, memory


def _commit(fact, version='1', value=10):
    logical = fact.logical_ref_for_scope(run_id='run-fact-1', domain_scope_ref='project:task-fact-1')
    content = FactSnapshotContentSchema(normalization_contract_version='facts-v1',
        source_bindings=[_source()], facts=[_fact(value=value)])
    ref = fact.commit_snapshot(logical_ref=logical, content=content,
        operation_ref=_op('memory-source-'+version, 'ARTIFACT_VERSION_COMMIT'))
    head = fact.artifact_runtime.resolve_active(logical)
    fact.promote_snapshot(logical_ref=logical, target_version_ref=ref,
        expected_head_revision=head.head_revision if head else 0,
        expected_active_version_id=head.active_version_ref.artifact_version_id if head and head.active_version_ref else None,
        operation_ref=_op('memory-promote-'+version, 'ARTIFACT_ACTIVE_PROMOTION'))
    return ref


def test_required_missing_backend_fails_closed():
    with pytest.raises(DurableMemoryCapabilityError, match='MEMORY_CONFIGURATION_REQUIRED'):
        TargetDurableMemoryComposition(required=True, runtime=None)


def test_nonrequired_missing_backend_preserves_compatibility():
    memory = TargetDurableMemoryComposition(required=False, runtime=None)
    assert memory.project_artifact(None) == ()


def test_source_commit_precedes_projection_and_exact_repeat_is_idempotent(tmp_path):
    fact, db, memory = _setup(tmp_path)
    ref = _commit(fact)
    assert db.memory == {}
    first = memory.project_artifact(ref)
    before = dict(db.memory)
    assert memory.project_artifact(ref) == first
    assert db.memory == before and len(before) == 1
    record = memory.runtime.resolve_record(first[0])
    assert record.source_binding.primary_source.exact_source_ref == ref.model_dump(mode='json')
    assert set(record.projection.projection_payload) == {'source_ref'}


def test_superseded_source_is_no_longer_active_and_old_bytes_unchanged(tmp_path):
    fact, db, memory = _setup(tmp_path)
    old = memory.project_artifact(_commit(fact))[0]
    before = dict(db.memory[old.memory_id])
    memory.project_artifact(_commit(fact, '2', 20))
    assert memory.runtime.assess_freshness(memory.runtime.resolve_record(old)).eligibility is MemoryFreshnessEligibility.HISTORICAL_ONLY
    assert db.memory[old.memory_id] == before
    assert old not in tuple(item.memory_ref for item in memory.recall(run_id='run-fact-1', scope_ref='project:task-fact-1'))


def test_canonical_source_invalidation_blocks_consumer(tmp_path):
    fact, db, memory = _setup(tmp_path)
    memory.project_artifact(_commit(fact))
    fact.source_binding_resolver = lambda _binding: False
    assert memory.recall(run_id='run-fact-1', scope_ref='project:task-fact-1') == ()


def test_wrong_run_and_scope_cannot_consume(tmp_path):
    fact, db, memory = _setup(tmp_path)
    memory.project_artifact(_commit(fact))
    assert memory.recall(run_id='other-run', scope_ref='project:task-fact-1') == ()
    assert memory.recall(run_id='run-fact-1', scope_ref='section:other') == ()


def test_crash_after_source_before_memory_recovers_from_exact_source(tmp_path):
    fact, db, memory = _setup(tmp_path)
    source = _commit(fact)
    # No projection call simulates process loss at the producer boundary.
    restored = TargetDurableMemoryComposition(required=True,
        runtime=DurableMemoryRuntime(store=memory.runtime.store), fact_snapshot_runtime=fact,
        lifecycle_service=fact.artifact_runtime.lifecycle_service)
    refs = restored.project_artifact(source)
    assert refs == memory.project_artifact(source) and len(db.memory) == 1


def test_crash_after_memory_before_checkpoint_has_no_rewrite(tmp_path, monkeypatch):
    fact, db, memory = _setup(tmp_path)
    source = _commit(fact)
    refs = memory.project_artifact(source)
    monkeypatch.setattr(memory.runtime.store, 'put_memory_record_if_absent_or_same',
        lambda *a, **k: pytest.fail('duplicate Memory write'))
    assert memory.project_artifact(source) == refs


def test_terminal_replay_is_read_only_even_when_index_missing(tmp_path, monkeypatch):
    fact, db, memory = _setup(tmp_path)
    source = _commit(fact)
    refs = memory.project_artifact(source)
    db.selector_index.clear()
    monkeypatch.setattr(memory, '_terminal', lambda run_id: True)
    monkeypatch.setattr(memory.runtime.store, 'put_memory_record_if_absent_or_same',
        lambda *a, **k: pytest.fail('terminal record write'))
    monkeypatch.setattr(memory.runtime.store, 'upsert_selector_index',
        lambda *a, **k: pytest.fail('terminal index write'))
    assert memory.project_artifact(source) == refs


def test_terminal_replay_missing_record_fails_closed(tmp_path, monkeypatch):
    fact, db, memory = _setup(tmp_path)
    source = _commit(fact)
    monkeypatch.setattr(memory, '_terminal', lambda run_id: True)
    with pytest.raises(DurableMemoryCapabilityError, match='MEMORY_TERMINAL_RECORD_MISSING'):
        memory.project_artifact(source)
    assert not db.memory


def test_corrupt_memory_rejected_without_overwriting_source(tmp_path):
    fact, db, memory = _setup(tmp_path)
    source = _commit(fact)
    ref = memory.project_artifact(source)[0]
    source_bytes = fact.artifact_runtime.ledger_path.read_bytes()
    db.memory[ref.memory_id]['projection']['projection_payload'] = {'source_ref': {'forged': True}}
    with pytest.raises(Exception):
        memory.recall(run_id='run-fact-1', scope_ref='project:task-fact-1')
    assert fact.artifact_runtime.ledger_path.read_bytes() == source_bytes


@pytest.mark.parametrize('scenario', ['VERIFIED', 'CONFLICTED'])
def test_required_production_sources_and_writer_consumption(tmp_path_factory, monkeypatch, scenario):
    _production(tmp_path_factory, monkeypatch, scenario)


def _production(tmp_path_factory, monkeypatch, scenario, live=None):
    import test_multi_agent_v3_verifier_mainline as mainline
    from application.mainline_service import MainlineApplicationService
    from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer, TargetWriterContextIntegrityError
    from schemas.context import ContextSourceFamily
    db = _FakePgDatabase()
    execution = {}
    packages = []
    original_package = TargetWriterContextComposer.build_package
    def package_call(self, **kwargs):
        package = original_package(self, **kwargs)
        packages.append((self, package))
        return package
    monkeypatch.setattr(TargetWriterContextComposer, 'build_package', package_call)
    original_run = MainlineApplicationService.run
    original_compose = TargetDurableMemoryComposition.from_settings
    def required_run(self, **kwargs):
        if live:
            kwargs['settings'] = replace(kwargs['settings'], memory_database_url=live.database_url)
        execution.update(kwargs)
        return original_run(self, **kwargs, durable_memory_required=True)
    def compose(cls, *, required, settings, **owners):
        assert required is True
        if live:
            return original_compose(required=required, settings=settings, **owners)
        store = PostgresDurableMemoryStore(database_url='', connection_factory=lambda: _FakePgConnection(db))
        return cls(required=True, runtime=DurableMemoryRuntime(store=store), **owners)
    monkeypatch.setattr(MainlineApplicationService, 'run', required_run)
    monkeypatch.setattr(TargetDurableMemoryComposition, 'from_settings', classmethod(compose))
    result = mainline.verifier_mainline.__wrapped__(tmp_path_factory, SimpleNamespace(param=scenario))
    _, observation, calls, invocations, summary, engine = result
    assert summary['status'] == 'partial_success'
    memory = engine.memory_runtime
    def snapshot():
        if live:
            with live._transaction() as cursor:
                cursor.execute('SELECT memory_id, record_json FROM durable_memory_record ORDER BY memory_id')
                return dict(cursor.fetchall())
        return dict(db.memory)
    types = {record['memory_ref']['memory_type'] for record in snapshot().values()}
    assert {'fact','evidence','research_history','artifact_reference'} <= types
    assert ('verified_claim' if scenario == 'VERIFIED' else 'verifier_issue') in types
    before = snapshot()
    reconciled = memory.reconcile_run('run-verifier-'+scenario)
    assert {ref.memory_id for ref in reconciled} == set(before)
    assert snapshot() == before
    writer_calls = [call for call in calls if call.extra.get('call_purpose') == 'scheme_section_generation']
    assert len(writer_calls) == 1 and len(invocations) == 1
    assert 'memory_ref' in str(writer_calls[0].messages)
    composer, package = packages[0]
    selected_ids = {item.candidate_id for item in package.selected_materializations}
    memory_decisions = [item for item in package.candidate_decisions
        if item.source_binding.source_family is ContextSourceFamily.DURABLE_RUN_MEMORY]
    assert len(memory_decisions) == 2
    assert all(item.candidate_id in selected_ids for item in memory_decisions)
    evidence_source = next(item.source_binding for item in memory_decisions
        if item.source_binding.exact_source_ref['source_ref']['logical_ref']['artifact_type'] == 'evidence_package')
    forged = evidence_source.model_dump(mode='json')
    forged['scope_ref'] = 'section:wrong'
    forged['exact_source_ref']['canonical_binding']['scope_ref'] = 'section:wrong'
    # Changing both wrapper scopes must not bypass the canonical Artifact scope.
    with pytest.raises(TargetWriterContextIntegrityError,match='canonical source Run/Section mismatch'):
        composer._resolve_binding_current(type(evidence_source).model_validate(forged))
    # Use the materialized provider bindings, not a made-up Memory-only query.
    sources = []
    def walk(value):
        if isinstance(value, dict):
            if value.get('source_family') == 'DURABLE_RUN_MEMORY' and 'exact_source_ref' in value:
                sources.append(value)
            for item in value.values(): walk(item)
        elif isinstance(value, (tuple,list)):
            for item in value: walk(item)
    walk(package.model_dump(mode='json'))
    assert sources
    from schemas.context import ContextSourceBindingSchema, ContextInvocationSubjectSchema
    source = ContextSourceBindingSchema.model_validate(sources[0])
    subject = ContextInvocationSubjectSchema(run_id=source.run_id, attempt_id='attempt-test',
        invocation_id='invocation-test', logical_operation_id='operation-test', agent_role='WRITER',
        execution_mode='NORMAL', context_purpose='SECTION_GENERATION', consumer_scope_ref='section:wrong')
    assert composer._hard_eligible(source,subject)[0] is False
    assert composer._hard_eligible(source,subject.model_copy(update={'run_id':'wrong-run'}))[0] is False
    # Reopen must recover the explicit requirement from authorized Task metadata,
    # even though inspect/recover expose no caller downgrade switch.
    class ObservedRequirement(Exception): pass
    def inspect_build(**kwargs):
        assert kwargs['options'].durable_memory_required is True
        raise ObservedRequirement
    with pytest.raises(ObservedRequirement):
        MainlineApplicationService(supervisor_factory=SimpleNamespace(build=inspect_build)).inspect_run(
            project_root=execution['project_root'], task_id=execution['task_id'], run_id=execution['run_id'],
            output_root=execution['output_root'], settings=execution['settings'], allow_demo_defaults=True)
    return result


def test_uncommitted_source_cannot_create_orphan_memory(tmp_path):
    fact, db, memory = _setup(tmp_path)
    source = _commit(fact).model_copy(update={'artifact_version_id':'uncommitted-version'})
    with pytest.raises(Exception):
        memory.project_artifact(source)
    assert not db.memory


@pytest.mark.parametrize('value', [0, 1, 'true', None])
def test_capability_declaration_is_strict_boolean(value):
    with pytest.raises(TypeError):
        TargetDurableMemoryComposition.from_settings(required=value,settings=SimpleNamespace())


def test_default_composition_never_checks_postgres(monkeypatch):
    monkeypatch.setattr(PostgresDurableMemoryStore,'validate_schema',lambda self: pytest.fail('global PG requirement'))
    assert TargetDurableMemoryComposition.from_settings(required=False,settings=SimpleNamespace()).runtime is None


@pytest.mark.parametrize('entry', ['run', 'stream_run'])
def test_required_mainline_entry_stops_before_model_without_configuration(tmp_path, monkeypatch, entry):
    from pathlib import Path
    from application.mainline_service import MainlineApplicationService
    from core.config import get_settings
    from model_gateway.fake_llm_client import FakeLLMClient
    from test_multi_agent_v3_workflow_recovery import _w13_single_section_project_input
    monkeypatch.setenv('USE_REAL_RAG_TOOL','false')
    monkeypatch.setattr(FakeLLMClient,'generate',lambda *a,**k: pytest.fail('model called without required capability'))
    from test_multi_agent_v3_workflow_recovery import _w13_current_new_task_project_input
    project = _w13_current_new_task_project_input(_w13_single_section_project_input('task-required-config'))
    settings = replace(get_settings(),data_root=tmp_path,memory_database_url='',
        default_model_name='fake_llm',supervisor_model_name='fake_llm',enable_llm_routing=False)
    with pytest.raises(DurableMemoryCapabilityError,match='MEMORY_CONFIGURATION_REQUIRED'):
        result = getattr(MainlineApplicationService(),entry)(project_root=Path(__file__).resolve().parents[2],
            user_input=project.user_query,task_id=project.task_id,run_id='run-required-config',output_root=tmp_path,
            settings=settings,project_input=project.model_dump(mode='json'),durable_memory_required=True,
            enable_agent_self_rag=False)
        if entry == 'stream_run':
            list(result)


def test_evidence_memory_honors_transitive_plan_fact_invalidation(tmp_path):
    from test_multi_agent_v3_research_runtime import _setup as research_setup
    env = research_setup(tmp_path)
    db = _FakePgDatabase()
    memory = TargetDurableMemoryComposition(required=True,
        runtime=DurableMemoryRuntime(store=PostgresDurableMemoryStore(database_url='',connection_factory=lambda:_FakePgConnection(db))),
        fact_snapshot_runtime=env['fact'],research_runtime=env['research'],lifecycle_service=env['lifecycle'])
    ref = memory.project_artifact(env['package_version'])[0]
    record = memory.runtime.resolve_record(ref)
    assert memory.runtime.assess_freshness(record).eligibility is MemoryFreshnessEligibility.ACTIVE_RECALL
    env['fact'].source_binding_resolver = lambda _: False
    assert memory.runtime.assess_freshness(record).eligibility is MemoryFreshnessEligibility.INELIGIBLE
    assert memory.recall(run_id=ref.run_id,scope_ref=ref.scope_ref) == ()


@pytest.mark.parametrize('status', ['RESOLVED', 'INVALIDATED'])
def test_issue_memory_follows_canonical_lifecycle_without_rewriting_old_record(tmp_path,status):
    import test_multi_agent_v3_claim_verification_runtime as claims
    from apps.enterprise_document.schemas.agent_rag_v1.verification import (
        VerifierSemanticIssueCandidateSchema, VerificationVerdict, VerifierIssueLifecycleStatus,
    )
    from core.hashing import canonical_sha256
    env, owner, claim, binding, _, support, _ = claims._setup_verification_chain(tmp_path)
    candidate = claims._verifier_candidate(claim,binding,support,
        verdict=VerificationVerdict.CONFLICTED,verification_occurrence_ref='verification://351-issue',
        issue_candidates=(VerifierSemanticIssueCandidateSchema(issue_slot='issue-slot:351',
            issue_code='CLAIM_REVIEW_REQUIRED',severity='major',semantic_category='semantic_conflict',
            message_hash=canonical_sha256('review required'),rationale_ref='trace://351-issue'),))
    committed = owner.commit_verification(claim.claim_ref,binding.binding_facts_ref,support.support_ref,
        candidate,None,claims._verification_context(env,candidate.verification_occurrence_ref))
    db = _FakePgDatabase()
    memory = TargetDurableMemoryComposition(required=True,
        runtime=DurableMemoryRuntime(store=PostgresDurableMemoryStore(database_url='',connection_factory=lambda:_FakePgConnection(db))),
        fact_snapshot_runtime=env['fact'],research_runtime=env['research'],claim_runtime=owner,lifecycle_service=env['lifecycle'])
    old = memory.project_issue(committed.issue_heads[0])[0]
    old_bytes = dict(db.memory[old.memory_id])
    receipt = owner.transition_issue_lifecycle(committed.issue_heads[0].issue_ref,
        expected_head_revision=1,expected_lifecycle_status=VerifierIssueLifecycleStatus.OPEN,
        target_status=VerifierIssueLifecycleStatus(status),reason_ref='trace://351-transition',
        operation_context=claims._context(env,authorization_ref='issue-transition://351',purpose='verifier-issue-lifecycle'),
        resolution_ref='artifact://351-resolution' if status=='RESOLVED' else None)
    new = memory.project_issue(receipt.resulting_head)[0]
    assert new != old and db.memory[old.memory_id] == old_bytes
    assert memory.project_issue(receipt.resulting_head) == (new,)
    assert len(db.memory) == 2
    expected = MemoryFreshnessEligibility.HISTORICAL_ONLY if status=='RESOLVED' else MemoryFreshnessEligibility.INELIGIBLE
    assert memory.runtime.assess_freshness(memory.runtime.resolve_record(old)).eligibility is expected
    assert memory.runtime.assess_freshness(memory.runtime.resolve_record(new)).eligibility is expected
