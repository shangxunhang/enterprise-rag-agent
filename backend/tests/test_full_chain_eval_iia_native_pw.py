"""IIA corrections: actual owner files through safe reader and formal report."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.runner import observe
from eval.full_chain.report import emit_report
from eval.full_chain.snapshot import load_source_snapshot


def _frozen_rows(tmp_path, rows_by_family):
    source = tmp_path / 'frozen-source'
    source.mkdir()
    declarations = []
    originals = {}
    for family, rows in rows_by_family.items():
        path = source / (family + '.jsonl')
        raw = b''.join(json.dumps(row, ensure_ascii=False).encode('utf-8') + b'\n' for row in rows)
        path.write_bytes(raw)
        originals[path] = raw
        declarations.append({'name':family, 'family':family, 'format':'jsonl', 'path':str(path),
            'sha256':sha256(raw).hexdigest(), 'owner':'FROZEN_NATIVE_OWNER_RECORDS'})
    manifest = {'schema_version':'full_chain_source_manifest_v1', 'snapshot_id':'iia-native', 'sources':declarations}
    return load_source_snapshot(manifest, [source]), originals


def _formal_report(tmp_path, snapshot, originals):
    report = observe(snapshot, EvaluationRunIdentity('IIA-native-PW', 'isolated-native'))
    assert not any(finding.code == 'DOMAIN_READ_FAILURE' for finding in report.findings)
    paths = emit_report(report, tmp_path / 'formal-report', source_roots=[tmp_path / 'frozen-source'])
    persisted = json.loads(paths[0].read_text(encoding='utf-8'))
    for path, raw in originals.items():
        assert path.read_bytes() == raw
    return report, persisted


def _safe_captured_snapshot(source, observer, extra_sources=(), *, excluded_tables=()):
    declarations = [{"name":"diagnostics", "family":"diagnostics", "format":"diagnostic_jsonl",
        "path":str(observer.path), "sha256":sha256(observer.path.read_bytes()).hexdigest(),
        "representation_files":sorted({row["payload_ref"]["relative_path"] for row in
            map(json.loads, observer.path.read_bytes().splitlines()) if row.get("payload_ref")})}]
    for name, family, path, format_ in extra_sources:
        spec = {"name":name, "family":family, "format":format_, "path":str(path), "sha256":sha256(path.read_bytes()).hexdigest()}
        if format_ == "sqlite":
            spec.update(frozen=True, schema_version="gate_v2_post_write_store_v4", tables=["post_write_metadata",
                "artifact_states", "post_write_entries", "validation_completions", "validation_facts",
                "delivery_gate_decisions", "post_write_control_decisions", "requirement_snapshots", "quality_delivery_contracts"])
            spec["tables"] = [table for table in spec["tables"] if table not in excluded_tables]
        declarations.append(spec)
    snapshot = load_source_snapshot({"schema_version":"full_chain_source_manifest_v1", "snapshot_id":"native-PW",
        "sources":declarations}, [source], output_root=source.parent / "reader-copy")
    assert not snapshot.limitations, snapshot.limitations
    return snapshot


@pytest.mark.parametrize("mutation", ["normal", "negative", "missing"])
def test_iia_native_required_declaration_completion_gate_reaches_formal_report(tmp_path, monkeypatch, mutation):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession
    from test_full_chain_eval_postwrite_release import _minimal_composition
    from apps.enterprise_document.schemas.post_write import ValidationCompletionItemSchema, ValidationCompletionItemState
    from apps.enterprise_document.services.post_write import DeliveryGateService, GateDecisionStore, GateInputIntegrityGuard, PostWriteEntryStore, QualityDeliveryContractStore
    source = tmp_path / "frozen-source"; source.mkdir()
    composition, kwargs = _minimal_composition(source / "owner")
    def fixed_integrity(**arguments):
        return ValidationCompletionItemSchema(expected_validation_item_ref="citation:integrity:" + arguments["artifact_ref"].artifact_hash,
            required=True, state=ValidationCompletionItemState.NOT_APPLICABLE)
    monkeypatch.setattr(composition.citation_integrity, "complete_item", fixed_integrity)
    composition.gate_service = DeliveryGateService(input_guard=GateInputIntegrityGuard(validation_store=composition.validation_store,
        entry_store=PostWriteEntryStore(composition.database), contract_store=QualityDeliveryContractStore(composition.database)),
        decision_store=GateDecisionStore(composition.database), clock=lambda:2000.)
    observer = DiagnosticSession(DiagnosticConfig(source / "capture", source, "native-required", flush_records=1))
    emit = observer.emit
    def fixed_boundary(payload):
        if payload.get("event_type") == "S-PLAN":
            if mutation == "missing":
                return
            if mutation == "negative":
                payload = {**payload, "items":[{**item, "expected_validation_item_ref":"foreign-required-item"} if index == 0 else item
                    for index,item in enumerate(payload["items"])]}
        return emit(payload)
    monkeypatch.setattr(observer, "emit", fixed_boundary)
    with observer:
        composition._complete_validation(**kwargs)
    snapshot = _safe_captured_snapshot(source, observer, [("postwrite","postwrite",composition.database.path,"sqlite")])
    originals = {path:path.read_bytes() for path in source.rglob("*") if path.is_file()}
    report, machine = _formal_report(tmp_path, snapshot, originals)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X17"]
    expected = {"normal":True,"negative":False,"missing":None}[mutation]
    assert edges and all(edge.assertion_result is expected for edge in edges), [(e.reason,e.assertion_result) for e in edges]
    assert all(edge.target_refs for edge in edges)
    if expected is not None:
        assert all(len(edge.source_refs) == 2 for edge in edges)
    assert [e["assertion_result"] for e in machine["edge_fidelity"] if e["edge_id"] == "X17"] == [expected]


@pytest.mark.parametrize("edge_id", ["X18", "X21", "X27"])
@pytest.mark.parametrize("mutation", ["normal", "negative", "missing"])
def test_iia_native_postwrite_release_owner_edges_reach_formal_report(tmp_path, monkeypatch, edge_id, mutation):
    import test_post_write_release_publication as fixtures
    from core.diagnostics import DiagnosticConfig, DiagnosticSession, capture_event
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition, _observe_postwrite
    from application.terminal_outcome_projector import project_terminal
    from schemas.status import ExecutionStatus
    source = tmp_path / "frozen-source"; source.mkdir()
    fx = fixtures._release_fixture(source / "owner")
    composition = object.__new__(TargetPostWriteComposition)
    observer = DiagnosticSession(DiagnosticConfig(source / "capture", source, "native-postwrite", flush_records=1))
    emit = observer.emit
    def fixed_boundary(payload):
        if edge_id == "X18" and payload.get("event_type") == "S-CONTROL-RESULT":
            if mutation == "missing":
                return
            if mutation == "negative":
                payload = {**payload, "gate_ref":{**payload["gate_ref"], "decision_hash":"0" * 64}}
        if edge_id == "X21" and payload.get("event_type") == "S-PUBLISH-RESULT" and mutation == "negative":
            payload = {**payload, "run_completion_assessment_ref":{**payload["run_completion_assessment_ref"], "assessment_sha256":"0" * 64}}
        return emit(payload)
    monkeypatch.setattr(observer, "emit", fixed_boundary)
    @_observe_postwrite("S-CONTROL")
    def approved_control_boundary(self, **kwargs):
        # The boundary returns the actual committed Control owner record. No
        # expected/normalized evaluation endpoint is constructed by the test.
        return fx["final_control"]
    with observer:
        approved_control_boundary(composition, run_id=fx["request"].run_id,
            validation_run_id=fx["final_control"].validation_run_id,
            gate_ref=fx["final_control"].delivery_gate_decision_ref)
        publication = composition._observe_validation_producer("publication", fx["service"].publish,
            request=fx["request"], run_completion_assessment_ref=fx["assessment_ref"],
            run_lifecycle_revision=fx["terminal"].lifecycle_revision,
            active_document_binding_supplier=lambda:fx["post_write"]["active_projection"])
        terminal = project_terminal(task_id=fx["request"].task_id, run_id=fx["request"].run_id,
            execution_status=ExecutionStatus.SUCCESS, run_status=fx["terminal"].status,
            phase="published", control=fx["final_control"], publication=publication)
        payload_path = fx["publication_store"]._path_for_ref(publication.manifest.release_payload_artifact.artifact_ref)
        import base64
        release_payload = json.loads(base64.b64decode(json.loads(payload_path.read_bytes())["content_base64"]))
        public = {"task_id":fx["request"].task_id,"run_id":fx["request"].run_id,"status":"success",
            "target_release_publication":publication.model_dump(mode="json"),
            "terminal_outcome":terminal.model_dump(mode="json"),"deliverable_final_result":{"draft":release_payload}}
        if edge_id == "X27" and mutation == "negative":
            public["terminal_outcome"]["quality_state"] = "UNRESOLVED"
        if not (edge_id == "X27" and mutation == "missing"):
            capture_event("S-PUBLIC", public, unit_id="E11", operation_id=fx["request"].run_id)
    extras = [("postwrite","postwrite",fx["post_write"]["env"]["database"].path,"sqlite"),
        ("lifecycle","lifecycle",source/"owner"/"run-lifecycle.jsonl","jsonl"),
        ("assessment","run_completion_assessments",source/"owner"/"run-completion-assessments.jsonl","jsonl"),
        ("payload","release_payload",payload_path,"json")]
    if edge_id == "X21" and mutation == "missing":
        extras = [item for item in extras if item[0] != "assessment"]
    snapshot = _safe_captured_snapshot(source, observer, extras,
        excluded_tables=("post_write_control_decisions",) if edge_id == "X18" and mutation == "missing" else ())
    originals = {path:path.read_bytes() for path in source.rglob("*") if path.is_file()}
    report, machine = _formal_report(tmp_path, snapshot, originals)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == edge_id]
    expected = {"normal":True,"negative":False,"missing":None}[mutation]
    assert edges and all(edge.assertion_result is expected for edge in edges), [(e.reason,e.assertion_result) for e in edges]
    if expected is not None:
        assert all(edge.source_refs and edge.target_refs for edge in edges)
    assert [e["assertion_result"] for e in machine["edge_fidelity"] if e["edge_id"] == edge_id] == [expected]


@pytest.mark.parametrize("mutation", ["normal", "negative", "missing"])
def test_iia_native_fact_plan_research_original_lineage_reaches_formal_report(tmp_path, mutation):
    from test_multi_agent_v3_research_runtime import _setup, _round_record
    from apps.enterprise_document.schemas.agent_rag_v1.facts import FactSourceBindingSchema
    source = tmp_path / "frozen-source"; source.mkdir()
    # This is the same typed, fixed authoritative source boundary supplied to
    # the existing FactSnapshotRuntime fixture, recorded independently of the
    # resulting Fact/Plan/Research owner bytes. It declares no semantic Gold.
    original_input = FactSourceBindingSchema(source_type="project_input", source_ref="project://research", source_version_ref="project-version://1")
    input_path = source / "project-source.json"
    input_path.write_text(original_input.model_dump_json(), encoding="utf-8")
    env = _setup(source / "owner")
    candidate = _round_record(env)
    env["research"].commit_round(round_candidate=candidate, operation_ref=env["research"].round_operation_ref(candidate.round_ref),
        source_plan_binding=env["plan_binding"], resulting_package_binding=env["package_binding"], commit_guard_context=env["lineage"],
        expected_progress_revision=0, expected_committed_round_count=0, expected_last_committed_round_ref=None)
    fact_path = env["artifact"].artifact_store._path_for_ref(env["fact_binding"].version_ref.content_ref)
    plan_path = env["artifact"].artifact_store._path_for_ref(env["plan_version"].content_ref)
    round_path = env["research"].ledger_path
    if mutation == "negative":
        rows = [json.loads(line) for line in round_path.read_bytes().splitlines()]
        row = next(row for row in rows if row.get("event_type") == "round_committed")
        row["record"]["source_plan_version_ref"]["content_hash"] = "sha256:" + "b" * 64
        round_path = source / "fault-research.jsonl"
        round_path.write_bytes(b"".join(json.dumps(row).encode()+b"\n" for row in rows))
    files = [("artifact_runtime",env["artifact"].ledger_path,"jsonl"), ("fact_snapshot",fact_path,"json"),
        ("section_plan",plan_path,"json"), ("research_runtime",round_path,"jsonl")]
    if mutation != "missing":
        files.append(("project_input_sources",input_path,"json"))
    specs = [{"name":family,"family":family,"path":str(path),"format":format_,"sha256":sha256(path.read_bytes()).hexdigest()}
        for family,path,format_ in files]
    snapshot = load_source_snapshot({"schema_version":"full_chain_source_manifest_v1","snapshot_id":"native-X30","sources":specs},[source])
    assert not snapshot.limitations
    originals = {path:path.read_bytes() for path in source.rglob("*") if path.is_file()}
    report, machine = _formal_report(tmp_path,snapshot,originals)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == "X30"]
    expected = {"normal":True,"negative":False,"missing":None}[mutation]
    assert len(edges)==1 and edges[0].assertion_result is expected, edges
    if expected is not None:
        assert len(edges[0].source_refs)==3 and len(edges[0].target_refs)==3
    assert next(e for e in machine["edge_fidelity"] if e["edge_id"]=="X30")["assertion_result"] is expected


@pytest.mark.parametrize("mutation", ["normal", "negative", "missing"])
def test_iia_actual_context_runtime_and_physical_gateway_reach_formal_report(tmp_path, monkeypatch, mutation):
    import test_multi_agent_v3_context_runtime as fixtures
    from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
    from core.diagnostics import DiagnosticConfig, DiagnosticSession
    from schemas.context import ContextSourceFamily
    from test_full_chain_eval_capture import FixedClient, gateway, request
    source = tmp_path / "frozen-source"; source.mkdir()
    runtime = ApplicationContextRuntime()
    state = {"candidates":(fixtures._candidate(ContextSourceFamily.FACT_SNAPSHOT,candidate_id="native-context-fact",ref="fact:original",
        exact_payload={"capacity":"未确认"}),)}
    fixtures._register(runtime,ContextSourceFamily.FACT_SNAPSHOT,state)
    observer = DiagnosticSession(DiagnosticConfig(source/"capture",source,"native-context",flush_records=1))
    original_capture = observer.capture_model
    def missing_boundary(kind, *args, **kwargs):
        if mutation == "missing" and kind == "MODEL_ATTEMPT_REQUEST":
            return
        return original_capture(kind,*args,**kwargs)
    monkeypatch.setattr(observer,"capture_model",missing_boundary)
    client = FixedClient()
    with observer:
        package = fixtures._materialize(runtime,policy=fixtures._policy(fixtures._rule(ContextSourceFamily.FACT_SNAPSHOT)))
        material = package.model_input.final_input_material.inline_final_input_payload
        messages = deepcopy(material["messages"])
        if mutation == "negative":
            messages[-1]["content"] += " changed physical request"
        actual = request().model_copy(update={"model_name":client.model_name,"run_id":package.package_ref.run_id,
            "model_call_id":package.package_ref.invocation_id,"messages":messages,
            "extra":{"llm_context_summary":{"managed":True,"schema_version":"llm_context_summary_v1",
                "package_id":package.package_ref.context_package_id,"context_sha256":package.context_semantic_fingerprint,
                "final_model_input_sha256":package.model_input.final_model_input_sha256,"context_authority":"TARGET_CONTEXT"}}},deep=True)
        gateway(client).generate(actual)
    assert len(client.calls) == 1
    snapshot = _safe_captured_snapshot(source,observer)
    originals = {path:path.read_bytes() for path in source.rglob("*") if path.is_file()}
    report,machine = _formal_report(tmp_path,snapshot,originals)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id=="X09"]
    expected = {"normal":True,"negative":False,"missing":None}[mutation]
    assert len(edges)==1 and edges[0].assertion_result is expected, edges
    assert edges[0].source_refs
    if expected is not None:
        assert edges[0].target_refs
    assert next(edge for edge in machine["edge_fidelity"] if edge["edge_id"]=="X09")["assertion_result"] is expected


@pytest.mark.parametrize('role', ['section_generation', 'general', 'missing'])
def test_iia_actual_physical_request_role_controls_writer_ownership(tmp_path, monkeypatch, role):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession
    from test_full_chain_eval_capture import FixedClient, gateway, request

    source = tmp_path / 'frozen-source'
    source.mkdir()
    client = FixedClient()
    observer = DiagnosticSession(DiagnosticConfig(source / 'capture', source, 'iia-role', flush_records=1))
    actual = request().model_copy(update={'model_role':role if role != 'missing' else 'section_generation',
        'model_name':client.model_name}, deep=True)
    if role == 'missing':
        capture = observer.capture_model
        def omitted_role_boundary(kind, captured_request, *args, **kwargs):
            if kind == 'MODEL_ATTEMPT_REQUEST':
                captured_request = captured_request.model_copy(update={'model_role':None}, deep=True)
            return capture(kind, captured_request, *args, **kwargs)
        monkeypatch.setattr(observer, 'capture_model', omitted_role_boundary)
    with observer:
        result = gateway(client).generate(actual)
    observer.close()
    assert result.success and len(client.calls) == 1
    raw = observer.path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    files = sorted({row['payload_ref']['relative_path'] for row in rows if row.get('payload_ref')})
    manifest = {'schema_version':'full_chain_source_manifest_v1', 'snapshot_id':'iia-role', 'sources':[
        {'name':'diagnostics', 'family':'diagnostics', 'format':'diagnostic_jsonl', 'path':str(observer.path),
         'sha256':sha256(raw).hexdigest(), 'representation_files':files}]}
    snapshot = load_source_snapshot(manifest, [source])
    assert not snapshot.limitations
    report, persisted = _formal_report(tmp_path, snapshot, {observer.path:raw})
    physical_writers = [stage for stage in report.stage_quality if stage.unit_id == 'D05'
        and stage.executed is True and stage.payload.get('representations')]
    assert len(physical_writers) == (1 if role == 'section_generation' else 0)
    assert all(stage.evidence_refs for stage in physical_writers)
    assert sum(row.get('event_type') == 'MODEL_ATTEMPT_REQUEST' for row in snapshot.records('diagnostics')) == 1
    assert persisted['diagnostic_readiness'] == 'NOT_ESTABLISHED'


def test_iia_actual_physical_writer_fallback_preserves_two_attempts(tmp_path):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession
    from test_full_chain_eval_capture import FixedClient, gateway, request
    source = tmp_path / 'frozen-source'; source.mkdir()
    first, second = FixedClient(unavailable=True), FixedClient('fixed-fallback')
    observer = DiagnosticSession(DiagnosticConfig(source / 'capture', source, 'iia-writer-fallback', flush_records=1))
    with observer:
        response = gateway(first,second).generate(request())
    assert response.success and len(first.calls) == len(second.calls) == 1
    snapshot = _safe_captured_snapshot(source,observer)
    originals = {path:path.read_bytes() for path in source.rglob('*') if path.is_file()}
    report, machine = _formal_report(tmp_path,snapshot,originals)
    writers = [stage for stage in report.stage_quality if stage.unit_id == 'D05'
        and stage.executed is True and stage.payload.get('representations')]
    assert len(writers) == 2 and len({stage.operation_id for stage in writers}) == 1
    assert len({stage.attempt for stage in writers}) == 2
    assert all(stage.evidence_refs for stage in writers)
    assert sorted(stage.payload['representations']['representations']['MODEL_CLIENT_RAW_RESPONSE']['material']['success']
        for stage in writers) == [False,True]
    persisted_writers = [stage for stage in machine['stage_quality'] if stage['unit_id']=='D05' and stage['executed'] is True]
    assert len(persisted_writers) == 2 and len({stage['attempt'] for stage in persisted_writers}) == 2


def test_iia_native_error_only_ingress_cannot_use_synthetic_subject_compatibility(tmp_path):
    from core.diagnostics import DiagnosticConfig, DiagnosticSession, capture_event
    from eval.full_chain.domains.planning_prewrite import capture_planning_postwrite_snapshot, extract_planning_prewrite
    row = {'event_type':'P-INGRESS', 'payload':{'subject_id':'ingress:original', 'phase':'error', 'error_type':'ScopeError'}}
    synthetic = extract_planning_prewrite(capture_planning_postwrite_snapshot([row]))
    old_edge = next(edge for edge in synthetic.edges if edge.edge_id=='X25')
    assert old_edge.assertion_result is True and old_edge.availability=='SYNTHETIC_FIXTURE'
    assert old_edge.reason=='SYNTHETIC_CAPTURE_EVALUATION_SUBJECT_BINDING'
    source = tmp_path / 'frozen-source'; source.mkdir()
    observer = DiagnosticSession(DiagnosticConfig(source/'capture',source,'native-error-only',flush_records=1))
    with observer:
        capture_event(row['event_type'],row['payload'])
    snapshot = _safe_captured_snapshot(source,observer)
    originals = {path:path.read_bytes() for path in source.rglob('*') if path.is_file()}
    report, machine = _formal_report(tmp_path,snapshot,originals)
    edge = next(edge for edge in report.edge_fidelity if edge.edge_id=='X25')
    assert edge.assertion_result is None and edge.measurement_state=='UNMEASURED'
    assert edge.reason=='NATIVE_PHASE_OPERATION_JOIN_NOT_PROVEN' and edge.target_refs and not edge.source_refs
    assert next(edge for edge in machine['edge_fidelity'] if edge['edge_id']=='X25')['assertion_result'] is None


@pytest.mark.parametrize('edge_id', ['X12', 'X13', 'X14'])
@pytest.mark.parametrize('mutation', ['normal', 'negative', 'missing'])
def test_iia_actual_claim_owner_structural_edges_reach_formal_report(tmp_path, monkeypatch, edge_id, mutation):
    import test_multi_agent_v3_claim_verification_runtime as fixtures
    from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimVerificationRuntime
    from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import ClaimSupportRuntime

    env, runtime, claim, binding, support_runtime, support, _ = fixtures._setup_verification_chain(tmp_path / 'actual-owner')
    candidate = fixtures._verifier_candidate(claim, binding, support)
    runtime.commit_verification(claim.claim_ref, binding.binding_facts_ref, support.support_ref, candidate,
        None, fixtures._verification_context(env, candidate.verification_occurrence_ref))
    claim_rows = [json.loads(line) for line in runtime.ledger_path.read_bytes().splitlines()]
    support_rows = [json.loads(line) for line in support_runtime.ledger_path.read_bytes().splitlines()]
    rows = {'claim_runtime':deepcopy(claim_rows), 'support_runtime':deepcopy(support_rows)}
    target_kind = {'X12':'binding_facts_committed', 'X13':'support_committed', 'X14':'verification_committed'}[edge_id]
    target_family = 'support_runtime' if edge_id == 'X13' else 'claim_runtime'
    target = next(row for row in rows[target_family] if row['event_type'] == target_kind)
    if mutation == 'negative':
        if edge_id == 'X12':
            target['record']['paragraph_version_ref']['content_hash'] = 'sha256:' + 'b' * 64
        elif edge_id == 'X13':
            target['record']['evidence_target_set_ref']['evidence_member_refs'].append('evidence:OUTSIDE_BINDING')
        else:
            target['verification_result']['claim_evidence_support_ref']['claim_evidence_support_id'] = 'support:WRONG_ORIGINAL_REF'
    elif mutation == 'missing':
        rows[target_family] = [row for row in rows[target_family] if row['event_type'] != target_kind]
    snapshot, originals = _frozen_rows(tmp_path, rows)
    def forbidden(*args, **kwargs):
        raise AssertionError('OBSERVE constructed a native business owner')
    monkeypatch.setattr(ClaimVerificationRuntime, '__init__', forbidden)
    monkeypatch.setattr(ClaimSupportRuntime, '__init__', forbidden)
    report, persisted = _formal_report(tmp_path, snapshot, originals)
    edges = [edge for edge in report.edge_fidelity if edge.edge_id == edge_id]
    expected = {'normal':True, 'negative':False, 'missing':None}[mutation]
    assert edges and any(edge.assertion_result is expected for edge in edges), edges
    if mutation == 'missing':
        assert all(edge.assertion_result is None for edge in edges)
    else:
        measured = [edge for edge in edges if edge.assertion_result is expected]
        assert all(edge.source_refs and edge.target_refs for edge in measured)
        assert any(edge.reason.startswith('NATIVE_') for edge in measured)
    assert any(edge['edge_id'] == edge_id and edge['assertion_result'] is expected for edge in persisted['edge_fidelity'])
    assert all(finding.evidence_level != 'REPRODUCED_MECHANISM' for finding in report.findings)
