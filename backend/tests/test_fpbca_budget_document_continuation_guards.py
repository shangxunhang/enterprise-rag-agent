"""Native Step 4 guards; fresh local fixtures, no Graph/application acceptance claim."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    BudgetReservationRefSchema, CanonicalBudgetVectorSchema,
    CanonicalBudgetTopupAuthorizationSchema, CanonicalBudgetTopupRequestSchema,
)
from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import (
    CitationGroundingError, TargetPostWriteCitationGrounding,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition, TargetPostWriteCompositionError,
)
from apps.enterprise_document.services.agent_rag_v1.terminal_outcome_transport import TargetPostWriteInterrupted
from apps.enterprise_document.services.scheme_writer.stage_service import SchemeGenerationStageService
from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import canonical_json_sha256
from schemas.model import TokenUsageSchema
from test_fpbca_budget_document_admission import _fixture, _authorize
from test_multi_agent_v3_citation_grounding_mainline import _grounding_run, _association_input, TEXT


def _observe(root, name, value):
    target = root / (name + '.json')
    assert not target.exists()
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _consume_test_owned_token_preimage(manager, amount, suffix):
    """Explicit test resource preimage, not a claim that these were model calls."""
    ref = BudgetReservationRefSchema(
        reservation_id='budget:test-owned-token-preimage:' + suffix,
        task_id=manager.task_id, run_id=manager.run_id,
        reservation_kind='local_test_owned_resource_preimage',
        owner_occurrence_ref='fixture://local-test-owned-resource-preimage/' + suffix,
        scope_ref='run:' + manager.run_id, operation_ref='fixture://local-test-owned-resource-preimage/' + suffix,
    )
    vector = CanonicalBudgetVectorSchema(model_tokens_total=amount)
    manager.reserve(ref, vector, expected_revision=manager.canonical_state().revision)
    manager.consume(ref.reservation_id, vector, expected_revision=manager.canonical_state().revision)


def _authorize_exact_native_refusal(owner, manager, life, identity, refusal, delta):
    """Trusted explicit test command; real Budget grant and full Child lifecycle fence."""
    pre = life.budget_preimage(manager.run_id)
    packet = owner.store.put_json(
        {'scope': 'STEP4_NATIVE_OWNER_NOT_GRAPH', 'refusal': refusal,
         'children': [r.model_dump(mode='json') for r in pre.current_children]},
        artifact_ref='artifact://local-native-document-wait/' + refusal['native_denial_sha256'],
        artifact_type='local_native_wait', schema_version='local_native_wait_v1',
        created_by='test-explicit-command', **identity,
    )
    version = str(refusal['artifact_version'])
    wait = life.pause_for_budget(manager.run_id, pre.run.lifecycle_revision,
        expected_current_children=pre.current_children, operation_key='pause:' + refusal['native_denial_sha256'],
        wait_ref=packet.artifact_ref, wait_sha256=packet.content_hash.removeprefix('sha256:'),
        semantic_operation_ref=refusal['semantic_operation_ref'], semantic_input_sha256=refusal['semantic_input_sha256'],
        artifact_version=version)
    before = manager.canonical_state()
    baseline = canonical_json_sha256({'policy_ref': before.policy_ref, 'policy_version': before.policy_version,
        'limits': before.limits.model_dump(mode='json')})
    command = dict(authorization_kind='SINGLE_USE', authorization_id='local:' + refusal['native_denial_sha256'],
        command_source='API', issuer='test-explicit-operator', task_id=manager.task_id, run_id=manager.run_id,
        canonical_request_ref='run-identity:' + manager.run_id,
        canonical_request_sha256=pre.run.identity.canonical_request_sha256,
        semantic_operation_ref=refusal['semantic_operation_ref'], semantic_input_sha256=refusal['semantic_input_sha256'],
        artifact_version=version, baseline_policy_sha256=baseline,
        authorized_delta={'model_tokens_total': delta},
        cumulative_ceiling={**before.limits.model_dump(mode='json'), 'model_tokens_total': before.effective_limits.model_tokens_total + delta},
        single_use_wait_event_ref=wait.event_ref, single_use_native_denial_sha256=refusal['native_denial_sha256'],
        single_use_child_preimage_sha256=pre.child_preimage_sha256)
    native = owner.store.put_json(command, artifact_ref='artifact://local-document-authorization/' + refusal['native_denial_sha256'],
        artifact_type='budget_topup_command', schema_version='budget_topup_command_v1',
        created_by='trusted-application-command', **identity)
    auth = CanonicalBudgetTopupAuthorizationSchema(**command, authorization_ref=native.artifact_ref,
        authorization_sha256=native.content_hash.removeprefix('sha256:'))
    failed = owner.store.get_json(refusal['native_denial_ref'], **identity)
    request = CanonicalBudgetTopupRequestSchema(approval_id='client:' + refusal['native_denial_sha256'],
        authorization_ref=auth.authorization_ref, authorization_sha256=auth.authorization_sha256,
        task_id=manager.task_id, run_id=manager.run_id, canonical_request_ref=auth.canonical_request_ref,
        canonical_request_sha256=auth.canonical_request_sha256, semantic_operation_ref=wait.semantic_operation_ref,
        semantic_input_sha256=wait.semantic_input_sha256, artifact_version=version,
        native_denial_ref=refusal['native_denial_ref'], native_denial_sha256=refusal['native_denial_sha256'],
        phase_proof_ref=refusal['phase_proof_ref'], phase_proof_sha256=refusal['phase_proof_sha256'],
        wait_event_ref=wait.event_ref, wait_event_sha256=wait.event_sha256,
        child_preimage_sha256=pre.child_preimage_sha256, baseline_policy_sha256=baseline,
        expected_budget_revision=before.revision, expected_lifecycle_revision=wait.resulting_run.lifecycle_revision,
        delta={'model_tokens_total': delta}, required=failed['failure']['native_denial']['requested'])
    def resolve(bound):
        reference, value = owner.store.get_json_with_reference(bound.authorization_ref, **identity)
        assert reference.content_hash == 'sha256:' + bound.authorization_sha256
        return CanonicalBudgetTopupAuthorizationSchema(**value, authorization_ref=reference.artifact_ref,
            authorization_sha256=bound.authorization_sha256)
    grant = manager.apply_topup(request, authorization_resolver=resolve,
        paused_authorization_fence=life.budget_authorization_fence)
    resumed = life.resume_budget_wait(manager.run_id, wait.resulting_run.lifecycle_revision,
        expected_current_children=wait.current_children, operation_key='resume:' + refusal['native_denial_sha256'],
        wait_event_ref=wait.event_ref, wait_event_sha256=wait.event_sha256,
        grant_ref=grant.grant_ref, grant_sha256=grant.grant_sha256)
    return dict(schema_version='document_budget_continuation_v1',
        predecessor_result_ref=refusal['native_denial_ref'], predecessor_result_sha256=refusal['native_denial_sha256'],
        accepted_grant_ref=grant.grant_ref, accepted_grant_sha256=grant.grant_sha256,
        resume_event_ref=resumed.event_ref, resume_event_sha256=resumed.event_sha256)


def test_missing_positive_phase_proof_preserves_original_budget_error(tmp_path, monkeypatch):
    state = _fixture(tmp_path)
    owner, manager, life, physical, identity, lineage, material, _, _ = state
    material = {**material, 'test_native_unproved_occurrence': True}
    def generic(_request):
        raise CanonicalBudgetCapacityExceeded('model_tokens_total', limit=24000, remaining=0, required=8192)
    monkeypatch.setattr(owner.gateway.budget_runtime, 'reserve_provider_attempt', generic)
    with pytest.raises(CitationGroundingError) as caught:
        owner._associate(material, identity, lineage, lambda: None)
    original = caught.value
    assert original.budget_failure_receipt['phase_proof_sha256'] is None
    pw = object.__new__(TargetPostWriteComposition)
    pw.database = SimpleNamespace(run_write_guard_context=lambda _: nullcontext())
    pw.lifecycle_service = life
    frozen = SimpleNamespace(section_results=(), source_sections=(), document=None)
    pw.materialize_sections_and_document = lambda **_: frozen
    pw._finalize_document_output_and_control = lambda **_: (_ for _ in ()).throw(original)
    pw.citation_grounding = owner
    pw.capture_budget_materialization = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unproved failure became a hold'))
    before = manager.canonical_state()
    with pytest.raises(TargetPostWriteInterrupted) as interrupted:
        pw.complete_target_post_write(task_id=identity['task_id'], run_id=identity['run_id'],
            document_id='local-document', project_input=SimpleNamespace(), section_execution_results=())
    _observe(tmp_path, 'unproved-original-cause', {'scope': 'ACTUAL_POSTWRITE_EXCEPTION_BOUNDARY_ONLY',
        'original_type': type(original).__name__, 'original_refusal': original.budget_failure_receipt,
        'interrupted_cause_type': type(interrupted.value.cause).__name__,
        'physical_count': len(physical), 'budget': manager.canonical_state().model_dump(mode='json')})
    assert interrupted.value.cause is original
    assert not hasattr(original, 'budget_materialization_ref')
    assert manager.canonical_state() == before and physical == []


@pytest.mark.parametrize('damage', ['missing', 'different_binding'])
def test_completed_admission_requires_exact_native_ack(tmp_path, damage):
    state = _fixture(tmp_path)
    owner, manager, life, physical, identity, lineage, material, _, _ = state
    binding = _authorize(state)
    _, intent, _ = owner._associate(material, identity, lineage, lambda: None, budget_continuation=binding)
    ack_ref = intent.removesuffix('/input') + '/ack'
    ack = owner.store.get_json(ack_ref, **identity)
    # Damage only this fresh fixture; no historical receipt is rewritten.
    owner.store._path_for_ref(ack_ref).unlink()
    if damage == 'different_binding':
        owner.store.put_json({**ack, 'admission_id': '0' * 64}, artifact_ref=ack_ref,
            artifact_type='citation_admission_ack', schema_version='citation_admission_ack_v1',
            created_by='fresh-test-tamper', **identity)
    before = manager.canonical_state()
    _observe(tmp_path, 'completed-admission-ack-' + damage, {'intent_ref': intent, 'ack_ref': ack_ref,
        'damage': damage, 'physical_count': len(physical), 'budget': before.model_dump(mode='json')})
    with pytest.raises((ValueError, RuntimeError, FileNotFoundError)):
        owner._associate(material, identity, lineage, lambda: None, budget_continuation=binding)
    assert len(physical) == 1 and manager.canonical_state() == before


def test_continuation_grant_read_uses_passive_existing_budget_owner(tmp_path, monkeypatch):
    state = _fixture(tmp_path)
    owner, manager, life, physical, identity, lineage, material, _, _ = state
    binding = _authorize(state)
    def create_forbidden(*_):
        raise AssertionError('continuation attempted create-capable Budget provider')
    monkeypatch.setattr(owner.post_write, 'budget_manager_provider', create_forbidden)
    rows, _, failure = owner._associate(material, identity, lineage, lambda: None, budget_continuation=binding)
    assert len(rows) == 1 and failure is None and len(physical) == 1


def test_second_prephysical_refusal_keeps_stable_semantic_operation(tmp_path):
    state = _fixture(tmp_path)
    owner, manager, life, physical, identity, lineage, material, refusal, _ = state
    binding = _authorize(state)
    _consume_test_owned_token_preimage(manager, 20000, 'second-refusal')
    old = owner.store.get_bytes(refusal['native_denial_ref'], **identity)
    with pytest.raises(CitationGroundingError) as caught:
        owner._associate(material, identity, lineage, lambda: None, budget_continuation=binding)
    second = caught.value.budget_failure_receipt
    _observe(tmp_path, 'second-prephysical-refusal', {'scope': 'NATIVE_OWNER_WITH_EXPLICIT_TEST_RESOURCE_PREIMAGE',
        'first': refusal, 'second': second, 'physical_count': len(physical),
        'budget': manager.canonical_state().model_dump(mode='json')})
    assert second['semantic_operation_ref'] == refusal['semantic_operation_ref']
    assert second['semantic_input_sha256'] == refusal['semantic_input_sha256']
    assert second['native_denial_ref'] != refusal['native_denial_ref'] and '/admission/' in second['native_denial_ref']
    assert owner.resolve_budget_refusal(receipt_ref=second['native_denial_ref'],
        expected_sha256=second['native_denial_sha256'], identity=identity) == second
    assert physical == [] and manager.canonical_state().limits.model_tokens_total == 24000
    assert owner.store.get_bytes(refusal['native_denial_ref'], **identity) == old


@pytest.mark.parametrize('cut', ['empty', 'ack', 'intent', 'completed'])
def test_passive_native_admission_slot_reads_are_accounting_neutral(tmp_path, cut, monkeypatch):
    state = _fixture(tmp_path)
    owner, manager, life, physical, identity, lineage, material, _, _ = state
    binding = _authorize(state)
    if cut != 'empty':
        put = owner.store.put_json
        class Cut(BaseException):pass
        def interrupted(value, **kwargs):
            result = put(value, **kwargs)
            path = kwargs.get('artifact_ref', '')
            if '/admission/' in path and ((cut == 'ack' and path.endswith('/ack')) or (cut == 'intent' and path.endswith('/input'))):
                raise Cut()
            return result
        monkeypatch.setattr(owner.store, 'put_json', interrupted)
        if cut == 'completed':
            owner._associate(material, identity, lineage, lambda: None, budget_continuation=binding)
        else:
            with pytest.raises(Cut):
                owner._associate(material, identity, lineage, lambda: None, budget_continuation=binding)
        monkeypatch.setattr(owner.store, 'put_json', put)
    before = manager.canonical_state(); count = len(physical)
    slot = owner.read_budget_document_admission(binding, identity=identity)
    expected = 'COMPLETED' if cut == 'completed' else 'INDETERMINATE' if cut == 'intent' else 'PRE_CITATION_INTENT_SAFE'
    assert slot['phase'] == expected and slot['semantic_input_sha256'] == canonical_sha256(material)
    assert slot['input_ref'].endswith('/input') and slot['result_ref'].endswith('/result')
    assert (slot['ack'] is None) == (cut == 'empty')
    assert len(physical) == count == (1 if cut == 'completed' else 0) and manager.canonical_state() == before


@pytest.fixture
def native_materialization(tmp_path, request):
    """Actual one-Section Writer/Support/Verifier/Post-write/assembly, frozen before DOCUMENT validation."""
    import os
    captured = {}
    marker = 'BT fresh native fixture freezes after assembly'
    original_fail = SchemeGenerationStageService._terminalize_noncancel_failed_run
    original_package = TargetPostWriteCitationGrounding._package
    original_finalize = TargetPostWriteComposition._finalize_document_output_and_control
    wants_refusal = 'native_document_refusal' in request.fixturenames
    def package(owner, *args, **kwargs):
        result = original_package(owner, *args, **kwargs)
        captured.setdefault('native_packages', []).append(result[0].model_dump(mode='json'))
        return result
    def freeze(pw, **kwargs):
        captured.update(post_write=pw, arguments=kwargs)
        if wants_refusal:
            options = {key: kwargs[key] for key in ('task_id', 'run_id', 'document_id', 'project_input')}
            captured['reference'] = pw.capture_budget_materialization(kwargs['materialization'], **options)
            manager = pw.budget_manager_provider(kwargs['task_id'], kwargs['run_id'])
            before = pw.lifecycle_service.budget_preimage(kwargs['run_id'])
            assert before.run.status.value == 'running'
            drained = []
            owner = pw.citation_grounding
            associate = owner._associate
            def refuse_exact(material, identity, lineage, validate_current, **arguments):
                if material['visible_artifact']['payload_kind'] == 'document' and not drained:
                    _consume_test_owned_token_preimage(manager,
                        manager.canonical_state().remaining.model_tokens_total, 'paused-native-document')
                    drained.append(True)
                return associate(material, identity, lineage, validate_current, **arguments)
            error = None
            returned = None
            with patch.object(owner, '_associate', refuse_exact):
                try:
                    returned = original_finalize(pw, **kwargs)
                except CitationGroundingError as raised:
                    error = raised
            after = pw.lifecycle_service.budget_preimage(kwargs['run_id'])
            refusal = error.budget_failure_receipt if error is not None else None
            captured.update(manager=manager, drained=drained, error=error, refusal=refusal,
                native_preimage_before=before, native_preimage_after=after)
            body = {'scope':'ACTUAL_LIVE_NATIVE_DOCUMENT_OWNER_BEFORE_MAINLINE_SHUTDOWN_NOT_GRAPH_COMPLETION',
                'status':'NATIVE_REFUSAL_OBSERVED' if error is not None else 'UNREACHED_NATIVE_REFUSAL',
                'native_error_type':type(error).__name__ if error is not None else None,
                'native_error':str(error) if error is not None else None, 'refusal':refusal,
                'native_unexpected_return':str(returned) if returned is not None else None,
                'materialization_ref':captured['reference'].model_dump(mode='json'),
                'native_budget':manager.canonical_state().model_dump(mode='json'),
                'native_run_before':before.run.model_dump(mode='json'),
                'native_run_after':after.run.model_dump(mode='json'),
                'full_current_children_before':[row.model_dump(mode='json') for row in before.current_children],
                'full_current_children_after':[row.model_dump(mode='json') for row in after.current_children],
                'test_owned_token_preimage_applied':bool(drained)}
            observation = tmp_path / 'native-live-document-refusal-before-test-marker.json'
            with observation.open('x', encoding='utf-8') as stream:
                json.dump(body, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            captured['live_native_observation'] = observation
        raise RuntimeError(marker)
    def isolate(stage, *, payload, error, failed_node):
        if captured.get('post_write') is not None and failed_node == 'evaluate_document_gate' and error.message == marker:
            captured['isolated'] = True
            return None
        return original_fail(stage, payload=payload, error=error, failed_node=failed_node)
    with patch.object(TargetPostWriteCitationGrounding, '_package', package), \
         patch.object(TargetPostWriteComposition, '_finalize_document_output_and_control', freeze), \
         patch.object(SchemeGenerationStageService, '_terminalize_noncancel_failed_run', isolate):
        engine, calls, observations, args, summary = _grounding_run(tmp_path, allow_failure=True)
    assert captured.get('isolated') and observations and captured.get('native_packages')
    pw = captured['post_write']; options = captured['arguments']; materialization = options['materialization']
    ref = captured.get('reference')
    if ref is None:
        ref = pw.capture_budget_materialization(materialization, **{key: options[key] for key in ('task_id', 'run_id', 'document_id', 'project_input')})
    captured.update(engine=engine, calls=calls, materialization=materialization, reference=ref, root=tmp_path)
    return captured


def _materialization_options(fx):
    options = fx['arguments']
    return {**{key: options[key] for key in ('task_id', 'run_id', 'document_id', 'project_input')},
        'section_execution_results': fx['materialization'].source_sections}


def test_native_materialization_restore_never_reenters_section_or_cognition(native_materialization, monkeypatch):
    fx = native_materialization; pw = fx['post_write']; options = _materialization_options(fx)
    manager = pw.budget_manager_provider(options['task_id'], options['run_id']); before = manager.canonical_state()
    def forbidden(*_args, **_kwargs):
        raise AssertionError('completed native Section or cognition was re-executed')
    monkeypatch.setattr(pw, '_section', forbidden)
    monkeypatch.setattr(pw.citation_grounding.gateway.invoker, 'invoke', forbidden)
    restored = pw.restore_budget_materialization(fx['reference'], **options)
    assert restored == fx['materialization'] and manager.canonical_state() == before


@pytest.mark.parametrize('damage', ['extra_section', 'section_completion', 'document_entry', 'writer_title'])
def test_materialization_restore_rejects_native_chain_substitution(native_materialization, damage):
    fx = native_materialization; pw = fx['post_write']; options = _materialization_options(fx)
    _, raw = pw.citation_grounding.store.get_json_with_reference(fx['reference'].artifact_ref,
        task_id=options['task_id'], run_id=options['run_id'])
    raw = deepcopy(raw)
    if damage == 'extra_section':
        raw['section_results'].append(deepcopy(raw['section_results'][0]))
    elif damage == 'section_completion':
        # Exact native completion content hash remains mandatory.
        section = fx['materialization'].section_results[0]
        raw['section_results'][0]['validation_completion_ref'] = section.validation_completion_ref.model_copy(
            update={'completion_sha256': '0' * 64}, deep=True).model_dump(mode='json')
    elif damage == 'document_entry':
        raw['document']['document_entry_ref'] = raw['section_results'][0]['post_write_entry_ref']
    else:
        source = fx['materialization'].source_sections[0]
        altered = source.model_copy(update={'section': source.section.model_copy(update={'section_title': 'Unauthorized changed Writer title'})}, deep=True)
        options['section_execution_results'] = (altered,)
        raw['source_sections'] = [altered.model_dump(mode='json')]
    ref = pw.citation_grounding.store.put_json(raw,
        artifact_ref='artifact://fresh-test-materialization-substitution/' + canonical_sha256(raw),
        artifact_type='budget_document_materialization', schema_version=raw['schema_version'],
        created_by='fresh-native-chain-negative-test', task_id=options['task_id'], run_id=options['run_id'])
    manager = pw.budget_manager_provider(options['task_id'], options['run_id']); before = manager.canonical_state()
    _observe(fx['root'], 'materialization-attack-' + damage, {'scope': 'ACTUAL_NATIVE_POSTWRITE_CURSOR_GUARD',
        'original_ref': fx['reference'].model_dump(mode='json'), 'attack_ref': ref.model_dump(mode='json'),
        'damage': damage, 'budget': before.model_dump(mode='json')})
    with pytest.raises((ValueError, RuntimeError)):
        pw.restore_budget_materialization(ref, **options)
    assert manager.canonical_state() == before


@pytest.mark.parametrize('field', ['candidate_decisions', 'selected_materializations', 'consumer_authorization_decisions'])
def test_actual_native_context_governed_source_fields_are_compared(native_materialization, field):
    raw = native_materialization['native_packages'][0]
    assert 'context' not in raw and field in raw
    altered = deepcopy(raw)
    if altered[field]:
        altered[field][0]['fresh_test_unauthorized_change'] = 'changed-source-authority'
    else:
        altered[field] = [{'fresh_test_unauthorized_authority': 'added'}]
    assert TargetPostWriteCitationGrounding._admission_context(altered) != TargetPostWriteCitationGrounding._admission_context(raw)


@pytest.fixture
def native_document_refusal(native_materialization):
    """Read the exact DOCUMENT owner refusal captured while its native Gateway was live."""
    fx = dict(native_materialization); pw = fx['post_write']; options = fx['arguments']
    before = fx['native_preimage_before']
    error = fx['error']
    assert isinstance(error, CitationGroundingError), fx['live_native_observation']
    refusal = fx['refusal']
    manager = fx['manager']
    assert fx['drained'] and refusal and refusal['phase_proof_sha256']
    assert fx['native_preimage_after'] == before
    assert pw.lifecycle_service.budget_preimage(options['run_id']) == before
    fx.update(error=error, refusal=refusal, options=_materialization_options(fx))
    _observe(fx['root'], 'native-paused-refusal-before-pause', {
        'scope':'REAL_DOCUMENT_OWNER_ADMISSION_WITH_EXPLICIT_TEST_RESOURCE_PREIMAGE_NOT_GRAPH',
        'refusal':refusal, 'materialization_ref':fx['reference'].model_dump(mode='json'),
        'native_budget':manager.canonical_state().model_dump(mode='json'),
        'native_run_before':before.run.model_dump(mode='json'),
        'native_run_after':pw.lifecycle_service.budget_preimage(options['run_id']).run.model_dump(mode='json')})
    return fx


def _pause_native_document(fx):
    pw=fx['post_write']; life=pw.lifecycle_service; refusal=fx['refusal']
    identity={key:refusal[key] for key in ('task_id','run_id','section_id')}
    pre=life.budget_preimage(identity['run_id'])
    packet=pw.citation_grounding.store.put_json({'scope':'NATIVE_PAUSED_READ_ONLY_TEST','refusal':refusal},
        artifact_ref='artifact://fresh-native-paused-currentness/'+refusal['native_denial_sha256'],
        artifact_type='local_native_wait',schema_version='local_native_wait_v1',
        created_by='local-native-currentness-test',**identity)
    return life.pause_for_budget(identity['run_id'],pre.run.lifecycle_revision,
        expected_current_children=pre.current_children,operation_key='pause-native-currentness:'+refusal['native_denial_sha256'],
        wait_ref=packet.artifact_ref,wait_sha256=packet.content_hash.removeprefix('sha256:'),
        semantic_operation_ref=refusal['semantic_operation_ref'],semantic_input_sha256=refusal['semantic_input_sha256'],
        artifact_version=str(refusal['artifact_version']))


def test_paused_native_document_currentness_never_enters_cognition_or_write(native_document_refusal,monkeypatch):
    fx=native_document_refusal; pw=fx['post_write']; manager=fx['manager']
    wait=_pause_native_document(fx); before=manager.canonical_state(); pre=pw.lifecycle_service.budget_preimage(wait.resulting_run.identity.run_id)
    def forbidden(*_args,**_kwargs):raise AssertionError('PAUSED native read entered cognition/validation/commit')
    monkeypatch.setattr(pw,'_section',forbidden)
    monkeypatch.setattr(pw,'_complete_validation',forbidden)
    monkeypatch.setattr(pw,'_restore_terminal_control',forbidden)
    monkeypatch.setattr(pw.citation_grounding.gateway.invoker,'invoke',forbidden)
    monkeypatch.setattr(pw.database,'normal_write_transaction',forbidden)
    restored=pw.validate_budget_document_current(fx['refusal'],materialization_ref=fx['reference'],**fx['options'])
    _observe(fx['root'],'native-paused-currentness-positive',{'scope':'ACTUAL_NATIVE_OWNER_PAUSED_READ_NOT_GRAPH',
        'wait_event_ref':wait.event_ref,'wait_event_sha256':wait.event_sha256,
        'materialization_ref':fx['reference'].model_dump(mode='json'),
        'refused_artifact_ref':fx['refusal']['artifact_state_ref'],'native_budget':manager.canonical_state().model_dump(mode='json'),
        'native_children':[row.model_dump(mode='json') for row in pre.current_children]})
    assert restored==fx['materialization'] and manager.canonical_state()==before
    assert pw.lifecycle_service.budget_preimage(pre.run.identity.run_id)==pre


@pytest.mark.parametrize('damage',['artifact_head','claim_head','cancelled_run','cancelled_child','refusal_packet'])
def test_paused_native_document_currentness_rejects_stale_or_cancelled_authority(native_document_refusal,damage,monkeypatch):
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus,AttemptLifecycleStatus
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimLifecycleStatus,ClaimOperationContextSchema
    from apps.enterprise_document.schemas.post_write.foundation import ArtifactStateRefSchema
    fx=native_document_refusal; pw=fx['post_write']; manager=fx['manager']; refusal=deepcopy(fx['refusal'])
    if damage=='artifact_head':
        previous=ArtifactStateRefSchema.model_validate(refusal['artifact_state_ref'])
        record=pw.artifact_store.resolve_exact(previous); payload=deepcopy(record.canonical_payload_ref)
        payload['partial']=not payload['partial']
        next_record=pw.artifact_service.materializer.materialize(artifact_id=previous.artifact_id,
            artifact_version=previous.artifact_version+1,artifact_scope=previous.artifact_scope,
            canonical_payload_ref=payload,origin=record.origin,parent_artifact_ref=previous)
        with pw.database.run_write_guard_context(refusal['run_id']):
            pw.artifact_store.append_next_version(next_record,expected_parent_ref=previous)
    elif damage=='claim_head':
        projection=pw.citation_grounding.claim_projection
        failed=ArtifactStateRefSchema.model_validate(refusal['artifact_state_ref'])
        material=pw.citation_grounding.store.get_json(refusal['input_ref'],
            **{key:refusal[key] for key in ('task_id','run_id','section_id')})['input_material']
        snapshot=projection.resolve_committed_source_snapshot(post_write=pw,validation_run_id=material['validation_run_id'],
            subject_hash=material['subject_hash'],artifact_ref=failed,scope='DOCUMENT')
        claim=snapshot.sections[0].claim_records[0]; head=projection.claim_runtime.resolve_claim_head(claim.claim_ref)
        lineage=pw.lifecycle_service.resolve_active_lineage(refusal['run_id'],'section:'+claim.section_id)
        projection.claim_runtime.transition_claim_lifecycle(claim.claim_ref,head.head_revision,ClaimLifecycleStatus.ACTIVE,
            ClaimLifecycleStatus.INVALIDATED,'fresh-native-currentness-test:claim-invalidated',
            ClaimOperationContextSchema(active_lineage=lineage,authorization_ref='test-explicit-currentness-change',operation_purpose='claim-lifecycle'))
    elif damage=='cancelled_child':
        child=pw.lifecycle_service.budget_preimage(refusal['run_id']).current_children[0]
        pw.lifecycle_service.transition_attempt(child.identity.attempt_id,child.lifecycle_revision,AttemptLifecycleStatus.CANCELLED,
            failure_ref='test-explicit-cancellation')
    wait=_pause_native_document(fx)
    if damage=='cancelled_run':
        pw.lifecycle_service.transition_run(refusal['run_id'],wait.resulting_run.lifecycle_revision,
            RunLifecycleStatus.CANCELLED,reason_ref='test-explicit-cancellation')
    elif damage=='refusal_packet':
        refusal['artifact_state_ref']=fx['materialization'].document.document_artifact_ref.model_dump(mode='json')
    before=manager.canonical_state(); pre=pw.lifecycle_service.budget_preimage(refusal['run_id'])
    def forbidden(*_args,**_kwargs):raise AssertionError('stale PAUSED read dispatched cognition')
    monkeypatch.setattr(pw.citation_grounding.gateway.invoker,'invoke',forbidden)
    _observe(fx['root'],'native-paused-currentness-negative-'+damage,{'scope':'ACTUAL_NATIVE_OWNER_SOURCE_OR_CANCELLATION_CHANGE',
        'damage':damage,'original_refusal':fx['refusal'],'supplied_refusal':refusal,
        'native_budget':before.model_dump(mode='json'),'native_run':pre.run.model_dump(mode='json'),
        'native_children':[row.model_dump(mode='json') for row in pre.current_children]})
    with pytest.raises((ValueError,RuntimeError)):
        pw.validate_budget_document_current(refusal,materialization_ref=fx['reference'],**fx['options'])
    assert manager.canonical_state()==before and pw.lifecycle_service.budget_preimage(refusal['run_id'])==pre


def test_bt03_native_document_support_after_exact_association_allowance_holds_again(tmp_path):
    """Reachability probe: real DOCUMENT Support, test-owned 24k used preimage, no Graph row claim."""
    observed = {}; physical = []
    from contracts.evidence import build_evidence_bundle, project_evidence_bundle
    from model_gateway.model_invoker import ModelInvoker
    from schemas.rag import RAGToolOutputSchema
    from tools.fake_rag_tool import FakeRAGTool
    import test_multi_agent_v3_workflow_recovery as workflow_tests
    import os
    original_rag = FakeRAGTool.run
    original_invoke = ModelInvoker.invoke
    def three_sources(tool, call):
        # Two Claims cite C1/C2, while original broad Support has three exact sources.
        # The DOCUMENT joint C1+C2 target is therefore a genuinely new pending item.
        with patch.dict(os.environ, {'FAKE_RAG_SCENARIO': 'citation_collision'}):
            responses = [original_rag(tool, call.model_copy(update={'tool_call_id': call.tool_call_id + '-source-' + str(i)})) for i in range(3)]
        outputs = [RAGToolOutputSchema.model_validate(row.result) for row in responses]
        citations = [output.citations[0].model_copy(update={'citation_id': 'C' + str(i+1), 'original_citation_id': 'C' + str(i+1)}) for i, output in enumerate(outputs)]
        bundle = build_evidence_bundle(query=outputs[0].query, rewritten_queries=[],
            selected_chunks=[output.retrieved_chunks[0] for output in outputs], dropped_chunks=[], citations=citations,
            trace=outputs[0].trace, max_context_chars=12000, extra=outputs[0].evidence.extra)
        bundle.assessment = outputs[0].evidence.assessment
        bundle.item_assessments = [output.evidence.item_assessments[0].model_copy(update={'evidence_id': bundle.selected_evidence_ids[i]}) for i, output in enumerate(outputs)]
        context, chunks, citations = project_evidence_bundle(bundle)
        output = outputs[0].model_copy(update={'evidence': bundle, 'context': context, 'retrieved_chunks': chunks, 'citations': citations})
        return responses[0].model_copy(update={'tool_call_id': call.tool_call_id, 'result': output.model_dump(mode='json')})
    def semantic_oracle(invoker, request, model_name):
        response = original_invoke(invoker, request, model_name)
        if request.extra.get('call_purpose') == 'scheme_section_generation':
            return response.model_copy(update={'content': TEXT * 2 + '[C1][C2]'}, deep=True)
        if request.extra.get('call_purpose') == 'citation_association':
            material = _association_input(request)
            ids = [row['claim_target_id'] for row in material['candidate_claim_targets']]
            assert len(ids) == 2 and len(material['eligible_occurrence_refs']) == 2
            document = material['visible_artifact']['payload_kind'] == 'document'
            rows = [{'occurrence_ref': ref, 'state': 'ASSOCIATED',
                     'associated_claim_target_ids': [ids[0] if document else ids[i]]}
                    for i, ref in enumerate(material['eligible_occurrence_refs'])]
            return response.model_copy(update={'content': json.dumps({'associations': rows})}, deep=True)
        return response
    def hook(pw, kwargs, original_complete):
        if kwargs['scope'].value != 'DOCUMENT':
            return original_complete(pw, **kwargs)
        owner = pw.citation_grounding
        manager = pw.budget_manager_provider(pw.lifecycle_service.load_run_identity(kwargs['run_id']).task_id, kwargs['run_id'])
        before = manager.canonical_state()
        assert before.limits.model_tokens_total == 24000 and not before.grant_history
        associate = owner._associate
        drained = []
        def reject_first_document_association(material, identity, lineage, validate_current, **options):
            if material['visible_artifact']['payload_kind'] == 'document' and not drained:
                actual = manager.canonical_state()
                _consume_test_owned_token_preimage(manager, actual.remaining.model_tokens_total, 'document-native-bt03')
                drained.append(actual.model_dump(mode='json'))
            return associate(material, identity, lineage, validate_current, **options)
        with patch.object(owner, '_associate', reject_first_document_association):
            try:
                original_complete(pw, **kwargs)
            except Exception as error:
                _observe(tmp_path, 'bt03-native-first-document-refusal', {
                    'scope': 'EXACT_DOCUMENT_ASSOCIATION_PREPHYSICAL_ADMISSION_WITH_TEST_RESOURCE_PREIMAGE',
                    'drained_at_exact_association': drained, 'error_type': type(error).__name__,
                    'error_message': str(error), 'error_origin': str(getattr(error, 'failure_origin', None)),
                    'refusal': getattr(error, 'budget_failure_receipt', None),
                    'native_budget': manager.canonical_state().model_dump(mode='json')})
                if not isinstance(error, CitationGroundingError) or error.budget_failure_receipt is None:
                    raise
                refusal = error.budget_failure_receipt
            else:
                raise AssertionError('actual DOCUMENT association did not produce first native refusal')
        identity = {key: refusal[key] for key in ('task_id', 'run_id', 'section_id')}
        failed = owner.store.get_json(refusal['native_denial_ref'], **identity)
        allowance = failed['failure']['native_denial']['required']
        binding = _authorize_exact_native_refusal(owner, manager, pw.lifecycle_service, identity, refusal, allowance)
        invoke = owner.gateway.invoker.invoke
        def measured(request, model_name):
            physical.append(request.model_copy(deep=True))
            response = invoke(request, model_name)
            if request.extra.get('call_purpose') == 'citation_association':
                response = response.model_copy(update={'token_usage': TokenUsageSchema(prompt_tokens=allowance, completion_tokens=0, total_tokens=allowance)}, deep=True)
            return response
        owner.gateway.invoker.invoke = measured
        old = owner.store.get_bytes(refusal['native_denial_ref'], **identity)
        try:
            original_complete(pw, **kwargs, budget_continuation=binding)
        except Exception as second:
            observed.update(error=second, refusal=getattr(second, 'budget_failure_receipt', None),
                first_refusal=refusal, binding=binding, task_id=identity['task_id'], run_id=identity['run_id'])
            _observe(tmp_path, 'bt03-native-pending-document-support', {
                'scope': 'ACTUAL_DOCUMENT_CITATION_SUPPORT_WITH_EXPLICIT_TEST_RESOURCE_PREIMAGE_NOT_GRAPH',
                'original_refusal': refusal, 'binding': binding, 'second_error_type': type(second).__name__,
                'second_error_origin': str(getattr(second, 'failure_origin', None)),
                'second_refusal': getattr(second, 'budget_failure_receipt', None),
                'physical_invoker_requests': [r.model_dump(mode='json') for r in physical],
                'native_budget': manager.canonical_state().model_dump(mode='json'),
                'native_children': [r.model_dump(mode='json') for r in pw.lifecycle_service.budget_preimage(identity['run_id']).current_children],
                'original_refusal_bytes_unchanged': owner.store.get_bytes(refusal['native_denial_ref'], **identity) == old,
            })
            raise
        else:
            _observe(tmp_path, 'bt03-native-pending-document-support', {
                'scope': 'ACTUAL_DOCUMENT_CITATION_SUPPORT_WITH_EXPLICIT_TEST_RESOURCE_PREIMAGE_NOT_GRAPH',
                'original_refusal': refusal, 'binding': binding, 'unexpected_completion': True,
                'physical_invoker_requests': [r.model_dump(mode='json') for r in physical],
                'native_budget': manager.canonical_state().model_dump(mode='json')})
            raise AssertionError('finite association-only allowance silently completed pending DOCUMENT work')
    project = workflow_tests._w13_current_new_task_project_input(
        workflow_tests._w13_single_section_project_input('task-gnd345-multi-claim'))
    project = project.model_copy(update={'generation_requirements': project.generation_requirements.model_copy(
        update={'min_section_chars': 40}, deep=True)}, deep=True)
    with patch.object(ModelInvoker, 'invoke', semantic_oracle):
        engine, calls, observations, arguments, summary = _grounding_run(tmp_path, scenario='multi-claim',
            project_override=project, rag_provider=three_sources, validation_hook=hook, allow_failure=True)
    _observe(tmp_path, 'bt03-native-reachability-outcome', {
        'scope': 'ACTUAL_MAINLINE_NATIVE_DOCUMENT_PROBE_BASELINE_24000_FRESH_SMALL_BODY_TWO_CLAIMS_THREE_SOURCES',
        'top_level_payload': workflow_tests._latest_w13_top_level_payload(engine).model_dump(mode='json'),
        'actual_model_requests': [r.model_dump(mode='json') for r in calls],
        'completed_validation_scopes': [row[0] for row in observations],
        'successor_observed': bool(observed),
    })
    assert observed, 'native DOCUMENT probe failed to reach the successor operation'
    assert len(physical) == 1 and physical[0].extra['call_purpose'] == 'citation_association'
    assert observed['refusal'] is not None, 'next native capacity refusal lost its positive immutable continuation carrier'
    assert observed['refusal']['phase_proof_sha256']
    assert observed['refusal']['native_denial_ref'] != observed['first_refusal']['native_denial_ref']
