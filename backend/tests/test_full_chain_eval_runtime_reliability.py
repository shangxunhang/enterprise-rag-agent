"""Runtime counterexamples and real isolated timing fixtures; no inference."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionRunner
from apps.enterprise_document.services.post_write.persistence import PostWriteSqliteDatabase, ValidationStore, PostWritePersistenceIntegrityError
from core.diagnostics import DiagnosticConfig, DiagnosticSession
from core.persistence.interprocess_lock import InterProcessFileLock
from eval.full_chain.domains.runtime_reliability import (check_lifecycle_and_replay, compute_timeline, extract_runtime_observations, read_diagnostic_prefix, reconcile_usage, evaluate_runtime_edge)
from eval.full_chain.snapshot import SnapshotView


def observer(tmp_path,name="runtime",**kwargs):
    return DiagnosticSession(DiagnosticConfig(tmp_path/name,tmp_path,"runtime-fixture",flush_records=1,**kwargs))


def test_T01_physical_usage_dedup_does_not_hide_repeated_dispatch():
    one={"model_call_id":"logical","provider_attempt_id":"one","token_usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9},"reservation_id":"r1"}
    two={**one,"provider_attempt_id":"two","reservation_id":"r2"}
    result=reconcile_usage([one,one,two],dispatches=[one,one,two])
    assert result["logical_calls"]==1 and result["physical_attempts"]==2
    assert result["token_usage"]["total_tokens"]==18
    assert result["dispatch_count"]==3
    assert "REPEATED_PHYSICAL_DISPATCH" in result["findings"]
    assert result["cost"] is None
    assert reconcile_usage([one])["dispatch_proven"] is False


def test_T02_unknown_usage_cannot_be_zero_cost_or_release():
    result=reconcile_usage([{"model_call_id":"logical","provider_attempt_id":"one","outcome":"UNKNOWN","token_usage":{}}])
    assert result["cost_state"]=="UNMEASURED" and result["cost"] is None
    assert result["token_usage"]["total_tokens"] is None
    assert result["unknown_reservation_release_allowed"] is False


@pytest.mark.parametrize("record,code",[
    ({"budget_account_present":False},"ORIGINAL_BUDGET_ACCOUNT_MISSING"),
    ({"actual_predecessors":["writer","assessment"],"recovery_predecessors":["writer"]},"RECOVERY_PREDECESSOR_MISMATCH"),
    ({"task_status":"FAILED","run_status":"RUNNING"},"TASK_RUN_STATUS_DIVERGENCE"),
    ({"os_exited":True,"close_status":"UNKNOWN"},"OS_EXIT_DOES_NOT_PROVE_SAFE_CLOSE"),
    ({"healthy_terminal_read":True,"new_provider_calls":1},"HEALTHY_READ_SIDE_EFFECT"),
    ({"durable_memory_required":True,"locator_current":False},"MEMORY_LOCATOR_STALE"),
    ({"raw_payload":"damaged","raw_sha256":hashlib.sha256(b"original").hexdigest(),"metadata_hash":"unchanged"},"ARTIFACT_PAYLOAD_CORRUPTION"),
])
def test_T03_T08_readonly_runtime_counterexamples(record,code):
    frozen=json.dumps(record,sort_keys=True)
    assert code in check_lifecycle_and_replay(record)
    assert json.dumps(record,sort_keys=True)==frozen


def test_T06_healthy_read_and_authorized_publication_are_separate():
    assert check_lifecycle_and_replay({"healthy_terminal_read":True,"new_provider_calls":0,"new_business_commits":0,"new_cost":0})==[]
    assert check_lifecycle_and_replay({"publication_repair_authorized":True,"new_business_commits":1,"healthy_terminal_read":False})==[]


def test_T07_memory_off_is_NA_unknown_and_missing_runtime_stay_unmeasured():
    batch=extract_runtime_observations(SnapshotView.from_records({"runtime":[{"unit_id":"F03","durable_memory_required":False,"completeness":"COMPLETE"}]}),"subject")
    assert next(s for s in batch.stages if s.unit_id=="F03").measurement_state=="N/A"
    assert next(s for s in batch.stages if s.unit_id=="F01").measurement_state=="UNMEASURED"
    assert "T15:PENDING_RUN_AUTHORIZATION" in batch.limitations


def test_T09_real_CPU_and_FS_call_wait_are_distinct(tmp_path):
    captured=observer(tmp_path)
    with captured:
        with captured.span("cpu-loop","CPU",activity_proven=True):
            value=0
            deadline=time.perf_counter()+2
            cpu_begin=time.thread_time_ns()
            while time.thread_time_ns()-cpu_begin < 31_250_000 and time.perf_counter()<deadline:
                for n in range(10_000):
                    value += n*n
        with captured.span("controlled-file-wait","FS_API_ELAPSED",activity_proven=True):
            file=tmp_path/"controlled.txt"
            file.write_text(str(value))
            time.sleep(.02)  # declared controlled API fixture, not physical disk busy
            assert file.read_text()==str(value)
    prefix=read_diagnostic_prefix(captured.path,[tmp_path])
    rows=[e for e in prefix["events"] if e["event_type"]=="SPAN_RESULT"]
    cpu,fs=rows
    assert cpu["thread_cpu_ns"]>0
    assert fs["end_ns"]-fs["begin_ns"]>=15_000_000
    assert fs["thread_cpu_ns"] < fs["end_ns"]-fs["begin_ns"]
    timeline=compute_timeline(rows)
    assert timeline["cpu_is_wall_partition"] is False


def test_T09_actual_worker_queue_and_join_identity(tmp_path):
    runner=WorkflowNodeExecutionRunner(now_iso=lambda:"2026-10-05T00:00:00Z")
    captured=observer(tmp_path)
    with captured:
        result=runner.execute(lambda control: "fixed",execution_id="worker-identity",commit_scope_id="scope",timeout_seconds=1,thread_name="fixed-worker",attempt_succeeded=lambda value:value=="fixed")
        assert runner.wait_for_idle(.1)
    assert result.value=="fixed" and result.kind=="ok"
    events=read_diagnostic_prefix(captured.path,[tmp_path])["events"]
    rows=[e for e in events if e["event_type"] in {"WORKER_SUBMIT","WORKER_START","WORKER_FINISH"}]
    assert {r["event_type"] for r in rows}=={"WORKER_SUBMIT","WORKER_START","WORKER_FINISH"}
    assert {r["worker_identity"] for r in rows}=={"worker-identity"}
    assert any(e.get("category")=="WORKER_JOIN_WAIT" and e["event_type"]=="SPAN_RESULT" for e in events)


def test_T09_actual_two_process_lock_contention(tmp_path):
    lock_path=tmp_path/"lock.byte"
    ready=tmp_path/"holder.ready"
    child=tmp_path/"holder.py"
    child.write_text("from pathlib import Path\nimport sys,time\nfrom core.persistence.interprocess_lock import InterProcessFileLock\nwith InterProcessFileLock(sys.argv[1]):\n Path(sys.argv[2]).write_text('ready')\n time.sleep(.3)\n")
    process=subprocess.Popen([sys.executable,str(child),str(lock_path),str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    deadline=time.monotonic()+5
    while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
        time.sleep(.005)
    assert ready.exists()
    captured=observer(tmp_path)
    with captured, InterProcessFileLock(lock_path):
        pass
    stdout,stderr=process.communicate(timeout=5)
    assert process.returncode==0,(stdout,stderr)
    events=read_diagnostic_prefix(captured.path,[tmp_path])["events"]
    wait=next(e for e in events if e["event_type"]=="SPAN_RESULT" and e.get("category")=="LOCK_WAIT")
    assert wait["end_ns"]-wait["begin_ns"]>10_000_000
    assert str(lock_path) not in json.dumps(events)
    assert any(e["event_type"]=="LOCK_HELD_RESULT" for e in events)


def test_T09_actual_DB_api_and_transaction_scope_are_separate(tmp_path):
    database=PostWriteSqliteDatabase(tmp_path/"isolated.sqlite")
    captured=observer(tmp_path)
    with captured:
        with database.read_transaction() as db:
            assert db.execute("SELECT 1").fetchone()[0]==1
        with pytest.raises(PostWritePersistenceIntegrityError):
            ValidationStore(database).resolve_run_exact("missing")
    rows=read_diagnostic_prefix(captured.path,[tmp_path])["events"]
    results=[r for r in rows if r["event_type"]=="SPAN_RESULT"]
    assert any(r["category"]=="DB_API_ELAPSED" for r in results)
    assert any(r["category"]=="DB_TRANSACTION_SCOPE" for r in results)


def test_T10_provider_union_exclusive_and_unexplained_keep_clock_domains():
    rows=[{"span_id":"parent","begin_ns":0,"end_ns":100,"clock_domain":"a","category":"WORKFLOW"},
          {"span_id":"one","parent_span_id":"parent","begin_ns":10,"end_ns":50,"clock_domain":"a","category":"PROVIDER","activity_proven":True},
          {"span_id":"two","parent_span_id":"parent","begin_ns":30,"end_ns":70,"clock_domain":"a","category":"PROVIDER","activity_proven":True},
          {"span_id":"other","begin_ns":0,"end_ns":900,"clock_domain":"b","category":"PROVIDER"}]
    result=compute_timeline(rows,window={"begin_ns":0,"end_ns":100,"clock_domain":"a","candidate_edges":["X22"]})
    assert result["provider_union_ns"]=={"a":60,"b":900}
    assert result["spans"][0]["exclusive_wall_ns"]==40
    assert [(r["begin_ns"],r["end_ns"]) for r in result["unexplained_intervals"]]==[(0,10),(70,100)]
    assert result["critical_path_state"]=="UNKNOWN"
    assert result["cross_clock_alignment"]=="UNPROVEN"


def test_T10_critical_path_requires_actual_dependency_graph():
    rows=[{"span_id":"a","begin_ns":0,"end_ns":10,"clock_domain":"one"},{"span_id":"b","begin_ns":10,"end_ns":30,"clock_domain":"one"}]
    assert compute_timeline(rows)["critical_path_state"]=="UNKNOWN"
    assert compute_timeline(rows,dependencies=[("a","b")])["critical_path_ns"]==30
    assert compute_timeline(rows,dependencies=[("a","b"),("b","a")])["critical_path_state"]=="UNKNOWN"


def test_T11_pool_overcap_not_complete_empty_and_segment_quota_no_reset(tmp_path):
    captured=observer(tmp_path,max_pool_items=2)
    with captured:
        captured.emit_pool({"declared_count":3,"pool_id":"fixed"},[{}, {}, {}])
        child=captured.allocate_segment(tmp_path/"child","child",byte_quota=8192,event_quota=5)
        assert child.max_session_bytes==8192 and child.max_events==5
        with pytest.raises(ValueError):
            captured.allocate_segment(tmp_path/"third","third",byte_quota=8192,event_quota=5)
    prefix=read_diagnostic_prefix(captured.path,[tmp_path])
    assert prefix["completeness"]=="CAPTURE_INCOMPLETE"
    assert not any(e["event_type"]=="POOL_END" for e in prefix["events"])


def test_T11_actual_hard_exit_has_only_complete_unsealed_prefix(tmp_path):
    script=tmp_path/"hard_exit.py"
    root=tmp_path/"hard-exit"
    script.write_text("import os,sys\nfrom core.diagnostics import DiagnosticConfig,DiagnosticSession\ns=DiagnosticSession(DiagnosticConfig(sys.argv[1],sys.argv[2],'hard-exit',flush_records=1))\ns.emit({'event_type':'before-hard-exit'})\ns.flush()\nos._exit(7)\n")
    completed=subprocess.run([sys.executable,str(script),str(root),str(tmp_path)],capture_output=True,timeout=10)
    assert completed.returncode==7,completed.stderr
    prefix=read_diagnostic_prefix(root/"process-0.jsonl",[tmp_path])
    assert prefix["completeness"]=="CAPTURE_INCOMPLETE" and "NO_SEAL" in prefix["limitations"]
    assert any(r["event_type"]=="before-hard-exit" for r in prefix["events"])


@pytest.mark.parametrize("cap", ["events", "event-bytes", "queue-bytes", "session-bytes", "representation-total", "window"])
def test_T11_each_resource_cap_limits_actual_diagnostic_output(tmp_path,cap):
    from test_full_chain_eval_capture import gateway, FixedClient, request
    captured=observer(tmp_path)
    captured.flush()
    replacements={
        "events":{"max_events":2},
        "event-bytes":{"max_event_bytes":700},
        "queue-bytes":{"max_queue_bytes":700,"max_event_bytes":700},
        "session-bytes":{"max_session_bytes":5000,"max_representation_total_bytes":1000},
        "representation-total":{"max_representation_total_bytes":10},
        "window":{"window_s":.001},
    }[cap]
    captured.config=replace(captured.config,**replacements)
    with captured:
        if cap=="window":
            time.sleep(.01)
        if cap=="queue-bytes":
            with captured._condition:
                captured.emit({"event_type":"first","payload":"x"*120})
                captured.emit({"event_type":"second","payload":"x"*120})
        else:
            captured.emit({"event_type":"cap-test","payload":"x"*800})
            captured.emit({"event_type":"cap-test-2","payload":"x"*800})
        result=gateway(FixedClient()).generate(request())
        assert result.content=="A"
    assert captured.health()["lost_count"]>0
    prefix=read_diagnostic_prefix(captured.path,[tmp_path])
    assert prefix["completeness"]=="CAPTURE_INCOMPLETE"
    if cap=="session-bytes":
        assert sum(p.stat().st_size for p in captured.path.parent.rglob("*") if p.is_file()) <= captured.config.max_session_bytes


def test_T09_worker_cpu_sampling_reports_process_scope_without_thread_fiction(tmp_path):
    captured=observer(tmp_path,worker_sample_interval_s=.01)
    with captured:
        captured.emit({"event_type":"WORKER_START","worker_identity":"one"})
        time.sleep(.04)
        captured.emit({"event_type":"WORKER_FINISH","worker_identity":"one"})
    prefix=read_diagnostic_prefix(captured.path,[tmp_path])
    samples=[e for e in prefix["events"] if e["event_type"]=="PROCESS_SAMPLE"]
    assert samples and any(s["active_worker_count"]==1 for s in samples)
    assert all(s["cpu_scope"]=="PROCESS" and s["thread_cpu_assignment"]=="UNMEASURED" for s in samples)


def test_T11_close_cap_refuses_unbounded_wait_configuration(tmp_path):
    with pytest.raises(ValueError,match="two seconds"):
        DiagnosticConfig(tmp_path/"invalid",tmp_path,"invalid",close_timeout_s=2.01)


def test_T13_corrupted_frame_and_torn_tail_preserved(tmp_path):
    captured=observer(tmp_path)
    with captured:
        captured.emit({"event_type":"original"})
    damaged=tmp_path/"damaged.jsonl"
    damaged.write_bytes(captured.path.read_bytes().replace(b'"original"',b'"modified"')+b'{"torn":')
    before=damaged.read_bytes()
    prefix=read_diagnostic_prefix(damaged,[tmp_path])
    assert prefix["completeness"]=="CAPTURE_INCOMPLETE"
    assert "FRAME_HASH_MISMATCH" in prefix["limitations"]
    assert damaged.read_bytes()==before


def test_T14_unapproved_root_and_session_rejected(tmp_path):
    with pytest.raises(ValueError,match="authorized root"):
        DiagnosticSession(DiagnosticConfig(tmp_path.parent/"outside",tmp_path,"outside"))
    captured=observer(tmp_path)
    captured.close()
    with pytest.raises(ValueError,match="SESSION_PERMISSION"):
        read_diagnostic_prefix(captured.path,[tmp_path],expected_session_id="foreign-session")
    with pytest.raises(ValueError,match="PROCESS_PERMISSION"):
        read_diagnostic_prefix(captured.path,[tmp_path],allowed_process_ids=[-1])


def runtime_edge_fixture(edge_id,negative=False):
    """Integration reuse: two individually valid endpoints with one crossed edge."""
    if edge_id=="X22":
        source={"checkpoint_ref":"checkpoint-1","run_id":"run","attempt_id":"attempt","namespace":"target-v1","source_head":"head-1","route":"NORMAL_WRITE","graph_ref":"graph-1","actual_predecessors":["writer","assessment"],"dispatched_attempt_ids":["physical-1"],"unknown_attempt_ids":[]}
        target={k:v for k,v in source.items() if k not in {"actual_predecessors","dispatched_attempt_ids","unknown_attempt_ids"}}
        target.update(recovery_predecessors=["writer"] if negative else ["writer","assessment"],redispatched_attempt_ids=[])
    elif edge_id=="X23":
        row={"model_call_id":"logical","provider_attempt_id":"physical","token_usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9},"outcome":"RETURNED"}
        source={"physical_attempts":[row],"dispatch_count":1}
        target={"usage_attempts":[{**row,"reservation_state":"SETTLED","token_usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":90 if negative else 9}}],"dispatch_count":1}
    elif edge_id=="X24":
        source={"session_id":"session","operation_ref":"operation","expected_event_types":["SPAN_BEGIN","SPAN_RESULT"]}
        target={"session_id":"session","events":[{"event_type":"SPAN_BEGIN","operation_ref":"operation"},{"event_type":"SPAN_RESULT","operation_ref":"operation"}],"seal":{"lost_count":1 if negative else 0},"completeness":"CAPTURE_INCOMPLETE" if negative else "COMPLETE"}
    elif edge_id=="X28":
        source={"run_id":"run","attempt_id":"attempt","terminal_status":"SUCCESS","usage_finalized":True}
        target={"run_id":"run","attempt_id":"attempt","active_workers":1 if negative else 0,"unknown_calls":0,"drain_status":"IDLE","owned_close_status":"CLOSED","os_exited":True}
    elif edge_id=="X29":
        digest=hashlib.sha256(b"original").hexdigest()
        source={"source_ref":"original-owner","source_version":1,"raw_sha256":digest}
        target={**source,"durable_memory_required":True,"readback_payload":"damaged" if negative else "original","locator_current":True,"source_authorized":True}
    else:
        raise ValueError(edge_id)
    return source,target


@pytest.mark.parametrize("edge_id",["X22","X23","X24","X28","X29"])
@pytest.mark.parametrize("negative",[False,True])
def test_runtime_edges_compute_original_endpoint_obligations(edge_id,negative):
    source,target=runtime_edge_fixture(edge_id,negative)
    verdict=evaluate_runtime_edge(edge_id,source,target)
    assert verdict["measurement_state"]=="MEASURED"
    assert verdict["assertion_result"] is not negative
    row={"edge_id":edge_id,"completeness":"COMPLETE","assertion_result":True,"source":source,"target":target}
    batch=extract_runtime_observations(SnapshotView.from_records({"runtime_edges":[row]}),"subject")
    edge=next(e for e in batch.edges if e.edge_id==edge_id)
    assert edge.assertion_result is not negative  # input True cannot conceal a negative edge


def test_runtime_input_verdict_without_endpoints_cannot_prove_edge():
    batch=extract_runtime_observations(SnapshotView.from_records({"runtime_edges":[{"edge_id":"X23","assertion_result":True,"completeness":"COMPLETE"}]}))
    assert next(e for e in batch.edges if e.edge_id=="X23").measurement_state=="UNMEASURED"


def test_T10_real_gateway_and_legacy_unlabelled_completed_span_feed_timeline(tmp_path):
    from test_full_chain_eval_capture import gateway, FixedClient, request
    class FixedWaitClient(FixedClient):
        def generate(self,actual):
            time.sleep(.03)
            return super().generate(actual)
    captured=observer(tmp_path)
    with captured:
        gateway(FixedWaitClient()).generate(request())
    prefix=read_diagnostic_prefix(captured.path,[tmp_path])
    rows=[*prefix["events"],prefix["seal"]]
    completed=next(r for r in rows if r.get("event_type")=="SPAN_RESULT" and r.get("category")=="PROVIDER")
    legacy={**completed}
    legacy.pop("unit_id")
    legacy["operation_ref"]="legacy-owner-wait"
    before=dict(legacy)
    snapshot=SnapshotView.from_records({"diagnostic_events":[*rows,legacy]})
    batch=extract_runtime_observations(snapshot,"subject")
    assert legacy==before
    assert any(s.unit_id=="F06" and s.operation_id=="legacy-owner-wait" for s in batch.stages)
    timeline=compute_timeline(s.payload for s in batch.stages if s.payload.get("event_type") in {"SPAN_RESULT","SPAN_ERROR"})
    assert timeline["provider_union_ns"][completed["clock_domain"]]==completed["end_ns"]-completed["begin_ns"]
    assert timeline["provider_union_ns"][completed["clock_domain"]]>=15_000_000
    assert any(s.get("inclusive_wall_ns") is not None and s.get("thread_cpu_ns") is not None for s in timeline["spans"])


def test_X23_unknown_settlement_and_X22_unknown_redispatch_are_rejected():
    source,target=runtime_edge_fixture("X23")
    source["physical_attempts"][0]["outcome"]="UNKNOWN"
    target["usage_attempts"][0].update(reservation_state="RELEASED",actual_cost=0)
    assert evaluate_runtime_edge("X23",source,target)["assertion_result"] is False
    source,target=runtime_edge_fixture("X22")
    source["unknown_attempt_ids"]=["unknown"]
    target["redispatched_attempt_ids"]=["unknown"]
    assert evaluate_runtime_edge("X22",source,target)["assertion_result"] is False
