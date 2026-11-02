from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from eval.full_chain.domains.generation_claim import (model_representations,extract_generation_claim,
    check_paragraph_projection,check_provenance_chain,check_verifier_correctness,
    evaluate_generation_claims,check_cross_section_consumption,check_local_correction_chain)
from eval.full_chain.snapshot import SnapshotView
from eval.full_chain.execution import invoke_scenario,AuthorizationError


def test_partial_and_empty_positive_basis_cannot_pass_edges():
    from eval.full_chain.domains.generation_claim import check_generation_edge
    assert check_generation_edge('X13',{'binding':{'binding_ref':'b'}},{'support':{'binding_ref':'b'}}).measurement_state=='UNMEASURED'
    assert check_generation_edge('X14',{'support':{'support_ref':'s','supporting_refs':['A']}},{'result':{'support_ref':'s'}}).measurement_state=='UNMEASURED'
    assert check_verifier_correctness({})['state']=='UNMEASURED'
    assert check_verifier_correctness({'verdict':'VERIFIED','positive_basis_refs':[]})['positive_basis_valid'] is False


def test_generation_multi_instance_never_discards_second_bad_edge():
    snapshot=SnapshotView.from_records({'W-PARA':[{'section_id':'s1','expected_claim_ids':['a']},{'section_id':'s2','expected_claim_ids':['b']}],
        'W-CLAIM':[{'section_id':'s1','claim_ids':['a']},{'section_id':'s2','claim_ids':['wrong']}]})
    batch=extract_generation_claim(snapshot)
    edges=[e for e in batch.edges if e.edge_id=='X11']
    assert len(edges)==2 and [e.assertion_result for e in edges]==[True,False]
    assert all(e.source_refs and e.target_refs for e in edges)
    ambiguous=SnapshotView.from_records({'W-PARA':[{'expected_claim_ids':['a']},{'expected_claim_ids':['b']}],
        'W-CLAIM':[{'claim_ids':['a']},{'claim_ids':['wrong']}]})
    assert all(e.assertion_result is None for e in extract_generation_claim(ambiguous).edges if e.edge_id=='X11')


def test_physical_representation_conflict_downgrades_entire_group(tmp_path):
    from test_full_chain_eval_capture import FixedClient,gateway,request,session,captured
    client=FixedClient();observer=session(tmp_path,'conflict')
    with observer: gateway(client).generate(request())
    prefix=captured(observer,tmp_path)
    raw=deepcopy(next(r for r in prefix['events'] if r.get('event_type')=='MODEL_CLIENT_RAW_RESPONSE'))
    raw['material']['content']='contradictory same physical identity'
    result=model_representations(prefix['events']+[raw])
    assert result['physical_attempts'][0]['raw_state']=='UNMEASURED'
    assert result['physical_attempts'][0]['completeness']=='CAPTURE_INCOMPLETE'


def test_consumer_allowed_text_cannot_override_original_writer(tmp_path):
    from test_full_chain_eval_integration import generation_edge_fixture
    from eval.full_chain.domains.generation_claim import check_generation_edge
    source,target,physical=generation_edge_fixture(tmp_path,'X10')
    target={'paragraphs':[{'text':'tampered'}],'allowed_text':'tampered'}
    assert check_generation_edge('X10',source,target,physical=physical).assertion_result is False


@pytest.mark.parametrize('case,scenario',[('W01','normal'),('W02','cancel')],ids=['W01','W02'])
def test_physical_gateway_four_representations(tmp_path,case,scenario):
    from backend.tests.test_full_chain_eval_capture import FixedClient,gateway,request,session,captured
    from core.runtime.execution_control import WorkflowExecutionControl,activate_execution_control,WorkflowExecutionCancelled
    observer=session(tmp_path,'generation')
    control=WorkflowExecutionControl.with_timeout(execution_id='generation-cancel',timeout_seconds=10)
    client=FixedClient(control=control if scenario=='cancel' else None)
    if scenario=='cancel':
        with observer,activate_execution_control(control),pytest.raises(WorkflowExecutionCancelled):
            gateway(client).generate(request())
    else:
        with observer:
            response=gateway(client).generate(request())
            observer.capture_model('WRITER_DELIVERY_PROJECTION',client.calls[0],response=response)
    prefix=captured(observer,tmp_path)
    result=model_representations(prefix['events'],capture_complete=prefix['completeness']=='COMPLETE')
    group=result['physical_attempts'][0]
    raw=group['representations']['MODEL_CLIENT_RAW_RESPONSE']['material']
    projected=group['representations']['MODEL_GATEWAY_PROJECTED_RESPONSE']['material']
    assert raw['success'] is True and raw['finish_reason']=='stop'
    if scenario=='cancel':
        assert projected['success'] is False and projected['finish_reason']=='cancelled'
    else:
        assert group['representations']['WRITER_DELIVERY_PROJECTION']['material']['content']==response.content
    batch=extract_generation_claim(SnapshotView.from_records({'diagnostic_events':prefix['events']+[prefix['seal']]}))
    assert any(s.payload.get('raw_state')=='MEASURED' for s in batch.stages)
    if scenario=='cancel': assert any(f.code=='GATEWAY_POST_RETURN_PROJECTION' for f in batch.findings)


@pytest.mark.parametrize('case',['W03'])
def test_same_paragraph_count_cannot_hide_replacement(case):
    writer={'content':'First.\n\nSecond.'}
    normal=check_paragraph_projection(writer,[{'text':'First.'},{'text':'Second.'}])
    replaced=check_paragraph_projection(writer,[{'text':'First.'},{'text':'First.'}])
    assert normal['preserved'] and not normal['duplicates']
    assert not replaced['preserved'] and replaced['duplicates']


@pytest.mark.parametrize('case',['W04'])
def test_complete_accounting_is_not_expected_claim_coverage(case):
    metrics=evaluate_generation_claims(['claim-A'],['claim-A','claim-B'],synthetic=True)
    recall=next(m for m in metrics if m.metric_id=='recall')
    assert recall.numerator==1 and recall.denominator==2 and recall.value==.5
    assert evaluate_generation_claims(['claim-A'])[0].measurement_state=='UNMEASURED'


@pytest.mark.parametrize('case',['W05','W06'])
def test_binding_support_verifier_are_independent(case):
    expected={k:'same' for k in ('project_id','scope','source_ref','version','container_ref','time','unit')}
    binding={**expected,'allowed_refs':['A','B']}
    support={'relation':'INSUFFICIENT','supporting_refs':['A']}
    result={'verdict':'VERIFIED','positive_basis_refs':['B']}
    bad_binding={**binding,'project_id':'foreign'}
    checks=check_provenance_chain(bad_binding,support,result,expected_binding=expected,
        expected_support={'relation':'INSUFFICIENT','supporting_refs':['A']},expected_verdict='UNSUPPORTED')
    assert checks['binding']['correct'] is False
    assert checks['support']['relation_correct'] and checks['support']['members_correct']
    assert checks['verifier']['positive_basis_valid'] is False and checks['verifier']['false_verified']
    normal=check_provenance_chain(binding,{'relation':'SUPPORTED','supporting_refs':['A']},
        {'verdict':'VERIFIED','positive_basis_refs':['A']},expected_binding=expected,
        expected_support={'relation':'SUPPORTED','supporting_refs':['A']},expected_verdict='VERIFIED')
    assert normal['binding']['correct'] and normal['support']['relation_correct'] and normal['verifier']['verdict_correct']


@pytest.mark.parametrize('case',['W07'])
def test_attempt4_exact_original_positive_members_keep_native_rejection(case):
    from backend.tests.test_local_verifier_recovery_integration import _history
    from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimVerificationRuntime,ClaimBindingError
    root,hashes,invocation,candidate,binding,support,receipt=_history()
    raw=deepcopy(candidate.model_dump(mode='json'))
    with pytest.raises(ClaimBindingError) as error:
        ClaimVerificationRuntime.validate_v2_provenance(binding,support,candidate)
    assert error.value.rule_code=='EVIDENCE_POSITIVE_BASIS_NOT_ADMITTED'
    checked=check_verifier_correctness({'verdict':candidate.recommended_verdict.value,
        'positive_basis_refs':candidate.positive_basis_refs},supporting_refs=support.supporting_refs,
        binding_allowed_refs=raw['positive_basis_refs'])
    assert checked['positive_basis_valid'] is False and len(checked['original_positive_basis_refs'])==2
    assert candidate.model_dump(mode='json')==raw
    assert {str(p):sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}==hashes


@pytest.mark.parametrize('case',['W08'])
def test_real_local_verifier_single_child_and_exact_replay(tmp_path,case):
    from backend.tests.test_local_verifier_recovery_integration import test_B_C_J_full_parent_chain_single_commit_native_budget_and_exact_replay
    test_B_C_J_full_parent_chain_single_commit_native_budget_and_exact_replay(tmp_path)
    # The production fixture above proves the immutable grant/root/child chain;
    # evaluate the normalized events using the exact persisted ledger locator.
    from backend.tests.local_verifier_recovery_helpers import reopen_fixture
    fx=reopen_fixture(tmp_path)
    persisted=[json.loads(line) for line in fx.runtime.ledger_path.read_text(encoding='utf-8').splitlines()]
    grants=[r['payload'] for r in persisted if r['event_type']=='verifier_correction_authorized']
    commits=[r for r in persisted if r['event_type']=='verification_committed']
    records=[{'root_ref':g['root']['original_root_ref'],'slot_ref':g['authorization_ref'],
        'correction_ordinal':g['correction_ordinal']} for g in grants]
    records.append({'root_ref':grants[0]['root']['original_root_ref'],'formal_commit':len(commits)==1})
    assert check_local_correction_chain(records)['correct']


@pytest.mark.parametrize('case',['W09'])
def test_durable_slot_reopen_concurrency_and_unknown(tmp_path,case):
    from backend.tests.test_local_verifier_recovery_integration import (test_D_second_violation_permanent_termination_restart_and_concurrency,
        test_F_unknown_no_blind_redispatch_retains_native_budget)
    root=tmp_path/'second'; root.mkdir()
    test_D_second_violation_permanent_termination_restart_and_concurrency(root)
    root=tmp_path/'unknown'; root.mkdir()
    test_F_unknown_no_blind_redispatch_retains_native_budget(root,'UNKNOWN',False)
    bad=check_local_correction_chain([{'root_ref':'r','outcome':'UNKNOWN','redispatched':True}])
    assert 'UNKNOWN_BLIND_REDISPATCH' in bad['findings']


@pytest.mark.parametrize('case',['W10'])
def test_stale_and_existing_formal_result_native_fences(tmp_path,case):
    from backend.tests.test_local_verifier_recovery_integration import (test_negative_initial_stale_is_not_candidate_contract_recovery,
        test_negative_existing_formal_result_head_cannot_authorize_or_dispatch)
    root=tmp_path/'stale'; root.mkdir(); test_negative_initial_stale_is_not_candidate_contract_recovery(root)
    root=tmp_path/'existing'; root.mkdir(); test_negative_existing_formal_result_head_cannot_authorize_or_dispatch(root)


@pytest.mark.parametrize('case',['W11','W12'])
def test_verified_dependency_authorization_independent_of_memory(case):
    source={'verdict':'VERIFIED','revision':3,'allowed_roles':['writer'],'allowed_scopes':['chapter2']}
    consumer={'accepted_dependency':True,'source_revision':3,'role':'writer','scope':'chapter2','memory_enabled':False,'selected':True}
    assert check_cross_section_consumption(source,consumer)['correct']
    consumer['accepted_dependency']=False
    assert 'NO_ACCEPTED_DEPENDENCY' in check_cross_section_consumption(source,consumer)['findings']


@pytest.mark.parametrize('case',['W13'])
def test_original_unsorted_and_attempt4_raw_are_not_precleaned(case):
    from backend.tests.test_bca014_verifier_reply_ref_normalization import test_frozen_real_a3_wire_only_loses_order_blocker_and_keeps_actual_verdict
    # Exact frozen source path from the existing reviewed fixture; no discovery.
    from backend.tests.test_bca014_verifier_reply_ref_normalization import A3_DIR
    test_frozen_real_a3_wire_only_loses_order_blocker_and_keeps_actual_verdict(A3_DIR)
    from backend.tests.test_local_verifier_recovery_integration import test_A_attempt4_original_members_still_rejected_readonly
    test_A_attempt4_original_members_still_rejected_readonly()


@pytest.mark.parametrize('case',['W14'])
def test_changed_prompt_old_response_never_exact_replay(case):
    with pytest.raises(AuthorizationError,match='MISMATCH'):
        invoke_scenario('ISOLATED_REPLAY','fixed',{'fixed':lambda x:x},{},
            exact_request_hash='new-prompt',recorded_request_hash='old-prompt')
    result=invoke_scenario('ISOLATED_REPLAY','fixed',{'fixed':lambda x:x},{},
        exact_request_hash='new-prompt',recorded_request_hash='old-prompt',injection=True)
    assert result['replay_kind']=='INJECTION'


def test_missing_raw_no_seal_and_client_exception_never_backfill():
    projected={'event_type':'MODEL_GATEWAY_PROJECTED_RESPONSE','model_call_id':'l','provider_attempt_id':'p','material':{'success':True,'finish_reason':'stop'}}
    request={'event_type':'MODEL_ATTEMPT_REQUEST','model_call_id':'l','provider_attempt_id':'p','material':{'prompt':'visible'}}
    result=model_representations([request,projected],capture_complete=False)
    assert result['physical_attempts'][0]['raw_state']=='UNMEASURED'
    exception={'event_type':'MODEL_CLIENT_EXCEPTION','model_call_id':'l','provider_attempt_id':'p','exception_type':'RuntimeError'}
    result=model_representations([request,exception,projected])
    assert result['physical_attempts'][0]['raw_state']=='NO_RAW_RESPONSE'


@pytest.mark.parametrize('bypass',[None,False,True])
def test_deferred_D11_requires_explicit_bypass_for_not_applicable(bypass):
    from eval.full_chain.domains.generation_claim import extract_generation_claim
    from eval.full_chain.snapshot import SnapshotView
    records={} if bypass is None else {'W-BYPASS':{'run_id':'bypass-run','operation_id':'deferred-branch',
        'executed':False,'bypassed':bypass,'reason':'FROZEN_DEFER'}}
    batch=extract_generation_claim(SnapshotView.from_records(records),subject_id='bypass-run')
    stage=next(stage for stage in batch.stages if stage.unit_id=='D11')
    assert stage.measurement_state==('N/A' if bypass is True else 'UNMEASURED' if bypass is None else 'MEASURED')
    assert stage.applicable is (False if bypass is True else None)
    assert stage.observed_quality_status=='UNKNOWN'
    assert not any(metric.unit_id=='D11' and metric.assertion_result is True for metric in batch.metrics)
    if bypass is not None:
        assert stage.evidence_refs and stage.payload['bypassed'] is bypass
