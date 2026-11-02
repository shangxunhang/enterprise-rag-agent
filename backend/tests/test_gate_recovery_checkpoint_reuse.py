"""Committed Gate replay at the reopened SQLite checkpoint boundary.

Research/Plan/Fact/Package/lifecycle owners are real. Completed-source tests use
explicit dependency and Writer read-port doubles; they test transport, not Claim
acceptance. Incomplete tests stop at the resume boundary, before Writer work.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.runtime.official_checkpoint_saver import create_sqlite_checkpoint_resource
from apps.enterprise_document.agents.agent_rag_v1.research import ResearchAgent
from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import (
    SectionParagraphManifestRefSchema, SectionParagraphManifestSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterLogicalExecutionKey, WriterExecutionRefSchema, WriterGenerationInvocationRefSchema,
)
from apps.enterprise_document.services.agent_rag_v1.section_dependency_runtime import SectionDependencyRuntime
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import TargetWorkflowRuntime
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
from model_gateway.model_gateway import ModelGateway
from rag.services.rag_service import RAGService
from backend.tests.test_gate_recovery_research_authorization import gate
from backend.tests.test_multi_agent_v3_prewrite_gate_integration import _apply, _shared_state
from backend.tests.test_multi_agent_v3_research_runtime import _round_record
from backend.tests.test_rag_interface_v2_phase4c_compatibility_removal import (
    _recoverable_section_result, DocumentCitationRegistry,
)


def _committed(gate, *, physical_complete=False):
    gate["controller"].release_physical_dispatch(gate["claim"], reason="fixture-ready")
    graph = gate["graph"]
    state = _apply(graph, gate["state"], "_execute_pre_write_recovery")
    payload = graph.codec.restore_payload(state)
    assert payload.pending_recovery_dispatch["phase"] == "candidate_durable"
    assert payload.research_authorization_binding.authorization_kind == "GATE_RECOVERY_RESEARCH"
    if physical_complete:
        state = _apply(graph, state, "_complete_pre_write_recovery_dispatch")
        payload = graph.codec.restore_payload(state)
        assert payload.pending_recovery_dispatch is None
    return payload


def _workflow_round(gate, payload):
    env = gate["env"]
    runtime = env["research"]
    ref = TargetWorkflowRuntime.workflow_research_authorization_ref(
        lineage=env["lineage"], section_id=payload.section_id)
    record = _round_record(env, authorization_ref=ref)
    progress = runtime.resolve_progress(
        run_id=payload.run_id, section_id=payload.section_id,
        attempt_scope_ref=env["lineage"].attempt_scope_ref, attempt_id=env["lineage"].attempt_id)
    package = payload.active_evidence_package_binding
    content = runtime.validate_evidence_package_binding_current(package)
    record = record.model_copy(update={"round_ordinal": progress.committed_round_count + 1,
        "prior_committed_round_ref": progress.last_committed_round_ref,
        "final_evidence_bundle_binding": content.evidence_bundle_binding,
        "package_progress": record.package_progress.model_copy(update={
            "resulting_evidence_package_version_ref": package.version_ref})})
    committed = runtime.commit_round(
        round_candidate=record, operation_ref=runtime.round_operation_ref(record.round_ref),
        source_plan_binding=env["plan_binding"], resulting_package_binding=package,
        commit_guard_context=env["lineage"], expected_progress_revision=progress.progress_revision,
        expected_committed_round_count=progress.committed_round_count,
        expected_last_committed_round_ref=progress.last_committed_round_ref)
    return payload.model_copy(update={"research_authorization_binding": record.authorization_binding,
                                      "current_research_round_binding": committed.binding})


def _completed(gate, payload, monkeypatch):
    """Strict read-port fixtures; no claim of full production acceptance here."""
    lineage = payload.active_execution_lineage
    route = gate["payload"].current_pre_write_route_decision
    key = WriterLogicalExecutionKey(writer_subject_ref=route.writer_subject_ref,
        write_instance_id=lineage.attempt_id, route_decision_ref=gate["graph"]._route_ref(route))
    writer = WriterExecutionRefSchema(logical_execution_key=key, result_sha256="a" * 64)
    invocation = WriterGenerationInvocationRefSchema(logical_execution_key=key, invocation_sha256="b" * 64)
    manifest = SectionParagraphManifestSchema(manifest_ref=SectionParagraphManifestRefSchema(
        run_id=payload.run_id, section_id=payload.section_id, attempt_scope_ref=lineage.attempt_scope_ref,
        attempt_id=lineage.attempt_id, write_instance_id=lineage.attempt_id), manifest_revision=1,
        generation_membership_state="SEALED")
    source = SectionDependencyRuntime._create(source_ref="section-source://unit-read-port",
        plan_binding=payload.canonical_plan_binding, producer_attempt_id=lineage.attempt_id,
        producer_execution_status="success", input_snapshot_version=lineage.input_snapshot_version,
        paragraph_manifest=manifest, writer_execution_ref=writer, writer_invocation_ref=invocation,
        coverage_proof_refs=(), verification_refs=(), workflow_status="VERIFYING")
    proof = {"plan_binding": payload.canonical_plan_binding.model_dump(mode="json"),
             "dependency_fingerprint": "current-unit-dependencies"}

    def validate_source(actual):
        if actual != source:
            raise ValueError("unit source owner mismatch")
        gate["env"]["lifecycle"].resolve_read_lineage(payload.run_id, lineage.attempt_scope_ref,
            expected_attempt_id=lineage.attempt_id)
        return actual

    dependencies = SimpleNamespace(validate_source=Mock(side_effect=validate_source),
        store=SimpleNamespace(get_json=Mock(return_value=proof)),
        require_dependencies=Mock(return_value=SimpleNamespace(fingerprint=proof["dependency_fingerprint"])))
    coordinator = gate["graph"].coordinator
    monkeypatch.setattr(coordinator, "section_dependencies", dependencies, raising=False)
    def resolve_writer(actual):
        assert actual == writer
        return SimpleNamespace(logical_execution_key=key)
    def resolve_invocation(actual):
        assert actual == invocation
        return SimpleNamespace(writer_subject_ref=key.writer_subject_ref,
                               write_instance_id=key.write_instance_id, route_decision_ref=key.route_decision_ref)
    monkeypatch.setattr(coordinator, "resolve_pre_write_writer_execution", resolve_writer)
    monkeypatch.setattr(coordinator, "resolve_pre_write_writer_invocation", resolve_invocation)
    result = _recoverable_section_result(payload).model_copy(update={
        "canonical_section_source_binding": source, "active_execution_lineage": lineage})
    return payload.model_copy(update={"completed": True, "route": "end", "result": result,
        "writer_execution_ref": writer, "writer_generation_invocation_ref": invocation,
        "section_paragraph_manifest": manifest}), dependencies


def _replay(gate, payload, tmp_path, monkeypatch):
    graph = gate["graph"]
    config = graph.checkpoint_config(workflow_namespace="gate-recovery-reuse", run_id=payload.run_id,
                                     section_id=payload.section_id)
    database = tmp_path / "reopened-checkpoint.sqlite3"
    resource = create_sqlite_checkpoint_resource(database)
    values = graph.codec.build_initial_state(payload)
    try:
        resource.saver.put(config, {"v": 4, "ts": "2026-09-28T10:00:00+08:00",
            "id": "committed-gate-checkpoint", "channel_values": values,
            "channel_versions": {k: "1" for k in values}, "versions_seen": {},
            "updated_channels": list(values)}, {"source": "input", "step": 0, "parents": {}}, {})
    finally:
        resource.close()
    resource = create_sqlite_checkpoint_resource(database)
    reopened = SectionExecutionGraph(coordinator=graph.coordinator, checkpointer=resource.saver)
    resume = Mock(return_value="resume-boundary")
    monkeypatch.setattr(reopened, "resume", resume)
    spies = [Mock(side_effect=AssertionError("committed replay must not dispatch")) for _ in range(5)]
    monkeypatch.setattr(ResearchAgent, "run", spies[0])
    monkeypatch.setattr(ResearchAgent, "run_projected", spies[1])
    monkeypatch.setattr(RAGService, "retrieve", spies[2])
    monkeypatch.setattr(ModelGateway, "generate", spies[3])
    workflow_incomplete = (not payload.completed and
        payload.research_authorization_binding.authorization_kind == "WORKFLOW_RESEARCH")
    if workflow_incomplete:
        spies[4] = Mock(wraps=reopened.research_runtime.authorize_round_ref)
    monkeypatch.setattr(reopened.research_runtime, "authorize_round_ref", spies[4])
    before_calls = gate["adapter"].calls
    ledger = reopened.research_runtime.ledger_path.read_bytes()
    try:
        result = reopened.recover_or_reuse(workflow_namespace="gate-recovery-reuse",
            task_id=payload.task_id, run_id=payload.run_id, document_id=payload.document_id,
            section_id=payload.section_id, project_input=payload.project_input,
            section_plan=payload.section_plan, shared_state=_shared_state(),
            citation_registry=DocumentCitationRegistry(),
            expected_canonical_plan_binding=payload.canonical_plan_binding)
        if payload.completed:
            assert result == payload.result
            resume.assert_not_called()
        else:
            assert result == "resume-boundary"
            resume.assert_called_once()
        return result
    finally:
        resource.close()
        for spy in spies[:4]:
            spy.assert_not_called()
        assert spies[4].call_count == (1 if workflow_incomplete else 0)
        assert gate["adapter"].calls == before_calls
        assert reopened.research_runtime.ledger_path.read_bytes() == ledger


@pytest.mark.parametrize("physical_complete", [False, True])
def test_committed_gate_checkpoint_reopens_without_reauthorization(gate, tmp_path, monkeypatch, physical_complete):
    payload = _committed(gate, physical_complete=physical_complete)
    _replay(gate, payload, tmp_path, monkeypatch)


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("kind", ["gate", "workflow"])
def test_completed_current_research_checkpoint_reuses_without_dispatch(gate, tmp_path, monkeypatch, kind, terminal):
    payload = _committed(gate, physical_complete=True)
    if kind == "workflow":
        payload = _workflow_round(gate, payload)
    payload, dependencies = _completed(gate, payload, monkeypatch)
    if terminal:
        from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus
        lifecycle = gate["env"]["lifecycle"]
        attempt = lifecycle.load_attempt(payload.active_execution_lineage.attempt_id)
        lifecycle.transition_attempt(attempt.identity.attempt_id, attempt.lifecycle_revision, AttemptLifecycleStatus.COMPLETED)
    _replay(gate, payload, tmp_path, monkeypatch)
    dependencies.validate_source.assert_called_once_with(payload.result.canonical_section_source_binding)
    assert dependencies.store.get_json.call_count == (1 if kind == "workflow" else 0)
    assert dependencies.require_dependencies.call_count == (1 if kind == "workflow" else 0)


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("defect", ["authorization", "historical", "missing_round", "missing_package", "source_owner"])
def test_committed_gate_checkpoint_fail_closed(gate, tmp_path, monkeypatch, completed, defect):
    payload = _committed(gate, physical_complete=True)
    if completed:
        payload, dependencies = _completed(gate, payload, monkeypatch)
    expected = "."
    if defect == "authorization":
        payload = payload.model_copy(update={"research_authorization_binding": payload.research_authorization_binding.model_copy(
            update={"gate_recovery_state_revision": 999})})
        expected = "committed Gate Research source mismatch"
    elif defect == "historical":
        _workflow_round(gate, payload)
        expected = "stale|current"
    elif defect == "missing_round":
        payload = payload.model_copy(update={"current_research_round_binding": None})
        expected = "requires committed Round"
    elif defect == "missing_package":
        payload = payload.model_copy(update={"active_evidence_package_binding": None})
        expected = "Package mismatch"
    elif completed:
        dependencies.validate_source.side_effect = ValueError("source owner rejected")
        expected = "source owner rejected"
    else:
        monkeypatch.setattr(gate["env"]["research"], "resolve_research_requirements",
                            Mock(side_effect=ValueError("fresh Plan source owner rejected")))
        expected = "fresh Plan source owner rejected"
    with pytest.raises(ValueError, match=expected):
        _replay(gate, payload, tmp_path, monkeypatch)


@pytest.mark.parametrize("defect", ["authorization", "dependency"])
def test_completed_workflow_keeps_exact_owner_validation(gate, tmp_path, monkeypatch, defect):
    payload = _workflow_round(gate, _committed(gate, physical_complete=True))
    payload, dependencies = _completed(gate, payload, monkeypatch)
    if defect == "authorization":
        payload = payload.model_copy(update={"research_authorization_binding": payload.research_authorization_binding.model_copy(
            update={"authorization_ref": "workflow-start-research://wrong"})})
        expected = "Research occurrence mismatch"
    else:
        dependencies.require_dependencies.return_value = SimpleNamespace(fingerprint="stale")
        expected = "dependency observation is stale"
    with pytest.raises(ValueError, match=expected):
        _replay(gate, payload, tmp_path, monkeypatch)


def test_new_gate_dispatch_still_requires_owner_context(gate):
    runtime = gate["env"]["research"]
    with pytest.raises(ValueError, match="exact owner context"):
        runtime.authorize_round_ref(run_id=gate["payload"].run_id, section_id=gate["payload"].section_id,
            source_plan_binding=gate["env"]["plan_binding"], active_attempt=gate["env"]["lineage"],
            authorization_binding=gate["binding"])
    assert gate["adapter"].calls == 0


@pytest.mark.parametrize("invalid", [False, True])
def test_incomplete_workflow_keeps_dispatch_owner_validation(gate, tmp_path, monkeypatch, invalid):
    payload = _workflow_round(gate, _committed(gate, physical_complete=True))
    if invalid:
        from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
        payload = payload.model_copy(update={"research_authorization_binding": payload.research_authorization_binding.model_copy(
            update={"authorization_ref": "workflow-start-research://wrong"})})
        with pytest.raises(WorkflowCutoverIntegrityError):
            _replay(gate, payload, tmp_path, monkeypatch)
    else:
        _replay(gate, payload, tmp_path, monkeypatch)


def _failing_production_agent(gate, monkeypatch):
    from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
        MainlineResearchAgentFailure, build_round5_section_agent_adapter,
    )
    from backend.tests.test_multi_agent_v3_prewrite_gate_integration import _recovery_coordinator
    from schemas.common import ErrorSchema

    error = ErrorSchema(error_code="TEST_RESEARCH_FAILURE", error_type="TestResearchFailure",
                        message="controlled ResearchAgent technical failure", recoverable=True, retryable=True)
    model = Mock()
    model.generate.side_effect = AssertionError("failure injection must not call model")
    service = Mock()
    service.retrieve.side_effect = AssertionError("failure injection precedes RAG")
    adapter = build_round5_section_agent_adapter(evidence_service=service, model_gateway=model)
    agent = Mock(side_effect=MainlineResearchAgentFailure(error=error))
    monkeypatch.setattr(adapter.research_agent, "run_projected", agent)
    coordinator, _ = _recovery_coordinator(gate["store"].database_path.parent, gate["env"], adapter, id_start=100)
    gate["graph"] = SectionExecutionGraph(coordinator=coordinator)
    gate["controller"].release_physical_dispatch(gate["claim"], reason="fixture-ready")
    gate["controller"] = coordinator.require_pre_write_services().recovery_controller
    runtime = gate["env"]["research"]
    commit = Mock(wraps=runtime.commit_round)
    authorize = Mock(wraps=runtime.authorize_round_ref)
    monkeypatch.setattr(runtime, "commit_round", commit)
    monkeypatch.setattr(runtime, "authorize_round_ref", authorize)
    return agent, model, service, commit, authorize


def _progress(gate):
    payload = gate["payload"]
    lineage = payload.active_execution_lineage
    return gate["env"]["research"].resolve_progress(run_id=payload.run_id,
        section_id=payload.section_id, attempt_scope_ref=lineage.attempt_scope_ref,
        attempt_id=lineage.attempt_id)


@pytest.mark.parametrize("previous_authorization", [False, True])
def test_agent_technical_failure_checkpoint_reopen_same_occurrence_and_next_round(gate, tmp_path, monkeypatch, previous_authorization):
    if previous_authorization:
        initial = gate["env"]["research"].validate_round_binding_current(gate["payload"].current_research_round_binding)
        gate["payload"] = gate["payload"].model_copy(update={"research_authorization_binding": initial.authorization_binding})
        gate["state"] = gate["graph"].codec.build_initial_state(gate["payload"])
    agent, model, service, commit, authorize = _failing_production_agent(gate, monkeypatch)
    graph, runtime = gate["graph"], gate["env"]["research"]
    before = _progress(gate)
    release = Mock(wraps=gate["controller"].release_physical_dispatch)
    monkeypatch.setattr(gate["controller"], "release_physical_dispatch", release)
    state = _apply(graph, gate["state"], "_execute_pre_write_recovery")
    failed = graph.codec.restore_payload(state)
    assert failed.recovery_runtime_state.last_round_outcome.value == "technical_failure"
    assert failed.pending_recovery_dispatch is None
    assert release.call_args.kwargs["reason"] == "MainlineResearchAgentFailure"
    assert agent.call_count == commit.call_count == authorize.call_count == 1
    assert failed.research_authorization_binding == gate["binding"]
    record = runtime.validate_round_binding_current(failed.current_research_round_binding)
    assert record.execution_outcome.value == "TECHNICAL_FAILURE"
    assert record.authorization_binding == failed.research_authorization_binding
    assert record.package_progress.prior_evidence_package_version_ref == failed.active_evidence_package_binding.version_ref
    progress = _progress(gate)
    assert progress.committed_round_count == before.committed_round_count + 1
    assert progress.last_committed_round_ref == failed.current_research_round_binding.round_ref
    assert runtime.resolve_current_round_binding(run_id=record.round_ref.run_id,
        section_id=record.round_ref.section_id, attempt_scope_ref=record.round_ref.attempt_scope_ref,
        attempt_id=record.round_ref.attempt_id) == failed.current_research_round_binding
    with monkeypatch.context() as restart_patch:
        _replay(gate, failed, tmp_path, restart_patch)
    assert agent.call_count == commit.call_count == 1
    model.generate.assert_not_called()
    service.retrieve.assert_not_called()

    # Crash after canonical commit but before Graph publication: replay the old
    # reserved input. The owner resolves the same failed occurrence, no new commit.
    ledger = runtime.ledger_path.read_bytes()
    replay_state = _apply(graph, gate["state"], "_execute_pre_write_recovery")
    replayed = graph.codec.restore_payload(replay_state)
    assert replayed.research_authorization_binding == failed.research_authorization_binding
    assert replayed.current_research_round_binding == failed.current_research_round_binding
    assert replayed.recovery_runtime_state == failed.recovery_runtime_state
    assert _progress(gate) == progress
    assert runtime.ledger_path.read_bytes() == ledger
    assert agent.call_count == commit.call_count == 1
    with monkeypatch.context() as restart_patch:
        _replay(gate, replayed, tmp_path / "same-failure", restart_patch)
    assert agent.call_count == commit.call_count == 1
    model.generate.assert_not_called()
    service.retrieve.assert_not_called()

    # Existing policy permits the second round. Restore a successful deterministic
    # adapter for that *new* occurrence; no configuration or authority changes.
    graph = SectionExecutionGraph(coordinator=graph.coordinator)
    graph.section_agent_adapter = gate["adapter"]
    for node in ("_commit_pre_write_route", "_dispatch_pre_write_recovery"):
        state = _apply(graph, state, node)
    reserved = graph.codec.restore_payload(state)
    assert reserved.current_pre_write_route_decision.selected_route.value == "recovery"
    assert reserved.pending_recovery_dispatch["reservation"]["occurrence_id"] != gate["context"].reservation.occurrence_id
    state = _apply(graph, state, "_execute_pre_write_recovery")
    next_payload = graph.codec.restore_payload(state)
    assert next_payload.pending_recovery_dispatch["phase"] == "candidate_durable"
    next_record = runtime.validate_round_binding_current(next_payload.current_research_round_binding)
    assert next_record.execution_outcome.value == "COMPLETED"
    assert next_record.prior_committed_round_ref == record.round_ref
    assert next_record.authorization_binding.authorization_ref != record.authorization_binding.authorization_ref
    assert _progress(gate).committed_round_count == progress.committed_round_count + 1
    assert commit.call_count == 2
    assert gate["adapter"].calls == 1


@pytest.mark.parametrize("commit_fails", [False, True])
def test_gate_failure_projection_preserves_original_exception_and_failed_commit_publishes_nothing(gate, monkeypatch, commit_fails):
    from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import MainlineResearchAgentFailure
    _failing_production_agent(gate, monkeypatch)
    graph = gate["graph"]
    observed = []
    original = graph.section_agent_adapter.research
    def capture(**kwargs):
        try:
            return original(**kwargs)
        except MainlineResearchAgentFailure as exc:
            observed.append(exc)
            raise
    monkeypatch.setattr(graph.section_agent_adapter, "research", capture)
    claim = gate["controller"].claim_physical_dispatch(gate["context"].reservation, owner_id="failure-test")
    assert claim.acquired
    publications = []
    before = _progress(gate)
    if commit_fails:
        monkeypatch.setattr(gate["env"]["research"], "commit_round",
                            Mock(side_effect=RuntimeError("controlled commit failure")))
    with pytest.raises(RuntimeError if commit_fails else MainlineResearchAgentFailure) as caught:
        graph._execute_canonical_gate_recovery_research(gate["payload"], shared_state=_shared_state(),
            reservation=gate["context"].reservation, source_chain=gate["context"].source_chain,
            query="failure identity", publish_committed_failure=publications.append)
    if commit_fails:
        assert str(caught.value) == "controlled commit failure"
        assert publications == []
        assert _progress(gate) == before
    else:
        assert caught.value is observed[0]
        assert len(publications) == 1
        assert publications[0].authorization == gate["binding"]
        assert publications[0].round_binding.round_ref == _progress(gate).last_committed_round_ref
