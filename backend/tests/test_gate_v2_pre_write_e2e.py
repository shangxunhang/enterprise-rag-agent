from __future__ import annotations

from itertools import count
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.agents.agent_rag_v1 import ResearchAgent
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentRole,
    ResearchEvidencePackageSchema,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    ResearchStatus,
    ToolResultStatus,
    WorkflowErrorCategory,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    CriterionEvidenceSupportState,
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    WriterAdmissionContractSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRoute,
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionFailureKind,
    WriterExecutionResultSchema,
    WriterExecutionStatus,
    WriterGenerationClass,
    WriterGenerationInvocationSchema,
    WriterLogicalExecutionKey,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    AttemptStateSchema,
    SchemeSectionSchema,
    SectionExecutionRequestSchema,
    SectionPlanSchema,
    SectionStateSchema,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_reasoners import (
    MainlineResearchReasoner,
)
from apps.enterprise_document.services.scheme_writer.pre_write_recovery import (
    PreWriteRecoveryController,
)
from apps.enterprise_document.services.scheme_writer.pre_write_router import PreWriteRouter
from core.hashing import canonical_sha256
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from apps.enterprise_document.services.scheme_writer.section_execution_coordinator import (
    PreWriteCoordinatorServices,
    PreWriteWriterExecutionFailed,
    SectionExecutionCoordinator,
)
from apps.enterprise_document.services.scheme_writer.writer_admission import (
    CriterionAssessmentResult,
    EvidenceRequirementsMaterializer,
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
from contracts.evidence import (
    build_evidence_bundle,
    evidence_bundle_sha256,
    project_evidence_bundle,
)
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from schemas.citation import CitationSchema
from schemas.common import ErrorSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import EvidenceBundleSchema, RAGContextSchema, RetrievedChunkSchema
from schemas.status import ExecutionStatus


class _Tools:
    def __init__(self, *, status: ToolResultStatus, data: dict, error_code: str | None = None) -> None:
        self.status = status
        self.data = data
        self.error_code = error_code
        self.call_refs: list[str] = []

    def call(self, tool_name: str, arguments: dict):
        assert tool_name == "knowledge.retrieve"
        self.call_refs.append("research-call-1")
        return SimpleNamespace(
            status=self.status,
            data=dict(self.data),
            error=SimpleNamespace(error_code=self.error_code),
        )


class _Gateway:
    def __init__(self, tools: _Tools) -> None:
        self.tools = tools

    def session(self, context: AgentExecutionContextSchema) -> _Tools:
        del context
        return self.tools


class _StaticReasoner:
    def __init__(self, payload: ResearchResultPayloadSchema) -> None:
        self.payload = payload

    def invoke(self, *, context, invocation_input, tools):
        del context, invocation_input, tools
        return self.payload


def _context() -> AgentExecutionContextSchema:
    return AgentExecutionContextSchema(
        invocation_id="research-invocation-1",
        logical_operation_id="research-operation-1",
        task_id="task-1",
        run_id="run-1",
        section_id="section-1",
        agent_role=AgentRole.RESEARCH,
        execution_mode="section_research",
        execution_id="research-execution-1",
        attempt_id="attempt-1",
        input_snapshot_version="snapshot-1",
        task_context_ref="state://task/task-1",
        source_snapshot_ref="state://source/snapshot-1",
        section_state_ref="state://section-state/section-1",
        authorized_tool_names=["knowledge.retrieve"],
    )


def _invocation(*, query: str = "q") -> ResearchInvocationInputSchema:
    return ResearchInvocationInputSchema(
        section_id="section-1",
        section_task_ref="state://section-state/section-1",
        fact_snapshot_ref="state://source/snapshot-1",
        source_inventory_ref="state://source/snapshot-1",
        evidence_topics=["topic-1"],
        retrieval_scope="document",
        research_round=1,
        query=query,
        section_title="Section 1",
        citation_required=True,
    )


def _run_mainline(*, status: ToolResultStatus, data: dict, error_code: str | None = None):
    tools = _Tools(status=status, data=data, error_code=error_code)
    agent = ResearchAgent(
        reasoner=MainlineResearchReasoner(),
        tool_gateway=_Gateway(tools),
    )
    return agent.run_projected(_context(), _invocation())


def test_p2d_successful_zero_result_is_completed_empty_handoff_not_business_deny() -> None:
    result = _run_mainline(
        status=ToolResultStatus.EMPTY,
        data={
            "retrieval_succeeded": True,
            "assessment": {
                "status": "insufficient",
                "details": {"gaps": ["missing-topic"]},
            },
            "accepted_evidence_ids": [],
            "rejected_evidence_ids": [],
            "tool_call_ids": ["trace-1"],
            "evidence_contract_sha256": "sha256:empty-evidence",
        },
    )

    assert result.status is AgentInvocationStatus.COMPLETED
    assert result.error is None
    assert result.emitted_signals == []
    payload = result.payload
    assert isinstance(payload, ResearchResultPayloadSchema)
    assert payload.research_execution_status == "completed"
    assert payload.evidence_contract_sha256 == "sha256:empty-evidence"
    assert payload.evidence_summary.accepted_evidence_ids == []
    assert payload.evidence_summary.research_status is None
    assert payload.evidence_summary.recommended_action is None
    assert payload.agent_metadata["retrieval_local_sufficiency_estimate"] == "insufficient"
    assert payload.agent_metadata["retrieval_local_no_gain_estimate"] is True


def test_p2d_valid_partial_is_technical_degradation_not_partial_delivery() -> None:
    result = _run_mainline(
        status=ToolResultStatus.PARTIAL,
        data={
            "retrieval_succeeded": True,
            "assessment": {"status": "not_assessed", "details": {}},
            "accepted_evidence_ids": ["evidence-1"],
            "rejected_evidence_ids": [],
            "tool_call_ids": ["trace-1"],
            "evidence_contract_sha256": "sha256:partial-evidence",
        },
    )

    assert result.status is AgentInvocationStatus.PARTIAL
    assert result.error is None
    assert result.emitted_signals == []
    payload = result.payload
    assert isinstance(payload, ResearchResultPayloadSchema)
    assert payload.research_execution_status == "partial"
    assert payload.evidence_summary.recommended_action is None
    assert payload.evidence_summary.research_status is None


def test_p2d_provider_tool_error_remains_technical_failure() -> None:
    result = _run_mainline(
        status=ToolResultStatus.RETRYABLE_ERROR,
        data={"retrieval_succeeded": False},
        error_code="PROVIDER_TIMEOUT",
    )

    assert result.status is AgentInvocationStatus.FAILED
    assert result.payload is None
    assert result.emitted_signals == []
    assert result.error is not None
    assert result.error.category is WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE
    assert result.error.category is not WorkflowErrorCategory.EVIDENCE_INSUFFICIENT


def test_p2d_partial_without_valid_handoff_is_technical_failure() -> None:
    result = _run_mainline(
        status=ToolResultStatus.PARTIAL,
        data={
            "retrieval_succeeded": False,
            "error_message": "provider returned no valid evidence handoff",
        },
    )
    assert result.status is AgentInvocationStatus.FAILED
    assert result.payload is None
    assert result.error is not None
    assert result.error.category is WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE


def test_p2d_precomputed_unsuccessful_retrieval_is_technical_failure() -> None:
    tools = _Tools(status=ToolResultStatus.SUCCESS, data={})
    agent = ResearchAgent(
        reasoner=MainlineResearchReasoner(),
        tool_gateway=_Gateway(tools),
    )
    invocation = _invocation(query="").model_copy(
        update={"retrieval_succeeded": False},
        deep=True,
    )
    result = agent.run_projected(_context(), invocation)

    assert tools.call_refs == []
    assert result.status is AgentInvocationStatus.FAILED
    assert result.payload is None
    assert result.error is not None
    assert result.error.category is WorkflowErrorCategory.TRANSIENT_INFRASTRUCTURE


def test_p2d_legacy_action_mutation_does_not_change_target_agent_semantics() -> None:
    outputs = []
    for action, legacy_status in (
        (ResearchRecommendedAction.WRITE, ResearchStatus.SUFFICIENT),
        (ResearchRecommendedAction.HUMAN_REVIEW, ResearchStatus.CONFLICTED),
        (ResearchRecommendedAction.PARTIAL_DELIVERY, ResearchStatus.INSUFFICIENT),
        (ResearchRecommendedAction.RESEARCH_MORE, ResearchStatus.EXHAUSTED),
    ):
        payload = ResearchResultPayloadSchema(
            section_id="section-1",
            evidence_package_ref="state://evidence/section-1",
            evidence_summary=ResearchEvidencePackageSchema(
                section_id="section-1",
                accepted_evidence_ids=[],
                evidence_gaps=["diagnostic-gap"],
                research_status=legacy_status,
                recommended_action=action,
            ),
            research_execution_status="completed",
            agent_metadata={"compatibility_fixture": True},
        )
        tools = _Tools(status=ToolResultStatus.SUCCESS, data={})
        agent = ResearchAgent(
            reasoner=_StaticReasoner(payload),
            tool_gateway=_Gateway(tools),
        )
        result = agent.run_projected(_context(), _invocation(query=""))
        outputs.append((result.status, tuple(result.emitted_signals)))

    assert outputs == [(AgentInvocationStatus.COMPLETED, ())] * 4


class _P4SupportEvaluator:
    def evaluate(self, *, requirement, criterion, selected_evidence):
        del requirement, criterion
        return CriterionAssessmentResult(
            support_state=CriterionEvidenceSupportState.SUPPORTED,
            evidence_ids=(selected_evidence[0].evidence_id,),
        )


class _P4WriterInvocation:
    def __init__(self, *, technical_failure: bool = False) -> None:
        self.technical_failure = technical_failure
        self.routes: list[PreWriteRoute] = []
        self.builder = SimpleNamespace(build=self._build)
        self.integrity_validator = SimpleNamespace(
            validate=lambda prepared, **kwargs: prepared
        )
        self.execution_store = SimpleNamespace(
            reserve_writer_execution=lambda *args, **kwargs: None
        )

    @staticmethod
    def _build(route_decision, *, evidence_bundle, section_title, **kwargs):
        del kwargs
        route_ref = PreWriteRouteDecisionRefSchema(
            route_decision_id=route_decision.route_decision_id
        )
        logical_key = WriterLogicalExecutionKey(
            writer_subject_ref=route_decision.writer_subject_ref,
            write_instance_id=route_decision.write_instance_id,
            route_decision_ref=route_ref,
        )
        invocation = WriterGenerationInvocationSchema(
            writer_subject_ref=route_decision.writer_subject_ref,
            write_instance_id=route_decision.write_instance_id,
            route_decision_ref=route_ref,
            generation_class=(
                WriterGenerationClass.FALLBACK_WRITE
                if route_decision.selected_route is PreWriteRoute.FALLBACK
                else WriterGenerationClass.NORMAL_WRITE
            ),
            canonical_evidence_input={
                **evidence_bundle.model_dump(mode="json"),
                "evidence_bundle_sha256": evidence_bundle_sha256(evidence_bundle),
            },
            unified_writer_input_snapshot={"section_title": section_title},
        )
        return SimpleNamespace(
            invocation=invocation,
            logical_execution_key=logical_key,
            semantic_fingerprint=canonical_sha256(invocation.model_dump(mode="json")),
        )

    def execute_initial(self, route_decision, **kwargs):
        self.routes.append(route_decision.selected_route)
        logical_key = WriterLogicalExecutionKey(
            writer_subject_ref=kwargs["writer_subject_ref"],
            write_instance_id=kwargs["write_instance_id"],
            route_decision_ref=PreWriteRouteDecisionRefSchema(
                route_decision_id=route_decision.route_decision_id
            ),
        )
        provenance = {
            "generation_class": (
                "fallback_write"
                if route_decision.selected_route is PreWriteRoute.FALLBACK
                else "normal_write"
            )
        }
        if self.technical_failure:
            return WriterExecutionResultSchema(
                logical_execution_key=logical_key,
                status=WriterExecutionStatus.TECHNICAL_FAILURE,
                failure_kind=WriterExecutionFailureKind.MODEL_TIMEOUT,
                error=ErrorSchema(
                    error_code="MODEL_TIMEOUT",
                    error_type="TimeoutError",
                    message="provider timed out",
                ),
                generation_provenance=provenance,
            )
        return WriterExecutionResultSchema(
            logical_execution_key=logical_key,
            status=WriterExecutionStatus.SUCCESS,
            artifact=SchemeSectionSchema(
                section_id=kwargs["writer_subject_ref"].section_id,
                section_title=kwargs["section_title"],
                section_order=kwargs["section_order"],
                content="canonical Writer artifact",
                status=ExecutionStatus.SUCCESS,
            ),
            generation_provenance=provenance,
        )


class _P4RecoveryEvidenceService:
    extract_rag_output = staticmethod(SchemeEvidenceService.extract_rag_output)

    def __init__(self, candidate: EvidenceBundleSchema) -> None:
        self.candidate = candidate.model_copy(
            update={"retrieval_trace_id": "p4-recovery-call-1"},
            deep=True,
        )
        self.calls = 0

    def retrieve(self, *args, **kwargs) -> EvidenceBundleSchema:
        del args, kwargs
        self.calls += 1
        return self.candidate


class _P4RecoveryQueryBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, *args, **kwargs) -> str:
        del args, kwargs
        self.calls += 1
        return "p4 deterministic section query"

    def build_recovery_target_query(self, *args, **kwargs) -> str:
        del args, kwargs
        self.calls += 1
        return "p4 deterministic recovery query"


def _p4_bundle(*, selected: bool) -> EvidenceBundleSchema:
    if not selected:
        return EvidenceBundleSchema(
            query="p4 initial query",
            items=[],
            selected_evidence_ids=[],
            dropped_evidence_ids=[],
            context=RAGContextSchema(
                context_text="",
                used_context_chars=0,
                context_item_count=0,
            ),
        )
    text = "The approved capacity is 100 MW."
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id="p4-child",
        context_chunk_id="p4-parent",
        child_chunk_id="p4-child",
        parent_chunk_id="p4-parent",
        doc_id="p4-doc",
        matched_granularity="child",
        context_granularity="parent",
        match_text=text,
        context_text=text,
        title="P4 Evidence",
    )
    citation = CitationSchema(
        citation_id="p4-source",
        source_type="document",
        source_document_id="p4-doc",
        doc_id="p4-doc",
        child_chunk_id="p4-child",
        parent_chunk_id="p4-parent",
        quote_text=text,
        title="P4 Evidence",
    )
    return build_evidence_bundle(
        query="p4 recovery query",
        rewritten_queries=[],
        selected_chunks=[chunk],
        dropped_chunks=[],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )


def _p4_requirements():
    return EvidenceRequirementsMaterializer.materialize(
        source_profile_ref="profile:p4:v1",
        source_task_ref="task:p4:section:v1",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="p4-r1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="p4-c1",
                        criterion="The approved capacity is supported.",
                    ),
                ),
            ),
        ),
    )


class _P4RouteStore:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[object, str]] = {}

    def put_if_absent_or_same(self, decision, *, authorization_snapshot_sha256):
        current = (decision, authorization_snapshot_sha256)
        existing = self.rows.get(decision.route_decision_id)
        if existing is not None and existing != current:
            raise ValueError("test RouteDecision immutable-row conflict")
        self.rows[decision.route_decision_id] = current
        return PreWriteRouteDecisionRefSchema(
            route_decision_id=decision.route_decision_id
        )

    def resolve_exact(self, ref):
        try:
            return self.rows[ref.route_decision_id][0]
        except KeyError as exc:
            raise ValueError("test RouteDecision missing") from exc

    def resolve_exact_for_snapshot(self, ref, *, authorization_snapshot_sha256):
        try:
            decision, stored_hash = self.rows[ref.route_decision_id]
        except KeyError as exc:
            raise ValueError("test RouteDecision missing") from exc
        if stored_hash != authorization_snapshot_sha256:
            raise ValueError("test RouteDecision snapshot mismatch")
        return decision


def _p4_coordinator(
    *,
    writer: _P4WriterInvocation,
    recovery_rounds_limit: int,
    recovery_controller: PreWriteRecoveryController | None = None,
    route_decision_store=None,
    evidence_service=None,
    query_builder=None,
) -> SectionExecutionCoordinator:
    ids = count(1)
    coordinator = SectionExecutionCoordinator(
        evidence_service=evidence_service or SimpleNamespace(),
        query_builder=query_builder or SimpleNamespace(),
        section_generation_service=SimpleNamespace(),
        runtime_support=SimpleNamespace(),
        generation_quality_metadata={},
    )
    fact_assessor = Mock(
        wraps=WriterAdmissionFactAssessor(
            evaluator=_P4SupportEvaluator(),
            assessment_id_factory=lambda: f"p4-assessment-{next(ids)}",
        )
    )
    admission_policy = Mock(
        wraps=WriterAdmissionPolicy(
            decision_id_factory=lambda: f"p4-decision-{next(ids)}"
        )
    )
    gate_binder = Mock(wraps=WriterGateBinder())
    enforcement = Mock(wraps=WriterAdmissionEnforcement())
    router = SimpleNamespace(
        evaluate=Mock(wraps=PreWriteRouter.evaluate),
        validate_commit=Mock(wraps=PreWriteRouter.validate_commit),
        authorization_snapshot_sha256=Mock(
            wraps=PreWriteRouter.authorization_snapshot_sha256
        ),
    )
    coordinator.attach_pre_write_services(
        PreWriteCoordinatorServices(
            requirements=_p4_requirements(),
            fact_assessor=fact_assessor,
            admission_policy=admission_policy,
            admission_contract=WriterAdmissionContractSchema(),
            gate_binder=gate_binder,
            applicability_validator=WriterGateApplicabilityValidator(),
            enforcement=enforcement,
            recovery_controller=(
                recovery_controller or PreWriteRecoveryController()
            ),
            router=router,
            route_decision_store=(
                route_decision_store
                or getattr(writer, "route_decision_store", None)
                or _P4RouteStore()
            ),
            writer_invocation=writer,
            recovery_rounds_limit=recovery_rounds_limit,
        )
    )
    return coordinator


def _p4_pre_write_producer_counts(
    coordinator: SectionExecutionCoordinator,
) -> tuple[int, int, int, int, int]:
    services = coordinator.require_pre_write_services()
    return (
        services.fact_assessor.assess.call_count,
        services.admission_policy.evaluate.call_count,
        services.gate_binder.bind.call_count,
        services.enforcement.evaluate.call_count,
        (
            services.router.evaluate.call_count
            + services.router.validate_commit.call_count
        ),
    )


def _p4_graph_state() -> GraphStateSchema:
    return GraphStateSchema(
        task_id="p4-task",
        run_id="p4-run",
        task_type="scheme_generation",
        user_input="p4 target integration",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="p4 target integration"),
            task=TaskContextSchema(
                task_id="p4-task",
                run_id="p4-run",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-23T00:00:00+08:00",
    )


def _p4_graph_execution_state(
    coordinator: SectionExecutionCoordinator,
    evidence_bundle: EvidenceBundleSchema,
):
    graph = SectionExecutionGraph(coordinator=coordinator)
    shared_state = _p4_graph_state()
    project_input = ProjectInputSchema(
        task_id="p4-task",
        task_type="scheme_generation",
        user_query="p4 target integration",
        generation_requirements={
            "required_sections": ["P4 Section"],
            "need_citation": False,
            "citation_required_sections": [],
        },
        output_schema={
            "document_title": "P4 Document",
            "required_sections": ["P4 Section"],
        },
    )
    section_plan = SectionPlanSchema(
        section_id="p4-section",
        section_title="P4 Section",
        section_order=1,
        citation_required=False,
    )
    payload = SectionExecutionGraphPayloadSchema(
        task_id="p4-task",
        run_id="p4-run",
        document_id="p4-document",
        project_input=project_input,
        section_plan=section_plan,
        shared_state_payload=graph.codec.dump_graph_state(shared_state),
        section_id="p4-section",
        section_title="P4 Section",
        section_order=1,
        section_state=SectionStateSchema(
            section_id="p4-section",
            section_title="P4 Section",
            section_order=1,
            attempt=AttemptStateSchema(
                attempt_id="p4-write-instance",
                attempt_number=1,
                execution_id="p4-execution-1",
                plan_revision=1,
                input_snapshot_version="p4-input-v1",
            ),
        ),
        document_evidence_bundle=evidence_bundle,
        active_evidence_bundle=evidence_bundle,
    )
    return graph, graph.codec.build_initial_state(payload)


def _p4_coordinator_request(
    evidence_bundle: EvidenceBundleSchema,
) -> SectionExecutionRequestSchema:
    context, chunks, citations = project_evidence_bundle(evidence_bundle)
    return SectionExecutionRequestSchema(
        shared_state=_p4_graph_state(),
        document_id="p4-document",
        project_input=ProjectInputSchema(
            task_id="p4-task",
            task_type="scheme_generation",
            user_query="p4 target integration",
            generation_requirements={
                "required_sections": ["P4 Section"],
                "need_citation": False,
                "citation_required_sections": [],
            },
            output_schema={
                "document_title": "P4 Document",
                "required_sections": ["P4 Section"],
            },
        ),
        section_plan=SectionPlanSchema(
            section_id="p4-section",
            section_title="P4 Section",
            section_order=1,
            citation_required=False,
        ),
        document_evidence_bundle=evidence_bundle,
        document_rag_context=context,
        document_retrieved_chunks=list(chunks),
        document_citations=list(citations),
        document_rag_output={},
        section_retrieval_enabled=False,
    )


def _p4_apply(graph: SectionExecutionGraph, state: dict, method_name: str) -> dict:
    return {**state, **getattr(graph, method_name)(state)}


def _p4_run_to_route(
    coordinator: SectionExecutionCoordinator,
    evidence_bundle: EvidenceBundleSchema,
):
    graph, state = _p4_graph_execution_state(coordinator, evidence_bundle)
    for method_name in (
        "_evaluate_pre_write_admission",
        "_bind_pre_write_gate",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
    ):
        state = _p4_apply(graph, state, method_name)
    return graph, state


def test_target_section_successful_empty_retrieval_replaces_old_document_evidence() -> None:
    document_evidence = _p4_bundle(selected=True)
    empty_section_evidence = _p4_bundle(selected=False)
    evidence_service = _P4RecoveryEvidenceService(empty_section_evidence)
    query_builder = _P4RecoveryQueryBuilder()
    writer = _P4WriterInvocation()
    coordinator = _p4_coordinator(
        writer=writer,
        recovery_rounds_limit=0,
        evidence_service=evidence_service,
        query_builder=query_builder,
    )
    graph, state = _p4_graph_execution_state(
        coordinator,
        document_evidence,
    )

    state = _p4_apply(graph, state, "_retrieve_section_evidence")
    after_retrieval = graph.codec.restore_payload(state)

    assert evidence_service.calls == 1
    assert after_retrieval.active_evidence_bundle == evidence_service.candidate
    assert after_retrieval.active_evidence_bundle != document_evidence
    assert after_retrieval.active_context.context_text == ""
    assert after_retrieval.active_chunks == []
    assert after_retrieval.active_citations == []
    assert evidence_bundle_sha256(
        after_retrieval.active_evidence_bundle
    ) != evidence_bundle_sha256(document_evidence)

    state = _p4_apply(graph, state, "_evaluate_pre_write_admission")
    after_admission = graph.codec.restore_payload(state)
    assert after_admission.active_writer_admission_decision.admission_outcome.value == "deny"
    assert (
        after_admission.active_writer_admission_fact_assessment
        .criterion_facts[0]
        .support_state.value
        == "not_supported"
    )


def test_target_coordinator_execute_origin_converges_once() -> None:
    writer = _P4WriterInvocation()
    coordinator = _p4_coordinator(writer=writer, recovery_rounds_limit=2)

    result = coordinator.execute(
        _p4_coordinator_request(_p4_bundle(selected=True)),
        citation_registry=DocumentCitationRegistry(),
    )

    assert result.section.status is ExecutionStatus.SUCCESS
    assert writer.routes == [PreWriteRoute.NORMAL]


def test_target_e2e_normal_allow_to_normal_write_success() -> None:
    writer = _P4WriterInvocation()
    coordinator = _p4_coordinator(writer=writer, recovery_rounds_limit=2)
    graph, state = _p4_run_to_route(
        coordinator,
        _p4_bundle(selected=True),
    )
    before_writer = graph.codec.restore_payload(state)

    assert before_writer.current_pre_write_route_decision.selected_route is PreWriteRoute.NORMAL
    assert before_writer.pending_writer_generation_invocation is not None
    assert writer.routes == []

    state = _p4_apply(graph, state, "_invoke_pre_write_writer")
    completed = graph.codec.restore_payload(state)

    assert completed.section.status is ExecutionStatus.SUCCESS
    assert writer.routes == [PreWriteRoute.NORMAL]


def test_target_e2e_deny_recovery_progress_to_allow_then_normal(
    tmp_path: Path,
) -> None:
    writer = _P4WriterInvocation()
    evidence_service = _P4RecoveryEvidenceService(_p4_bundle(selected=True))
    query_builder = _P4RecoveryQueryBuilder()
    controller = PreWriteRecoveryController(
        execution_store=SQLitePreWriteExecutionStore(
            tmp_path / "p4-execution-control.sqlite3"
        )
    )
    coordinator = _p4_coordinator(
        writer=writer,
        recovery_rounds_limit=2,
        recovery_controller=controller,
        evidence_service=evidence_service,
        query_builder=query_builder,
    )
    graph, state = _p4_run_to_route(
        coordinator,
        _p4_bundle(selected=False),
    )
    recovery = graph.codec.restore_payload(state)

    assert recovery.current_pre_write_route_decision.selected_route is PreWriteRoute.RECOVERY
    assert writer.routes == []
    assert evidence_service.calls == 0

    state = _p4_apply(graph, state, "_dispatch_pre_write_recovery")
    reserved = graph.codec.restore_payload(state)
    assert reserved.pending_recovery_dispatch["phase"] == "reserved"
    assert reserved.recovery_runtime_state.recovery_rounds_used == 1
    assert evidence_service.calls == 0

    state = _p4_apply(graph, state, "_execute_pre_write_recovery")
    candidate = graph.codec.restore_payload(state)
    assert candidate.pending_recovery_dispatch["phase"] == "candidate_durable"
    assert candidate.active_writer_admission_decision.admission_outcome.value == "allow"
    assert evidence_service.calls == 1
    assert query_builder.calls == 1
    assert writer.routes == []

    for method_name in (
        "_complete_pre_write_recovery_dispatch",
        "_evaluate_pre_write_enforcement",
        "_commit_pre_write_route",
    ):
        state = _p4_apply(graph, state, method_name)
    promoted = graph.codec.restore_payload(state)
    assert promoted.current_pre_write_route_decision.selected_route is PreWriteRoute.NORMAL
    assert writer.routes == []

    state = _p4_apply(graph, state, "_invoke_pre_write_writer")
    completed = graph.codec.restore_payload(state)
    assert completed.section.status is ExecutionStatus.SUCCESS
    assert evidence_service.calls == 1
    assert writer.routes == [PreWriteRoute.NORMAL]


def test_target_e2e_recovery_exhausted_to_fallback_artifact() -> None:
    writer = _P4WriterInvocation()
    coordinator = _p4_coordinator(writer=writer, recovery_rounds_limit=0)
    graph, state = _p4_run_to_route(
        coordinator,
        _p4_bundle(selected=False),
    )
    before_writer = graph.codec.restore_payload(state)

    assert before_writer.active_writer_admission_decision.admission_outcome.value == "deny"
    assert before_writer.current_recovery_control_facts.recovery_exhausted
    assert before_writer.current_pre_write_route_decision.selected_route is PreWriteRoute.FALLBACK

    state = _p4_apply(graph, state, "_invoke_pre_write_writer")
    completed = graph.codec.restore_payload(state)
    assert completed.section.content == "canonical Writer artifact"
    assert completed.active_writer_admission_decision.admission_outcome.value == "deny"
    assert writer.routes == [PreWriteRoute.FALLBACK]


def test_target_e2e_writer_technical_failure_produces_no_fake_artifact_or_fallback_success() -> None:
    writer = _P4WriterInvocation(technical_failure=True)
    coordinator = _p4_coordinator(writer=writer, recovery_rounds_limit=2)
    graph, state = _p4_run_to_route(
        coordinator,
        _p4_bundle(selected=True),
    )
    before_writer = graph.codec.restore_payload(state)

    with pytest.raises(PreWriteWriterExecutionFailed) as captured:
        _p4_apply(graph, state, "_invoke_pre_write_writer")

    assert captured.value.result.status is WriterExecutionStatus.TECHNICAL_FAILURE
    assert captured.value.result.artifact is None
    assert before_writer.section is None
    assert before_writer.current_pre_write_route_decision.selected_route is PreWriteRoute.NORMAL
    assert writer.routes == [PreWriteRoute.NORMAL]


def _invoke_actual_downstream_failure(
    downstream_failure: str,
    *,
    section: SchemeSectionSchema,
    tmp_path: Path,
) -> tuple[str, str]:
    if downstream_failure == "claim_audit":
        from apps.enterprise_document.services.scheme_writer.writer_governance import (
            WriterClaimAuditor,
        )
        from stage_e_integrity_test_fixtures import make_binding

        audit = WriterClaimAuditor().audit(
            "本项目预算为100万元。",
            binding=make_binding(allowed=["general_method"]),
        )
        assert not audit.passed
        return "WriterClaimAuditor.audit", audit.violations[0].violation_code

    if downstream_failure == "citation_validation":
        from apps.enterprise_document.services.scheme_writer.citation_integrity_service import (
            CitationOccurrenceIntegrityGate,
        )

        failing_section = section.model_copy(
            update={"content": "实际下游引用完整性检查 [C-MISSING]。"},
            deep=True,
        )
        result = CitationOccurrenceIntegrityGate().evaluate(
            sections=[failing_section],
            bindings=[],
            full_text=failing_section.content,
        )
        assert not result.passed
        return (
            "CitationOccurrenceIntegrityGate.evaluate",
            "citation_marker_binding_bijection",
        )

    if downstream_failure == "document_gate":
        from stage_e_integrity_test_fixtures import evaluate

        hard_gate = evaluate(section)
        assert not hard_gate.passed
        return "evaluate_scheme_draft", hard_gate.failures[0]

    if downstream_failure == "release_publication":
        from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
            FileArtifactStore,
        )
        from apps.enterprise_document.services.release_publication import (
            ReleaseEligibilityError,
            ReleasePublicationService,
        )
        from test_stage_f_release_publication import (
            PUBLISHED_AT,
            VERSION,
            _artifact,
            _report,
            _version,
        )

        draft, hard_gate = _artifact()
        failed_gate = hard_gate.model_copy(
            update={"passed": False, "failures": ["injected_document_gate_failure"]},
            deep=True,
        )
        service = ReleasePublicationService(
            FileArtifactStore(tmp_path / "release-publication")
        )
        with pytest.raises(ReleaseEligibilityError, match="hard gate must pass"):
            service.publish(
                task_id=draft.task_id,
                run_id=draft.run_id,
                document_version=VERSION,
                draft=draft,
                output_format="markdown",
                hard_gate=failed_gate,
                generation_report=_report(),
                version_decision=_version(),
                output_status=ExecutionStatus.FAILED,
                published_at=PUBLISHED_AT,
            )
        return "ReleasePublicationService.publish", "ReleaseEligibilityError"

    raise AssertionError(f"unknown downstream failure: {downstream_failure}")


@pytest.mark.parametrize(
    "downstream_failure",
    (
        "claim_audit",
        "citation_validation",
        "document_gate",
        "release_publication",
    ),
)
def test_no_downstream_re_evaluation_and_pre_post_authority_separation(
    downstream_failure: str,
    tmp_path: Path,
) -> None:
    writer = _P4WriterInvocation()
    coordinator = _p4_coordinator(writer=writer, recovery_rounds_limit=2)
    graph, state = _p4_run_to_route(
        coordinator,
        _p4_bundle(selected=True),
    )
    state = _p4_apply(graph, state, "_invoke_pre_write_writer")
    producer_counts_after_writer = _p4_pre_write_producer_counts(coordinator)
    pre_write = graph.codec.restore_payload(state)
    canonical_before = (
        evidence_bundle_sha256(pre_write.active_evidence_bundle),
        pre_write.active_evidence_requirements_snapshot.requirements_sha256,
        pre_write.active_writer_admission_fact_assessment.assessment_id,
        pre_write.active_writer_admission_decision.decision_id,
        pre_write.active_writer_gate_binding.binding_id,
        pre_write.current_enforcement_result,
        pre_write.current_recovery_control_facts,
        pre_write.current_pre_write_route_decision.route_decision_id,
    )

    state = _p4_apply(graph, state, "_prepare_paragraph_pipeline")
    for _ in range(8):
        route = state["section_route"]
        if route == "persist_paragraph":
            state = _p4_apply(graph, state, "_persist_paragraph")
        elif route == "precheck_paragraph":
            state = _p4_apply(graph, state, "_precheck_paragraph")
        else:
            break
    assert state["section_route"] == "evaluate_generation"
    state = _p4_apply(graph, state, "_evaluate_generation")
    assert state["section_route"] == "finalize"
    state = _p4_apply(graph, state, "_finalize_section")
    post_write = graph.codec.restore_payload(state)
    canonical_after = (
        evidence_bundle_sha256(post_write.active_evidence_bundle),
        post_write.active_evidence_requirements_snapshot.requirements_sha256,
        post_write.active_writer_admission_fact_assessment.assessment_id,
        post_write.active_writer_admission_decision.decision_id,
        post_write.active_writer_gate_binding.binding_id,
        post_write.current_enforcement_result,
        post_write.current_recovery_control_facts,
        post_write.current_pre_write_route_decision.route_decision_id,
    )
    assert canonical_after == canonical_before
    writer_routes_after_writer = list(writer.routes)
    assert writer_routes_after_writer == [PreWriteRoute.NORMAL]

    owner, injected_failure = _invoke_actual_downstream_failure(
        downstream_failure,
        section=post_write.section,
        tmp_path=tmp_path,
    )
    assert owner
    assert injected_failure
    assert _p4_pre_write_producer_counts(coordinator) == producer_counts_after_writer
    assert writer.routes == writer_routes_after_writer
    after_failure = graph.codec.restore_payload(state)
    assert (
        evidence_bundle_sha256(after_failure.active_evidence_bundle),
        after_failure.active_evidence_requirements_snapshot.requirements_sha256,
        after_failure.active_writer_admission_fact_assessment.assessment_id,
        after_failure.active_writer_admission_decision.decision_id,
        after_failure.active_writer_gate_binding.binding_id,
        after_failure.current_enforcement_result,
        after_failure.current_recovery_control_facts,
        after_failure.current_pre_write_route_decision.route_decision_id,
    ) == canonical_before

    initial = inspect.getsource(SectionExecutionGraph._invoke_pre_write_writer)
    mixed = inspect.getsource(SectionExecutionGraph._generate_section)
    assert "invoke_pre_write_writer(" in initial
    assert "generate_section(" not in initial
    assert "initial Graph Writer occurrence must use invoke_pre_write_writer" in mixed
    assert mixed.count(".generate_section(") == 1

    recovery = inspect.getsource(SectionExecutionGraph._retrieve_recovery)
    finalization = inspect.getsource(SectionExecutionGraph._finalize_section)
    assert recovery.count("not self._pre_write_ready") == 2
    assert "not self._pre_write_ready" in finalization

    forbidden = (
        "evaluate_pre_write_admission",
        "bind_pre_write_gate",
        "evaluate_pre_write_enforcement",
        "commit_pre_write_route",
    )
    for method_name in (
        "_prepare_paragraph_pipeline",
        "_persist_paragraph",
        "_precheck_paragraph",
        "_evaluate_generation",
        "_retrieve_recovery",
        "_retrieve_more",
        "_corrective_retrieval",
        "_finalize_section",
    ):
        source = inspect.getsource(getattr(SectionExecutionGraph, method_name))
        assert all(value not in source for value in forbidden)
