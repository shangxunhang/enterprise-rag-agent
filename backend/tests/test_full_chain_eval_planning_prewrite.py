"""P01-P12 local deterministic contracts, without inference or historical repair."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from eval.full_chain.contracts import canonical_hash
from core.hashing import canonical_sha256
from eval.full_chain.snapshot import SnapshotView
from eval.full_chain.domains.planning_prewrite import (check_context_edge, check_fanin_remap,
    check_package_edge, check_planning_lineage, evaluate_planning_prewrite)


def _evaluate(records, references=None):
    return evaluate_planning_prewrite(SnapshotView.from_records(records), references=references)


def _codes(batch):
    return {finding.code for finding in batch.findings}


def _member(ref="evidence:run1:a", *, run="run1"):
    return {"id": "a", "ref": ref, "hash": "a" * 64, "scope": "section:s1", "run_id": run, "round_id": "r1"}


def _fanin(identity="source:project1:a", target="C2"):
    return {"section_id": "s2", "merge_order": 2,
        "local_registry": [{"citation_id": "C1", "source_identity": identity}],
        "global_registry": [{"citation_id": target, "source_identity": identity}],
        "accepted_source_identities": [identity], "id_map": {"C1": target},
        "before": {"content": "A[C1]", "model_output": "A[C1]", "prompt": "Evidence[C1]"},
        "after": {"content": f"A[{target}]", "model_output": f"A[{target}]", "prompt": f"Evidence[{target}]"},
        "after_bindings": [{"citation_id": target, "target_marker_index": 0}]}


def test_P01_scope_error_before_task_keeps_evaluation_subject():
    batch = _evaluate({"P-INGRESS": {"subject_id": "ingress:fixed-source-hash", "request_hash": "a" * 64,
        "failure_stage": "scope_validation", "exception_type": "ScopeError", "task_id": None, "run_id": None}})
    ingress = next(s for s in batch.stages if s.unit_id == "B01")
    assert ingress.subject_id == "ingress:fixed-source-hash"
    assert ingress.measurement_state == "TECHNICAL_FAILURE"
    assert ingress.payload["task_id"] is None
    assert "ENTRY_FAILURE" in _codes(batch)
    assert all(s.measurement_state == "UNMEASURED" for s in batch.stages if s.unit_id != "B01")


def test_P02_unknown_fact_becomes_definite_is_planning_contamination():
    key = {"project": "p1", "subject": "server", "property": "capacity", "time": "2026", "unit": "GB"}
    batch = _evaluate({"P-FACT": {"run_id": "r1", "facts": [{"key": key, "state": "KNOWN", "value": 128}]}},
        {"facts": [{"key": key, "state": "UNKNOWN", "value": None}]})
    assert "UNKNOWN_FACT_ASSERTED" in _codes(batch)
    assert next(m for m in batch.metrics if m.metric_id == "fact_precision").value == 0


def test_P03_multiple_valid_plans_allow_order_but_missing_requirement_fails():
    reference = {"required_requirements": ["r1", "r2"]}
    for sections in (["s1", "s2"], ["s2", "s1"]):
        batch = _evaluate({"P-PLAN": {"run_id": "run", "sections": sections, "requirement_ids": ["r2", "r1"], "dependencies": {}}}, reference)
        assert "PLAN_REQUIREMENT_MISSING" not in _codes(batch)
    missing = _evaluate({"P-PLAN": {"run_id": "run", "requirement_ids": ["r1"]}}, reference)
    assert "PLAN_REQUIREMENT_MISSING" in _codes(missing)
    assert next(m for m in missing.metrics if m.metric_id == "requirement_recall").value == .5


def test_P04_uncompleted_dependency_cannot_be_excused_by_good_writer():
    batch = _evaluate({"P-DISPATCH": {"run_id": "r1", "section_id": "s2", "dependencies_complete": False,
        "authorized": True, "writer_output_quality": "PASS"}})
    assert "UNSATISFIED_DEPENDENCY_DISPATCH" in _codes(batch)


def test_P05_same_cardinality_cross_run_ref_is_package_mismatch():
    original = {"members": [_member()]}
    assert check_package_edge(original, deepcopy(original)).assertion_result is True
    other = {"members": [_member("evidence:run2:a", run="run2")]}
    edge = check_package_edge(original, other)
    assert edge.assertion_result is False
    assert edge.reason == "PACKAGE_IDENTITY_MISMATCH"
    assert check_package_edge({"count": 1}, {"count": 1}).measurement_state == "UNMEASURED"


def test_P06_not_supported_is_semantic_negative_bad_json_is_technical():
    negative = _evaluate({"P-ADMISSION": {"run_id": "r1", "semantic_state": "NOT_SUPPORTED"}})
    assert "CRITERION_TECHNICAL_FAILURE" not in _codes(negative)
    broken = _evaluate({"P-ADMISSION": {"run_id": "r1", "exception_type": "JSONDecodeError"}})
    assert "CRITERION_TECHNICAL_FAILURE" in _codes(broken)
    assert not any(m.metric_id == "criterion_observed_state" for m in broken.metrics)


def test_P07_recovery_checkpoint_restart_concurrent_dispatch_and_budget(tmp_path):
    import test_gate_v2_pre_write_recovery as fixtures
    import test_checkpoint_storage_compression as checkpoint_fixture
    from agent.runtime.official_checkpoint_saver import create_sqlite_checkpoint_resource
    from apps.enterprise_document.services.scheme_writer.pre_write_recovery import PreWriteRecoveryController, PreWriteRecoveryIntegrityError
    from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore, RecoveryDispatchStatus

    controller = PreWriteRecoveryController(execution_store=SQLitePreWriteExecutionStore(tmp_path / "dispatch.sqlite3"))
    chain = fixtures._chain(name="fce-local", blockers={"c1"}, bundle_query="e1")
    reservation = controller.prepare_round_reservation(chain=chain, applicability=fixtures._applicable(chain),
        runtime_state=fixtures._state(controller, rounds=1, no_progress=1))
    material = {"runtime_state": reservation.state_after_reservation.model_dump(mode="json"),
        "occurrence_id": reservation.occurrence_id, "fingerprint": reservation.semantic_fingerprint}
    checkpoint_path = tmp_path / "checkpoint.sqlite3"
    resource = create_sqlite_checkpoint_resource(checkpoint_path)
    resource.saver.put(checkpoint_fixture._config("fce"), checkpoint_fixture._checkpoint("cp-1", material),
        {"source": "input", "step": 0, "parents": {}}, {})
    resource.close()
    reopened = create_sqlite_checkpoint_resource(checkpoint_path)
    restored = reopened.saver.get_tuple(checkpoint_fixture._config("fce")).checkpoint["channel_values"]["payload"]
    reopened.close()
    assert restored == material
    controllers = [PreWriteRecoveryController(execution_store=SQLitePreWriteExecutionStore(tmp_path / "dispatch.sqlite3")) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda pair: pair[1].claim_physical_dispatch(reservation, owner_id="worker-" + str(pair[0])), enumerate(controllers)))
    assert sorted(o.status.value for o in outcomes) == sorted([RecoveryDispatchStatus.ACQUIRED.value, RecoveryDispatchStatus.IN_PROGRESS.value])
    with pytest.raises(PreWriteRecoveryIntegrityError):
        controller.prepare_round_reservation(chain=chain, applicability=fixtures._applicable(chain), runtime_state=reservation.state_after_reservation)
    batch = _evaluate({"P-RECOVERY": {"run_id": "r1", "rounds_used": restored["runtime_state"]["recovery_rounds_used"],
        "rounds_limit": 1, "cost_used": 3, "budget_limit": 2}})
    assert "RECOVERY_QUOTA_OR_BUDGET_EXCEEDED" in _codes(batch)


def test_P08_protected_unknown_lost_at_context_selection():
    from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
    from schemas.context import ContextSourceFamily
    import test_multi_agent_v3_context_runtime as fixtures
    runtime = ApplicationContextRuntime()
    state = {"candidates": (fixtures._candidate(ContextSourceFamily.FACT_SNAPSHOT, candidate_id="fact", ref="fact:1",
        exact_payload={"capacity": "未确认"}),)}
    fixtures._register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    package = fixtures._materialize(runtime, policy=fixtures._policy(fixtures._rule(ContextSourceFamily.FACT_SNAPSHOT)))
    material = package.model_input.final_input_material.inline_final_input_payload
    record = {"run_id": "run-1", "final_input_material": material}
    assert "CONTEXT_PROTECTED_VALUE_LOST" not in _codes(_evaluate({"P-CONTEXT": record}, {"protected_values": {"capacity": "未确认"}}))
    damaged = json.loads(json.dumps(record, ensure_ascii=False).replace("未确认", "128GB"))
    assert "CONTEXT_PROTECTED_VALUE_LOST" in _codes(_evaluate({"P-CONTEXT": damaged}, {"protected_values": {"capacity": "未确认"}}))


def test_P09_same_local_alias_different_sources_cannot_cross_bind():
    first, second = _fanin("source:project1:a", "C1"), _fanin("source:project2:b", "C2")
    assert check_fanin_remap(first).assertion_result is True
    assert check_fanin_remap(second).assertion_result is True
    second["global_registry"][0]["source_identity"] = "source:project1:a"
    assert check_fanin_remap(second).assertion_result is False


def test_P10_production_registry_legal_rename_preserves_source_and_raw():
    from apps.enterprise_document.services.scheme_writer.document_citation_registry import DocumentCitationRegistry
    from schemas.citation import CitationSchema
    registry = DocumentCitationRegistry()
    registry.register([CitationSchema(citation_id="C1", source_type="document", doc_id="project-a", quote_text="first")], scope="s1", query="q")
    second = CitationSchema(citation_id="C1", source_type="document", doc_id="project-b", quote_text="second")
    committed, mapping = registry.register([second], scope="s2", query="q")
    data = _fanin(DocumentCitationRegistry._identity(second), committed[0].citation_id)
    data["id_map"] = mapping
    raw = json.dumps({"messages": [{"role": "user", "content": "Evidence[C1]"}]})
    assert check_fanin_remap(data).assertion_result is True
    assert canonical_hash(data["before"]) != canonical_hash(data["after"])
    assert "[C1]" in raw


def test_P11_legal_fallback_does_not_assert_writer_quality():
    batch = _evaluate({"P-ENFORCEMENT": {"run_id": "r1", "action": "FALLBACK_WRITE", "allowed_actions": ["FALLBACK_WRITE"],
        "writer_output": "unsupported 128GB fact"}})
    assert next(m for m in batch.metrics if m.metric_id == "policy_conformance").assertion_result is True
    assert all(s.observed_quality_status == "UNKNOWN" for s in batch.stages)


def test_P12_as_of_valid_current_stale_keeps_historical_verdict():
    batch = _evaluate({"P-RECOVERY": {"run_id": "r1", "as_of_commit_valid": True, "current_commit_valid": False}})
    metric = next(m for m in batch.metrics if m.metric_id == "historical_currentness")
    assert metric.value == "AS_OF_VALID_CURRENT_STALE" and metric.assertion_result is True


def test_actual_request_lineage_and_display_projection_are_distinct():
    material = {"input_kind": "MESSAGES", "messages": [{"role": "user", "content": "Evidence[C1]"}]}
    context = {"run_id": "r1", "invocation_id": "i1", "final_input_material": material, "final_input_hash": canonical_sha256(material)}
    request = {"representation_kind": "MODEL_ATTEMPT_REQUEST", "run_id": "r1", "invocation_id": "i1", "messages": material["messages"]}
    assert check_context_edge(context, request).assertion_result is True
    wrong = dict(request, run_id="r2")
    assert check_context_edge(context, wrong).assertion_result is False
    display = dict(request, representation_kind="DISPLAY_PROMPT")
    assert check_context_edge(context, display).measurement_state == "UNMEASURED"


def test_context_capture_enabled_disabled_same_business_package(tmp_path):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession, activate_diagnostics
    from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
    from schemas.context import ContextSourceFamily
    import test_multi_agent_v3_context_runtime as fixtures
    runtime = ApplicationContextRuntime()
    state = {"candidates": (fixtures._candidate(ContextSourceFamily.FACT_SNAPSHOT, candidate_id="fact", ref="fact:1", exact_payload={"capacity": "未确认"}),)}
    fixtures._register(runtime, ContextSourceFamily.FACT_SNAPSHOT, state)
    policy = fixtures._policy(fixtures._rule(ContextSourceFamily.FACT_SNAPSHOT))
    baseline = fixtures._materialize(runtime, policy=policy)
    session = DiagnosticSession(DiagnosticConfig(tmp_path / "capture", tmp_path, "context-equivalence"))
    with activate_diagnostics(session):
        captured = fixtures._materialize(runtime, policy=policy)
    session.close()
    assert baseline.context_semantic_fingerprint == captured.context_semantic_fingerprint
    assert baseline.package_ref == captured.package_ref
    assert baseline.model_input == captured.model_input
    events = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("event_type") == "P-CONTEXT" and e.get("final_input_material") == captured.model_input.final_input_material.inline_final_input_payload for e in events)
    assert session.lost_count == 0


def planning_edge_fixture(edge_id, *, negative=False):
    """Finite fixed endpoint factory used by cross-domain offline integration."""
    if edge_id == "X08":
        source, target = {"members": [_member()]}, {"members": [_member()]}
        if negative:
            target["members"][0]["ref"] = "evidence:wrong-run:a"
    elif edge_id == "X09":
        material = {"input_kind": "MESSAGES", "messages": [{"role": "user", "content": "未确认[C1]"}]}
        source = {"run_id": "r1", "invocation_id": "i1", "final_input_material": material, "final_input_hash": canonical_sha256(material)}
        target = {"representation_kind": "MODEL_ATTEMPT_REQUEST", "run_id": "r1", "invocation_id": "i1", "messages": deepcopy(material["messages"])}
        if negative:
            target["messages"][0]["content"] = "known128[C1]"
    elif edge_id == "X25":
        source = {"subject_id": "ingress:fixed", "task_id": None, "error_type": "ScopeError"}
        target = {"subject_id": "ingress:fixed", "task_id": None, "production_task_created": False}
        if negative:
            target["task_id"] = "fabricated-task"
    elif edge_id == "X26":
        source = _fanin()
        target = None
        if negative:
            source["global_registry"][0]["source_identity"] = "cross-project-other-source"
    elif edge_id == "X30":
        source = {"project_input_ref": "project:1", "fact_snapshot_ref": "fact:v1", "run_id": "r1", "scope": "s1"}
        target = {"planner_input_refs": ["project:1", "fact:v1"], "plan_ref": "plan:v1", "research_plan_ref": "plan:v1",
            "route_run_id": "r1", "route_scope": "s1"}
        if negative:
            target["research_plan_ref"] = "plan:v2"
    else:
        raise ValueError(edge_id)
    return source, target


@pytest.mark.parametrize("edge_id", ["X08", "X09", "X25", "X26", "X30"])
def test_planning_edges_have_normal_negative_and_missing_endpoints(edge_id):
    def check(source, target):
        if edge_id == "X08":
            return check_package_edge(source, target)
        if edge_id == "X09":
            return check_context_edge(source, target)
        if edge_id == "X26":
            return check_fanin_remap(source)
        return check_planning_lineage(edge_id, source, target)
    normal = planning_edge_fixture(edge_id)
    negative = planning_edge_fixture(edge_id, negative=True)
    assert check(*normal).assertion_result is True
    assert check(*negative).assertion_result is False
    assert check(None, None).measurement_state == "UNMEASURED"


def planning_edge_snapshot(edge_id, *, negative=False):
    source, target = planning_edge_fixture(edge_id, negative=negative)
    records = {"X08": lambda: {"P-PACKAGE": {"run_id": "r1", "bundle": source, "package": target}},
        "X09": lambda: {"P-CONTEXT": {**source, "actual_request": target}},
        "X25": lambda: {"P-INGRESS": source, "P-EVALUATION-SUBJECT": target},
        "X26": lambda: {"P-FANIN": source},
        "X30": lambda: {"P-LINEAGE": {"run_id": "r1", "source": source, "target": target}}}[edge_id]()
    return SnapshotView.from_records(records)


def test_real_capture_envelopes_fanin_join_and_early_error_subject():
    from eval.full_chain.domains.planning_prewrite import capture_planning_postwrite_snapshot, extract_planning_prewrite
    data = _fanin()
    before = {"phase": "before", **{k: data[k] for k in ("section_id", "local_registry", "before")}}
    after = {"phase": "after", **{k: data[k] for k in ("section_id", "global_registry", "id_map", "after", "after_bindings")}}
    rows = [{"event_type": "P-FANIN", "operation_ref": "s2", "payload": before},
        {"event_type": "P-FANIN", "operation_ref": "s2", "payload": after},
        {"event_type": "P-INGRESS", "payload": {"subject_id": "ingress:original", "phase": "error", "error_type": "ScopeError"}}]
    batch = extract_planning_prewrite(capture_planning_postwrite_snapshot(rows), subject_id="fallback-evaluation")
    assert next(e for e in batch.edges if e.edge_id == "X26").assertion_result is True
    assert next(e for e in batch.edges if e.edge_id == "X25").assertion_result is True
    assert any(s.subject_id == "ingress:original" and s.measurement_state == "TECHNICAL_FAILURE" for s in batch.stages)


def test_actual_context_package_summary_is_physical_request_lineage():
    material = {"input_kind": "MESSAGES", "messages": [{"role": "user", "content": "unconfirmed"}]}
    context = {"run_id": "r1", "attempt_id": "attempt1", "invocation_id": "model_call_r1_s1",
        "context_package_ref": {"context_package_id": "ctxpkg_exact"}, "final_input_material": material,
        "final_input_hash": canonical_sha256(material)}
    request = {"representation_kind": "MODEL_ATTEMPT_REQUEST", "run_id": "r1", "model_call_id": "model_call_r1_s1",
        "messages": material["messages"], "extra": {"llm_context_summary": {"package_id": "ctxpkg_exact", "final_model_input_sha256": canonical_sha256(material)}}}
    assert check_context_edge(context, request).assertion_result is True
    altered = deepcopy(request)
    altered["extra"]["llm_context_summary"]["package_id"] = "ctxpkg_other"
    assert check_context_edge(context, altered).assertion_result is False


@pytest.mark.parametrize("mutation", ["normal", "negative", "missing"])
def test_same_capture_source_actual_request_material_connects_context(tmp_path, mutation):
    from test_full_chain_eval_capture import FixedClient, gateway, request, session, captured
    from eval.full_chain.domains.planning_prewrite import extract_planning_prewrite, capture_planning_postwrite_snapshot
    physical_request = request().model_copy(update={"extra": {}})
    material = {"input_kind": "MESSAGES", "messages": physical_request.messages}
    context = {"run_id": physical_request.run_id, "invocation_id": physical_request.model_call_id,
        "final_input_material": material, "final_input_hash": canonical_sha256(material)}
    observer = session(tmp_path, "same-source")
    client = FixedClient()
    with observer:
        observer.emit({"event_type": "P-CONTEXT", "payload": context})
        gateway(client).generate(physical_request)
    assert len(client.calls) == 1
    rows = captured(observer, tmp_path)["events"]
    before = observer.path.read_bytes()
    rows = deepcopy(rows)
    if mutation == "negative":
        actual = next(row for row in rows if row["event_type"] == "MODEL_ATTEMPT_REQUEST")
        actual["material"]["messages"][0]["content"] = "changed actual physical message"
    elif mutation == "missing":
        rows = [row for row in rows if row["event_type"] != "MODEL_ATTEMPT_REQUEST"]
    batch = extract_planning_prewrite(capture_planning_postwrite_snapshot(rows))
    edges = [edge for edge in batch.edges if edge.edge_id == "X09"]
    assert len(edges) == 1
    assert edges[0].assertion_result is {"normal": True, "negative": False, "missing": None}[mutation]
    assert edges[0].source_refs
    if mutation != "missing":
        assert edges[0].target_refs and edges[0].payload["target_observation"]["model_call_id"] == physical_request.model_call_id
    assert observer.path.read_bytes() == before


def test_context_multiple_original_invocations_keeps_later_bad_edge_and_unbound_request():
    from eval.full_chain.domains.planning_prewrite import extract_planning_prewrite
    rows = []
    for index in (1, 2):
        context, actual = planning_edge_fixture("X09", negative=index == 2)
        context.update(run_id="r" + str(index), invocation_id="i" + str(index))
        actual.update(run_id=context["run_id"], invocation_id=context["invocation_id"])
        rows.extend([{"event_type": "P-CONTEXT", "payload": context},
            {"event_type": "MODEL_ATTEMPT_REQUEST", "material": actual}])
    rows.append({"event_type": "MODEL_ATTEMPT_REQUEST", "material": {"run_id": "unbound", "model_call_id": "unbound", "messages": []}})
    batch = extract_planning_prewrite(SnapshotView.from_records({"diagnostics": rows}))
    edges = [edge for edge in batch.edges if edge.edge_id == "X09"]
    assert [edge.assertion_result for edge in edges] == [True, False, None]
    assert all(edge.source_refs or edge.target_refs for edge in edges)
    assert any(edge.payload["target_observation"].get("model_call_id") == "unbound" for edge in edges if edge.payload.get("target_observation"))


def test_context_ambiguous_same_call_without_package_binding_remains_unknown():
    from eval.full_chain.domains.planning_prewrite import extract_planning_prewrite
    first, actual = planning_edge_fixture("X09")
    second = deepcopy(first)
    second["final_input_material"]["messages"][0]["content"] = "different original Context"
    second["final_input_hash"] = canonical_sha256(second["final_input_material"])
    rows = [{"event_type": "P-CONTEXT", "payload": row} for row in (first, second)]
    rows.append({"event_type": "MODEL_ATTEMPT_REQUEST", "material": actual})
    edges = [edge for edge in extract_planning_prewrite(SnapshotView.from_records({"diagnostics": rows})).edges if edge.edge_id == "X09"]
    assert len(edges) == 3 and all(edge.assertion_result is None for edge in edges)
    assert all(edge.source_refs or edge.target_refs for edge in edges)


def test_artifact_runtime_domain_scope_ref_is_preserved_as_original_scope():
    from eval.full_chain.domains.planning_prewrite import extract_planning_prewrite
    original_scope = {"domain_scope_type": "SECTION", "section_id": "section-exact"}
    row = {"event_type": "version_committed", "version_ref": {"logical_ref": {
        "artifact_type": "fact_snapshot", "run_id": "r1", "domain_scope_ref": original_scope}}}
    stages = extract_planning_prewrite(SnapshotView.from_records({"artifact_runtime": [row]})).stages
    stage = next(stage for stage in stages if stage.observed_execution_status == "PERSISTED_OWNER_RECORD")
    assert stage.scope == str(original_scope)
    assert stage.payload["logical_ref"]["domain_scope_ref"] == original_scope


def _native_single_workflow_route():
    from agent.runtime.workflow_schema import WorkflowDefinitionSchema
    from agent.services.workflow_router import WorkflowCatalog, WorkflowRouter
    from schemas.task import TaskSchema
    from test_full_chain_eval_capture import FixedClient, gateway

    task = TaskSchema(task_id="task-native-route", run_id="run-native-route", task_type="scheme_generation",
        user_input="fixed routing fixture", project_input={}, created_at="2026-10-05T00:00:00Z")
    workflow = WorkflowDefinitionSchema(workflow_id="scheme_generation_v2", workflow_name="Fixed workflow",
        task_type=task.task_type, workflow_version="v2.0", created_at=task.created_at)
    client = FixedClient()
    fixed_gateway = gateway(client)
    gateway_calls = []
    original_generate = fixed_gateway.generate

    def counted_generate(*args, **kwargs):
        gateway_calls.append((args, kwargs))
        return original_generate(*args, **kwargs)

    fixed_gateway.generate = counted_generate
    router = WorkflowRouter(WorkflowCatalog({workflow.task_type: workflow}),
        model_gateway=fixed_gateway, enable_llm_routing=True)
    return task, workflow, router.route(task), gateway_calls, client


@pytest.mark.parametrize("mutation", ["normal", "outside_allowed", "missing_routing"])
def test_B02_explicit_native_runtime_trace_route_contract(tmp_path, mutation):
    from hashlib import sha256
    from agent.services.workflow_trace import WorkflowTraceService
    from data_capture.run_trace_recorder import JsonlRunTraceRecorder
    from eval.full_chain.domains.planning_prewrite import extract_planning_prewrite
    from eval.full_chain.snapshot import load_source_snapshot

    task, workflow, decision, _, _ = _native_single_workflow_route()
    payload = {"routing": deepcopy(decision.metadata), "workflow": workflow.model_dump(mode="json"),
        "task": task.model_dump(mode="json")}
    if mutation == "outside_allowed":
        payload["routing"]["selected_task_type"] = "unregistered_task_type"
    elif mutation == "missing_routing":
        payload.pop("routing")
    trace = WorkflowTraceService(JsonlRunTraceRecorder(tmp_path))
    trace.start_run(task=task, component_name="SupervisorAgent")
    trace.start_workflow(task=task, workflow=workflow, payload=payload)
    path = tmp_path / (task.run_id + "_trace.jsonl")
    original = path.read_bytes()
    row = json.loads(original.splitlines()[1])
    declaration = {"name": "declared-trace", "family": "runtime_trace", "format": "jsonl", "path": str(path),
        "owner": "JsonlRunTraceRecorder", "schema_version": "run_trace_event_v2", "sha256": sha256(original).hexdigest()}
    manifest = {"schema_version": "full_chain_source_manifest_v1", "snapshot_id": "native-route", "sources": [declaration]}
    snapshot = load_source_snapshot(manifest, [tmp_path])
    batch = extract_planning_prewrite(snapshot)
    stage = next(stage for stage in batch.stages if stage.unit_id == "B02")
    metrics = {metric.metric_id: metric for metric in batch.metrics if metric.unit_id == "B02"}
    contract = metrics["route_contract_conformance"]
    assert contract.assertion_result == {"normal": True, "outside_allowed": False, "missing_routing": None}[mutation]
    assert stage.measurement_state == ("UNMEASURED" if mutation == "missing_routing" else "MEASURED")
    assert stage.payload == row and stage.operation_id == row["event_id"] and stage.subject_id == task.run_id
    assert stage.evidence_refs[0].locator == str(path) and stage.evidence_refs[0].pointer == "/1"
    assert stage.evidence_refs[0].raw_sha256 == sha256(original).hexdigest()
    assert metrics["task_classification_accuracy"].measurement_state == ("N/A" if mutation == "normal" else "UNMEASURED")
    assert metrics["task_classification_accuracy"].value is None
    assert metrics["router_llm_call_count"].measurement_state == "UNMEASURED"
    assert metrics["router_llm_call_count"].value is None
    assert ("ROUTE_CONTRACT_MISMATCH" in _codes(batch)) == (mutation == "outside_allowed")
    assert path.read_bytes() == original
    # An unrelated owner family cannot be guessed as routing authority.
    unrelated = extract_planning_prewrite(SnapshotView.from_records({"owner_records": [row]}))
    assert not any(metric.unit_id == "B02" for metric in unrelated.metrics)
    assert next(stage for stage in unrelated.stages if stage.unit_id == "B02").measurement_state == "UNMEASURED"


def test_B02_real_workflow_router_single_workflow_fixed_gateway_zero_calls():
    task, workflow, decision, gateway_calls, client = _native_single_workflow_route()
    assert decision.task_type == task.task_type == workflow.task_type
    assert decision.metadata["routing_mode"] == "single_workflow_direct"
    assert decision.metadata["allowed_task_types"] == [workflow.task_type]
    assert decision.metadata["reason"] == "only_one_workflow_registered"
    assert decision.model_response is None
    assert gateway_calls == [] and client.calls == []
