"""Step 4 real citation/Budget/Lifecycle/IPC owner controls, not graph acceptance."""
from copy import deepcopy
from types import SimpleNamespace
from threading import RLock
import json
import pytest
from core.hashing import canonical_sha256
from core.persistence.crash_safe_jsonl import canonical_json_sha256
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunIdentitySchema, RunLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.budgets import CanonicalBudgetTopupAuthorizationSchema, CanonicalBudgetTopupRequestSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import TargetPostWriteCitationGrounding, CitationGroundingError
from test_fpbca_budget_document_phase_proof import _manager, _seed, _gateway


def _fixture(root):
    budget=_manager(root);_seed(budget,24000)
    life=RunAttemptLifecycleService(root/'lifecycle.jsonl')
    life.claim_new_run(RunIdentitySchema(task_id=budget.task_id,run_id=budget.run_id,tenant_id='tenant-local',canonical_request_sha256=canonical_sha256('local-native-request')))
    life.transition_run(budget.run_id,0,RunLifecycleStatus.RUNNING)
    for i in range(8):
        row=life.open_attempt(budget.run_id,'section:s'+str(i),'snapshot-local',attempt_id='a'+str(i))
        life.activate_attempt(row.identity.attempt_id,0)
    gateway,clients,physical=_gateway(budget)
    original=clients[0].generate
    def reply(request):
        response=original(request)
        return response.model_copy(update={'content':json.dumps({'associations':[{'occurrence_ref':'occurrence://local','state':'ASSOCIATED','associated_claim_target_ids':['claim-local']}]} )})
    clients[0].generate=reply
    owner=object.__new__(TargetPostWriteCitationGrounding)
    owner.store=FileArtifactStore(root/'artifacts');owner._lock=RLock();owner.gateway=gateway
    owner.post_write=SimpleNamespace(budget_manager_provider=lambda *_:budget,lifecycle_service=life)
    owner.claim_projection=SimpleNamespace(claim_runtime=SimpleNamespace(lifecycle_service=life))
    context={'model_capability_binding':{'context':65536,'max_output':8192},'source_binding':{'source':'local-authored-unchanged'}}
    def package(material,lineage,occurrence):
        raw={'context':deepcopy(context),'model_input':{'final_input_material':{'inline_final_input_payload':{'messages':[{'role':'user','content':'Exact unchanged DOCUMENT source'}]}},'final_model_input_sha256':canonical_sha256('unchanged-messages')}}
        return SimpleNamespace(model_dump=lambda **_:deepcopy(raw),model_input=SimpleNamespace(final_input_material=SimpleNamespace(inline_final_input_payload=raw['model_input']['final_input_material']['inline_final_input_payload']),final_model_input_sha256=raw['model_input']['final_model_input_sha256']),package_ref=SimpleNamespace(context_package_id=occurrence)),8192
    owner._package=package
    identity={'task_id':budget.task_id,'run_id':budget.run_id,'section_id':'s0'}
    lineage=life.resolve_active_lineage(budget.run_id,'section:s0')
    material={'visible_artifact':{'payload_kind':'document'},'artifact_state_ref':{'artifact_version':1},'candidate_claim_targets':[{'claim_target_id':'claim-local'}],'eligible_occurrence_refs':['occurrence://local'],'no_applicable_allowed':{'occurrence://local':False}}
    with pytest.raises(CitationGroundingError) as caught:owner._associate(material,identity,lineage,lambda:None)
    refusal=caught.value.budget_failure_receipt
    assert owner.resolve_budget_refusal(receipt_ref=refusal['native_denial_ref'],expected_sha256=refusal['native_denial_sha256'],identity=identity)==refusal
    return owner,budget,life,physical,identity,lineage,material,refusal,context


def _authorize(state):
    owner,budget,life,physical,identity,lineage,material,refusal,context=state
    pre=life.budget_preimage(budget.run_id)
    packet=owner.store.put_json({'scope':'STEP4_OWNER_CONTROL_NOT_GRAPH','native_refusal':refusal,'children':[r.model_dump(mode='json') for r in pre.current_children]},artifact_ref='artifact://local-native-wait',artifact_type='local_native_wait',schema_version='local_native_wait_v1',created_by='test-explicit-command',**identity)
    wait=life.pause_for_budget(budget.run_id,pre.run.lifecycle_revision,expected_current_children=pre.current_children,operation_key='native-local-pause',wait_ref=packet.artifact_ref,wait_sha256=packet.content_hash.removeprefix('sha256:'),semantic_operation_ref=refusal['semantic_operation_ref'],semantic_input_sha256=refusal['semantic_input_sha256'],artifact_version='1')
    before=budget.canonical_state()
    baseline=canonical_json_sha256({'policy_ref':before.policy_ref,'policy_version':before.policy_version,'limits':before.limits.model_dump(mode='json')})
    command={'authorization_kind':'SINGLE_USE','authorization_id':'explicit-local-operator-command','command_source':'API','issuer':'test-explicit-operator','task_id':budget.task_id,'run_id':budget.run_id,'canonical_request_ref':'run-identity:'+budget.run_id,'canonical_request_sha256':pre.run.identity.canonical_request_sha256,'semantic_operation_ref':wait.semantic_operation_ref,'semantic_input_sha256':wait.semantic_input_sha256,'artifact_version':'1','baseline_policy_sha256':baseline,'authorized_delta':{'model_tokens_total':20000},'cumulative_ceiling':{**before.limits.model_dump(mode='json'),'model_tokens_total':44000},'single_use_wait_event_ref':wait.event_ref,'single_use_native_denial_sha256':refusal['native_denial_sha256'],'single_use_child_preimage_sha256':pre.child_preimage_sha256}
    native=owner.store.put_json(command,artifact_ref='artifact://trusted-local-authorization',artifact_type='budget_topup_command',schema_version='budget_topup_command_v1',created_by='trusted-application-command',**identity)
    auth=CanonicalBudgetTopupAuthorizationSchema(**command,authorization_ref=native.artifact_ref,authorization_sha256=native.content_hash.removeprefix('sha256:'))
    original=owner.store.get_json(refusal['native_denial_ref'],**identity)
    required=original['failure']['native_denial']['requested']
    request=CanonicalBudgetTopupRequestSchema(approval_id='client-local',authorization_ref=auth.authorization_ref,authorization_sha256=auth.authorization_sha256,task_id=budget.task_id,run_id=budget.run_id,canonical_request_ref=auth.canonical_request_ref,canonical_request_sha256=auth.canonical_request_sha256,semantic_operation_ref=wait.semantic_operation_ref,semantic_input_sha256=wait.semantic_input_sha256,artifact_version='1',native_denial_ref=refusal['native_denial_ref'],native_denial_sha256=refusal['native_denial_sha256'],phase_proof_ref=refusal['phase_proof_ref'],phase_proof_sha256=refusal['phase_proof_sha256'],wait_event_ref=wait.event_ref,wait_event_sha256=wait.event_sha256,child_preimage_sha256=pre.child_preimage_sha256,baseline_policy_sha256=baseline,expected_budget_revision=before.revision,expected_lifecycle_revision=wait.resulting_run.lifecycle_revision,delta={'model_tokens_total':20000},required=required)
    def resolver(bound):
        reference,value=owner.store.get_json_with_reference(bound.authorization_ref,**identity)
        assert reference.content_hash=='sha256:'+bound.authorization_sha256
        return CanonicalBudgetTopupAuthorizationSchema(**value,authorization_ref=reference.artifact_ref,authorization_sha256=bound.authorization_sha256)
    grant=budget.apply_topup(request,authorization_resolver=resolver,paused_authorization_fence=life.budget_authorization_fence)
    resumed=life.resume_budget_wait(budget.run_id,wait.resulting_run.lifecycle_revision,expected_current_children=wait.current_children,operation_key='native-local-resume',wait_event_ref=wait.event_ref,wait_event_sha256=wait.event_sha256,grant_ref=grant.grant_ref,grant_sha256=grant.grant_sha256)
    return {'schema_version':'document_budget_continuation_v1','predecessor_result_ref':refusal['native_denial_ref'],'predecessor_result_sha256':refusal['native_denial_sha256'],'accepted_grant_ref':grant.grant_ref,'accepted_grant_sha256':grant.grant_sha256,'resume_event_ref':resumed.event_ref,'resume_event_sha256':resumed.event_sha256}


def test_new_document_admission_dispatch_once_old_failure_unchanged(tmp_path):
    state=_fixture(tmp_path);owner,budget,life,physical,identity,lineage,material,refusal,_=state
    original=owner.store.get_bytes(refusal['native_denial_ref'],**identity)
    children=life.budget_preimage(budget.run_id).current_children
    binding=_authorize(state);before=budget.canonical_state()
    rows,intent,failure=owner._associate(material,identity,lineage,lambda:None,budget_continuation=binding)
    assert failure is None and len(rows)==1 and '/admission/' in intent and len(physical)==1
    after=budget.canonical_state();assert after.used.logical_model_calls==before.used.logical_model_calls+1 and after.used.model_tokens_total>before.used.model_tokens_total
    assert owner._associate(material,identity,lineage,lambda:None,budget_continuation=binding)==(rows,intent,None)
    assert budget.canonical_state()==after and len(physical)==1
    with pytest.raises(CitationGroundingError):owner._associate(material,identity,lineage,lambda:None)
    assert owner.store.get_bytes(refusal['native_denial_ref'],**identity)==original
    assert life.budget_preimage(budget.run_id).current_children==children
    assert after.limits.model_tokens_total==24000


@pytest.mark.parametrize('damage',['material','capability','child','grant','resume'])
def test_new_admission_changed_native_binding_never_dispatches(tmp_path,damage):
    state=_fixture(tmp_path);owner,budget,life,physical,identity,lineage,material,refusal,context=state
    binding=_authorize(state)
    if damage=='material':material={**material,'unauthorized_source':'changed'}
    elif damage=='capability':context['model_capability_binding']['context']=999
    elif damage=='child':life.open_attempt(budget.run_id,'section:extra','snapshot-local',attempt_id='extra')
    elif damage=='grant':binding['accepted_grant_sha256']='0'*64
    else:binding['resume_event_sha256']='0'*64
    before=budget.canonical_state()
    with pytest.raises((ValueError,RuntimeError)):owner._associate(material,identity,lineage,lambda:None,budget_continuation=binding)
    assert physical==[] and budget.canonical_state()==before


@pytest.mark.parametrize('cut',['intent','result'])
def test_new_admission_uncertain_cut_never_repeats_physical_dispatch(tmp_path,cut,monkeypatch):
    state=_fixture(tmp_path);owner,budget,life,physical,identity,lineage,material,refusal,_=state
    binding=_authorize(state);put=owner.store.put_json
    class Cut(BaseException):pass
    def interrupted(value,**kwargs):
        if '/admission/' in kwargs.get('artifact_ref',''):
            if cut=='intent' and kwargs['artifact_ref'].endswith('/input'):
                put(value,**kwargs);raise Cut()
            if cut=='result' and kwargs['artifact_ref'].endswith('/result'):raise Cut()
        return put(value,**kwargs)
    monkeypatch.setattr(owner.store,'put_json',interrupted)
    with pytest.raises(Cut):owner._associate(material,identity,lineage,lambda:None,budget_continuation=binding)
    monkeypatch.setattr(owner.store,'put_json',put)
    count=len(physical);before=budget.canonical_state()
    assert count==(0 if cut=='intent' else 1)
    with pytest.raises(CitationGroundingError,match='uncertain'):owner._associate(material,identity,lineage,lambda:None,budget_continuation=binding)
    assert len(physical)==count and budget.canonical_state()==before
