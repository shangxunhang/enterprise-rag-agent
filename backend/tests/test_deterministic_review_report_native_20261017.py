"""Two fresh Native Fake Runs through the unchanged production Graph.

Opt in with a newly protected RPT_TEST_ROOT and a new RPT_EVIDENCE_DIR.
The failure cut follows real Document validation/Control, before Run success.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import traceback
from unittest.mock import patch
import uuid

import pytest

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.services.agent_rag_v1 import deterministic_review_report as report
from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
import test_phase7_document_closure_mainline as phase7
from test_writer_review_copy_c3_production_20261017_r03 import protected_directory


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _formal_files(root):
    """Compare every existing file except the new report derivative itself."""
    return {str(path.relative_to(root)): _sha(path.read_bytes())
        for path in sorted(root.rglob("*")) if path.is_file()
        and not path.name.startswith(("review-report-", ".review-report-"))}


def _postwrite_rows(database):
    """Digest committed values with a normal live read-only WAL transaction."""
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {}
        for name, ddl in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1] for row in connection.execute("PRAGMA table_info(" + quoted + ")")]
            rows = [[{"blob_hex": item.hex()} if isinstance(item, bytes) else item for item in row]
                for row in connection.execute("SELECT * FROM " + quoted)]
            rows.sort(key=wc._json_bytes)
            tables[name] = {"columns": columns, "count": len(rows), "ddl_sha256": _sha(ddl.encode("utf-8")),
                "rows_sha256": wc._sha(rows)}
        return {"tables": tables, "committed_rows_sha256": wc._sha(tables)}
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()


def _assert_preserved(root, before_files, before_rows, database):
    after_rows = _postwrite_rows(database)
    after_files = _formal_files(root)
    changed = {name: {"before": digest, "after": after_files.get(name)}
        for name, digest in before_files.items() if after_files.get(name) != digest}
    added = {name: {"bytes": (root / name).stat().st_size, "sha256": digest}
        for name, digest in after_files.items() if name not in before_files}
    # The two preserved Native02 REDs established this SQLite-build's empty WAL
    # initialization. Main DBs and every originally existing file stay exact.
    allowed = {str(Path("runs") / "post_write.sqlite3-shm"): {
            "bytes": 32768, "sha256": "fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb"},
        str(Path("runs") / "post_write.sqlite3-wal"): {"bytes": 0, "sha256": _sha(b"")}}
    assert not changed, changed
    assert all(name in allowed and details == allowed[name] for name, details in added.items()), added
    assert after_rows == before_rows
    sidecars = {str(path.relative_to(root)): {"bytes": path.stat().st_size, "sha256": _sha(path.read_bytes())}
        for path in (Path(str(database) + "-shm"), Path(str(database) + "-wal")) if path.exists()}
    return {"changed_or_removed_formal_files": changed, "new_housekeeping_files": added,
        "postwrite_sidecars": sidecars, "committed_rows": after_rows,
        "all_original_files_unchanged": True, "all_committed_rows_unchanged": True}


def _runtime_paths(engine, output_root):
    """Read live existing owner handles only; never construct a historical owner."""
    service = engine.stage_service
    source = service.target_workflow_runtime.section_dependencies
    post = service.target_post_write_composition
    paths = {"checkpoint.path": engine.checkpointer.path,
        "source.store.root": source.store.root, "source.lifecycle.path": source.lifecycle.path,
        "artifact.ledger_path": source.paragraph_runtime.artifact_runtime.ledger_path,
        "paragraph.manifest_ledger_path": source.paragraph_runtime.manifest_ledger_path,
        "claim.ledger_path": source.claim_runtime.ledger_path,
        "writer.database_path": source.writer_execution_store.database_path,
        "task_manager.output_dir": service.target_workflow_runtime.task_manager.output_dir,
        "capture.output_dir": service.capture_service.data_capture_recorder.output_dir,
        "trace.output_dir": engine.trace_sink.output_dir,
        "side_effect.path": engine.side_effect_commit_store.path,
        "postwrite.database.path": post.database.path, "postwrite.runs_dir": post.runs_dir,
        "postwrite.output_store.root": post.output_store.root,
        "postwrite.release_store.root": post.release_store.root,
        "postwrite.assessment_store.path": post.assessment_store.path,
        "research.ledger_path": post.research_runtime.ledger_path}
    # Existing budget instances are observed through their provider closure, never
    # by calling a factory that could create an account or perform reconciliation.
    provider = engine.budget_runtime
    closure = dict(zip(provider.__code__.co_freevars,
        (cell.cell_contents for cell in provider.__closure__ or ())))
    managers = closure["canonical_budget_managers"]
    assert managers, "the real Run must own an existing Budget account"
    for identity, manager in managers.items():
        paths["budget.storage_path:" + "/".join(identity)] = manager.storage_path
    assert engine.fact_snapshot_runtime.artifact_runtime is engine.artifact_runtime
    assert engine.section_plan_runtime.artifact_runtime is engine.artifact_runtime
    memory = engine.memory_runtime
    assert memory is None or memory.runtime is None, "Native Fake must not write a shared Memory database"
    rows = [{"owner_attribute": key, "path": str(Path(value).resolve()),
        "inside_whole_output_root": Path(value).resolve().is_relative_to(output_root.resolve())}
        for key, value in sorted(paths.items())]
    assert all(row["inside_whole_output_root"] for row in rows), rows
    return rows


def _project(run_id, technical_failure):
    titles = ["建设范围"] if technical_failure else ["建设范围", "计算资源"]
    return ProjectInputSchema(schema_version="project_input_v3", task_id="task-" + run_id,
        task_type="scheme_generation", user_query="生成企业级 RAG-Agent 系统建设方案",
        generation_requirements={"required_sections": titles,
            "need_citation": False, "citation_required_sections": []},
        output_schema={"document_title": "企业级 RAG-Agent 系统建设方案",
            "required_sections": titles})


@pytest.mark.parametrize("technical_failure", [False, True], ids=["committed-normal", "committed-sections-failed-run"])
def test_native_report_from_committed_results_and_fault_isolation(technical_failure):
    parent_env, evidence_env = os.environ.get("RPT_TEST_ROOT"), os.environ.get("RPT_EVIDENCE_DIR")
    if not parent_env or not evidence_env:
        pytest.skip("requires a new protected RPT_TEST_ROOT and independent RPT_EVIDENCE_DIR")
    parent = Path(parent_env)
    assert parent.is_absolute() and parent.drive.lower() == "e:" and parent.is_dir()
    assert parent.name.startswith("report-r01-")
    uuid.UUID(parent.name[len("report-r01-"):])
    run_id = "report-native-" + ("failed-" if technical_failure else "normal-") + uuid.uuid4().hex
    output_root = protected_directory(parent / run_id)
    assert not any(output_root.iterdir()), "clean_existing is permitted only on this new UUID root"
    evidence_dir = Path(evidence_env) / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)
    observation = {"status": "RUNNING", "run_id": run_id, "output_root": str(output_root),
        "technical_failure": technical_failure, "writer_copy_hook_calls": [], "faults": [],
        "quality_or_unresolved_values_synthesized": False,
        "model_provider": "NATIVE_FAKE_ONLY", "paid_provider_calls": 0,
        "evidence_level": "NEW_NATIVE_FAKE_WITH_REAL_PRODUCTION_OWNERS",
        "source_sha256": {"test": _sha(Path(__file__).read_bytes()),
            "report_module": _sha(Path(report.__file__).read_bytes()),
            "writer_copy": _sha(Path(wc.__file__).read_bytes())}}
    state = {"inside_observer": False, "errors": [], "rendered": [], "exports": 0}
    original_export, original_render = report.try_export_after_workflow, report.render_review_report
    original_factory = phase7._InspectingFactory

    def rendered(bundle):
        body = original_render(bundle)
        state["rendered"].append((copy.deepcopy(bundle), body))
        return body

    class Factory(original_factory):
        def build(self, **kwargs):
            supervisor = super().build(**kwargs)
            state["supervisor"] = supervisor
            engine = supervisor.workflow_engine
            post = engine.stage_service.target_post_write_composition
            original_hook = engine._maybe_seal_writer_review_copy

            def writer_copy(**arguments):
                sealed = original_hook(**arguments)
                observation["writer_copy_hook_calls"].append({"returned_ns": time.perf_counter_ns(), "sealed": sealed})
                return sealed

            engine._maybe_seal_writer_review_copy = writer_copy
            if not technical_failure:
                original_section = post._section

                def capture_first_committed_section(**arguments):
                    section = original_section(**arguments)
                    if "intermediate_report" not in observation:
                        source = report._find_writer_pair(post, arguments["task_id"], arguments["run_id"])
                        bundle = report.collect_review_results(post, source_receipt=source, document_control_ref=None)
                        bundle.update(source_receipt=source, terminal={"execution_status": "running"},
                            publication={"state": "NOT_VERIFIED"})
                        assert bundle["completeness"] == "PARTIAL"
                        assert sum("gate" in item for item in bundle["sections"]) == 1
                        usage = supervisor.model_usage_snapshot()
                        intermediate = report.seal_review_report(runs_dir=post.runs_dir, bundle=bundle)
                        assert supervisor.model_usage_snapshot() == usage
                        observation["intermediate_report"] = {"ref": intermediate,
                            "markdown_file_sha256": _sha(Path(intermediate["report_path"]).read_bytes()),
                            "receipt_file_sha256": _sha(Path(intermediate["receipt_path"]).read_bytes()),
                            "committed_section_count": 1,
                            "capture_point": "AFTER_FIRST_REAL_SECTION_CONTROL_BEFORE_SECOND_SECTION_AND_DOCUMENT"}
                    return section

                post._section = capture_first_committed_section
            if technical_failure:
                original_finalize = post._finalize_document_output_and_control

                def fail_after_formal_results(**arguments):
                    final = original_finalize(**arguments)
                    current = post.lifecycle_service.load_run_lifecycle(arguments["run_id"])
                    assert current.status is RunLifecycleStatus.RUNNING
                    preimage = post.lifecycle_service.budget_preimage(arguments["run_id"])
                    assert preimage.run == current and len(preimage.current_children) == 1
                    observation["failure_cut"] = {"position": "AFTER_REAL_DOCUMENT_CONTROL_BEFORE_RUN_TERMINAL_COMMIT",
                        "run_status_before_cut": current.status.value,
                        "committed_document_control_ref": final.control_decision_ref.model_dump(mode="json"),
                        "current_attempt_count": len(preimage.current_children),
                        "current_attempts": [child.model_dump(mode="json") for child in preimage.current_children],
                        "explicit_trigger_supplied": False,
                        "owner_rule": "NONCANCEL_FAILURE derives its sole existing current Attempt",
                        "injected_ns": time.perf_counter_ns()}
                    raise ValueError("test-only report cut after committed Post-write Document Control")

                post._finalize_document_output_and_control = fail_after_formal_results
            return supervisor

    def observe_export(**arguments):
        if state["inside_observer"]:
            return original_export(**arguments)
        state["inside_observer"] = True
        try:
            state["exports"] += 1
            supervisor, engine = state["supervisor"], arguments["engine"]
            workflow, task, result = arguments["workflow"], arguments["task"], arguments["result"]
            post = engine.stage_service.target_post_write_composition
            assert engine.active_worker_count == 0, "report must run after the Node worker returned"
            observation["actual_writable_owner_paths"] = _runtime_paths(engine, output_root)
            payload = engine._latest_checkpoint_payload(workflow, run_id=task.run_id)
            source = report._find_writer_pair(post, task.task_id, task.run_id)
            writer = wc.verify_sealed_pair(runs_dir=post.runs_dir, task_id=task.task_id,
                run_id=task.run_id, source_snapshot_sha256=source["source_snapshot_sha256"])
            before_result = result.model_dump(mode="json")
            before_files = _formal_files(output_root)
            before_rows = _postwrite_rows(post.database.path)
            before_usage = supervisor.model_usage_snapshot()
            observation["preservation_baseline"] = {"formal_files": before_files,
                "committed_rows": before_rows,
                "baseline_live_read": _assert_preserved(output_root, before_files, before_rows, post.database.path)}
            _save(evidence_dir / "native-observation.json", observation)
            original_publish = wc._wc_publish
            for fail_receipt in (False, True):
                calls = []

                def fail_publish(path, body):
                    assert path.name.startswith("review-report-")
                    calls.append(str(path))
                    if not fail_receipt or path.name.endswith(".receipt.json"):
                        if fail_receipt:
                            assert path.with_name(path.name[:-len(".receipt.json")] + ".md").is_file()
                            assert not path.exists(), "Receipt must remain absent on failed publication"
                        raise OSError("test-only report publication failure")
                    return original_publish(path, body)

                with patch.object(wc, "_wc_publish", side_effect=fail_publish):
                    supervisor._maybe_export_review_report(workflow=workflow, task=task, result=result)
                assert calls, "fault injection must reach the real report publisher"
                assert result.model_dump(mode="json") == before_result
                preservation = _assert_preserved(output_root, before_files, before_rows, post.database.path)
                assert supervisor.model_usage_snapshot() == before_usage
                observation["faults"].append({"kind": "RECEIPT_LAST" if fail_receipt else "MARKDOWN_WRITE",
                    "calls": calls, "result_unchanged": True, "preservation": preservation,
                    "model_usage_unchanged": True})
            reference = original_export(**arguments)
            assert reference and reference["ready"] is True
            saved_bundle, first_bytes = state["rendered"][-1]
            again = original_export(**arguments)
            assert again == reference
            fresh = report.collect_review_results(post, source_receipt=source,
                document_control_ref=payload.target_post_write_control_decision_ref)
            fresh.update(source_receipt=source, terminal=copy.deepcopy(saved_bundle["terminal"]),
                publication=copy.deepcopy(saved_bundle["publication"]))
            assert fresh == saved_bundle
            assert original_render(fresh) == first_bytes == Path(reference["report_path"]).read_bytes()
            receipt_bytes = Path(reference["receipt_path"]).read_bytes()
            receipt = json.loads(receipt_bytes)
            assert wc._json_bytes(receipt) == receipt_bytes
            assert receipt["report_sha256"] == _sha(first_bytes)
            assert receipt["results_sha256"] == report._results_sha(fresh)
            core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
            assert receipt["receipt_sha256"] == wc._sha(core)
            assert receipt["writer_source_sha256"] == source["source_snapshot_sha256"]
            assert receipt["writer_markdown_sha256"] == source["markdown_sha256"]
            writer_bytes = Path(writer["markdown_path"]).read_bytes()
            assert report.report_applies_to(receipt, writer_bytes)
            assert not report.report_applies_to(receipt, writer_bytes + b"\nEngineer edit\n")
            conflict_bytes = b"test-only pre-existing conflicting report bytes\n"
            with patch.object(report, "RENDERER_VERSION", report.RENDERER_VERSION + "-conflict-fixture"):
                with wc._wc_locked_paths(post.runs_dir, task.task_id, task.run_id,
                        source["source_snapshot_sha256"], create=False) as (writer_path, _):
                    stem = "review-report-" + source["source_snapshot_sha256"] + "-" + report._results_sha(fresh)
                    conflict_path = writer_path.parent / (stem + ".md")
                    conflict_receipt = writer_path.parent / (stem + ".receipt.json")
                    assert not conflict_path.exists() and not conflict_receipt.exists()
                    wc._wc_publish(conflict_path, conflict_bytes)
                with pytest.raises((report.ReviewReportUnavailable, wc.WriterReviewCopyUnavailable)):
                    report.seal_review_report(runs_dir=post.runs_dir, bundle=fresh)
                assert conflict_path.read_bytes() == conflict_bytes and not conflict_receipt.exists()
            assert Path(reference["report_path"]).read_bytes() == first_bytes
            assert Path(reference["receipt_path"]).read_bytes() == receipt_bytes
            preservation = _assert_preserved(output_root, before_files, before_rows, post.database.path)
            assert supervisor.model_usage_snapshot() == before_usage
            canonical = post.lifecycle_service.load_run_lifecycle(task.run_id)
            assert canonical.status is (RunLifecycleStatus.FAILED if technical_failure else RunLifecycleStatus.SUCCESS)
            assert len(fresh["sections"]) == (1 if technical_failure else 2)
            assert all("gate" in section for section in fresh["sections"])
            if technical_failure:
                assert result.status.value == "failed"
                assert fresh["completeness"] == "PARTIAL" and fresh["document"] is None
                assert payload.target_release_publication is None
                assert b"COMPLETION_NOT_ESTABLISHED" in first_bytes
            else:
                assert fresh["completeness"] == "COMPLETE" and fresh["document"] is not None
                assert fresh["document"]["control"]["enforcement_action"] == (
                    post.control_store.resolve_exact(payload.target_post_write_control_decision_ref).enforcement_action.value)
                earlier = observation["intermediate_report"]
                assert earlier["ref"]["receipt"]["writer_source_sha256"] == receipt["writer_source_sha256"]
                assert earlier["ref"]["receipt"]["results_sha256"] != receipt["results_sha256"]
                assert earlier["ref"]["report_path"] != reference["report_path"]
                assert _sha(Path(earlier["ref"]["report_path"]).read_bytes()) == earlier["markdown_file_sha256"]
                assert _sha(Path(earlier["ref"]["receipt_path"]).read_bytes()) == earlier["receipt_file_sha256"]
                observation["new_committed_result_version_preserves_partial"] = True
            if payload.target_release_publication is not None:
                assert fresh["publication"]["release_mode"] == payload.target_release_publication.manifest.release_mode.value
            else:
                assert fresh["publication"]["state"] == "NOT_VERIFIED"
            observation.update(report_ref=reference, source_receipt=source,
                canonical_run=canonical.model_dump(mode="json"), result_status=result.status.value,
                bundle=fresh, repeated_bytes_sha256=_sha(first_bytes), byte_idempotency=True,
                preserved_formal_file_count=len(before_files), preserved_formal_files=before_files,
                final_preservation=preservation,
                conflict_no_replace={"path": str(conflict_path), "sha256": _sha(conflict_bytes),
                    "existing_conflict_preserved": True, "receipt_absent": True},
                model_usage_before=before_usage, model_usage_after=supervisor.model_usage_snapshot(),
                node_worker_count_at_export=engine.active_worker_count,
                machine_truth_preserved=True, human_approval="HUMAN_APPROVAL_NOT_ESTABLISHED")
            (evidence_dir / "review-report.md").write_bytes(first_bytes)
            (evidence_dir / "review-report.receipt.json").write_bytes(receipt_bytes)
            return reference
        except BaseException as error:
            state["errors"].append(error)
            observation.setdefault("observer_errors", []).append({"type": type(error).__name__,
                "message": str(error), "traceback": traceback.format_exc()})
            raise
        finally:
            state["inside_observer"] = False
            _save(evidence_dir / "native-observation.json", observation)

    try:
        with patch.object(phase7, "_InspectingFactory", Factory), \
                patch.object(report, "try_export_after_workflow", side_effect=observe_export), \
                patch.object(report, "render_review_report", side_effect=rendered):
            summary, factory = phase7._run_fake(output_root, run_id=run_id,
                project_input=_project(run_id, technical_failure))
        observation["summary"] = summary
        assert not state["errors"], observation.get("observer_errors")
        assert state["exports"] == 1 and len(factory.engines) == 1
        assert len(observation["writer_copy_hook_calls"]) == 1
        assert observation["writer_copy_hook_calls"][0]["sealed"] is not None
        internal = summary["supervisor_result"]["result"]["target_review_report"]
        assert internal == observation["report_ref"]
        observation["status"] = "PASS"
    except BaseException as error:
        observation.update(status="FAIL", failure_type=type(error).__name__,
            failure_message=str(error), traceback=traceback.format_exc())
        raise
    finally:
        _save(evidence_dir / "native-observation.json", observation)
