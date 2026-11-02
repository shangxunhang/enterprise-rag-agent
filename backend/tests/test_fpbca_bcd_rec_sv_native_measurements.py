"""Fresh native REC-S/V observations. Verification only; not full Graph proof.

Reuse unchanged native tests in fresh roots. Counters observe actual invoker and
native reservation transitions; source/AST/assertions/PASS do not create facts.
"""
from collections import Counter
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import threading
import traceback
from unittest.mock import patch
import pytest
from core.runtime.side_effects import LogicalOperationRef
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimVerificationRevisionRefSchema, VerificationResultRefSchema,
    VERIFIER_LOCAL_CORRECTION_KEY,
)
from apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery import RunningInvocationRecoveryService
from backend.tests import local_verifier_recovery_helpers as native
from backend.tests import test_fpbca_bcd_support_crash_cuts as support
from backend.tests import test_local_verifier_recovery_integration as verification
from backend.tests import test_multi_agent_v3_claim_verification_runtime as native_cas

ROOT=Path(__file__).resolve().parents[2]
S_ROW,V_ROW="D2/REC-S","D2/REC-V"


def _json(value):
    if hasattr(value,"model_dump"):return value.model_dump(mode="json",round_trip=True)
    if is_dataclass(value):return {f.name:_json(getattr(value,f.name)) for f in fields(value)}
    if isinstance(value,Enum):return _json(value.value)
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,Path):return str(value)
    if isinstance(value,dict):
        assert all(isinstance(k,str) for k in value)
        return {k:_json(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [_json(v) for v in value]
    assert value is None or isinstance(value,(str,int,float,bool)),type(value)
    json.dumps(value,allow_nan=False)
    return value


def _once(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(_json(value),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())


def _error(error):
    result=[];seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        result.append({"type":type(error).__name__,"message":str(error),"error_code":getattr(error,"error_code",None),
            "frames":[{"path":f.filename,"line":f.lineno,"function":f.name} for f in traceback.extract_tb(error.__traceback__)]})
        error=error.__cause__
    return result


def _point(function,operation,refs=()):
    function=inspect.unwrap(function)
    code=function.__func__.__code__ if hasattr(function,"__func__") else function.__code__
    path=Path(code.co_filename).resolve()
    assert path.is_relative_to(ROOT) and path.is_file() and not path.is_symlink()
    return {"path":str(path),"sha256":sha256(path.read_bytes()).hexdigest(),"function":function.__qualname__,
            "line":code.co_firstlineno,"native_operation":operation,"native_refs":_json(list(refs))}


def _complete_rows(path):
    raw=path.read_bytes() if path.exists() else b""
    rows=[];errors=[]
    for number,line in enumerate(raw.splitlines(keepends=True),1):
        if not line.endswith(b"\n"):
            errors.append({"line":number,"disposition":"INCOMPLETE_SUFFIX_RAW_ONLY"});continue
        try:rows.append(json.loads(line))
        except (UnicodeDecodeError,json.JSONDecodeError) as error:
            errors.append({"line":number,"type":type(error).__name__,"disposition":"INVALID_COMPLETE_FRAME_RAW_ONLY"})
    return {"path":str(path),"sha256":sha256(raw).hexdigest(),"bytes":len(raw),"rows":rows,"raw_errors":errors}


class NativeAudit:
    """Test-owned spy; never holds its mutex while calling native owner code."""
    def __init__(self,root,row,nodeid,case,*,concurrent=False):
        self.root,self.row,self.nodeid,self.case=Path(root),row,nodeid,case
        self.concurrent=concurrent;self._mutex=threading.RLock();self._installed=set()
        self.events=[];self.snapshots=[];self.actors=[];self.fx=[];self.targets={}
        self.points={};self.cuts=[];self.consumer=[];self.errors=[];self.budget_before={};self.closed=False;self.observer_failures=[]

    def event(self,kind,value,point=None):
        try:encoded=_json(value)
        except Exception as gap:
            failure={"phase":"SERIALIZE_NATIVE_OBSERVATION","kind":kind,"actual_error":_error(gap),
                "source_type":type(value).__module__+"."+type(value).__qualname__}
            self.observer_failures.append(failure);encoded={"status":"UNMEASURED_SERIALIZER_GAP",**failure}
        data={"kind":kind,"value":encoded,"pid":os.getpid(),"thread_id":threading.get_ident(),"native_point":point}
        with self._mutex:
            data["sequence"]=len(self.events);self.events.append(data)
            try:
                self.root.mkdir(parents=True,exist_ok=True)
                with (self.root/"actual-boundary-events.jsonl").open("a",encoding="utf-8") as stream:
                    stream.write(json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n")
                    stream.flush();os.fsync(stream.fileno())
            except Exception as gap:
                self.observer_failures.append({"phase":"DURABLE_NATIVE_OBSERVATION","kind":kind,"actual_error":_error(gap)})
        return data

    def safe_snapshot(self,fx,phase):
        try:return self.snapshot(fx,phase)
        except Exception as gap:
            failure={"phase":phase,"root":str(fx.root),"actual_error":_error(gap)}
            self.observer_failures.append(failure);self.event("UNMEASURED_NATIVE_SNAPSHOT",failure)
            return None

    def cold_constructor(self,function,root,args,kwargs):
        point=_point(function,"ACTUAL_EXISTING_OWNER_CONSTRUCTOR");self.points.setdefault("cold",point)
        self.event("NATIVE_COLD_CONSTRUCTOR_ENTRY",{"root":str(root)},point)
        try:returned=function(*args,**kwargs)
        except BaseException as error:
            with self._mutex:
                self.actors.append({"actor":len(self.actors),"phase":"COLD_EXISTING_CONSTRUCTOR_FAILED","root":str(root),
                    "pid":os.getpid(),"thread_id":threading.get_ident(),"actual_constructor":point})
            self.event("NATIVE_COLD_CONSTRUCTOR_ERROR",{"root":str(root),"actual_error":_error(error)},point);raise
        self.event("NATIVE_COLD_CONSTRUCTOR_RETURN",{"root":str(root),"returned_owner_type":type(returned).__qualname__},point)
        return returned

    def wrap(self,owner,name,tag):
        token=(id(owner),name)
        if token in self._installed:return
        original=getattr(owner,name);self._installed.add(token)
        point=_point(original,tag);self.points.setdefault(tag,point)
        def observed(*args,**kwargs):
            entry=self.event("OWNER_ENTRY",{"tag":tag,"args":args,"kwargs":kwargs},point)
            try:value=original(*args,**kwargs)
            except BaseException as error:
                self.event("OWNER_ERROR",{"tag":tag,"entry":entry["sequence"],"error":_error(error)},point);raise
            self.event("OWNER_RETURN",{"tag":tag,"entry":entry["sequence"],"returned":value},point)
            return value
        setattr(owner,name,observed)

    def attach(self,fx,phase,*,target=None):
        assert fx.gateway.budget_runtime is not None
        with self._mutex:
            if target is not None:self.targets[str(fx.root)]=target
            actor={"actor":len(self.actors),"phase":phase,"root":str(fx.root),"pid":os.getpid(),
                   "thread_id":threading.get_ident(),"budget_existing_scope":phase.startswith("COLD")}
            self.actors.append(actor);self.fx.append(fx)
        invoker=fx.gateway.invoker;original=invoker.invoke;token=(id(invoker),"invoke")
        if token not in self._installed:
            self._installed.add(token);point=_point(original,"ModelInvoker.invoke");self.points.setdefault("physical",point)
            def invoke(request,model_name):
                raw=request.model_dump(mode="json")
                self.event("ACTUAL_INVOKER_ENTRY",{"actor":actor,"model_name":model_name,"request":raw,
                    "request_sha256":sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode("utf-8")).hexdigest()},point)
                return original(request,model_name)
            invoker.invoke=invoke
        for owner,name,tag in ((fx.runtime,"commit_verification","commit_verification"),
            (fx.runtime,"record_verifier_candidate_rejection","record_verifier_candidate_rejection"),
            (fx.runtime,"authorize_verifier_correction","authorize_verifier_correction"),
            (fx.runtime,"terminate_verifier_correction","terminate_verifier_correction"),
            (fx.support_composition.runtime,"commit_support","commit_support")):
            self.wrap(owner,name,tag)
        for store in (fx.composer.store,fx.support_composition.store):self.wrap(store,"put_json","Store.put_json")
        for log,label in ((fx.budget._canonical_log,"Budget.native_append"),(fx.runtime.log,"Verification.native_append"),
                          (fx.support_composition.runtime.log,"Support.native_append")):
            # Only original serialized frame; no recursive reads inside native
            # Budget/Support/Claim append critical sections.
            self.wrap(log,"append_serialized",label)
        self.points.setdefault("settlement",_point(fx.budget._append_canonical_state_unlocked,"Budget.canonical_state_append"))
        baseline=fx.budget.canonical_state()
        with self._mutex:
            self.budget_before.setdefault(str(fx.root),{"state":baseline.model_dump(mode="json",round_trip=True),
                "raw":(fx.root/"budget.jsonl").read_bytes(),"provider_rows":native.provider_rows(fx)})
        self.safe_snapshot(fx,phase+"_OPEN")
        return fx

    def snapshot(self,fx,phase):
        pre=fx.lifecycle.budget_preimage(fx.context.active_lineage.run_id)
        paragraph=fx.artifact.resolve_active(fx.claim.paragraph_version_ref.logical_ref)
        state=fx.budget.canonical_state();stores=[];roots=set()
        for store in (fx.composer.store,fx.support_composition.store):
            if str(store.root) in roots:continue
            roots.add(str(store.root))
            for ref in store.list_references():
                exact,body=store.get_bytes_with_reference(ref.artifact_ref,task_id=ref.task_id,run_id=ref.run_id,
                    section_id=ref.section_id,attempt_id=ref.attempt_id,input_snapshot_version=ref.input_snapshot_version)
                stores.append({"reference":exact.model_dump(mode="json"),"actual_content_sha256":sha256(body).hexdigest(),"body":json.loads(body)})
        value={"phase":phase,"root":str(fx.root),"pid":os.getpid(),"thread_id":threading.get_ident(),
            "consistency":"SEQUENTIAL_NATIVE_OWNER_READS_NOT_CROSS_OWNER_TRANSACTION",
            "run":pre.run.model_dump(mode="json"),"children":[r.model_dump(mode="json",round_trip=True) for r in pre.current_children],
            "child_preimage_sha256":pre.child_preimage_sha256,"supplied_lineage":fx.context.active_lineage.model_dump(mode="json"),
            "claim":fx.runtime.resolve_claim(fx.claim.claim_ref).model_dump(mode="json"),
            "binding":fx.runtime.resolve_binding_facts(fx.binding.binding_facts_ref).model_dump(mode="json"),
            "paragraph_active":_json(paragraph),"artifacts":stores,"budget":state.model_dump(mode="json",round_trip=True),
            "budget_journal":_complete_rows(fx.root/"budget.jsonl"),
            "support_journal":_complete_rows(fx.support_composition.runtime.ledger_path),
            "verification_journal":_complete_rows(fx.runtime.ledger_path),"physical_provider_rows":native.provider_rows(fx)}
        with self._mutex:number=len(self.snapshots);self.snapshots.append(value)
        _once(self.root/("native-snapshot-%03d.json"%number),value)
        return value

    def call(self,fx,tag,function,*args,**kwargs):
        self.safe_snapshot(fx,tag+"_BEFORE");point=_point(function,tag);self.points.setdefault("owner_consumer",point)
        try:result=function(*args,**kwargs)
        except BaseException as error:
            actual_error=_error(error)
            self.event("NATIVE_CONSUMER_ERROR",{"tag":tag,"root":str(fx.root),"error":actual_error},point)
            if isinstance(error,(KeyboardInterrupt,support.NativeCut)):
                self.cut(self.case,point,{"native_raised_error":actual_error,
                    "actual_boundary_events_before_error":[e for e in self.events if e["kind"] in {"OWNER_RETURN","OWNER_ERROR","ACTUAL_INVOKER_ENTRY"}],
                    "scope":"SIMULATED_NATIVE_CALL_FRAME_NOT_OS_EXIT"})
            self.safe_snapshot(fx,tag+"_ERROR");raise
        self.event("NATIVE_CONSUMER_RETURN",{"tag":tag,"root":str(fx.root),"returned":result},point)
        self.safe_snapshot(fx,tag+"_RETURN");return result

    def cut(self,label,point,value):
        row={"label":label,"native_point":point,"actual_value":value}
        observed=self.event("ACTUAL_CUT_HOOK",row,point);self.cuts.append(observed["value"])

    def settlement_records(self):
        result=[]
        for root,before in self.budget_before.items():
            fx=next(f for f in reversed(self.fx) if str(f.root)==root)
            final=fx.budget.canonical_state() # strict native history validation
            transport=(fx.root/"budget.jsonl").read_bytes()
            assert transport.startswith(before["raw"]),"normal native ledger prefix changed"
            previous={r["reservation_ref"]["reservation_id"]:r for r in before["state"]["reservations"]}
            observed=[]
            for frame in _complete_rows(fx.root/"budget.jsonl")["rows"]:
                state=frame["state"]
                if state["revision"]<=before["state"]["revision"]:continue
                for row in state["reservations"]:
                    rid=row["reservation_ref"]["reservation_id"];old=previous.get(rid)
                    if (old is not None and old["status"]=="active" and row["status"] in {"consumed","released"}
                            and row["reservation_ref"]["reservation_kind"]=="model_provider_attempt_tokens"):
                        observed.append({"reservation":row,"before":old,"native_event_type":frame["event_type"],
                            "native_state_revision":state["revision"]})
                    previous[rid]=row
            result.append({"root":root,"baseline":before["state"],"final":final.model_dump(mode="json",round_trip=True),
                "before_journal_sha256":sha256(before["raw"]).hexdigest(),"after_journal_sha256":sha256(transport).hexdigest(),
                "actual_provider_terminal_transitions":observed})
        return result

    def provider_spy_coverage(self):
        coverage=[]
        for root,before in self.budget_before.items():
            fx=next(f for f in reversed(self.fx) if str(f.root)==root)
            baseline=before["provider_rows"];rows=native.provider_rows(fx)
            assert rows[:len(baseline)]==baseline,"native provider baseline changed"
            durable=rows[len(baseline):]
            entries=[e for e in self.events if e["kind"]=="ACTUAL_INVOKER_ENTRY" and e["value"]["actor"]["root"]==root]
            key=lambda r:json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
            # Exact native Provider body cross-check, no adapter count/AST/alias.
            assert Counter(key(r["request"]) for r in durable)==Counter(key(e["value"]["request"]) for e in entries),"unobserved physical scope"
            coverage.append({"root":root,"baseline_native_rows":baseline,"actual_new_provider_rows":durable,
                "actual_invoker_entries":entries,"comparison":"EXACT_REQUEST_BODY_MULTISET"})
        return coverage

    def emit(self):
        if not self.fx:return
        for fx in {str(f.root):f for f in self.fx}.values():self.safe_snapshot(fx,"FINAL_NATIVE_OWNER_READ")
        if self.observer_failures:
            _once(self.root/"native-capture-incomplete.json",{"status":"UNMEASURED_OBSERVER_GAP",
                "actual_observer_failures":self.observer_failures,"actual_events":self.events,"actual_snapshots":self.snapshots})
            raise AssertionError("native observer gap: exact native call arguments/returns were preserved; no positive measurement emitted")
        settlements=self.settlement_records();coverage=self.provider_spy_coverage()
        physical=[r for r in self.events if r["kind"]=="ACTUAL_INVOKER_ENTRY"]
        returned=[r for r in self.events if r["kind"] in {"OWNER_RETURN","OWNER_ERROR","NATIVE_CONSUMER_RETURN","NATIVE_CONSUMER_ERROR","NATIVE_COLD_CONSTRUCTOR_RETURN","NATIVE_COLD_CONSTRUCTOR_ERROR"}]
        refs=[{"phase":s["phase"],"root":s["root"],"artifacts":s["artifacts"]} for s in self.snapshots]
        semantic=[{"phase":s["phase"],"support":s["support_journal"],"verification":s["verification_journal"]} for s in self.snapshots]
        values={"exact_native_operation_identity":{"case":self.case,"physical_requests":physical,
                    "immutable_receipts":refs,"native_semantic_journals":semantic},
            "native_intent_result_refs":refs,
            "native_currentness_revisions":[{k:s[k] for k in ("phase","root","run","children","child_preimage_sha256",
                "supplied_lineage","claim","binding","paragraph_active")} for s in self.snapshots],
            "actual_physical_dispatch_count":len(physical),
            "actual_settlement_count":sum(len(r["actual_provider_terminal_transitions"]) for r in settlements)}
        if self.cuts:values["actual_cut_location"]=self.cuts
        cold=[r for r in self.actors if r["phase"].startswith("COLD")]
        if cold:
            values["native_cold_reopen_outcome"]={"actual_existing_owner_constructors":cold,"native_returns":returned,"REC_consumers":self.consumer}
            values["exact_replay_outcome"]={"actual_owner_values":returned,"REC_consumers":self.consumer}
        if self.concurrent:
            values["concurrent_duplicate_outcome"]={"actors":self.actors,"actual_owner_returns_and_errors":returned,
                "physical_boundary_entries":physical,"settlements":settlements}
        points={"actual_physical_dispatch_count":self.points["physical"],"actual_settlement_count":self.points["settlement"],
            "native_cold_reopen_outcome":self.points.get("cold",self.points.get("owner_consumer",self.points["Store.put_json"]))}
        default=self.points.get("owner_consumer",self.points["Store.put_json"])
        observations={name:{"records":[{"status":"MEASURED","native_value":_json(value),
            "evidence_scope":"NATIVE_OWNER_AUTHORED_FAKE_COMPONENT_COLD_REC_CONSUMER",
            "native_point":points.get(name,default),"nodeid":self.nodeid}]} for name,value in values.items()}
        _once(self.root/"actual-counter-settlement-details.json",{"closed_test_owned_scope":self.closed,
            "physical_entries":physical,"native_provider_spy_scope":coverage,"provider_terminal_transitions":settlements,"native_cut_hook_facts":self.cuts,
            "actual_native_failures":self.errors,"REC_consumers":self.consumer,
            "not_claimed":["FULL_GRAPH_BUSINESS_SUCCESS","CROSS_OWNER_ATOMIC_SNAPSHOT","MODEL_CERTAINTY_CORRECTNESS"]})
        _once(self.root/"native-row-observations"/self.row.replace("/","_")/"observation.json",{
            "schema_version":"fpbca_explicit_native_field_observation_v1","row":self.row,"nodeid":self.nodeid,
            "observations":observations,"absent_fields_are":"UNMEASURED","automatic_semantic_inference":False,
            "not_applicable_at_this_node":{"concurrent_duplicate_outcome":not self.concurrent,
                "actual_cut_location":not bool(self.cuts)}})


def _run(audit,function):
    try:result=function()
    except BaseException as primary:
        audit.errors.append(_error(primary));audit.closed=True
        try:audit.emit()
        except BaseException as emission:
            # Never replace the original native/oracle failure with observer failure.
            try:_once(audit.root/"native-observation-incomplete.json",{
                "status":"UNMEASURED_OBSERVER_FAILED","primary_error":_error(primary),"emission_error":_error(emission),
                "actual_events":audit.events,"actual_snapshots":audit.snapshots})
            except BaseException:pass
        raise
    audit.closed=True;audit.emit();return result


def _install_support(audit,monkeypatch):
    fresh,reopen,evaluate=support._fresh,support._reopen,support._evaluate
    def fresh_owner(root):
        fx,target,reply=fresh(root);audit.attach(fx,"FRESH",target=target)
        return fx,target,reply
    def cold_owner(fx,reply):
        value=audit.cold_constructor(reopen,fx.root,(fx,reply),{});audit.attach(value,"COLD_EXISTING",target=audit.targets[str(fx.root)])
        return value
    def evaluate_owner(fx,target):return audit.call(fx,"SUPPORT_EVALUATE",evaluate,fx,target)
    monkeypatch.setattr(support,"_fresh",fresh_owner);monkeypatch.setattr(support,"_reopen",cold_owner)
    monkeypatch.setattr(support,"_evaluate",evaluate_owner)


def _install_verifier(audit,monkeypatch):
    fresh,reopen,verify=verification.create_fixture,verification.reopen_fixture,verification.verify
    def fresh_owner(*args,**kwargs):return audit.attach(fresh(*args,**kwargs),"FRESH")
    def cold_owner(*args,**kwargs):
        root=args[0] if args else kwargs["root"]
        return audit.attach(audit.cold_constructor(reopen,root,args,kwargs),"COLD_EXISTING")
    def verify_owner(fx,**kwargs):return audit.call(fx,"VERIFIER_VERIFY",verify,fx,**kwargs)
    monkeypatch.setattr(verification,"create_fixture",fresh_owner)
    monkeypatch.setattr(verification,"reopen_fixture",cold_owner)
    monkeypatch.setattr(verification,"verify",verify_owner)


def _support_REC(fx,target,audit):
    owner=fx.support_composition.runtime
    record=owner.resolve_support_by_identity(target.claim_support_subject_ref,target,support.SUPPORT_SEMANTIC_CONTRACT)
    if record is None:return None
    frames=_complete_rows(owner.ledger_path)["rows"]
    frame=next(r for r in frames if r.get("event_type")=="support_committed" and r["record"]["committed_operation_id"]==record.committed_operation_id)
    operation=LogicalOperationRef(**frame["operation_ref"]);service=RunningInvocationRecoveryService(support_runtime=owner)
    before=owner.ledger_path.read_bytes()
    value=service.reconcile_support(operation,frame["semantic_fingerprint"],record.support_ref)
    negative=service.reconcile_support(operation,"0"*64,record.support_ref)
    audit.consumer.append({"kind":"SUPPORT_REC","native_event":frame,"actual_return":_json(value),
        "actual_conflicting_fingerprint_return":_json(negative),
        "native_point":_point(service.reconcile_support,"reconcile_support",[frame["result_ref"]])})
    assert value["outcome"]=="RECOVERED_DURABLE_RESULT" and value["result"]==record
    assert negative["outcome"]=="INDETERMINATE" and owner.ledger_path.read_bytes()==before
    return value


def _verifier_REC(fx,audit):
    committed=[r for r in _complete_rows(fx.runtime.ledger_path)["rows"] if r.get("event_type")=="verification_committed"]
    if not committed:return None
    frame=committed[-1];receipt=frame["receipt"];operation=LogicalOperationRef(**frame["operation_ref"])
    result=VerificationResultRefSchema.model_validate(receipt["verification_result_ref"])
    revision=ClaimVerificationRevisionRefSchema.model_validate(receipt["verification_revision_ref"])
    before=fx.runtime.ledger_path.read_bytes()
    exact=fx.runtime.resolve_committed_verification(operation,frame["semantic_fingerprint"],result,revision)
    service=RunningInvocationRecoveryService(verification_runtime=fx.runtime)
    value=service.reconcile_verification(operation,frame["semantic_fingerprint"],result,revision)
    changed=revision.model_copy(update={"verification_revision":revision.verification_revision+1})
    negative=service.reconcile_verification(operation,frame["semantic_fingerprint"],result,changed)
    audit.consumer.append({"kind":"VERIFIER_REC","native_event":frame,"exact_cold_owner_return":_json(exact),
        "actual_return":_json(value),"actual_changed_revision_return":_json(negative),
        "native_point":_point(service.reconcile_verification,"reconcile_verification",[result.model_dump(mode="json"),revision.model_dump(mode="json")])})
    assert value["outcome"]=="RECOVERED_DURABLE_RESULT"
    assert value["result"]==exact.verification_result and value["revision"]==exact.verification_revision
    assert negative["outcome"]=="INDETERMINATE" and fx.runtime.ledger_path.read_bytes()==before
    return value


SUPPORT_CASES=("input_before_model","provider_success_before_candidate","candidate_before_support_commit","committed_read",
    "cancelled_input_before_model","cancelled_provider_success_before_candidate","cancelled_candidate_before_support_commit",
    "currentness","conflict","concurrent")


@pytest.mark.parametrize("case",SUPPORT_CASES)
def test_native_support_existing_controls_emit_actual_owner_cold_facts(tmp_path,monkeypatch,request,case):
    audit=NativeAudit(tmp_path,S_ROW,request.node.nodeid,case,concurrent=case=="concurrent")
    _install_support(audit,monkeypatch)
    def run():
        if case in {"input_before_model","provider_success_before_candidate"}:
            support.test_native_support_unresolved_input_reopen_never_redispatches(tmp_path/"native",case,0 if case=="input_before_model" else 1)
        elif case.startswith("cancelled_"):
            support.test_native_support_cancelled_cut_receipts_never_adopt_or_redispatch(tmp_path/"native",case.removeprefix("cancelled_"))
        else:
            function={"candidate_before_support_commit":support.test_native_support_candidate_before_commit_exact_reopen_reconciliation,
                "committed_read":support.test_native_support_committed_read_and_certainty_are_exact_no_dispatch,
                "currentness":support.test_native_support_candidate_currentness_failure_blocks_reconcile,
                "conflict":support.test_native_support_same_identity_conflict_preserves_commit,
                "concurrent":support.test_native_support_independent_owners_concurrent_single_dispatch_commit}[case]
            function(tmp_path/"native")
        fx=audit.fx[-1];_support_REC(fx,audit.targets[str(fx.root)],audit)
    _run(audit,run)


VERIFIER_CUTS=("rejection_persisted_grant_missing","grant_persisted_reservation_missing","child_intent_response_missing",
    "dispatched_unknown","gateway_settled_response_missing","response_complete_validation_interrupted",
    "candidate_persisted_commit_interrupted","commit_persisted_return_interrupted")


@pytest.mark.parametrize("cut",VERIFIER_CUTS)
def test_native_verifier_existing_eight_cuts_emit_actual_owner_cold_facts(tmp_path,monkeypatch,request,cut):
    audit=NativeAudit(tmp_path,V_ROW,request.node.nodeid,cut);_install_verifier(audit,monkeypatch)
    def run():
        verification.test_I_durable_actual_owner_cut_resume_or_fail_closed(tmp_path/"native",monkeypatch,cut)
        assert audit.cuts,"selected original native cut never reached"
        _verifier_REC(audit.fx[-1],audit)
    _run(audit,run)


def test_native_verifier_permanent_slot_concurrent_duplicate_emits_actual_refusals(tmp_path,monkeypatch,request):
    audit=NativeAudit(tmp_path,V_ROW,request.node.nodeid,"terminated_concurrent",concurrent=True);_install_verifier(audit,monkeypatch)
    _run(audit,lambda:verification.test_D_second_violation_permanent_termination_restart_and_concurrency(tmp_path/"native"))


def _cut_verifier(fx,monkeypatch,cut,audit):
    if cut in {"child_input_only","child_response_saved"}:
        original=fx.composer.store.put_json
        def interrupted(value,**kwargs):
            result=original(value,**kwargs)
            child=len(verification._events(fx,"verifier_correction_authorized"))==1
            selected=kwargs.get("artifact_type")==("canonical_verifier_input" if cut=="child_input_only" else "canonical_verifier_response")
            if child and selected:
                audit.cut(cut,_point(_cut_verifier,"Verifier.durable_before_native_cut",[result.artifact_ref]),
                    {"native_reference":result.model_dump(mode="json"),"original_body":value})
                raise KeyboardInterrupt(cut)
            return result
        monkeypatch.setattr(fx.composer.store,"put_json",interrupted)
    else:
        original=fx.runtime.commit_verification
        def interrupted(*args,**kwargs):
            if VERIFIER_LOCAL_CORRECTION_KEY in args[3].deterministic_validation_facts:
                audit.cut(cut,_point(_cut_verifier,"Verifier.candidate_before_formal_commit"),
                    {"original_candidate":args[3].model_dump(mode="json")})
                raise KeyboardInterrupt(cut)
            return original(*args,**kwargs)
        monkeypatch.setattr(fx.runtime,"commit_verification",interrupted)
    with pytest.raises(KeyboardInterrupt):verification.verify(fx)
    assert audit.cuts,"Actual selected native cut was not reached"


@pytest.mark.parametrize("cut",("child_input_only","child_response_saved","child_candidate_saved"))
def test_native_verifier_cancelled_precommit_cold_owner_never_adopts_or_dispatches(tmp_path,monkeypatch,request,cut):
    audit=NativeAudit(tmp_path,V_ROW,request.node.nodeid,cut);_install_verifier(audit,monkeypatch)
    def run():
        fx=verification.create_fixture(tmp_path/"native",[verification._illegal_reply(),native.legal_reply()])
        _cut_verifier(fx,monkeypatch,cut,audit)
        run=fx.lifecycle.load_run(fx.context.active_lineage.run_id)
        fx.lifecycle.transition_run(run.identity.run_id,run.lifecycle_revision,RunLifecycleStatus.CANCELLED)
        cold=verification.reopen_fixture(fx.root)
        before=cold.runtime.ledger_path.read_bytes();budget=cold.budget.canonical_state()
        physical_before=len([e for e in audit.events if e["kind"]=="ACTUAL_INVOKER_ENTRY"])
        with pytest.raises(Exception):verification.verify(cold)
        assert cold.runtime.ledger_path.read_bytes()==before and cold.budget.canonical_state()==budget
        assert len([e for e in audit.events if e["kind"]=="ACTUAL_INVOKER_ENTRY"])==physical_before
        assert verification._events(cold,"verification_committed")==[]
    _run(audit,run)


@pytest.mark.parametrize("kind",("SUPPORT","VERIFIER"))
def test_native_committed_cancelled_cold_REC_reads_exact_result_without_ack_or_dispatch(tmp_path,monkeypatch,request,kind):
    row=S_ROW if kind=="SUPPORT" else V_ROW
    audit=NativeAudit(tmp_path,row,request.node.nodeid,"committed_cancelled_REC")
    if kind=="SUPPORT":_install_support(audit,monkeypatch)
    else:_install_verifier(audit,monkeypatch)
    def run():
        if kind=="SUPPORT":
            fx,target,reply=support._fresh(tmp_path/"native");record=support._evaluate_commit(fx,target)
        else:
            fx=verification.create_fixture(tmp_path/"native",[verification._illegal_reply(),native.legal_reply()])
            record=verification.verify(fx)[0]
        current=fx.lifecycle.load_run(fx.context.active_lineage.run_id)
        fx.lifecycle.transition_run(current.identity.run_id,current.lifecycle_revision,RunLifecycleStatus.CANCELLED)
        cold=support._reopen(fx,reply) if kind=="SUPPORT" else verification.reopen_fixture(fx.root)
        owner=cold.support_composition.runtime if kind=="SUPPORT" else cold.runtime
        journal=owner.ledger_path.read_bytes();coordination=owner.coordination_store.log.path
        before_coordination=coordination.read_bytes() if coordination.exists() else b""
        budget=cold.budget.canonical_state();physical_before=len([e for e in audit.events if e["kind"]=="ACTUAL_INVOKER_ENTRY"])
        forbidden=[]
        def illegal(*args,**kwargs):
            forbidden.append({"actual_args":_json(args),"actual_kwargs":_json(kwargs)})
            raise AssertionError("Cancelled committed REC reader entered native mutation/provider boundary")
        with patch.object(owner,"_ack_side_effect",illegal),patch.object(owner,"_reconcile" if kind=="SUPPORT" else "_reconcile_coordination",illegal), \
             patch.object(owner,"commit_support" if kind=="SUPPORT" else "commit_verification",illegal), \
             patch.object(cold.gateway.invoker,"invoke",illegal):
            value=_support_REC(cold,target,audit) if kind=="SUPPORT" else _verifier_REC(cold,audit)
        assert value is not None and forbidden==[]
        assert owner.ledger_path.read_bytes()==journal
        assert (coordination.read_bytes() if coordination.exists() else b"")==before_coordination
        assert cold.budget.canonical_state()==budget
        assert len([e for e in audit.events if e["kind"]=="ACTUAL_INVOKER_ENTRY"])==physical_before
        audit.event("CANCELLED_PASSIVE_REC_CLOSED",{"kind":kind,"actual_forbidden_boundary_entries":forbidden,
            "actual_record":_json(record),"actual_REC_return":_json(value)})
    _run(audit,run)


def test_native_support_commit_append_ack_loss_cancelled_cold_REC_measures_exact_output(tmp_path,monkeypatch,request):
    audit=NativeAudit(tmp_path,S_ROW,request.node.nodeid,"support_append_before_ACK");_install_support(audit,monkeypatch)
    def run():
        fx,target,reply=support._fresh(tmp_path/"native")
        outcome=support._evaluate(fx,target);owner=fx.support_composition.runtime;original=owner.log.append_serialized
        def interrupted(line):
            result=original(line);frame=json.loads(line)
            if frame["event_type"]=="support_committed":
                audit.cut("support_append_before_ACK",_point(interrupted,"Support.durable_append_before_ACK"),frame)
                raise support.NativeCut("support_append_before_ACK")
            return result
        with patch.object(owner.log,"append_serialized",interrupted):
            with pytest.raises(support.NativeCut):owner.commit_support(outcome.candidate,fx.context)
        current=fx.lifecycle.load_run(fx.context.active_lineage.run_id)
        fx.lifecycle.transition_run(current.identity.run_id,current.lifecycle_revision,RunLifecycleStatus.CANCELLED)
        cold=support._reopen(fx,reply);runtime=cold.support_composition.runtime;coordination=runtime.coordination_store.log.path
        before=coordination.read_bytes() if coordination.exists() else b""
        with patch.object(runtime,"_ack_side_effect",side_effect=AssertionError("passive REC ACK forbidden")), \
             patch.object(runtime,"_reconcile",side_effect=AssertionError("passive REC replay forbidden")):
            _support_REC(cold,target,audit)
        assert (coordination.read_bytes() if coordination.exists() else b"")==before
    _run(audit,run)


@pytest.mark.parametrize("tail",(b'{"torn":',b'{"bad":}\n',b'\xff\n'),ids=("torn","complete_json_corrupt","complete_utf8_corrupt"))
def test_native_support_journal_transport_boundary_measures_original_prefix(tmp_path,monkeypatch,request,tail):
    audit=NativeAudit(tmp_path,S_ROW,request.node.nodeid,"support_transport_tail");_install_support(audit,monkeypatch)
    def run():
        fx,target,reply=support._fresh(tmp_path/"native");record=support._evaluate_commit(fx,target)
        owner=fx.support_composition.runtime;prefix=owner.ledger_path.read_bytes()
        with owner.ledger_path.open("ab") as stream:stream.write(tail);stream.flush();os.fsync(stream.fileno())
        transport=owner.ledger_path.read_bytes()
        audit.cut("transport_tail",_point(test_native_support_journal_transport_boundary_measures_original_prefix,
            "Support.literal_native_transport_fault"),{"tail_hex":tail.hex(),"original_prefix_sha256":sha256(prefix).hexdigest(),
                "transport_before_sha256":sha256(transport).hexdigest()})
        if not tail.endswith(b"\n"):
            cold=support._reopen(fx,reply)
            assert cold.support_composition.runtime.resolve_support(record.support_ref)==record
            assert owner.ledger_path.read_bytes()==prefix
            _support_REC(cold,target,audit)
        else:
            with pytest.raises((ValueError,UnicodeDecodeError)):
                cold=support._reopen(fx,reply);cold.support_composition.runtime.resolve_support(record.support_ref)
            assert owner.ledger_path.read_bytes()==transport
        audit.event("TRANSPORT_ACTUAL_FINAL_BYTES",{"prefix_sha256":sha256(prefix).hexdigest(),
            "before_sha256":sha256(transport).hexdigest(),"after_sha256":sha256(owner.ledger_path.read_bytes()).hexdigest()})
    _run(audit,run)


def test_original_native_head_CAS_oracle_kept_with_explicit_D5_no_Gateway_disposition(tmp_path,monkeypatch,request):
    """Pure Runtime fixture retains CAS oracle; does not claim physical0."""
    setup=native_cas._setup_verification_chain;observed=[]
    def source_owned(*args,**kwargs):
        values=setup(*args,**kwargs)
        env,runtime,claim,binding,support_runtime,support_record,state=values
        original=runtime.commit_verification
        def commit(*args,**kwargs):
            before=runtime.resolve_claim_head(claim.claim_ref)
            try:result=original(*args,**kwargs)
            except BaseException as error:
                observed.append({"before_head":_json(before),"actual_error":_error(error),
                    "after_head":_json(runtime.resolve_claim_head(claim.claim_ref))});raise
            observed.append({"before_head":_json(before),"actual_return":_json(result),
                "after_head":_json(runtime.resolve_claim_head(claim.claim_ref))});return result
        runtime.commit_verification=commit
        return values
    monkeypatch.setattr(native_cas,"_setup_verification_chain",source_owned)
    try:native_cas.test_claim_head_expected_revision_is_strict_cas(tmp_path/"native")
    finally:
        authority=ROOT/"docs/fpbca_bcd_closure_design_v0_2_20261007/03_D_VERIFICATION_DESIGN_V0_2.md"
        _once(tmp_path/"native-row-observations"/"D2_REC-V"/"d5-pure-disposition.json",{
            "nodeid":request.node.nodeid,"status":"SOURCE_BOUND_N_A",
            "source_point":_point(native_cas.test_claim_head_expected_revision_is_strict_cas,"original_native_CAS_oracle"),
            "actual_native_CAS_values":observed,
            "dispositions":[{"field":field,"status":"SOURCE_BOUND_N_A","source_decision":{
                "authority_path":str(authority),"authority_sha256":sha256(authority.read_bytes()).hexdigest(),
                "authority_pointer":"D5/line169 dispatch-mutation accounting where relevant",
                "scope":"PURE_RUNTIME_NO_GATEWAY","reason":"Existing deterministic Runtime chain has no ModelGateway/InvocationBudget owner. Exact native head CAS is observed; no physical0 or finite-resource receipt is inferred."}}
                for field in ("actual_physical_dispatch_count","actual_settlement_count")]})



def test_native_support_before_durable_input_cold_first_admission_measures_exact_dispatch(tmp_path,monkeypatch,request):
    """Absent input is measured at its actual producer cut; generic absence is not proof."""
    audit=NativeAudit(tmp_path,S_ROW,request.node.nodeid,"support_before_input");_install_support(audit,monkeypatch)
    def run():
        fx,target,reply=support._fresh(tmp_path/"native");composition=fx.support_composition
        before=fx.lifecycle.budget_preimage(fx.context.active_lineage.run_id);original=composition.store.put_json
        def interrupted(value,**options):
            if options.get("artifact_type")=="claim_support_input":
                audit.cut("before_durable_support_input",_point(interrupted,"Support.exact_input_producer_before_write"),
                    {"actual_native_input_value":value,"actual_native_put_options":options,
                     "scope":"SIMULATED_NATIVE_CALL_FRAME_NOT_OS_EXIT"})
                raise support.NativeCut("before_durable_support_input")
            return original(value,**options)
        with patch.object(composition.store,"put_json",interrupted):
            with pytest.raises(support.NativeCut):support._evaluate(fx,target)
        intent,result,input_body,candidate=support._receipt_material(fx,target)
        assert input_body is None and candidate is None
        assert fx.lifecycle.budget_preimage(fx.context.active_lineage.run_id)==before
        assert len([e for e in audit.events if e["kind"]=="ACTUAL_INVOKER_ENTRY"])==0
        cold=support._reopen(fx,reply);record=support._evaluate_commit(cold,target)
        _support_REC(cold,target,audit)
        assert record is not None and len([e for e in audit.events if e["kind"]=="ACTUAL_INVOKER_ENTRY"])==1
    _run(audit,run)
