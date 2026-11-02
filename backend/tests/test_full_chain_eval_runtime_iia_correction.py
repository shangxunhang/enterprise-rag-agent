"""FCE-IIA native Runtime entry probes; bounded local capture, fixed clients only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession
from eval.full_chain.contracts import EvaluationRunIdentity, canonical_hash
from eval.full_chain.domains.runtime_reliability import (
    compute_timeline, extract_runtime_observations, read_diagnostic_prefix,
    timeline_spans_from_stages,
)
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import (
    SnapshotView, diagnostic_frame_valid, original_diagnostic_frame,
    load_source_snapshot,
)
from test_full_chain_eval_capture import FixedClient, gateway, request


def capture(root, **caps):
    return DiagnosticSession(DiagnosticConfig(root / "capture", root, "iia-runtime", flush_records=1, **caps))


def file_hashes(root):
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def snapshot(root, session=None, exports=None, *, capture_path=None, max_records=100000):
    sources = []
    if session is not None:
        path = capture_path or session.path
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        sources.append({"name": "diagnostics", "family": "diagnostics", "format": "diagnostic_jsonl",
            "path": str(path), "session_id": session.config.session_id,
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "representation_files": sorted({row["payload_ref"]["relative_path"] for row in rows if row.get("payload_ref")})})
    for name, material in (exports or {}).items():
        path = root / (name + ".json")
        path.write_text(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        sources.append({"name": name, "family": name, "format": "json", "path": str(path),
                        "sha256": sha256(path.read_bytes()).hexdigest()})
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "full_chain_source_manifest_v1",
        "snapshot_id": "iia-runtime", "sources": sources}, sort_keys=True), encoding="utf-8")
    before = file_hashes(root)
    result = load_source_snapshot(manifest, [root], max_records=max_records)
    assert file_hashes(root) == before
    return result


def formal(view):
    return observe(view, EvaluationRunIdentity("iia", "subject", source_snapshot_id=view.snapshot_id))


def edge(report, identifier):
    return next(row for row in report.edge_fidelity if row.edge_id == identifier)


def test_native_repeated_sequential_and_threaded_spans_have_original_instance_keys(tmp_path):
    session = capture(tmp_path)
    def once():
        with session.span("same-loader", "FS_API_ELAPSED"):
            time.sleep(.002)
    with session:
        once()
        once()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: once(), range(2)))
    view = snapshot(tmp_path, session)
    before = file_hashes(tmp_path)
    report = formal(view)
    rows = [row for row in report.stage_quality if row.unit_id == "F06" and row.payload.get("event_type") == "SPAN_RESULT"]
    assert len(rows) == 4 and len({row.key for row in rows}) == 4
    assert {row.operation_id for row in rows} == {row.payload["span_id"] for row in rows}
    assert all(row.identity_quality == "ORIGINAL_SPAN_ID" and row.evidence_refs for row in rows)
    assert all(row.measurement_state == "MEASURED" and diagnostic_frame_valid(row.payload) for row in rows)
    assert all("activity_proven" not in original_diagnostic_frame(row.payload) for row in rows)
    assert file_hashes(tmp_path) == before


def test_native_fallback_preserves_physical_instances_and_one_usage_per_attempt(tmp_path):
    session = capture(tmp_path)
    with session:
        gw = gateway(FixedClient(unavailable=True), FixedClient("fixed-2"))
        assert gw.generate(request()).content == "A"
    report = formal(snapshot(tmp_path, session))
    model = [row for row in report.stage_quality if row.unit_id == "F01" and row.payload.get("event_type") in
             {"MODEL_ATTEMPT_REQUEST", "MODEL_CLIENT_RAW_RESPONSE", "MODEL_GATEWAY_PROJECTED_RESPONSE"}]
    assert len(model) == len({row.key for row in model}) == 6
    assert len({row.payload["provider_attempt_id"] for row in model}) == 2
    assert all(row.attempt == row.payload["provider_attempt_id"] for row in model)
    metric = next(row for row in report.metrics if row.metric_id == "physical_attempts")
    assert metric.value == 2 and metric.measurement_state == "MEASURED"
    assert edge(report, "X24").assertion_result is True


def test_formal_native_db_elapsed_has_zero_overlap_with_unexplained_gap(tmp_path):
    session = capture(tmp_path)
    with session:
        with session.span("workflow", "WORKFLOW_WALL"):
            with session.span("actual-db-wait", "DB_API_ELAPSED"):
                time.sleep(.04)
    view = snapshot(tmp_path, session)
    report = formal(view)
    timeline = report.performance_cost["timeline"]
    row = next(r for r in view.records() if r.get("event_type") == "SPAN_RESULT" and r.get("category") == "DB_API_ELAPSED")
    assert row["end_ns"] - row["begin_ns"] >= 20_000_000
    assert all(max(0, min(row["end_ns"], gap["end_ns"]) - max(row["begin_ns"], gap["begin_ns"])) == 0
               for gap in timeline["unexplained_intervals"])
    assert timeline["known_activity_by_category_ns"][row["clock_domain"]]["DB_API_ELAPSED"] == row["end_ns"] - row["begin_ns"]
    assert timeline["known_activity_cause_state"] == "BOUNDARY_ONLY_INTERNAL_CAUSE_UNKNOWN"
    assert timeline["critical_path_state"] == "UNKNOWN" and timeline["cpu_is_wall_partition"] is False


@pytest.mark.parametrize("category", ["PROVIDER", "DB_TRANSACTION_SCOPE", "FS_API_ELAPSED", "FS_PARSE_SCOPE", "LOCK_WAIT", "QUEUE", "WORKER", "WORKER_JOIN_WAIT", "DRAIN", "DRAIN_WAIT"])
def test_native_timer_categories_are_same_clock_activity_and_cpu_stays_separate(tmp_path, category):
    session = capture(tmp_path)
    with session:
        with session.span("workflow", "WORKFLOW_WALL"):
            with session.span("native-boundary", category):
                time.sleep(.003)
    view = snapshot(tmp_path, session)
    batch = extract_runtime_observations(view, "subject")
    spans = timeline_spans_from_stages(batch.stages)
    row = next(r for r in spans if r["category"] == category)
    assert row["activity_proven"] is True and row["boundary_measurement_state"] == "MEASURED"
    assert row["internal_cause_state"] == "UNKNOWN"
    timeline = compute_timeline(spans, window=next(r for r in spans if r["category"] == "WORKFLOW_WALL"))
    assert timeline["known_activity_union_ns"][row["clock_domain"]] == row["end_ns"]-row["begin_ns"]
    assert next(r for r in timeline["spans"] if r["span_id"] == row["span_id"])["thread_cpu_scope"] == row["thread_id"]
    assert "activity_proven" not in next(r for r in view.records() if r.get("sequence") == row["sequence"])


def test_native_overlap_union_does_not_double_count_nested_categories_or_assign_cpu(tmp_path):
    session = capture(tmp_path)
    with session:
        with session.span("workflow", "WORKFLOW_WALL"):
            with session.span("transaction", "DB_TRANSACTION_SCOPE"):
                with session.span("db", "DB_API_ELAPSED"):
                    with session.span("file", "FS_API_ELAPSED"):
                        time.sleep(.004)
    report = formal(snapshot(tmp_path, session))
    timeline = report.performance_cost["timeline"]
    row = next(r for r in report.stage_quality if r.payload.get("category") == "DB_TRANSACTION_SCOPE" and r.payload.get("event_type") == "SPAN_RESULT")
    assert timeline["known_activity_union_ns"][row.payload["clock_domain"]] == row.payload["end_ns"]-row.payload["begin_ns"]
    assert timeline["category_intervals_overlap"] is True
    assert timeline["critical_path_ns"] is None and timeline["cpu_is_wall_partition"] is False


def test_native_actual_file_lock_held_and_worker_timers_reach_formal_timeline(tmp_path):
    from core.persistence.interprocess_lock import InterProcessFileLock
    from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionRunner
    session = capture(tmp_path)
    worker = WorkflowNodeExecutionRunner(now_iso=lambda: "2026-10-05T00:00:00Z")
    with session:
        with session.span("workflow", "WORKFLOW_WALL"):
            with InterProcessFileLock(tmp_path / "actual.lock"):
                time.sleep(.002)
            result = worker.execute(lambda _control: "fixed", execution_id="native-worker",
                commit_scope_id="scope", timeout_seconds=1, thread_name="native-worker",
                attempt_succeeded=lambda value: value == "fixed")
            assert result.value == "fixed" and worker.wait_for_idle(.1)
    report = formal(snapshot(tmp_path, session))
    known = report.performance_cost["timeline"]["known_activity_by_category_ns"]
    categories = {kind for row in known.values() for kind in row}
    assert {"LOCK_WAIT", "LOCK_HELD", "WORKER", "WORKER_JOIN_WAIT", "DRAIN_WAIT"} <= categories
    assert edge(report, "X24").assertion_result is True


def test_native_timeline_uses_original_timer_frame_after_hydrated_view_changes(tmp_path):
    session = capture(tmp_path)
    with session:
        with session.span("original-db", "DB_API_ELAPSED"):
            time.sleep(.003)
    view = snapshot(tmp_path, session)
    batch = extract_runtime_observations(view)
    original = next(row for row in batch.stages if row.payload.get("event_type") == "SPAN_RESULT")
    encoded = original_diagnostic_frame(original.payload)
    original.payload["end_ns"] += 100_000_000
    derived = next(row for row in timeline_spans_from_stages(batch.stages) if row.get("event_type") == "SPAN_RESULT")
    assert derived["end_ns"] == encoded["end_ns"] and derived["activity_proven"] is True
    assert original_diagnostic_frame(original.payload) == encoded


@pytest.mark.parametrize("fault", ["no-seal", "frame", "clock", "missing-begin"])
def test_native_timing_missing_or_changed_proof_cannot_remove_unexplained_time(tmp_path, fault):
    session = capture(tmp_path)
    with session:
        with session.span("workflow", "WORKFLOW_WALL"):
            with session.span("native-db", "DB_API_ELAPSED"):
                time.sleep(.003)
    prefix = read_diagnostic_prefix(session.path, [tmp_path])
    rows = prefix["events"] + [prefix["seal"]]
    before = file_hashes(tmp_path)
    if fault == "no-seal": rows = rows[:-1]
    elif fault == "missing-begin": rows = [r for r in rows if not (r.get("event_type") == "SPAN_BEGIN" and r.get("category") == "DB_API_ELAPSED")]
    else:
        rows = [dict(r) for r in rows]
        item = next(r for r in rows if r.get("event_type") == "SPAN_RESULT" and r.get("category") == "DB_API_ELAPSED")
        if fault == "frame": item["activity_proven"] = True  # untrusted caller flag invalidates the original hash
        else:
            item["clock_domain"] = "process:foreign"
            item["frame_sha256"] = canonical_hash({k:v for k,v in item.items() if k != "frame_sha256"})
    view = SnapshotView.from_records({"diagnostics": rows})
    batch = extract_runtime_observations(view)
    timer = next(r for r in timeline_spans_from_stages(batch.stages) if r.get("category") == "DB_API_ELAPSED")
    assert timer["activity_proven"] is False
    assert file_hashes(tmp_path) == before


@pytest.mark.parametrize("fault", ["none", "lost-cap", "no-seal", "blob-corrupt", "record-cap"])
def test_native_X24_formal_channel_normal_known_fault_and_missing(tmp_path, fault):
    session = capture(tmp_path, **({"max_events": 2} if fault == "lost-cap" else {}))
    with session:
        assert gateway(FixedClient()).generate(request()).content == "A"
    path = session.path
    if fault == "no-seal":
        path = session.path.parent / "unsealed.jsonl"
        path.write_bytes(b"\n".join(session.path.read_bytes().splitlines()[:-1])+b"\n")
    elif fault == "blob-corrupt":
        blob = next((session.path.parent / "representations").glob("*.json"))
        blob.write_bytes(blob.read_bytes()+b" ")
    report = formal(snapshot(tmp_path, session, capture_path=path, max_records=2 if fault == "record-cap" else 100000))
    actual = edge(report, "X24")
    if fault == "none":
        assert actual.measurement_state == "MEASURED" and actual.assertion_result is True
        assert actual.source_refs and actual.target_refs
    elif fault == "no-seal":
        assert actual.measurement_state == "UNMEASURED" and actual.assertion_result is None
    else:
        assert actual.measurement_state == "MEASURED" and actual.assertion_result is False
    assert all("edge_id" not in r for r in actual.payload["endpoints"]["records"])


@pytest.mark.parametrize("pool_cap", [False, True])
def test_native_X24_pool_contract_is_finite_original_count_digest_order(tmp_path, pool_cap):
    session = capture(tmp_path, max_pool_items=1 if pool_cap else 4)
    with session:
        session.emit_pool({"evidence_key": "R-DENSE", "pool_id": "actual-pool", "declared_count": 2},
                          [{"id": "a", "text": "original-a"}, {"id": "b", "text": "original-b"}])
    actual = edge(formal(snapshot(tmp_path, session)), "X24")
    assert actual.measurement_state == "MEASURED" and actual.assertion_result is not pool_cap


@pytest.mark.parametrize("scenario", ["normal", "fallback", "after-return-cancel", "control-exception", "base-exception", "downstream-failure"])
def test_native_X24_actual_fixed_gateway_six_boundary_scenarios(tmp_path, scenario):
    from core.runtime.execution_control import (
        WorkflowExecutionControl, WorkflowExecutionCancelled, activate_execution_control,
    )
    session = capture(tmp_path)
    failure = BaseException("fixed original")
    with session:
        if scenario == "after-return-cancel":
            control = WorkflowExecutionControl.with_timeout(execution_id="actual-cancel", timeout_seconds=5)
            with activate_execution_control(control), pytest.raises(WorkflowExecutionCancelled):
                gateway(FixedClient(control=control)).generate(request())
        elif scenario == "control-exception":
            with pytest.raises(WorkflowExecutionCancelled):
                gateway(FixedClient(throws=True)).generate(request())
        elif scenario == "base-exception":
            with pytest.raises(BaseException) as actual:
                gateway(FixedClient(throws=failure)).generate(request())
            assert actual.value is failure
        elif scenario == "fallback":
            assert gateway(FixedClient(unavailable=True), FixedClient("fixed-2")).generate(request()).content == "A"
        elif scenario == "downstream-failure":
            with pytest.raises(RuntimeError, match="downstream"):
                assert gateway(FixedClient()).generate(request()).content == "A"
                raise RuntimeError("downstream")
        else:
            assert gateway(FixedClient()).generate(request()).content == "A"
    report = formal(snapshot(tmp_path, session))
    assert edge(report, "X24").assertion_result is True
    raw = [row for row in report.stage_quality if row.payload.get("event_type") == "MODEL_CLIENT_RAW_RESPONSE"]
    if scenario in {"control-exception", "base-exception"}: assert raw == []


def test_native_hydrated_model_material_changes_cannot_pass_representation_hash(tmp_path):
    session = capture(tmp_path)
    with session:
        gateway(FixedClient()).generate(request())
    view = snapshot(tmp_path, session)
    row = next(r for r in view.records() if r.get("event_type") == "MODEL_CLIENT_RAW_RESPONSE")
    frame = original_diagnostic_frame(row)
    row["material"]["content"] = "changed-view"
    assert diagnostic_frame_valid(row) and original_diagnostic_frame(row) == frame
    assert edge(formal(view), "X24").assertion_result is False


def native_budget_exports(tmp_path):
    from test_multi_agent_v3_model_tool_runtime import _gateway, _request
    gw, manager = _gateway(tmp_path)
    gw.register_client(FixedClient("m1"))
    session = capture(tmp_path)
    with session:
        assert gw.generate(_request()).content == "A"
    return session, {"usage": {"schema_version": "model_usage_v1", "task_id": "task_1", "run_id": "run_1",
            "created_at": datetime.now(timezone.utc).isoformat(), "finalized": True, "usage": gw.usage_snapshot()},
            "budget": manager.canonical_state().model_dump(mode="json")}


@pytest.mark.parametrize("fault", ["none", "usage-total", "reservation-total", "missing-reservation", "aggregate-only"])
def test_native_X23_actual_budget_and_usage_exports_reach_formal_edge(tmp_path, fault):
    session, exports = native_budget_exports(tmp_path)
    if fault == "usage-total": exports["usage"]["usage"]["total_tokens"] += 1
    elif fault == "reservation-total":
        next(r for r in exports["budget"]["reservations"] if r["reservation_ref"]["reservation_kind"] == "model_provider_attempt_tokens")["actual"]["model_tokens_total"] += 1
    elif fault == "missing-reservation": exports["budget"]["reservations"] = []
    elif fault == "aggregate-only": del exports["budget"]
    actual = edge(formal(snapshot(tmp_path, session, exports)), "X23")
    if fault == "none": assert actual.assertion_result is True and actual.measurement_state == "MEASURED"
    elif fault in {"usage-total", "reservation-total"}: assert actual.assertion_result is False and actual.measurement_state == "MEASURED"
    else: assert actual.assertion_result is None and actual.measurement_state == "UNMEASURED"
    assert actual.source_refs and actual.payload["calculation"]["basis"] == "PHYSICAL_CHANNEL_USAGE_V1_CANONICAL_BUDGET"


@pytest.mark.parametrize("fault", ["none", "hash", "missing"])
def test_native_X22_checkpoint_export_partial_negative_missing_no_input_verdict(tmp_path, fault):
    from apps.enterprise_document.services.agent_rag_v1.checkpoint_store import JsonCheckpointStore
    from test_agent_rag_v1_recovery import _state
    path = JsonCheckpointStore(tmp_path / "checkpoint-owner").save(_state())
    exports = {"checkpoint": json.loads(path.read_bytes())}
    if fault == "hash": exports["checkpoint"]["state_hash"] = "0"*64
    elif fault == "missing": exports = {}
    actual = edge(formal(snapshot(tmp_path, exports=exports)), "X22")
    if fault == "hash": assert actual.assertion_result is False
    else: assert actual.assertion_result is None and actual.measurement_state == "UNMEASURED"
    assert "ACTUAL_PREDECESSORS_AND_PHYSICAL_REDISPATCH_SET_NOT_RECORDED" in actual.payload["calculation"]["missing_obligations"]


@pytest.mark.parametrize("fault", ["none", "unfinalized", "missing"])
def test_native_X28_actual_public_wrapper_and_usage_keep_missing_idle_proof(tmp_path, fault):
    from application.mainline_service import _observe_new_run
    session = capture(tmp_path)
    summary = {"task_id": "task", "run_id": "run", "status": "success", "shutdown_diagnostics": []}
    @_observe_new_run
    def boundary(_self):
        return summary
    with session:
        assert boundary(None) is summary
    exports = {} if fault == "missing" else {"usage": {"schema_version": "model_usage_v1", "task_id": "task", "run_id": "run", "finalized": fault != "unfinalized", "usage": {}}}
    actual = edge(formal(snapshot(tmp_path, session if fault != "missing" else None, exports)), "X28")
    if fault == "unfinalized": assert actual.assertion_result is False
    else: assert actual.assertion_result is None and actual.measurement_state == "UNMEASURED"
    assert "EXPLICIT_PHYSICAL_IDLE_UNKNOWN_CALL_AND_OWNED_RESOURCE_CLOSE_PROOF_NOT_RECORDED" in actual.payload["calculation"]["missing_obligations"]


def memory_export():
    from core.hashing import canonical_sha256
    from apps.enterprise_document.schemas.agent_rag_v1.memory import (
        CanonicalMemorySourceRefEnvelopeSchema, DurableMemoryRefSchema, DurableMemoryRecordSchema,
        MemoryLogicalOperationRefSchema, MemoryProjectionPayloadEnvelopeSchema, MemorySourceBindingSchema,
    )
    digest = "sha256:"+canonical_sha256({"source": "original"})
    source = CanonicalMemorySourceRefEnvelopeSchema(source_authority="fact_authority", source_kind="fact_snapshot",
        source_scope_ref="section", logical_source_ref="fact:source", exact_source_ref={"source": "original"},
        exact_source_ref_schema_id="fact_ref_v1", exact_source_ref_sha256=digest, source_semantic_fingerprint=digest)
    record = DurableMemoryRecordSchema(memory_ref=DurableMemoryRefSchema(run_id="run", memory_type="fact", scope_ref="section", memory_id="memory"),
        source_binding=MemorySourceBindingSchema(primary_source=source, source_set_sha256=digest, currentness_resolver_id="resolver",
            currentness_policy_version="v1", source_binding_semantic_fingerprint=digest),
        projection=MemoryProjectionPayloadEnvelopeSchema(projection_schema_id="fact", projection_schema_version="v1", projection_payload={"text": "original"}, projection_payload_sha256="sha256:"+canonical_hash({"text": "original"})),
        projection_policy_id="projection", projection_policy_version="v1", committed_operation_ref=MemoryLogicalOperationRefSchema(
            logical_operation_id="memory-operation", operation_kind="MEMORY_PROJECTION_COMMIT", run_id="run", operation_scope_ref="memory"),
        record_semantic_fingerprint=digest, created_at=datetime.now(timezone.utc))
    return record.model_dump(mode="json")


@pytest.mark.parametrize("fault", ["none", "hash", "missing"])
def test_native_X29_original_memory_envelope_partial_negative_missing(tmp_path, fault):
    exports = {"memory": memory_export()} if fault != "missing" else {}
    if fault == "hash": exports["memory"]["source_binding"]["primary_source"]["exact_source_ref_sha256"] = "sha256:"+"0"*64
    actual = edge(formal(snapshot(tmp_path, exports=exports)), "X29")
    if fault == "hash": assert actual.assertion_result is False
    else: assert actual.assertion_result is None and actual.measurement_state == "UNMEASURED"
    assert "ORIGINAL_SOURCE_AUTHORIZATION_NOT_RECORDED" in actual.payload["calculation"]["missing_obligations"]


def native_lifecycle(root):
    from test_multi_agent_v3_lifecycle_commit import _service, _running_run
    service = _service(root)
    run = _running_run(service)
    opened = service.open_attempt(run.identity.run_id, "section:s1", "snapshot-v1")
    attempt = service.activate_attempt(opened.identity.attempt_id, opened.lifecycle_revision)
    return service, run, attempt


@pytest.mark.parametrize("case", ["normal", "one_fault_close", "missing"])
def test_native_X28_real_supervisor_worker_resource_close_and_terminal_owner_exports(tmp_path, case):
    from agent.agent_registry import AgentRegistry
    from agent.supervisor_agent import SupervisorAgent
    from agent.runtime.business_node_execution_runner import WorkflowNodeExecutionRunner
    from application.mainline_service import MainlineApplicationService, _observe_new_run
    from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus, RunLifecycleStatus
    from eval.full_chain.report import emit_report
    service, run, attempt = native_lifecycle(tmp_path)
    worker = WorkflowNodeExecutionRunner(now_iso=lambda: "2026-10-05T00:00:00Z")
    closed = []
    failure = RuntimeError("actual owned-resource close failure")
    class Resource:
        def close(self):
            closed.append("close")
            if case == "one_fault_close": raise failure
    resource = Resource()
    supervisor = SupervisorAgent(AgentRegistry(), {}, model_gateway=gateway(FixedClient()),
        enable_llm_routing=False, workflow_engine=worker, owned_resources=[resource, resource])
    session = capture(tmp_path)
    exports = {}
    @_observe_new_run
    def boundary(_self):
        actual = worker.execute(lambda _control: "fixed", execution_id=attempt.identity.attempt_id,
            commit_scope_id="scope", timeout_seconds=1, thread_name="native-closure-worker",
            attempt_succeeded=lambda value: value == "fixed")
        assert actual.kind == "ok"
        terminal_attempt = service.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision,
            AttemptLifecycleStatus.FAILED, failure_ref="fixed:business-failure")
        terminal_run = service.transition_run(run.identity.run_id, run.lifecycle_revision, RunLifecycleStatus.FAILED,
            reason_ref="fixed:business-failure")
        diagnostics = MainlineApplicationService._close_supervisor(supervisor, primary_error=RuntimeError("original business failure"))
        exports.update(run=terminal_run.model_dump(mode="json"), attempt=terminal_attempt.model_dump(mode="json"),
            usage={"schema_version": "model_usage_v1", "task_id": run.identity.task_id, "run_id": run.identity.run_id,
                   "finalized": True, "usage": supervisor.model_usage_snapshot()},
            supervisor={"owner_type": type(supervisor).__module__+"."+type(supervisor).__name__,
                "_resources_closed": supervisor._resources_closed,
                "shutdown_diagnostics": list(supervisor.shutdown_diagnostics),
                "active_worker_count": worker.active_worker_count,
                "wait_for_idle_result": worker.wait_for_idle(.1),
                "execution_control": actual.execution_control.metadata(), "diagnostic_config": {"session_id": session.config.session_id}})
        return {"task_id": run.identity.task_id, "run_id": run.identity.run_id, "status": "failed", "shutdown_diagnostics": diagnostics}
    with session:
        assert boundary(None)["status"] == "failed"
    assert closed == ["close"]  # duplicate ownership is closed exactly once
    if case == "missing": del exports["supervisor"]
    view = snapshot(tmp_path, session, exports)
    frozen = file_hashes(tmp_path)
    report = formal(view)
    actual = edge(report, "X28")
    expected = True if case == "normal" else False if case == "one_fault_close" else None
    assert actual.assertion_result is expected
    assert actual.measurement_state == ("UNMEASURED" if expected is None else "MEASURED")
    assert actual.source_refs and actual.target_refs
    assert file_hashes(tmp_path) == frozen
    machine, _ = emit_report(report, tmp_path / "formal-report", source_roots=[session.path.parent])
    assert next(r for r in json.loads(machine.read_bytes())["edge_fidelity"] if r["edge_id"] == "X28")["assertion_result"] is expected


def native_graph_recovery_export(root, session):
    """Create a real native checkpoint and inspect/preflight it without invoking a business stage."""
    from types import SimpleNamespace
    from agent.runtime.graph_state import GraphStateSchema
    from agent.runtime.workflow_schema import WorkflowDefinitionSchema
    from apps.enterprise_document.schemas.scheme_generation_graph import SchemeGenerationGraphPayloadSchema
    from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
    from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
    service, run, attempt = native_lifecycle(root)
    class BoundedStage:
        runtime_support = SimpleNamespace(now_iso=lambda: "2026-10-05T00:00:00Z")
        target_post_write_composition = None
        section_execution_coordinator = None
        def __getattr__(self, name):
            def refused(*args, **kwargs):
                raise AssertionError("business stage must not execute during native checkpoint fixture: "+name)
            return refused
    engine = EnterpriseDocumentWorkflowEngine(stage_service=BoundedStage())
    workflow = WorkflowDefinitionSchema(workflow_id="native-recovery", workflow_name="native-recovery",
        workflow_version="v1", task_type="scheme_generation", steps=[], created_at="2026-10-05T00:00:00Z")
    state = GraphStateSchema(task_id=run.identity.task_id, run_id=run.identity.run_id,
        task_type="scheme_generation", user_input="native checkpoint fixture", created_at="2026-10-05T00:00:00Z",
        context_bundle=ContextBundleSchema(user=UserContextSchema(user_query="native checkpoint fixture"),
            task=TaskContextSchema(task_id=run.identity.task_id, run_id=run.identity.run_id, task_type="scheme_generation")))
    runtime = engine.runtime_state_codec.build_initial_state(workflow, state)
    runtime.update(engine.business_state_codec.build_initial_update(SchemeGenerationGraphPayloadSchema(
        task_id=run.identity.task_id, run_id=run.identity.run_id, workflow_namespace=engine.runtime_state_codec.workflow_namespace(workflow), started_at="2026-10-05T00:00:00Z")))
    runtime["current_business_node"] = "plan_document"
    graph = engine.build_graph(workflow)
    config = engine._invoke_config(workflow, run_id=run.identity.run_id)
    # Use the same standard-empty namespace carrier that original inspect()
    # uses; the existing saver adapter restores workflow_namespace for storage.
    query_config = {**config, "configurable": {**config["configurable"], "checkpoint_ns": ""}}
    graph.update_state(query_config, runtime, as_node="plan_document")
    inspection = engine.inspect(workflow, run_id=run.identity.run_id)
    with session.span("engine.recovery_plan", "CHECKPOINT_RECOVERY_PREFLIGHT", run_id=run.identity.run_id,
                      attempt_id=attempt.identity.attempt_id):
        plan = engine.recovery_plan(workflow, run_id=run.identity.run_id)
    history = [{name: getattr(item, name) for name in ("config", "checkpoint", "metadata", "parent_config", "pending_writes")}
               for item in engine.checkpoint_history(workflow, run_id=run.identity.run_id)]
    return {"attempt": attempt.model_dump(mode="json"), "graph_owner": {
        "owner_type": type(engine).__module__+"."+type(engine).__name__, "workflow": workflow.model_dump(mode="json"),
        "inspection": inspection, "recovery_plan": plan, "checkpoint_history": history}}


@pytest.mark.parametrize("case", ["normal", "one_fault_checkpoint", "missing"])
def test_native_X22_actual_graph_checkpoint_inspect_recovery_preflight_formal_entry(tmp_path, case):
    from eval.full_chain.report import emit_report
    session = capture(tmp_path)
    with session:
        exports = native_graph_recovery_export(tmp_path, session)
    if case == "one_fault_checkpoint":
        exports["graph_owner"]["recovery_plan"]["checkpoint_config"]["configurable"]["checkpoint_id"] = "wrong-original-checkpoint"
    elif case == "missing": del exports["graph_owner"]
    view = snapshot(tmp_path, session, exports)
    before = file_hashes(tmp_path)
    report = formal(view)
    actual = edge(report, "X22")
    expected = True if case == "normal" else False if case == "one_fault_checkpoint" else None
    assert actual.assertion_result is expected
    assert actual.measurement_state == ("UNMEASURED" if expected is None else "MEASURED")
    assert actual.source_refs and actual.target_refs
    assert file_hashes(tmp_path) == before
    machine, _ = emit_report(report, tmp_path / "formal-report", source_roots=[session.path.parent])
    assert next(r for r in json.loads(machine.read_bytes())["edge_fidelity"] if r["edge_id"] == "X22")["assertion_result"] is expected
