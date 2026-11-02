"""Step5 directed carrier/routing controls, not native business acceptance.

Explicit test-owned document/checkpoint/materialization refs verify strict carrier
bindings only. Real Factory fixed24k hold and actual native grant/continuation
tests are separate; these fixtures do not prove physical-call/accounting absence.
"""
from copy import deepcopy

import pytest

from apps.enterprise_document.schemas import scheme_generation_graph as graph_schema
from apps.enterprise_document.schemas.scheme_generation_graph import SchemeGenerationGraphPayloadSchema


def _schema(name):
    # RED collects the existing module; actual missing owner API fails at call.
    return getattr(graph_schema, name)
from apps.enterprise_document.workflows.top_level_business_graph import (
    _effective_business_route, phase2_gate_routes,
)
from core.hashing import canonical_sha256
from test_fpbca_budget_lifecycle import _fresh, _pause, _resume


def _test_owned_packet(tmp_path):
    _, preimage = _fresh(tmp_path, count=8)
    task_id, run_id = preimage.run.identity.task_id, preimage.run.identity.run_id
    ref = {
        'artifact_ref':'artifact://test-owned-materialization',
        'artifact_type':'budget_document_materialization',
        'content_hash':'sha256:' + canonical_sha256('test-owned-material'),
        'schema_version':'budget_document_materialization_v1', 'size_bytes':19,
        'created_by':'explicit-carrier-fixture', 'task_id':task_id, 'run_id':run_id,
    }
    key = canonical_sha256('test-owned-document-input')
    base = 'artifact://citation-association/' + key
    refusal = dict(task_id=task_id, run_id=run_id, section_id='test-owned-document-scope',
        input_ref=base+'/input', input_sha256=canonical_sha256('test-owned-input-receipt'),
        native_denial_ref=base+'/result', native_denial_sha256=canonical_sha256('test-owned-failed-receipt'),
        phase_proof_ref=base+'/result', phase_proof_sha256=canonical_sha256('test-owned-native-phase-proof'),
        semantic_input_sha256=key, semantic_operation_ref='citation-association-call://sha256/'+key,
        artifact_state_ref=dict(artifact_id='test-owned-document',artifact_version=1,
            artifact_hash=canonical_sha256('test-owned-document'),artifact_scope='DOCUMENT'),artifact_version=1)
    epoch = {'test-owned-producer.py':canonical_sha256('test-owned-source')}
    cursor = dict(task_id=task_id,run_id=run_id,document_id='test-owned-document',
        canonical_request_ref='run-identity:'+run_id,
        canonical_request_sha256=preimage.run.identity.canonical_request_sha256,
        project_input_sha256=canonical_sha256('test-owned-project'),input_snapshot_version='snapshot-budget-pause',
        workflow_id='test-owned-workflow',workflow_version='v1',workflow_namespace='test-owned-workflow:v1',
        checkpoint_config={'configurable':{'thread_id':run_id,'checkpoint_id':'test-owned-checkpoint'}},
        checkpoint_state_sha256=canonical_sha256('test-owned-checkpoint'),
        predecessor_transition_count=1,predecessor_committed_node_refs=({'node_id':'final_artifact_review'},),
        source_sections=(),budget_materialization_ref=ref,source_epoch=epoch,
        source_epoch_sha256=canonical_sha256(epoch))
    return dict(refusal=refusal,cursor=cursor,run_preimage=preimage.run.model_dump(mode='json'),
        current_children=[row.model_dump(mode='json',round_trip=True) for row in preimage.current_children],
        child_preimage_sha256=preimage.child_preimage_sha256,
        runner_closure_ref='artifact://test-owned-original-runner-closure',
        runner_closure_sha256=canonical_sha256('test-owned-runner-closure'))


def test_wait_carrier_round_trip_preserves_full_native_children_and_int_artifact_version(tmp_path):
    raw = _test_owned_packet(tmp_path)
    packet = _schema('BudgetDocumentWaitPacketSchema').model_validate(raw)
    restored = _schema('BudgetDocumentWaitPacketSchema').model_validate(packet.model_dump(mode='json'))
    assert restored == packet and len(restored.current_children) == 8
    assert type(restored.refusal.artifact_version) is int
    assert restored.cursor.pending_node == 'evaluate_document_gate'
    assert restored.runner_closed is True


@pytest.mark.parametrize('damage',[
    'missing-child','reordered-child','changed-child-record','wrong-child-run',
    'wrong-child-hash','wrong-run','wrong-refusal-run','runner-active',
])
def test_wait_carrier_rejects_forged_full_preimage_or_owner_identity(tmp_path,damage):
    raw = _test_owned_packet(tmp_path)
    if damage == 'missing-child': raw['current_children'].pop()
    elif damage == 'reordered-child': raw['current_children'].reverse()
    elif damage == 'changed-child-record': raw['current_children'][0]['lifecycle_revision'] += 1
    elif damage == 'wrong-child-run': raw['current_children'][0]['identity']['run_id'] = 'foreign-run'
    elif damage == 'wrong-child-hash': raw['child_preimage_sha256'] = '0'*64
    elif damage == 'wrong-run': raw['run_preimage']['identity']['run_id'] = 'foreign-run'
    elif damage == 'wrong-refusal-run': raw['refusal']['run_id'] = 'foreign-run'
    else: raw['runner_closed'] = False
    with pytest.raises(ValueError): _schema('BudgetDocumentWaitPacketSchema').model_validate(raw)


@pytest.mark.parametrize('damage',['input-pair','failure-pair','proof-pair','artifact-version','null-proof','extra-authority'])
def test_refusal_carrier_rejects_wrong_exact_pair_and_unproved_phase(tmp_path,damage):
    raw = _test_owned_packet(tmp_path)['refusal']
    if damage == 'input-pair': raw['input_ref'] += '/altered'
    elif damage == 'failure-pair': raw['native_denial_ref'] += '/altered'
    elif damage == 'proof-pair': raw['phase_proof_ref'] = 'artifact://foreign-proof'
    elif damage == 'artifact-version': raw['artifact_version'] += 1
    elif damage == 'null-proof': raw['phase_proof_sha256'] = None
    else: raw['may_dispatch'] = True
    with pytest.raises(ValueError): _schema('BudgetDocumentRefusalSchema').model_validate(raw)


@pytest.mark.parametrize('damage',['request-anchor','materialization-run','source-epoch','source-hash','pending-node','committed-count'])
def test_cursor_carrier_rejects_other_scope_source_epoch_or_pending_node(tmp_path,damage):
    raw = _test_owned_packet(tmp_path)['cursor']
    if damage == 'request-anchor': raw['canonical_request_ref'] = 'request-extra:untrusted'
    elif damage == 'materialization-run': raw['budget_materialization_ref']['run_id'] = 'foreign-run'
    elif damage == 'source-epoch': raw['source_epoch']['test-owned-producer.py'] = '0'*64
    elif damage == 'source-hash': raw['source_epoch_sha256'] = '0'*64
    elif damage == 'pending-node': raw['pending_node'] = 'execute_sections'
    else: raw['predecessor_transition_count'] += 1
    with pytest.raises(ValueError): _schema('BudgetDocumentCursorSchema').model_validate(raw)


def test_historical_graph_payload_remains_passive_and_has_no_budget_authority():
    payload = SchemeGenerationGraphPayloadSchema(task_id='historical-test-owned-task',run_id='historical-test-owned-run')
    assert payload.pause_purpose is None and payload.budget_document_continuation is None
    assert payload.budget_wait_ref is None and payload.budget_materialization_ref is None
    assert payload.business_revision == 0 and payload.executed_nodes == []


@pytest.mark.parametrize('route',['deliverable','rejected','blocked','technical_failure'])
def test_existing_gate_route_exports_preserved(route):
    assert _effective_business_route('gate',{'business_route':route}) == route
    assert route in phase2_gate_routes()


def test_budget_route_is_pure_and_does_not_change_legacy_gate_map():
    before = phase2_gate_routes()
    assert _effective_business_route('gate',{'business_route':'budget_wait'}) == 'budget_wait'
    assert phase2_gate_routes() == before and 'budget_wait' not in before
    assert _effective_business_route('gate',{'business_route':'unknown-authority'}) == 'technical_failure'


def test_passive_lifecycle_budget_getter_is_exact_native_current_authority(tmp_path):
    service, preimage = _fresh(tmp_path)
    assert service.current_budget_transition(preimage.run.identity.run_id) is None
    wait = _pause(service,preimage)
    assert service.current_budget_transition(preimage.run.identity.run_id) == wait
    resumed = _resume(service,wait)
    assert service.current_budget_transition(preimage.run.identity.run_id) == resumed
    assert service.budget_preimage(preimage.run.identity.run_id).current_children == preimage.current_children


def test_passive_lifecycle_budget_getter_does_not_restore_stale_wait_after_cancel(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
    service, preimage = _fresh(tmp_path)
    wait = _pause(service,preimage)
    service.transition_run(preimage.run.identity.run_id,wait.resulting_run.lifecycle_revision,
        RunLifecycleStatus.CANCELLED,reason_ref='explicit-directed-cancellation')
    assert service.current_budget_transition(preimage.run.identity.run_id) is None


# Root-owned native graph_native_pause_test_01 remains a separate actual Factory
# native8Section fixed24000 test. Subsequent integration rows require actual
# accepted Budget/Lifecycle events, citation ACK/intent/result, Graph checkpoint,
# Task currentness and real local physical/accounting observations.
