"""Native trusted application approval/continuation directed acceptance.

Prepared TXT only; parent exports after Step5 GREEN and runs RED before Step6
source export. All native fixtures keep the existing 24000 baseline. Physical
measurements wrap the actual Fake invoker entry, never a trace inference.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
from uuid import uuid4

import pytest

from application.mainline_service import MainlineApplicationService
from application.run_workspace import RunWorkspace
from core.config import get_settings
from core.persistence.crash_safe_jsonl import canonical_json_sha256
from test_phase2_runtime_safety_closure import _run_production_fake, _execution

# New local harness allowance only; production Budget/Run authority is unchanged.
NATIVE_APPLICATION_TEST_WAIT_SECONDS = 180


def _interfaces():
    # Actual per-test boundary failure during RED, not collection-time failure.
    from agent.supervisor_agent import BudgetTopupCommandSchema
    assert callable(getattr(MainlineApplicationService, "topup_budget_run", None))
    assert callable(getattr(MainlineApplicationService, "resume_budget_run", None))
    return BudgetTopupCommandSchema


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for name, value in {
        "USE_REAL_RAG_TOOL":"false", "FAKE_RAG_SCENARIO":"default",
        "FAKE_LLM_SCENARIO":"default", "ENABLE_AGENT_SELF_RAG":"false",
        "ENABLE_SEMANTIC_GATE":"false",
    }.items():
        monkeypatch.setenv(name,value)


def _native_grounding_commit_observer(engine, case, observations):
    """Observe actual resumed DOCUMENT commits; preserve native finalize calls."""
    from apps.enterprise_document.schemas.post_write.foundation import ArtifactScope
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import GroundingAccounting
    pw = engine.stage_service.target_post_write_composition
    citation = pw.citation_grounding
    original = pw.validation_store.finalize_validator_execution

    def record_phase(phase, terminal_event, record, input_envelope, active):
        row = {"schema_version":"native_budget_grounding_passive_observation_v1",
            "scope":"ACTUAL_EXISTING_APP_RESUME_NATIVE_DOCUMENT_FINALIZE_PASSIVE_READ",
            "phase":phase,"active_engine_entry":active,
            "validation_run_id":record.validation_run_id,"validator_id":record.validator_id,
            "validator_execution_ref":record.validator_execution_ref,
            "expected_item_ref":"citation:item:"+input_envelope.artifact_state_ref.artifact_hash,
            "terminal_event_argument":terminal_event.model_dump(mode="json"),
            "record_argument":record.model_dump(mode="json"),
            "input_envelope_argument":input_envelope.model_dump(mode="json")}
        try:
            binding = active["continuation"]
            native_denial, _ = citation.store.get_json_with_reference(binding["predecessor_result_ref"],
                task_id=active["task_id"],run_id=active["run_id"])
            identity = {"task_id":native_denial.task_id,"run_id":native_denial.run_id,
                "section_id":native_denial.section_id}
            refusal = citation.resolve_budget_refusal(receipt_ref=binding["predecessor_result_ref"],
                expected_sha256=binding["predecessor_result_sha256"],identity=identity)
            kind = refusal.get("operation_kind","CITATION_ASSOCIATION")
            doc_identity = refusal["document_identity"] if kind=="DOCUMENT_SUPPORT" else identity
            input_ref = refusal["document_input_ref"] if kind=="DOCUMENT_SUPPORT" else refusal["input_ref"]
            native_input, doc_input = citation.store.get_json_with_reference(input_ref,**doc_identity)
            material = doc_input["input_material"]
            row.update({"operation_kind":kind,"native_denial_reference":native_denial.model_dump(mode="json"),
                "document_input_reference":native_input.model_dump(mode="json"),"document_identity":doc_identity,
                "document_input_material":material,"native_refusal":refusal})
            if (material["validation_run_id"]!=record.validation_run_id
                    or material["subject_hash"]!=record.subject_hash
                    or material["artifact_state_ref"]!=input_envelope.artifact_state_ref.model_dump(mode="json")
                    or material.get("visible_artifact",{}).get("payload_kind")!="document"):
                raise AssertionError("passive observer reached another DOCUMENT identity")
            canonical_run = engine.lifecycle_service.load_run_lifecycle(active["run_id"])
            transition = engine.lifecycle_service.current_budget_transition(active["run_id"])
            row.update({"canonical_run":canonical_run.model_dump(mode="json"),
                "resume_event_ref":transition.event_ref if transition else None,
                "resume_event_sha256":transition.event_sha256 if transition else None})
            if (canonical_run.status.value!="running" or transition is None or transition.kind!="RESUME"
                    or transition.event_ref!=binding["resume_event_ref"]
                    or transition.event_sha256!=binding["resume_event_sha256"]):
                raise AssertionError("passive observer requires exact active RUNNING resume")
            manager = engine.budget_runtime(active["task_id"],active["run_id"],require_existing_account=True)
            with case.counter_lock:
                physical_before = json.loads(json.dumps(case.physical))
            budget_before = manager.canonical_state().model_dump(mode="json")
            refusal_bytes_before = citation.store._path_for_ref(binding["predecessor_result_ref"]).read_bytes()
            with pw.database.read_transaction() as connection:
                validation_run = pw.validation_store.resolve_run_exact(record.validation_run_id,connection=connection)
                terminal = pw.validation_store.find_validator_execution_for_item(
                    validation_run_id=record.validation_run_id,validator_id=citation.VALIDATOR_ID,
                    expected_item_ref=row["expected_item_ref"],connection=connection)
                markers = connection.execute(
                    "SELECT event_id,event_json FROM validation_execution_events WHERE validation_run_id=? "
                    "AND event_kind='INTERMEDIATE_COMMITTED' AND json_extract(event_json,'$.payload.target_ref')=?",
                    (record.validation_run_id,row["expected_item_ref"])).fetchall()
                intermediate = pw.validation_intermediate_runtime.resolve_typed(
                    record.intermediate_refs[0],GroundingAccounting,connection=connection)
            default_slot = citation.read_budget_document_admission(binding,identity=identity)
            full_slot = citation.read_budget_document_admission(binding,identity=identity,include_grounding_commit=True)
            budget_after = manager.canonical_state().model_dump(mode="json")
            with case.counter_lock:
                physical_after = json.loads(json.dumps(case.physical))
            refusal_bytes_after = citation.store._path_for_ref(binding["predecessor_result_ref"]).read_bytes()
            row.update({"status":"MEASURED","validation_run":validation_run.model_dump(mode="json"),
                "actual_grounding_terminal":terminal.model_dump(mode="json") if terminal is not None else None,
                "actual_retained_item_markers":[{"event_id":m["event_id"],"event_json":m["event_json"]} for m in markers],
                "actual_grounding_intermediate":intermediate.model_dump(mode="json"),
                "default_native_slot":_native_observation_wire(default_slot),
                "grounding_native_slot":_native_observation_wire(full_slot),
                "physical_requests_before_passive":physical_before,"physical_requests_after_passive":physical_after,
                "budget_before_passive":budget_before,"budget_after_passive":budget_after,
                "old_denial_bytes_sha256_before":hashlib.sha256(refusal_bytes_before).hexdigest(),
                "old_denial_bytes_sha256_after":hashlib.sha256(refusal_bytes_after).hexdigest()})
        except Exception as exc:
            # Observation failure cannot replace or prevent native finalization.
            # The end-of-test control fails explicitly; no missing value becomes0.
            row.update({"status":"UNMEASURED_OBSERVER_ERROR","error_type":type(exc).__name__,"error":str(exc)})
        target_root = case.root/"native_grounding_commit_observations"
        key = canonical_json_sha256({"phase":phase,"validator_execution_ref":record.validator_execution_ref,
            "continuation":active["continuation"]})
        target = target_root/(key+".json")
        try:
            target_root.mkdir(parents=True,exist_ok=True)
            payload=(json.dumps(_native_observation_wire(row),ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
            with target.open("xb") as stream:
                stream.write(payload);stream.flush();os.fsync(stream.fileno())
            row["observation_file"]={"path":str(target),"sha256":hashlib.sha256(payload).hexdigest()}
        except Exception as exc:
            row.update({"status":"UNMEASURED_OBSERVATION_DURABILITY_ERROR","durability_error_type":type(exc).__name__,
                "durability_error":str(exc),"observation_path":str(target)})
        observations.append(row)

    def observed_finalize(terminal_event, record, *, stage_plan, input_envelope):
        # Initial constructor may run completed Section work before case binding.
        # It has no active resume entry, and is never an observation premise.
        if (not case.engine_entries or record.validator_id!=citation.VALIDATOR_ID
                or input_envelope.artifact_state_ref.artifact_scope is not ArtifactScope.DOCUMENT):
            return original(terminal_event,record,stage_plan=stage_plan,input_envelope=input_envelope)
        active = json.loads(json.dumps(case.engine_entries[-1]))
        item_ref = "citation:item:"+input_envelope.artifact_state_ref.artifact_hash
        if (record.validation_run_id!=input_envelope.validation_run_id
                or tuple(row.expected_validation_item_ref for row in record.expected_item_accounting)!=(item_ref,)):
            return original(terminal_event,record,stage_plan=stage_plan,input_envelope=input_envelope)
        record_phase("BEFORE_NATIVE_FINALIZE",terminal_event,record,input_envelope,active)
        returned = original(terminal_event,record,stage_plan=stage_plan,input_envelope=input_envelope)
        record_phase("AFTER_NATIVE_FINALIZE",terminal_event,record,input_envelope,active)
        return returned
    pw.validation_store.finalize_validator_execution = observed_finalize


def _native_grounding_commit_oracles(observations, expected_kind, *, root, allow_unreached=False):
    rows=[row for row in observations if row.get("operation_kind")==expected_kind]
    summary={"schema_version":"native_budget_grounding_passive_summary_v1",
        "expected_operation_kind":expected_kind,"actual_observations":_native_observation_wire(observations),
        "status":"NATIVE_PHASES_OBSERVED_PENDING_ORACLES" if rows else "UNREACHED",
        "gap_pass":False,"allow_unreached":allow_unreached}
    directory=root/"native_grounding_commit_observations"
    directory.mkdir(parents=True,exist_ok=True)
    payload=(json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    with (directory/("summary_"+expected_kind+".json")).open("xb") as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    assert all(row["status"]=="MEASURED" and row.get("observation_file") for row in observations),observations
    if not rows:
        assert allow_unreached,"UNREACHED: no actual matching resumed DOCUMENT Grounding finalize"
        return summary
    grouped={}
    for row in rows:
        assert row["canonical_run"]["status"]=="running"
        assert row["physical_requests_before_passive"]==row["physical_requests_after_passive"]
        assert row["budget_before_passive"]==row["budget_after_passive"]
        assert row["old_denial_bytes_sha256_before"]==row["old_denial_bytes_sha256_after"]
        assert row["default_native_slot"]["phase"]=="COMPLETED"
        assert row["actual_retained_item_markers"]
        key=(row["validator_execution_ref"],row["resume_event_ref"])
        group=grouped.setdefault(key,{})
        assert row["phase"] not in group
        group[row["phase"]]=row
    for group in grouped.values():
        assert set(group)=={"BEFORE_NATIVE_FINALIZE","AFTER_NATIVE_FINALIZE"}
        before,after=group["BEFORE_NATIVE_FINALIZE"],group["AFTER_NATIVE_FINALIZE"]
        assert before["actual_grounding_terminal"] is None
        assert before["grounding_native_slot"]["phase"]=="INDETERMINATE"
        assert before["grounding_native_slot"]["native_admission_phase"]=="COMPLETED"
        assert before["grounding_native_slot"]["grounding_commit"]["reason"]=="INCOMPLETE_GROUNDING_COMMIT"
        assert after["actual_grounding_terminal"]["validator_execution_ref"]==after["validator_execution_ref"]
        assert after["grounding_native_slot"]["phase"]=="COMPLETED"
        assert after["actual_retained_item_markers"]==before["actual_retained_item_markers"]
    result={**summary,"status":"NATIVE_PASSIVE_PHASE_ORACLES_PASSED","gap_pass":True}
    payload=(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    with (directory/("result_"+expected_kind+".json")).open("xb") as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    return result


class _NativeApplicationCase:
    def __init__(self, root, *, engine_hook=None, run_builder=None, expected_current_child_count=8, native_run_id=None):
        self.Command = _interfaces()
        assert type(expected_current_child_count) is int and expected_current_child_count>0
        self.expected_current_child_count=expected_current_child_count
        self.root = root
        self.physical = []
        self.engine_entries = []
        self.counter_lock = threading.Lock()
        self.test_workers = []
        def measured_engine(engine):
            invoker = engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
            invoke = invoker.invoke
            def measured(request, model_name):
                raw = request.model_dump(mode="json")
                with self.counter_lock:
                    self.physical.append({"model_call_id":request.model_call_id,
                        "request_sha256":canonical_json_sha256(raw),"model_name":model_name,
                        "caller_agent":request.caller_agent,"extra":raw["extra"]})
                return invoke(request,model_name)
            invoker.invoke = measured
            resume = engine.resume_budget_document
            def counted(workflow,state,continuation):
                with self.counter_lock:
                    self.engine_entries.append({"task_id":state.task_id,"run_id":state.run_id,
                        "continuation":continuation.model_dump(mode="json")})
                return resume(workflow,state,continuation)
            engine.resume_budget_document = counted
            if engine_hook is not None: engine_hook(engine,self)
        native_run=_run_production_fake if run_builder is None else run_builder
        self.summary, self.factory = native_run(root,
            run_id=("bt_app_"+uuid4().hex if native_run_id is None else native_run_id),mutate_engine=measured_engine)
        assert _execution(self.summary)["status"] == "paused"
        assert _execution(self.summary)["metadata"]["pause_purpose"] == "BUDGET_AUTHORIZATION"
        self.service = MainlineApplicationService(supervisor_factory=self.factory)
        self.settings = replace(get_settings(),data_root=root,default_model_name="fake_llm",
            supervisor_model_name="fake_llm",enable_llm_routing=False,
            trace_enabled=True,data_capture_enabled=True)
        self.arguments = dict(project_root=Path(__file__).resolve().parents[2],
            task_id=self.summary["task_id"],run_id=self.summary["run_id"],output_root=root,
            settings=self.settings,allow_demo_defaults=True,enable_agent_self_rag=False)
        runtime = self.open_runtime()
        try:
            engine = runtime.supervisor.workflow_engine
            self.wait = engine.resolve_budget_wait(runtime.workflow,run_id=runtime.task.run_id)
            self.pause = engine.lifecycle_service.current_budget_transition(runtime.task.run_id)
            assert self.pause.kind == "PAUSE"
            self.store = engine.stage_service.target_post_write_composition.citation_grounding.store
            self.denial_path = self.store._path_for_ref(self.wait.refusal.native_denial_ref)
            self.original_denial_bytes = self.denial_path.read_bytes()
            self.wait_path = self.store._path_for_ref(self.pause.wait_ref)
            self.original_wait_bytes = self.wait_path.read_bytes()
            self.old_budget = engine.budget_runtime(runtime.task.task_id,runtime.task.run_id,
                require_existing_account=True).canonical_state()
            assert self.old_budget.limits.model_tokens_total == 24000
            assert len(self.wait.current_children)==self.expected_current_child_count
            self.budget_journal_path = RunWorkspace(root,runtime.task.task_id,runtime.task.run_id).runs_dir / "canonical_budget.jsonl"
            self.original_budget_bytes = self.budget_journal_path.read_bytes()
        finally:
            self.close_runtime(runtime)
        self.initial_physical_count = len(self.physical)
        self.before = self.capture()

    def open_runtime(self):
        return self.service._build_existing_runtime(**self.arguments,
            retrieval_access_scope=None, require_execution_compatibility=True)

    def close_runtime(self,runtime):
        self.service._close_supervisor(runtime.supervisor,primary_error=None)

    def command(self, *, approval_id="approval",authorization_id="finite-user-auth",tokens=20000,**changes):
        ceiling = self.old_budget.limits.model_dump(mode="json")
        ceiling["model_tokens_total"] += tokens
        values = dict(approval_id=approval_id,authorization_id=authorization_id,
            command_source="APPLICATION",issuer="explicit-local-test-user",
            wait_event_ref=self.pause.event_ref,wait_event_sha256=self.pause.event_sha256,
            delta={"model_tokens_total":tokens},cumulative_ceiling=ceiling)
        values.update(changes)
        return self.Command(**values)

    def approve(self,command,*,continue_document=False):
        return self.service.topup_budget_run(**self.arguments,command=command,
            continue_document=continue_document)

    def resume(self,ack):
        return self.service.resume_budget_run(**self.arguments,
            accepted_grant_ref=ack["grant_ref"],accepted_grant_sha256=ack["grant_sha256"])

    def capture(self):
        runtime = self.open_runtime()
        try:
            engine = runtime.supervisor.workflow_engine
            native = runtime.supervisor.inspect_budget_state(runtime.task)
            original_children = [engine.lifecycle_service.load_attempt(row.identity.attempt_id).model_dump(mode="json")
                for row in self.wait.current_children]
            from apps.enterprise_document.workflows.top_level_business_graph import phase2_checkpoint_identity
            checkpoint = engine.checkpointer.get_tuple(phase2_checkpoint_identity(runtime.workflow,run_id=runtime.task.run_id))
            values = checkpoint.checkpoint["channel_values"]
            return {"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,
                "scope":"ACTUAL_FACTORY_NATIVE_APPLICATION_FIXED_24000",**native,
                "fixture_expected_current_child_count":self.expected_current_child_count,
                "task_state":runtime.task_manager.get_task(runtime.task.task_id).model_dump(mode="json"),
                "original_children_current":original_children,
                "wait_packet":self.wait.model_dump(mode="json"),
                "native_wait_event":self.pause.event_ref,"native_wait_event_sha256":self.pause.event_sha256,
                "checkpoint_config":checkpoint.config,"checkpoint_node_outputs":values.get("node_outputs",[]),
                "physical_count":len(self.physical),"physical_requests":list(self.physical),
                "engine_entry_count":len(self.engine_entries),"engine_entries":list(self.engine_entries),
                "model_usage":runtime.supervisor.model_usage_snapshot(),
                "budget_journal_sha256":hashlib.sha256(self.budget_journal_path.read_bytes()).hexdigest(),
                "denial_envelope_sha256":hashlib.sha256(self.denial_path.read_bytes()).hexdigest(),
                "original_denial_native_sha256":self.wait.refusal.native_denial_sha256}
        finally:
            self.close_runtime(runtime)

    def receipt(self,rows,label,*,acknowledgements=(),exceptions=(),observations=None):
        after = self.capture()
        assert self.denial_path.read_bytes()==self.original_denial_bytes
        assert self.wait_path.read_bytes()==self.original_wait_bytes
        assert self.budget_journal_path.read_bytes().startswith(self.original_budget_bytes)
        body = {"schema_version":"bt_application_native_observation_v1","rows":list(rows),
            "label":label,"before":self.before,"after":after,
            "acknowledgements":list(acknowledgements),"exceptions":list(exceptions),
            "additional_native_observations":observations or {},
            "physical_delta":after["physical_count"]-self.before["physical_count"],
            "engine_entry_delta":after["engine_entry_count"]-self.before["engine_entry_count"],
            "baseline_preserved":after["budget"]["limits"]==self.before["budget"]["limits"],
            "old_denial_bytes_preserved":True,"old_wait_bytes_preserved":True,
            "used_before":self.before["budget"]["used"],"used_after":after["budget"]["used"]}
        path = self.root.parent/(label+"_native_observation.json")
        assert not path.exists()
        path.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding="utf-8")
        return after


@pytest.mark.parametrize("field,value",[
    ("delta",{"model_tokens_total":True}),
    ("delta",{"model_tokens_total":-1}),
    ("delta",{"model_tokens_total":1.5}),
    ("delta",{"model_tokens_total":"1000"}),
    ("delta",{"model_tokens_total":2**63}),
    ("delta",{"invented_dimension":1}),
    ("cumulative_ceiling",{"model_tokens_total":float("inf")}),
    ("authorized_delta",{"model_tokens_total":False}),
    ("command_source","MODEL"),
    ("issuer"," "),
    ("unknown_authority",True),
])
def test_trusted_command_rejects_invalid_finite_authority(field,value):
    Command = _interfaces()
    payload = dict(approval_id="explicit-client",authorization_id="explicit-auth",
        command_source="API",issuer="authenticated-user",wait_event_ref="native-wait",
        wait_event_sha256="a"*64,delta={"model_tokens_total":1000},
        cumulative_ceiling={"model_tokens_total":25000})
    payload[field]=value
    with pytest.raises(ValueError):
        Command(**payload)


def test_native_application_without_authority_and_explicit_denial_keep_pause(tmp_path):
    case = _NativeApplicationCase(tmp_path/"native")
    with pytest.raises(ValueError,match="typed explicit"):
        case.approve(case.command().model_dump(mode="json"))
    denied = case.approve(case.command(decision="DENIED",tokens=0))
    ack = denied["budget_acknowledgement"]
    assert ack["decision"]=="DENIED" and ack["grant_ref"] is None
    after = case.receipt(["BT-04","BT-29"],"noauth_denied",acknowledgements=[ack])
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["budget"]==case.before["budget"]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


@pytest.mark.parametrize("kind",["too_small","ceiling","expired"])
def test_native_application_rejections_have_immutable_ack_no_dispatch(tmp_path,kind):
    case = _NativeApplicationCase(tmp_path/"native")
    if kind=="too_small": command=case.command(tokens=1)
    elif kind=="ceiling": command=case.command(cumulative_ceiling=case.old_budget.limits.model_dump(mode="json"))
    else: command=case.command(expires_at=datetime.now(timezone.utc)-timedelta(minutes=1))
    response = case.approve(command)
    ack = response["budget_acknowledgement"]
    assert ack["decision"] in {"LIMIT_REACHED","REJECTED"} and ack["grant_ref"] is None
    replay = case.approve(command)["budget_acknowledgement"]
    assert replay==ack
    after = case.receipt(["BT-04","BT-06"],"rejected_"+kind,acknowledgements=[ack,replay])
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["budget"]==case.before["budget"] and after["physical_count"]==case.before["physical_count"]


def test_native_approval_only_reopen_alias_dedup_preserves_baseline_failure_and_usage(tmp_path):
    case = _NativeApplicationCase(tmp_path/"native")
    command=case.command()
    first=case.approve(command)["budget_acknowledgement"]
    again=case.approve(command)["budget_acknowledgement"]
    alias=case.approve(case.command(approval_id="alias-client"))["budget_acknowledgement"]
    assert first==again and (alias["grant_ref"],alias["grant_sha256"])==(first["grant_ref"],first["grant_sha256"])
    with pytest.raises(ValueError):
        case.approve(case.command(tokens=19999))
    with pytest.raises(ValueError):
        case.approve(case.command(approval_id="changed-alias",tokens=19999))
    after=case.receipt(["BT-06","BT-07","BT-12","BT-18"],"approval_only_alias",
        acknowledgements=[first,again,alias])
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert len(after["budget"]["grant_history"])==1
    assert after["budget"]["revision"]==case.before["budget"]["revision"]+1
    assert after["effective_limits"]["model_tokens_total"]==44000
    assert after["budget"]["limits"]["model_tokens_total"]==24000
    assert after["budget"]["used"]==case.before["budget"]["used"]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0
    usage=RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).read_model_usage()
    assert usage==after["model_usage"]


def test_native_two_client_threads_share_single_authorization_debit(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    def approve(index): return case.approve(case.command(approval_id="thread-"+str(index)))["budget_acknowledgement"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        acknowledgements=list(executor.map(approve,range(2)))
    assert len({ack["grant_ref"] for ack in acknowledgements})==1
    after=case.receipt(["BT-07","BT-08"],"concurrent_approval",acknowledgements=acknowledgements)
    assert len(after["budget"]["grant_history"])==1
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


def test_native_two_thread_resume_has_one_engine_one_document_dispatch_and_cold_replay(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    responses=[];native_thread_errors=[]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(case.resume,approval) for _index in range(2)]
        for index,future in enumerate(futures):
            try:responses.append(future.result())
            except (ValueError,RuntimeError,OSError) as error:
                import traceback
                native_thread_errors.append({"client_index":index,"error_type":type(error).__name__,
                    "error_message":str(error),"errno":getattr(error,"errno",None),
                    "traceback":traceback.format_exc()})
    if native_thread_errors:
        case.receipt(["BT-08","BT-16"],"concurrent_resume_owner_contention",
            acknowledgements=[approval,*[response["budget_acknowledgement"] for response in responses]],
            exceptions=native_thread_errors,
            observations={"actual_native_threaded_resume_outcomes":responses,
                "actual_native_threaded_resume_errors":native_thread_errors,
                "all_fresh_client_futures_joined":all(future.done() for future in futures)})
    assert not native_thread_errors and len(responses)==2
    acknowledgements=[response["budget_acknowledgement"] for response in responses]
    assert acknowledgements[0]==acknowledgements[1]
    before_replay=case.capture()
    replay=case.resume(approval)["budget_acknowledgement"]
    assert replay==acknowledgements[0]
    after=case.receipt(["BT-02","BT-06","BT-08","BT-18","BT-21"],"concurrent_resume",
        acknowledgements=[approval,*acknowledgements,replay],
        observations={"actual_native_threaded_resume_outcomes":responses,
            "actual_native_threaded_resume_errors":native_thread_errors,
            "all_fresh_client_futures_joined":all(future.done() for future in futures)})
    assert len(after["budget"]["grant_history"])==1 and after["engine_entry_count"]==1
    assert after["run"]["status"]=="success"
    prefix="citation-association-call://sha256/"+case.wait.refusal.semantic_input_sha256+"/admission/"
    calls=[row for row in after["physical_requests"][case.initial_physical_count:]
        if row["extra"].get("logical_operation_id","").startswith(prefix)]
    assert len(calls)==1
    old_ops={row["extra"].get("logical_operation_id") for row in case.before["physical_requests"]
        if row["extra"].get("logical_operation_id")}
    old_calls={row["model_call_id"] for row in case.before["physical_requests"]}
    assert all(row["extra"].get("logical_operation_id") not in old_ops
        for row in after["physical_requests"][case.initial_physical_count:])
    assert all(row["model_call_id"] not in old_calls
        for row in after["physical_requests"][case.initial_physical_count:])
    before_reservations={row["reservation_ref"]["reservation_id"]:row
        for row in case.before["budget"]["reservations"]}
    after_reservations={row["reservation_ref"]["reservation_id"]:row
        for row in after["budget"]["reservations"]}
    for key,reservation in before_reservations.items():
        assert after_reservations[key]==reservation
    assert after["checkpoint_node_outputs"][:len(case.before["checkpoint_node_outputs"])]==case.before["checkpoint_node_outputs"]
    assert after["physical_count"]==before_replay["physical_count"]
    assert after["budget"]==before_replay["budget"]


def test_native_budget_hold_rejects_human_review_and_model_construct_provenance(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    with pytest.raises(ValueError,match="Budget authorization"):
        case.service.resume_run(**case.arguments,decision="approved",
            input_snapshot_version=case.wait.cursor.input_snapshot_version)
    forged=case.command().model_copy(update={"command_source":"MODEL"})
    forged_ack=case.approve(forged)["budget_acknowledgement"]
    assert forged_ack["decision"]=="REJECTED" and forged_ack["grant_ref"] is None
    after=case.receipt(["BT-29"],"human_review_model_rejected")
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["budget"]==case.before["budget"] and after["physical_count"]==case.before["physical_count"]


def _identity_child(connection):
    from agent.supervisor_agent import _budget_native_process_identity
    connection.send(_budget_native_process_identity())
    if connection.poll(NATIVE_APPLICATION_TEST_WAIT_SECONDS):
        assert connection.recv()=="exit"
    connection.close()


def test_native_windows_process_birth_requires_positive_original_closure(tmp_path):
    _interfaces()
    from agent.supervisor_agent import _budget_native_process_identity, _budget_original_process_closed
    assert os.name=="nt"
    mine=_budget_native_process_identity()
    assert mine["state"]=="ACTIVE" and mine["pid"]==os.getpid() and mine["creation_filetime"]>0
    with pytest.raises(RuntimeError,match="remains active"):
        _budget_original_process_closed({"claimant_pid":mine["pid"],"claimant_creation_filetime":mine["creation_filetime"]})
    context=multiprocessing.get_context("spawn")
    parent,child=context.Pipe()
    process=context.Process(target=_identity_child,args=(child,))
    process.start();child.close()
    assert parent.poll(30),"native child identity was not returned within the test time budget"
    original=parent.recv()
    with pytest.raises(RuntimeError,match="remains active"):
        _budget_original_process_closed({"claimant_pid":original["pid"],"claimant_creation_filetime":original["creation_filetime"]})
    parent.send("exit");process.join(30)
    assert process.exitcode==0
    closed=_budget_original_process_closed({"claimant_pid":original["pid"],"claimant_creation_filetime":original["creation_filetime"]})
    path=tmp_path/"windows_native_process_closure.json"
    path.write_text(json.dumps({"rows":["BT-16"],"scope":"OS_PROCESS_IDENTITY_SUPPORT_ONLY",
        "original":original,"closed":closed,"physical":"UNMEASURED",
        "cursor_no_effect_authority":"NOT_GRANTED_BY_THIS_PROBE"},indent=2),encoding="utf-8")


# PREPARED_NOT_APPLIED_NOT_RUN. Real Windows spawned workers and the actual
# Supervisor/ArtifactStore/cursor protocol. All process waits are harness-only.
def _application_cut_child(connection, arguments, grant, cut):
    from bootstrap.supervisor_factory import SupervisorFactory
    from agent.supervisor_agent import _budget_native_process_identity
    physical=[]
    entries=[]
    class CutFactory(SupervisorFactory):
        def build(self, **kwargs):
            supervisor=super().build(**kwargs)
            engine=supervisor.workflow_engine
            invoker=engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
            invoke=invoker.invoke
            def measured(request, model_name):
                raw=request.model_dump(mode="json")
                physical.append({"model_call_id":request.model_call_id,
                    "request_sha256":canonical_json_sha256(raw),"extra":raw["extra"],
                    "caller_agent":request.caller_agent,"model_name":model_name})
                return invoke(request,model_name)
            invoker.invoke=measured
            resume=engine.resume_budget_document
            def counted(workflow,state,continuation):
                entries.append(continuation.model_dump(mode="json"))
                result=resume(workflow,state,continuation)
                if cut=="terminal_engine_exit":
                    native=engine.lifecycle_service.budget_preimage(state.run_id)
                    assert native.run.status.value=="success"
                    store=engine.stage_service.target_post_write_composition.citation_grounding.store
                    claim_refs=[ref for ref in store.list_references()
                        if ref.artifact_type=="budget_document_occurrence_claim" and ref.task_id==state.task_id and ref.run_id==state.run_id]
                    assert len(claim_refs)==1
                    claim_ref,claim=store.get_json_with_reference(claim_refs[0].artifact_ref,task_id=state.task_id,run_id=state.run_id)
                    inspection=engine.inspect(workflow,run_id=state.run_id)
                    assert inspection["next_nodes"]==[]
                    connection.send({"stage":"NATIVE_GRAPH_SUCCESS_BEFORE_APPLICATION_RESULT",
                        "process":_budget_native_process_identity(),"latest_native_claim":{
                            "reference":claim_ref.model_dump(mode="json"),"carrier":claim},
                        "native_run":native.run.model_dump(mode="json"),
                        "native_current_children":[row.model_dump(mode="json") for row in native.current_children],
                        "native_graph_inspection":inspection,"actual_engine_result":result.model_dump(mode="json"),
                        "native_budget":engine.budget_runtime(state.task_id,state.run_id,
                            require_existing_account=True).canonical_state().model_dump(mode="json"),
                        "physical":physical,"engine_entries":entries})
                    os._exit(63)
                return result
            engine.resume_budget_document=counted
            native_put=supervisor._budget_put
            def cut_after_native_put(store,ref,body,task,artifact_type):
                reference=native_put(store,ref,body,task,artifact_type)
                target=(artifact_type=="budget_document_occurrence_claim" and cut in {"claim_exit","claim_alive"}
                    or artifact_type=="budget_document_occurrence_result" and cut in {"result_exit","result_alive"})
                if target:
                    stored,actual=store.get_json_with_reference(ref,task_id=task.task_id,run_id=task.run_id)
                    assert stored==reference
                    connection.send({"stage":"NATIVE_CUT_COMMITTED","cut":cut,
                        "process":_budget_native_process_identity(),"reference":reference.model_dump(mode="json"),
                        "carrier":actual,"physical":list(physical),"engine_entries":list(entries)})
                    if cut in {"claim_exit","result_exit"}:
                        os._exit(61)  # Actual process crash after committed/fsynced native artifact.
                    raise RuntimeError("directed application cut after committed native claim")
                return reference
            supervisor._budget_put=cut_after_native_put
            return supervisor
    root=Path(arguments["output_root"])
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",
        supervisor_model_name="fake_llm",enable_llm_routing=False,
        trace_enabled=True,data_capture_enabled=True)
    try:
        MainlineApplicationService(supervisor_factory=CutFactory()).resume_budget_run(
            **{**arguments,"project_root":Path(arguments["project_root"]),"output_root":root},
            settings=settings,allow_demo_defaults=True,enable_agent_self_rag=False,
            accepted_grant_ref=grant["grant_ref"],accepted_grant_sha256=grant["grant_sha256"])
        connection.send({"stage":"UNEXPECTED_NORMAL_COMPLETION","physical":physical,"engine_entries":entries})
    except BaseException as exc:
        connection.send({"stage":"EXECUTION_IPC_RELEASED_CLAIMANT_ACTIVE", "error":type(exc).__name__+": "+str(exc),
            "process":_budget_native_process_identity(),"physical":physical,"engine_entries":entries})
        # The application returned, releasing its occurrence lock and closing
        # resources. Its native claimant process deliberately remains alive.
        if connection.poll(NATIVE_APPLICATION_TEST_WAIT_SECONDS):
            assert connection.recv()=="exit"
    finally:
        connection.close()


def _start_application_cut(case, approval, cut):
    context=multiprocessing.get_context("spawn")
    parent,child=context.Pipe()
    arguments={name:str(case.arguments[name]) for name in ("project_root","output_root","task_id","run_id")}
    process=context.Process(target=_application_cut_child,args=(child,arguments,approval,cut))
    process.start()
    case.test_workers.append(process)
    child.close()
    committed=_native_worker_message(parent,process,case,"native_application_"+cut)
    assert committed["stage"]=="NATIVE_CUT_COMMITTED"
    return process,parent,committed


def test_native_second_cold_adopter_tracks_latest_process_not_original_dead_pid(tmp_path):
    from agent.supervisor_agent import _budget_original_process_closed
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    first,first_pipe,cut0=_start_application_cut(case,approval,"claim_exit")
    _native_worker_join(first,case,"native_first_claim_cut",61)
    assert cut0["carrier"]["generation"]==0 and not cut0["physical"] and not cut0["engine_entries"]
    first_closed=_budget_original_process_closed(cut0["carrier"])
    first_pipe.close()
    second,second_pipe,cut1=_start_application_cut(case,approval,"claim_alive")
    try:
        released=_native_worker_message(second_pipe,second,case,"native_second_adopter_return")
        assert released["stage"]=="EXECUTION_IPC_RELEASED_CLAIMANT_ACTIVE"
        assert not released["physical"] and not released["engine_entries"]
        assert cut1["carrier"]["generation"]==1
        assert cut1["carrier"]["predecessor_claim_ref"]==cut0["reference"]["artifact_ref"]
        assert cut1["carrier"]["predecessor_claim_sha256"]==cut0["reference"]["content_hash"].removeprefix("sha256:")
        with pytest.raises(RuntimeError,match="remains active"):
            _budget_original_process_closed(cut1["carrier"])
        # This fresh call can acquire the actual released IPC lock. It must
        # still fail closed on the latest generation's live process identity.
        before=case.capture()
        with pytest.raises(RuntimeError,match="remains active"):
            case.resume(approval)
        with ThreadPoolExecutor(max_workers=2) as executor:
            def blocked(_index):
                with pytest.raises(RuntimeError,match="remains active"):
                    case.resume(approval)
            list(executor.map(blocked,range(2)))
        blocked_snapshot=case.capture()
        assert blocked_snapshot["budget"]==before["budget"]
        assert blocked_snapshot["run"]==before["run"]
        assert blocked_snapshot["current_children"]==before["current_children"]
        assert blocked_snapshot["physical_count"]==before["physical_count"]
        assert blocked_snapshot["engine_entry_count"]==before["engine_entry_count"]==0
    finally:
        if second.is_alive(): second_pipe.send("exit")
        _native_worker_join(second,case,"native_second_adopter_exit",0)
        second_pipe.close()
    assert second.exitcode==0
    second_closed=_budget_original_process_closed(cut1["carrier"])
    resumed=case.resume(approval)["budget_acknowledgement"]
    assert resumed["decision"]=="ACCEPTED" and resumed["claim_ref"].endswith("/claims/2")
    claim0=case.store.get_json_with_reference(cut0["reference"]["artifact_ref"],task_id=case.summary["task_id"],run_id=case.summary["run_id"])
    claim1=case.store.get_json_with_reference(cut1["reference"]["artifact_ref"],task_id=case.summary["task_id"],run_id=case.summary["run_id"])
    claim2=case.store.get_json_with_reference(resumed["claim_ref"],task_id=case.summary["task_id"],run_id=case.summary["run_id"])
    assert claim0[0].model_dump(mode="json")==cut0["reference"] and claim0[1]==cut0["carrier"]
    assert claim1[0].model_dump(mode="json")==cut1["reference"] and claim1[1]==cut1["carrier"]
    assert claim2[1]["generation"]==2 and claim2[1]["predecessor_claim_ref"]==claim1[0].artifact_ref
    assert claim2[1]["predecessor_claim_sha256"]==claim1[0].content_hash.removeprefix("sha256:")
    after=case.receipt(["BT-08","BT-16"],"second_adopter_native_chain",acknowledgements=[approval,resumed],
        observations={"cut0":cut0,"cut1":cut1,"released":released,"first_positive_closure":first_closed,
            "second_positive_closure":second_closed,"blocked_snapshot":blocked_snapshot,
            "claim2":{"reference":claim2[0].model_dump(mode="json"),"carrier":claim2[1]}})
    assert after["engine_entry_count"]==1 and after["run"]["status"]=="success"


@pytest.mark.parametrize("cut_mode",["result_exit","result_alive"])
def test_native_result_cut_cold_reads_completed_without_second_engine_or_physical_dispatch(tmp_path,cut_mode):
    from agent.supervisor_agent import _budget_original_process_closed
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    process,pipe,cut=_start_application_cut(case,approval,cut_mode)
    active_read=None
    if cut_mode=="result_alive":
        try:
            released=_native_worker_message(pipe,process,case,"native_result_return")
            assert released["stage"]=="EXECUTION_IPC_RELEASED_CLAIMANT_ACTIVE"
            before_active=case.capture()
            usage_bytes=RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).model_usage_path.read_bytes()
            active_read=case.resume(approval)["budget_acknowledgement"]
            assert active_read["read_only"] is True
            assert active_read["disposition"]=="NATIVE_RESULT_RECORDED_CLAIMANT_ACTIVE"
            after_active=case.capture()
            for name in ("run","current_children","budget","task_state"):
                assert after_active[name]==before_active[name]
            assert after_active["physical_count"]==before_active["physical_count"]
            assert after_active["engine_entry_count"]==before_active["engine_entry_count"]==0
            assert RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).model_usage_path.read_bytes()==usage_bytes
        finally:
            if process.is_alive(): pipe.send("exit")
            _native_worker_join(process,case,"native_result_alive_exit",0)
            pipe.close()
        assert process.exitcode==0
    else:
        _native_worker_join(process,case,"native_result_crash_exit",61)
        pipe.close()
    result_ref=cut["reference"]["artifact_ref"]
    recorded=case.store.get_json_with_reference(result_ref,task_id=case.summary["task_id"],run_id=case.summary["run_id"])
    producing_claim=case.store.get_json(recorded[1]["claim_ref"],task_id=case.summary["task_id"],run_id=case.summary["run_id"])
    positive_closure=_budget_original_process_closed(producing_claim)
    assert len(cut["engine_entries"])==1
    prefix="citation-association-call://sha256/"+case.wait.refusal.semantic_input_sha256+"/admission/"
    assert len([item for item in cut["physical"] if item["extra"].get("logical_operation_id","").startswith(prefix)])==1
    before_replay=case.capture()
    ack=case.resume(approval)["budget_acknowledgement"]
    assert ack["agent_result_ref"]==result_ref
    assert ack["agent_result_sha256"]==recorded[0].content_hash.removeprefix("sha256:")
    replay=case.resume(approval)["budget_acknowledgement"]
    assert replay==ack
    after=case.receipt(["BT-06","BT-16","BT-17","BT-21"],"native_result_cut_"+cut_mode,acknowledgements=[approval,ack,replay],
        observations={"cut":cut,"active_passive_read":active_read,"positive_closure":positive_closure,
            "cold_before":before_replay,"child_physical_delta":len(cut["physical"]),
            "child_engine_entry_delta":len(cut["engine_entries"]),"parent_scope":"COLD_REPLAY_ONLY"})
    assert after["physical_count"]==before_replay["physical_count"]
    assert after["engine_entry_count"]==before_replay["engine_entry_count"]==0
    assert after["budget"]==before_replay["budget"]
    assert after["model_usage"]==recorded[1]["agent_result"]["result"]["model_usage"]


# PREPARED_NOT_APPLIED_NOT_RUN. Frozen cancellation/no-authority preservation.
def test_native_supervisor_ordinary_recovery_rejects_budget_before_failure_projection(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    runtime=case.open_runtime()
    try:
        with pytest.raises(ValueError,match="Budget authorization"):
            runtime.supervisor.recover(runtime.task,recovery_attempt=1,
                recovery_reason="local no-authority negative control",recovery_policy="existing-native-policy")
    finally:
        case.close_runtime(runtime)
    after=case.receipt(["BT-29","BT-26"],"ordinary_recovery_budget_rejection")
    for name in ("run","current_children","budget","task_state"):
        assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


def test_native_approval_then_explicit_cancel_keeps_grant_but_forbids_document_dispatch(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    cancelled=case.service.cancel_run(**case.arguments,reason="explicit native cancellation")
    control=case.capture()
    assert control["run"]["status"]=="cancelled"
    assert control["task_state"]["status"]=="cancelled"
    with pytest.raises(ValueError):
        case.resume(approval)
    after=case.receipt(["BT-10","BT-26"],"grant_then_native_cancel",acknowledgements=[approval],
        observations={"explicit_cancellation":cancelled,"cancelled_control":control})
    for name in ("run","current_children","budget","task_state"):
        assert after[name]==control[name]
    assert len(after["budget"]["grant_history"])==1
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


# PREPARED_NOT_APPLIED_NOT_RUN. Existing positive packet/native PAUSE replay;
# parent Graph cuts separately certify the positive pre-PAUSE crash boundary.
def test_native_trusted_wait_recovery_exact_replay_never_grants_or_dispatches(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    result=case.service.recover_budget_pause_from_wait_packet(**case.arguments,
        wait_ref=case.pause.wait_ref,wait_sha256=case.pause.wait_sha256)
    replay=case.service.recover_budget_pause_from_wait_packet(**case.arguments,
        wait_ref=case.pause.wait_ref,wait_sha256=case.pause.wait_sha256)
    assert result==replay and result["status"]=="paused"
    with pytest.raises(ValueError):
        case.service.recover_budget_pause_from_wait_packet(**case.arguments,
            wait_ref=case.pause.wait_ref,wait_sha256="0"*64)
    with pytest.raises(ValueError):
        case.service.recover_budget_pause_from_wait_packet(**case.arguments,
            wait_ref=case.wait.refusal.native_denial_ref,wait_sha256=case.wait.refusal.native_denial_sha256)
    after=case.receipt(["BT-15"],"exact_wait_recovery",observations={"result":result,"replay":replay,
        "boundary":"EXISTING_NATIVE_PAUSE_ONLY_PRE_PAUSE_OS_BOUNDARY_SEPARATE"})
    for name in ("run","current_children","budget","task_state"):
        assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


# PREPARED_NOT_APPLIED_NOT_RUN. Additional test helpers only; no new production
# owner. Explicit test-owned resource preimage is recorded apart from real model
# charging; native Gateway closure is still required for every new refusal.
def _native_wait(case):
    runtime=case.open_runtime()
    try:
        engine=runtime.supervisor.workflow_engine
        pause=engine.lifecycle_service.current_budget_transition(runtime.task.run_id)
        packet=engine.resolve_budget_wait(runtime.workflow,run_id=runtime.task.run_id)
        budget=engine.budget_runtime(runtime.task.task_id,runtime.task.run_id,
            require_existing_account=True).canonical_state()
        reference,failure=engine.stage_service.target_post_write_composition.citation_grounding.store.get_json_with_reference(
            packet.refusal.native_denial_ref,task_id=runtime.task.task_id,run_id=runtime.task.run_id,
            section_id=packet.refusal.section_id)
        assert reference.content_hash=="sha256:"+packet.refusal.native_denial_sha256
        required=failure["failure"]["phase_proof"]["native_denial"]["requested"]["model_tokens_total"]
        return packet,pause,budget,required
    finally:
        case.close_runtime(runtime)


def _native_pending_command(case, *, approval_id,authorization_id,tokens,
                            authorization_kind="SINGLE_USE",authorized_delta=None,cumulative_ceiling=None):
    packet,pause,state,required=_native_wait(case)
    if cumulative_ceiling is None:
        cumulative_ceiling=state.effective_limits.model_dump(mode="json")
        cumulative_ceiling["model_tokens_total"]+=tokens
    return case.command(approval_id=approval_id,authorization_id=authorization_id,tokens=tokens,
        authorization_kind=authorization_kind,authorized_delta=authorized_delta,
        cumulative_ceiling=cumulative_ceiling,wait_event_ref=pause.event_ref,wait_event_sha256=pause.event_sha256)


def _native_test_token_charge(engine, amount, suffix):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import BudgetReservationRefSchema,CanonicalBudgetVectorSchema
    identity=engine.lifecycle_service.load_run_identity(engine._bt_fixture_run_id)
    engine.lifecycle_service.assert_run_committable(identity.run_id)
    manager=engine.budget_runtime(identity.task_id,identity.run_id,require_existing_account=True)
    before=manager.canonical_state()
    ref=BudgetReservationRefSchema(reservation_id="budget:test-owned-resource:"+suffix,
        task_id=identity.task_id,run_id=identity.run_id,reservation_kind="local_test_owned_resource_preimage",
        owner_occurrence_ref="fixture://explicit-local-resource-charge/"+suffix,
        scope_ref="run:"+identity.run_id,operation_ref="fixture://explicit-local-resource-charge/"+suffix)
    vector=CanonicalBudgetVectorSchema(model_tokens_total=amount)
    reservation=manager.reserve(ref,vector,expected_revision=before.revision)
    settlement=manager.consume(ref.reservation_id,vector,expected_revision=manager.canonical_state().revision)
    return {"scope":"TEST_OWNED_NATIVE_RESOURCE_PREIMAGE_NOT_PROVIDER_BILLING",
        "before":before.model_dump(mode="json"),"after":manager.canonical_state().model_dump(mode="json"),
        "reservation":reservation.model_dump(mode="json"),"settlement":settlement.model_dump(mode="json")}


def _prephysical_charge_hook(charges, *, initial=False, max_new_refusals=0):
    from test_multi_agent_v3_citation_grounding_mainline import _association_input
    def configure(engine, case):
        owner=engine.stage_service.target_post_write_composition.citation_grounding
        generate=owner.gateway.generate
        def controlled(request, **options):
            if request.extra.get("call_purpose")=="citation_association":
                material=_association_input(request)
                if material["visible_artifact"]["payload_kind"]=="document":
                    engine._bt_fixture_run_id=request.run_id
                    admission="/admission/" in request.extra.get("logical_operation_id","")
                    should_charge=(initial and not admission and not charges
                        or admission and len([r for r in charges if r["new_admission"]])<max_new_refusals)
                    if should_charge:
                        manager=engine.budget_runtime(request.task_id,request.run_id,require_existing_account=True)
                        amount=manager.canonical_state().remaining.model_tokens_total
                        assert amount>0
                        charged=_native_test_token_charge(engine,amount,"native-before-first-token-"+uuid4().hex)
                        charged.update(new_admission=admission,request=request.model_dump(mode="json"),
                            physical_before=len(case.physical),source_epoch_paths=sorted(engine._budget_source_epoch()))
                        charges.append(charged)
            return generate(request,**options)
        owner.gateway.generate=controlled
    return configure


def _native_old_owner_operation_rows(before,after):
    old_physical=before["physical_requests"]
    new_physical=after["physical_requests"][len(old_physical):]
    before_reservations={row["reservation_ref"]["reservation_id"]:row for row in before["budget"]["reservations"]}
    after_reservations={row["reservation_ref"]["reservation_id"]:row for row in after["budget"]["reservations"]}
    rows=[]
    for physical in old_physical:
        operation=physical["extra"].get("logical_operation_id")
        model_call=physical["model_call_id"]
        reservations=[row for row in before_reservations.values()
            if row["reservation_ref"]["operation_ref"] in {operation,model_call}
                or row["reservation_ref"]["owner_occurrence_ref"] in {operation,model_call}
                or model_call in row["reservation_ref"]["reservation_id"]]
        if not reservations:
            continue  # The final recorder must not label unidentified charging PASS.
        changed=[row["reservation_ref"]["reservation_id"] for row in reservations
            if after_reservations.get(row["reservation_ref"]["reservation_id"])!=row]
        repeated=[row for row in new_physical
            if row["model_call_id"]==model_call or operation is not None and row["extra"].get("logical_operation_id")==operation]
        rows.append({"caller_agent":physical["caller_agent"],"model_call_id":model_call,
            "logical_operation_id":operation,"before_physical":physical,"native_old_reservations":reservations,
            "new_matching_physical_entries":repeated,"changed_native_accounting_reservation_ids":changed})
    return rows


# PREPARED_NOT_APPLIED_NOT_RUN. Authored offline producer oracles for a fresh
# eight-Section native fixture. All outputs pass ordinary model/cognition owners.
def _bt03_eight_source_run(root, *, run_id, mutate_engine):
    from test_phase2_runtime_safety_closure import _InspectingSupervisorFactory
    import test_multi_agent_v3_workflow_recovery as workflow_tests
    from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
    task_id="bt-eight-"+uuid4().hex
    raw=workflow_tests._w13_current_new_task_project_input(
        workflow_tests._w13_single_section_project_input(task_id)).model_dump(mode="json")
    names=["BT actual section "+str(index+1) for index in range(8)]
    raw["generation_requirements"].update(required_sections=names,min_section_chars=40,
        need_citation=True,citation_required_sections=names,need_human_review=False)
    raw["output_schema"].update(required_sections=names,document_title="BT native eight-Section document")
    project=ProjectInputSchema.model_validate(raw)
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",
        supervisor_model_name="fake_llm",enable_llm_routing=False,
        trace_enabled=True,data_capture_enabled=True)
    factory=_InspectingSupervisorFactory(mutate_engine)
    summary=MainlineApplicationService(supervisor_factory=factory).run(
        project_root=Path(__file__).resolve().parents[2],task_id=task_id,run_id=run_id,
        user_input=project.user_query,project_input=project.model_dump(mode="json"),
        output_root=root,clean_existing=True,settings=settings,allow_demo_defaults=True,
        enable_agent_self_rag=False)
    return summary,factory


def _bt03_dedicated_source_run(root, *, run_id, mutate_engine):
    from test_phase2_runtime_safety_closure import _InspectingSupervisorFactory
    import test_multi_agent_v3_workflow_recovery as workflow_tests
    from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
    task_id="bt-eight-"+uuid4().hex
    raw=workflow_tests._w13_current_new_task_project_input(
        workflow_tests._w13_single_section_project_input(task_id)).model_dump(mode="json")
    # Only BT03/21 use this fresh one-Section scope; default BT01 stays8.
    raw["user_query"]="BT native construction document"
    names=["BT"]
    raw["generation_requirements"].update(required_sections=names,min_section_chars=40,
        need_citation=True,citation_required_sections=names,need_human_review=False)
    raw["output_schema"].update(required_sections=names,document_title="BT native dedicated document")
    project=ProjectInputSchema.model_validate(raw)
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",
        supervisor_model_name="fake_llm",enable_llm_routing=False,
        trace_enabled=True,data_capture_enabled=True)
    factory=_InspectingSupervisorFactory(mutate_engine)
    summary=MainlineApplicationService(supervisor_factory=factory).run(
        project_root=Path(__file__).resolve().parents[2],task_id=task_id,run_id=run_id,
        user_input=project.user_query,project_input=project.model_dump(mode="json"),
        output_root=root,clean_existing=True,settings=settings,allow_demo_defaults=True,
        enable_agent_self_rag=False)
    return summary,factory


def _bt03_three_source_producer(original_rag,upstream_observations=None):
    from contracts.evidence import build_evidence_bundle,project_evidence_bundle
    from schemas.rag import RAGToolOutputSchema
    from unittest.mock import patch
    def produce(tool,call):
        with patch.dict(os.environ,{"FAKE_RAG_SCENARIO":"citation_collision"}):
            # Short fresh source identity labels; retain exact parent material.
            source_key=hashlib.sha256(call.tool_call_id.encode("utf-8")).hexdigest()[:12]
            child_calls=[call.model_copy(update={"tool_call_id":"bt3-"+source_key+"-"+str(index)})
                for index in range(3)]
            responses=[original_rag(tool,child) for child in child_calls]
        outputs=[RAGToolOutputSchema.model_validate(row.result) for row in responses]
        assert len({output.citations[0].doc_id for output in outputs})==3
        assert len({output.citations[0].child_chunk_id for output in outputs})==3
        assert len({output.citations[0].quote_text for output in outputs})==1
        assert len({output.retrieved_chunks[0].context_text for output in outputs})==1
        citations=[output.citations[0].model_copy(update={"citation_id":"C"+str(index+1),
            "original_citation_id":"C"+str(index+1)}) for index,output in enumerate(outputs)]
        evidence=build_evidence_bundle(query=outputs[0].query,rewritten_queries=[],
            selected_chunks=[output.retrieved_chunks[0] for output in outputs],dropped_chunks=[],
            citations=citations,trace=outputs[0].trace,max_context_chars=12000,extra=outputs[0].evidence.extra)
        evidence.assessment=outputs[0].evidence.assessment
        evidence.item_assessments=[output.evidence.item_assessments[0].model_copy(
            update={"evidence_id":evidence.selected_evidence_ids[index]}) for index,output in enumerate(outputs)]
        context,chunks,projected=project_evidence_bundle(evidence)
        if upstream_observations is not None:
            upstream_observations.append({"parent_actual_call":call.model_dump(mode="json"),
                "actual_child_calls":[child.model_dump(mode="json") for child in child_calls],
                "actual_native_fake_outputs":[response.model_dump(mode="json") for response in responses],
                "native_bundle":evidence.model_dump(mode="json"),
                "source_identity_scope":"FRESH_AUTHORED_UPSTREAM_ID_LABEL_ONLY"})
        native=outputs[0].model_copy(update={"evidence":evidence,"context":context,
            "retrieved_chunks":chunks,"citations":projected})
        return responses[0].model_copy(update={"tool_call_id":call.tool_call_id,"result":native.model_dump(mode="json")})
    return produce


def _bt03_model_semantic_producer(original_generate,validity_observations=None,validity_root=None):
    from core.hashing import canonical_sha256
    from test_multi_agent_v3_citation_grounding_mainline import _association_input,TEXT
    import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
    import test_multi_agent_v3_claim_support_mainline as support_tests
    def produce(client,request):
        response=original_generate(client,request)
        contract=request.extra.get("response_contract")
        if request.extra.get("call_purpose")=="scheme_section_generation":
            content=TEXT*5+"[C1][C2]"
        elif contract==extraction_tests.CONTRACT:
            units=extraction_tests._task_contract(request)["coverage_units"]
            content=json.dumps({"units":[{"unit_id":unit["unit_id"],
                "accounting_state":"FACTUAL_CLAIMS_MATERIALIZED",
                "claims":[{"quote":TEXT,"occurrence_index":index,"claim_type":"general_method"}
                    for index in range(2)]} for unit in units]},ensure_ascii=False)
        elif contract==support_tests.SUPPORT_RESPONSE_CONTRACT:
            refs=[row["exact_source_ref"] for row in support_tests._json_message(request,"support_evidence")["support_evidence"]]
            assert refs
            content=json.dumps({"relation":"SUPPORTED","supporting_refs":refs[:1],"contradicting_refs":[],
                "certainty_relation":"ALIGNED","certainty_reason_codes":["TEST_EXPLICIT_CERTAINTY_ALIGNED"]})
        elif contract=="canonical_claim_verifier_v2":
            # Explicit independent authored method-validity oracle. Support is
            # only provenance admission; it does not itself decide validity.
            task=support_tests._json_message(request,"shared_support")
            paragraph=support_tests._json_message(request,"paragraph_text")
            evidence=support_tests._json_message(request,"authorized_evidence")["authorized_evidence"]
            claim=paragraph["claim"]; span=claim["text_span"]; shared=task["shared_support"]
            assert claim["claim_ref"]==task["verification_target"]
            assert claim["claim_type"]=="general_method" and claim["normalized_value"]==TEXT
            assert span["text"]==TEXT and paragraph["paragraph_text"][span["start_offset"]:span["end_offset"]]==TEXT
            assert shared["relation"]=="SUPPORTED" and not shared["contradicting_refs"]
            allowed=set(task["allowed_authority_refs"])
            admitted=set(task["admissible_positive_basis_refs"])
            basis=sorted(set(shared["supporting_refs"]) & allowed & admitted)
            assert basis
            sources={row["exact_source_ref"]:row for row in evidence}
            assert all(ref in sources for ref in basis)
            # The test independently fixes the normative construction-method
            # premise to this exact native source body, beyond Support status.
            expected_context_sha256="b03617cee194082b3995cbc6133b91a7334e9060e6a27f51b63872ebb16c798b"
            for ref in basis:
                material=sources[ref]; assessment=material["assessment"]
                assert hashlib.sha256(material["context_text"].encode("utf-8")).hexdigest()==expected_context_sha256
                assert material["context_text_sha256"]==canonical_sha256(material["context_text"])
                assert assessment["eligibility"] and assessment["project_relation"]=="current_project"
                assert claim["claim_type"] in assessment["supported_claim_types"]
            candidate={"verdict":"VERIFIED","positive_basis_refs":basis,"issues":[],
                "rationale":"Authored independent normative-method validity oracle over exact native paragraph and construction requirement body."}
            content=json.dumps(candidate,ensure_ascii=False)
            observed={"scope":"TEST_OWNED_INDEPENDENT_NORMATIVE_METHOD_VALIDITY_ORACLE",
                "claim_ref":claim["claim_ref"],"actual_claim_span":span,
                "shared_support":shared,"allowed_authority_refs":sorted(allowed),
                "admissible_positive_basis_refs":sorted(admitted),"positive_basis_refs":basis,
                "native_exact_source_bodies":[sources[ref] for ref in basis],
                "expected_context_sha256":expected_context_sha256,
                "actual_request":request.model_dump(mode="json"),
                "actual_response":response.model_copy(update={"content":content},deep=True).model_dump(mode="json")}
            if validity_observations is not None:
                validity_observations.append(observed)
            if validity_root is not None:
                validity_root.mkdir(parents=True,exist_ok=True)
                identity=hashlib.sha256((request.model_call_id+str(request.extra.get("provider_attempt_id"))).encode("utf-8")).hexdigest()
                path=validity_root/(identity+".json")
                with path.open("x",encoding="utf-8") as stream:
                    json.dump(observed,stream,ensure_ascii=False,indent=2)
        elif request.extra.get("call_purpose")=="citation_association":
            material=_association_input(request)
            ids=[row["claim_target_id"] for row in material["candidate_claim_targets"]]
            refs=material["eligible_occurrence_refs"]
            assert ids and refs
            document=material["visible_artifact"]["payload_kind"]=="document"
            content=json.dumps({"associations":[{"occurrence_ref":ref,"state":"ASSOCIATED",
                "associated_claim_target_ids":[ids[0] if document else ids[index%len(ids)]]}
                for index,ref in enumerate(refs)]})
        else:
            return response
        return response.model_copy(update={"content":content,"metadata":{
            **response.metadata,"test_producer_oracle":"BT03_NATIVE_THREE_EXACT_SOURCES_TWO_CLAIMS"}},deep=True)
    return produce


def test_native_exact_association_allowance_holds_pending_document_support_then_second_finite_authorization(tmp_path):
    from unittest.mock import patch
    from model_gateway.fake_llm_client import FakeLLMClient
    from tools.fake_rag_tool import FakeRAGTool
    from schemas.model import TokenUsageSchema
    charges=[]; response_charges=[]; exact_allowance=[]; upstream_observations=[]; validity_observations=[]
    grounding_observations=[]
    initial_charge=_prephysical_charge_hook(charges,initial=True)
    def configure(engine,case):
        initial_charge(engine,case)
        invoker=engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
        invoke=invoker.invoke
        def report_exact_approved_allowance(request,model_name):
            response=invoke(request,model_name)
            if (request.extra.get("call_purpose")=="citation_association"
                    and "/admission/" in request.extra.get("logical_operation_id","") and not response_charges):
                assert len(exact_allowance)==1
                allowance=exact_allowance[0]
                response=response.model_copy(update={"token_usage":TokenUsageSchema(
                    prompt_tokens=allowance,completion_tokens=0,total_tokens=allowance)},deep=True)
                response_charges.append({"scope":"EXPLICIT_FAKE_PROVIDER_REPORTED_CURRENT_ALLOWANCE",
                    "request":request.model_dump(mode="json"),"response":response.model_dump(mode="json")})
            return response
        invoker.invoke=report_exact_approved_allowance
        _native_grounding_commit_observer(engine,case,grounding_observations)
    with patch.object(FakeRAGTool,"run",_bt03_three_source_producer(FakeRAGTool.run,upstream_observations)), \
            patch.object(FakeLLMClient,"generate",_bt03_model_semantic_producer(FakeLLMClient.generate,validity_observations,tmp_path/"native_validity_oracle")):
        case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure,run_builder=_bt03_dedicated_source_run,expected_current_child_count=1,
            native_run_id="bt3_"+uuid4().hex[:12])
        packet1,pause1,state1,required1=_native_wait(case)
        assert len(packet1.current_children)==1 and state1.limits.model_tokens_total==24000
        assert state1.remaining.model_tokens_total==0 and len(charges)==1
        allowance=max(1,required1-state1.remaining.model_tokens_total)
        exact_allowance.append(required1)
        first=case.approve(_native_pending_command(case,approval_id="bt03-only-current-allowance",
            authorization_id="bt03-first-explicit-finite",tokens=allowance))["budget_acknowledgement"]
        first_result=case.resume(first)
        packet2,pause2,state2,required2=_native_wait(case)
        assert packet2.refusal.operation_kind=="DOCUMENT_SUPPORT"
        assert packet2.refusal.native_denial_ref!=packet1.refusal.native_denial_ref
        assert first_result["canonical_budget_state"]["run"]["status"]=="paused"
        assert packet2.current_children==packet1.current_children
        assert state2.remaining.model_tokens_total==0 and len(response_charges)==1
        after_first=case.capture()
        first_new=after_first["physical_requests"][case.initial_physical_count:]
        assert len(first_new)==1 and first_new[0]["caller_agent"]=="CitationClaimAssociation"
        second=case.approve(_native_pending_command(case,approval_id="bt03-second-support-only",
            authorization_id="bt03-second-explicit-finite",tokens=required2))["budget_acknowledgement"]
        second_result=case.resume(second)
        after=case.receipt(["BT-03","BT-21"],"bt03_actual_graph_second_finite",acknowledgements=[first,second],
            observations={"first_wait":packet1.model_dump(mode="json"),"first_pause":pause1.event_ref,
                "second_wait":packet2.model_dump(mode="json"),"second_pause":pause2.event_ref,
                "test_owned_resource_charges":charges,"explicit_provider_reported_allowance":response_charges,
                "native_upstream_source_observations":upstream_observations,
                "native_verifier_validity_oracle_observations":validity_observations,
                "after_first_allowance":after_first,"first_result":first_result,"second_result":second_result,
                "native_grounding_commit_observations":_native_observation_wire(grounding_observations)})
        old_rows=_native_old_owner_operation_rows(case.before,after)
        assert old_rows and all(not row["new_matching_physical_entries"]
            and not row["changed_native_accounting_reservation_ids"] for row in old_rows)
        new_calls=after["physical_requests"][case.initial_physical_count:]
        assert sum(row["caller_agent"]=="CitationClaimAssociation" for row in new_calls)==1
        assert any(row["caller_agent"]=="ClaimSupportEvaluator" for row in new_calls)
        assert len(after["budget"]["grant_history"])==2 and after["budget"]["limits"]["model_tokens_total"]==24000
        # A second grant never manufactures completion if another finite pending
        # item remains. Its actual native outcome is recorded without flattening.
        assert after["run"]["status"] in {"paused","success"}
        if after["run"]["status"]=="paused":
            later,_,_,_=_native_wait(case)
            assert later.refusal.phase_proof_sha256

        _native_grounding_commit_oracles(grounding_observations,"DOCUMENT_SUPPORT",root=case.root,allow_unreached=True)

# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. Fresh native fixtures only; every
# counter below is derived from actual invoker and canonical reservation rows.
def test_native_pre_authorized_exact_operation_repeated_refusals_exhaust_finite_root(tmp_path):
    charges=[]
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=_prephysical_charge_hook(
        charges,initial=True,max_new_refusals=2))
    packet0,pause0,state0,required0=_native_wait(case)
    assert state0.limits.model_tokens_total==24000 and state0.remaining.model_tokens_total==0
    cap=required0*2
    ceiling=state0.limits.model_dump(mode="json")
    ceiling["model_tokens_total"]+=cap
    def command(client,tokens):
        return _native_pending_command(case,approval_id=client,authorization_id="bt05-exact-native-root",
            tokens=tokens,authorization_kind="PREAUTHORIZED",authorized_delta={"model_tokens_total":cap},
            cumulative_ceiling=ceiling)
    grant0=case.approve(command("root-debit-0",required0))["budget_acknowledgement"]
    outcome0=case.resume(grant0)
    packet1,pause1,state1,required1=_native_wait(case)
    assert packet1.refusal.semantic_operation_ref==packet0.refusal.semantic_operation_ref
    assert packet1.refusal.semantic_input_sha256==packet0.refusal.semantic_input_sha256
    assert packet1.refusal.artifact_version==packet0.refusal.artifact_version
    assert required1==required0 and pause1.event_ref!=pause0.event_ref
    grant1=case.approve(command("root-debit-1",required1))["budget_acknowledgement"]
    alias=case.approve(command("root-debit-1-other-client",required1))["budget_acknowledgement"]
    assert alias["grant_ref"]==grant1["grant_ref"] and alias["grant_sha256"]==grant1["grant_sha256"]
    outcome1=case.resume(grant1)
    packet2,pause2,state2,required2=_native_wait(case)
    assert packet2.refusal.semantic_operation_ref==packet0.refusal.semantic_operation_ref
    assert packet2.refusal.semantic_input_sha256==packet0.refusal.semantic_input_sha256
    assert packet2.refusal.artifact_version==packet0.refusal.artifact_version
    assert len(state2.grant_history)==2 and state2.remaining.model_tokens_total==0
    exhausted=case.approve(command("root-debit-2",required2))["budget_acknowledgement"]
    exhausted_alias=case.approve(command("root-debit-2-other-client",required2))["budget_acknowledgement"]
    assert exhausted["decision"]==exhausted_alias["decision"]=="LIMIT_REACHED"
    after=case.receipt(["BT-05","BT-07","BT-21"],"native_exact_preauth_root_cap",
        acknowledgements=[grant0,grant1,alias,exhausted,exhausted_alias],observations={
            "native_waits":[row.model_dump(mode="json") for row in (packet0,packet1,packet2)],
            "native_wait_events":[pause0.event_ref,pause1.event_ref,pause2.event_ref],
            "actual_outcomes":[outcome0,outcome1],"test_owned_native_resource_charges":charges,
            "finite_root_cap":cap,"root_scope":"ONE_EXACT_CITATION_OPERATION_INPUT_ARTIFACT"})
    assert len(after["budget"]["grant_history"])==2
    grants=after["budget"]["grant_history"]
    assert len({row["authorization"]["authorization_ref"] for row in grants})==1
    assert sum(row["request"]["delta"]["model_tokens_total"] for row in grants)==cap
    assert len({row["request"]["wait_event_ref"] for row in grants})==2
    assert len(charges)==3 and len([row for row in charges if row["new_admission"]])==2
    assert after["physical_count"]==case.before["physical_count"]
    assert after["current_children"]==case.before["current_children"] and after["run"]["status"]=="paused"
    rows=_native_old_owner_operation_rows(case.before,after)
    assert rows and all(not row["new_matching_physical_entries"]
        and not row["changed_native_accounting_reservation_ids"] for row in rows)


def _application_concurrent_child(connection,arguments,command_wire,start_event):
    from bootstrap.supervisor_factory import SupervisorFactory
    from agent.supervisor_agent import BudgetTopupCommandSchema,_budget_native_process_identity
    physical=[]; entries=[]
    class MeasuredFactory(SupervisorFactory):
        def build(self,**kwargs):
            supervisor=super().build(**kwargs)
            engine=supervisor.workflow_engine
            invoker=engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
            invoke=invoker.invoke
            def measured(request,model_name):
                raw=request.model_dump(mode="json")
                physical.append({"model_call_id":request.model_call_id,"request_sha256":canonical_json_sha256(raw),
                    "caller_agent":request.caller_agent,"model_name":model_name,"extra":raw["extra"]})
                return invoke(request,model_name)
            invoker.invoke=measured
            resume=engine.resume_budget_document
            def counted(workflow,state,continuation):
                entries.append(continuation.model_dump(mode="json"))
                return resume(workflow,state,continuation)
            engine.resume_budget_document=counted
            return supervisor
    root=Path(arguments["output_root"])
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",supervisor_model_name="fake_llm",
        enable_llm_routing=False,trace_enabled=True,data_capture_enabled=True)
    identity=_budget_native_process_identity()
    connection.send({"stage":"READY","process":identity})
    if not start_event.wait(NATIVE_APPLICATION_TEST_WAIT_SECONDS):
        connection.send({"stage":"TEST_TIME_BUDGET_EXHAUSTED","process":identity,
            "production_failure":False,"physical":physical,"engine_entries":entries})
        connection.close(); return
    try:
        service=MainlineApplicationService(supervisor_factory=MeasuredFactory())
        result=service.topup_budget_run(**{**arguments,"project_root":Path(arguments["project_root"]),
            "output_root":root},settings=settings,allow_demo_defaults=True,enable_agent_self_rag=False,
            command=BudgetTopupCommandSchema.model_validate(command_wire),continue_document=True)
        connection.send({"stage":"COMPLETE","process":identity,"result":result,
            "physical":physical,"engine_entries":entries})
    except BaseException as exc:
        connection.send({"stage":"ACTUAL_EXCEPTION","process":identity,"error":type(exc).__name__+": "+str(exc),
            "physical":physical,"engine_entries":entries})
    finally:
        connection.close()


def test_native_two_windows_processes_client_alias_approve_resume_once(tmp_path):
    assert os.name=="nt"
    case=_NativeApplicationCase(tmp_path/"native")
    context=multiprocessing.get_context("spawn")
    start=context.Event(); workers=[]; readiness=[]; completed=[]
    arguments={name:str(case.arguments[name]) for name in ("project_root","output_root","task_id","run_id")}
    for index in range(2):
        parent,child=context.Pipe()
        wire=case.command(approval_id="native-process-alias-"+str(index)).model_dump(mode="json")
        worker=context.Process(target=_application_concurrent_child,args=(child,arguments,wire,start))
        worker.start();child.close();workers.append((worker,parent));case.test_workers.append(worker)
    for worker,pipe in workers:
        ready=_native_worker_message(pipe,worker,case,"native_concurrent_worker_ready")
        assert ready["stage"]=="READY";readiness.append(ready)
    assert len({row["process"]["pid"] for row in readiness})==2
    start.set()
    for worker,pipe in workers:
        result=_native_worker_message(pipe,worker,case,"native_concurrent_worker_result");completed.append(result)
        _native_worker_join(worker,case,"native_concurrent_worker_exit",0);pipe.close()
    case.receipt(["BT-06","BT-07","BT-08","BT-21"],"native_windows_process_alias_pre_oracle",
        observations={"native_process_ready":readiness,"native_process_results":completed,
            "actual_child_invoker_entries":[entry for row in completed for entry in row.get("physical",[])],
            "actual_child_engine_entries":[entry for row in completed for entry in row.get("engine_entries",[])],
            "actual_worker_exit_codes":[worker.exitcode for worker,_ in workers],
            "scope":"ACTUAL_TWO_WINDOWS_PROCESSES_BEFORE_ANY_RESULT_OR_COUNTER_ORACLE"})
    assert all(row["stage"]=="COMPLETE" for row in completed),completed
    acks=[row["result"]["budget_acknowledgement"] for row in completed]
    assert all(row["schema_version"]=="budget_document_occurrence_ack_v1" for row in acks)
    identities=[row["identity"] for row in acks]
    assert len({row["grant_ref"] for row in identities})==1
    assert len({row["resume_occurrence_id"] for row in identities})==1
    child_physical=[entry for row in completed for entry in row["physical"]]
    child_entries=[entry for row in completed for entry in row["engine_entries"]]
    assert len(child_entries)==1 and len(child_physical)==1
    assert child_physical[0]["caller_agent"]=="CitationClaimAssociation"
    before_replay=case.capture()
    replay=case.resume(identities[0])["budget_acknowledgement"]
    after=case.receipt(["BT-06","BT-07","BT-08","BT-21"],"native_windows_process_aliases",
        acknowledgements=[*acks,replay],observations={"native_process_ready":readiness,
            "native_process_results":completed,"actual_child_invoker_entries":child_physical,
            "actual_child_engine_entries":child_entries,"cold_before_replay":before_replay})
    assert len(after["budget"]["grant_history"])==1 and after["run"]["status"]=="success"
    assert after["budget"]==before_replay["budget"]
    assert after["physical_count"]==before_replay["physical_count"]
    assert after["engine_entry_count"]==before_replay["engine_entry_count"]==0
    # Parent capture contains only parent physical records; child records are
    # included explicitly above, never replaced by a literal zero counter.
    child_after={**after,"physical_requests":[*after["physical_requests"],*child_physical]}
    rows=_native_old_owner_operation_rows(case.before,child_after)
    assert rows and all(not row["new_matching_physical_entries"]
        and not row["changed_native_accounting_reservation_ids"] for row in rows)


def test_native_completed_writer_support_verifier_each_operation_reused_without_physical_or_accounting_delta(tmp_path):
    from unittest.mock import patch
    from model_gateway.fake_llm_client import FakeLLMClient
    from tools.fake_rag_tool import FakeRAGTool
    charges=[]; upstream_observations=[]; validity_observations=[]
    grounding_observations=[]
    existing_configure=_prephysical_charge_hook(charges,initial=True)
    def configure(engine,case):
        existing_configure(engine,case)
        _native_grounding_commit_observer(engine,case,grounding_observations)
    with patch.object(FakeRAGTool,"run",_bt03_three_source_producer(FakeRAGTool.run,upstream_observations)), \
            patch.object(FakeLLMClient,"generate",_bt03_model_semantic_producer(FakeLLMClient.generate,validity_observations,tmp_path/"native_validity_oracle")):
        case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure,
            run_builder=_bt03_dedicated_source_run,expected_current_child_count=1,
            native_run_id="bt3_"+uuid4().hex[:12])
        packet,pause,state,required=_native_wait(case)
        native_outputs_before=_native_successful_section_owner_outputs(case,expected_section_count=1)
        approval=case.approve(_native_pending_command(case,approval_id="bt21-finite-pending-work",
            authorization_id="bt21-explicit-local-authority",tokens=max(20000,required)))["budget_acknowledgement"]
        result=case.resume(approval)
        after_first=case.capture()
        replay=case.resume(approval)
        after=case.capture()
        native_all_outputs_after=_native_successful_section_owner_outputs(case,expected_section_count=1)
        from copy import deepcopy
        native_outputs_after=deepcopy(native_all_outputs_after)
        new_candidate_receipts=[];missing_old_receipts=[]
        old_sections={row["section_id"]:row for row in native_outputs_before["sections"]}
        for section in native_outputs_after["sections"]:
            old_refs={row["reference"]["artifact_ref"] for row in old_sections[section["section_id"]]["exact_candidate_output_receipts"]}
            seen={row["reference"]["artifact_ref"] for row in section["exact_candidate_output_receipts"]}
            missing_old_receipts.extend(sorted(old_refs-seen))
            new_candidate_receipts.extend(row for row in section["exact_candidate_output_receipts"]
                if row["reference"]["artifact_ref"] not in old_refs)
            # Every row was resolved by exact current native ref/hash above.
            # Compare only actual old refs, never classify an operation as old
            # merely because DOCUMENT Support reuses its underlying section_id.
            section["exact_candidate_output_receipts"]=[row for row in section["exact_candidate_output_receipts"]
                if row["reference"]["artifact_ref"] in old_refs]
        operation_rows=_native_old_owner_operation_rows(case.before,after)
        writer=[row for row in operation_rows if row["before_physical"]["extra"].get("call_purpose")=="scheme_section_generation"]
        support=[row for row in operation_rows if row["caller_agent"]=="ClaimSupportEvaluator"]
        verifier=[row for row in operation_rows if row["caller_agent"]=="VerifierAgent"]
        case.receipt(["BT-21"],"native_each_completed_owner_reuse",acknowledgements=[approval],observations={
            "actual_pending_result":result,"actual_exact_replay":replay,
            "native_completed_operation_rows":operation_rows,
            "native_upstream_source_observations":upstream_observations,
                "native_verifier_validity_oracle_observations":validity_observations,
            "native_successful_owner_outputs_before":native_outputs_before,
            "native_successful_owner_outputs_after":native_outputs_after,
            "native_all_owner_outputs_after":native_all_outputs_after,
            "new_document_candidate_receipts_separate":new_candidate_receipts,
            "missing_old_native_receipt_refs":missing_old_receipts,
            "completed_writer_rows":writer,"completed_support_rows":support,"completed_verifier_rows":verifier,
            "exact_original_source_cursor":packet.cursor.model_dump(mode="json"),
            "original_full_checkpoint_outputs":case.before["checkpoint_node_outputs"],
            "after_first":after_first,"test_owned_native_resource_charges":charges,
            "native_grounding_commit_observations":_native_observation_wire(grounding_observations)})


        # Save the actual native pair before final preservation oracles.
        assert not missing_old_receipts
        assert native_outputs_after==native_outputs_before
        assert all(section["prerequisite_validator_executions"] and section["native_verification_support_outputs"]
            and section["exact_candidate_output_receipts"] for section in native_outputs_before["sections"])
        assert len(writer)==len(case.wait.current_children)==1 and support and verifier,(
            "all completed owners in the explicit one-Section BT21 fixture must actually exist")
        assert len(operation_rows)==len(case.before["physical_requests"]),"unidentified native charging is not PASS"
        for row in operation_rows:
            assert row["native_old_reservations"]
            assert not row["new_matching_physical_entries"]
            assert not row["changed_native_accounting_reservation_ids"]
        assert after["budget"]==after_first["budget"]
        assert after["physical_count"]==after_first["physical_count"]

        _native_grounding_commit_oracles(grounding_observations,"CITATION_ASSOCIATION",root=case.root,allow_unreached=True)

# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. The race seams delay actual existing
# owner entry; cancellation and Child/artifact changes use native owner APIs.
@pytest.mark.parametrize("order",["cancel_before_fence","grant_before_cancel"])
def test_native_approval_explicit_cancel_held_fence_decides_order(tmp_path,order):
    from contextlib import contextmanager
    reached=threading.Event();release=threading.Event();active=threading.Event(); observations=[]
    def configure(engine,case):
        life=engine.lifecycle_service
        original=life.budget_authorization_fence
        @contextmanager
        def ordered(**bindings):
            if not active.is_set():
                with original(**bindings) as preimage: yield preimage
                return
            if order=="cancel_before_fence":
                observations.append({"phase":"BEFORE_NATIVE_FENCE","bindings":bindings})
                reached.set()
                if not release.wait(NATIVE_APPLICATION_TEST_WAIT_SECONDS): raise RuntimeError("TEST_TIME_BUDGET_EXHAUSTED before owner fence")
                with original(**bindings) as preimage: yield preimage
            else:
                with original(**bindings) as preimage:
                    observations.append({"phase":"ACTUAL_NATIVE_FENCE_HELD","bindings":bindings,
                        "preimage":_native_observation_wire(preimage)})
                    reached.set()
                    if not release.wait(NATIVE_APPLICATION_TEST_WAIT_SECONDS): raise RuntimeError("TEST_TIME_BUDGET_EXHAUSTED held owner fence")
                    yield preimage
        life.budget_authorization_fence=ordered
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    active.set();approval=None;actual_errors=[]
    with ThreadPoolExecutor(max_workers=2) as executor:
        future=executor.submit(case.approve,case.command())
        assert reached.wait(NATIVE_APPLICATION_TEST_WAIT_SECONDS),"TEST_TIME_BUDGET_EXHAUSTED approval fence not reached"
        if order=="cancel_before_fence":
            cancellation=case.service.cancel_run(**case.arguments,reason="explicit native cancellation wins fence")
            release.set()
            try: approval=future.result(timeout=NATIVE_APPLICATION_TEST_WAIT_SECONDS)
            except Exception as exc: actual_errors.append(type(exc).__name__+": "+str(exc))
            assert actual_errors and approval is None
        else:
            cancelling=executor.submit(case.service.cancel_run,**case.arguments,reason="explicit cancellation after held grant")
            release.set()
            approval=future.result(timeout=NATIVE_APPLICATION_TEST_WAIT_SECONDS)
            cancellation=cancelling.result(timeout=NATIVE_APPLICATION_TEST_WAIT_SECONDS)
    active.clear()
    native=case.capture()
    assert native["run"]["status"]=="cancelled"
    if approval is not None:
        assert approval["budget_acknowledgement"]["decision"]=="ACCEPTED"
        with pytest.raises(ValueError): case.resume(approval["budget_acknowledgement"])
    after=case.receipt(["BT-10","BT-26"],"native_cancel_race_"+order,
        acknowledgements=[] if approval is None else [approval["budget_acknowledgement"]],exceptions=actual_errors,
        observations={"actual_owner_fence_order":observations,"explicit_cancellation":cancellation,
            "native_after_control_revocation":native})
    assert len(after["budget"].get("grant_history",[]))==(0 if approval is None else 1)
    assert after["run"]==native["run"] and after["current_children"]==native["current_children"]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


@pytest.mark.parametrize("change",["child_open","document_head"])
def test_native_resume_current_child_or_document_head_change_forbids_dispatch(tmp_path,change):
    active=threading.Event(); changes=[]
    def configure(engine,case):
        original=engine.resume_budget_document
        def change_before_original(workflow,state,continuation):
            if active.is_set() and not changes:
                life=engine.lifecycle_service
                before=life.budget_preimage(state.run_id)
                assert before.run.status.value=="running"
                if change=="child_open":
                    prior=before.current_children[0]
                    new=life.open_attempt(state.run_id,prior.identity.attempt_scope_ref,
                        prior.identity.input_snapshot_version,attempt_id="bt-native-race-"+uuid4().hex)
                    after=life.budget_preimage(state.run_id)
                    assert after.run.lifecycle_revision==before.run.lifecycle_revision
                    assert after.current_children!=before.current_children
                    changes.append({"kind":"NATIVE_CHILD_OPEN","before":_native_preimage_wire(before),
                        "opened":new.model_dump(mode="json"),"after":_native_preimage_wire(after)})
                else:
                    packet=case.wait
                    pw=engine.stage_service.target_post_write_composition
                    prior=pw.artifact_store.resolve_exact(packet.refusal.artifact_state_ref)
                    payload={**prior.canonical_payload_ref,"bt_local_explicit_currentness_race":uuid4().hex}
                    with pw.database.run_write_guard_context(state.run_id):
                        proofs=tuple(pw.output_conformance_verifier.prove_precommit(
                            artifact_id=prior.artifact_ref.artifact_id,
                            artifact_scope=prior.artifact_ref.artifact_scope,
                            canonical_payload_ref=payload,visible_projection=prior.visible_projection,
                            output_binding=binding) for binding in prior.validated_output_bindings)
                        new_ref=pw.artifact_service.append_next_version(expected_parent_ref=prior.artifact_ref,
                            canonical_payload_ref=payload,origin=prior.origin,
                            rendered_output_ref=prior.rendered_output_ref,
                            visible_projection_ref=prior.visible_projection_ref,visible_projection=prior.visible_projection,
                            validated_output_bindings=prior.validated_output_bindings,
                            output_conformance_proofs=proofs)
                    assert new_ref!=prior.artifact_ref
                    new_record=pw.artifact_store.resolve_exact(new_ref)
                    assert new_record.parent_artifact_ref==prior.artifact_ref
                    assert new_ref.artifact_version==prior.artifact_ref.artifact_version+1
                    assert pw.artifact_store.resolve_exact(prior.artifact_ref)==prior
                    for binding in new_record.validated_output_bindings:
                        proof=pw.output_conformance_verifier.resolve_bound_conforming(record=new_record,output_binding=binding)
                        assert proof.artifact_hash==new_ref.artifact_hash
                    changes.append({"kind":"NATIVE_DOCUMENT_NEXT_IMMUTABLE_VERSION",
                        "previous":prior.model_dump(mode="json"),"new_head":new_ref.model_dump(mode="json"),
                        "native_new_record":new_record.model_dump(mode="json")})
            return original(workflow,state,continuation)
        engine.resume_budget_document=change_before_original
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    approval=case.approve(case.command())["budget_acknowledgement"]
    active.set();errors=[]
    try: case.resume(approval)
    except Exception as exc: errors.append(type(exc).__name__+": "+str(exc))
    assert errors and len(changes)==1
    after=case.receipt(["BT-10","BT-11","BT-19","BT-26"],"native_resume_currentness_"+change,
        acknowledgements=[approval],exceptions=errors,observations={"actual_native_owner_changes":changes})
    assert after["physical_count"]==case.before["physical_count"]
    assert len(after["budget"]["grant_history"])==1
    assert after["run"]["status"]=="running"
    assert after["budget"]["used"]==case.before["budget"]["used"]
    assert after["budget"]["active_reserved"]==case.before["budget"]["active_reserved"]


def test_native_paused_child_open_is_rejected_without_control_or_dispatch_mutation(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    runtime=case.open_runtime()
    try:
        prior=case.wait.current_children[0]
        with pytest.raises(ValueError):
            runtime.supervisor.workflow_engine.lifecycle_service.open_attempt(runtime.task.run_id,
                prior.identity.attempt_scope_ref,prior.identity.input_snapshot_version,attempt_id="bt-paused-open-"+uuid4().hex)
    finally: case.close_runtime(runtime)
    after=case.receipt(["BT-10","BT-11"],"native_paused_child_open_rejected")
    for name in ("run","current_children","budget","task_state"): assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. Explicit trusted entry only.
def test_native_no_command_reads_exact_immutable_pending_fact_and_never_adds_authority(tmp_path):
    case=_NativeApplicationCase(tmp_path/"native")
    pending=case.approve(None)["budget_acknowledgement"]
    assert pending["decision"]=="PENDING_AUTHORIZATION" and pending["read_only"] is True
    assert pending["decision_epoch"]=="ORIGINAL_IMMUTABLE_WAIT_PACKET"
    assert pending["wait_packet_ref"]==case.pause.wait_ref
    assert pending["wait_packet_sha256"]==case.pause.wait_sha256
    assert pending["authorization_ref"] is None and pending["grant_ref"] is None
    assert pending["accepted_grants_for_wait"]==[]
    repeat=case.approve(None)["budget_acknowledgement"]
    assert repeat==pending
    after=case.receipt(["BT-04","BT-29"],"native_no_command_pending",acknowledgements=[pending,repeat])
    for name in ("run","current_children","budget","task_state"): assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0
    accepted=case.approve(case.command())["budget_acknowledgement"]
    after_accept=case.approve(None)["budget_acknowledgement"]
    assert after_accept["decision_epoch"]=="ORIGINAL_IMMUTABLE_WAIT_PACKET"
    assert after_accept["accepted_grants_for_wait"]==[{"grant_ref":accepted["grant_ref"],
        "grant_sha256":accepted["grant_sha256"]}]
    assert after_accept["effective_limits"]["model_tokens_total"]==44000


@pytest.mark.parametrize("field,value",[
    ("delta",{"model_tokens_total":-1}),
    ("delta",{"model_tokens_total":2**63}),
    ("delta",{"invented_dimension":1}),
    ("delta",{"model_tokens_total":True}),
    ("delta",{"model_tokens_total":1.5}),
    ("cumulative_ceiling",{"model_tokens_total":float("inf")}),
    ("command_source","MODEL"),
    ("issuer"," "),
])
def test_native_malformed_typed_command_has_immutable_rejected_ack_and_unchanged_pause(tmp_path,field,value):
    case=_NativeApplicationCase(tmp_path/"native")
    command=case.command().model_copy(update={field:value})
    rejection=case.approve(command)["budget_acknowledgement"]
    assert rejection["decision"]=="REJECTED" and rejection["reason"]=="STRUCTURAL_COMMAND_REJECTED"
    assert rejection["authorization_ref"] is None and rejection["grant_ref"] is None
    assert rejection["rejected_command_shape_sha256"]==rejection["command_sha256"]
    assert rejection["structural_error"] and rejection["rejected_command_shape_json"]
    reference,raw=case.store.get_json_with_reference(rejection["ack_ref"],task_id=case.summary["task_id"],
        run_id=case.summary["run_id"])
    assert reference.content_hash=="sha256:"+rejection["ack_sha256"]
    assert raw["command_sha256"]==hashlib.sha256(raw["rejected_command_shape_json"].encode("utf-8")).hexdigest()
    repeated=case.approve(command)["budget_acknowledgement"]
    assert repeated==rejection
    with pytest.raises(ValueError): case.approve(case.command())
    usage_bytes=RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).model_usage_path.read_bytes()
    after=case.receipt(["BT-04","BT-06","BT-29"],"native_shape_"+field+"_"+uuid4().hex,
        acknowledgements=[rejection,repeated],observations={"exact_rejected_native_reference":reference.model_dump(mode="json"),
            "native_rejection_carrier":raw,"malformed_field":field})
    for name in ("run","current_children","budget","task_state"): assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0
    assert RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).model_usage_path.read_bytes()==usage_bytes



# Observation encoding only; no synthesized owner state.
def _native_preimage_wire(value):
    return {"run":value.run.model_dump(mode="json"),
        "current_children":[row.model_dump(mode="json") for row in value.current_children],
        "child_preimage_sha256":value.child_preimage_sha256}


def _native_observation_wire(value):
    if hasattr(value,"model_dump"):return value.model_dump(mode="json")
    from dataclasses import is_dataclass,fields
    from enum import Enum
    if isinstance(value,Enum):return value.value
    if isinstance(value,Path):return str(value)
    if is_dataclass(value):return {field.name:_native_observation_wire(getattr(value,field.name)) for field in fields(value)}
    if isinstance(value,dict):return {str(key):_native_observation_wire(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [_native_observation_wire(item) for item in value]
    return value


# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. All controls belong to the same fresh
# actual Factory/Graph/PAUSED native fixture; no component verdict is spliced in.
def test_native_distinct_authorizations_share_budget_cas_and_finite_cumulative_ceiling(tmp_path):
    from unittest.mock import patch
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
    case=_NativeApplicationCase(tmp_path/"native")
    barrier=threading.Barrier(2);observed=[];original=BudgetManager.apply_topup
    def simultaneous(manager,request,**kwargs):
        if request.run_id==case.summary["run_id"]:
            observed.append(request.model_dump(mode="json"))
            barrier.wait(timeout=NATIVE_APPLICATION_TEST_WAIT_SECONDS)
        return original(manager,request,**kwargs)
    def approve(index):
        try:return {"response":case.approve(case.command(approval_id="distinct-cas-client-"+str(index),
            authorization_id="distinct-cas-auth-"+str(index)))}
        except Exception as error:return {"actual_error":type(error).__name__+": "+str(error)}
    with patch.object(BudgetManager,"apply_topup",simultaneous),ThreadPoolExecutor(max_workers=2) as executor:
        responses=list(executor.map(approve,range(2)))
    assert len(observed)==2 and len({row["expected_budget_revision"] for row in observed})==1
    accepted=[row["response"]["budget_acknowledgement"] for row in responses if "response" in row
        and row["response"]["budget_acknowledgement"]["decision"]=="ACCEPTED"]
    assert len(accepted)==1 and any("actual_error" in row for row in responses)
    after=case.receipt(["BT-09"],"native_distinct_authorization_cas",acknowledgements=accepted,
        observations={"actual_native_topup_requests":observed,"actual_concurrent_results":responses})
    assert len(after["budget"]["grant_history"])==1
    assert after["effective_limits"]["model_tokens_total"]==44000
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


@pytest.mark.parametrize("mutation",["removed","changed","duplicate","used","reservation","revision","effective","bad_digest"])
def test_native_application_fresh_copy_malicious_budget_journal_fails_closed_without_dispatch(tmp_path,mutation):
    from shutil import copytree
    from unittest.mock import patch
    from bootstrap.supervisor_factory import SupervisorFactory
    from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
    case=_NativeApplicationCase(tmp_path/"original-native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    original_bytes=case.budget_journal_path.read_bytes()
    runtime=case.open_runtime()
    try:invoker_type=type(runtime.supervisor.workflow_engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker)
    finally:case.close_runtime(runtime)
    cloned=tmp_path/("fresh-attack-copy-"+mutation)
    copytree(case.root,cloned)
    attack_journal=cloned/case.budget_journal_path.relative_to(case.root)
    lines=attack_journal.read_text(encoding="utf-8").splitlines()
    frame=json.loads(lines[-1]);state=frame["state"]
    if mutation=="removed":state["grant_history"]=[]
    elif mutation=="changed":state["grant_history"][0]["request"]["delta"]["model_tokens_total"]+=1
    elif mutation=="duplicate":state["grant_history"]*=2
    elif mutation=="used":state["used"]["model_tokens_total"]+=1
    elif mutation=="reservation":state["active_reserved"]["model_tokens_total"]+=1
    elif mutation=="revision":state["revision"]+=2
    elif mutation=="effective":state["effective_limits"]={"model_tokens_total":999999}
    if mutation=="bad_digest":frame["event_sha256"]="0"*64
    else:frame["event_sha256"]=canonical_json_sha256({key:value for key,value in frame.items() if key!="event_sha256"})
    lines[-1]=json.dumps(frame,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    attack_journal.write_text("\n".join(lines)+"\n",encoding="utf-8")
    malformed_bytes=attack_journal.read_bytes()
    life=RunAttemptLifecycleService(attack_journal.parent/"lifecycle.jsonl")
    before=life.budget_preimage(case.summary["run_id"])
    actual_physical=[];original_invoke=invoker_type.invoke
    def measured(invoker,request,model_name):
        actual_physical.append(request.model_dump(mode="json"))
        return original_invoke(invoker,request,model_name)
    service=MainlineApplicationService(supervisor_factory=SupervisorFactory())
    arguments={**case.arguments,"output_root":cloned,"settings":replace(case.settings,data_root=cloned)}
    errors=[]
    with patch.object(invoker_type,"invoke",measured):
        try:service.resume_budget_run(**arguments,accepted_grant_ref=approval["grant_ref"],
            accepted_grant_sha256=approval["grant_sha256"])
        except Exception as error:errors.append(type(error).__name__+": "+str(error))
    assert errors and actual_physical==[]
    after=life.budget_preimage(case.summary["run_id"])
    assert after==before and attack_journal.read_bytes()==malformed_bytes
    assert case.budget_journal_path.read_bytes()==original_bytes
    record={"rows":["BT-13"],"scope":"FRESH_FULL_NATIVE_APP_COPY_MALICIOUS_LEDGER",
        "actual_decode_errors":errors,"native_before":_native_preimage_wire(before),
        "native_after":_native_preimage_wire(after),"budget_accounting":"UNKNOWN_CORRUPT_NATIVE_LEDGER",
        "actual_invoker_entries":actual_physical,"original_journal_sha256":hashlib.sha256(original_bytes).hexdigest(),
        "attack_journal_sha256":hashlib.sha256(malformed_bytes).hexdigest(),"mutation":mutation}
    (tmp_path/("native_budget_attack_"+mutation+".json")).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")


def test_native_legacy_failed_citation_exact_call_after_grant_replays_original_failure_without_billing(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import CitationGroundingError
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    before=case.capture();runtime=case.open_runtime()
    try:
        owner=runtime.supervisor.workflow_engine.stage_service.target_post_write_composition.citation_grounding
        identity={"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,"section_id":case.wait.refusal.section_id}
        original_input=owner.store.get_json(case.wait.refusal.input_ref,**identity)
        attempt=original_input["request"]["extra"]["attempt_id"]
        lineage=next(row.active_execution_lineage for row in case.wait.cursor.source_sections
            if row.active_execution_lineage.attempt_id==attempt)
        validations=[]
        def forbidden_live_validation():
            validations.append("unexpected_live_validation");raise AssertionError("legacy failure should cold replay before live validation")
        with pytest.raises(CitationGroundingError) as caught:
            owner._associate(original_input["input_material"],identity,lineage,forbidden_live_validation)
        assert owner.normalize_budget_refusal(caught.value.budget_failure_receipt)==owner.normalize_budget_refusal(case.wait.refusal)
        assert validations==[]
    finally:case.close_runtime(runtime)
    after=case.receipt(["BT-18"],"native_legacy_failure_after_grant",acknowledgements=[approval],
        observations={"actual_exact_legacy_input":original_input,"actual_replayed_refusal":caught.value.budget_failure_receipt,
            "native_before_exact_call":before})
    for name in ("run","current_children","budget","task_state"):assert after[name]==before[name]
    assert after["physical_count"]==before["physical_count"] and after["engine_entry_count"]==0


@pytest.mark.parametrize("damage",["material","capability","predecessor"])
def test_native_approved_cursor_cannot_admit_changed_material_capability_or_predecessor(tmp_path,damage):
    active=threading.Event();attacks=[]
    def configure(engine,case):
        owner=engine.stage_service.target_post_write_composition.citation_grounding
        original=engine.resume_budget_document
        def attack_before_graph(workflow,state,binding):
            if active.is_set() and not attacks:
                identity={"task_id":state.task_id,"run_id":state.run_id,"section_id":case.wait.refusal.section_id}
                old=owner.store.get_json(case.wait.refusal.input_ref,**identity)
                material=old["input_material"]
                attempt=old["request"]["extra"]["attempt_id"]
                lineage=next(row.active_execution_lineage for row in case.wait.cursor.source_sections
                    if row.active_execution_lineage.attempt_id==attempt)
                package=owner._package
                if damage=="material":material={**material,"unauthorized_material_change":"native-negative-control"}
                elif damage=="predecessor":binding=binding.model_copy(update={"predecessor_result_sha256":"0"*64})
                else:
                    def changed_capability(*args,**options):
                        exact,limit=package(*args,**options)
                        return exact,limit+1
                    owner._package=changed_capability
                try:
                    owner._associate(material,identity,lineage,
                        lambda:engine.lifecycle_service.assert_run_committable(state.run_id),budget_continuation=binding)
                except Exception as error:attacks.append({"actual_error":type(error).__name__+": "+str(error),
                    "damage":damage,"binding":binding.model_dump(mode="json"),"material":material})
                finally:owner._package=package
                assert len(attacks)==1
                raise RuntimeError("TEST_DIRECTED_BOUNDARY_COMPLETED_NO_ENGINE_CONTINUATION")
            return original(workflow,state,binding)
        engine.resume_budget_document=attack_before_graph
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    approval=case.approve(case.command())["budget_acknowledgement"];active.set()
    with pytest.raises(RuntimeError,match="TEST_DIRECTED_BOUNDARY_COMPLETED"):case.resume(approval)
    after=case.receipt(["BT-19"],"native_admission_change_"+damage,acknowledgements=[approval],
        observations={"actual_native_admission_rejections":attacks,"campaign_completion":"DIRECTED_BOUNDARY_ONLY",
            "test_stop_is_production_failure":False})
    assert after["physical_count"]==case.before["physical_count"]
    assert after["budget"]["used"]==case.before["budget"]["used"]
    assert after["current_children"]==case.before["current_children"] and after["run"]["status"]=="running"


@pytest.mark.parametrize("damage",["missing_writer","wrong_writer_hash","nonexport_source"])
def test_native_paused_current_document_rejects_missing_wrong_or_nonexport_writer_source(tmp_path,damage):
    case=_NativeApplicationCase(tmp_path/"native");runtime=case.open_runtime();errors=[]
    try:
        pw=runtime.supervisor.workflow_engine.stage_service.target_post_write_composition
        sources=list(case.wait.cursor.source_sections);first=sources[0]
        if damage=="missing_writer":altered=first.model_copy(update={"writer_execution_ref":None})
        elif damage=="wrong_writer_hash":altered=first.model_copy(update={"writer_execution_ref":
            first.writer_execution_ref.model_copy(update={"result_sha256":"0"*64})})
        else:altered=first.model_copy(update={"section_paragraph_manifest":None,"writer_generation_invocation_ref":None})
        sources[0]=altered
        try:pw.validate_budget_document_current(case.wait.refusal.model_dump(mode="json"),
            materialization_ref=case.wait.cursor.budget_materialization_ref,task_id=runtime.task.task_id,
            run_id=runtime.task.run_id,document_id=case.wait.cursor.document_id,
            project_input=runtime.task.project_input,section_execution_results=tuple(sources))
        except Exception as error:errors.append(type(error).__name__+": "+str(error))
    finally:case.close_runtime(runtime)
    assert errors
    after=case.receipt(["BT-22"],"native_paused_writer_source_"+damage,exceptions=errors,
        observations={"actual_native_original_source":first.model_dump(mode="json"),
            "supplied_changed_source":altered.model_dump(mode="json"),"damage":damage,
            "boundary":"SAME_FULL_NATIVE_APP_HOLD_OWNER_READ_REJECTION_NOT_A_NEW_EXPORT_VERDICT"})
    for name in ("run","current_children","budget","task_state"):assert after[name]==case.before[name]
    assert after["run"]["status"]=="paused"
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


def test_native_draining_before_hold_remains_draining_and_rejects_topup(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import CitationGroundingError
    changes=[]
    def configure(engine,case):
        owner=engine.stage_service.target_post_write_composition.citation_grounding
        associate=owner._associate
        def draining_after_native_closed_refusal(*args,**options):
            try:return associate(*args,**options)
            except CitationGroundingError as error:
                if error.budget_failure_receipt is not None and not changes:
                    manager=engine.budget_runtime(error.budget_failure_receipt["task_id"],error.budget_failure_receipt["run_id"],
                        require_existing_account=True)
                    before=manager.canonical_state()
                    after=manager.start_draining(expected_revision=before.revision)
                    changes.append({"before":before.model_dump(mode="json"),"after":after.model_dump(mode="json"),
                        "existing_native_denial":error.budget_failure_receipt})
                raise
        owner._associate=draining_after_native_closed_refusal
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    assert len(changes)==1 and case.before["budget"]["draining"] is True
    errors=[];response=None
    try:response=case.approve(case.command())
    except Exception as error:errors.append(type(error).__name__+": "+str(error))
    if response is not None:assert response["budget_acknowledgement"]["decision"]!="ACCEPTED"
    after=case.receipt(["BT-28"],"native_draining_topup_rejection",exceptions=errors,
        acknowledgements=[] if response is None else [response["budget_acknowledgement"]],
        observations={"actual_native_draining_transition_before_hold":changes})
    assert after["budget"]==case.before["budget"] and after["budget"]["draining"] is True
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. Read existing native owner facts;
# successful Section availability remains separate from the unfinished Document.
def _native_release_absence_observer(case, runtime, composition):
    """Read actual immutable Target publication envelopes while the Run is PAUSED."""
    engine = runtime.supervisor.workflow_engine
    store = composition.release_store
    assert composition.release_service.publication_store is store
    native_run_before = engine.lifecycle_service.load_run(runtime.task.run_id)
    references = tuple(store.list_references())
    native_run_after = engine.lifecycle_service.load_run(runtime.task.run_id)
    assert native_run_before == native_run_after
    assert native_run_after.status.value == "paused" and native_run_after.finished_at is None
    method = type(store).list_references
    source_path = Path(method.__code__.co_filename).resolve()
    expected_source = Path(__file__).resolve().parents[1] / "apps/enterprise_document/services/agent_rag_v1/artifact_store.py"
    assert source_path == expected_source and method.__qualname__ == "FileArtifactStore.list_references"
    all_refs = [row.model_dump(mode="json") for row in references]
    exact_run_refs = [row.model_dump(mode="json") for row in references
                      if row.task_id == runtime.task.task_id and row.run_id == runtime.task.run_id]
    value = {
        "schema_version": "bt23_native_target_release_absence_v1",
        "scope": "PASSIVE_EXACT_NATIVE_TARGET_PUBLICATION_STORE_NOT_REAL_EXTERNAL_PUBLICATION",
        "task_id": runtime.task.task_id,
        "run_id": runtime.task.run_id,
        "document_id": case.wait.cursor.document_id,
        "native_run_before": native_run_before.model_dump(mode="json"),
        "native_run_after": native_run_after.model_dump(mode="json"),
        "actual_publication_store_root": str(store.root),
        "actual_all_store_references": all_refs,
        "actual_same_run_references": exact_run_refs,
        "actual_same_run_manifest_refs": [row for row in exact_run_refs
            if row["artifact_type"] == "post_write_release_manifest"],
        "actual_same_run_payload_refs": [row for row in exact_run_refs
            if row["artifact_type"] == "post_write_release_payload"],
        "actual_same_run_proof_refs": [row for row in exact_run_refs
            if row["artifact_type"] == "post_write_release_input_proof"],
        "release_permission_from_this_observation": "NONE",
    }
    return {"schema_version": "bt23_native_release_absence_records_v1", "records": [{
        "status": "MEASURED", "native_value": value,
        "native_point": {"path": str(source_path),
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "function": method.__qualname__, "line": method.__code__.co_firstlineno,
            "native_operation": "PASSIVE_ENUMERATE_EXACT_TARGET_PUBLICATION_STORE",
            "native_refs": [row.artifact_ref for row in references]},
        "evidence_scope": "Actual existing FileArtifactStore.list_references return; empty refs are a reached native getter result, never an absent trace/default zero.",
    }]}


def test_native_exportable_section_owner_facts_coexist_with_budget_pause_and_no_document_release(tmp_path):
    from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema
    case=_NativeApplicationCase(tmp_path/"native");runtime=case.open_runtime();rows=[]
    try:
        pw=runtime.supervisor.workflow_engine.stage_service.target_post_write_composition
        ref,material=pw.citation_grounding.store.get_json_with_reference(
            case.wait.cursor.budget_materialization_ref.artifact_ref,task_id=runtime.task.task_id,run_id=runtime.task.run_id)
        assert ref==case.wait.cursor.budget_materialization_ref
        for source,raw in zip(case.wait.cursor.source_sections,material["section_results"]):
            writer=pw.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
            control=pw.control_store.resolve_exact(PostWriteControlDecisionRefSchema.model_validate(raw["control_decision_ref"]))
            from apps.enterprise_document.schemas.post_write.foundation import ArtifactStateRefSchema
            artifact=pw.artifact_store.resolve_exact(ArtifactStateRefSchema.model_validate(raw["artifact_state_ref"]))
            current=pw.artifact_store.resolve_current(artifact.artifact_ref.artifact_id)
            assert writer.status.value=="success" and writer.artifact is not None
            assert writer.artifact.content==source.section.content and source.section.content
            assert artifact==current
            assert control.availability_projection.state.value in {"AVAILABLE","BEST_EFFORT"}
            rows.append({"section_id":source.section.section_id,"writer_ref":source.writer_execution_ref.model_dump(mode="json"),
                "exact_writer_result":writer.model_dump(mode="json"),"native_current_artifact":artifact.model_dump(mode="json"),
                "native_section_control":control.model_dump(mode="json"),
                "availability_axis":control.availability_projection.model_dump(mode="json")})
        inspect=runtime.supervisor.workflow_engine.inspect(runtime.workflow,run_id=runtime.task.run_id)
        graph=inspect["graph_state"]
        release_absence=_native_release_absence_observer(case,runtime,pw)
    finally:case.close_runtime(runtime)
    assert len(rows)==8
    execution=_execution(case.summary)
    assert execution["status"]=="paused"
    assert execution["metadata"]["pause_purpose"]=="BUDGET_AUTHORIZATION"
    after=case.receipt(["BT-23"],"native_section_availability_under_hold",observations={
        "exact_native_section_availability_rows":rows,"native_graph_inspection":inspect,
        "native_graph_state":graph,"actual_agent_result":execution,
        "document_completion_authority":"NONE_NATIVE_BUDGET_WAIT",
        "native_document_release_absence":release_absence})
    for name in ("run","current_children","budget","task_state"):assert after[name]==case.before[name]
    assert after["run"]["status"]=="paused" and after["physical_count"]==case.before["physical_count"]
    assert after["engine_entry_count"]==0

    native_release=release_absence["records"][0]["native_value"]
    assert native_release["actual_same_run_manifest_refs"]==[]
    assert native_release["actual_same_run_payload_refs"]==[]
    assert native_release["actual_same_run_proof_refs"]==[]


def test_native_token_topup_preserves_each_other_finite_cap_at_actual_approved_cursor(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import CANONICAL_BUDGET_DIMENSIONS,CanonicalBudgetVectorSchema,BudgetReservationRefSchema
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
    active=threading.Event();observations=[]
    def configure(engine,case):
        original=engine.resume_budget_document
        def inspect_caps(workflow,state,continuation):
            if active.is_set() and not observations:
                manager=engine.budget_runtime(state.task_id,state.run_id,require_existing_account=True)
                before=manager.canonical_state()
                for dimension in CANONICAL_BUDGET_DIMENSIONS:
                    requested=CanonicalBudgetVectorSchema(**{dimension:getattr(before.remaining,dimension)+1})
                    reservation=BudgetReservationRefSchema(reservation_id="bt-native-cap-"+dimension+"-"+uuid4().hex,
                        task_id=state.task_id,run_id=state.run_id,reservation_kind="local_test_owned_finite_cap_probe",
                        owner_occurrence_ref="fixture://actual-approved-cursor/cap/"+dimension,scope_ref="run:"+state.run_id,
                        operation_ref="fixture://actual-approved-cursor/cap/"+dimension)
                    with pytest.raises(CanonicalBudgetCapacityExceeded) as refusal:
                        manager.reserve(reservation,requested,expected_revision=before.revision)
                    denial=refusal.value.native_denial
                    assert denial.denied_dimension==dimension
                    observations.append({"dimension":dimension,"requested":requested.model_dump(mode="json"),
                        "actual_native_denial":denial.model_dump(mode="json"),"budget_after":manager.canonical_state().model_dump(mode="json")})
                    assert manager.canonical_state()==before
                raise RuntimeError("TEST_DIRECTED_FINITE_CAP_BOUNDARY_COMPLETE")
            return original(workflow,state,continuation)
        engine.resume_budget_document=inspect_caps
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    approval=case.approve(case.command())["budget_acknowledgement"];active.set()
    with pytest.raises(RuntimeError,match="TEST_DIRECTED_FINITE_CAP_BOUNDARY"):case.resume(approval)
    after=case.receipt(["BT-28"],"native_all_finite_caps_after_token_topup",acknowledgements=[approval],
        observations={"actual_native_dimension_capacity_refusals":observations,
            "harness_stop_after_directed_boundary":True,"production_failure":False})
    assert {row["dimension"] for row in observations}==set(CANONICAL_BUDGET_DIMENSIONS)
    for dimension in CANONICAL_BUDGET_DIMENSIONS:
        expected=case.before["effective_limits"][dimension]+(20000 if dimension=="model_tokens_total" else 0)
        assert after["effective_limits"][dimension]==expected
    assert after["budget"]["used"]==case.before["budget"]["used"]
    assert after["physical_count"]==case.before["physical_count"] and after["run"]["status"]=="running"
    assert after["current_children"]==case.before["current_children"]


def test_native_model_request_extra_cannot_authorize_capacity_or_dispatch_while_paused(tmp_path):
    from schemas.model import ModelRequestSchema
    case=_NativeApplicationCase(tmp_path/"native");runtime=case.open_runtime();errors=[]
    try:
        owner=runtime.supervisor.workflow_engine.stage_service.target_post_write_composition.citation_grounding
        identity={"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,"section_id":case.wait.refusal.section_id}
        old=owner.store.get_json(case.wait.refusal.input_ref,**identity)
        request=ModelRequestSchema.model_validate(old["request"]).model_copy(update={
            "model_call_id":"model-extra-forgery-"+uuid4().hex,
            "extra":{**old["request"]["extra"],"budget_topup_authorization":case.command().model_dump(mode="json"),
                "trusted_budget_approved":True,"budget_delta":{"model_tokens_total":20000}}})
        try:owner.gateway.generate(request)
        except Exception as error:errors.append(type(error).__name__+": "+str(error))
    finally:case.close_runtime(runtime)
    assert errors
    after=case.receipt(["BT-29"],"native_request_extra_no_authority",exceptions=errors,
        observations={"actual_model_request_extra_attack":request.model_dump(mode="json")})
    for name in ("run","current_children","budget","task_state"):assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. Actual Windows exit after existing
# validator durable commit/readback, never a fabricated completed/no-effect fact.
def _application_validator_cut_child(connection,arguments,grant):
    from bootstrap.supervisor_factory import SupervisorFactory
    from agent.supervisor_agent import _budget_native_process_identity
    physical=[];entries=[];claims=[]
    class ValidatorCutFactory(SupervisorFactory):
        def build(self,**kwargs):
            supervisor=super().build(**kwargs);engine=supervisor.workflow_engine
            pw=engine.stage_service.target_post_write_composition
            invoker=pw.citation_grounding.gateway.invoker;invoke=invoker.invoke
            def measured(request,model_name):
                raw=request.model_dump(mode="json")
                physical.append({"model_call_id":request.model_call_id,"request_sha256":canonical_json_sha256(raw),
                    "caller_agent":request.caller_agent,"model_name":model_name,"extra":raw["extra"]})
                return invoke(request,model_name)
            invoker.invoke=measured
            resume=engine.resume_budget_document
            def counted(workflow,state,binding):
                entries.append(binding.model_dump(mode="json"));return resume(workflow,state,binding)
            engine.resume_budget_document=counted
            put=supervisor._budget_put
            def track_claim(store,ref,body,task,kind):
                result=put(store,ref,body,task,kind)
                if kind=="budget_document_occurrence_claim":
                    actual,payload=store.get_json_with_reference(ref,task_id=task.task_id,run_id=task.run_id)
                    claims.append({"reference":actual.model_dump(mode="json"),"carrier":payload})
                return result
            supervisor._budget_put=track_claim
            finalize=pw.validation_store.finalize_validator_execution
            def cut_after_actual_finalize(event,record,**options):
                actual=finalize(event,record,**options)
                if record.validator_id==pw.citation_grounding.VALIDATOR_ID and record.terminal_execution_state.value=="COMPLETED":
                    read=pw.validation_store.find_validator_execution_for_item(validation_run_id=record.validation_run_id,
                        validator_id=record.validator_id,expected_item_ref=record.expected_item_accounting[0].expected_validation_item_ref)
                    assert read==actual==record and claims
                    connection.send({"stage":"NATIVE_VALIDATOR_COMMIT_CUT","process":_budget_native_process_identity(),
                        "native_validator_record":record.model_dump(mode="json"),"native_terminal_event":event.model_dump(mode="json"),
                        "latest_native_claim":claims[-1],"physical":physical,"engine_entries":entries,
                        "native_budget":engine.budget_runtime(grant["task_id"],grant["run_id"],
                            require_existing_account=True).canonical_state().model_dump(mode="json")})
                    os._exit(62)
                return actual
            pw.validation_store.finalize_validator_execution=cut_after_actual_finalize
            return supervisor
    root=Path(arguments["output_root"])
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",supervisor_model_name="fake_llm",
        enable_llm_routing=False,trace_enabled=True,data_capture_enabled=True)
    try:
        result=MainlineApplicationService(supervisor_factory=ValidatorCutFactory()).resume_budget_run(
            **{**arguments,"project_root":Path(arguments["project_root"]),"output_root":root},settings=settings,
            allow_demo_defaults=True,enable_agent_self_rag=False,accepted_grant_ref=grant["grant_ref"],
            accepted_grant_sha256=grant["grant_sha256"])
        connection.send({"stage":"UNEXPECTED_COMPLETION_WITHOUT_TARGET_CUT","result":result,
            "physical":physical,"engine_entries":entries})
    except BaseException as error:
        connection.send({"stage":"ACTUAL_PRE_CUT_EXCEPTION","error":type(error).__name__+": "+str(error),
            "physical":physical,"engine_entries":entries})
    finally:connection.close()


def test_native_application_validator_commit_windows_exit_reuses_successful_cognition_without_redispatch(tmp_path):
    from agent.supervisor_agent import _budget_original_process_closed
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    context=multiprocessing.get_context("spawn");parent,child=context.Pipe()
    arguments={name:str(case.arguments[name]) for name in ("project_root","output_root","task_id","run_id")}
    worker=context.Process(target=_application_validator_cut_child,args=(child,arguments,approval))
    worker.start();child.close();case.test_workers.append(worker)
    cut=_native_worker_message(parent,worker,case,"native_validator_commit_cut")
    _native_worker_join(worker,case,"native_validator_commit_exit",62);parent.close()
    assert worker.exitcode==62 and cut["stage"]=="NATIVE_VALIDATOR_COMMIT_CUT",cut
    assert len(cut["physical"])==1 and len(cut["engine_entries"])==1
    original_closed=_budget_original_process_closed(cut["latest_native_claim"]["carrier"])
    before_reopen=case.capture()
    from agent.runtime.graph_state import GraphStateSchema
    from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentContinuationSchema
    runtime=case.open_runtime();cursor_errors=[]
    try:
        engine=runtime.supervisor.workflow_engine
        binding=BudgetDocumentContinuationSchema.model_validate(cut["latest_native_claim"]["carrier"]["identity"]["continuation"])
        slot=engine.stage_service.target_post_write_composition.citation_grounding.read_budget_document_admission(binding,
            identity={"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,"section_id":case.wait.refusal.section_id})
        assert slot["phase"]=="COMPLETED"
        inspection=engine.inspect(runtime.workflow,run_id=runtime.task.run_id)
        try:
            cursor=engine.validate_budget_execution_cursor(runtime.workflow,graph_state=GraphStateSchema.model_validate(inspection["graph_state"]),
                continuation=binding,original_claim=cut["latest_native_claim"]["carrier"])
        except (ValueError,RuntimeError) as error:
            cursor_errors.append(type(error).__name__+": "+str(error))
            cursor={"phase":"INDETERMINATE","reason":"NO_EXACT_NATIVE_GRAPH_CURSOR_PROOF"}
    finally:case.close_runtime(runtime)
    result=None;replay=None;resume_errors=[]
    if cursor["phase"] in {"COMPLETED","GRAPH_COMPLETED","PRE_CITATION_INTENT_SAFE"}:
        result=case.resume(approval)
        after_resume=case.capture()
        replay=case.resume(approval)
    else:
        assert cursor["phase"]=="INDETERMINATE"
        try:case.resume(approval)
        except (ValueError,RuntimeError) as error:resume_errors.append(type(error).__name__+": "+str(error))
        assert resume_errors
        after_resume=case.capture()
        for name in ("run","current_children","budget","task_state"):assert after_resume[name]==before_reopen[name]
        assert after_resume["engine_entry_count"]==before_reopen["engine_entry_count"]
    after=case.receipt(["BT-17","BT-21"],"native_validator_commit_process_cut",
        acknowledgements=[approval]+([] if result is None else [result["budget_acknowledgement"],replay["budget_acknowledgement"]]),
        exceptions=cursor_errors+resume_errors,observations={
            "actual_windows_native_cut":cut,"positive_latest_claim_process_closure":original_closed,
            "native_before_cold_reopen":before_reopen,"native_after_resume":after_resume,
            "actual_cold_native_completed_admission":slot,"actual_native_cursor_classification":_native_observation_wire(cursor),
            "source_and_cursor_authority":"VALIDATED_BY_NATIVE_GRAPH_NOT_GRANTED_BY_PROCESS_EXIT"})
    assert after["physical_count"]==before_reopen["physical_count"]
    assert after["budget"]["used"]==before_reopen["budget"]["used"]
    assert after["budget"]["active_reserved"]==before_reopen["budget"]["active_reserved"]
    assert after["budget"]==after_resume["budget"] and after["engine_entry_count"]==after_resume["engine_entry_count"]
    assert after["run"]["status"]==("success" if result is not None else before_reopen["run"]["status"])
    all_after={**after,"physical_requests":[*after["physical_requests"],*cut["physical"]]}
    rows=_native_old_owner_operation_rows(case.before,all_after)
    assert rows and all(not row["new_matching_physical_entries"]
        and not row["changed_native_accounting_reservation_ids"] for row in rows)


# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. Existing worker gets a new test-only
# AFTER_ENGINE_NATIVE_SUCCESS_BEFORE_APPLICATION_RESULT physical crash boundary.
def test_native_graph_success_checkpoint_before_application_result_cold_graph_completed_no_engine_replay(tmp_path):
    from agent.supervisor_agent import _budget_original_process_closed
    from agent.runtime.graph_state import GraphStateSchema
    from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentContinuationSchema
    case=_NativeApplicationCase(tmp_path/"native")
    approval=case.approve(case.command())["budget_acknowledgement"]
    context=multiprocessing.get_context("spawn");parent,child=context.Pipe()
    arguments={name:str(case.arguments[name]) for name in ("project_root","output_root","task_id","run_id")}
    worker=context.Process(target=_application_cut_child,args=(child,arguments,approval,"terminal_engine_exit"))
    worker.start();child.close();case.test_workers.append(worker)
    cut=_native_worker_message(parent,worker,case,"native_graph_success_cut")
    _native_worker_join(worker,case,"native_graph_success_exit",63);parent.close()
    assert worker.exitcode==63 and cut["stage"]=="NATIVE_GRAPH_SUCCESS_BEFORE_APPLICATION_RESULT",cut
    assert cut["native_run"]["status"]=="success" and len(cut["physical"])==1 and len(cut["engine_entries"])==1
    closed=None;closure_error=None
    try:closed=_budget_original_process_closed(cut["latest_native_claim"]["carrier"])
    except RuntimeError as error:closure_error=error
    # This exact native success cut must remain reviewable even if cold cursor
    # classification fails. Save the worker's controls and positive OS closure
    # before opening a cold classifier; no receipt implies execution permission.
    pre_classifier={"schema_version":"bt_native_graph_success_pre_classifier_v1",
        "rows":["BT-16","BT-17","BT-21"],"phase":"PRE_COLD_CURSOR_CLASSIFICATION",
        "task_id":case.summary["task_id"],"run_id":case.summary["run_id"],
        "configuredTestSeconds":NATIVE_APPLICATION_TEST_WAIT_SECONDS,
        "production_failure":"NO","before":case.before,"approval_acknowledgement":approval,
        "actual_native_engine_return_cut":cut,"actual_test_worker_pid":worker.pid,
        "actual_test_worker_exitcode":worker.exitcode,
        "positive_original_claim_process_closure":closed,
        "actual_process_closure_error":None if closure_error is None else {
            "type":type(closure_error).__name__,"message":str(closure_error)},
        "native_run":cut["native_run"],"native_current_children":cut["native_current_children"],
        "native_budget":cut["native_budget"],"native_graph_inspection":cut["native_graph_inspection"],
        "physical_observations":{"parent":list(case.physical),"child":cut["physical"]},
        "engine_entry_observations":{"parent":list(case.engine_entries),"child":cut["engine_entries"]},
        "source_and_cursor_authority":"NOT_GRANTED_BY_PROCESS_EXIT"}
    pre_classifier_path=case.root.parent/"native_graph_success_cut_pre_classifier_native_observation.json"
    with pre_classifier_path.open("x",encoding="utf-8") as file:
        file.write(json.dumps(pre_classifier,ensure_ascii=False,indent=2))
    if closure_error is not None:raise closure_error
    runtime=case.open_runtime()
    try:
        engine=runtime.supervisor.workflow_engine
        claim=cut["latest_native_claim"]["carrier"]
        binding=BudgetDocumentContinuationSchema.model_validate(claim["identity"]["continuation"])
        inspection=engine.inspect(runtime.workflow,run_id=runtime.task.run_id)
        cursor=engine.validate_budget_execution_cursor(runtime.workflow,
            graph_state=GraphStateSchema.model_validate(inspection["graph_state"]),continuation=binding,original_claim=claim)
        assert cursor["phase"]=="GRAPH_COMPLETED"
        assert cursor["terminal_agent_result"]
        refs=engine.stage_service.target_post_write_composition.citation_grounding.store.list_references()
        occurrence_base=cut["latest_native_claim"]["reference"]["artifact_ref"].removesuffix("/claim")
        assert not any(ref.artifact_ref==occurrence_base+"/result" for ref in refs)
    finally:case.close_runtime(runtime)
    before=case.capture()
    result=case.resume(approval);after_first=case.capture()
    replay=case.resume(approval)
    after=case.receipt(["BT-16","BT-17","BT-21"],"native_graph_success_before_app_result",
        acknowledgements=[approval,result["budget_acknowledgement"],replay["budget_acknowledgement"]],observations={
            "actual_native_engine_return_cut":cut,"positive_original_claim_process_closure":closed,
            "pre_cold_classifier_native_observation_path":str(pre_classifier_path),
            "pre_cold_classifier_native_observation_sha256":hashlib.sha256(pre_classifier_path.read_bytes()).hexdigest(),
            "cold_native_graph_classification":_native_observation_wire(cursor),"native_before_projection":before,
            "native_first_projection":after_first})
    assert after["run"]==before["run"] and after["current_children"]==before["current_children"]
    assert after["budget"]==before["budget"]
    assert after["physical_count"]==before["physical_count"] and after["engine_entry_count"]==before["engine_entry_count"]==0
    assert result["budget_acknowledgement"]==replay["budget_acknowledgement"]
    assert result["budget_acknowledgement"]["claim_ref"].endswith("/claims/1")
    all_after={**after,"physical_requests":[*after["physical_requests"],*cut["physical"]]}
    rows=_native_old_owner_operation_rows(case.before,all_after)
    assert rows and all(not row["new_matching_physical_entries"]
        and not row["changed_native_accounting_reservation_ids"] for row in rows)


# PREPARED_NOT_APPLIED_NOT_PARSED_NOT_RUN. Cleanup only the exact fresh test
# worker. It grants no production terminal or external no-effect authority.
def _native_worker_message(pipe,process,case,label):
    if pipe.poll(NATIVE_APPLICATION_TEST_WAIT_SECONDS):return pipe.recv()
    _native_worker_time_budget_exhausted(process,case,label)


def _native_worker_join(process,case,label,expected_exit):
    process.join(NATIVE_APPLICATION_TEST_WAIT_SECONDS)
    if process.is_alive():_native_worker_time_budget_exhausted(process,case,label)
    assert process.exitcode==expected_exit


def _native_worker_time_budget_exhausted(process,case,label):
    # A fresh multiprocessing.Process handle identifies exactly this test-owned
    # worker. No process/path search, production cancel API or lifecycle mutation.
    identity={"pid":process.pid,"exitcode_before_cleanup":process.exitcode}
    raw=[]
    for path in (case.budget_journal_path,case.budget_journal_path.parent/"lifecycle.jsonl",
                 RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).task_state_path):
        if path.exists():
            data=path.read_bytes()
            raw.append({"path":str(path),"size":len(data),"sha256":hashlib.sha256(data).hexdigest(),
                "decoded_owner_state":"UNKNOWN_NOT_READ_WHILE_WORKER_ACTIVE"})
    workers=case.test_workers or [process]
    worker_observations=[]
    for fresh_worker in workers:
        if fresh_worker.is_alive():fresh_worker.terminate()
        fresh_worker.join(10)
        worker_observations.append({"pid":fresh_worker.pid,"exitcode":fresh_worker.exitcode})
    record={"schema_version":"bt_native_harness_timeout_v1","test_status":"TEST_TIME_BUDGET_EXHAUSTED",
        "configuredTestSeconds":NATIVE_APPLICATION_TEST_WAIT_SECONDS,"production_failure":"NO",
        "campaign_status":"E2E_INCOMPLETE","task_id":case.summary["task_id"],"run_id":case.summary["run_id"],
        "harness_has_terminal_production_authority":False,"worker_identity":identity,
        "worker_exitcode_after_cleanup":process.exitcode,"raw_native_file_observations":raw,
        "exact_fresh_test_workers_after_cleanup":worker_observations,
        "cursor_no_effect_authority":"NOT_GRANTED_BY_TIMEOUT_OR_TERMINATION",
        "physical_accounting_after_unobserved_cut":"UNKNOWN",
        "original_denial_sha256":hashlib.sha256(case.original_denial_bytes).hexdigest(),
        "original_wait_sha256":hashlib.sha256(case.original_wait_bytes).hexdigest()}
    path=case.root.parent/(label+"_"+uuid4().hex+"_harness_incomplete.json")
    path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    pytest.fail("TEST_TIME_BUDGET_EXHAUSTED / E2E_INCOMPLETE: "+str(path))


# PREPARED_NOT_EXPORTED_NOT_PARSED_NOT_RUN. Append to latest full App tests only
# after root gate. Same native fixture/owners, no new production authority.
@pytest.mark.parametrize("substitution",["delta","authorization_id","ceiling"])
def test_native_same_approval_id_changed_command_meaning_conflicts(tmp_path,substitution):
    case=_NativeApplicationCase(tmp_path/"native")
    command=case.command();accepted=case.approve(command)["budget_acknowledgement"]
    before=case.capture()
    if substitution=="delta":changed=case.command(tokens=19999)
    elif substitution=="authorization_id":changed=case.command(authorization_id="different-explicit-auth")
    else:
        ceiling=command.cumulative_ceiling.model_dump(mode="json");ceiling["model_tokens_total"]+=1
        changed=case.command(cumulative_ceiling=ceiling)
    errors=[]
    try:case.approve(changed)
    except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
    assert errors
    replay=case.approve(command)["budget_acknowledgement"];assert replay==accepted
    after=case.receipt(["BT-07"],"native_same_id_command_"+substitution,
        acknowledgements=[accepted,replay],exceptions=errors,observations={
            "actual_original_command":command.model_dump(mode="json"),
            "actual_changed_command":changed.model_dump(mode="json"),"native_after_first_grant":before})
    for name in ("run","current_children","budget","task_state"):assert after[name]==before[name]
    assert after["physical_count"]==before["physical_count"] and after["engine_entry_count"]==0


@pytest.mark.parametrize("field",["run_id","semantic_operation_ref","semantic_input_sha256","child_preimage_sha256"])
def test_native_accepted_app_request_same_id_owner_binding_substitution_conflicts(tmp_path,field):
    case=_NativeApplicationCase(tmp_path/"native")
    accepted=case.approve(case.command())["budget_acknowledgement"];before=case.capture()
    runtime=case.open_runtime();errors=[];native_grant=None;changed=None
    try:
        engine=runtime.supervisor.workflow_engine
        manager=engine.budget_runtime(runtime.task.task_id,runtime.task.run_id,require_existing_account=True)
        grant=manager.resolve_topup_grant(accepted["grant_ref"],accepted["grant_sha256"])
        native_grant=grant.model_dump(mode="json")
        value=("foreign-native-run:"+uuid4().hex if field=="run_id" else
            "different-semantic-operation:"+uuid4().hex if field=="semantic_operation_ref" else "0"*64)
        changed=grant.request.model_copy(update={field:value})
        # Public trusted App never accepts these authority fields. This attack
        # reuses its actual accepted native request/id at the existing owner API,
        # with its exact immutable resolved authorization, not a supplied root.
        try:manager.apply_topup(changed,authorization_resolver=lambda request:grant.authorization,
            paused_authorization_fence=engine.lifecycle_service.budget_authorization_fence)
        except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
    finally:case.close_runtime(runtime)
    assert errors
    after=case.receipt(["BT-07"],"native_same_id_owner_"+field,acknowledgements=[accepted],exceptions=errors,
        observations={"actual_accepted_native_app_grant":native_grant,"changed_existing_owner_request":changed.model_dump(mode="json"),
            "native_after_first_grant":before,"boundary":"SAME_APP_NATIVE_ACCEPTED_REQUEST_OWNER_REPLAY_NOT_PUBLIC_SELF_AUTHORITY"})
    for name in ("run","current_children","budget","task_state"):assert after[name]==before[name]
    assert after["physical_count"]==before["physical_count"] and after["engine_entry_count"]==0


def test_native_actual_cold_factory_changed_25000_baseline_rejects_original_24000_account(tmp_path,monkeypatch):
    from bootstrap.agent_quality_factory import AgentQualityFactory
    case=_NativeApplicationCase(tmp_path/"native")
    accepted=case.approve(case.command())["budget_acknowledgement"];before=case.capture()
    journal_before=case.budget_journal_path.read_bytes();factory_calls=[];errors=[]
    original=AgentQualityFactory.build
    def changed_baseline(factory,**options):
        actual=original(factory,**options)
        baseline={**actual.metadata["canonical_budget"],"model_tokens_total":25000}
        factory_calls.append({"native_catalog_baseline":actual.metadata["canonical_budget"],"changed_constructor_baseline":baseline})
        return replace(actual,metadata={**actual.metadata,"canonical_budget":baseline})
    with monkeypatch.context() as patch:
        patch.setattr(AgentQualityFactory,"build",changed_baseline)
        runtime=None
        try:
            runtime=case.open_runtime()
            # Native Factory Budget provider is lazy. Force the actual existing
            # account constructor/read while the changed catalog is active.
            runtime.supervisor.workflow_engine.budget_runtime(runtime.task.task_id,runtime.task.run_id,
                require_existing_account=True).canonical_state()
        except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
        finally:
            if runtime is not None:case.close_runtime(runtime)
    assert factory_calls and errors
    assert case.budget_journal_path.read_bytes()==journal_before
    after=case.receipt(["BT-12"],"native_actual_cold_ctor_25000",acknowledgements=[accepted],exceptions=errors,
        observations={"actual_cold_factory_constructor_calls":factory_calls,"native_after_grant":before,
            "old_account_raw_sha256":hashlib.sha256(journal_before).hexdigest(),"baseline_change_was_negative_attack_only":True})
    for name in ("run","current_children","budget","task_state"):assert after[name]==before[name]
    assert after["budget"]["limits"]["model_tokens_total"]==24000 and len(after["budget"]["grant_history"])==1
    assert after["physical_count"]==before["physical_count"] and after["engine_entry_count"]==0


def test_native_actual_settlement_overrun_at_approved_cursor_retains_full_charge_and_other_caps(tmp_path):
    from apps.enterprise_document.schemas.agent_rag_v1.budgets import BudgetReservationRefSchema,CanonicalBudgetVectorSchema,CANONICAL_BUDGET_DIMENSIONS
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
    enabled=threading.Event();observations=[]
    def configure(engine,case):
        original=engine.resume_budget_document
        def actual_settlement(workflow,state,continuation):
            if enabled.is_set() and not observations:
                manager=engine.budget_runtime(state.task_id,state.run_id,require_existing_account=True)
                before=manager.canonical_state();amount=before.remaining.model_tokens_total+1
                assert before.remaining.model_tokens_total>0
                ref=BudgetReservationRefSchema(reservation_id="bt-native-actual-overrun-"+uuid4().hex,
                    task_id=state.task_id,run_id=state.run_id,reservation_kind="local_test_owned_actual_settlement",
                    owner_occurrence_ref="fixture://actual-approved-cursor/overrun",scope_ref="run:"+state.run_id,
                    operation_ref="fixture://actual-approved-cursor/overrun")
                reserved=manager.reserve(ref,CanonicalBudgetVectorSchema(model_tokens_total=1),expected_revision=before.revision)
                settled=manager.consume(ref.reservation_id,CanonicalBudgetVectorSchema(model_tokens_total=amount),
                    expected_revision=manager.canonical_state().revision)
                after=manager.canonical_state()
                assert after.used.model_tokens_total==before.used.model_tokens_total+amount
                assert after.remaining.model_tokens_total==0 and after.used.model_tokens_total>after.effective_limits.model_tokens_total
                for name in CANONICAL_BUDGET_DIMENSIONS:
                    if name!="model_tokens_total":assert getattr(after.used,name)==getattr(before.used,name)
                for negative in (-1,):
                    with pytest.raises(ValueError):CanonicalBudgetVectorSchema(model_tokens_total=negative)
                next_ref=ref.model_copy(update={"reservation_id":ref.reservation_id+"-next","operation_ref":ref.operation_ref+"/next"})
                with pytest.raises(CanonicalBudgetCapacityExceeded) as denied:
                    manager.reserve(next_ref,CanonicalBudgetVectorSchema(model_tokens_total=1),expected_revision=after.revision)
                replay=manager.consume(ref.reservation_id,CanonicalBudgetVectorSchema(model_tokens_total=amount),expected_revision=after.revision)
                assert replay==settled and manager.canonical_state()==after
                observations.append({"scope":"TEST_OWNED_NATIVE_SETTLEMENT_NOT_PROVIDER_BILLING",
                    "before":before.model_dump(mode="json"),"reservation":reserved.model_dump(mode="json"),
                    "settlement":settled.model_dump(mode="json"),"settlement_replay":replay.model_dump(mode="json"),
                    "after":after.model_dump(mode="json"),"next_exact_native_denial":denied.value.native_denial.model_dump(mode="json")})
                raise RuntimeError("TEST_DIRECTED_NATIVE_ACTUAL_OVERRUN_COMPLETE")
            return original(workflow,state,continuation)
        engine.resume_budget_document=actual_settlement
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    accepted=case.approve(case.command())["budget_acknowledgement"];enabled.set()
    with pytest.raises(RuntimeError,match="TEST_DIRECTED_NATIVE_ACTUAL_OVERRUN"):case.resume(accepted)
    after=case.receipt(["BT-28"],"native_actual_settlement_overrun",acknowledgements=[accepted],
        observations={"actual_native_settlements":observations,"harness_directed_stop":True,"production_failure":False})
    assert observations and after["run"]["status"]=="running" and after["current_children"]==case.before["current_children"]
    assert after["physical_count"]==case.before["physical_count"] and after["budget"]["used"]==observations[0]["after"]["used"]


@pytest.mark.parametrize("spoof",["generic","fallback","settlement","missing_phase"])
def test_native_full_app_hold_immutable_failed_carrier_spoof_has_no_continuation_authority(tmp_path,spoof):
    from copy import deepcopy
    case=_NativeApplicationCase(tmp_path/"native");runtime=case.open_runtime();errors=[]
    try:
        owner=runtime.supervisor.workflow_engine.stage_service.target_post_write_composition.citation_grounding
        identity={"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,"section_id":case.wait.refusal.section_id}
        original_ref,original=owner.store.get_json_with_reference(case.wait.refusal.native_denial_ref,**identity)
        original_input=case.wait.refusal.input_ref
        input_ref,intent=owner.store.get_json_with_reference(original_input,**identity)
        forged=deepcopy(original)
        if spoof=="generic":forged["failure"]={"kind":"CANONICAL_BUDGET_FAILURE","reason":"generic-no-positive-phase"}
        elif spoof=="missing_phase":forged["failure"].pop("phase_proof",None)
        elif spoof=="fallback":forged["failure"]["phase_proof"].update(first_provider_attempt_ordinal=2,availability_fallback_from=["explicit-failed-provider"])
        else:forged["failure"]["phase_proof"].update(phase="POST_DISPATCH_SETTLEMENT",physical_dispatch_started=True)
        if "phase_proof" in forged["failure"]:
            proof=forged["failure"]["phase_proof"]
            proof["proof_sha256"]=canonical_json_sha256({key:value for key,value in proof.items() if key!="proof_sha256"})
        base=original_ref.artifact_ref.removesuffix("/result")+"/admission/forged-"+uuid4().hex
        # Fresh immutable Store records with actual content hashes, copied exact
        # native predecessor input. No old ref, raw receipt, wait/event is edited.
        attack_input=owner.store.put_json(intent,artifact_type=input_ref.artifact_type,
            schema_version=input_ref.schema_version,created_by="explicit-local-negative-test",
            artifact_ref=base+"/input",**identity)
        attack_result=owner.store.put_json(forged,artifact_type=original_ref.artifact_type,
            schema_version=original_ref.schema_version,created_by="explicit-local-negative-test",
            artifact_ref=base+"/result",**identity)
        try:owner.resolve_budget_refusal(receipt_ref=attack_result.artifact_ref,
            expected_sha256=attack_result.content_hash.removeprefix("sha256:"),identity=identity)
        except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
        assert errors
        # Trusted App cannot turn this forged receipt into a native Lifecycle wait.
        bad_command=case.command(wait_event_ref=attack_result.artifact_ref,
            wait_event_sha256=attack_result.content_hash.removeprefix("sha256:"))
        try:case.approve(bad_command)
        except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
    finally:case.close_runtime(runtime)
    after=case.receipt(["BT-20"],"native_full_hold_proof_spoof_"+spoof,exceptions=errors,observations={
        "original_native_result_ref":original_ref.model_dump(mode="json"),"copied_exact_native_input_ref":attack_input.model_dump(mode="json"),
        "new_immutable_attack_ref":attack_result.model_dump(mode="json"),"new_attack_payload":forged,
        "actual_negative_command":bad_command.model_dump(mode="json"),"boundary":"NATIVE_IMMUTABLE_PHASE_RESOLVER_AND_TRUSTED_APP_WAIT_PROVENANCE"})
    for name in ("run","current_children","budget","task_state"):assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


@pytest.mark.parametrize("decision",["approved","rejected"])
def test_native_budget_entry_rejects_human_review_decision_shape_and_old_review_api(tmp_path,decision):
    case=_NativeApplicationCase(tmp_path/"native");errors=[]
    wrong=case.command().model_copy(update={"decision":decision})
    ack=case.approve(wrong)["budget_acknowledgement"]
    assert ack["decision"]=="REJECTED" and ack["grant_ref"] is None
    try:case.service.resume_run(**case.arguments,decision=decision,input_snapshot_version=case.wait.cursor.input_snapshot_version)
    except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
    assert errors
    after=case.receipt(["BT-29"],"native_human_review_misroute_"+decision,acknowledgements=[ack],exceptions=errors,
        observations={"actual_typed_wrong_purpose_command":wrong.model_dump(mode="json"),
            "reverse_native_human_graph_pause":"SOURCE_BOUND_UNREACHABLE_NONLITERAL_NOT_INVENTED"})
    for name in ("run","current_children","budget","task_state"):assert after[name]==case.before[name]
    assert after["physical_count"]==case.before["physical_count"] and after["engine_entry_count"]==0


# PREPARED_NOT_EXPORTED_NOT_PARSED_NOT_RUN. Fresh Windows actual App workers.
# No source mutation/recovery, no lock-release => no-effect inference.
def _application_precise_boundary_child(connection,arguments,command_wire,grant,cut):
    from bootstrap.supervisor_factory import SupervisorFactory
    from agent.supervisor_agent import BudgetTopupCommandSchema,_budget_native_process_identity
    from apps.enterprise_document.services.agent_rag_v1.budget_manager import BudgetManager
    from apps.enterprise_document.workflows.top_level_business_graph import phase2_checkpoint_identity
    physical=[];entries=[];claims=[];cut_done=False
    class BoundaryFactory(SupervisorFactory):
        def build(self,**options):
            supervisor=super().build(**options);engine=supervisor.workflow_engine
            pw=engine.stage_service.target_post_write_composition;owner=pw.citation_grounding
            manager=engine.budget_runtime(arguments["task_id"],arguments["run_id"],require_existing_account=True)
            def stop(extra=None,*,locked_append=False):
                nonlocal cut_done
                assert not cut_done;cut_done=True
                body={"stage":"ACTUAL_NATIVE_BOUNDARY_EXIT","cut":cut,"process":_budget_native_process_identity(),
                    "physical":list(physical),"engine_entries":list(entries),"native_claim_chain":list(claims),
                    "extra":extra or {},"budget_raw_sha256":hashlib.sha256(manager._canonical_log.path.read_bytes()).hexdigest(),
                    "budget_raw_bytes_hex":manager._canonical_log.path.read_bytes().hex()}
                if not locked_append:
                    native=engine.lifecycle_service.budget_preimage(arguments["run_id"])
                    body.update(native_run=native.run.model_dump(mode="json"),
                        native_current_children=[row.model_dump(mode="json") for row in native.current_children],
                        native_child_preimage_sha256=native.child_preimage_sha256,
                        native_budget=manager.canonical_state().model_dump(mode="json"))
                    checkpoint=engine.checkpointer.get_tuple(phase2_checkpoint_identity(options["workflow"],run_id=arguments["run_id"]))
                    body["checkpoint_config"]=checkpoint.config
                    body["checkpoint_values"]=checkpoint.checkpoint["channel_values"]
                else:
                    body["locked_owner_snapshot"]="READ_PROPOSED_ARGUMENT_AND_RAW_ONLY_NO_NESTED_BUDGET_LIFE_IPC"
                connection.send(_native_observation_wire(body));os._exit(64)
            invoke=owner.gateway.invoker.invoke
            def measured(request,model_name):
                raw=request.model_dump(mode="json")
                physical.append({"model_call_id":request.model_call_id,"request_sha256":canonical_json_sha256(raw),
                    "caller_agent":request.caller_agent,"model_name":model_name,"extra":raw["extra"]})
                if cut=="invoker_entry" and "/admission/" in raw["extra"].get("logical_operation_id",""):
                    stop({"actual_invoker_entry_request":raw,"provider_reply":"UNKNOWN_NO_REPLY"})
                return invoke(request,model_name)
            owner.gateway.invoker.invoke=measured
            original_resume=engine.resume_budget_document
            def counted(workflow,state,continuation):
                entries.append(continuation.model_dump(mode="json"));return original_resume(workflow,state,continuation)
            engine.resume_budget_document=counted
            native_put=supervisor._budget_put
            def app_put(store,ref,body,task,kind):
                actual=native_put(store,ref,body,task,kind)
                if kind=="budget_document_occurrence_claim":
                    native,payload=store.get_json_with_reference(ref,task_id=task.task_id,run_id=task.run_id)
                    claims.append({"reference":native.model_dump(mode="json"),"carrier":payload})
                if cut=="grant_before_resume" and kind=="budget_topup_approval_ack" and body["decision"]=="ACCEPTED":
                    stop({"actual_committed_approval_ack_ref":actual.model_dump(mode="json"),"actual_ack":body})
                return actual
            supervisor._budget_put=app_put
            resume_wait=engine.lifecycle_service.resume_budget_wait
            def native_resume_wait(*args,**kwargs):
                actual=resume_wait(*args,**kwargs)
                if cut=="resume_before_task":stop({"actual_resume_transition":_native_observation_wire(actual)})
                return actual
            engine.lifecycle_service.resume_budget_wait=native_resume_wait
            mark=supervisor.lifecycle.mark_running
            def native_mark(task):
                actual=mark(task)
                if cut=="task_before_checkpoint":
                    stop({"actual_task_after_mark":supervisor.lifecycle.task_manager.get_task(task.task_id).model_dump(mode="json")})
                return actual
            supervisor.lifecycle.mark_running=native_mark
            checkpoint_put=engine.checkpointer.put
            def native_checkpoint_put(config,checkpoint,metadata,new_versions):
                actual=checkpoint_put(config,checkpoint,metadata,new_versions)
                values=checkpoint.get("channel_values",{});payload=values.get("scheme_generation_payload") or {}
                if (cut=="checkpoint_before_citation_ack" and isinstance(payload,dict)
                    and payload.get("budget_document_continuation") and payload.get("pause_purpose") is None
                    and values.get("business_route")=="continue"):
                    stop({"actual_checkpoint_put_return":actual,"actual_committed_checkpoint":checkpoint})
                return actual
            engine.checkpointer.put=native_checkpoint_put
            append=manager._append_canonical_state_unlocked
            def native_append(*,event_type,state):
                if event_type=="topup_granted" and cut in {"append_before","append_after","append_torn"}:
                    if cut=="append_before":stop({"proposed_uncommitted_state":state.model_dump(mode="json")},locked_append=True)
                    if cut=="append_torn":
                        serialized=manager._serialize_canonical_event(event_type=event_type,state=state).encode("utf-8")
                        assert b"\n" not in serialized
                        with manager._canonical_log.path.open("ab") as stream:
                            stream.write(serialized[:len(serialized)//2]);stream.flush();os.fsync(stream.fileno())
                        stop({"proposed_uncommitted_state":state.model_dump(mode="json"),
                            "uncommitted_tail_length":len(serialized)//2},locked_append=True)
                    actual=append(event_type=event_type,state=state)
                    stop({"committed_state_argument":state.model_dump(mode="json")},locked_append=True)
                    return actual
                return append(event_type=event_type,state=state)
            manager._append_canonical_state_unlocked=native_append
            reserve=manager.reserve
            def native_reserve(reservation,requested,**kwargs):
                actual=reserve(reservation,requested,**kwargs)
                if (cut=="token_reserved" and entries
                    and reservation.reservation_kind=="model_provider_attempt_tokens"
                    and reservation.operation_ref.startswith("model_call_citation_association_")):
                    stop({"actual_native_token_reservation":actual.model_dump(mode="json")})
                return actual
            manager.reserve=native_reserve
            citation_put=owner.store.put_json
            def native_citation_put(value,**kwargs):
                ref=kwargs.get("artifact_ref","")
                target=ref.startswith("artifact://citation-association/") and "/admission/" in ref
                suffix="before_ack" if ref.endswith("/ack") else "before_input" if ref.endswith("/input") else "before_result"
                if target and cut=="citation_"+suffix:stop({"not_yet_written_ref":ref,"not_yet_written_kind":kwargs["artifact_type"]})
                actual=citation_put(value,**kwargs)
                suffix="after_ack" if ref.endswith("/ack") else "after_input" if ref.endswith("/input") else "after_result"
                if target and cut=="citation_"+suffix:
                    read,payload=owner.store.get_json_with_reference(ref,task_id=arguments["task_id"],run_id=arguments["run_id"])
                    assert actual==read
                    stop({"actual_native_admission_ref":actual.model_dump(mode="json"),"actual_native_admission_payload":payload})
                return actual
            owner.store.put_json=native_citation_put
            finalize=pw.validation_store.finalize_validator_execution
            def native_finalize(event,record,**kwargs):
                target=record.validator_id==owner.VALIDATOR_ID and record.terminal_execution_state.value=="COMPLETED"
                if target and cut=="validator_before_finalize":
                    stop({"proposed_not_committed_validator_record":record.model_dump(mode="json"),
                        "proposed_not_committed_terminal_event":event.model_dump(mode="json")})
                if not target or cut!="validator_transaction_precommit":return finalize(event,record,**kwargs)
                from contextlib import contextmanager
                transaction=pw.database.normal_write_transaction
                @contextmanager
                def before_native_commit(**transaction_options):
                    with transaction(**transaction_options) as connection:
                        yield connection
                        # All actual finalize inserts executed; native COMMIT has
                        # not occurred. Read no same DB connection in this cut.
                        stop({"proposed_uncommitted_validator_record":record.model_dump(mode="json"),
                            "transaction_state":"INSERTS_EXECUTED_BEFORE_NATIVE_COMMIT"})
                pw.database.normal_write_transaction=before_native_commit
                try:return finalize(event,record,**kwargs)
                finally:pw.database.normal_write_transaction=transaction
            pw.validation_store.finalize_validator_execution=native_finalize
            return supervisor
    root=Path(arguments["output_root"])
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",supervisor_model_name="fake_llm",
        enable_llm_routing=False,trace_enabled=True,data_capture_enabled=True)
    try:
        service=MainlineApplicationService(supervisor_factory=BoundaryFactory())
        common={**arguments,"project_root":Path(arguments["project_root"]),"output_root":root,"settings":settings,
            "allow_demo_defaults":True,"enable_agent_self_rag":False}
        if grant is None:
            response=service.topup_budget_run(**common,command=BudgetTopupCommandSchema.model_validate(command_wire),continue_document=False)
        else:response=service.resume_budget_run(**common,accepted_grant_ref=grant["grant_ref"],accepted_grant_sha256=grant["grant_sha256"])
        connection.send({"stage":"TARGET_NATIVE_CUT_NOT_REACHED","response":response,"physical":physical,"engine_entries":entries})
    except BaseException as error:
        connection.send({"stage":"ACTUAL_PRE_CUT_EXCEPTION","error":type(error).__name__+": "+str(error),"physical":physical,"engine_entries":entries})
    finally:connection.close()


def _start_native_precise_boundary(case,command,grant,cut):
    context=multiprocessing.get_context("spawn");parent,child=context.Pipe()
    args={name:str(case.arguments[name]) for name in ("project_root","output_root","task_id","run_id")}
    worker=context.Process(target=_application_precise_boundary_child,args=(child,args,command.model_dump(mode="json"),grant,cut))
    worker.start();child.close();case.test_workers.append(worker)
    result=_native_worker_message(parent,worker,case,"native_boundary_"+cut)
    try:
        _native_worker_join(worker,case,"native_boundary_exit_"+cut,64)
    finally:
        # Save only the child's exact native boundary facts before any outcome
        # oracle or parent owner read; parent reads can recover a torn suffix.
        parent.close()
        from agent.supervisor_agent import _budget_original_process_closed
        native_process=result.get("process")
        closure={"status":"UNKNOWN_NATIVE_PROCESS_IDENTITY_ABSENT"}
        if native_process is not None:
            try:
                closure=_budget_original_process_closed({"claimant_pid":native_process["pid"],
                    "claimant_creation_filetime":native_process["creation_filetime"]})
            except (RuntimeError,KeyError) as error:
                closure={"status":"UNPROVEN","error":type(error).__name__+": "+str(error)}
        body=dict(result);raw_hex=body.pop("budget_raw_bytes_hex",None)
        raw_observation={"status":"UNKNOWN_NATIVE_RAW_ABSENT"}
        if raw_hex is not None:
            data=bytes.fromhex(raw_hex)
            raw_path=case.root.parent/("native_boundary_"+cut+"_pre_oracle_budget_raw.bin")
            with raw_path.open("xb") as stream:stream.write(data)
            raw_observation={"path":str(raw_path),"sha256":hashlib.sha256(data).hexdigest(),
                "bytes":len(data),"declared_native_sha256":result.get("budget_raw_sha256")}
        artifact={"schema_version":"bt_actual_native_boundary_pre_oracle_observation_v1",
            "cut":cut,"actual_child_result":body,"actual_worker_pid":worker.pid,
            "actual_worker_exitcode":worker.exitcode,"actual_native_process_closure":closure,
            "native_budget_raw_snapshot":raw_observation,
            "parent_owner_reads_before_capture":[],"semantic_noeffect_authority":"NO"}
        path=case.root.parent/("native_boundary_"+cut+"_pre_oracle.json")
        with path.open("x",encoding="utf-8") as stream:
            json.dump(artifact,stream,ensure_ascii=False,indent=2)
    assert result["stage"]=="ACTUAL_NATIVE_BOUNDARY_EXIT" and result["cut"]==cut,result
    assert worker.exitcode==64
    return result


@pytest.mark.parametrize("cut",["append_before","append_after","append_torn"])
def test_native_windows_actual_app_grant_append_boundary_reopens_committed_prefix_once(tmp_path,cut):
    case=_NativeApplicationCase(tmp_path/"native");command=case.command()
    result=_start_native_precise_boundary(case,command,None,cut)
    crashed_bytes=bytes.fromhex(result["budget_raw_bytes_hex"])
    assert crashed_bytes.startswith(case.original_budget_bytes) and result["physical"]==[] and result["engine_entries"]==[]
    # Save the exact fresh torn bytes before any native owner committed-prefix
    # recovery. Immutable original historical prefix is never edited/truncated.
    (tmp_path/(cut+"_pre_reopen_budget_raw.bin")).write_bytes(crashed_bytes)
    if cut=="append_torn":assert not crashed_bytes.endswith(b"\n")
    before=case.capture()
    assert len(before["budget"].get("grant_history",[]))==(1 if cut=="append_after" else 0)
    accepted=case.approve(command)["budget_acknowledgement"]
    replay=case.approve(command)["budget_acknowledgement"]
    alias=case.approve(case.command(approval_id="cold-alias"))["budget_acknowledgement"]
    assert accepted==replay and alias["grant_ref"]==accepted["grant_ref"]
    after=case.receipt(["BT-06","BT-14"],"native_app_"+cut,acknowledgements=[accepted,replay,alias],observations={
        "actual_windows_native_boundary":result,"pre_reopen_raw_sha256":hashlib.sha256(crashed_bytes).hexdigest(),
        "actual_owner_reopen":before,"historical_complete_prefix_sha256":hashlib.sha256(case.original_budget_bytes).hexdigest(),
        "fresh_uncommitted_tail_recovery_only":cut=="append_torn"})
    assert len(after["budget"]["grant_history"])==1 and after["budget"]["revision"]==case.before["budget"]["revision"]+1
    assert after["run"]==case.before["run"] and after["current_children"]==case.before["current_children"]
    assert after["budget"]["used"]==case.before["budget"]["used"] and after["engine_entry_count"]==0
    assert after["physical_count"]==case.before["physical_count"]


@pytest.mark.parametrize("cut",["grant_before_resume","resume_before_task","task_before_checkpoint","checkpoint_before_citation_ack"])
def test_native_windows_actual_app_grant_resume_task_checkpoint_precise_boundaries(tmp_path,cut):
    case=_NativeApplicationCase(tmp_path/"native");command=case.command()
    accepted=None if cut=="grant_before_resume" else case.approve(command)["budget_acknowledgement"]
    result=_start_native_precise_boundary(case,command,accepted,cut)
    assert result["physical"]==[]
    if cut=="grant_before_resume":
        assert result["native_run"]["status"]=="paused"
        accepted=case.approve(command)["budget_acknowledgement"]
    else:assert result["native_run"]["status"]=="running"
    before=case.capture();completed=case.resume(accepted);first=case.capture();replay=case.resume(accepted)
    after=case.receipt(["BT-06","BT-16","BT-21"],"native_app_"+cut,
        acknowledgements=[accepted,completed["budget_acknowledgement"],replay["budget_acknowledgement"]],observations={
            "actual_windows_native_boundary":result,"native_before_cold_resume":before,"native_after_first_resume":first,
            "actual_child_physical":result["physical"],"actual_child_engine_entries":result["engine_entries"]})
    assert after["run"]["status"]=="success" and len(after["budget"]["grant_history"])==1
    assert after["physical_count"]-before["physical_count"]==1 and after["engine_entry_count"]-before["engine_entry_count"]==1
    assert after["budget"]==first["budget"] and after["physical_count"]==first["physical_count"]
    assert completed["budget_acknowledgement"]==replay["budget_acknowledgement"]


@pytest.mark.parametrize("cut",["citation_before_ack","citation_after_ack","citation_after_input","token_reserved","invoker_entry",
    "citation_after_result","validator_before_finalize","validator_transaction_precommit"])
def test_native_windows_full_application_citation_and_validator_uncertainty_boundaries(tmp_path,cut):
    from agent.supervisor_agent import _budget_original_process_closed
    from agent.runtime.graph_state import GraphStateSchema
    from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentContinuationSchema
    case=_NativeApplicationCase(tmp_path/"native");command=case.command()
    accepted=case.approve(command)["budget_acknowledgement"]
    result=_start_native_precise_boundary(case,command,accepted,cut)
    assert result["native_claim_chain"] and len(result["engine_entries"])==1
    claim=result["native_claim_chain"][-1]["carrier"];closed=_budget_original_process_closed(claim)
    before=case.capture();runtime=case.open_runtime();errors=[]
    try:
        engine=runtime.supervisor.workflow_engine;binding=BudgetDocumentContinuationSchema.model_validate(claim["identity"]["continuation"])
        slot=engine.stage_service.target_post_write_composition.citation_grounding.read_budget_document_admission(binding,
            identity={"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,"section_id":case.wait.refusal.section_id})
        inspection=engine.inspect(runtime.workflow,run_id=runtime.task.run_id)
        try:cursor=engine.validate_budget_execution_cursor(runtime.workflow,graph_state=GraphStateSchema.model_validate(inspection["graph_state"]),
            continuation=binding,original_claim=claim)
        except (ValueError,RuntimeError) as error:
            errors.append(type(error).__name__+": "+str(error));cursor={"phase":"INDETERMINATE","reason":"NATIVE_CURSOR_REJECTED"}
    finally:case.close_runtime(runtime)
    allowed=cut in {"citation_before_ack","citation_after_ack","citation_after_result"}
    if allowed:
        assert slot["phase"]==("COMPLETED" if cut=="citation_after_result" else "PRE_CITATION_INTENT_SAFE")
        assert cursor["phase"] in {"PRE_CITATION_INTENT_SAFE","COMPLETED","GRAPH_COMPLETED"}
        completed=case.resume(accepted);replay=case.resume(accepted)
        expected_new_physical=0 if cut=="citation_after_result" else 1
    else:
        # An active reservation/input-only/invoker-no-result or incomplete native
        # Grounding transaction is not silently converted into safe execution.
        assert cursor["phase"]=="INDETERMINATE"
        try:case.resume(accepted)
        except (ValueError,RuntimeError) as error:errors.append(type(error).__name__+": "+str(error))
        assert errors;completed=replay=None;expected_new_physical=0
    after=case.receipt(["BT-17","BT-21"],"native_full_app_"+cut,
        acknowledgements=[accepted]+([] if completed is None else [completed["budget_acknowledgement"],replay["budget_acknowledgement"]]),
        exceptions=errors,observations={"actual_windows_native_boundary":result,"positive_latest_claim_process_closure":closed,
            "actual_native_admission":slot,"actual_native_cursor":_native_observation_wire(cursor),"native_before_cold_resume":before,
            "actual_child_physical":result["physical"],"actual_child_engine_entries":result["engine_entries"]})
    assert after["physical_count"]-before["physical_count"]==expected_new_physical
    if not allowed:
        for name in ("run","current_children","budget","task_state"):assert after[name]==before[name]
        assert after["engine_entry_count"]==before["engine_entry_count"]
    if cut=="citation_after_result":
        assert after["budget"]["used"]==before["budget"]["used"] and after["budget"]["active_reserved"]==before["budget"]["active_reserved"]
    assert len(result["physical"])==(1 if cut in {"invoker_entry","citation_after_result","validator_before_finalize","validator_transaction_precommit"} else 0)
    rows=_native_old_owner_operation_rows(case.before,{**after,"physical_requests":[*after["physical_requests"],*result["physical"]]})
    assert rows and all(not row["new_matching_physical_entries"] and not row["changed_native_accounting_reservation_ids"] for row in rows)


# PREPARED_NOT_EXPORTED_NOT_PARSED_NOT_RUN. Genuine existing owner Section
# availability producer, authored native requirement-source revision stated.
def test_native_nonexportable_section_owner_control_is_withheld_without_fabricated_document_completion(tmp_path):
    from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema
    from apps.enterprise_document.schemas.post_write.currentness import ActiveComparisonMode
    enabled=threading.Event();observations=[]
    def configure(engine,case):
        original=engine.resume_budget_document
        def native_nonexport(workflow,state,continuation):
            if enabled.is_set() and not observations:
                engine.lifecycle_service.assert_run_committable(state.run_id)
                pw=engine.stage_service.target_post_write_composition
                material_ref,material=pw.citation_grounding.store.get_json_with_reference(
                    case.wait.cursor.budget_materialization_ref.artifact_ref,task_id=state.task_id,run_id=state.run_id)
                assert material_ref==case.wait.cursor.budget_materialization_ref
                raw=material["section_results"][0];source=case.wait.cursor.source_sections[0]
                old_ref=PostWriteControlDecisionRefSchema.model_validate(raw["control_decision_ref"])
                old=pw.control_store.resolve_exact(old_ref)
                gate=pw.gate_store.resolve_exact(old.delivery_gate_decision_ref)
                entry=pw.entry_store.resolve_exact(old.input_manifest.post_write_entry_ref)
                requirement=pw.requirement_store.resolve_exact(entry.identity.requirement_ref)
                with pw.database.run_write_guard_context(state.run_id):
                    new_requirement=pw.requirement_materializer.materialize(
                        semantic_contract_version=requirement.semantic_contract_version,
                        source_business_ref=requirement.source_business_ref+"/explicit-local-source-revision/"+uuid4().hex,
                        source_task_ref=requirement.source_task_ref,requirements=requirement.requirements)
                    assert new_requirement!=entry.identity.requirement_ref
                    native_requirement=pw.requirement_store.resolve_exact(new_requirement)
                    active=gate.currentness_proof.active_projection.model_copy(update={"requirement_ref":new_requirement})
                    generated=pw.currentness_resolver.prove(historical_entry_ref=old.input_manifest.post_write_entry_ref,
                        active_projection=active,comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY)
                    assert generated.currentness_state.value=="STALE"
                    new_control_ref=pw._control(task_id=state.task_id,run_id=state.run_id,
                        artifact_ref=old.input_manifest.artifact_state_ref,gate_ref=old.delivery_gate_decision_ref,
                        currentness=generated,generation_class=old.input_manifest.generation_class)
                    native_control=pw.control_store.resolve_exact(new_control_ref)
                    eligible=pw.section_eligibility.resolve(section_key=source.section.section_id,control_decision_ref=new_control_ref)
                assert native_control.fresh_currentness_proof==generated
                assert native_control.availability_projection.state.value=="WITHHELD"
                assert native_control.enforcement_action.value=="BLOCK"
                assert eligible.selection_state.value=="WITHHELD" and eligible.candidate is None
                assert eligible.reason_code=="SECTION_CURRENTNESS_NOT_CURRENT"
                assert pw.control_store.resolve_exact(old_ref)==old
                observations.append({"scope":"TEST_OWNED_NATIVE_REQUIREMENT_SOURCE_REVISION_OWNER_AVAILABILITY_ONLY",
                    "old_native_section_control":old.model_dump(mode="json"),"old_native_section_entry":entry.model_dump(mode="json"),
                    "new_native_requirement_ref":new_requirement.model_dump(mode="json"),
                    "new_native_requirement":native_requirement.model_dump(mode="json"),"generated_currentness":generated.model_dump(mode="json"),
                    "new_persisted_native_control_ref":new_control_ref.model_dump(mode="json"),
                    "new_persisted_native_control":native_control.model_dump(mode="json"),"native_eligibility":_native_observation_wire(eligible),
                    "frozen_graph_continuation_authority":"NONE_FROM_TEST_OWNED_SOURCE_REVISION"})
                raise RuntimeError("TEST_DIRECTED_NATIVE_NONEXPORT_SECTION_COMPLETE")
            return original(workflow,state,continuation)
        engine.resume_budget_document=native_nonexport
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    accepted=case.approve(case.command())["budget_acknowledgement"];enabled.set()
    with pytest.raises(RuntimeError,match="TEST_DIRECTED_NATIVE_NONEXPORT_SECTION"):case.resume(accepted)
    after=case.receipt(["BT-22","BT-23"],"native_actual_nonexport_section",acknowledgements=[accepted],
        observations={"actual_existing_owner_nonexport_rows":observations,"harness_directed_stop":True,"production_failure":False})
    assert observations and after["run"]["status"]=="running"
    assert after["current_children"]==case.before["current_children"]
    assert after["budget"]["used"]==case.before["budget"]["used"]
    assert after["physical_count"]==case.before["physical_count"]
    assert all(row["candidate"] is None for row in [observations[0]["native_eligibility"]])


# PREPARED_NOT_EXPORTED_NOT_PARSED_NOT_RUN. Passive exact native output capture.
def _native_successful_section_owner_outputs(case, *, expected_section_count=8):
    assert type(expected_section_count) is int and expected_section_count in (1,8)
    from apps.enterprise_document.schemas.post_write.foundation import PostWriteControlDecisionRefSchema,ValidationCompletionRefSchema
    runtime=case.open_runtime()
    try:
        pw=runtime.supervisor.workflow_engine.stage_service.target_post_write_composition
        owner=pw.citation_grounding;projection=owner.claim_projection
        native_ref,material=owner.store.get_json_with_reference(case.wait.cursor.budget_materialization_ref.artifact_ref,
            task_id=runtime.task.task_id,run_id=runtime.task.run_id)
        assert native_ref==case.wait.cursor.budget_materialization_ref
        if expected_section_count==1:
            assert case.expected_current_child_count==1
            assert len(case.wait.current_children)==1
            assert len(case.wait.cursor.source_sections)==1
            assert len(material["section_results"])==1
        sections=[]
        for source,raw in zip(case.wait.cursor.source_sections,material["section_results"]):
            writer=pw.writer_execution_store.resolve_writer_execution(source.writer_execution_ref)
            invocation=pw.writer_execution_store.resolve_writer_generation_invocation(source.writer_generation_invocation_ref)
            native_control=pw.control_store.resolve_exact(PostWriteControlDecisionRefSchema.model_validate(raw["control_decision_ref"]))
            completion_ref=ValidationCompletionRefSchema.model_validate(raw["validation_completion_ref"])
            completion=pw.validation_store.resolve_completion(completion_ref)
            executions=[]
            for item in completion.items:
                record=pw.validation_store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
                    expected_item_ref=item.expected_validation_item_ref)
                executions.append({"item":item.model_dump(mode="json"),
                    "native_record":record.model_dump(mode="json") if record else None,
                    "native_facts":[pw.validation_store.resolve_fact(ref).model_dump(mode="json") for ref in record.produced_fact_refs] if record else [],
                    "native_intermediates":[pw.validation_store.resolve_intermediate(ref) for ref in record.intermediate_refs] if record else []})
            verifications=[]
            for ref in source.canonical_claim_verification_refs:
                verification=projection.claim_runtime.resolve_verification_result(ref)
                support=projection.support.runtime.resolve_support(verification.claim_evidence_support_ref)
                bindings=projection.claim_runtime.resolve_binding_facts(verification.binding_facts_ref)
                verifications.append({"native_verification":verification.model_dump(mode="json"),
                    "native_support":support.model_dump(mode="json"),"native_bindings":bindings.model_dump(mode="json")})
            candidate_receipts=[]
            wanted={"claim_support_input","claim_support_candidate","canonical_verifier_input","canonical_verifier_request",
                "canonical_verifier_response","canonical_verifier_candidate","citation_association_input","citation_association_result"}
            for store in {projection.support.store,projection.verifier.store,owner.store}:
                for ref in store.list_references():
                    if (ref.task_id!=runtime.task.task_id or ref.run_id!=runtime.task.run_id
                        or ref.section_id!=source.section.section_id or ref.artifact_type not in wanted):continue
                    native,payload=store.get_json_with_reference(ref.artifact_ref,task_id=ref.task_id,run_id=ref.run_id,
                        section_id=ref.section_id,attempt_id=ref.attempt_id,input_snapshot_version=ref.input_snapshot_version)
                    assert native==ref
                    candidate_receipts.append({"reference":native.model_dump(mode="json"),"payload":payload})
            candidate_receipts.sort(key=lambda row:row["reference"]["artifact_ref"])
            sections.append({"section_id":source.section.section_id,"exact_writer_ref":source.writer_execution_ref.model_dump(mode="json"),
                "exact_writer_result":writer.model_dump(mode="json"),"exact_writer_invocation":invocation.model_dump(mode="json"),
                "native_section_control":native_control.model_dump(mode="json"),"native_completion_ref":completion_ref.model_dump(mode="json"),
                "native_completion":completion.model_dump(mode="json"),"prerequisite_validator_executions":executions,
                "native_verification_support_outputs":verifications,"exact_candidate_output_receipts":candidate_receipts})
        if expected_section_count==8:
            assert len(sections)==8
        else:
            assert len(sections)==1
        return {"native_materialization_ref":native_ref.model_dump(mode="json"),"sections":sections}
    finally:case.close_runtime(runtime)


def test_native_pause_owner_rejects_full_child_preimage_changed_without_run_revision(tmp_path):
    enabled=threading.Event();observations=[];native_receipts=[]
    def configure(engine,case):
        original=engine.resume_budget_document
        def stale_owner_pause(workflow,state,continuation):
            if enabled.is_set() and not observations:
                life=engine.lifecycle_service;outside=life.budget_preimage(state.run_id)
                assert outside.run.status.value=="running"
                prior=outside.current_children[0]
                opened=life.open_attempt(state.run_id,prior.identity.attempt_scope_ref,
                    prior.identity.input_snapshot_version,attempt_id="bt-native-stale-pause-child-"+uuid4().hex)
                current=life.budget_preimage(state.run_id)
                assert current.run==outside.run and current.current_children!=outside.current_children
                errors=[];pause_error=None
                try:life.pause_for_budget(state.run_id,current.run.lifecycle_revision,
                    expected_current_children=outside.current_children,operation_key="bt-native-stale-preimage-pause-"+uuid4().hex,
                    wait_ref=case.pause.wait_ref,wait_sha256=case.pause.wait_sha256,
                    semantic_operation_ref=case.pause.semantic_operation_ref,semantic_input_sha256=case.pause.semantic_input_sha256,
                    artifact_version=case.pause.artifact_version)
                except (ValueError,RuntimeError) as error:
                    pause_error=error
                    errors.append(type(error).__name__+": "+str(error))
                after_owner=life.budget_preimage(state.run_id)
                observations.append({"outside_full_preimage":_native_preimage_wire(outside),
                    "actual_native_opened_child":opened.model_dump(mode="json"),"current_full_preimage":_native_preimage_wire(current),
                    "actual_owner_pause_errors":errors,"run_revision_unchanged":current.run==outside.run,
                    "actual_owner_pause_error_type":None if pause_error is None else type(pause_error).__name__,
                    "actual_owner_pause_error_message":None if pause_error is None else str(pause_error),
                    "owner_after_rejection":_native_preimage_wire(after_owner)})
                # Save exact native controls/error before evaluating the targeted oracle.
                native_receipts.append(case.receipt(["BT-10","BT-11"],"native_full_child_owner_pause_fence",
                    acknowledgements=[accepted],exceptions=errors,
                    observations={"actual_native_owner_full_set_checks":observations,
                        "harness_directed_stop":True,"production_failure":False}))
                from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleRevisionConflict
                assert isinstance(pause_error,LifecycleRevisionConflict)
                assert str(pause_error)=="budget complete current Child preimage conflict"
                assert errors and after_owner==current
                raise RuntimeError("TEST_DIRECTED_NATIVE_FULL_CHILD_PAUSE_FENCE_COMPLETE")
            return original(workflow,state,continuation)
        engine.resume_budget_document=stale_owner_pause
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure)
    accepted=case.approve(case.command())["budget_acknowledgement"];enabled.set()
    with pytest.raises(RuntimeError,match="TEST_DIRECTED_NATIVE_FULL_CHILD_PAUSE_FENCE"):case.resume(accepted)
    assert len(native_receipts)==1
    after=native_receipts[0]
    assert observations and after["run"]["status"]=="running"
    assert after["run"]==observations[0]["current_full_preimage"]["run"]
    assert after["current_children"]==observations[0]["current_full_preimage"]["current_children"]
    assert after["budget"]["used"]==case.before["budget"]["used"] and after["physical_count"]==case.before["physical_count"]
