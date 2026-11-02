"""Narrow Writer Copy isolation and official SQLite cursor compatibility.

The exported fault group consumes the parent's one live canonical Native Run.
The standalone tests use a test-only interrupt at plan_document, before any
Research/Writer work. They establish cursor transport, not Source acceptance.
No compiled graph, durability argument, NodeRunner, or Saver is substituted.
"""
from __future__ import annotations

from contextlib import ExitStack
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from langgraph.types import interrupt
import pytest

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.official_checkpoint_saver import create_sqlite_checkpoint_resource
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine, phase2_checkpoint_identity,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.status import ExecutionStatus


NOW = "2026-10-17T00:00:00+00:00"


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _save(path, material):
    if path is not None:
        Path(path).write_text(json.dumps(material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _business_projection(engine, workflow, runtime_state, update):
    graph, payload = engine._restore({**runtime_state, **update}, workflow)
    output = update["node_outputs"][-1]
    result, metadata = output["result"], output["metadata"]
    graph_data = graph.model_dump(mode="json")
    # Actual wall-clock observations are not business-authority differences.
    graph_data.pop("node_history", None)
    attempts = [{key: value for key, value in item.items() if key != "latency_ms"}
        for item in metadata["attempt_history"]]
    return {"graph_sha256": _sha(graph_data), "payload_sha256": _sha(payload.model_dump(mode="json")),
        "node_input": update["node_inputs"][-1], "node": output["node_id"],
        "node_status": output["status"], "result_status": result["status"],
        "result": result["result"], "error": output["error"],
        "route": update["business_route"], "transition_count": update["transition_count"],
        "attempt_count": metadata["attempt_count"], "retry_count": metadata["retry_count"],
        "attempt_history": attempts, "worker_still_active": metadata["worker_still_active"],
        "payload_changed_paths": metadata["payload_changed_paths"]}


def exercise_live_copy_fault_isolation(*, engine, workflow, runtime_state, evidence_path=None):
    """Six real helper faults, then leave the original Graph hook to finish.

    Call only at the parent's first final_artifact_review entry while canonical
    owners and SQLite are live. Every call uses this exact Run's actual runs_dir.
    Seven deterministic original Stage calls (one baseline plus six faults) are
    returned locally; none of their updates are committed to the business Graph.
    The last two faults may leave the exact immutable MD without a READY receipt.
    """
    from test_writer_review_copy_native_20261017_r02 import _native_source_owner_fingerprint

    graph, payload = engine._restore(runtime_state, workflow)
    owner = engine.stage_service.target_workflow_runtime.section_dependencies
    runs_dir = Path(engine.stage_service.target_post_write_composition.runs_dir)
    source = wc.validate_exact_writer_document(wc.resolve_committed_predecessor(engine,
        workflow=workflow, before_graph=graph, before_payload=payload), section_dependencies=owner)
    receipt = wc._wc_receipt(source["receipt"])
    target = runs_dir / "review_drafts" / graph.task_id / graph.run_id
    markdown_path = target / ("writer-review-" + receipt["source_snapshot_sha256"] + ".md")
    receipt_path = markdown_path.with_suffix(".receipt.json")
    assert not receipt_path.exists(), "faults must precede the original seal"
    before = _native_source_owner_fingerprint(engine, owner, graph)
    observations = {"result": "RUNNING", "task_id": graph.task_id, "run_id": graph.run_id,
        "actual_runs_dir": str(runs_dir), "business_updates_committed": 0,
        "run_stage_calls": 0, "original_stage_calls": 0, "cases": [],
        "source_snapshot_sha256": receipt["source_snapshot_sha256"]}

    def run_original_stage():
        observations["run_stage_calls"] += 1
        def counted_stage(current_graph, current_payload):
            observations["original_stage_calls"] += 1
            return engine.stage_service.final_artifact_review(current_graph, current_payload)
        return EnterpriseDocumentWorkflowEngine._run_stage(engine, runtime_state, workflow=workflow,
            node_name="final_artifact_review", stage=counted_stage)

    try:
        # This baseline alone disables derivation. Positive fault paths retain
        # the original helper, original resolver and actual canonical owner.
        with patch.object(engine, "_maybe_seal_writer_review_copy", return_value=None):
            baseline = _business_projection(engine, workflow, runtime_state, run_original_stage())
        assert baseline["node_status"] == baseline["result_status"] == "success"
        assert baseline["route"] == "continue" and baseline["retry_count"] == 0
        observations["baseline_business_projection_sha256"] = _sha(baseline)
        original_link, original_json = wc.os.link, wc._json_bytes

        for label in ("sqlite_read", "canonical_source_owner", "disk_full_fsync", "permission_link",
                "receipt_serialization", "receipt_last_publication"):
            calls = []

            def fail(*args, **kwargs):
                calls.append(label)
                if label == "sqlite_read":
                    raise sqlite3.OperationalError("test-only SQLite read failure")
                if label == "canonical_source_owner":
                    raise wc.WriterReviewCopyUnavailable("test-only canonical owner refusal")
                if label == "disk_full_fsync":
                    raise OSError(errno.ENOSPC, "test-only no space")
                if label == "receipt_serialization":
                    raise TypeError("test-only receipt encoding failure")
                raise PermissionError(errno.EACCES, "test-only publication refused")

            def link(source_path, destination_path, *args, **kwargs):
                if label == "permission_link" or str(destination_path).endswith(".receipt.json"):
                    return fail(source_path, destination_path)
                return original_link(source_path, destination_path, *args, **kwargs)

            def encode(value):
                if isinstance(value, dict) and "receipt_sha256" in value:
                    return fail(value)
                return original_json(value)

            with ExitStack() as stack:
                if label == "sqlite_read":
                    stack.enter_context(patch.object(engine.checkpointer, "get_tuple", side_effect=fail))
                elif label == "canonical_source_owner":
                    stack.enter_context(patch.object(owner, "validate_source", side_effect=fail))
                elif label == "disk_full_fsync":
                    stack.enter_context(patch.object(wc.os, "fsync", side_effect=fail))
                elif label == "receipt_serialization":
                    stack.enter_context(patch.object(wc, "_json_bytes", side_effect=encode))
                else:
                    stack.enter_context(patch.object(wc.os, "link", side_effect=link))
                projection = _business_projection(engine, workflow, runtime_state, run_original_stage())
            assert calls, (label, "the actual requested boundary was never reached")
            assert projection == baseline, (label, projection, baseline)
            assert not receipt_path.exists(), (label, "failed copy was advertised READY")
            observations["cases"].append({"case": label, "result": "PASS",
                "fault_boundary_calls": len(calls), "business_projection_sha256": _sha(projection),
                "ready_receipt_exists": False, "markdown_exists": markdown_path.exists(),
                "markdown_sha256": hashlib.sha256(markdown_path.read_bytes()).hexdigest()
                    if markdown_path.exists() else None})
            _save(evidence_path, observations)
        assert markdown_path.read_bytes() == source["markdown"]
        assert observations["run_stage_calls"] == observations["original_stage_calls"] == 7
        assert _native_source_owner_fingerprint(engine, owner, graph) == before
        observations.update(result="PASS", canonical_source_lifecycle_budget_unchanged=True,
            exact_markdown_left_for_original_seal=True)
        return observations
    except BaseException as exc:
        observations.update(result="FAIL", exception_type=type(exc).__name__)
        raise
    finally:
        _save(evidence_path, observations)


class _CursorCut(RuntimeError):
    pass


class _CursorStages:
    """No provider or canonical business owner is constructed in transport tests."""
    runtime_support = SimpleNamespace(now_iso=lambda: NOW)
    section_max_concurrency = 1

    @staticmethod
    def _stage(graph, payload):
        return payload

    prepare_document_plan = retrieve_document_evidence = initialize_generation = _stage
    execute_sections = document_verify = repair_document_conflicts = _stage
    incremental_document_reverify = assemble_document = validate_document_assembly = _stage
    final_artifact_review = decide_document_version = evaluate_document_gate = _stage
    build_generation_report = persist_deliverable = persist_rejected = _stage
    finalize_workflow_result = finalize_workflow_failure = _stage


class _CursorNormalization:
    @staticmethod
    def execute(graph):
        return SimpleNamespace(project_input=None, table_analysis=None, structured_facts=[])


class _NativeCursorEngine(EnterpriseDocumentWorkflowEngine):
    """Only the selected interrupt/cut node is a test seam; Graph is production."""
    cut_once = False

    def _run_stage(self, state, *, workflow, node_name, stage):
        if node_name != "plan_document":
            return super()._run_stage(state, workflow=workflow, node_name=node_name, stage=stage)
        if self.cut_once:
            self.cut_once = False
            raise _CursorCut("test-only cut after committed normalization")
        response = self._human_review_interrupt(state, workflow=workflow)["human_review_response"]
        graph, payload = self._restore(state, workflow)
        # The existing authority validates stream resumes too. The schema's
        # legacy review route is deliberately not added to the production graph.
        self._apply_human_review(graph, payload, response)
        interrupt({"after_exact_identity_validation": True, "task_id": graph.task_id,
            "run_id": graph.run_id, "input_snapshot_version": self._active_input_snapshot(payload)})
        raise AssertionError("the second native interrupt must pause this narrow test")


def _cursor_fixture(tmp_path):
    from test_writer_review_copy_c3_production_20261017_r03 import protected_directory

    root = protected_directory(tmp_path / ("cursor-runtime-" + uuid.uuid4().hex))
    resource = create_sqlite_checkpoint_resource(root / "runs" / "langgraph_checkpoints.sqlite3")
    workflow = WorkflowDefinitionSchema(workflow_id="writer-copy-r03-cursor", workflow_name="cursor transport",
        task_type="scheme_generation", workflow_version="v1", steps=[], created_at=NOW)
    run_id = "cursor-" + uuid.uuid4().hex
    task_id = "task-" + run_id
    graph = GraphStateSchema(task_id=task_id, run_id=run_id, task_type="scheme_generation",
        user_input="local cursor transport", created_at=NOW,
        context_bundle=ContextBundleSchema(user=UserContextSchema(user_query="local cursor transport"),
            task=TaskContextSchema(task_id=task_id, run_id=run_id, task_type="scheme_generation")))
    engine = _NativeCursorEngine(stage_service=_CursorStages(), normalization_use_case=_CursorNormalization(),
        checkpointer=resource.saver)
    return root, resource, engine, workflow, graph


def _cursor_evidence(name, material):
    evidence_dir = os.environ.get("WC_NATIVE_R03_EVIDENCE_DIR")
    _save(Path(evidence_dir) / name if evidence_dir else None, material)


def _finish_cursor(engine, resource, evidence, name):
    idle = False
    try:
        engine.cancel_active_workers("compatibility-test-finished")
        idle = engine.wait_for_idle(5)
        evidence["workers_joined"] = idle
        if idle:
            resource.close()
        else:
            evidence.update(result="FAIL", resource_close="NOT_CLOSED_ACTIVE_WORKER")
    finally:
        _cursor_evidence(name, evidence)
    assert idle, "test worker remains active; preserve its SQLite owner"


@pytest.mark.parametrize("stream_resume", [False, True], ids=["sync", "stream"])
def test_native_sqlite_execute_resume_stream_identity_without_completed_node_replay(tmp_path, stream_resume):
    root, resource, engine, workflow, graph = _cursor_fixture(tmp_path)
    evidence = {"result": "RUNNING", "root": str(root), "run_id": graph.run_id,
        "scope": "official native cursor transport; test-only plan interrupt; no Source acceptance",
        "stream_resume": stream_resume}
    try:
        if stream_resume:
            initial = list(engine.stream_events(workflow, graph))
            paused = initial[-1].payload["result"]
            assert initial[0].event_type == "workflow_started"
            assert initial[-1].event_type == "human_review_required"
        else:
            paused = engine.execute(workflow, graph).model_dump(mode="json")
        assert paused["status"] == "paused" and paused["completed_node_ids"] == ["normalize_project_input"]
        latest = resource.saver.get_tuple(phase2_checkpoint_identity(workflow, run_id=graph.run_id))
        assert latest is not None
        before_id = latest.config["configurable"]["checkpoint_id"]
        request = paused["metadata"]["human_review_request"]
        resume = {"decision": "approved", "task_id": graph.task_id, "run_id": graph.run_id,
            "input_snapshot_version": request["metadata"]["input_snapshot_version"]}
        reject = []
        for label, change in (("missing_snapshot", {"input_snapshot_version": None}),
                ("stale_snapshot", {"input_snapshot_version": "stale"}),
                ("wrong_task", {"task_id": "other-task"}), ("wrong_run", {"run_id": "other-run"})):
            with pytest.raises(ValueError):
                engine.resume(workflow, graph, {**resume, **change})
            assert resource.saver.get_tuple(phase2_checkpoint_identity(workflow,
                run_id=graph.run_id)).config["configurable"]["checkpoint_id"] == before_id
            reject.append(label)
        with pytest.raises(ValueError, match="resume_run"):
            engine.recovery_plan(workflow, run_id=graph.run_id)
        if stream_resume:
            events = list(engine.stream_events(workflow, graph, resume_value=resume))
            resumed = events[-1].payload["result"]
            assert events[0].event_type == "workflow_resumed"
            assert events[-1].event_type == "human_review_required"
            assert [item.sequence for item in events] == list(range(1, len(events) + 1))
        else:
            resumed = engine.resume(workflow, graph, resume).model_dump(mode="json")
        assert resumed["status"] == "paused" and resumed["metadata"]["resumed"] is True
        assert resumed["completed_node_ids"] == ["normalize_project_input"]
        assert resumed["metadata"]["interrupts"][0]["after_exact_identity_validation"] is True
        latest = resource.saver.get_tuple(phase2_checkpoint_identity(workflow, run_id=graph.run_id))
        evidence.update(result="PASS", initial_checkpoint_id=before_id,
            resumed_checkpoint_id=latest.config["configurable"]["checkpoint_id"],
            rejected_without_checkpoint_write=reject, normalization_completed_once=True,
            recovery_rejected_human_pause=True)
    except BaseException as exc:
        evidence.update(result="FAIL", exception_type=type(exc).__name__)
        raise
    finally:
        _finish_cursor(engine, resource, evidence,
            "31_CURSOR_" + ("STREAM" if stream_resume else "SYNC") + ".json")


def test_native_sqlite_recovery_reopens_exact_unfinished_cursor(tmp_path):
    root, resource, engine, workflow, graph = _cursor_fixture(tmp_path)
    evidence = {"result": "RUNNING", "root": str(root), "run_id": graph.run_id,
        "scope": "official pending cursor recovery; test-only plan cut; no Source acceptance"}
    try:
        engine.cut_once = True
        with pytest.raises(_CursorCut):
            engine.execute(workflow, graph)
        plan = engine.recovery_plan(workflow, run_id=graph.run_id)
        assert plan["reason"] == "unfinished_top_level_checkpoint"
        exact_id = plan["checkpoint_config"]["configurable"]["checkpoint_id"]
        assert engine.wait_for_idle(5)
        resource.close()
        resource = create_sqlite_checkpoint_resource(root / "runs" / "langgraph_checkpoints.sqlite3")
        engine = _NativeCursorEngine(stage_service=_CursorStages(), normalization_use_case=_CursorNormalization(),
            checkpointer=resource.saver)
        assert engine.recovery_plan(workflow, run_id=graph.run_id)["checkpoint_config"]["configurable"]["checkpoint_id"] == exact_id
        result = engine.recover(workflow, graph, recovery_attempt=1,
            recovery_reason="native_test_cut", recovery_policy="resume_checkpoint")
        assert result.status == ExecutionStatus.PAUSED
        assert result.completed_node_ids == ["normalize_project_input"]
        assert result.metadata["resumed"] is True and result.metadata["human_review_required"] is True
        evidence.update(result="PASS", exact_reopened_checkpoint_id=exact_id,
            normalization_completed_once=True, pending_plan_node_reentered=True)
    except BaseException as exc:
        evidence.update(result="FAIL", exception_type=type(exc).__name__)
        raise
    finally:
        _finish_cursor(engine, resource, evidence, "32_CURSOR_RECOVERY.json")
