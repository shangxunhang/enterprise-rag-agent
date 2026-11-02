"""Prepared local native full App pending DOCUMENT_SUPPORT Windows hard cuts.

Independent finite fixture: one Section, three actual Fake RAG sources, two
Claims; immutable original 24000 baseline and exactly two explicit grants.
This module neither changes original98 IDs nor relabels owner-only cuts.
"""
from __future__ import annotations
from dataclasses import replace
import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest
from application.mainline_service import MainlineApplicationService
from core.config import get_settings
from core.persistence.crash_safe_jsonl import canonical_json_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from schemas.model import TokenUsageSchema
from tools.fake_rag_tool import FakeRAGTool
from test_fpbca_bcd_budget_topup_application import (
    _NativeApplicationCase, _bt03_dedicated_source_run, _bt03_model_semantic_producer,
    _bt03_three_source_producer, _native_observation_wire, _native_old_owner_operation_rows,
    _native_pending_command, _native_wait, _native_worker_message, _native_worker_join,
    _offline, _prephysical_charge_hook,
)
from test_fpbca_budget_document_support_grounding_native import _read_completed_document_carriers


def _once(path, body):
    data=(json.dumps(_native_observation_wire(body),ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    return {"path":str(path),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}


def _pending_support_case(tmp_path):
    charges=[];reported=[];allowances=[]
    initial=_prephysical_charge_hook(charges,initial=True)
    def configure(engine,case):
        initial(engine,case)
        invoker=engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
        original=invoker.invoke
        def first_allowance(request,model_name):
            response=original(request,model_name)
            if (request.extra.get("call_purpose")=="citation_association"
                    and "/admission/" in request.extra.get("logical_operation_id","") and not reported):
                assert len(allowances)==1
                response=response.model_copy(update={"token_usage":TokenUsageSchema(
                    prompt_tokens=allowances[0],completion_tokens=0,total_tokens=allowances[0])},deep=True)
                reported.append({"request":request.model_dump(mode="json"),"response":response.model_dump(mode="json")})
            return response
        invoker.invoke=first_allowance
    case=_NativeApplicationCase(tmp_path/"native",engine_hook=configure,
        run_builder=_bt03_dedicated_source_run,expected_current_child_count=1,native_run_id="bts_"+uuid4().hex[:12])
    first_packet,first_pause,first_state,required=_native_wait(case)
    assert first_state.limits.model_tokens_total==24000
    assert len(first_packet.current_children)==1 and first_state.remaining.model_tokens_total==0
    assert len(charges)==1
    allowances.append(required)
    first_command=_native_pending_command(case,approval_id="os-first-association",
        authorization_id="os-first-association-explicit",tokens=max(1,required-first_state.remaining.model_tokens_total))
    first_ack=case.approve(first_command)["budget_acknowledgement"]
    first_result=case.resume(first_ack)
    second_packet,second_pause,second_state,second_required=_native_wait(case)
    first_after=case.capture()
    assert second_packet.refusal.operation_kind=="DOCUMENT_SUPPORT"
    assert second_packet.current_children==first_packet.current_children
    assert first_result["canonical_budget_state"]["run"]["status"]=="paused"
    assert second_state.remaining.model_tokens_total==0 and len(reported)==1
    calls=first_after["physical_requests"][case.initial_physical_count:]
    assert len(calls)==1 and calls[0]["caller_agent"]=="CitationClaimAssociation"
    second_command=_native_pending_command(case,approval_id="os-support-finite-20000",
        authorization_id="os-support-explicit-20000",tokens=20000)
    second_ack=case.approve(second_command)["budget_acknowledgement"]
    facts={"scope":"INDEPENDENT_FULL_APP_DOCUMENT_SUPPORT_LOCAL_FAKE_NO_ORIGINAL98_SUBSTITUTION",
        "first_wait":first_packet,"first_pause":first_pause,"first_state":first_state,
        "first_command":first_command,"first_ack":first_ack,"first_result":first_result,
        "second_wait":second_packet,"second_pause":second_pause,"second_state":second_state,
        "second_required":second_required,"second_command":second_command,"second_ack":second_ack,
        "test_owned_native_resource_charges":charges,"reported_first_allowance":reported,
        "first_after":first_after}
    _once(tmp_path/"bt17_support_pre_dispatch_native_hold.json",facts)
    return case,second_packet,second_ack,facts


def _support_worker(pipe,arguments,grant,pending,cut):
    from bootstrap.supervisor_factory import SupervisorFactory
    from agent.supervisor_agent import _budget_native_process_identity
    physical=[];entries=[];claims=[];slot=None;hit=False
    class MeasuredFactory(SupervisorFactory):
        def build(self,**options):
            nonlocal slot
            supervisor=super().build(**options);engine=supervisor.workflow_engine
            citation=engine.stage_service.target_post_write_composition.citation_grounding
            owner=citation.support
            assert owner.model_gateway.invoker is citation.gateway.invoker
            manager=engine.budget_runtime(arguments["task_id"],arguments["run_id"],require_existing_account=True)
            identity={"task_id":arguments["task_id"],"run_id":arguments["run_id"],"section_id":pending["section_id"]}
            def stop(boundary,details):
                nonlocal hit
                assert not hit;hit=True
                # The Support semantic IPC may be held. Do not reenter its slot
                # getter or runtime replay here; only immutable Store/native Budget
                # and Lifecycle snapshots after their observed method returns.
                native=engine.lifecycle_service.budget_preimage(arguments["run_id"])
                raw=manager.storage_path.read_bytes()
                refs=[]
                for key in ("ack_ref","input_ref","result_ref"):
                    ref=slot[key]
                    path=owner.store._path_for_ref(ref)
                    if path.is_file():
                        reference,value=owner.store.get_json_with_reference(ref,**identity)
                        payload=path.read_bytes()
                        refs.append({"ref_field":key,"reference":reference.model_dump(mode="json"),"body":value,
                            "envelope_sha256":hashlib.sha256(payload).hexdigest(),"bytes":len(payload)})
                body={"stage":"ACTUAL_NATIVE_DOCUMENT_SUPPORT_OS_EXIT","scope":"ACTUAL_FULL_APP_PENDING_DOCUMENT_SUPPORT",
                    "cut":boundary,"process":_budget_native_process_identity(),"physical":list(physical),
                    "engine_entries":list(entries),"native_claim_chain":list(claims),"actual_pre_entry_slot":slot,
                    "exact_semantic_operation_ref":pending["semantic_operation_ref"],
                    "exact_new_occurrence":pending["semantic_operation_ref"]+"/admission/"+slot["admission_id"],
                    "native_admission_envelopes":refs,"native_run":native.run.model_dump(mode="json"),
                    "native_current_children":[x.model_dump(mode="json") for x in native.current_children],
                    "native_child_preimage_sha256":native.child_preimage_sha256,
                    "native_budget":manager.canonical_state().model_dump(mode="json"),
                    "budget_raw_sha256":hashlib.sha256(raw).hexdigest(),"budget_raw_hex":raw.hex(),"details":details}
                _once(Path(arguments["output_root"]).parent/"bt17_actual_support_child_cut.json",body)
                pipe.send(_native_observation_wire(body));os._exit(74)
            resume=engine.resume_budget_document
            def actual_entry(workflow,state,continuation):
                nonlocal slot
                # Native resume is durable, semantic Support lock not entered.
                slot=citation.read_budget_document_admission(continuation,identity=identity)
                assert slot["phase"]=="PRE_CITATION_INTENT_SAFE"
                entries.append({"task_id":arguments["task_id"],"run_id":arguments["run_id"],
                    "continuation":continuation.model_dump(mode="json"),"slot":slot})
                return resume(workflow,state,continuation)
            engine.resume_budget_document=actual_entry
            native_put=supervisor._budget_put
            def observe_claim(store,ref,body,task,kind):
                returned=native_put(store,ref,body,task,kind)
                if kind=="budget_document_occurrence_claim":
                    reference,carrier=store.get_json_with_reference(ref,task_id=task.task_id,run_id=task.run_id)
                    claims.append({"reference":reference.model_dump(mode="json"),"carrier":carrier})
                return returned
            supervisor._budget_put=observe_claim
            put=owner.store.put_json
            def store_cut(value,**kwargs):
                returned=put(value,**kwargs)
                if slot is not None:
                    matched={"ack":slot["ack_ref"],"input":slot["input_ref"],"candidate":slot["result_ref"]}
                    if cut in matched and kwargs.get("artifact_ref")==matched[cut]:
                        stop(cut,{"actual_put_return":returned.model_dump(mode="json"),"actual_stored_argument":value})
                return returned
            owner.store.put_json=store_cut
            reserve=manager.reserve
            def reservation_cut(reference,requested,**kwargs):
                returned=reserve(reference,requested,**kwargs)
                if slot is not None and cut=="token_reserved" and reference.reservation_kind=="model_provider_attempt_tokens":
                    intent=owner.store.get_json(slot["input_ref"],**identity)
                    if reference.operation_ref==intent["request"]["model_call_id"]:
                        stop(cut,{"actual_native_token_reservation":returned.model_dump(mode="json"),"actual_request":intent["request"]})
                return returned
            manager.reserve=reservation_cut
            invoke=owner.model_gateway.invoker.invoke
            def invoker_cut(request,model_name):
                value=request.model_dump(mode="json")
                physical.append({"model_call_id":request.model_call_id,"caller_agent":request.caller_agent,
                    "request_sha256":canonical_json_sha256(value),"model_name":model_name,"extra":value["extra"]})
                if slot is not None and cut=="invoker_entry" and request.extra.get("logical_operation_id")==pending["semantic_operation_ref"]+"/admission/"+slot["admission_id"]:
                    stop(cut,{"actual_invoker_request":value,"provider_response":"UNKNOWN_NO_REPLY"})
                return invoke(request,model_name)
            owner.model_gateway.invoker.invoke=invoker_cut
            append=owner.runtime.log.append_serialized
            def commit_cut(line):
                returned=append(line);event=json.loads(line)
                if slot is not None and cut=="support_committed" and event.get("event_type")=="support_committed":
                    record=event["record"]
                    if (record["evidence_target_set_ref"]==pending["pending_support"]["target"]
                            and record["evaluation_occurrence_ref"]==pending["semantic_operation_ref"]+"/admission/"+slot["admission_id"]):
                        stop(cut,{"actual_durable_support_commit_event":event})
                return returned
            owner.runtime.log.append_serialized=commit_cut
            return supervisor
    root=Path(arguments["output_root"])
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",supervisor_model_name="fake_llm",
        enable_llm_routing=False,trace_enabled=True,data_capture_enabled=True)
    with patch.object(FakeRAGTool,"run",_bt03_three_source_producer(FakeRAGTool.run)), \
            patch.object(FakeLLMClient,"generate",_bt03_model_semantic_producer(FakeLLMClient.generate)):
        try:
            response=MainlineApplicationService(supervisor_factory=MeasuredFactory()).resume_budget_run(
                **{**arguments,"project_root":Path(arguments["project_root"]),"output_root":root},settings=settings,
                allow_demo_defaults=True,enable_agent_self_rag=False,accepted_grant_ref=grant["grant_ref"],
                accepted_grant_sha256=grant["grant_sha256"])
            pipe.send({"stage":"NATIVE_DOCUMENT_SUPPORT_TARGET_UNREACHED","response":response,"physical":physical,"engine_entries":entries})
        except BaseException as error:
            pipe.send({"stage":"ACTUAL_PRE_CUT_EXCEPTION","error_type":type(error).__name__,"error":str(error),
                "physical":physical,"engine_entries":entries})
        finally:pipe.close()


def _matching_reservations(budget,operation):
    return [row for row in budget["reservations"] if row["reservation_ref"]["operation_ref"]==operation]


@pytest.mark.parametrize("cut",["ack","input","token_reserved","invoker_entry","candidate","support_committed"])
def test_native_full_app_pending_document_support_windows_os_exit_exact_cold_admission(tmp_path,cut):
    assert os.name=="nt"
    from agent.supervisor_agent import _budget_original_process_closed
    from agent.runtime.graph_state import GraphStateSchema
    from apps.enterprise_document.schemas.scheme_generation_graph import BudgetDocumentContinuationSchema
    with patch.object(FakeRAGTool,"run",_bt03_three_source_producer(FakeRAGTool.run)), \
            patch.object(FakeLLMClient,"generate",_bt03_model_semantic_producer(FakeLLMClient.generate)):
        case,pending,ack,fixture=_pending_support_case(tmp_path)
        completed_doc_before=_read_completed_document_carriers(case,pending.refusal)
        denied_path=case.store._path_for_ref(pending.refusal.native_denial_ref);denied_before=denied_path.read_bytes()
        old_envelope=json.loads(denied_before)
        old_content=base64.b64decode(old_envelope["content_base64"],validate=True)
        assert hashlib.sha256(old_content).hexdigest()==pending.refusal.native_denial_sha256
        old_failed_candidate=json.loads(old_content)
        old_logical_id=old_failed_candidate["failure"]["phase_proof"]["logical_reservation_id"]
        old_released=[row for row in fixture["first_after"]["budget"]["reservations"] if row["reservation_ref"]["reservation_id"]==old_logical_id]
        assert len(old_released)==1 and old_released[0]["status"]=="released"
        context=multiprocessing.get_context("spawn");parent,child=context.Pipe()
        arguments={k:str(case.arguments[k]) for k in ("project_root","output_root","task_id","run_id")}
        worker=context.Process(target=_support_worker,args=(child,arguments,ack,pending.refusal.model_dump(mode="json"),cut))
        worker.start();child.close();case.test_workers.append(worker)
        observed=_native_worker_message(parent,worker,case,"bt17_document_support_"+cut)
        _once(tmp_path/"bt17_support_worker_message_before_exit_oracle.json",{
            "actual_message":observed,"worker_pid":worker.pid,"exitcode_at_receive":worker.exitcode,
            "semantic_noeffect_authority":"NO","phase":"IPC_RECEIVED_BEFORE_EXIT_ORACLE"})
        _native_worker_join(worker,case,"bt17_document_support_exit_"+cut,74);parent.close()
        closure=_budget_original_process_closed({"claimant_pid":observed["process"]["pid"],
            "claimant_creation_filetime":observed["process"]["creation_filetime"]})
        _once(tmp_path/"bt17_support_pre_cold_native_process_observation.json",{
            "worker_pid":worker.pid,"actual_exit_code":worker.exitcode,"native_positive_process_closure":closure,
            "actual_child_boundary":observed,"parent_owner_reads_before_this_observation":[],"semantic_noeffect_authority":"NO"})
        assert observed["stage"]=="ACTUAL_NATIVE_DOCUMENT_SUPPORT_OS_EXIT" and observed["cut"]==cut
        assert observed["native_claim_chain"] and len(observed["engine_entries"])==1
        claim=observed["native_claim_chain"][-1]["carrier"]
        claim_closure=_budget_original_process_closed(claim)
        before=case.capture();runtime=case.open_runtime();errors=[]
        try:
            engine=runtime.supervisor.workflow_engine;citation=engine.stage_service.target_post_write_composition.citation_grounding
            binding=BudgetDocumentContinuationSchema.model_validate(claim["identity"]["continuation"])
            identity={"task_id":runtime.task.task_id,"run_id":runtime.task.run_id,"section_id":pending.refusal.section_id}
            slot=citation.read_budget_document_admission(binding,identity=identity,include_grounding_commit=True)
            inspection=engine.inspect(runtime.workflow,run_id=runtime.task.run_id)
            cursor=engine.validate_budget_execution_cursor(runtime.workflow,
                graph_state=GraphStateSchema.model_validate(inspection["graph_state"]),continuation=binding,original_claim=claim)
        finally:case.close_runtime(runtime)
        allowed=cut in {"ack","candidate","support_committed"}
        _once(tmp_path/"bt17_support_native_cold_classifier.json",{
            "cut":cut,"native_slot":slot,"native_cursor":cursor,"native_before_cold":before,
            "exact_claim":claim,"positive_latest_claim_process_closure":claim_closure})
        if allowed:
            assert cursor["phase"] in {"PRE_CITATION_INTENT_SAFE","COMPLETED","GRAPH_COMPLETED"}
            completed=case.resume(ack);first_completed=case.capture()
            replay=case.resume(ack);after_completed_replay=case.capture()
            _once(tmp_path/"bt17_support_exact_completed_replay_native_observation.json",{
                "actual_first_completed":first_completed,"actual_after_exact_replay":after_completed_replay,
                "actual_resume_results":[completed,replay]})
            for key in ("budget","physical_requests","engine_entries","run","current_children","task_state"):
                assert after_completed_replay[key]==first_completed[key]
        else:
            assert slot["phase"]=="INDETERMINATE" and cursor["phase"]=="INDETERMINATE"
            try:case.resume(ack)
            except (RuntimeError,ValueError) as error:errors.append(type(error).__name__+": "+str(error))
            assert errors
            completed=replay=None
        after=case.capture();doc_after=_read_completed_document_carriers(case,pending.refusal)
        occurrence=observed["exact_new_occurrence"]
        all_physical=[*after["physical_requests"],*observed["physical"]]
        matching=[row for row in all_physical if row["extra"].get("logical_operation_id")==occurrence]
        slot_intent=next((row["body"] for row in observed["native_admission_envelopes"] if row["ref_field"]=="input_ref"),None)
        operation=slot_intent["request"]["model_call_id"] if slot_intent is not None else (matching[0]["model_call_id"] if matching else None)
        reservation_before=_matching_reservations(before["budget"],operation) if operation else []
        reservation_after=_matching_reservations(after["budget"],operation) if operation else []
        logical_before=_matching_reservations(before["budget"],occurrence)
        logical_after=_matching_reservations(after["budget"],occurrence)
        old_released_after=[row for row in after["budget"]["reservations"] if row["reservation_ref"]["reservation_id"]==old_logical_id]
        old_operations=_native_old_owner_operation_rows(case.before,{**after,"physical_requests":all_physical})
        completed_association_operations=_native_old_owner_operation_rows(fixture["first_after"],{**after,"physical_requests":all_physical})
        facts={"scope":"ACTUAL_FULL_APP_DOCUMENT_SUPPORT_OS_EXIT_NOT_SECTION_OR_COOPERATIVE_CUT",
            "cut":cut,"fixture":fixture,"child":observed,"worker_exit":worker.exitcode,"process_closure":closure,
            "latest_claim_process_closure":claim_closure,"before_cold":before,"after_cold":after,
            "native_slot":slot,"native_cursor":cursor,"cold_result":completed,"exact_result_replay":replay,
            "errors":errors,"exact_pending_support_occurrence":occurrence,"matching_actual_physical_entries":matching,
            "exact_native_reservations_before":reservation_before,"exact_native_reservations_after":reservation_after,
            "exact_new_logical_reservations_before":logical_before,"exact_new_logical_reservations_after":logical_after,
            "old_failure_phase_proof_logical_reservation_id":old_logical_id,
            "exact_old_released_reservation_before":old_released,"exact_old_released_reservation_after":old_released_after,
            "consumed_terminal_revisions_after":[row["terminal_budget_revision"] for row in reservation_after if row["status"]=="consumed"],
            "completed_document_carriers_before":completed_doc_before,"completed_document_carriers_after":doc_after,
            "old_support_failure_sha256_before":hashlib.sha256(denied_before).hexdigest(),
            "old_support_failure_sha256_after":hashlib.sha256(denied_path.read_bytes()).hexdigest(),
            "old_completed_owner_operations":old_operations,
            "completed_association_and_old_owner_operations":completed_association_operations,
            "other_new_document_calls_are_separate_not_duplicates":True}
        _once(tmp_path/"bt17_pending_document_support_native_os_cut.json",facts)
        assert doc_after==completed_doc_before and denied_path.read_bytes()==denied_before
        assert old_released_after==old_released
        assert after["budget"]["limits"]["model_tokens_total"]==24000 and len(after["budget"]["grant_history"])==2
        assert old_operations and all(not row["new_matching_physical_entries"] and not row["changed_native_accounting_reservation_ids"] for row in old_operations)
        assert completed_association_operations and len(completed_association_operations)==len(old_operations)+1
        assert all(not row["new_matching_physical_entries"] and not row["changed_native_accounting_reservation_ids"] for row in completed_association_operations)
        assert sum(row["caller_agent"]=="CitationClaimAssociation" for row in all_physical[case.initial_physical_count:])==1
        assert len(matching)==(1 if cut in {"ack","invoker_entry","candidate","support_committed"} else 0)
        if cut in {"candidate","support_committed"}:
            assert reservation_after==reservation_before
            assert logical_after==logical_before
        if not allowed:
            for key in ("run","current_children","budget","task_state"):assert after[key]==before[key]
            assert after["physical_requests"]==before["physical_requests"] and after["engine_entries"]==before["engine_entries"]
