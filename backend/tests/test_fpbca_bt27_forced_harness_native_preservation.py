"""Explicitly force the existing new App test harness timeout branch.

The independent held native App remains PAUSED with original24000 and no grant.
A separate fresh test-owned OS worker exercises the existing node runner after
its aggregate observation threshold. It performs no canonical business entry,
Provider or owner mutation. The real existing harness stops only that worker;
its actual INCOMPLETE receipt never supplies no-effect/retry authority.
"""
from __future__ import annotations
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time
from unittest.mock import patch
import pytest
import test_fpbca_bcd_budget_topup_application as app_tests
from test_fpbca_bcd_budget_topup_application import _NativeApplicationCase,_offline


def _once(path,body):
    data=(json.dumps(body,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
    with path.open("xb") as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    return {"path":str(path),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}


def _healthy_test_worker(pipe):
    from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionRunner
    from agent.supervisor_agent import _budget_native_process_identity
    runner=WorkflowNodeExecutionRunner(now_iso=lambda:"2026-10-08T00:00:00+00:00")
    def actual_work(control):
        time.sleep(.03)
        control.checkpoint()
        body={"schema_version":"bt27_actual_healthy_test_worker_v1","pid":os.getpid(),
            "process":_budget_native_process_identity(),"actual_control_metadata":control.metadata(),
            "actual_registered_worker_count":runner.active_worker_count,
            "canonical_business_entry":"NONE_TEST_WORKER_ONLY",
            "canonical_run_attempt_owner_access":"NONE",
            "physical_provider_or_settlement":"SOURCE_BOUND_N_A_THIS_HEALTHY_TEST_WORKER"}
        pipe.send(body)
        # The second IPC response is intentionally never produced. This is a
        # deterministic harness-only wait budget, not a production failure.
        import threading
        threading.Event().wait(30)
        control.checkpoint()
        return {"test_worker":"completed_after_observation"}
    try:
        outcome=runner.execute(actual_work,execution_id="bt27-healthy-fresh-test-worker",
            commit_scope_id="bt27-harness-only-test-scope",timeout_seconds=.005,
            thread_name="bt27-test-worker",attempt_succeeded=lambda value:True)
        pipe.send({"unexpected_returned_outcome":outcome.kind})
    finally:pipe.close()


def _native_custody(case):
    from application.run_workspace import RunWorkspace
    paths=(case.budget_journal_path,case.budget_journal_path.parent/"lifecycle.jsonl",
        RunWorkspace(case.root,case.summary["task_id"],case.summary["run_id"]).task_state_path,
        case.denial_path,case.wait_path)
    rows=[]
    for path in paths:
        data=Path(path).read_bytes()
        rows.append({"path":str(path),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)})
    return rows


def test_actual_forced_existing_harness_budget_preserves_canonical_pause_and_children(tmp_path):
    assert os.name=="nt"
    from agent.supervisor_agent import _budget_original_process_closed
    case=_NativeApplicationCase(tmp_path/"native")
    before=case.capture();raw_before=_native_custody(case)
    context=multiprocessing.get_context("spawn");parent,child=context.Pipe()
    worker=context.Process(target=_healthy_test_worker,args=(child,))
    worker.start();child.close();case.test_workers.append(worker)
    try:
        assert parent.poll(30),"fresh healthy test worker failed before harness boundary"
        ready=parent.recv()
        _once(tmp_path/"bt27_actual_test_worker_before_forced_harness.json",ready)
        assert ready["actual_control_metadata"]["deadline_exceeded"] is True
        assert ready["actual_control_metadata"]["cancel_requested"] is False
        assert ready["actual_registered_worker_count"]==1 and worker.is_alive()
        harness_before=set(tmp_path.glob("bt27_forced_*_harness_incomplete.json"))
        started=time.monotonic()
        with patch.object(app_tests,"NATIVE_APPLICATION_TEST_WAIT_SECONDS",.02):
            with pytest.raises(pytest.fail.Exception,match="TEST_TIME_BUDGET_EXHAUSTED / E2E_INCOMPLETE") as exhausted:
                app_tests._native_worker_message(parent,worker,case,"bt27_forced")
        elapsed=time.monotonic()-started
        harness_after=set(tmp_path.glob("bt27_forced_*_harness_incomplete.json"))
        created=harness_after-harness_before
        assert len(created)==1
        actual_path=created.pop();actual_raw=actual_path.read_bytes();actual_harness=json.loads(actual_raw)
        closure=_budget_original_process_closed({"claimant_pid":ready["process"]["pid"],
            "claimant_creation_filetime":ready["process"]["creation_filetime"]})
        after=case.capture();raw_after=_native_custody(case)
        body={"schema_version":"bt27_forced_harness_native_canonical_preservation_v1",
            "scope":"NEW_LOCAL_TEST_ONLY_FORCED_EXISTING_HARNESS_BRANCH; ORIGINAL_LIMITS_AND_98_IDS_UNCHANGED",
            "before":before,"after":after,"raw_native_before":raw_before,"raw_native_after":raw_after,
            "actual_worker_ready":ready,"actual_worker_pid":worker.pid,"actual_worker_exit_code":worker.exitcode,
            "positive_exact_fresh_process_closure":closure,"actual_harness_failure_type":type(exhausted.value).__name__,
            "actual_harness_failure_message":str(exhausted.value),"observed_wait_seconds":elapsed,
            "configured_new_fixture_observation_seconds":.02,"actual_harness_receipt":actual_harness,
            "actual_harness_receipt_custody":{"path":str(actual_path),"sha256":hashlib.sha256(actual_raw).hexdigest(),"bytes":len(actual_raw)},
            "new_grants":"NONE","new_business_engine_entry":"NONE",
            "semantic_noeffect_or_retry_authority":"NOT_GRANTED_BY_OS_EXIT_OR_HARNESS_TIMEOUT"}
        _once(tmp_path/"bt27_forced_harness_native_preservation_observation.json",body)
        assert actual_harness["test_status"]=="TEST_TIME_BUDGET_EXHAUSTED"
        assert actual_harness["campaign_status"]=="E2E_INCOMPLETE" and actual_harness["production_failure"]=="NO"
        assert actual_harness["harness_has_terminal_production_authority"] is False
        assert actual_harness["configuredTestSeconds"]==.02
        assert not worker.is_alive() and worker.exitcode is not None
        assert before["run"]["status"]==after["run"]["status"]=="paused"
        assert before["budget"]["limits"]["model_tokens_total"]==after["budget"]["limits"]["model_tokens_total"]==24000
        for key in ("run","current_children","budget","task_state","physical_requests","engine_entries"):
            assert after[key]==before[key]
        assert raw_after==raw_before
        assert not after["budget"].get("grant_history",[])
    finally:
        parent.close()
        if worker.is_alive():worker.terminate()
        worker.join(10)
