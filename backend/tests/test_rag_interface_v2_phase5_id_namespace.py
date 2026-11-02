from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.tests.test_rag_interface_v2_phase4b_canonical_carrier import (
    _item_assessment,
)
from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.quality.budget import WorkflowBudget
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    NormalizedToolResultSchema,
    ResearchEvidencePackageSchema,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    ResearchStatus,
    ToolProvenanceSchema,
    ToolResultStatus,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer_schema import (
    ResearchProjectionStatus,
    SectionLifecycleStatus,
    SectionPlanSchema,
    SectionStateSchema,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    MainlineResearchAgentFailure,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    BoundResearchToolRequest,
    MainlineResearchRetrieval,
    MainlineResearchToolGateway,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_reasoners import (
    MainlineResearchReasoner,
)
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from apps.enterprise_document.services.scheme_writer.evidence_projection import (
    bundle_first_assessment_projection,
)
from apps.enterprise_document.workflows.section_execution_graph import SectionExecutionGraph
from contracts.evidence import (
    build_evidence_bundle,
    evidence_bundle_sha256,
    project_evidence_bundle,
)
from schemas.citation import CitationSchema
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceDisposition,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RetrievedChunkSchema,
)

RAGEvidenceContractBuilder = SimpleNamespace(build=build_evidence_bundle)
RAGEvidenceContractReader = SimpleNamespace(projections=project_evidence_bundle)


def _chunk(marker: str, *, rank: int = 1) -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=rank,
        matched_chunk_id=f"matched_{marker}",
        context_chunk_id=f"context_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        doc_id=f"doc_{marker}",
        match_text=f"match {marker}",
        context_text=f"canonical evidence {marker}",
        title=f"Evidence {marker}",
    )


def _citation(marker: str, *, citation_id: str = "C99") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        source_document_id=f"doc_{marker}",
        doc_id=f"doc_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        quote_text=f"canonical evidence {marker}",
        title=f"Evidence {marker}",
    )


def _bundle(
    marker: str,
    *,
    scope: str = "section",
    include_citation: bool = True,
    include_dropped: bool = True,
    selected_id: str | None = None,
    dropped_id: str | None = None,
    status: EvidenceStatus = EvidenceStatus.SUFFICIENT,
) -> EvidenceBundleSchema:
    selected_chunk = _chunk(f"{marker}_selected")
    dropped_chunk = _chunk(f"{marker}_dropped", rank=2)
    bundle = RAGEvidenceContractBuilder.build(
        query=f"query {marker}",
        rewritten_queries=[],
        selected_chunks=[selected_chunk],
        dropped_chunks=[dropped_chunk] if include_dropped else [],
        citations=(
            [_citation(f"{marker}_selected", citation_id="C99")]
            if include_citation
            else []
        ),
        trace=None,
        max_context_chars=6000,
    )
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=status,
        coverage_ratio=1.0 if status is EvidenceStatus.SUFFICIENT else 0.0,
        required_topics=[f"query {marker}"],
        covered_topics=(
            [f"query {marker}"] if status is EvidenceStatus.SUFFICIENT else []
        ),
        missing_topics=(
            [f"query {marker}"] if status is EvidenceStatus.INSUFFICIENT else []
        ),
        authoritative_evidence_present=(status is EvidenceStatus.SUFFICIENT),
        prompt_evidence_source="phase5_id_namespace_test",
        reason_codes=[
            "coverage_sufficient"
            if status is EvidenceStatus.SUFFICIENT
            else "coverage_insufficient"
        ],
    )
    data = bundle.model_dump(mode="python", round_trip=True)
    data.update(
        {
            "task_id": "task_phase5",
            "run_id": "run_phase5",
            "retrieval_trace_id": f"trace_{marker}",
            "retrieval_scope": scope,
            "assessment": assessment.model_dump(mode="python", round_trip=True),
        }
    )
    if selected_id is not None:
        old_selected = list(data["selected_evidence_ids"])
        assert len(old_selected) == 1
        old_id = old_selected[0]
        data["selected_evidence_ids"] = [selected_id]
        for item in data["items"]:
            if item["evidence_id"] == old_id:
                item["evidence_id"] = selected_id
    if dropped_id is not None and data["dropped_evidence_ids"]:
        old_dropped = list(data["dropped_evidence_ids"])
        assert len(old_dropped) == 1
        old_id = old_dropped[0]
        data["dropped_evidence_ids"] = [dropped_id]
        for item in data["items"]:
            if item["evidence_id"] == old_id:
                item["evidence_id"] = dropped_id
    data["item_assessments"] = [
        _item_assessment(
            evidence_id=data["selected_evidence_ids"][0],
            document_id=selected_chunk.doc_id,
            status=status,
        ).model_dump(mode="python", round_trip=True)
    ]
    return EvidenceBundleSchema.model_validate(data)


def _project_input() -> ProjectInputSchema:
    return ProjectInputSchema(
        task_id="task_phase5",
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={
            "required_sections": ["技术方案"],
            "need_citation": True,
            "citation_required_sections": ["技术方案"],
        },
        output_schema={
            "document_title": "建设方案",
            "required_sections": ["技术方案"],
        },
    )


def _shared_state() -> GraphStateSchema:
    return GraphStateSchema(
        task_id="task_phase5",
        run_id="run_phase5",
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id="task_phase5",
                run_id="run_phase5",
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-15T19:31:00+08:00",
    )


def _gateway_context() -> AgentExecutionContextSchema:
    return AgentExecutionContextSchema(
        invocation_id="inv_phase5_gateway",
        logical_operation_id="op_phase5_gateway",
        task_id="task_phase5",
        run_id="run_phase5",
        section_id="section_phase5_001",
        agent_role=AgentRole.RESEARCH,
        execution_mode="mainline",
        execution_id="exec_phase5_gateway",
        attempt_id="attempt_phase5_gateway",
        input_snapshot_version="snapshot_phase5_gateway",
        task_context_ref="state://task/task_phase5",
        source_snapshot_ref="state://source/task_phase5",
        section_state_ref="state://section/section_phase5_001",
        authorized_tool_names=[MainlineResearchToolGateway.tool_name],
    )


class _GatewayEvidenceService:
    def __init__(self, bundle: EvidenceBundleSchema) -> None:
        self.bundle = bundle

    def retrieve(self, *args, **kwargs):
        del args, kwargs
        return self.bundle

    def extract_rag_output(self, shared_state, result):
        del shared_state
        context, chunks, citations = RAGEvidenceContractReader.projections(result)
        return (
            context,
            chunks,
            citations,
            {
                "context": context.model_dump(mode="json"),
                "retrieved_chunks": [item.model_dump(mode="json") for item in chunks],
                "citations": [item.model_dump(mode="json") for item in citations],
            },
        )


def _gateway_result(bundle: EvidenceBundleSchema):
    gateway = MainlineResearchToolGateway(
        evidence_service=_GatewayEvidenceService(bundle)  # type: ignore[arg-type]
    )
    request = BoundResearchToolRequest(
        shared_state=_shared_state(),
        project_input=_project_input(),
        section_id="section_phase5_001",
        section_title="技术方案",
        query=bundle.query,
        scope=bundle.retrieval_scope,
        call_suffix="phase5",
        citation_registry=[],
    )
    result = gateway.invoke_bound(
        _gateway_context(),
        request,
        call_id="call_phase5_gateway",
        tool_name=MainlineResearchToolGateway.tool_name,
        arguments={
            "query": bundle.query,
            "scope": bundle.retrieval_scope,
            "section_id": "section_phase5_001",
        },
    )
    return result, request


def _section_plan() -> SectionPlanSchema:
    return SectionPlanSchema(
        section_id="section_phase5_001",
        section_title="技术方案",
        section_order=1,
        citation_required=True,
    )


def _graph_payload(bundle: EvidenceBundleSchema) -> SectionExecutionGraphPayloadSchema:
    context, chunks, citations = RAGEvidenceContractReader.projections(bundle)
    section_state = SectionStateSchema(
        section_id="section_phase5_001",
        section_title="技术方案",
        section_order=1,
        lifecycle_status=SectionLifecycleStatus.RESEARCHING,
    )
    section_state = section_state.model_copy(
        update={
            "research": section_state.research.model_copy(
                update={
                    "status": ResearchProjectionStatus.COMPLETED,
                    "evidence_package_ref": (
                        "state://section-evidence/section_phase5_001/current"
                    ),
                    "evidence_contract_sha256": evidence_bundle_sha256(bundle),
                    "accepted_evidence_ids": list(bundle.selected_evidence_ids),
                    "rejected_evidence_ids": [],
                    "research_rounds": 1,
                    "recommended_action": ResearchRecommendedAction.WRITE.value,
                },
                deep=True,
            )
        },
        deep=True,
    )
    return SectionExecutionGraphPayloadSchema(
        task_id="task_phase5",
        run_id="run_phase5",
        document_id="document_phase5",
        project_input=_project_input(),
        section_plan=_section_plan(),
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            _shared_state()
        ),
        document_evidence_bundle=bundle,
        section_id="section_phase5_001",
        section_title="技术方案",
        section_order=1,
        total_sections=1,
        citation_required=True,
        section_state=section_state,
        citation_registry=list(citations),
        active_evidence_bundle=bundle,
        active_context=context,
        active_chunks=list(chunks),
        active_citations=list(citations),
        active_query=bundle.query,
        active_scope=bundle.retrieval_scope,
        budget_state=WorkflowBudget.from_policy_metadata({}).snapshot(),
    )


class _CapturingResearchAdapter:
    uses_research_tool_gateway = True

    def __init__(
        self,
        *,
        result_bundle: EvidenceBundleSchema | None,
        accepted_ids: list[str] | None = None,
        contract_sha: str | None = None,
        state_ids: list[str] | None = None,
        state_sha: str | None = None,
        rejected_ids: list[str] | None = None,
        retrieval_failed: bool = False,
    ) -> None:
        self.result_bundle = result_bundle
        self.accepted_ids = accepted_ids
        self.contract_sha = contract_sha
        self.state_ids = state_ids
        self.state_sha = state_sha
        self.rejected_ids = list(rejected_ids or [])
        self.retrieval_failed = retrieval_failed
        self.kwargs: dict | None = None

    def research(self, **kwargs):
        self.kwargs = dict(kwargs)
        if self.result_bundle is None:
            result_ids = list(
                self.accepted_ids
                if self.accepted_ids is not None
                else kwargs["accepted_evidence_ids"]
            )
            result_sha = (
                self.contract_sha
                if self.contract_sha is not None
                else kwargs["evidence_contract_sha256"]
            )
            retrieval = (
                MainlineResearchRetrieval(
                    success=False,
                    evidence_bundle=None,
                    context=None,
                    chunks=(),
                    citations=(),
                    citation_registry=(),
                    normalized={},
                    assessment={"status": "not_assessed", "details": {}},
                    retrieval_trace_id="trace_phase5_failed",
                    error_message="retrieval_failed",
                )
                if self.retrieval_failed
                else None
            )
        else:
            result_ids = list(
                self.accepted_ids
                if self.accepted_ids is not None
                else self.result_bundle.selected_evidence_ids
            )
            result_sha = (
                self.contract_sha
                if self.contract_sha is not None
                else evidence_bundle_sha256(self.result_bundle)
            )
            context, chunks, citations = RAGEvidenceContractReader.projections(
                self.result_bundle
            )
            retrieval = MainlineResearchRetrieval(
                success=True,
                evidence_bundle=self.result_bundle,
                context=context,
                chunks=tuple(chunks),
                citations=tuple(citations),
                citation_registry=tuple(citations),
                normalized={
                    "context": context.model_dump(mode="json"),
                    "retrieved_chunks": [
                        item.model_dump(mode="json") for item in chunks
                    ],
                    "citations": [item.model_dump(mode="json") for item in citations],
                },
                assessment=bundle_first_assessment_projection(self.result_bundle),
                retrieval_trace_id=self.result_bundle.retrieval_trace_id,
            )
        rejected = [
            {"evidence_id": value, "reason_code": "phase5_test_rejected"}
            for value in self.rejected_ids
        ]
        payload = ResearchResultPayloadSchema(
            section_id=kwargs["section_id"],
            evidence_package_ref=(
                f"state://section-evidence/{kwargs['section_id']}/phase5"
            ),
            evidence_summary=ResearchEvidencePackageSchema(
                section_id=kwargs["section_id"],
                accepted_evidence_ids=result_ids,
                rejected_evidence=rejected,
                research_status=ResearchStatus.SUFFICIENT,
                recommended_action=ResearchRecommendedAction.WRITE,
            ),
            evidence_contract_sha256=result_sha,
            tool_call_ids=(
                [str(self.result_bundle.retrieval_trace_id)]
                if self.result_bundle is not None
                and self.result_bundle.retrieval_trace_id
                else list(kwargs["tool_call_ids"])
            ),
            research_round=kwargs["research_round"],
            research_mode=kwargs["research_mode"],
            agent_metadata={"invocation_id": "inv_phase5_test"},
        )
        section_state = kwargs["section_state"]
        projected_state_ids = list(
            self.state_ids if self.state_ids is not None else result_ids
        )
        projected_state_sha = (
            self.state_sha if self.state_sha is not None else result_sha
        )
        projected_research = section_state.research.model_copy(
            update={
                "status": ResearchProjectionStatus.COMPLETED,
                "evidence_package_ref": payload.evidence_package_ref,
                "evidence_contract_sha256": projected_state_sha,
                "accepted_evidence_ids": projected_state_ids,
                "rejected_evidence_ids": list(self.rejected_ids),
                "research_rounds": kwargs["research_round"],
                "tool_call_ids": list(payload.tool_call_ids),
                "recommended_action": ResearchRecommendedAction.WRITE.value,
            },
            deep=True,
        )
        projected_state = section_state.model_copy(
            update={
                "lifecycle_status": SectionLifecycleStatus.EVIDENCE_READY,
                "research": projected_research,
            },
            deep=True,
        )
        return SimpleNamespace(
            payload=payload,
            section_state=projected_state,
            result=SimpleNamespace(),
            retrieval=retrieval,
        )


class _GraphCoordinator:
    def __init__(self, adapter: _CapturingResearchAdapter) -> None:
        self.evidence_service = SimpleNamespace()
        self.query_builder = SimpleNamespace()
        self.section_generation_service = SimpleNamespace()
        self.runtime_support = SimpleNamespace()
        self.generation_quality_metadata = {}
        self.section_agent_adapter = adapter

    def _writer_gate_binding(self, *args, **kwargs):
        del args, kwargs
        return None

    @staticmethod
    def writer_gate_blocks(binding) -> bool:
        return binding is None


def _graph(adapter: _CapturingResearchAdapter) -> SectionExecutionGraph:
    graph = SectionExecutionGraph(coordinator=_GraphCoordinator(adapter))  # type: ignore[arg-type]
    graph.attempt_guard = SimpleNamespace(
        validate_result_metadata=lambda **kwargs: {"validated": True}
    )
    return graph


class _FailingResearchAdapter:
    def research(self, **kwargs):
        del kwargs
        raise MainlineResearchAgentFailure(
            error=SimpleNamespace(message="research failed"),  # type: ignore[arg-type]
            retrieval=None,
        )


class _FailedToolSession:
    def __init__(self) -> None:
        self.call_refs = ["tool://phase5-failed"]

    def call(self, tool_name: str, arguments: dict) -> NormalizedToolResultSchema:
        assert tool_name == MainlineResearchToolGateway.tool_name
        assert arguments["query"]
        return NormalizedToolResultSchema(
            call_id="tool://phase5-failed",
            status=ToolResultStatus.PARTIAL,
            data_type="research_retrieval",
            data={
                "retrieval_succeeded": False,
                "assessment": {"status": "not_assessed", "details": {}},
                "accepted_evidence_ids": [],
                "rejected_evidence_ids": [],
                "tool_call_ids": ["trace_phase5_failed"],
                "retrieval_scope": "recovery",
                "error_message": "retrieval_failed",
            },
            provenance=ToolProvenanceSchema(
                input_snapshot_version="snapshot_phase5_failed",
                tool_name=MainlineResearchToolGateway.tool_name,
                tool_version="phase5_test",
                input_fingerprint="phase5_failed",
            ),
        )


def _invoke_graph_research(
    graph: SectionExecutionGraph,
    payload: SectionExecutionGraphPayloadSchema,
    *,
    query: str = "",
    shared_state: GraphStateSchema | None = None,
    recovery_candidate: bool = False,
):
    return graph._invoke_research_agent(
        payload,
        node_name="phase5_test",
        research_mode="initial" if not recovery_candidate else "corrective_retrieval",
        retrieval_succeeded=not bool(query),
        retrieval_scope=("recovery" if recovery_candidate else payload.active_scope),
        shared_state=shared_state,
        query=query,
        call_suffix="phase5",
        citation_registry=list(payload.citation_registry),
        recovery_candidate=recovery_candidate,
    )


def test_gateway_accepted_ids_are_exact_canonical_selected_projection() -> None:
    bundle = _bundle("gateway", selected_id="E_GATEWAY", dropped_id="E_DROP")

    result, request = _gateway_result(bundle)

    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["accepted_evidence_ids"] == bundle.selected_evidence_ids
    assert request.execution is not None
    assert request.execution.evidence_bundle == bundle
    assert result.data["rejected_evidence_ids"] == []
    assert bundle.dropped_evidence_ids == ["E_DROP"]
    assert result.provenance.source_refs == []


def test_citation_namespace_never_enters_accepted_evidence_ids() -> None:
    bundle = _bundle("citation", selected_id="E_CITATION")

    result, request = _gateway_result(bundle)

    assert request.execution is not None
    citation_ids = {item.citation_id for item in request.execution.citations}
    assert citation_ids
    assert not citation_ids & set(result.data["accepted_evidence_ids"])
    assert result.data["accepted_evidence_ids"] == ["E_CITATION"]


def test_chunk_namespaces_never_enter_accepted_evidence_ids() -> None:
    bundle = _bundle(
        "chunks",
        include_citation=False,
        selected_id="E_CHUNK",
        dropped_id="E_DROP_CHUNK",
    )

    result, request = _gateway_result(bundle)

    assert request.execution is not None
    chunks = request.execution.chunks
    assert chunks
    chunk_namespace_ids = {
        value
        for item in chunks
        for value in (
            item.matched_chunk_id,
            item.context_chunk_id,
            item.child_chunk_id,
            item.parent_chunk_id,
            str(getattr(item, "chunk_id", "") or ""),
        )
        if value
    }
    assert not chunk_namespace_ids & set(result.data["accepted_evidence_ids"])
    assert result.data["accepted_evidence_ids"] == ["E_CHUNK"]


def test_section_graph_current_ids_come_from_typed_active_bundle_only() -> None:
    bundle = _bundle("active", selected_id="E_ACTIVE")
    payload = _graph_payload(bundle)
    payload = payload.model_copy(
        update={
            "active_citations": [_citation("pollution", citation_id="C_WRONG")],
            "active_chunks": [_chunk("pollution")],
        },
        deep=True,
    )

    assert SectionExecutionGraph._active_evidence_ids(payload) == ["E_ACTIVE"]


def test_selected_evidence_ids_must_be_ordered_unique() -> None:
    bundle = _bundle("selected_unique", selected_id="E1", include_dropped=False)
    data = bundle.model_dump(mode="python", round_trip=True)
    data["selected_evidence_ids"] = ["E1", "E1"]
    data["context"]["context_item_count"] = 2

    with pytest.raises(ValueError, match="selected_evidence_ids.*ordered-unique"):
        EvidenceBundleSchema.model_validate(data)


def test_dropped_evidence_ids_must_be_ordered_unique() -> None:
    bundle = _bundle("dropped_unique", selected_id="E1", dropped_id="E2")
    data = bundle.model_dump(mode="python", round_trip=True)
    data["dropped_evidence_ids"] = ["E2", "E2"]

    with pytest.raises(ValueError, match="dropped_evidence_ids.*ordered-unique"):
        EvidenceBundleSchema.model_validate(data)


def test_precomputed_path_exact_ids_and_hash_propagate_to_result_and_state() -> None:
    bundle_a = _bundle("precomputed_a", selected_id="E_A")
    adapter = _CapturingResearchAdapter(result_bundle=None)
    graph = _graph(adapter)
    payload = _graph_payload(bundle_a)

    updated = _invoke_graph_research(graph, payload)

    assert adapter.kwargs is not None
    assert adapter.kwargs["accepted_evidence_ids"] == bundle_a.selected_evidence_ids
    assert adapter.kwargs["rejected_evidence_ids"] == []
    assert adapter.kwargs["evidence_contract_sha256"] == evidence_bundle_sha256(bundle_a)
    research_payload = updated.research_agent_payloads[-1]
    assert research_payload.evidence_summary.accepted_evidence_ids == bundle_a.selected_evidence_ids
    assert research_payload.evidence_summary.rejected_evidence == []
    assert research_payload.evidence_contract_sha256 == evidence_bundle_sha256(bundle_a)
    assert updated.section_state is not None
    assert updated.section_state.research.accepted_evidence_ids == bundle_a.selected_evidence_ids
    assert updated.section_state.research.rejected_evidence_ids == []
    assert updated.section_state.research.evidence_contract_sha256 == evidence_bundle_sha256(bundle_a)


def test_tool_path_keeps_invocation_snapshot_a_but_projects_bundle_b_downstream() -> None:
    bundle_a = _bundle("tool_a", selected_id="E_A", scope="section")
    bundle_b = _bundle("tool_b", selected_id="E_B", scope="recovery")
    adapter = _CapturingResearchAdapter(result_bundle=bundle_b)
    graph = _graph(adapter)
    payload = _graph_payload(bundle_a)

    updated = _invoke_graph_research(
        graph,
        payload,
        query=bundle_b.query,
        shared_state=_shared_state(),
    )

    assert adapter.kwargs is not None
    assert adapter.kwargs["accepted_evidence_ids"] == ["E_A"]
    assert adapter.kwargs["evidence_contract_sha256"] == evidence_bundle_sha256(bundle_a)
    research_payload = updated.research_agent_payloads[-1]
    assert research_payload.evidence_summary.accepted_evidence_ids == ["E_B"]
    assert research_payload.evidence_contract_sha256 == evidence_bundle_sha256(bundle_b)
    assert updated.section_state is not None
    assert updated.section_state.research.accepted_evidence_ids == ["E_B"]
    assert updated.section_state.research.evidence_contract_sha256 == evidence_bundle_sha256(bundle_b)
    assert "E_A" not in research_payload.evidence_summary.accepted_evidence_ids
    assert "E_A" not in updated.section_state.research.accepted_evidence_ids


def test_graph_commits_canonical_technical_failure_before_propagating_agent_error() -> None:
    graph = _graph(_FailingResearchAdapter())  # type: ignore[arg-type]
    payload = _graph_payload(_bundle("graph_failure", selected_id="E_A"))
    calls: list[object] = []

    def _record_failure(candidate, *, retrieval):
        calls.append((candidate, retrieval))
        return {"current_research_round_binding": "committed-failure-round"}

    graph._commit_canonical_research_failure = _record_failure  # type: ignore[method-assign]

    with pytest.raises(MainlineResearchAgentFailure):
        _invoke_graph_research(graph, payload)

    assert len(calls) == 1
    assert calls[0][0] is payload
    assert calls[0][1] is None


def test_failed_tool_retrieval_reasoner_fails_closed_without_reusing_prior_bundle() -> None:
    bundle_a = _bundle("failed_reasoner_a", selected_id="E_A", scope="section")
    invocation = ResearchInvocationInputSchema(
        section_id="section_phase5_001",
        section_task_ref="state://section/section_phase5_001",
        fact_snapshot_ref="state://facts/task_phase5",
        source_inventory_ref="state://sources/task_phase5",
        evidence_topics=["技术方案"],
        research_mode="corrective_retrieval",
        retrieval_scope="recovery",
        research_round=2,
        query="failed retrieval query",
        section_title="技术方案",
        citation_required=True,
        evidence_assessment=bundle_first_assessment_projection(bundle_a),
        accepted_evidence_ids=list(bundle_a.selected_evidence_ids),
        rejected_evidence_ids=[],
        evidence_contract_sha256=evidence_bundle_sha256(bundle_a),
        retrieval_succeeded=True,
    )

    with pytest.raises(
        RuntimeError,
        match="research retrieval technical failure: retrieval_failed",
    ):
        MainlineResearchReasoner().invoke(
            context=_gateway_context(),
            invocation_input=invocation,
            tools=_FailedToolSession(),
        )


def test_failed_tool_retrieval_wrong_ids_hard_fail() -> None:
    bundle_a = _bundle("failed_guard_id_a", selected_id="E_A")
    adapter = _CapturingResearchAdapter(
        result_bundle=None,
        accepted_ids=["E_WRONG"],
        state_ids=["E_WRONG"],
        retrieval_failed=True,
    )
    graph = _graph(adapter)

    with pytest.raises(ValueError, match="Research Evidence ID projection mismatch"):
        _invoke_graph_research(
            graph,
            _graph_payload(bundle_a),
            query="failed retrieval query",
            shared_state=_shared_state(),
        )


def test_failed_tool_retrieval_wrong_hash_hard_fail() -> None:
    bundle_a = _bundle("failed_guard_hash_a", selected_id="E_A")
    adapter = _CapturingResearchAdapter(
        result_bundle=None,
        contract_sha="sha256:wrong",
        state_sha="sha256:wrong",
        retrieval_failed=True,
    )
    graph = _graph(adapter)

    with pytest.raises(ValueError, match="Research Evidence contract hash mismatch"):
        _invoke_graph_research(
            graph,
            _graph_payload(bundle_a),
            query="failed retrieval query",
            shared_state=_shared_state(),
        )


def test_failed_tool_retrieval_cannot_mutate_active_bundle_a_identity() -> None:
    bundle_a = _bundle("failed_active_a", selected_id="E_A")
    hash_a = evidence_bundle_sha256(bundle_a)
    adapter = _CapturingResearchAdapter(
        result_bundle=None,
        retrieval_failed=True,
    )
    graph = _graph(adapter)
    payload = _graph_payload(bundle_a)

    updated = _invoke_graph_research(
        graph,
        payload,
        query="failed retrieval query",
        shared_state=_shared_state(),
    )

    assert updated.active_evidence_bundle == bundle_a
    assert SectionExecutionGraph._active_evidence_ids(updated) == ["E_A"]
    assert SectionExecutionGraph._evidence_contract_sha(updated) == hash_a
    assert updated.section_state is not None
    assert updated.section_state.research.accepted_evidence_ids == ["E_A"]
    assert updated.section_state.research.evidence_contract_sha256 == hash_a
    assert updated.retrieval_metadata["research_tool_retrieval_success"] is False
    assert updated.retrieval_metadata["research_tool_error"] == "retrieval_failed"


def test_runtime_guard_rejects_accepted_id_mismatch() -> None:
    bundle_a = _bundle("guard_id_a", selected_id="E_A")
    bundle_b = _bundle("guard_id_b", selected_id="E_B", scope="recovery")
    adapter = _CapturingResearchAdapter(
        result_bundle=bundle_b,
        accepted_ids=["E_WRONG"],
        state_ids=["E_WRONG"],
    )
    graph = _graph(adapter)

    with pytest.raises(ValueError, match="Research Evidence ID projection mismatch"):
        _invoke_graph_research(
            graph,
            _graph_payload(bundle_a),
            query=bundle_b.query,
            shared_state=_shared_state(),
        )


def test_runtime_guard_rejects_bundle_hash_mismatch() -> None:
    bundle_a = _bundle("guard_hash_a", selected_id="E_A")
    bundle_b = _bundle("guard_hash_b", selected_id="E_B", scope="recovery")
    adapter = _CapturingResearchAdapter(
        result_bundle=bundle_b,
        contract_sha="sha256:wrong",
        state_sha="sha256:wrong",
    )
    graph = _graph(adapter)

    with pytest.raises(ValueError, match="Research Evidence contract hash mismatch"):
        _invoke_graph_research(
            graph,
            _graph_payload(bundle_a),
            query=bundle_b.query,
            shared_state=_shared_state(),
        )


def test_runtime_guard_rejects_research_owned_rejected_ids() -> None:
    bundle_a = _bundle("guard_rejected", selected_id="E_A", dropped_id="E_DROP")
    adapter = _CapturingResearchAdapter(
        result_bundle=None,
        rejected_ids=["E_DROP"],
    )
    graph = _graph(adapter)

    with pytest.raises(ValueError, match="Research rejected_evidence_ids must remain empty"):
        _invoke_graph_research(graph, _graph_payload(bundle_a))

    assert bundle_a.dropped_evidence_ids == ["E_DROP"]


def test_bundle_local_same_evidence_id_is_disambiguated_by_bundle_hash() -> None:
    bundle_a = _bundle("same_id_a", selected_id="E1")
    bundle_b = _bundle("same_id_b", selected_id="E1", scope="recovery")
    hash_a = evidence_bundle_sha256(bundle_a)
    hash_b = evidence_bundle_sha256(bundle_b)
    adapter = _CapturingResearchAdapter(result_bundle=bundle_b)
    graph = _graph(adapter)

    updated = _invoke_graph_research(
        graph,
        _graph_payload(bundle_a),
        query=bundle_b.query,
        shared_state=_shared_state(),
    )

    assert bundle_a.selected_evidence_ids == bundle_b.selected_evidence_ids == ["E1"]
    assert hash_a != hash_b
    assert adapter.kwargs is not None
    assert adapter.kwargs["accepted_evidence_ids"] == ["E1"]
    assert adapter.kwargs["evidence_contract_sha256"] == hash_a
    research_payload = updated.research_agent_payloads[-1]
    assert research_payload.evidence_summary.accepted_evidence_ids == ["E1"]
    assert research_payload.evidence_contract_sha256 == hash_b
    assert updated.section_state is not None
    assert updated.section_state.research.accepted_evidence_ids == ["E1"]
    assert updated.section_state.research.evidence_contract_sha256 == hash_b


def test_recovery_reject_retains_active_a_ids_and_hash() -> None:
    bundle_a = _bundle("recovery_reject_a", selected_id="E_A")
    bundle_b = _bundle("recovery_reject_b", selected_id="E_B", scope="recovery")
    adapter = _CapturingResearchAdapter(result_bundle=bundle_b)
    graph = _graph(adapter)
    graph._recovery_candidate_decision = lambda **kwargs: (
        False,
        True,
        False,
        "phase5_rejected",
    )

    updated = _invoke_graph_research(
        graph,
        _graph_payload(bundle_a),
        query=bundle_b.query,
        shared_state=_shared_state(),
        recovery_candidate=True,
    )

    assert updated.active_evidence_bundle == bundle_a
    assert updated.section_state is not None
    assert updated.section_state.research.accepted_evidence_ids == bundle_a.selected_evidence_ids
    assert updated.section_state.research.evidence_contract_sha256 == evidence_bundle_sha256(bundle_a)


def test_recovery_accept_promotes_active_b_ids_and_hash() -> None:
    bundle_a = _bundle("recovery_accept_a", selected_id="E_A")
    bundle_b = _bundle("recovery_accept_b", selected_id="E_B", scope="recovery")
    adapter = _CapturingResearchAdapter(result_bundle=bundle_b)
    graph = _graph(adapter)
    graph._recovery_candidate_decision = lambda **kwargs: (
        True,
        False,
        True,
        None,
    )

    updated = _invoke_graph_research(
        graph,
        _graph_payload(bundle_a),
        query=bundle_b.query,
        shared_state=_shared_state(),
        recovery_candidate=True,
    )

    assert updated.active_evidence_bundle == bundle_b
    assert updated.section_state is not None
    assert updated.section_state.research.accepted_evidence_ids == bundle_b.selected_evidence_ids
    assert updated.section_state.research.evidence_contract_sha256 == evidence_bundle_sha256(bundle_b)


def test_bundle_items_keep_canonical_selected_and_dropped_dispositions() -> None:
    bundle = _bundle("dispositions", selected_id="E1", dropped_id="E2")
    dispositions = {item.evidence_id: item.disposition for item in bundle.items}

    assert dispositions["E1"] is EvidenceDisposition.SELECTED
    assert dispositions["E2"] is EvidenceDisposition.DROPPED
