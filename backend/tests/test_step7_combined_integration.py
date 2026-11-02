"""Cross-lane production composition checks beyond the frozen lane union.

The dated correction records below preserve historical BLOCKED outcomes.
Current integrated acceptance and issue closure are recorded in
docs/implementation_routes_20260816/MULTI_AGENT/350_351_STEP7_INTEGRATED_CLOSURE_20260912.md.

2026-09-12 bounded test-only correction, authorized after cfafbfe:
INT-VERIFY-02 (BLOCKER): build observations were mistaken for Writer dispatches.
Verify exact execution identities, reserve/dispatch phases and one model dispatch.
INT-VERIFY-03 (BLOCKER): the budget fixture added B -> A but left A UNSUPPORTED.
Provide controlled A support/Verifier replies through the unchanged canonical
pipeline; retain the real PostgreSQL UNSUPPORTED -> WAIT negative scenario.
INT-VERIFY-04 remains OPEN: legal resource deadline evidence is required after
focused and frozen regression. No production/budget/currentness change is allowed.
Correction verification: focused 3 PASS / 1 FAIL; historical receipts unchanged.
INT-VERIFY-02: both three-Section variants pass exact identity/two-phase/single
dispatch, candidate semantics, currentness, replay and supersession checks.
INT-VERIFY-03: PostgreSQL UNSUPPORTED/WAIT negative passes. The eight-Section
normal run proves actual A ACCEPTED and B consuming its Claim plus Memory;
all eight Writers/extractors/support/Verifier calls execute before a later error.

INT-VERIFY-05 (BLOCKER, OPEN, discovered during this correction on 2026-09-12):
test_combined_dependency_memory_eight_section_budget[normal-43-of-44] fails the
terminal-success assertion after 134.2278967s. Persisted ConsistencyCompositionError
is canonical deadline exhaustion, not dependency WAIT or a changed call limit.
Canonical start/deadline: 14:27:36.275945Z / 14:29:36.275945Z. Used calls: 42/44,
all 42 identities unique, provider ordinals 1, retries and active reservations 0.
Executed work: 1 Planner + 8*(Writer/extraction/support/Verifier) + 9 adherence;
the remaining legitimate consistency call is denied by the deadline.
Trace: execute_sections 78.057s; evaluate_document_gate 48.937s (inclusive).
Impact: focused normal acceptance fails. Under the user's focused-failure STOP,
the frozen 480 union, seven-test PG suite and corrected resource repetitions are
NOT RUN in this correction. No commit is authorized without those passing gates.
Production correction necessity remains UNDETERMINED; the failed legal normal
path is performance-blocking evidence, not proof of contract incompatibility.
Underlying reusable-work/currentness-window attribution is deferred to a newly
authorized bounded performance investigation; no production fix is performed.
Evidence root (outside repository):
E:/rag-agent-tmp/step7-fixture-correction-ba5ae33571f949efb077aba23d0df8a1/focused/
execute-receipt.json SHA256:
197eebdfa139140d4d1b7c7994e3cb9f11901b7a691ba7f35db4b303b4dee4ab
execute-tmp/test_combined_dependency_memor0/runs/canonical_budget.jsonl SHA256:
03ae81845a2462a2b4cc274a326e5d22eb2408dbdd90c1b73db3b8c2628e1d20
Final status: STEP7_350_351_COMBINED_FIXTURE_CORRECTION_BLOCKED. STOP before 352.
"""
from collections import Counter, defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.agent_rag_v1.context_runtime import ContextSourceStale
from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer
from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyNotReady
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import SectionExecutionCoordinator
from apps.enterprise_document.services.scheme_writer.writer_invocation import PreWriteWriterInvocationService
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.context import ContextPriorityTier, ContextSourceFamily, ContextRepresentationLevel
import test_multi_agent_v3_cross_section_context_mainline as cross
import test_multi_agent_v3_verifier_mainline as verifier
import test_phase7_document_closure_mainline as budget_mainline
from test_step7_durable_memory_postgres import pg_store


@dataclass(frozen=True)
class WriterCapture:
    phase: str
    package: object
    plan_binding: object
    claim_ids: tuple
    memory_ids: tuple

    @property
    def logical_key(self):
        subject = self.package.invocation_subject
        return (subject.run_id, self.plan_binding.section_id, subject.attempt_id,
                subject.logical_operation_id)


def _activate_memory(monkeypatch, store):
    original = MainlineApplicationService.run
    def run(service, **kwargs):
        kwargs['settings'] = replace(kwargs['settings'], memory_database_url=store.database_url)
        kwargs['durable_memory_required'] = True
        return original(service, **kwargs)
    monkeypatch.setattr(MainlineApplicationService, 'run', run)


def _capture_writer(monkeypatch, *, negatives=False):
    packages = []
    phase = ContextVar('combined_writer_build_phase', default=None)
    def observe_phase(owner, method, name):
        delegate = getattr(owner, method)
        @wraps(delegate)
        def observe(*args, **kwargs):
            token = phase.set(name)
            try:
                return delegate(*args, **kwargs)
            finally:
                phase.reset(token)
        monkeypatch.setattr(owner, method, observe)
    observe_phase(SectionExecutionCoordinator, 'prepare_pre_write_writer', 'prepare-reserve')
    observe_phase(PreWriteWriterInvocationService, 'execute_initial', 'dispatch-validation')
    original = TargetWriterContextComposer.build_package
    def build(composer, **kwargs):
        package = original(composer, **kwargs)
        claims = [d for d in package.candidate_decisions
                  if d.source_binding.source_family is ContextSourceFamily.ACTIVE_VERIFIED_CLAIM]
        memories = [d for d in package.candidate_decisions
                    if d.source_binding.source_family is ContextSourceFamily.DURABLE_RUN_MEMORY]
        selected = {m.candidate_id for m in package.selected_materializations}
        subject = package.invocation_subject
        assert phase.get() in {'prepare-reserve', 'dispatch-validation'}
        assert subject.run_id == kwargs['plan_binding'].plan_version_ref.logical_ref.run_id
        assert subject.consumer_scope_ref == 'section:' + kwargs['plan_binding'].section_id
        assert subject.attempt_id == kwargs['write_instance_id']
        assert subject.agent_role == 'WRITER' and subject.context_purpose == 'SECTION_GENERATION'
        if claims:
            assert all(d.mandatory and d.priority_tier is ContextPriorityTier.P1_OPERATION_CRITICAL
                       and d.candidate_id in selected for d in claims)
            for candidate in claims:
                exact = candidate.source_binding.exact_source_ref
                assert candidate.role_visibility.value == 'CONDITIONAL'
                assert candidate.source_binding.run_id == subject.run_id
                assert exact['consumer_attempt_id'] == subject.attempt_id
                assert exact['consumer_plan_binding'] == kwargs['plan_binding'].model_dump(mode='json')
                assert exact['accepted_source']['workflow_status'] == 'ACCEPTED'
                assert candidate.candidate_id == 'writer-verified:' + exact['claim_ref']['claim_id']
            expected_version = 'v2-memory-locators' if composer.memory_composition.required else 'v2'
            assert package.policy_binding.role_policy_version == expected_version
            assert composer._active_versions(package, project_input=kwargs['project_input'])['role_policy_version'] == expected_version
            assert composer.memory_composition.claims is composer.cross_section_provider.dependencies.claim_runtime
            assert composer.memory_composition.lifecycle is composer.lifecycle_service
            assert composer.memory_composition.facts is composer.fact_snapshot_runtime
            if negatives:
                versions = composer._active_versions(package, project_input=kwargs['project_input'])
                versions['role_policy_version'] = 'obsolete-integration-policy'
                with patch.object(composer, '_active_versions', return_value=versions):
                    with pytest.raises(ContextSourceStale, match='POLICY_OR_CAPABILITY_BINDING_CHANGED'):
                        composer.validate_pre_model_call(package, project_input=kwargs['project_input'])
                with patch.object(composer.cross_section_provider, 'resolve_binding_current',
                                  side_effect=ContextSourceStale('superseded canonical Claim')):
                    with pytest.raises(ContextSourceStale, match='SOURCE_REVALIDATION_FAILED'):
                        composer.validate_pre_model_call(package, project_input=kwargs['project_input'])
        assert all(not d.mandatory and d.priority_tier is ContextPriorityTier.P3_SUPPORTING
                   for d in memories)
        assert all(d.role_visibility.value == 'MAY' for d in memories)
        cross_lane_ids = {d.candidate_id for d in claims + memories}
        assert all(m.representation_level is ContextRepresentationLevel.EXACT
                   for m in package.selected_materializations if m.candidate_id in cross_lane_ids)
        if composer.memory_composition.required:
            assert len(memories) == 2
            assert all(d.candidate_id in selected for d in memories)
        else:
            assert not memories
        packages.append(WriterCapture(phase.get(), package, kwargs['plan_binding'],
                                     tuple(d.candidate_id for d in claims),
                                     tuple(d.candidate_id for d in memories)))
        return package
    monkeypatch.setattr(TargetWriterContextComposer, 'build_package', build)
    return packages


def _assert_writer_execution_observations(packages, calls, *, run_id, section_ids):
    grouped = defaultdict(list)
    for capture in packages:
        grouped[capture.logical_key].append(capture)
    assert len(grouped) == len(section_ids)
    assert {key[0] for key in grouped} == {run_id}
    assert Counter(key[1] for key in grouped) == Counter(section_ids)
    writers = [call for call in calls if call.extra.get('call_purpose') == 'scheme_section_generation']
    assert Counter(call.extra['section_id'] for call in writers) == Counter(section_ids)
    assert len({call.model_call_id for call in writers}) == len(section_ids)
    for key, observations in grouped.items():
        assert Counter(item.phase for item in observations) == Counter(
            {'prepare-reserve': 1, 'dispatch-validation': 1})
        first, second = observations
        assert first.package.invocation_subject == second.package.invocation_subject
        assert first.claim_ids == second.claim_ids
        assert first.memory_ids == second.memory_ids
        assert [(d.candidate_id, d.source_binding.semantic_identity())
                for d in first.package.candidate_decisions] == [
                    (d.candidate_id, d.source_binding.semantic_identity())
                    for d in second.package.candidate_decisions]
        dispatched = [call for call in writers if call.extra['section_id'] == key[1]]
        assert len(dispatched) == 1
        assert dispatched[0].run_id == key[0]
        assert dispatched[0].model_call_id == first.package.invocation_subject.invocation_id
    return grouped


def _assert_one_claim_consumer(packages, *, producer, consumer_id):
    observations = [item for item in packages if item.claim_ids]
    assert {item.plan_binding.section_id for item in observations} == {consumer_id}
    assert len({item.logical_key for item in observations}) == 1
    assert Counter(item.phase for item in observations) == Counter(
        {'prepare-reserve': 1, 'dispatch-validation': 1})
    expected = {'writer-verified:' + ref.claim_ref.claim_id for ref in producer.verification_refs}
    for item in observations:
        assert set(item.claim_ids) == expected
        for candidate in item.package.candidate_decisions:
            if candidate.candidate_id in expected:
                assert candidate.source_binding.exact_source_ref['accepted_source'] == producer.model_dump(mode='json')


@pytest.mark.parametrize('required', [False, True], ids=['not-required', 'required-postgres'])
def test_combined_claim_memory_writer_currentness_and_recovery(tmp_path_factory, monkeypatch, request, required):
    store = request.getfixturevalue('pg_store') if required else None
    if store is not None:
        _activate_memory(monkeypatch, store)
    packages = _capture_writer(monkeypatch, negatives=True)
    data = cross._run_dependency_mainline(tmp_path_factory, 'VERIFIED')
    cross.test_350_explicit_dependency_requires_canonical_acceptance(data)
    cross.test_350_only_semantic_authorized_claim_reaches_writer(data)
    sections = data['top'].document_plan.sections
    _assert_writer_execution_observations(packages, data['calls'], run_id=data['top'].run_id,
                                         section_ids=[section.section_id for section in sections])
    _assert_one_claim_consumer(packages, producer=data['results'][0][1], consumer_id=sections[1].section_id)
    if required:
        assert any(item.claim_ids and item.memory_ids for item in packages)
        assert any('verified_statement' in str(call.messages) and 'memory_ref' in str(call.messages)
                   for call in data['calls'] if call.extra.get('call_purpose') == 'scheme_section_generation')
        def rows():
            with store._transaction() as cursor:
                cursor.execute('SELECT memory_id, record_json FROM durable_memory_record ORDER BY memory_id')
                return cursor.fetchall()
        before = rows()
    cross.test_350_projection_replay_does_not_call_models_or_commit_truth(data)
    for replay_case in ('exact', 'stale_dependency', 'wrong_dependency_plan'):
        cross.test_350_reopened_factory_reuses_terminal_dependent_checkpoint_without_writes(data, replay_case)
    if required:
        assert rows() == before
    cross.test_350_completed_source_adapter_then_supersession(data, tmp_path_factory)


def test_combined_unsupported_producer_keeps_consumer_waiting(tmp_path_factory, monkeypatch, pg_store):
    """A real unresolved Verifier outcome must not create a consumer Attempt."""
    _activate_memory(monkeypatch, pg_store)
    packages = _capture_writer(monkeypatch)
    data = cross._run_dependency_mainline(tmp_path_factory, 'UNSUPPORTED')
    cross.test_350_explicit_dependency_requires_canonical_acceptance(data)
    cross.test_350_only_semantic_authorized_claim_reaches_writer(data)
    producer = data['results'][0][1]
    consumer = data['top'].document_plan.sections[1]
    binding = producer.plan_binding.model_copy(update={'section_id': consumer.section_id})
    assessment = data['runtime'].assess_dependencies(binding)
    assert assessment.aggregate == 'WAITING'
    assert len(assessment.edges) == 1
    assert assessment.edges[0].result == 'WAITING'
    assert assessment.edges[0].reason == 'UPSTREAM_NOT_ACCEPTED'
    assert assessment.edges[0].source_binding == producer
    assert data['runtime'].assess_dependencies(binding).fingerprint == assessment.fingerprint
    assert data['top'].terminal_error.message == str(SectionDependencyNotReady(assessment))
    assert data['runtime'].lifecycle.load_latest_attempt(data['top'].run_id, 'section:' + consumer.section_id) is None
    _assert_writer_execution_observations(packages, data['calls'], run_id=data['top'].run_id,
        section_ids=[section.section_id for section in data['top'].document_plan.sections
                     if section.section_id != consumer.section_id])
    assert not any(item.claim_ids for item in packages)


def _accepted_budget_model(monkeypatch, claims_per_section):
    """Only A gains a validity oracle; all existing logical calls remain real."""
    calls = []
    original = FakeLLMClient.generate
    def provider(client, request):
        calls.append(request.model_copy(deep=True))
        response = original(client, request)
        contract = request.extra.get('response_contract')
        if request.extra.get('call_purpose') == 'scheme_section_generation':
            return response.model_copy(update={'content': verifier.TEXT * 6}, deep=True)
        if contract == cross.extraction.CONTRACT:
            units = cross.extraction._task_contract(request)['coverage_units']
            payload = {'units': [{'unit_id': unit['unit_id'], 'accounting_state': 'FACTUAL_CLAIMS_MATERIALIZED',
                       'claims': [{'quote': verifier.TEXT, 'occurrence_index': i, 'claim_type': 'general_method'}
                                  for i in range(claims_per_section)]} for unit in units]}
            return response.model_copy(update={'content': json.dumps(payload, ensure_ascii=False)}, deep=True)
        if request.extra.get('section_id') == 'section_semantic-default-budget342_001':
            if contract == cross.support.SUPPORT_RESPONSE_CONTRACT:
                refs = [item['exact_source_ref'] for item in cross.support._json_message(request, 'support_evidence')['support_evidence']]
                assert refs
                payload = {'relation': 'SUPPORTED', 'supporting_refs': refs[:1], 'contradicting_refs': [],
                           'certainty_relation': 'ALIGNED', 'certainty_reason_codes': ['COMBINED_PRODUCER_ALIGNED']}
                return response.model_copy(update={'content': json.dumps(payload)}, deep=True)
            if contract == verifier.VERIFIER_RESPONSE_CONTRACT:
                return response.model_copy(update={'content': json.dumps({'verdict': 'VERIFIED',
                    'rationale': 'Controlled combined producer validity oracle.', 'issues': []})}, deep=True)
        return response
    monkeypatch.setattr(FakeLLMClient, 'generate', provider)
    return calls


@pytest.mark.parametrize('claims_per_section', [1, 2], ids=['normal-43-of-44', 'resource-44-of-44'])
def test_combined_dependency_memory_eight_section_budget(tmp_path, monkeypatch, pg_store, claims_per_section):
    """Normal: 1 + 8*4 + 9 adherence + 1 consistency = 43.

    Resource: 1 + 7*6 = 43 before section 8; its Writer consumes call 44,
    then extraction is denied. B is section 2 and must have already consumed A.
    No padding calls, reduced obligations or changed production budget policy.
    """
    _activate_memory(monkeypatch, pg_store)
    packages = _capture_writer(monkeypatch)
    original_build = budget_mainline._InspectingFactory.build
    observed = []
    def build(factory, **kwargs):
        supervisor = original_build(factory, **kwargs)
        stage = supervisor.workflow_engine.stage_service
        stage.planner_adapter = cross.DependencyPlanner(stage.planner_adapter)
        observed.append(supervisor.workflow_engine)
        return supervisor
    monkeypatch.setattr(budget_mainline._InspectingFactory, 'build', build)
    calls = _accepted_budget_model(monkeypatch, claims_per_section)
    policy_path = Path(verifier.__file__).resolve().parents[1] / 'apps/enterprise_document/config/grounded_generation_v1.yaml'
    original_policy = policy_path.read_bytes()
    with patch.dict(os.environ, {'GROUNDED_GENERATION_POLICY_FILE': str(policy_path)}):
        summary, _ = budget_mainline._run_fake(tmp_path, run_id='semantic-default-budget342')
    assert policy_path.read_bytes() == original_policy
    top = cross.w13._latest_w13_top_level_payload(observed[0])
    engine = observed[0]
    state = engine.budget_runtime(top.task_id, top.run_id).canonical_state()
    assert state.limits.logical_model_calls == 44
    assert (state.deadline_at - state.started_at).total_seconds() == 120
    sections = top.document_plan.sections
    assert len(sections) == 8
    # Assert the actual accepted-source/currentness/authorization observation
    # captured while B was dispatch-current, before checking terminal budgets.
    dependent = [item for item in packages if item.plan_binding.section_id == sections[1].section_id]
    assert len(dependent) == 2 and all(item.claim_ids and item.memory_ids for item in dependent)
    claim = next(d for d in dependent[0].package.candidate_decisions
                 if d.source_binding.source_family is ContextSourceFamily.ACTIVE_VERIFIED_CLAIM)
    from apps.enterprise_document.schemas.agent_rag_v1.section_dependency import SectionSourceBindingSchema
    producer = SectionSourceBindingSchema.model_validate(claim.source_binding.exact_source_ref['accepted_source'])
    assert producer.workflow_status == 'ACCEPTED'
    assert producer.plan_binding.section_id == sections[0].section_id
    assert len(producer.verification_refs) == claims_per_section
    _assert_one_claim_consumer(packages, producer=producer, consumer_id=sections[1].section_id)
    writers = [call for call in calls if call.extra.get('call_purpose') == 'scheme_section_generation']
    verifiers = [call for call in calls if call.extra.get('call_purpose') == 'claim_verification']
    assert state.active_reserved.logical_model_calls == 0
    if claims_per_section == 1:
        assert top.terminal_error is None
        assert summary['status'] == 'partial_success'
        assert len(writers) == len(verifiers) == 8
        assert state.used.logical_model_calls == 43
        assert summary.get('target_release_publication') is not None
    else:
        assert top.terminal_error is not None
        assert 'logical_model_calls' in top.terminal_error.message
        assert 'limit=44' in top.terminal_error.message
        assert summary['status'] in {'failed', 'retryable_failed'}
        assert summary.get('target_release_publication') is None
        assert len(writers) == 8 and len(verifiers) == 14
        assert state.used.logical_model_calls == 44
    _assert_writer_execution_observations(packages, calls, run_id=top.run_id,
                                         section_ids=[section.section_id for section in sections])
