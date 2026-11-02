"""Prepared finite native REC-M/B observations, not semantic Provider recovery.

Every count is an actual invoker/append/reservation observation. Model raw refs
are native diagnostic observations; only Budget journal records own accounting.
Controlled BaseException cuts are owner-bound cuts, not Windows process crashes.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
from apps.enterprise_document.services.agent_rag_v1 import BudgetManager, CanonicalBudgetIntegrityError
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from core.diagnostics import DiagnosticConfig, DiagnosticSession, activate_diagnostics
from core.persistence.crash_safe_jsonl import CrashSafeJsonlEventLog
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_invoker import ModelInvoker
from model_gateway.model_contract import ModelRole, RoutingPolicy
from model_gateway.model_router import ModelRouter
import test_multi_agent_v3_model_tool_runtime as model_tests
import test_multi_agent_v3_budget_runtime as budget_tests

ROOT=Path(__file__).resolve().parents[2]
FIELDS=("exact_native_operation_identity","actual_cut_location","native_intent_result_refs",
 "native_currentness_revisions","actual_physical_dispatch_count","actual_settlement_count",
 "native_cold_reopen_outcome","exact_replay_outcome","concurrent_duplicate_outcome")

class LocalOwnerResponseLost(BaseException):pass

def _sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def _wire(value):
 if hasattr(value,"model_dump"):return value.model_dump(mode="json")
 if isinstance(value,Path):return str(value)
 if isinstance(value,dict):return {str(k):_wire(v) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [_wire(v) for v in value]
 return value

def _point(function,native_operation,native_refs):
 path=Path(inspect.getsourcefile(function)).resolve()
 assert path.is_relative_to(ROOT)
 return {"path":str(path),"sha256":_sha(path),
    "function":function.__qualname__,"line":inspect.getsourcelines(function)[1],
    "native_operation":native_operation,"native_refs":_wire(native_refs)}

def _emit(tmp_path,request,row,values,points,scope):
 assert set(values)==set(points)==set(FIELDS)
 for name in ("actual_physical_dispatch_count","actual_settlement_count"):
  assert type(values[name]) is int and values[name]>=0
 observed={"schema_version":"fpbca_explicit_native_field_observation_v1","row":row,
    "nodeid":request.node.nodeid,"observations":{name:{"records":[{
       "status":"MEASURED","native_value":_wire(values[name]),
       "evidence_scope":(scope+"; "+(
           "COUNT_ONLY_NATIVE_PROVIDER_TOKEN_ACTIVE_TO_CONSUMED_TERMINAL_TRANSITIONS"
           if row=="D2/REC-M" else
           "COUNT_ONLY_NATIVE_BUDGET_ACTIVE_TO_CONSUMED_TERMINAL_TRANSITIONS_RELEASED_EXCLUDED")
           if name=="actual_settlement_count" else scope),
       "native_point":points[name]}]} for name in FIELDS}}
 folder=tmp_path/"native-row-observations"/row.replace("/","_");folder.mkdir(parents=True)
 path=folder/"observation.json"
 with path.open("x",encoding="utf-8") as stream:json.dump(observed,stream,ensure_ascii=False,indent=2)
 return path

def _cold_model(path):
 return BudgetManager.canonical(task_id="task_1",run_id="run_1",limits=model_tests._limits(),
    max_elapsed_seconds=120,storage_path=path,policy_ref="budget-policy://step4-model-tool",
    policy_version="v1",clock=lambda:model_tests.NOW,require_existing_account=True)

def _cold_budget(path):
 return BudgetManager.canonical(task_id="task_1",run_id="run_1",limits=budget_tests._limits(),
    max_elapsed_seconds=60,storage_path=path,policy_ref="budget-policy://tests/canonical-v1",
    policy_version="v1",clock=lambda:budget_tests.NOW,require_existing_account=True)

def _attach_measured_client(gateway,physical,*,hold=None):
 def success(native):
  if hold is not None:
   entered,release=hold;entered.set()
   if not release.wait(30):raise RuntimeError("TEST_TIME_BUDGET_EXHAUSTED native owner fixture; production_failure=NO")
  return model_tests._success(native).model_copy(update={"model_name":"m1"},deep=True)
 client=model_tests._StubClient("m1",success);gateway.register_client(client)
 original=gateway.invoker.invoke
 def measured(native,model_name):
  physical.append(native.model_copy(deep=True));return original(native,model_name)
 gateway.invoker.invoke=measured
 return client

def _reopened_gateway(manager,physical):
 profiles=[model_tests._profile("p1","m1"),model_tests._profile("p2","m2")]
 router=ModelRouter("m1",profiles=profiles,policies=[RoutingPolicy(
    role=ModelRole.SECTION_GENERATION,candidates=[p.profile_id for p in profiles])])
 gateway=ModelGateway(default_model_name="m1",router=router,
    budget_runtime=CanonicalInvocationBudgetAdapter(manager))
 _attach_measured_client(gateway,physical)
 return gateway

def _diag(root,name):
 return DiagnosticSession(DiagnosticConfig(output_root=root/"native-diagnostics",
    allowed_root=root,session_id=name,segment_id="local-owner",flush_records=1))

def _native_diagnostic_refs(session):
 assert session.health()["completeness"]=="COMPLETE",session.health()
 result=[]
 for line in session.path.read_text(encoding="utf-8").splitlines():
  event=json.loads(line)
  if "payload_ref" not in event:continue
  ref=event["payload_ref"];path=session.path.parent/ref["relative_path"]
  assert _sha(path)==ref["sha256"] and path.stat().st_size==ref["bytes"]
  result.append({"event_type":event["event_type"],"sequence":event["sequence"],
      "model_call_id":event.get("model_call_id"),"provider_attempt_id":event.get("provider_attempt_id"),
      "native_event_journal":str(session.path),"native_event_journal_sha256":_sha(session.path),
      "payload_ref":ref,"native_payload_path":str(path)})
 return result

def _provider_records(state):
 return [row for row in state.reservations if row.reservation_ref.reservation_kind=="model_provider_attempt_tokens"]

def _settlement_transitions(before,after,*,provider_only=False):
 previous={r.reservation_ref.reservation_id:r for r in before.reservations}
 return [r for r in after.reservations if r.status=="consumed" and r.terminal_budget_revision is not None
    and (not provider_only or r.reservation_ref.reservation_kind=="model_provider_attempt_tokens")
    and (r.reservation_ref.reservation_id not in previous or previous[r.reservation_ref.reservation_id].status!="consumed")]

def _error_wire(error):
 chain=[];seen=set();current=error
 while current is not None and id(current) not in seen:
  seen.add(id(current));chain.append({"type":type(current).__name__,"message":str(current)})
  current=current.__cause__ if current.__cause__ is not None else current.__context__
 return {"type":type(error).__name__,"message":str(error),"native_error_chain":chain}

def _actual_active_guard(error):
 return any("unreconciled active provider-token" in row["message"]
    for row in (error or {}).get("native_error_chain",[]))

def _m_case(root,mode):
 root.mkdir();gateway,owner=model_tests._gateway(root);physical=[]
 hold=(threading.Event(),threading.Event()) if mode=="concurrent" else None
 client=_attach_measured_client(gateway,physical,hold=hold)
 before=owner.canonical_state();original_bytes=owner.storage_path.read_bytes()
 native_request=model_tests._request();native_response=None;errors=[];cut=[];concurrent=None
 session=_diag(root,"REC_M_"+mode)
 original_settle=gateway.budget_runtime.settle_provider_attempt
 if mode=="response_before_settlement":
  def lost(reservation_id,request,response):
   cut.append({"reservation_id":reservation_id,"actual_request":_wire(request),
      "actual_response":_wire(response),"native_state":_wire(owner.canonical_state())})
   raise LocalOwnerResponseLost("actual raw response captured; token settlement not called")
  gateway.budget_runtime.settle_provider_attempt=lost
 with session:
  if mode=="concurrent":
   def winner():
    with activate_diagnostics(session):return gateway.generate(native_request)
   with ThreadPoolExecutor(max_workers=1) as pool:
    future=pool.submit(winner)
    if not hold[0].wait(30):
     hold[1].set();raise RuntimeError("TEST_TIME_BUDGET_EXHAUSTED fixture; production_failure=NO")
    active=owner.canonical_state();count_before=len(physical)
    try:gateway.generate(native_request)
    except (RuntimeError,CanonicalBudgetIntegrityError) as error:errors.append(_error_wire(error))
    finally:hold[1].set()
    native_response=future.result(timeout=30)
    concurrent={"native_active_preimage":_wire(active),"duplicate_error":errors,
        "actual_duplicate_physical_delta":len(physical)-count_before,
        "native_after":_wire(owner.canonical_state())}
  else:
   try:native_response=gateway.generate(native_request)
   except LocalOwnerResponseLost as error:errors.append({"type":type(error).__name__,"message":str(error)})
 after=owner.canonical_state();committed_bytes=owner.storage_path.read_bytes();refs=_native_diagnostic_refs(session)
 cold=_cold_model(owner.storage_path);cold_state=cold.canonical_state()
 cold_physical=[];cold_gateway=_reopened_gateway(cold,cold_physical)
 cold_error=None
 if mode=="response_before_settlement":
  try:cold_gateway.generate(native_request)
  except (RuntimeError,CanonicalBudgetIntegrityError) as error:cold_error=_error_wire(error)
  replay={"scope":"ACTIVE_PROVIDER_TOKEN_GUARD_NOT_SEMANTIC_RECOVERY",
      "cold_attempt_error":cold_error,"actual_cold_physical_count":len(cold_physical),
      "native_before":_wire(cold_state),"native_after":_wire(cold.canonical_state())}
 else:
  token=_provider_records(cold_state)[0];provider_native=physical[0]
  append_calls=[];append=cold._canonical_log.append_serialized
  def measured_append(serialized):append_calls.append(serialized);return append(serialized)
  with patch.object(cold._canonical_log,"append_serialized",measured_append):
   CanonicalInvocationBudgetAdapter(cold).settle_provider_attempt(
      token.reservation_ref.reservation_id,provider_native,native_response)
  replay={"scope":"EXACT_NATIVE_ACCOUNTING_SETTLEMENT_REPLAY_NO_PROVIDER_REDISPATCH",
     "reservation_id":token.reservation_ref.reservation_id,"native_response":_wire(native_response),
     "before":_wire(cold_state),"after":_wire(cold.canonical_state()),
     "actual_replay_append_count":len(append_calls),"actual_cold_physical_count":len(cold_physical)}
 outcome={"mode":mode,"request":_wire(native_request),"before":_wire(before),"after":_wire(after),
    "native_storage_path":str(owner.storage_path),"original_journal_sha256":hashlib.sha256(original_bytes).hexdigest(),
    "committed_journal_sha256":hashlib.sha256(committed_bytes).hexdigest(),
    "cold_journal_sha256":_sha(owner.storage_path),"cold_state":_wire(cold_state),
    "cold_after":_wire(cold.canonical_state()),"physical":[_wire(r) for r in physical],
    "client_calls":[_wire(r) for r in client.calls],"native_response":_wire(native_response),
    "cut":cut,"errors":errors,"concurrent":concurrent,"replay":replay,
    "provider_settlement_transitions":[_wire(r) for r in _settlement_transitions(before,after,provider_only=True)],
    "diagnostic_refs":refs,"usage_ledger_actual_snapshot":gateway.usage_snapshot(),
    "cold_usage_ledger_actual_snapshot":cold_gateway.usage_snapshot(),
    "domain_semantic_result_store":"NOT_PRESENT_IN_GATEWAY_OWNER_SCOPE"}
 cold_gateway.close();gateway.close()
 return outcome

def test_rec_m_native_gateway_dispatch_settlement_cold_guard_replay_and_duplicate_observations(tmp_path,request):
 cases=[_m_case(tmp_path/mode,mode) for mode in ("complete","response_before_settlement","concurrent")]
 refs=[{"journal":c["native_storage_path"],"sha256":c["committed_journal_sha256"],
    "reservations":c["after"]["reservations"],"native_model_representations":c["diagnostic_refs"]} for c in cases]
 identity=[{"mode":c["mode"],"model_call_id":c["request"]["model_call_id"],
    "logical_operation_id":c["request"]["extra"]["logical_operation_id"],
    "actual_provider_attempts":[r["extra"]["provider_attempt_id"] for r in c["physical"]],
    "native_journal":c["native_storage_path"]} for c in cases]
 values={"exact_native_operation_identity":identity,
  "actual_cut_location":{"controlled_cut":"ACTUAL_NATIVE_RAW_RESPONSE_BEFORE_PROVIDER_TOKEN_SETTLEMENT",
    "cut_cases":[c["cut"] for c in cases if c["cut"]],"OS_process_crash":"NOT_THIS_BOUNDED_OWNER_CONTROL"},
  "native_intent_result_refs":refs,
  "native_currentness_revisions":[{"mode":c["mode"],"before":c["before"],"after":c["after"],
      "cold_state":c["cold_state"],"usage_ledger_actual_snapshot":c["usage_ledger_actual_snapshot"]} for c in cases],
  "actual_physical_dispatch_count":sum(len(c["physical"]) for c in cases),
  "actual_settlement_count":sum(len(c["provider_settlement_transitions"]) for c in cases),
  "native_cold_reopen_outcome":[{"mode":c["mode"],"native_state":c["cold_state"],"after":c["cold_after"],
      "journal_before_sha256":c["committed_journal_sha256"],"journal_after_sha256":c["cold_journal_sha256"]} for c in cases],
  "exact_replay_outcome":[c["replay"] for c in cases],
  "concurrent_duplicate_outcome":[c["concurrent"] for c in cases if c["concurrent"] is not None]}
 point=_point(ModelGateway.generate,"native_ModelGateway_same_logical_operation_and_canonical_accounting",refs)
 points={name:point for name in FIELDS}
 points["actual_physical_dispatch_count"]=_point(ModelInvoker.invoke,"actual_invoker_entries",[c["physical"] for c in cases])
 points["actual_settlement_count"]=_point(BudgetManager.consume,"actual_provider_token_ACTIVE_to_CONSUMED_terminal_revision",[c["provider_settlement_transitions"] for c in cases])
 _emit(tmp_path,request,"D2/REC-M",values,points,"EXISTING_GATEWAY_BUDGET_NATIVE_OWNER_CONTROL_NOT_DOMAIN_SEMANTIC_RETRY_AUTHORITY")
 # Persist actual evidence before these final positive/negative oracles.
 for case in cases:
  assert len(case["physical"])==len(case["client_calls"])==1
  assert any(ref["event_type"]=="MODEL_CLIENT_RAW_RESPONSE" for ref in case["diagnostic_refs"])
  assert case["after"]==case["cold_state"]==case["cold_after"]
  assert case["committed_journal_sha256"]==case["cold_journal_sha256"]
 complete,cut,concurrent=cases
 assert len(complete["provider_settlement_transitions"])==len(concurrent["provider_settlement_transitions"])==1
 assert not cut["provider_settlement_transitions"]
 assert _actual_active_guard(cut["replay"]["cold_attempt_error"])
 assert cut["replay"]["actual_cold_physical_count"]==0
 assert concurrent["concurrent"]["duplicate_error"] and _actual_active_guard(concurrent["concurrent"]["duplicate_error"][0])
 assert concurrent["concurrent"]["actual_duplicate_physical_delta"]==0
 for case in (complete,concurrent):assert case["replay"]["actual_replay_append_count"]==case["replay"]["actual_cold_physical_count"]==0

def _b_response_loss(root,operation):
 root.mkdir();owner=budget_tests._manager(root)
 ref=budget_tests._ref("native-cut");reserved=CanonicalBudgetVectorSchema(logical_model_calls=1,model_tokens_total=20)
 actual=CanonicalBudgetVectorSchema(logical_model_calls=1,model_tokens_total=12)
 if operation!="reserve":owner.reserve(ref,reserved,expected_revision=0)
 before=owner.canonical_state();before_bytes=owner.storage_path.read_bytes();commits=[]
 append=owner._canonical_log.append_serialized
 def lost(serialized):
  append(serialized);commits.append(serialized)
  raise LocalOwnerResponseLost(operation)
 with patch.object(owner._canonical_log,"append_serialized",lost):
  try:
   if operation=="reserve":owner.reserve(ref,reserved,expected_revision=before.revision)
   elif operation=="consume":owner.consume(ref.reservation_id,actual,expected_revision=before.revision)
   else:owner.release(ref.reservation_id,expected_revision=before.revision)
  except LocalOwnerResponseLost:pass
 committed_bytes=owner.storage_path.read_bytes();cold=_cold_budget(owner.storage_path);state=cold.canonical_state()
 native=cold.resolve_reservation(ref.reservation_id);replay_appends=[];original=cold._canonical_log.append_serialized
 def measured(serialized):replay_appends.append(serialized);return original(serialized)
 with patch.object(cold._canonical_log,"append_serialized",measured):
  if operation=="reserve":receipt=cold.reserve(ref,reserved,expected_revision=before.revision)
  elif operation=="consume":receipt=cold.consume(ref.reservation_id,actual,expected_revision=before.revision)
  else:receipt=cold.release(ref.reservation_id,expected_revision=before.revision)
 return {"operation":operation,"ref":_wire(ref),"before":_wire(before),"after":_wire(state),
    "native_reservation":_wire(native),"actual_replay_receipt":_wire(receipt),
    "native_journal":str(owner.storage_path),"before_sha256":hashlib.sha256(before_bytes).hexdigest(),
    "committed_sha256":hashlib.sha256(committed_bytes).hexdigest(),"cold_replay_sha256":_sha(owner.storage_path),
    "committed_prefix_preserved":committed_bytes.startswith(before_bytes),
    "actual_append_count":len(commits),"actual_replay_append_count":len(replay_appends),
    "settlement_transitions":[_wire(r) for r in _settlement_transitions(before,state)],
    "cold_replay_state":_wire(cold.canonical_state())}

def _b_concurrent(root):
 root.mkdir();owner=budget_tests._manager(root);cold=_cold_budget(owner.storage_path)
 ref=budget_tests._ref("duplicate-exact");reserved=CanonicalBudgetVectorSchema(logical_model_calls=1,model_tokens_total=20)
 actual=CanonicalBudgetVectorSchema(logical_model_calls=1,model_tokens_total=12)
 before=owner.canonical_state();barrier=threading.Barrier(2)
 def reserve(index):
  barrier.wait(timeout=10)
  return (owner,cold)[index].reserve(ref,reserved,expected_revision=before.revision)
 with ThreadPoolExecutor(max_workers=2) as pool:reservations=list(pool.map(reserve,range(2)))
 active=owner.canonical_state();barrier=threading.Barrier(2)
 def settle(index):
  barrier.wait(timeout=10)
  return (owner,cold)[index].consume(ref.reservation_id,actual,expected_revision=active.revision)
 with ThreadPoolExecutor(max_workers=2) as pool:receipts=list(pool.map(settle,range(2)))
 after=owner.canonical_state();journal=owner.storage_path.read_bytes();reopened=_cold_budget(owner.storage_path)
 return {"ref":_wire(ref),"before":_wire(before),"active":_wire(active),"after":_wire(after),
    "native_reserve_receipts":[_wire(r) for r in reservations],"native_settlement_receipts":[_wire(r) for r in receipts],
    "native_cold_state":_wire(reopened.canonical_state()),"native_journal":str(owner.storage_path),
    "journal_sha256":hashlib.sha256(journal).hexdigest(),"cold_journal_sha256":_sha(owner.storage_path),
    "settlement_transitions":[_wire(r) for r in _settlement_transitions(before,after)]}

def _b_transport(root,committed):
 root.mkdir();outcomes=[]
 for index,tail in enumerate((b'{"uncommitted":',b'{"invalid":}\n',b'\xff\n')):
  path=root/(str(index)+".jsonl")
  with path.open("xb") as stream:stream.write(committed);stream.write(tail)
  transport=path.read_bytes();calls=[];native=None;error=None
  append=CrashSafeJsonlEventLog.append_serialized
  def measured(self,serialized):calls.append(serialized);return append(self,serialized)
  with patch.object(CrashSafeJsonlEventLog,"append_serialized",measured):
   try:native=_cold_budget(path).canonical_state()
   except (CanonicalBudgetIntegrityError,ValueError) as exception:error={"type":type(exception).__name__,"message":str(exception)}
  outcomes.append({"native_path":str(path),"tail_hex":tail.hex(),"committed_sha256":hashlib.sha256(committed).hexdigest(),
     "before_transport_sha256":hashlib.sha256(transport).hexdigest(),"after_transport_sha256":_sha(path),
     "native_cold_state":_wire(native),"native_error":error,"actual_semantic_append_count":len(calls),
     "committed_prefix_preserved":path.read_bytes().startswith(committed)})
 return outcomes

def test_rec_b_native_budget_response_loss_exact_replay_duplicate_and_transport_observations(tmp_path,request):
 physical=[];invoke=ModelInvoker.invoke
 def measured(self,native,model_name):physical.append(_wire(native));return invoke(self,native,model_name)
 with patch.object(ModelInvoker,"invoke",measured):
  cuts=[_b_response_loss(tmp_path/operation,operation) for operation in ("reserve","consume","release")]
  concurrent=_b_concurrent(tmp_path/"duplicate")
  committed=Path(cuts[1]["native_journal"]).read_bytes()
  transport=_b_transport(tmp_path/"fresh_transport_copies",committed)
 refs=[{"native_reservation_ref":c["ref"],"journal":c["native_journal"],"sha256":c["committed_sha256"],
        "native_terminal_reservation":c["native_reservation"],"replay_receipt":c["actual_replay_receipt"]} for c in cuts]
 refs.append({"concurrent":concurrent})
 values={"exact_native_operation_identity":[{"operation":c["operation"],"ref":c["ref"],"journal":c["native_journal"]} for c in cuts],
    "actual_cut_location":{"controlled_boundary":"AFTER_NATIVE_JSONL_FSYNC_APPEND_BEFORE_OWNER_RESPONSE","actual_native_append_counts":[c["actual_append_count"] for c in cuts],
      "OS_process_crash":"NOT_THIS_BOUNDED_OWNER_CONTROL"},
    "native_intent_result_refs":refs,"native_currentness_revisions":[{"operation":c["operation"],"before":c["before"],"after":c["after"]} for c in cuts],
    "actual_physical_dispatch_count":len(physical),
    "actual_settlement_count":sum(len(c["settlement_transitions"]) for c in cuts)+len(concurrent["settlement_transitions"]),
    "native_cold_reopen_outcome":{"response_lost":[{"operation":c["operation"],"state":c["after"],"journal_sha256":c["committed_sha256"]} for c in cuts],"transport":transport},
    "exact_replay_outcome":[{"operation":c["operation"],"receipt":c["actual_replay_receipt"],"before_state":c["after"],"after_state":c["cold_replay_state"],
       "actual_replay_append_count":c["actual_replay_append_count"],"before_journal_sha256":c["committed_sha256"],"after_journal_sha256":c["cold_replay_sha256"]} for c in cuts],
    "concurrent_duplicate_outcome":concurrent}
 points={name:_point(BudgetManager.reserve,"same_exact_canonical_Budget_account_operation_and_cold_prefix",refs) for name in FIELDS}
 points["actual_cut_location"]=_point(CrashSafeJsonlEventLog.append_serialized,"native_append_completed_before_controlled_response_loss",refs)
 points["actual_physical_dispatch_count"]=_point(ModelInvoker.invoke,"actual_invoker_entry_instrumentation_over_Budget_owner_scope",physical)
 points["actual_settlement_count"]=_point(BudgetManager.consume,"native_CONSUMED_terminal_budget_revision_transitions",[c["settlement_transitions"] for c in cuts]+[concurrent["settlement_transitions"]])
 _emit(tmp_path,request,"D2/REC-B",values,points,"EXISTING_CANONICAL_BUDGET_JOURNAL_CAS_LOCAL_OWNER_NOT_PROVIDER_OR_BUSINESS_COMPLETION")
 # Save all actual observations before final native prefix/replay/race oracles.
 assert not physical
 for c in cuts:
  assert c["actual_append_count"]==1 and c["actual_replay_append_count"]==0
  assert c["after"]==c["cold_replay_state"] and c["committed_sha256"]==c["cold_replay_sha256"]
  assert c["committed_prefix_preserved"] and c["after"]["revision"]==c["before"]["revision"]+1
 assert concurrent["after"]==concurrent["native_cold_state"]
 assert concurrent["native_reserve_receipts"][0]==concurrent["native_reserve_receipts"][1]
 assert concurrent["native_settlement_receipts"][0]==concurrent["native_settlement_receipts"][1]
 assert concurrent["after"]["revision"]==concurrent["before"]["revision"]+2
 assert len(concurrent["after"]["reservations"])==len(concurrent["settlement_transitions"])==1
 for t in transport:assert t["actual_semantic_append_count"]==0 and t["committed_prefix_preserved"]
 assert transport[0]["native_cold_state"] is not None and transport[0]["native_error"] is None
 assert transport[0]["after_transport_sha256"]==transport[0]["committed_sha256"]
 for t in transport[1:]:assert t["native_error"] and t["after_transport_sha256"]==t["before_transport_sha256"]
