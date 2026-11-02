"""Actual Windows factory/graph BT15 cuts; local Fake, eight Sections, 24000.

No pre-entry protocol and no OS-exit-to-joined inference. Positive closure is
the immutable packet produced AFTER the actual runner joined and finalized
native control; the two earlier cuts deliberately lack that authority.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if __name__ == "__main__":
    assert ROOT == Path(r"D:\MyCode\rag-agent-shared")
    sys.dont_write_bytecode = True
    sys.path[:] = [p for p in sys.path if "rag-agent" not in str(p).lower() or "site-packages" in str(p).lower()]
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests"), str(ROOT)]
    roots = {item.name for item in (ROOT / "backend").iterdir() if item.is_dir()}
    class SourceGuard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] not in roots:
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is not None:
                locations = list(spec.submodule_search_locations or ())
                if spec.origin not in (None, "built-in", "frozen"):
                    locations.append(spec.origin)
                if any(not Path(item).resolve().is_relative_to(ROOT) for item in locations):
                    raise ImportError(f"Foreign Graph native source: {fullname}: {locations!r}")
            return spec
    sys.meta_path.insert(0, SourceGuard())
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("Graph process-cut external network is not authorized")
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = deny_network

import pytest
from application.mainline_service import MainlineApplicationService
from application.run_workspace import RunWorkspace
from apps.enterprise_document.workflows.top_level_business_graph import phase2_checkpoint_identity
from bootstrap.supervisor_factory import SupervisorFactory
from core.config import get_settings
from core.hashing import canonical_sha256
from test_phase2_runtime_safety_closure import _run_production_fake


def _json_once(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _append(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _physical_entries(case_root):
    path = case_root / "actual-invoker-entry.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _measure(engine, case_root, *, cold):
    invoker = engine.stage_service.target_post_write_composition.citation_grounding.gateway.invoker
    invoke = invoker.invoke
    lock = threading.Lock()
    def measured(request, model_name):
        raw = request.model_dump(mode="json")
        with lock:
            _append(case_root / "actual-invoker-entry.jsonl", {
                "pid": os.getpid(), "model_name": model_name,
                "model_call_id": request.model_call_id, "request_sha256": canonical_sha256(raw),
                "extra": raw["extra"], "caller_agent": request.caller_agent,
            })
        return invoke(request, model_name)
    invoker.invoke = measured
    if cold:
        run_stage = engine._run_stage
        def observed_stage(runtime_state, **kwargs):
            _append(case_root / "cold-business-stage-entry.jsonl", {"pid":os.getpid(), "node":kwargs["node_name"]})
            return run_stage(runtime_state, **kwargs)
        engine._run_stage = observed_stage


def _artifact_bytes(store, task_id, run_id):
    result = {}
    for reference in store.list_references():
        if (reference.task_id, reference.run_id) != (task_id, run_id):
            continue
        content = store.get_bytes(reference.artifact_ref, task_id=task_id, run_id=run_id)
        result[reference.artifact_ref] = {
            "reference": reference.model_dump(mode="json"),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "envelope_sha256": hashlib.sha256(store._path_for_ref(reference.artifact_ref).read_bytes()).hexdigest(),
        }
    return result


def _snapshot(engine, workflow, task_id, run_id, output_root):
    life = engine.stage_service.target_post_write_composition.lifecycle_service
    preimage = life.budget_preimage(run_id)
    budget = engine.budget_runtime(task_id, run_id, require_existing_account=True).canonical_state()
    citation = engine.stage_service.target_post_write_composition.citation_grounding
    checkpoint = engine.checkpointer.get_tuple(phase2_checkpoint_identity(workflow, run_id=run_id))
    values = checkpoint.checkpoint["channel_values"] if checkpoint is not None else {}
    workspace = RunWorkspace(output_root, task_id, run_id)
    raw_path = workspace.raw_capture_path
    current = life.current_budget_transition(run_id)
    return {
        "scope":"ACTUAL_WINDOWS_GRAPH_FACTORY_EIGHT_SECTIONS_FIXED_24000",
        "task_id":task_id, "run_id":run_id,
        "run":preimage.run.model_dump(mode="json"),
        "children":[row.model_dump(mode="json", round_trip=True) for row in preimage.current_children],
        "child_preimage_sha256":preimage.child_preimage_sha256,
        "budget":budget.model_dump(mode="json"),
        "native_budget_transition":({
            **{name:getattr(current,name) for name in ("kind","operation_key","event_ref","event_sha256",
                "wait_ref","wait_sha256","child_preimage_sha256","semantic_operation_ref",
                "semantic_input_sha256","artifact_version","wait_event_ref","wait_event_sha256",
                "grant_ref","grant_sha256","resume_occurrence_id")},
            "resulting_run":current.resulting_run.model_dump(mode="json"),
            "current_children":[row.model_dump(mode="json",round_trip=True) for row in current.current_children],
        } if current is not None else None),
        "active_background_workers_observed":engine.active_worker_count,
        "checkpoint_config":checkpoint.config if checkpoint is not None else None,
        "checkpoint_material_sha256":canonical_sha256(engine._budget_checkpoint_material(values)) if checkpoint is not None else None,
        "current_business_node":values.get("current_business_node"),
        "business_route":values.get("business_route"),
        "committed_node_refs":values.get("node_outputs", []),
        "source_epoch":engine._budget_source_epoch(),
        "artifacts":_artifact_bytes(citation.store, task_id, run_id),
        "raw_capture_exists":raw_path.exists(),
        "raw_capture_sha256":hashlib.sha256(raw_path.read_bytes()).hexdigest() if raw_path.exists() else None,
        "budget_journal_sha256":hashlib.sha256((workspace.runs_dir / "canonical_budget.jsonl").read_bytes()).hexdigest(),
    }


def _worker(case_root, run_id, cut):
    output_root = case_root / "native"
    assert not output_root.exists(), "Fresh native run is required; no historical recovery"
    state = {}
    def install(engine):
        _measure(engine, case_root, cold=False)
        citation = engine.stage_service.target_post_write_composition.citation_grounding
        life = engine.stage_service.target_post_write_composition.lifecycle_service
        execute = engine.execute
        def observed_execute(workflow, graph_state):
            state.update(workflow=workflow, task_id=graph_state.task_id, run_id=graph_state.run_id)
            return execute(workflow, graph_state)
        engine.execute = observed_execute
        put = citation.store.put_json
        pause = life.pause_for_budget
        def exit_at(**facts):
            snapshot = _snapshot(engine, state["workflow"], state["task_id"], state["run_id"], output_root)
            _json_once(case_root / "actual-cut.json", {
                "pid":os.getpid(), "cut":cut, "snapshot":snapshot,
                "physical_entries":_physical_entries(case_root), **facts,
            })
            os._exit(71)
        def selected_document_result(value, kwargs):
            ref = kwargs.get("artifact_ref", "")
            if (kwargs.get("artifact_type") != "citation_association_result"
                    or not ref.endswith("/result") or value.get("outcome") != "failed"
                    or value.get("failure", {}).get("kind") != "CANONICAL_BUDGET_FAILURE"):
                return None
            identity = {name:kwargs[name] for name in ("task_id", "run_id", "section_id")}
            input_ref = ref.removesuffix("/result") + "/input"
            envelope, native = citation.store.get_json_with_reference(input_ref, **identity)
            if native.get("input_material", {}).get("visible_artifact", {}).get("payload_kind") != "document":
                return None
            return input_ref, envelope, identity
        def cut_write(value, **kwargs):
            selected = selected_document_result(value, kwargs)
            if selected is not None and cut == "before_refusal_result":
                input_ref, envelope, identity = selected
                exit_at(input_ref=input_ref, input_sha256=envelope.content_hash.removeprefix("sha256:"),
                    selected_result_ref=kwargs["artifact_ref"], identity=identity,
                    proof_scope="DURABLE_INPUT_ONLY_NO_RESULT_OR_GRAPH_CURSOR")
            reference = put(value, **kwargs)
            if selected is not None and cut == "result_only":
                input_ref, envelope, identity = selected
                refusal = citation.resolve_budget_refusal(receipt_ref=reference.artifact_ref,
                    expected_sha256=reference.content_hash.removeprefix("sha256:"), identity=identity)
                exit_at(input_ref=input_ref, input_sha256=envelope.content_hash.removeprefix("sha256:"),
                    refusal=refusal, identity=identity,
                    proof_scope="NATIVE_REFUSAL_ONLY_NO_MATERIALIZATION_OR_JOINED_GRAPH_PACKET")
            return reference
        def cut_pause(run_id_arg, expected_revision, **kwargs):
            if cut == "packet_before_pause":
                envelope, packet = citation.store.get_json_with_reference(kwargs["wait_ref"],
                    task_id=state["task_id"], run_id=state["run_id"])
                assert envelope.content_hash == "sha256:" + kwargs["wait_sha256"]
                exit_at(wait_ref=kwargs["wait_ref"], wait_sha256=kwargs["wait_sha256"], wait_packet=packet,
                    proof_scope="ACTUAL_JOINED_RUNNER_CONTROL_AND_CURSOR_NATIVE_PACKET")
            result = pause(run_id_arg, expected_revision, **kwargs)
            if cut == "pause_before_checkpoint":
                envelope, packet = citation.store.get_json_with_reference(kwargs["wait_ref"],
                    task_id=state["task_id"], run_id=state["run_id"])
                assert envelope.content_hash == "sha256:" + kwargs["wait_sha256"]
                exit_at(wait_ref=kwargs["wait_ref"], wait_sha256=kwargs["wait_sha256"], wait_packet=packet,
                    pause_event_ref=result.event_ref, pause_event_sha256=result.event_sha256,
                    proof_scope="NATIVE_PAUSE_ONLY_CHECKPOINT_STILL_PREDECESSOR")
            return result
        citation.store.put_json = cut_write
        life.pause_for_budget = cut_pause
    _run_production_fake(output_root, run_id=run_id, mutate_engine=install)
    raise AssertionError("Actual selected native Graph boundary was not reached")


def _open_runtime(case_root, boundary):
    class MeasuredFactory(SupervisorFactory):
        def build(self, **kwargs):
            supervisor = super().build(**kwargs)
            _measure(supervisor.workflow_engine, case_root, cold=True)
            return supervisor
    root = case_root / "native"
    service = MainlineApplicationService(supervisor_factory=MeasuredFactory())
    settings = replace(get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True)
    snapshot = boundary["snapshot"]
    runtime = service._build_existing_runtime(project_root=ROOT,
        task_id=snapshot["task_id"], run_id=snapshot["run_id"], output_root=root,
        settings=settings, enable_agent_self_rag=False, allow_demo_defaults=True,
        retrieval_access_scope=None, require_execution_compatibility=True)
    return service, runtime


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows process exit evidence is required")
@pytest.mark.parametrize("cut", ["before_refusal_result", "result_only", "packet_before_pause", "pause_before_checkpoint"])
def test_native_windows_graph_budget_pause_cut_preserves_exact_authority(tmp_path, monkeypatch, cut):
    for name, value in {"USE_REAL_RAG_TOOL":"false", "FAKE_RAG_SCENARIO":"default",
            "FAKE_LLM_SCENARIO":"default", "ENABLE_AGENT_SELF_RAG":"false", "ENABLE_SEMANTIC_GATE":"false"}.items():
        monkeypatch.setenv(name, value)
    case_root = tmp_path / cut
    case_root.mkdir()
    run_id = "bt15_graph_" + uuid4().hex
    try:
        child = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
            "worker", str(case_root), run_id, cut], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills/waits for this test process. The harness owns no
        # canonical production lifecycle or account mutation at this boundary.
        def as_text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        _json_once(case_root / "test-time-budget-exhausted.json", {
            "status":"TEST_TIME_BUDGET_EXHAUSTED", "coverage":"E2E_INCOMPLETE",
            "production_failure":False, "canonical_terminal_mutation":False,
            "cut":cut, "run_id":run_id, "harness_limit_seconds":60,
            "stdout":as_text(exc.stdout), "stderr":as_text(exc.stderr),
            "native_boundary_reached":(case_root / "actual-cut.json").exists(),
        })
        pytest.fail("TEST_TIME_BUDGET_EXHAUSTED / E2E_INCOMPLETE / production_failure=NO")
    _json_once(case_root / "actual-process-exit.json", {
        "returncode":child.returncode, "stdout":child.stdout, "stderr":child.stderr,
        "positive_process_exit":child.returncode == 71,
        "exit_does_not_establish_joined_control_or_no_effect":True,
    })
    assert child.returncode == 71, child.stderr
    boundary = json.loads((case_root / "actual-cut.json").read_text(encoding="utf-8"))
    before = boundary["snapshot"]
    assert len(before["children"]) == 8 and all(row["status"] == "running" for row in before["children"])
    assert before["budget"]["limits"]["model_tokens_total"] == 24000
    assert before["budget"].get("grant_history", []) == []
    assert before["current_business_node"] == "final_artifact_review"
    physical_bytes = (case_root / "actual-invoker-entry.jsonl").read_bytes()
    service, runtime = _open_runtime(case_root, boundary)
    try:
        engine = runtime.supervisor.workflow_engine
        citation = engine.stage_service.target_post_write_composition.citation_grounding
        reopened = _snapshot(engine, runtime.workflow, runtime.task.task_id, runtime.task.run_id, case_root / "native")
        assert reopened["source_epoch"] == before["source_epoch"] and len(reopened["source_epoch"]) == 19
        core_lock_path = "backend/core/persistence/interprocess_lock.py"
        assert reopened["source_epoch"][core_lock_path] == hashlib.sha256((ROOT / core_lock_path).read_bytes()).hexdigest()
        assert reopened["run"] == before["run"] and reopened["children"] == before["children"]
        assert reopened["budget"] == before["budget"]
        if cut in {"before_refusal_result", "result_only"}:
            assert before["run"]["status"] == "running" and before["native_budget_transition"] is None
            assert not [ref for ref in before["artifacts"] if ref.startswith("artifact://document-budget-wait/")]
            assert not [ref for ref in before["artifacts"] if ref.startswith("artifact://document-budget-runner-closure/")]
            assert not [info for info in before["artifacts"].values()
                if info["reference"]["artifact_type"] == "budget_document_materialization"]
            if cut == "before_refusal_result":
                with pytest.raises(FileNotFoundError):
                    citation.store.get_json(boundary["selected_result_ref"], **boundary["identity"])
                wrong_ref, wrong_hash = boundary["input_ref"], boundary["input_sha256"]
            else:
                refusal = citation.resolve_budget_refusal(receipt_ref=boundary["refusal"]["native_denial_ref"],
                    expected_sha256=boundary["refusal"]["native_denial_sha256"], identity=boundary["identity"])
                assert refusal == boundary["refusal"]
                wrong_ref, wrong_hash = refusal["native_denial_ref"], refusal["native_denial_sha256"]
            with pytest.raises(ValueError):
                engine.recover_budget_pause_from_wait_packet(runtime.workflow,
                    task_id=runtime.task.task_id, run_id=runtime.task.run_id,
                    wait_ref=wrong_ref, wait_sha256=wrong_hash)
            observation = {"phase":"INDETERMINATE", "production_failure":False,
                "canonical_run":"RUNNING", "safe_hold_recovered":False}
        else:
            packet = boundary["wait_packet"]
            _, closure = citation.store.get_json_with_reference(packet["runner_closure_ref"],
                task_id=runtime.task.task_id, run_id=runtime.task.run_id)
            assert closure["worker_still_active"] is False
            assert closure["active_background_workers_observed"] == 0
            assert closure["execution_control"]["cancel_requested"] is False
            assert closure["execution_control"]["pending_external_commits"] == 0
            assert closure["execution_control"]["external_commit_state"] == "committed"
            assert closure["source_epoch"] == before["source_epoch"]
            if cut == "packet_before_pause":
                assert before["run"]["status"] == "running" and before["native_budget_transition"] is None
                observation = engine.recover_budget_pause_from_wait_packet(runtime.workflow,
                    task_id=runtime.task.task_id, run_id=runtime.task.run_id,
                    wait_ref=boundary["wait_ref"], wait_sha256=boundary["wait_sha256"])
                replay = engine.recover_budget_pause_from_wait_packet(runtime.workflow,
                    task_id=runtime.task.task_id, run_id=runtime.task.run_id,
                    wait_ref=boundary["wait_ref"], wait_sha256=boundary["wait_sha256"])
                assert observation == replay
            else:
                assert before["run"]["status"] == "paused"
                graph_state = engine.runtime_state_codec.restore_graph_state(
                    engine.checkpointer.get_tuple(packet["cursor"]["checkpoint_config"]).checkpoint["channel_values"],
                    workflow=runtime.workflow)
                observation = engine.ensure_budget_wait_checkpoint(runtime.workflow, graph_state)
                replay = engine.ensure_budget_wait_checkpoint(runtime.workflow, graph_state)
                assert observation == replay
            assert observation["pause_purpose"] == "BUDGET_AUTHORIZATION"
            assert observation["next_nodes"] == ["budget_authorization_interrupt"] and observation["interrupts"]
        after = _snapshot(engine, runtime.workflow, runtime.task.task_id, runtime.task.run_id, case_root / "native")
        assert after["children"] == before["children"] and after["child_preimage_sha256"] == before["child_preimage_sha256"]
        assert after["budget"] == before["budget"] and after["budget_journal_sha256"] == before["budget_journal_sha256"]
        assert after["committed_node_refs"] == before["committed_node_refs"]
        assert after["raw_capture_exists"] == before["raw_capture_exists"]
        assert after["raw_capture_sha256"] == before["raw_capture_sha256"]
        assert all(after["artifacts"].get(ref) == value for ref, value in before["artifacts"].items())
        assert (case_root / "actual-invoker-entry.jsonl").read_bytes() == physical_bytes
        assert not (case_root / "cold-business-stage-entry.jsonl").exists()
        if cut in {"before_refusal_result", "result_only"}:
            assert after["run"] == before["run"] and after["native_budget_transition"] is None
            assert after["checkpoint_material_sha256"] == before["checkpoint_material_sha256"]
        else:
            assert after["run"]["status"] == "paused" and after["run"]["finished_at"] is None
            assert after["run"]["lifecycle_revision"] == before["run"]["lifecycle_revision"] + (cut == "packet_before_pause")
            assert after["native_budget_transition"]["kind"] == "PAUSE"
            assert after["native_budget_transition"]["wait_ref"] == boundary["wait_ref"]
            assert after["native_budget_transition"]["wait_sha256"] == boundary["wait_sha256"]
        _json_once(case_root / "native-reopen-observation.json", {
            "rows":["BT-15"], "cut":cut, "before":before, "after":after,
            "observation":observation, "native_process_returncode":child.returncode,
            "physical_entries":_physical_entries(case_root), "physical_delta_after_cold":0,
            "successful_section_writer_support_verifier_accounting_delta":0,
            "scope":"ACTUAL_WINDOWS_GRAPH_FACTORY_OFFLINE_LOCAL_NOT_REAL_E2E",
        })
    finally:
        service._close_supervisor(runtime.supervisor, primary_error=None)


if __name__ == "__main__":
    assert sys.argv[1] == "worker"
    _worker(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
