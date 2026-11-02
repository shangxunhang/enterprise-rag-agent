from __future__ import annotations

from dataclasses import replace
from itertools import count
from types import SimpleNamespace

import pytest

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    ResearchPackageProgressSchema,
    ResearchRoundAuthorizationBindingSchema,
    ResearchRoundExecutionOutcome,
    ResearchRoundRecordSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceSupportState,
    WriterAdmissionContractSchema,
)
from apps.enterprise_document.schemas.pre_write_control import PreWriteRoute
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStateSchema,
    SectionPlanSchema,
    SectionStateSchema,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    MainlineResearchRetrieval,
)
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
)
from apps.enterprise_document.services.scheme_writer.gate_recovery_research_authorization import (
    GateRecoveryResearchAuthorizationValidator,
    ResearchAuthorizationDispatcher,
)
from apps.enterprise_document.services.agent_rag_v1.workflow_runtime import TargetWorkflowRuntime
from apps.enterprise_document.services.scheme_writer.pre_write_router import PreWriteRouter
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    PreWriteCoordinatorServices,
    SectionExecutionCoordinator,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    CriterionAssessmentResult,
    WriterAdmissionFactAssessor,
    WriterAdmissionPolicy,
)
from apps.enterprise_document.services.scheme_writer.writer_admission_enforcement import (
    WriterAdmissionEnforcement,
)
from apps.enterprise_document.services.scheme_writer.writer_gate_binding import (
    WriterGateApplicabilityValidator,
    WriterGateBinder,
)
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from core.runtime.pre_write_route_decision_store import (
    SQLitePreWriteRouteDecisionStore,
)
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema

from backend.tests.test_gate_v2_pre_write_e2e import (
    _P4WriterInvocation,
    _p4_coordinator,
    _p4_requirements,
)
from backend.tests.test_gate_v2_pre_write_writer_invocation import (
    _build as _build_writer,
    _current_chain,
    _execute as _execute_writer,
    _failure_response,
    _section_service,
    _service as _writer_service,
    _success_response,
)
from backend.tests.test_multi_agent_v3_research_runtime import (
    RUN_ID,
    SECTION_ID,
    TASK_ID,
    _requirements,
    _round_record,
    _setup,
)
from backend.tests.test_rag_interface_v2_phase5_id_namespace import (
    _CapturingResearchAdapter,
)


def _commit_initial_research_round(env):
    record = _round_record(env)
    return env["research"].commit_round(
        round_candidate=record,
        operation_ref=env["research"].round_operation_ref(record.round_ref),
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=env["package_binding"],
        commit_guard_context=env["lineage"],
        expected_progress_revision=0,
        expected_committed_round_count=0,
        expected_last_committed_round_ref=None,
    )


def _shared_state() -> GraphStateSchema:
    return GraphStateSchema(
        task_id=TASK_ID,
        run_id=RUN_ID,
        task_type="scheme_generation",
        user_input="step7 B5 canonical pre-write readiness",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(
                user_query="step7 B5 canonical pre-write readiness"
            ),
            task=TaskContextSchema(
                task_id=TASK_ID,
                run_id=RUN_ID,
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-09-04T20:00:00+08:00",
    )


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id=TASK_ID,
        task_type="scheme_generation",
        user_query="step7 B5 canonical pre-write readiness",
        generation_requirements={
            "required_sections": ["Section 1"],
            "need_citation": True,
            "citation_required_sections": ["Section 1"],
        },
        output_schema={
            "document_title": "B5 Document",
            "required_sections": ["Section 1"],
        },
    )


def _canonical_payload(
    graph: SectionExecutionGraph,
    env,
    round_binding,
    *,
    active_bundle=None,
    package_binding=None,
    section_attempt_id: str | None = None,
) -> SectionExecutionGraphPayloadSchema:
    lineage = env["lineage"]
    bundle = env["bundle"] if active_bundle is None else active_bundle
    return SectionExecutionGraphPayloadSchema(
        task_id=TASK_ID,
        run_id=RUN_ID,
        document_id="doc-1",
        project_input=_project_input(),
        section_plan=SectionPlanSchema(
            section_id=SECTION_ID,
            section_title="Section 1",
            section_order=1,
            citation_required=True,
        ),
        shared_state_payload=graph.codec.dump_graph_state(_shared_state()),
        document_evidence_bundle=bundle,
        section_id=SECTION_ID,
        section_title="Section 1",
        section_order=1,
        citation_required=True,
        section_state=SectionStateSchema(
            section_id=SECTION_ID,
            section_title="Section 1",
            section_order=1,
            attempt=AttemptStateSchema(
                attempt_id=section_attempt_id or lineage.attempt_id,
                attempt_number=1,
                execution_id="step7-b5-execution",
                plan_revision=1,
                input_snapshot_version=lineage.input_snapshot_version,
            ),
        ),
        active_evidence_bundle=bundle,
        canonical_plan_binding=env["plan_binding"],
        active_execution_lineage=lineage,
        active_evidence_package_binding=(
            env["package_binding"] if package_binding is None else package_binding
        ),
        current_research_round_binding=round_binding,
    )


def _canonical_graph(env) -> tuple[SectionExecutionCoordinator, SectionExecutionGraph]:
    coordinator = _p4_coordinator(
        writer=_P4WriterInvocation(),
        recovery_rounds_limit=2,
    )
    coordinator.attach_research_runtime(env["research"])
    return coordinator, SectionExecutionGraph(coordinator=coordinator)


def _apply(graph: SectionExecutionGraph, state: dict, method_name: str) -> dict:
    return {**state, **getattr(graph, method_name)(state)}


def test_current_research_package_drives_gate_with_exact_plan_requirements(
    tmp_path,
) -> None:
    env = _setup(tmp_path / "research")
    committed = _commit_initial_research_round(env)
    coordinator, graph = _canonical_graph(env)
    assert (
        coordinator.require_pre_write_services().requirements.requirements_sha256
        != _requirements().requirements_sha256
    )
    payload = _canonical_payload(graph, env, committed.binding)
    state = graph.codec.build_initial_state(payload)

    state = _apply(graph, state, "_evaluate_pre_write_admission")
    current = graph.codec.restore_payload(state)

    assert current.active_evidence_requirements_snapshot == _requirements()
    assert (
        current.active_writer_admission_fact_assessment.requirements_sha256
        == _requirements().requirements_sha256
    )
    assert current.active_writer_admission_fact_assessment.evidence_bundle_sha256 == (
        evidence_bundle_sha256(env["bundle"])
    )
    assert current.active_writer_admission_decision is not None


def test_stale_or_bundle_mismatched_package_fails_before_gate_assessment(
    tmp_path,
) -> None:
    env = _setup(tmp_path / "research")
    committed = _commit_initial_research_round(env)
    coordinator, graph = _canonical_graph(env)
    services = coordinator.require_pre_write_services()
    assert services.fact_assessor.assess.call_count == 0

    stale_binding = env["package_binding"].model_copy(
        update={
            "resolved_head_revision": (
                env["package_binding"].resolved_head_revision + 1
            )
        }
    )
    stale = _canonical_payload(
        graph,
        env,
        committed.binding,
        package_binding=stale_binding,
    )
    with pytest.raises(ValueError, match="stale"):
        graph._canonical_pre_write_sources(stale)
    assert services.fact_assessor.assess.call_count == 0

    mismatched_bundle = env["bundle"].model_copy(
        update={"query": "different active projection"},
        deep=True,
    )
    mismatch = _canonical_payload(
        graph,
        env,
        committed.binding,
        active_bundle=mismatched_bundle,
    )
    with pytest.raises(ValueError, match="projection disagrees"):
        graph._canonical_pre_write_sources(mismatch)
    assert services.fact_assessor.assess.call_count == 0


def test_technical_failure_current_research_round_cannot_authorize_gate(
    tmp_path,
) -> None:
    env = _setup(tmp_path / "research")
    first = _commit_initial_research_round(env)
    progress = first.progress
    authorization = ResearchRoundAuthorizationBindingSchema(
        authorization_kind="WORKFLOW_RESEARCH",
        authorization_ref="workflow-start-research://b5-terminal-failure",
    )
    ref = env["research"].derive_round_ref(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        authorization_binding=authorization,
        active_attempt=env["lineage"],
    )
    failure = ResearchRoundRecordSchema(
        round_ref=ref,
        round_ordinal=2,
        authorization_binding=authorization,
        prior_committed_round_ref=first.binding.round_ref,
        source_plan_version_ref=env["plan_version"],
        execution_outcome=ResearchRoundExecutionOutcome.TECHNICAL_FAILURE,
        package_progress=ResearchPackageProgressSchema(
            prior_evidence_package_version_ref=env["package_version"],
            resulting_evidence_package_version_ref=None,
            material_package_change="UNAVAILABLE_DUE_TO_TECHNICAL_FAILURE",
        ),
    )
    failed = env["research"].commit_round(
        round_candidate=failure,
        operation_ref=env["research"].round_operation_ref(ref),
        source_plan_binding=env["plan_binding"],
        resulting_package_binding=None,
        commit_guard_context=env["lineage"],
        expected_progress_revision=progress.progress_revision,
        expected_committed_round_count=progress.committed_round_count,
        expected_last_committed_round_ref=progress.last_committed_round_ref,
    )
    coordinator, graph = _canonical_graph(env)
    payload = _canonical_payload(graph, env, failed.binding)

    with pytest.raises(ValueError, match="TECHNICAL_FAILURE"):
        graph._canonical_pre_write_sources(payload)
    assert coordinator.require_pre_write_services().fact_assessor.assess.call_count == 0


def test_persisted_gate_write_instance_must_match_active_sif01_attempt(
    tmp_path,
) -> None:
    env = _setup(tmp_path / "research")
    committed = _commit_initial_research_round(env)
    _coordinator, graph = _canonical_graph(env)
    payload = _canonical_payload(
        graph,
        env,
        committed.binding,
        section_attempt_id="stale-checkpoint-attempt",
    )

    with pytest.raises(ValueError, match="write_instance_id disagrees"):
        graph._pre_write_identity(payload)


def test_successful_writer_forms_exact_execution_and_invocation_refs(
    tmp_path,
) -> None:
    chain = _current_chain(allow=True)
    store = SQLitePreWriteExecutionStore(tmp_path / "writer.sqlite3")
    calls: list[dict[str, object]] = []
    service = _writer_service(
        store=store,
        section_service=_section_service(
            calls,
            responses=[_success_response()],
        ),
    )
    prepared = _build_writer(service.builder, chain)
    result = _execute_writer(service, chain)
    coordinator = _p4_coordinator(
        writer=service,
        recovery_rounds_limit=2,
    )

    execution_ref, invocation_ref = coordinator.resolve_pre_write_writer_refs(
        result=result,
        invocation=prepared.invocation,
    )

    assert execution_ref is not None
    assert invocation_ref is not None
    assert store.resolve_writer_execution(execution_ref) == result
    assert store.resolve_writer_generation_invocation(invocation_ref) == (
        prepared.invocation
    )
    record = store.get_writer_execution(execution_ref.logical_execution_key)
    assert record is not None
    assert invocation_ref.invocation_sha256 == record.semantic_fingerprint


class _P4DurableWriterInvocation(_P4WriterInvocation):
    def __init__(self) -> None:
        self.last_invocation = None
        self.last_result = None
        super().__init__()
        self.builder = SimpleNamespace(build=self._build_durable)
        self.execution_store = SimpleNamespace(
            reserve_writer_execution=lambda *args, **kwargs: None,
            resolve_writer_execution=self._resolve_result,
            resolve_writer_generation_invocation=self._resolve_invocation,
        )

    def _build_durable(self, *args, **kwargs):
        prepared = _P4WriterInvocation._build(*args, **kwargs)
        self.last_invocation = prepared.invocation
        return prepared

    def execute_initial(self, route_decision, **kwargs):
        result = super().execute_initial(route_decision, **kwargs)
        self.last_result = result
        return result

    def _resolve_result(self, ref):
        assert self.last_result is not None
        from core.hashing import canonical_sha256

        assert ref.result_sha256 == canonical_sha256(
            self.last_result.model_dump(mode="json")
        )
        return self.last_result

    def _resolve_invocation(self, ref):
        assert self.last_invocation is not None
        from core.hashing import canonical_sha256

        assert ref.invocation_sha256 == canonical_sha256(
            self.last_invocation.model_dump(mode="json")
        )
        return self.last_invocation


def test_section_graph_persists_exact_writer_refs_for_paragraph_handoff(
    tmp_path,
) -> None:
    env = _setup(tmp_path / "research")
    initial = _commit_initial_research_round(env)
    writer = _P4DurableWriterInvocation()
    coordinator = _p4_coordinator(
        writer=writer,
        recovery_rounds_limit=2,
    )
    coordinator.attach_research_runtime(env["research"])
    graph = SectionExecutionGraph(coordinator=coordinator)
    payload = _canonical_payload(graph, env, initial.binding)
    state = graph.codec.build_initial_state(payload)

    for method_name in (
        "_evaluate_pre_write_admission",
        "_bind_pre_write_gate",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
        "_invoke_pre_write_writer",
    ):
        state = _apply(graph, state, method_name)

    current = graph.codec.restore_payload(state)
    assert current.current_pre_write_route_decision is not None
    assert current.current_pre_write_route_decision.selected_route is PreWriteRoute.NORMAL
    assert current.writer_execution_ref is not None
    assert current.writer_generation_invocation_ref is not None
    assert writer.execution_store.resolve_writer_execution(
        current.writer_execution_ref
    ) == writer.last_result
    assert writer.execution_store.resolve_writer_generation_invocation(
        current.writer_generation_invocation_ref
    ) == writer.last_invocation
    assert current.writer_execution_ref.logical_execution_key == (
        current.writer_generation_invocation_ref.logical_execution_key
    )


def test_writer_technical_failure_never_becomes_gate_fallback_or_exact_success_ref(
    tmp_path,
) -> None:
    chain = _current_chain(allow=True)
    assert chain.route_decision.selected_route is PreWriteRoute.NORMAL
    store = SQLitePreWriteExecutionStore(tmp_path / "writer-failure.sqlite3")
    service = _writer_service(
        store=store,
        section_service=_section_service(
            [],
            responses=[_failure_response()],
        ),
    )
    prepared = _build_writer(service.builder, chain)
    result = _execute_writer(service, chain)
    coordinator = _p4_coordinator(
        writer=service,
        recovery_rounds_limit=2,
    )

    assert result.status.value == "technical_failure"
    assert chain.route_decision.selected_route is PreWriteRoute.NORMAL
    with pytest.raises(ValueError, match="successful canonical result"):
        coordinator.resolve_pre_write_writer_refs(
            result=result,
            invocation=prepared.invocation,
        )


class _AlwaysNotSupportedEvaluator:
    def evaluate(self, *, requirement, criterion, selected_evidence):
        del requirement, criterion, selected_evidence
        return CriterionAssessmentResult(
            support_state=CriterionEvidenceSupportState.NOT_SUPPORTED,
            evidence_ids=(),
        )


class _ProjectionEvidenceService:
    @staticmethod
    def extract_rag_output(shared_state, bundle):
        del shared_state
        context, chunks, citations = project_evidence_bundle(bundle)
        return (
            context,
            chunks,
            citations,
            {
                "context": context.model_dump(mode="json"),
                "retrieved_chunks": [
                    item.model_dump(mode="json") for item in chunks
                ],
                "citations": [
                    item.model_dump(mode="json") for item in citations
                ],
            },
        )


class _RecoveryQueryBuilder:
    calls = 0

    def build_recovery_target_query(self, *args, **kwargs) -> str:
        del args, kwargs
        self.calls += 1
        return "step7 B5 canonical recovery query"


class _CanonicalRecoveryAdapter(_CapturingResearchAdapter):
    def __init__(self, env) -> None:
        super().__init__(result_bundle=env["bundle"])
        self._binding = env["bundle_binding"]
        self.calls = 0

    def research(self, **kwargs):
        self.calls += 1
        outcome = super().research(**kwargs)
        assert outcome.retrieval is not None
        outcome.retrieval = replace(
            outcome.retrieval,
            canonical_bundle_binding=self._binding,
        )
        return outcome


def _recovery_coordinator(tmp_path, env, adapter, *, id_start=1):
    ids = count(id_start)
    database = tmp_path / "gate-recovery.sqlite3"
    execution_store = SQLitePreWriteExecutionStore(database)
    route_store = SQLitePreWriteRouteDecisionStore(database)
    recovery_controller = PreWriteRecoveryController(
        execution_store=execution_store
    )
    coordinator = SectionExecutionCoordinator(
        evidence_service=_ProjectionEvidenceService(),  # type: ignore[arg-type]
        query_builder=_RecoveryQueryBuilder(),  # type: ignore[arg-type]
        section_generation_service=SimpleNamespace(),
        runtime_support=SimpleNamespace(),
        generation_quality_metadata={},
    )
    coordinator.attach_pre_write_services(
        PreWriteCoordinatorServices(
            requirements=_p4_requirements(),
            fact_assessor=WriterAdmissionFactAssessor(
                evaluator=_AlwaysNotSupportedEvaluator(),
                assessment_id_factory=lambda: f"b5-deny-assessment-{next(ids)}",
            ),
            admission_policy=WriterAdmissionPolicy(
                decision_id_factory=lambda: f"b5-deny-decision-{next(ids)}"
            ),
            admission_contract=WriterAdmissionContractSchema(),
            gate_binder=WriterGateBinder(),
            applicability_validator=WriterGateApplicabilityValidator(),
            enforcement=WriterAdmissionEnforcement(),
            recovery_controller=recovery_controller,
            router=PreWriteRouter,
            route_decision_store=route_store,
            writer_invocation=SimpleNamespace(execution_store=execution_store),
            enforcement_mode="strict",
            recovery_rounds_limit=2,
            no_progress_rounds_limit=1,
        )
    )
    coordinator.attach_research_runtime(env["research"])
    coordinator.attach_section_agent_adapter(adapter)  # type: ignore[arg-type]
    workflow = TargetWorkflowRuntime(
        task_manager=None,
        lifecycle_service=env["lifecycle"],
        fact_snapshot_runtime=env["fact"],
        section_plan_runtime=env["plan"],
        requirements=_requirements(),
    )
    env["research"].authorization_validator = ResearchAuthorizationDispatcher(
        workflow_runtime=workflow,
        recovery_validator=GateRecoveryResearchAuthorizationValidator(
            recovery_controller=recovery_controller,
            route_decision_store=route_store,
            execution_store=execution_store,
        ),
    )
    return coordinator, route_store


def test_gate_recovery_dispatch_binds_exact_route_and_replays_same_research_round(
    tmp_path,
) -> None:
    env = _setup(tmp_path / "research")
    initial = _commit_initial_research_round(env)
    adapter = _CanonicalRecoveryAdapter(env)
    coordinator, route_store = _recovery_coordinator(
        tmp_path,
        env,
        adapter,
    )
    graph = SectionExecutionGraph(coordinator=coordinator)
    payload = _canonical_payload(graph, env, initial.binding)
    state = graph.codec.build_initial_state(payload)

    for method_name in (
        "_evaluate_pre_write_admission",
        "_bind_pre_write_gate",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
        "_dispatch_pre_write_recovery",
    ):
        state = _apply(graph, state, method_name)

    reserved = graph.codec.restore_payload(state)
    route = reserved.current_pre_write_route_decision
    assert route is not None
    assert route.selected_route is PreWriteRoute.RECOVERY
    assert route_store.resolve_exact(graph._route_ref(route)) == route
    reservation = graph._restore_reservation(
        dict(reserved.pending_recovery_dispatch["reservation"])
    )
    query = coordinator.query_builder.build_recovery_target_query(
        reservation.target,
        reserved.active_evidence_requirements_snapshot,
        section_title=str(reserved.section_title),
    )

    state = _apply(graph, state, "_execute_pre_write_recovery")
    candidate = graph.codec.restore_payload(state)
    assert candidate.pending_recovery_dispatch["phase"] == "candidate_durable"
    assert candidate.research_authorization_binding is not None
    assert (
        candidate.research_authorization_binding.authorization_kind
        == "GATE_RECOVERY_RESEARCH"
    )
    assert (
        candidate.research_authorization_binding.authorization_ref
        == reservation.occurrence_id
    )
    assert candidate.research_authorization_binding.route_decision_ref == (
        graph._route_ref(route)
    )
    assert candidate.active_evidence_package_binding is not None
    assert candidate.current_research_round_binding is not None
    assert adapter.calls == 1

    replay = graph._execute_canonical_gate_recovery_research(
        reserved,
        shared_state=_shared_state(),
        reservation=reservation,
        source_chain=graph._pre_write_chain(reserved),
        query=query,
    )
    replay_authorization, replay_outcome, replay_retrieval, _, replay_updates = replay

    assert replay_authorization == candidate.research_authorization_binding
    assert replay_outcome is None
    assert replay_retrieval.evidence_bundle == env["bundle"]
    assert adapter.calls == 1
    assert (
        replay_updates["current_research_round_binding"]
        == candidate.current_research_round_binding
    )
    progress = env["research"].resolve_progress(
        run_id=RUN_ID,
        section_id=SECTION_ID,
        attempt_scope_ref=env["lineage"].attempt_scope_ref,
        attempt_id=env["lineage"].attempt_id,
    )
    # Initial Workflow Research + one Gate Recovery Research.
    assert progress.committed_round_count == 2

    missing_route_payload = reserved.model_copy(
        update={
            "current_pre_write_route_decision": route.model_copy(
                update={"route_decision_id": "missing-b5-route"}
            )
        },
        deep=True,
    )
    with pytest.raises(Exception):
        graph._execute_canonical_gate_recovery_research(
            missing_route_payload,
            shared_state=_shared_state(),
            reservation=reservation,
            source_chain=graph._pre_write_chain(reserved),
            query=query,
        )
    assert adapter.calls == 1
