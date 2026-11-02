"""Step 2 native first-token closure controls. Full BT lifecycle/graph rows are separate."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy
import json
import pytest
from contracts.base_client import BaseLLMClient
from apps.enterprise_document.schemas.agent_rag_v1.budgets import BudgetReservationRefSchema, CanonicalBudgetVectorSchema
from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager, CanonicalBudgetCapacityExceeded
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter, CanonicalInvocationBudgetRouter
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_router import ModelRouter
from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema
from schemas.failure import FailureOrigin

NOW=datetime(2026,10,8,0,0,tzinfo=timezone.utc)

def _manager(root):
    return BudgetManager.canonical(task_id="task-bt-phase",run_id="run-bt-phase",
        limits=CanonicalBudgetVectorSchema(logical_model_calls=44,logical_tool_calls=4,model_tokens_total=24000,
            research_rounds=2,repair_attempts=2,generation_attempts=4,replan_attempts=2,true_retry_attempts=2),
        max_elapsed_seconds=120,storage_path=root/"native-budget.jsonl",
        policy_ref="budget-policy://bt-phase/fixed-24000",policy_version="v1",clock=lambda:NOW)

def _seed(manager, used):
    ref=BudgetReservationRefSchema(reservation_id="budget:completed-balance",task_id=manager.task_id,run_id=manager.run_id,
        reservation_kind="local_fixture_closed_balance",owner_occurrence_ref="fixture://completed-native-balance",
        scope_ref="run:run-bt-phase",operation_ref="fixture://completed-native-balance")
    vector=CanonicalBudgetVectorSchema(logical_model_calls=42,model_tokens_total=used)
    manager.reserve(ref,vector,expected_revision=manager.canonical_state().revision)
    manager.consume(ref.reservation_id,vector,expected_revision=manager.canonical_state().revision)

class Client(BaseLLMClient):
    def __init__(self,name,*,unavailable=False,usage=1):self.model_name=name;self.unavailable=unavailable;self.usage=usage;self.calls=[]
    def generate(self,request):
        self.calls.append(request.model_copy(deep=True))
        return ModelResponseSchema(model_call_id=request.model_call_id,task_id=request.task_id,run_id=request.run_id,
            model_name=self.model_name,success=not self.unavailable,content="ok" if not self.unavailable else "",
            error_message="provider unavailable" if self.unavailable else None,
            failure_origin=FailureOrigin.PROVIDER_RUNTIME if self.unavailable else FailureOrigin.UNKNOWN,
            created_at=request.created_at,token_usage=TokenUsageSchema(prompt_tokens=self.usage,completion_tokens=0,total_tokens=self.usage))

def _gateway(manager,*,fallback=False):
    profiles=[ModelProfile(profile_id="p1",model_name="m1",provider="controlled-native",context_window=65536,max_output_tokens=8192)]
    if fallback:profiles.append(ModelProfile(profile_id="p2",model_name="m2",provider="controlled-native",context_window=65536,max_output_tokens=8192))
    router=ModelRouter("m1",profiles=profiles,policies=[RoutingPolicy(role=ModelRole.GENERAL,candidates=[p.profile_id for p in profiles])])
    gateway=ModelGateway(default_model_name="m1",router=router,budget_runtime=CanonicalInvocationBudgetAdapter(manager))
    clients=[Client(profile.model_name) for profile in profiles]
    for client in clients:gateway.register_client(client)
    physical=[];invoke=gateway.invoker.invoke
    def measured(request,model_name):physical.append(request.model_copy(deep=True));return invoke(request,model_name)
    gateway.invoker.invoke=measured
    return gateway,clients,physical

def _request(required=19165):
    return ModelRequestSchema(model_call_id="model-call-document-original",task_id="task-bt-phase",run_id="run-bt-phase",
        caller_agent="CitationClaimAssociation",model_role=ModelRole.GENERAL.value,prompt="x"*(required-8192),max_tokens=8192,
        created_at=NOW.isoformat(),extra={"section_id":"s1","attempt_id":"a1",
            "logical_operation_id":"citation-association-call://sha256/original-input","call_purpose":"citation_association"})

def _receipt(root,name,manager,physical,error):
    payload={"schema_version":"bt_phase_native_observation_v1","scope":"STEP2_BUDGET_GATEWAY_ONLY_NOT_FULL_BT01",
        "physical_invoker_requests":[row.model_dump(mode="json") for row in physical],
        "native_budget_state":manager.canonical_state().model_dump(mode="json"),
        "error":{"type":type(error).__name__,"message":str(error)},
        "native_denial":getattr(error,"native_denial",None).model_dump(mode="json") if getattr(error,"native_denial",None) else None,
        "phase_proof":getattr(error,"pre_physical_phase_proof",None).model_dump(mode="json") if getattr(error,"pre_physical_phase_proof",None) else None}
    target=root/(name+".json")
    assert not target.exists();target.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),encoding="utf-8")

@pytest.mark.parametrize("required,used",[(19165,5063),(19209,5065),(19297,5069)])
def test_native_locked_denial_has_closed_first_attempt_proof(tmp_path,required,used):
    manager=_manager(tmp_path);_seed(manager,used);gateway,clients,physical=_gateway(manager)
    before=manager.canonical_state()
    with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:gateway.generate(_request(required))
    error=caught.value;_receipt(tmp_path,"native-first-refusal",manager,physical,error)
    assert (error.required,error.remaining,error.limit)==(required,24000-used,24000)
    assert physical==[] and all(client.calls==[] for client in clients)
    assert gateway._active_calls==0
    proof=error.pre_physical_phase_proof
    assert proof.gateway_call_closed and not proof.physical_dispatch_started
    assert proof.native_denial==error.native_denial
    assert proof.first_provider_attempt_ordinal==1 and proof.availability_fallback_from==()
    after=manager.canonical_state()
    assert after.limits==before.limits and after.used==before.used
    assert after.active_reserved.is_zero()
    assert gateway.budget_runtime.validate_pre_physical_token_rejection_proof(proof)==proof
    native_bytes=(tmp_path/"native-budget.jsonl").read_bytes()
    reopened=_manager(tmp_path)
    assert CanonicalInvocationBudgetAdapter(reopened).validate_pre_physical_token_rejection_proof(proof)==proof
    assert (tmp_path/"native-budget.jsonl").read_bytes()==native_bytes
    router=CanonicalInvocationBudgetRouter(lambda *_: (_ for _ in ()).throw(AssertionError("passive proof created account")),
        existing_manager_provider=lambda task,run:reopened)
    assert router.validate_pre_physical_token_rejection_proof(proof)==proof

def test_generic_budget_error_cannot_create_positive_proof(tmp_path,monkeypatch):
    manager=_manager(tmp_path);gateway,clients,physical=_gateway(manager)
    def generic(_request):raise CanonicalBudgetCapacityExceeded("model_tokens_total",limit=24000,remaining=0,required=19165)
    monkeypatch.setattr(gateway.budget_runtime,"reserve_provider_attempt",generic)
    with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:gateway.generate(_request())
    _receipt(tmp_path,"generic-no-proof",manager,physical,caught.value)
    assert not getattr(caught.value,"pre_physical_phase_proof",None)
    assert physical==[] and manager.canonical_state().used.is_zero()

def test_fallback_denial_preserves_real_first_effect_without_noeffect_proof(tmp_path):
    manager=_manager(tmp_path);_seed(manager,23980);gateway,clients,physical=_gateway(manager,fallback=True)
    clients[0].unavailable=True;clients[0].usage=10
    request=_request(15);request.max_tokens=14;request.prompt="x"
    with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:gateway.generate(request)
    _receipt(tmp_path,"fallback-no-proof",manager,physical,caught.value)
    assert len(physical)==1 and len(clients[0].calls)==1 and clients[1].calls==[]
    assert not getattr(caught.value,"pre_physical_phase_proof",None)
    state=manager.canonical_state()
    assert state.used.logical_model_calls==43 and state.used.model_tokens_total==23990
    assert caught.value.native_denial.reservation_ref.owner_occurrence_ref!=physical[0].extra["provider_attempt_id"]

def test_settlement_budget_error_has_actual_effect_and_no_phase_proof(tmp_path,monkeypatch):
    manager=_manager(tmp_path);gateway,clients,physical=_gateway(manager)
    def settle(*_args):raise CanonicalBudgetCapacityExceeded("model_tokens_total",limit=24000,remaining=0,required=1)
    monkeypatch.setattr(gateway.budget_runtime,"settle_provider_attempt",settle)
    with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:gateway.generate(_request(9000))
    _receipt(tmp_path,"settlement-no-proof",manager,physical,caught.value)
    assert len(physical)==1 and len(clients[0].calls)==1
    assert not getattr(caught.value,"pre_physical_phase_proof",None)
    assert manager.canonical_state().used.logical_model_calls==1

@pytest.mark.parametrize("field",["proof_sha256","release_budget_revision","release_budget_state_sha256","gateway_call_closed","physical_dispatch_started"])
def test_phase_proof_tamper_rejected_without_mutation(tmp_path,field):
    manager=_manager(tmp_path);_seed(manager,5063);gateway,clients,physical=_gateway(manager)
    with pytest.raises(CanonicalBudgetCapacityExceeded) as caught:gateway.generate(_request())
    raw=deepcopy(caught.value.pre_physical_phase_proof.model_dump(mode="json"))
    before=(tmp_path/"native-budget.jsonl").read_bytes()
    raw[field]=False if field=="gateway_call_closed" else True if field=="physical_dispatch_started" else 999 if field=="release_budget_revision" else "0"*64
    with pytest.raises((ValueError,TypeError)):gateway.budget_runtime.validate_pre_physical_token_rejection_proof(raw)
    assert (tmp_path/"native-budget.jsonl").read_bytes()==before and physical==[]
