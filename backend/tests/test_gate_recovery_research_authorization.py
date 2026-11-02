"""B+ authorization proofs through real owner stores and canonical ResearchRuntime."""

from dataclasses import replace
import sqlite3
from unittest.mock import Mock

import pytest

from apps.enterprise_document.agents.agent_rag_v1.research import ResearchAgent
from apps.enterprise_document.schemas.agent_rag_v1.research import ResearchRoundAuthorizationBindingSchema
from apps.enterprise_document.schemas.pre_write_control import PreWriteRoute
from apps.enterprise_document.services.scheme_writer.gate_recovery_research_authorization import (
    GateRecoveryResearchAuthorizationContext,
    ResearchAuthorizationDispatcher,
)
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import WorkflowCutoverIntegrityError
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
from model_gateway.model_gateway import ModelGateway
from rag.services.rag_service import RAGService
from backend.tests.test_multi_agent_v3_prewrite_gate_integration import (
    _apply, _canonical_payload, _CanonicalRecoveryAdapter,
    _commit_initial_research_round, _recovery_coordinator, _shared_state,
)
from backend.tests.test_multi_agent_v3_research_runtime import _setup, RUN_ID, SECTION_ID


@pytest.fixture
def gate(tmp_path):
    env = _setup(tmp_path / "research")
    initial = _commit_initial_research_round(env)
    adapter = _CanonicalRecoveryAdapter(env)
    coordinator, routes = _recovery_coordinator(tmp_path, env, adapter)
    graph = SectionExecutionGraph(coordinator=coordinator)
    state = graph.codec.build_initial_state(_canonical_payload(graph, env, initial.binding))
    for node in ("_evaluate_pre_write_admission", "_bind_pre_write_gate",
                 "_evaluate_pre_write_enforcement", "_commit_pre_write_route",
                 "_dispatch_pre_write_recovery"):
        state = _apply(graph, state, node)
    payload = graph.codec.restore_payload(state)
    reservation = graph._restore_reservation(dict(payload.pending_recovery_dispatch["reservation"]))
    services = coordinator.require_pre_write_services()
    controller = services.recovery_controller
    claim = controller.claim_physical_dispatch(reservation, owner_id="test-gate")
    assert claim.acquired
    context = GateRecoveryResearchAuthorizationContext(
        reservation=reservation, source_chain=graph._pre_write_chain(payload),
    )
    binding = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="GATE_RECOVERY_RESEARCH",
        authorization_ref=reservation.occurrence_id,
        route_decision_ref=graph._route_ref(payload.current_pre_write_route_decision),
        gate_recovery_dispatch_occurrence_ref=reservation.occurrence_id,
        gate_recovery_state_revision=reservation.state_after_reservation.recovery_state_revision,
    )
    return dict(env=env, adapter=adapter, graph=graph, state=state, payload=payload,
                routes=routes, controller=controller, store=controller._execution_store,
                claim=claim, context=context, binding=binding,
                dispatcher=env["research"].authorization_validator)


def _authorize(gate, *, binding=None, context=None):
    env = gate["env"]
    return env["research"].authorize_round_ref(
        run_id=RUN_ID, section_id=SECTION_ID,
        source_plan_binding=env["plan_binding"], active_attempt=env["lineage"],
        authorization_binding=binding or gate["binding"],
        authorization_context=context or gate["context"],
    )


def _execute(gate):
    return gate["graph"]._execute_canonical_gate_recovery_research(
        gate["payload"], shared_state=_shared_state(),
        reservation=gate["context"].reservation,
        source_chain=gate["context"].source_chain,
        query="canonical recovery query",
    )


def _replace_route(gate, **updates):
    route = gate["payload"].current_pre_write_route_decision.model_copy(
        update={"route_decision_id": "test-exact-alternate-route", **updates}, deep=True,
    )
    return gate["routes"].put_if_absent_or_same(route, authorization_snapshot_sha256="a" * 64)


def _physical_update(gate, sql, values=()):
    with sqlite3.connect(gate["store"].database_path) as db:
        db.execute(sql, values)


NEGATIVES = [
    "unknown_kind", "missing_route_ref", "missing_occurrence_ref", "unequal_refs",
    "missing_revision", "unresolved_route", "normal_route", "fallback_route",
    "cross_run", "cross_section", "write_instance", "attempt_scope",
    "revision", "old_occurrence_new_route", "missing_context", "wrong_context",
    "wrong_context_reservation", "target", "state_before", "state_after", "fingerprint",
    "reservation_occurrence", "binding_occurrence", "physical_missing",
    "physical_fingerprint", "tampered_row", "workflow_with_context",
    "route_binding", "route_decision", "route_document",
]


@pytest.mark.parametrize("case", NEGATIVES)
def test_authorization_negative_precedes_all_research_side_effects(gate, monkeypatch, case):
    # Sentinels are outside the exception class caught below: reaching any is a test failure.
    model = Mock(side_effect=AssertionError("model dispatch forbidden"))
    rag = Mock(side_effect=AssertionError("physical RAG forbidden"))
    agent = Mock(side_effect=AssertionError("ResearchAgent invocation forbidden"))
    monkeypatch.setattr(ModelGateway, "generate", model)
    monkeypatch.setattr(RAGService, "retrieve", rag)
    monkeypatch.setattr(ResearchAgent, "run", agent)
    monkeypatch.setattr(ResearchAgent, "run_projected", agent)
    before = gate["env"]["research"].ledger_path.read_bytes()
    original = gate["dispatcher"]

    def corrupt(binding, ref, context=None):
        changes = {}
        if case == "unknown_kind": changes["authorization_kind"] = "UNKNOWN"
        elif case == "missing_route_ref": changes["route_decision_ref"] = None
        elif case == "missing_occurrence_ref": changes["gate_recovery_dispatch_occurrence_ref"] = None
        elif case == "missing_revision": changes["gate_recovery_state_revision"] = None
        elif case == "unequal_refs": changes["authorization_ref"] = "other"
        elif case == "unresolved_route":
            changes["route_decision_ref"] = binding.route_decision_ref.model_copy(
                update={"route_decision_id": "absent"})
        elif case in {"normal_route", "fallback_route"}:
            changes["route_decision_ref"] = _replace_route(
                gate, selected_route=PreWriteRoute.NORMAL if case == "normal_route" else PreWriteRoute.FALLBACK)
        elif case in {"cross_run", "cross_section", "route_document"}:
            route = gate["payload"].current_pre_write_route_decision
            field = {"cross_run": "run_id", "cross_section": "section_id", "route_document": "document_id"}[case]
            changes["route_decision_ref"] = _replace_route(
                gate, writer_subject_ref=route.writer_subject_ref.model_copy(update={field: "other"}))
        elif case == "write_instance":
            changes["route_decision_ref"] = _replace_route(gate, write_instance_id="other")
        elif case in {"route_binding", "route_decision"}:
            route = gate["payload"].current_pre_write_route_decision
            field = "binding_ref" if case == "route_binding" else "decision_ref"
            id_field = "binding_id" if case == "route_binding" else "decision_id"
            changes["route_decision_ref"] = _replace_route(
                gate, **{field: getattr(route, field).model_copy(update={id_field: "other"})})
        elif case == "attempt_scope": ref = ref.model_copy(update={"attempt_scope_ref": "section:other"})
        elif case == "revision": changes["gate_recovery_state_revision"] = binding.gate_recovery_state_revision + 1
        elif case == "old_occurrence_new_route":
            graph = gate["graph"]
            failure = gate["controller"].complete_technical_failure(
                current_chain=context.source_chain,
                applicability=graph._pre_write_applicability(gate["payload"]),
                reservation=context.reservation,
            )
            newer_payload = gate["payload"].model_copy(update={
                "recovery_runtime_state": failure.runtime_state,
                "current_recovery_control_facts": failure.recovery_control_facts,
                "pending_recovery_dispatch": None,
                "current_pre_write_route_decision": None,
            })
            newer_state = graph.codec.build_initial_state(newer_payload)
            newer_state = _apply(graph, newer_state, "_commit_pre_write_route")
            newer_route = graph.codec.restore_payload(newer_state).current_pre_write_route_decision
            assert newer_route.selected_route is PreWriteRoute.RECOVERY
            newer_revision = newer_route.recovery_control_ref.recovery_state_revision
            assert newer_revision > context.reservation.state_before.recovery_state_revision
            changes["route_decision_ref"] = graph._route_ref(newer_route)
            changes["gate_recovery_state_revision"] = newer_revision + 1
            assert gate["store"].resolve_recovery_dispatch(context.reservation.occurrence_id)
        elif case == "missing_context": context = None
        elif case == "wrong_context": context = object()
        elif case == "wrong_context_reservation": context = replace(context, reservation=object())
        elif case in {"target", "state_before", "state_after", "fingerprint", "reservation_occurrence"}:
            r = context.reservation
            if case == "target":
                r = replace(r, target=r.target.model_copy(update={"write_instance_id": "other"}))
            elif case == "state_before":
                r = replace(r, state_before=r.state_before.model_copy(update={"recovery_state_revision": 99}))
            elif case == "state_after":
                r = replace(r, state_after_reservation=r.state_after_reservation.model_copy(update={"recovery_rounds_used": 99}))
            elif case == "fingerprint": r = replace(r, semantic_fingerprint="f" * 64)
            else: r = replace(r, occurrence_id="other")
            context = replace(context, reservation=r)
        elif case == "binding_occurrence":
            changes.update(authorization_ref="other", gate_recovery_dispatch_occurrence_ref="other")
        elif case == "physical_missing": _physical_update(gate, "DELETE FROM recovery_dispatches")
        elif case == "physical_fingerprint":
            _physical_update(gate, "UPDATE recovery_dispatches SET semantic_fingerprint = ?", ("f" * 64,))
        elif case == "tampered_row": _physical_update(gate, "UPDATE recovery_dispatches SET claim_token = NULL")
        elif case == "workflow_with_context":
            binding = ResearchRoundAuthorizationBindingSchema(
                authorization_kind="WORKFLOW_RESEARCH", authorization_ref="workflow-test")
        return original(binding.model_copy(update=changes), ref, context)

    gate["env"]["research"].authorization_validator = corrupt
    with pytest.raises((ValueError, TypeError)) as rejected:
        _execute(gate)
    if case == "old_occurrence_new_route":
        assert "reservation/route revision mismatch" in str(rejected.value)
    assert gate["adapter"].calls == 0
    assert model.call_count == rag.call_count == agent.call_count == 0
    assert gate["env"]["research"].ledger_path.read_bytes() == before


@pytest.mark.parametrize("physical_state", ["claimed", "expired", "released", "completed"])
def test_valid_gate_owner_proof_is_independent_of_physical_lease_state(gate, physical_state):
    if physical_state == "expired":
        _physical_update(gate, "UPDATE recovery_dispatches SET lease_expires_at = 0")
    elif physical_state == "released":
        gate["controller"].release_physical_dispatch(gate["claim"], reason="crash")
    elif physical_state == "completed":
        gate["controller"].complete_physical_dispatch(gate["claim"], result_ref="round-result")
    first = _authorize(gate)
    assert _authorize(gate) == first
    reservation = gate["context"].reservation
    route = gate["routes"].resolve_exact(gate["binding"].route_decision_ref)
    assert route.recovery_control_ref.recovery_state_revision == reservation.state_before.recovery_state_revision
    assert route.recovery_control_ref.recovery_state_revision + 1 == reservation.state_after_reservation.recovery_state_revision


def test_workflow_delegates_exact_old_two_argument_authorization(gate):
    workflow = gate["dispatcher"].workflow_runtime
    env = gate["env"]
    binding = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref=workflow.workflow_research_authorization_ref(
            lineage=env["lineage"], section_id=SECTION_ID),
    )
    ref = env["research"].derive_round_ref(
        run_id=RUN_ID, section_id=SECTION_ID, authorization_binding=binding,
        active_attempt=env["lineage"],
    )
    spy = Mock(wraps=workflow.validate_research_authorization)
    workflow.validate_research_authorization = spy
    gate["dispatcher"](binding, ref)
    spy.assert_called_once_with(binding, ref)
    assert spy.call_args.args[0] is binding and spy.call_args.args[1] is ref
    with pytest.raises(WorkflowCutoverIntegrityError):
        gate["dispatcher"](binding.model_copy(update={"authorization_ref": "stale"}), ref)


def test_committed_gate_replay_after_runtime_restart_has_zero_redispatch(gate, monkeypatch):
    authorization, _, _, _, updates = _execute(gate)
    assert gate["adapter"].calls == 1
    ref = updates["current_research_round_binding"].round_ref
    record = gate["env"]["research"].resolve_round(ref)
    assert record.authorization_binding == authorization
    gate["controller"].complete_physical_dispatch(gate["claim"], result_ref=ref.research_round_id)
    runtime = gate["env"]["research"]
    # New runtime and graph read committed Round/Package from the same durable owners.
    restarted = type(runtime)(
        artifact_runtime=runtime.artifact_runtime, section_plan_runtime=runtime.section_plan_runtime,
        fact_snapshot_runtime=runtime.fact_snapshot_runtime,
        evidence_bundle_resolver=runtime.evidence_bundle_resolver,
        authorization_validator=gate["dispatcher"], lifecycle_service=runtime.lifecycle_service,
        ledger_path=runtime.ledger_path, coordination_store=runtime.coordination_store,
    )
    coordinator, _ = _recovery_coordinator(
        gate["store"].database_path.parent,
        {**gate["env"], "research": restarted}, gate["adapter"],
    )
    gate["graph"] = SectionExecutionGraph(coordinator=coordinator)
    sentinels = [Mock(side_effect=AssertionError("replay dispatch forbidden")) for _ in range(4)]
    monkeypatch.setattr(gate["adapter"], "research", sentinels[0])
    monkeypatch.setattr(ResearchAgent, "run", sentinels[1])
    monkeypatch.setattr(ResearchAgent, "run_projected", sentinels[1])
    monkeypatch.setattr(RAGService, "retrieve", sentinels[2])
    monkeypatch.setattr(ModelGateway, "generate", sentinels[3])
    replay = _execute(gate)
    assert replay[0] == authorization
    assert replay[1] is None
    assert replay[4]["current_research_round_binding"].round_ref == ref
    assert all(s.call_count == 0 for s in sentinels)


def test_gate_round_commit_without_context_fails_before_canonical_mutation(gate):
    _, _, _, _, updates = _execute(gate)
    runtime = gate["env"]["research"]
    record = runtime.resolve_round(updates["current_research_round_binding"].round_ref)
    before = runtime.ledger_path.read_bytes()
    with pytest.raises(ValueError, match="owner context"):
        runtime.commit_round(
            round_candidate=record, operation_ref=runtime.round_operation_ref(record.round_ref),
            source_plan_binding=gate["env"]["plan_binding"],
            commit_guard_context=gate["env"]["lineage"],
            expected_progress_revision=1, expected_committed_round_count=1,
            expected_last_committed_round_ref=record.prior_committed_round_ref,
            resulting_package_binding=updates["active_evidence_package_binding"],
        )
    assert runtime.ledger_path.read_bytes() == before


def test_production_research_agent_tool_bridge_commit_reassessment_and_replay(gate, tmp_path, monkeypatch):
    from apps.enterprise_document.schemas.agent_rag_v1 import CanonicalBudgetVectorSchema
    from apps.enterprise_document.services.agent_rag_v1 import (
        BudgetManager, CanonicalInvocationBudgetAdapter, JsonlToolExecutionStore, ToolGateway,
    )
    from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
        build_round5_section_agent_adapter,
    )
    from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import MainlineResearchToolGateway
    from backend.tests.test_multi_agent_v3_research_tool_bridge import _CanonicalEvidenceService

    env = gate["env"]
    service = _CanonicalEvidenceService(env["bundle"], tmp_path / "physical-bundle-store")
    old_resolver = env["research"].evidence_bundle_resolver
    env["research"].evidence_bundle_resolver = lambda handle: (
        old_resolver(handle) if handle in env["bundles"] else service.resolve_canonical_bundle(handle)
    )
    budget = BudgetManager.canonical(
        task_id=gate["payload"].task_id, run_id=RUN_ID,
        limits=CanonicalBudgetVectorSchema(
            logical_model_calls=2, logical_tool_calls=4, model_tokens_total=4096,
            research_rounds=4, repair_attempts=2, generation_attempts=4,
            replan_attempts=2, true_retry_attempts=2,
        ),
        max_elapsed_seconds=120, storage_path=tmp_path / "tool-budget.jsonl",
        policy_ref="budget-policy://test-gate-authorization", policy_version="v1",
    )

    def tool_provider(context):
        def authorize(ctx, registration, arguments):
            assert ctx.run_id == RUN_ID and ctx.attempt_id == env["lineage"].attempt_id
            assert registration.name == MainlineResearchToolGateway.tool_name
            assert arguments["section_id"] == SECTION_ID

        def current(ctx):
            env["lifecycle"].assert_attempt_committable(
                run_id=ctx.run_id, attempt_scope_ref=f"section:{ctx.section_id}",
                attempt_id=ctx.attempt_id, input_snapshot_version=ctx.input_snapshot_version,
            )

        return ToolGateway(
            execution_store=JsonlToolExecutionStore(tmp_path / "tool-results.jsonl"),
            budget_runtime=CanonicalInvocationBudgetAdapter(budget),
            authorization_validator=authorize, currentness_validator=current,
        )

    model = Mock()
    model.generate.side_effect = AssertionError("no model calls in deterministic Research fixture")
    adapter = build_round5_section_agent_adapter(
        evidence_service=service, model_gateway=model,
        canonical_tool_gateway_provider=tool_provider,
    )
    coordinator, _ = _recovery_coordinator(
        gate["store"].database_path.parent, env, adapter, id_start=100,
    )
    graph = SectionExecutionGraph(coordinator=coordinator)
    agent_calls = Mock(wraps=adapter.research_agent.run_projected)
    monkeypatch.setattr(adapter.research_agent, "run_projected", agent_calls)
    gate["controller"].release_physical_dispatch(gate["claim"], reason="fixture-ready")
    failure_diagnostics = []
    controller = coordinator.require_pre_write_services().recovery_controller
    release = controller.release_physical_dispatch

    def capture_failure(*args, **kwargs):
        import traceback
        failure_diagnostics.append(traceback.format_exc())
        return release(*args, **kwargs)

    monkeypatch.setattr(controller, "release_physical_dispatch", capture_failure)
    state = _apply(graph, gate["state"], "_execute_pre_write_recovery")
    candidate = graph.codec.restore_payload(state)
    assert candidate.pending_recovery_dispatch is not None, "\n".join(failure_diagnostics)
    assert candidate.pending_recovery_dispatch["phase"] == "candidate_durable"
    assert candidate.recovery_runtime_state.last_round_outcome.value != "technical_failure"
    assert candidate.active_writer_admission_decision != gate["payload"].active_writer_admission_decision
    assert candidate.current_research_round_binding is not None
    assert candidate.active_evidence_package_binding is not None
    assert service.retrieve_calls == 1
    assert agent_calls.call_count == 1
    assert model.generate.call_count == 0
    state = _apply(graph, state, "_complete_pre_write_recovery_dispatch")
    state = _apply(graph, state, "_evaluate_pre_write_enforcement")
    # Replay exact source reservation after physical completion: no second Agent or RAG.
    replay = graph._execute_canonical_gate_recovery_research(
        gate["payload"], shared_state=_shared_state(),
        reservation=gate["context"].reservation, source_chain=gate["context"].source_chain,
        query="canonical recovery query",
    )
    assert replay[4]["current_research_round_binding"] == candidate.current_research_round_binding
    assert service.retrieve_calls == agent_calls.call_count == 1
    assert model.generate.call_count == 0
    assert budget.canonical_state().used.logical_tool_calls == 1


def test_factory_composes_identical_authorization_owners(tmp_path):
    from backend.tests.test_gate_v2_pre_write_config_bootstrap import _build_p5_supervisor

    supervisor, _, _ = _build_p5_supervisor(tmp_path)
    try:
        coordinator = supervisor.workflow_engine.stage_service.section_execution_coordinator
        services = coordinator.require_pre_write_services()
        dispatcher = coordinator.research_runtime.authorization_validator
        assert isinstance(dispatcher, ResearchAuthorizationDispatcher)
        assert dispatcher.recovery_validator.recovery_controller is services.recovery_controller
        assert dispatcher.recovery_validator.route_decision_store is services.route_decision_store
        assert dispatcher.recovery_validator.execution_store is services.recovery_controller._execution_store
    finally:
        supervisor.close()
