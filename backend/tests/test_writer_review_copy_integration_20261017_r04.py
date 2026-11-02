"""Two fresh real-Mainline Native Runs; execute only after the R04 gates pass.

The first Run shares real source, isolation and delivery checks. The second
injects a business failure only after the original review and another process's
verified read. File history never asks a live SourceOwner to re-derive evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from unittest.mock import patch
import uuid

from apps.enterprise_document.agents.scheme_writer_agent import SchemeWriterAgent
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from apps.enterprise_document.services.scheme_writer.use_case import SchemeGenerationUseCase
from schemas.status import TERMINAL_STATUSES
import test_phase7_document_closure_mainline as phase7
import test_writer_review_copy_native_20261017_r02 as native
from test_writer_review_copy_c3_production_20261017_r03 import protected_directory
from test_writer_review_copy_compat_20261017_r03 import exercise_live_copy_fault_isolation
from test_writer_review_copy_sources_20261017_r03 import run_live_source_checks


_READ_CHILD = r'''
import hashlib, json, os, sys, time, traceback
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyRuntime
from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
from model_gateway.fake_llm_client import FakeLLMClient
request = json.load(sys.stdin)
counts = {}
def forbid(name):
    def call(*args, **kwargs):
        counts[name] = counts.get(name, 0) + 1
        raise AssertionError('file history called forbidden authority: ' + name)
    return call
result = {'pid': os.getpid(), 'mode': request['mode'], 'status': 'RUNNING',
    'clock': vars(time.get_clock_info('perf_counter'))}
try:
    with ExitStack() as stack:
        for owner, name in ((wc, 'resolve_committed_predecessor'), (wc, 'validate_exact_writer_document'),
                (SectionDependencyRuntime, 'validate_source'), (SectionDependencyRuntime, 'complete_source'),
                (FakeLLMClient, 'generate')):
            stack.enter_context(patch.object(owner, name, side_effect=forbid(name)))
        for name in ('execute', 'resume', 'recover', 'stream_events'):
            stack.enter_context(patch.object(EnterpriseDocumentWorkflowEngine, name, side_effect=forbid(name)))
        result['read_started_ns'] = time.perf_counter_ns()
        value = wc.verify_sealed_pair(runs_dir=request['runs_dir'], task_id=request['receipt']['task_id'],
            run_id=request['receipt']['run_id'], source_snapshot_sha256=request['receipt']['source_snapshot_sha256'],
            expected_receipt=request['receipt'])
        markdown = Path(value['markdown_path']).read_bytes()
        receipt_bytes = Path(value['receipt_path']).read_bytes()
        assert value['ready'] is True and value['verification'] == 'HISTORICAL_VERIFIED'
        assert hashlib.sha256(markdown).hexdigest() == request['receipt']['markdown_sha256']
        assert not counts
        result.update(status='PASS', read_finished_ns=time.perf_counter_ns(),
            markdown_sha256=hashlib.sha256(markdown).hexdigest(),
            receipt_file_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
            source_snapshot_sha256=value['receipt']['source_snapshot_sha256'],
            committed_checkpoint_source_sha256=value['receipt']['committed_checkpoint_source_sha256'],
            receipt_sha256=value['receipt']['receipt_sha256'], forbidden_authority_calls=counts,
            markdown_bytes=len(markdown), verification=value['verification'])
except BaseException as error:
    result.update(status='FAIL', exception_type=type(error).__name__, traceback=traceback.format_exc(),
        forbidden_authority_calls=counts)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    raise
print(json.dumps(result, ensure_ascii=False), flush=True)
'''


def _save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _error(error):
    return {"type": type(error).__name__, "message": str(error),
        "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        "writer_copy_source_evidence": getattr(error, "writer_copy_source_evidence", None)}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _child_read(runs_dir, sealed, *, mode, evidence_dir):
    request = {"runs_dir": str(runs_dir), "receipt": sealed["receipt"], "mode": mode}
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    backend = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = str(backend) + os.pathsep + environment.get("PYTHONPATH", "")
    started = time.perf_counter_ns()
    process = subprocess.run([sys.executable, "-c", _READ_CHILD], input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        capture_output=True, timeout=45, env=environment,
        cwd=str(backend.parent))
    returned = time.perf_counter_ns()
    (evidence_dir / (mode + ".stdout.raw.log")).write_bytes(process.stdout)
    (evidence_dir / (mode + ".stderr.raw.log")).write_bytes(process.stderr)
    rows = [json.loads(line) for line in process.stdout.decode("utf-8").splitlines() if line.startswith('{"pid":')]
    observation = {"returncode": process.returncode, "parent_started_ns": started,
        "parent_returned_ns": returned, "result": rows[-1] if rows else None}
    _save(evidence_dir / (mode + ".json"), observation)
    assert process.returncode == 0 and rows and rows[-1]["status"] == "PASS", observation
    child = rows[-1]
    assert child["pid"] != os.getpid()
    # This Native sample also verifies the cross-process perf_counter ordering.
    assert started <= child["read_started_ns"] <= child["read_finished_ns"] <= returned
    return observation


def _project(run_id, titles):
    return ProjectInputSchema(schema_version="project_input_v3", task_id="task-" + run_id, task_type="scheme_generation",
        user_query="生成企业级 RAG-Agent 系统建设方案",
        generation_requirements={"required_sections": list(titles), "need_citation": False,
            "citation_required_sections": []},
        output_schema={"document_title": "企业级 RAG-Agent 系统建设方案", "required_sections": list(titles)})


def _runtime_paths(engine, output_root):
    """Inspect existing owner attributes only; never instantiate an owner."""
    service = engine.stage_service
    owner = service.target_workflow_runtime.section_dependencies
    capture = service.capture_service.data_capture_recorder
    paths = {"checkpoint.path": engine.checkpointer.path, "source.store.root": owner.store.root,
        "source.lifecycle.path": owner.lifecycle.path, "postwrite.runs_dir": service.target_post_write_composition.runs_dir,
        "capture.output_dir": capture.output_dir}
    paths.update({"source.artifact.ledger_path": owner.paragraph_runtime.artifact_runtime.ledger_path,
        "source.paragraph.manifest_ledger_path": owner.paragraph_runtime.manifest_ledger_path,
        "source.claim.ledger_path": owner.claim_runtime.ledger_path,
        "source.writer.database_path": owner.writer_execution_store.database_path,
        "runtime.task_manager.output_dir": service.target_workflow_runtime.task_manager.output_dir})
    rows = [{"owner_attribute": name, "path": str(Path(path).resolve()),
        "inside_whole_output_root": Path(path).resolve().is_relative_to(output_root.resolve())}
        for name, path in sorted(paths.items())]
    assert rows and all(row["inside_whole_output_root"] for row in rows), rows
    return rows


def _run_case(parent, evidence_dir, *, shared, fail_after_read):
    label = "shared" if shared else "failed-history"
    run_id = "writer-copy-r04-" + label + "-" + uuid.uuid4().hex
    output_root = protected_directory(parent / run_id)
    assert not (output_root / "runs").exists(), "never reuse a runtime root"
    evidence_dir.mkdir(exist_ok=False)
    report = {"run_id": run_id, "output_root": str(output_root), "status": "RUNNING",
        "acceptance_scope": "WRITER_COPY_SOURCE_TIMING_ISOLATION_AND_HISTORICAL_FILE_READ_ONLY",
        "business_success_or_quality_pass_asserted": False,
        "shared_source_group": shared, "fail_after_read": fail_after_read,
        "clock": vars(time.get_clock_info("perf_counter")), "production_hook_calls": [],
        "postwrite_calls": [],
        "source_group": "NOT_RUN", "isolation_group": "NOT_RUN", "history_verify": "NOT_RUN",
        "source_hashes": {"integration_test": _sha(__file__), "writer_copy": _sha(wc.__file__),
            "source_helper": _sha(Path(__file__).with_name("test_writer_review_copy_sources_20261017_r03.py")),
            "compat_helper": _sha(Path(__file__).with_name("test_writer_review_copy_compat_20261017_r03.py")),
            "live_c1_helper": _sha(native.__file__), "graph": _sha(Path(wc.__file__).parents[2] / "workflows" / "top_level_business_graph.py")}}
    report_path = evidence_dir / "integration.json"
    errors, engines = [], []
    state = {"phase": "probe", "sealed": None, "inside_copy_hook": False}
    lock = threading.RLock()

    def persist():
        with lock:
            _save(report_path, report)

    def capture(error, boundary):
        with lock:
            if not errors:
                errors.append(error)
            report.setdefault("captured_errors", []).append({"boundary": boundary, **_error(error)})
            report["status"] = "CAPTURED_STOP_BUSINESS_ALLOWED_TO_CONTINUE"
            persist()

    def configure(engine):
        engines.append(engine)
        report["actual_writable_owner_paths"] = _runtime_paths(engine, output_root)
        persist()
        original_hook, original_stage = engine._maybe_seal_writer_review_copy, engine._run_stage
        service = engine.stage_service.final_artifact_review_service
        original_review = service.review_mainline
        owner = engine.stage_service.target_workflow_runtime.section_dependencies
        original_source_read = owner.validate_source
        composition = engine.stage_service.target_post_write_composition
        original_postwrite = composition.complete_target_post_write

        def postwrite(**kwargs):
            call = {"started_ns": time.perf_counter_ns(), "run_id": kwargs.get("run_id")}
            report["postwrite_calls"].append(call)
            try:
                value = original_postwrite(**kwargs)
            except BaseException as error:
                call.update(returned_ns=time.perf_counter_ns(), original_exception=_error(error))
                raise
            else:
                call.update(returned_ns=time.perf_counter_ns(), returned=True)
                return value
            finally:
                persist()

        def source_read(*args, **kwargs):
            try:
                return original_source_read(*args, **kwargs)
            except BaseException as error:
                if state["phase"] == "actual" and state["inside_copy_hook"]:
                    capture(error, "actual_hook_real_source_owner")
                raise  # The original best-effort production hook handles it.

        def seal(**kwargs):
            state["inside_copy_hook"] = state["phase"] == "actual"
            started = time.perf_counter_ns()
            try:
                value = original_hook(**kwargs)
            finally:
                state["inside_copy_hook"] = False
            if state["phase"] == "actual":
                returned = time.perf_counter_ns()
                report["production_hook_calls"].append({"started_ns": started, "returned_ns": returned,
                    "elapsed_ns": returned - started, "timing_scope": "original hook: source validation plus file seal; no net-overhead claim",
                    "sealed": value})
                state["sealed"] = value
                persist()
            return value

        def review(**kwargs):
            value = original_review(**kwargs)
            if state["phase"] == "actual" and not errors:
                try:
                    report["original_review_body_returned_ns"] = time.perf_counter_ns()
                    assert state["sealed"] is not None, "original production hook did not seal a Writer copy"
                    runs_dir = engine.stage_service.target_post_write_composition.runs_dir
                    report["live_child_read"] = _child_read(runs_dir, state["sealed"], mode="live-read", evidence_dir=evidence_dir)
                    report["review_return_or_failure_ns"] = time.perf_counter_ns()
                except BaseException as error:
                    # Harness observation failure must not manufacture business failure.
                    capture(error, "live_read_after_original_review")
                else:
                    if fail_after_read:
                        report["intentional_business_failure_injected_ns"] = time.perf_counter_ns()
                        persist()
                        raise RuntimeError("test-only finalreview failure after verified WriterCopy")
            return value

        def stage(runtime_state, *, workflow, node_name, stage):
            if node_name in {"finalize_workflow_result", "finalize_workflow_failure"}:
                report["terminal_stage"] = node_name
                report["terminal_stage_entered_ns"] = time.perf_counter_ns()
            result = original_stage(runtime_state, workflow=workflow, node_name=node_name, stage=stage)
            if node_name == "final_artifact_review" and state["phase"] == "actual":
                report["final_review_update"] = result
                report["final_review_stage_returned_ns"] = time.perf_counter_ns()
                persist()
            return result

        engine._maybe_seal_writer_review_copy = seal
        service.review_mainline = review
        owner.validate_source = source_read
        composition.complete_target_post_write = postwrite
        engine._run_stage = stage

    def adapter(output_root, *, run_id, patchers=(), mutate_engine=None):
        parent_factory = phase7._InspectingFactory
        class Factory(parent_factory):
            def build(self, **kwargs):
                supervisor = super().build(**kwargs)
                report["effective_settings_data_root"] = str(kwargs["settings"].data_root)
                if mutate_engine is not None:
                    mutate_engine(supervisor.workflow_engine)  # Original C1 observer first.
                configure(supervisor.workflow_engine)
                return supervisor
        guards = (patch.object(SchemeWriterAgent, "run", side_effect=AssertionError("coarse Writer fallback")),
            patch.object(SchemeGenerationUseCase, "run", side_effect=AssertionError("legacy Writer fallback")))
        with patch.object(phase7, "_InspectingFactory", Factory):
            return phase7._run_fake(output_root, run_id=run_id, patchers=(*guards, *patchers),
                project_input=_project(run_id, ["建设范围", "计算资源"] if shared else ["建设范围"]))

    def on_entry(*, engine, workflow, graph, payload, checkpoint, values, entry):
        report["live_entry"] = entry
        if shared:
            try:
                report["source_group"] = run_live_source_checks(engine, workflow, graph, payload)
                report["isolation_group"] = exercise_live_copy_fault_isolation(engine=engine,
                    workflow=workflow, runtime_state=values, evidence_path=evidence_dir / "isolation.json")
            except BaseException as error:
                capture(error, "source_or_isolation_live_entry")
        state["phase"] = "actual"
        persist()

    persist()
    try:
        with patch.object(native, "_run_production_fake", adapter):
            c1, summary, factory = native._run_native_at_live_entry(output_root, run_id=run_id,
                on_entry=on_entry, evidence_path=evidence_dir / "live-c1.json")
        report.update(c1=c1, summary=summary, mainline_returned_ns=time.perf_counter_ns())
        persist()
        # Mainline can return a failed summary. Preserve and rethrow the exact
        # observed source error only after the original business path finishes.
        if errors:
            raise errors[0]
        execution = native._execution(summary)
        report["execution_status"] = execution["status"]
        assert len(factory.engines) == len(engines) == 1, "one real Engine per fresh Run"
        output = summary.get("scheme_writer_output") or {}
        report["business_quality_projection"] = {"summary_status": summary.get("status"),
            "terminal_outcome": summary.get("terminal_outcome"), "hard_gate": output.get("hard_gate"),
            "closure": (output.get("extra") or {}).get("document_closure"),
            "metadata": execution.get("metadata"), "error": execution.get("error")}
        assert execution["status"] in {status.value for status in TERMINAL_STATUSES}, execution
        if shared:
            assert report["postwrite_calls"], "real Target PostWrite did not execute"
            assert any(row["node_id"] == "evaluate_document_gate" for row in execution["node_outputs"])
            first = report["postwrite_calls"][0]["started_ns"]
            report["before_real_postwrite"] = {"first_started_ns": first,
                "seal_returned_before": report["production_hook_calls"][0]["returned_ns"] < first,
                "live_read_finished_before": report["live_child_read"]["result"]["read_finished_ns"] < first,
                "parent_read_returned_before": report["live_child_read"]["parent_returned_ns"] < first}
            assert all(value for key, value in report["before_real_postwrite"].items() if key != "first_started_ns")
        assert len(report["production_hook_calls"]) == 1 and state["sealed"] is not None
        assert report["live_child_read"]["result"]["read_finished_ns"] < report["terminal_stage_entered_ns"]
        assert entry_order(report), "commit/seal/read/review/terminal ordering was not observed"
        if fail_after_read:
            assert execution["status"] == "failed", execution
            error = execution.get("error") or {}
            assert error["failed_node"] == "final_artifact_review", error
            assert report["terminal_stage"] == "finalize_workflow_failure"
            lifecycle = factory.engines[0].stage_service.target_workflow_runtime.section_dependencies.lifecycle
            report["canonical_run_lifecycle"] = lifecycle.load_run_lifecycle(run_id).model_dump(mode="json")
            assert report["canonical_run_lifecycle"]["status"] == "failed"
        sealed = state["sealed"]
        report["history_verify"] = _child_read(output_root / "runs", sealed,
            mode="failed-history-read" if fail_after_read else "natural-terminal-history-read", evidence_dir=evidence_dir)
        archive = evidence_dir / "sealed-pair"
        archive.mkdir(exist_ok=False)
        rows = []
        for field in ("markdown_path", "receipt_path"):
            source_path = Path(sealed[field]); target = archive / source_path.name
            body = source_path.read_bytes()
            with target.open("xb") as stream:
                stream.write(body)
            assert _sha(source_path) == _sha(target)
            rows.append({"source": str(source_path), "archive": str(target), "bytes": len(body), "sha256": _sha(target)})
        report.update(archive=rows, source_snapshot_sha256=sealed["receipt"]["source_snapshot_sha256"],
            committed_checkpoint_source_sha256=sealed["receipt"]["committed_checkpoint_source_sha256"],
            receipt_sha256=sealed["receipt"]["receipt_sha256"], status="PASS")
        return report
    except BaseException as error:
        report.update(status="STOP", exception=_error(error))
        raise
    finally:
        for engine in engines:
            idle = engine.wait_for_idle(5)
            report.setdefault("workers_idle", []).append(idle)
            if not idle:
                report["status"] = "STOP_ACTIVE_WORKER_PRESERVE_ROOT"
        persist()
        assert all(report.get("workers_idle", [])), "active worker; preserve runtime and stop"


def entry_order(report):
    entry = report["live_entry"]
    seal = report["production_hook_calls"][0]
    read = report["live_child_read"]
    return (entry["validate_put_returned_ns"] < entry["entry_ns"] < seal["returned_ns"]
        <= read["parent_started_ns"] <= read["result"]["read_started_ns"]
        <= read["result"]["read_finished_ns"] <= read["parent_returned_ns"]
        <= report["review_return_or_failure_ns"] < report["terminal_stage_entered_ns"])


def test_wc_r04_shared_source_delivery_then_failed_history():
    """One collected node; a first-case STOP prevents the second fresh Run."""
    assert os.environ.get("WC_NATIVE_R04_GATES") == "VOLUME_C3_SIF13_PASS", "run only after explicit formal gates"
    parent = Path(os.environ["WC_NATIVE_R04_ROOT"])
    evidence = Path(os.environ["WC_NATIVE_R04_EVIDENCE_DIR"])
    assert parent.is_absolute() and parent.is_dir() and parent.drive.upper() == "E:"
    assert evidence.is_absolute() and evidence.is_dir()
    # Creation calls occur here, never during import/AST preparation.
    shared = _run_case(parent, evidence / "40_SHARED_RUN", shared=True, fail_after_read=False)
    failed = _run_case(parent, evidence / "41_FAILED_HISTORY_RUN", shared=False, fail_after_read=True)
    _save(evidence / "42_TWO_RUN_INTEGRATION.json", {"status": "PASS", "runs": [shared, failed],
        "acceptance_scope": "WRITER_COPY_ONLY; natural business quality is recorded independently",
        "same_bytes_new_paragraph_version": "NOT_RUN_NO_CANONICAL_HEAD_MUTATION_IN_SHARED_RUN",
        "different_nonprivileged_token_attack": "NOT_RUN_ACCEPTED_USER_GAP"})
