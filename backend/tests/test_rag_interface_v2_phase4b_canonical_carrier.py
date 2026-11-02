from __future__ import annotations

import json
from types import SimpleNamespace

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.agents.agent_rag_v1.base import StatelessBusinessAgent
from apps.enterprise_document.schemas.agent_rag_v1 import (
    ResearchEvidencePackageSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    ResearchStatus,
)
from apps.enterprise_document.quality.budget import WorkflowBudget
from apps.enterprise_document.schemas.gate_decision import DeliveryMode, GateDecisionSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_generation_graph import (
    SchemeGenerationGraphPayloadSchema,
    SchemeGenerationGraphStateCodec,
)
from apps.enterprise_document.schemas.scheme_writer.evidence import (
    SectionEvidenceBundleSchema,
)
from apps.enterprise_document.schemas.writer_governance import WriterGateBindingSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    ResearchProjectionStatus,
    ResearchStateSchema,
    SectionLifecycleStatus,
    SectionPlanSchema,
    SectionStateSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
    SectionExecutionGraphStateCodec,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    MainlineResearchRetrieval,
)
from apps.enterprise_document.services.scheme_writer.document_citation_registry import (
    DocumentCitationRegistry,
)
from context_manager.policies import SectionGenerationContextPolicy
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from apps.enterprise_document.services.scheme_writer.stage_service import (
    SchemeGenerationStageService,
)
from apps.enterprise_document.runtime.agent_rag_v1.mainline_recovery import (
    MainlineReplanAttempt,
)
from apps.enterprise_document.workflows.section_execution_graph import (
    SectionExecutionGraph,
)
from apps.enterprise_document.services.scheme_writer.evidence_projection import (
    bundle_first_assessment_projection,
)
from contracts.evidence import (
    build_evidence_bundle,
    evidence_bundle_sha256,
    project_evidence_bundle,
)
from schemas.citation import CitationSchema
from schemas.claim import ClaimType
from schemas.context import ContextBundleSchema, TaskContextSchema, UserContextSchema
from schemas.evidence_assessment import (
    EvidenceAssessmentV2Schema,
    EvidenceRelevance,
)
from schemas.evidence_source import (
    AuthorityLevel,
    DocumentType,
    EvidenceRole,
    ProjectRelation,
)
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from schemas.rag import (
    EvidenceRequirementsSchema,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RAGContextSchema,
    RetrievedChunkSchema,
)

RAGEvidenceContractBuilder = SimpleNamespace(build=build_evidence_bundle)
RAGEvidenceContractReader = SimpleNamespace(projections=project_evidence_bundle)


def _chunk(*, child: str = "child_1", parent: str = "parent_1") -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=child,
        context_chunk_id=parent,
        child_chunk_id=child,
        parent_chunk_id=parent,
        doc_id="doc_1",
        match_text="matched evidence",
        context_text="canonical evidence text",
        title="Evidence",
    )


def _citation(*, citation_id: str = "source-local-id") -> CitationSchema:
    return CitationSchema(
        citation_id=citation_id,
        source_type="document",
        source_document_id="doc_1",
        doc_id="doc_1",
        child_chunk_id="child_1",
        parent_chunk_id="parent_1",
        quote_text="canonical evidence text",
        title="Evidence",
    )


def _bundle(*, scope: str = "document"):
    contract = RAGEvidenceContractBuilder.build(
        query="canonical evidence",
        rewritten_queries=[],
        selected_chunks=[_chunk()],
        dropped_chunks=[],
        citations=[_citation()],
        trace=None,
        max_context_chars=6000,
    )
    return contract.model_copy(
        update={
            "task_id": "task_phase4b",
            "run_id": "run_phase4b",
            "retrieval_trace_id": f"trace_{scope}",
            "retrieval_scope": scope,
            "evidence_requirements": EvidenceRequirementsSchema.from_topics(
                section_id="document",
                section_type="document",
                topics=["canonical evidence"],
            ),
        },
        deep=True,
    )


def _item_assessment(
    *,
    evidence_id: str,
    document_id: str,
    status: EvidenceStatus,
) -> EvidenceAssessmentV2Schema:
    sufficient = status is EvidenceStatus.SUFFICIENT
    return EvidenceAssessmentV2Schema(
        evidence_id=evidence_id,
        document_id=document_id,
        document_type=DocumentType.CURRENT_PROJECT_REQUIREMENT,
        evidence_role=EvidenceRole.PROJECT_FACT,
        project_relation=ProjectRelation.CURRENT_PROJECT,
        authority_level=AuthorityLevel.AUTHORITATIVE,
        eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
        semantic_relevance=1.0 if sufficient else 0.0,
        eligibility=sufficient,
        relevance=(
            EvidenceRelevance.RELEVANT
            if sufficient
            else EvidenceRelevance.IRRELEVANT
        ),
        supported_topics=["canonical evidence"] if sufficient else [],
        supported_claim_types=(
            [ClaimType.CURRENT_PROJECT_FACT] if sufficient else []
        ),
        forbidden_claim_types=[],
        reason_codes=[
            "fixture_eligible" if sufficient else "fixture_ineligible"
        ],
    )


def _assessed_bundle(*, status: EvidenceStatus, query: str, scope: str):
    bundle = _bundle(scope=scope)
    if status is EvidenceStatus.SUFFICIENT:
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status=status,
            coverage_ratio=1.0,
            required_topics=[query],
            covered_topics=[query],
            authoritative_evidence_present=True,
            prompt_evidence_source="phase4b_fix_test",
            reason_codes=["coverage_sufficient"],
        )
    else:
        assessment = RAGEvidenceAssessmentSchema(
            assessment_state="completed",
            status=status,
            coverage_ratio=0.0,
            required_topics=[query],
            missing_topics=[query],
            prompt_evidence_source="phase4b_fix_test",
            reason_codes=["coverage_insufficient"],
        )
    return bundle.model_copy(
        update={
            "query": query,
            "retrieval_scope": scope,
            "item_assessments": [
                _item_assessment(
                    evidence_id="E1",
                    document_id=bundle.items[0].doc_id,
                    status=status,
                )
            ],
            "assessment": assessment,
        },
        deep=True,
    )


def _distinct_assessed_bundle(
    *,
    status: EvidenceStatus,
    query: str,
    scope: str,
    marker: str,
):
    chunk = RetrievedChunkSchema(
        rank=1,
        matched_chunk_id=f"child_{marker}",
        context_chunk_id=f"parent_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        doc_id=f"doc_{marker}",
        match_text=f"match {marker}",
        context_text=f"CONTEXT_{marker}",
        title=f"Evidence {marker}",
    )
    citation = CitationSchema(
        citation_id=f"source_{marker}",
        source_type="document",
        source_document_id=f"doc_{marker}",
        doc_id=f"doc_{marker}",
        child_chunk_id=f"child_{marker}",
        parent_chunk_id=f"parent_{marker}",
        quote_text=f"CONTEXT_{marker}",
        title=f"Evidence {marker}",
    )
    bundle = RAGEvidenceContractBuilder.build(
        query=query,
        rewritten_queries=[],
        selected_chunks=[chunk],
        dropped_chunks=[],
        citations=[citation],
        trace=None,
        max_context_chars=6000,
    )
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=status,
        coverage_ratio=1.0 if status is EvidenceStatus.SUFFICIENT else 0.0,
        required_topics=[query],
        covered_topics=[query] if status is EvidenceStatus.SUFFICIENT else [],
        missing_topics=[query] if status is EvidenceStatus.INSUFFICIENT else [],
        authoritative_evidence_present=(status is EvidenceStatus.SUFFICIENT),
        prompt_evidence_source="phase4b_final_fix_test",
        reason_codes=[
            "coverage_sufficient"
            if status is EvidenceStatus.SUFFICIENT
            else "coverage_insufficient"
        ],
    )
    updated = bundle.model_copy(
        update={
            "task_id": "task_phase4b_recovery",
            "run_id": "run_phase4b_recovery",
            "retrieval_trace_id": f"trace_{marker}",
            "retrieval_scope": scope,
            "item_assessments": [
                _item_assessment(
                    evidence_id="E1",
                    document_id=bundle.items[0].doc_id,
                    status=status,
                )
            ],
            "assessment": assessment,
        },
        deep=True,
    )
    return updated.__class__.model_validate(
        updated.model_dump(mode="python", round_trip=True)
    )


def _writer_gate(*, permitted: bool, bundle) -> WriterGateBindingSchema:
    contract_sha = evidence_bundle_sha256(bundle)
    return WriterGateBindingSchema(
        section_id="section_phase4b_recovery_001",
        section_title="技术方案",
        section_order=1,
        document_id="document_phase4b_recovery",
        run_id="run_phase4b_recovery",
        gate_decision=GateDecisionSchema(
            policy_id="phase4b_final_fix_test",
            evidence_status=(
                EvidenceStatus.SUFFICIENT
                if permitted
                else EvidenceStatus.INSUFFICIENT
            ),
            delivery_mode=(DeliveryMode.GROUNDED if permitted else DeliveryMode.BLOCKED),
            allowed_claim_types=[],
            forbidden_claim_types=[],
            human_review_required=False,
            reason_codes=["test_permitted" if permitted else "test_blocked"],
        ),
        generation_allowed=permitted,
        evidence_contract_sha256=contract_sha,
        prompt_evidence_contract_sha256=contract_sha,
    )


class _GraphRecoveryEvidenceService:
    def __init__(self, candidate_bundle) -> None:
        self.candidate_bundle = candidate_bundle

    def retrieve(self, *args, **kwargs):
        del args, kwargs
        return self.candidate_bundle

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
                "evidence_contract_sha256": evidence_bundle_sha256(result),
            },
        )


class _GraphRecoveryQueryBuilder:
    def build(self, project_input, section_title, *, recovery: bool):
        del project_input, section_title
        return "candidate recovery query" if recovery else "initial query"


class _GraphRecoveryEnforcement:
    def __init__(self, should_block: bool) -> None:
        self.should_block = should_block

    def as_metadata(self, *, gate_id: str) -> dict:
        return {"gate_id": gate_id, "should_block": self.should_block}


class _GraphRecoveryCoordinator:
    def __init__(self, *, candidate_bundle, candidate_gate) -> None:
        self.evidence_service = _GraphRecoveryEvidenceService(candidate_bundle)
        self.query_builder = _GraphRecoveryQueryBuilder()
        self.section_generation_service = SimpleNamespace()
        self.runtime_support = SimpleNamespace()
        self.generation_quality_metadata = {}
        self.section_agent_adapter = None
        self.candidate_gate = candidate_gate

    def _writer_gate_binding(self, *args, **kwargs):
        del args, kwargs
        return self.candidate_gate

    @staticmethod
    def writer_gate_blocks(binding) -> bool:
        return binding is None or not binding.ordinary_generation_permitted

    def resolve_writer_gate_enforcement(self, binding):
        return _GraphRecoveryEnforcement(self.writer_gate_blocks(binding))


def _graph_recovery_payload(*, active_bundle, active_gate):
    task_id = "task_phase4b_recovery"
    run_id = "run_phase4b_recovery"
    project_input = ProjectInputSchema(
        task_id=task_id,
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
    plan = SectionPlanSchema(
        section_id="section_phase4b_recovery_001",
        section_title="技术方案",
        section_order=1,
        citation_required=True,
    )
    shared_state = GraphStateSchema(
        task_id=task_id,
        run_id=run_id,
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id=task_id,
                run_id=run_id,
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-14T20:20:00+08:00",
    )
    active_context, active_chunks, active_citations = (
        RAGEvidenceContractReader.projections(active_bundle)
    )
    return SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id="document_phase4b_recovery",
        project_input=project_input,
        section_plan=plan,
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(shared_state),
        document_evidence_bundle=active_bundle,
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        total_sections=1,
        citation_required=True,
        citation_registry=list(active_citations),
        active_evidence_bundle=active_bundle,
        active_context=active_context,
        active_chunks=list(active_chunks),
        active_citations=list(active_citations),
        active_query=active_bundle.query,
        active_scope=active_bundle.retrieval_scope,
        active_writer_gate=active_gate,
        budget_state=WorkflowBudget.from_policy_metadata({}).snapshot(),
    )


def _run_graph_recovery(*, active_bundle, active_gate, candidate_bundle, candidate_gate, corrective):
    coordinator = _GraphRecoveryCoordinator(
        candidate_bundle=candidate_bundle,
        candidate_gate=candidate_gate,
    )
    graph = SectionExecutionGraph(coordinator=coordinator)
    payload = _graph_recovery_payload(
        active_bundle=active_bundle,
        active_gate=active_gate,
    )
    state = graph.codec.build_initial_state(payload)
    update = graph._retrieve_recovery(state, corrective=corrective)
    restored = graph.codec.restore_payload({**state, **update})
    return restored


class _GatewayRecoveryAdapter:
    uses_research_tool_gateway = True

    def __init__(self, candidate_bundle) -> None:
        self.candidate_bundle = candidate_bundle

    def research(self, **kwargs):
        context, chunks, citations = RAGEvidenceContractReader.projections(
            self.candidate_bundle
        )
        normalized = {
            "context": context.model_dump(mode="json"),
            "retrieved_chunks": [item.model_dump(mode="json") for item in chunks],
            "citations": [item.model_dump(mode="json") for item in citations],
        }
        assessment = bundle_first_assessment_projection(self.candidate_bundle)
        sufficient = assessment.get("status") == "sufficient"
        payload = ResearchResultPayloadSchema(
            section_id=str(kwargs["section_id"]),
            evidence_package_ref=(
                f"state://section-evidence/{kwargs['section_id']}/candidate"
            ),
            evidence_summary=ResearchEvidencePackageSchema(
                section_id=str(kwargs["section_id"]),
                accepted_evidence_ids=list(
                    self.candidate_bundle.selected_evidence_ids
                ),
                research_status=(
                    ResearchStatus.SUFFICIENT
                    if sufficient
                    else ResearchStatus.INSUFFICIENT
                ),
                recommended_action=(
                    ResearchRecommendedAction.WRITE
                    if sufficient
                    else ResearchRecommendedAction.HUMAN_REVIEW
                ),
            ),
            evidence_contract_sha256=evidence_bundle_sha256(
                self.candidate_bundle
            ),
            tool_call_ids=[str(self.candidate_bundle.retrieval_trace_id)],
            research_round=int(kwargs["research_round"]),
            research_mode=str(kwargs["research_mode"]),
            agent_metadata={"invocation_id": "phase4b_gateway_recovery_test"},
        )
        retrieval = MainlineResearchRetrieval(
            success=True,
            evidence_bundle=self.candidate_bundle,
            context=context,
            chunks=tuple(chunks),
            citations=tuple(citations),
            citation_registry=tuple(citations),
            normalized=normalized,
            assessment=assessment,
            retrieval_trace_id=self.candidate_bundle.retrieval_trace_id,
        )
        section_state = kwargs["section_state"]
        projected_research = section_state.research.model_copy(
            update={
                "status": (
                    ResearchProjectionStatus.COMPLETED
                    if sufficient
                    else ResearchProjectionStatus.INSUFFICIENT
                ),
                "evidence_package_ref": payload.evidence_package_ref,
                "evidence_contract_sha256": payload.evidence_contract_sha256,
                "accepted_evidence_ids": list(
                    self.candidate_bundle.selected_evidence_ids
                ),
                "rejected_evidence_ids": [],
                "research_rounds": int(kwargs["research_round"]),
                "tool_call_ids": list(payload.tool_call_ids),
                "recommended_action": payload.evidence_summary.recommended_action.value,
            },
            deep=True,
        )
        section_state = section_state.model_copy(
            update={
                "lifecycle_status": (
                    SectionLifecycleStatus.EVIDENCE_READY
                    if sufficient
                    else SectionLifecycleStatus.BLOCKED
                ),
                "research": projected_research,
            },
            deep=True,
        )
        return SimpleNamespace(
            payload=payload,
            section_state=section_state,
            result=SimpleNamespace(),
            retrieval=retrieval,
        )


def _run_gateway_graph_recovery(
    *, active_bundle, active_gate, candidate_bundle, candidate_gate, corrective
):
    coordinator = _GraphRecoveryCoordinator(
        candidate_bundle=candidate_bundle,
        candidate_gate=candidate_gate,
    )
    coordinator.section_agent_adapter = _GatewayRecoveryAdapter(candidate_bundle)
    graph = SectionExecutionGraph(coordinator=coordinator)
    graph.attempt_guard = SimpleNamespace(
        validate_result_metadata=lambda **kwargs: {"validated": True},
        snapshot_version=lambda *args, **kwargs: "phase4b-fixture-snapshot-v1",
    )
    payload = _graph_recovery_payload(
        active_bundle=active_bundle,
        active_gate=active_gate,
    )
    payload = payload.model_copy(
        update={
            "section_state": graph._initialize_section_state(
                payload,
                section_id=str(payload.section_id),
            )
        },
        deep=True,
    )
    state = graph.codec.build_initial_state(payload)
    update = graph._retrieve_recovery(state, corrective=corrective)
    return graph.codec.restore_payload({**state, **update})


def test_citation_ref_is_stable_source_identity_not_local_or_document_label() -> None:
    first = RAGEvidenceContractBuilder.build(
        query="q1",
        rewritten_queries=[],
        selected_chunks=[_chunk()],
        dropped_chunks=[],
        citations=[_citation(citation_id="upstream-A")],
        trace=None,
        max_context_chars=6000,
    )
    second = RAGEvidenceContractBuilder.build(
        query="q2",
        rewritten_queries=[],
        selected_chunks=[_chunk()],
        dropped_chunks=[],
        citations=[_citation(citation_id="upstream-B")],
        trace=None,
        max_context_chars=6000,
    )

    assert first.citations[0].citation_id == "C1"
    assert second.citations[0].citation_id == "C1"
    assert first.citations[0].citation_ref
    assert first.citations[0].citation_ref == second.citations[0].citation_ref

    registry = DocumentCitationRegistry.from_citations(
        [
            CitationSchema(
                citation_id="C1",
                source_type="document",
                doc_id="other_doc",
                quote_text="other evidence",
            )
        ]
    )
    remapped, mapping = registry.register(
        list(first.citations),
        scope="section",
        query="q1",
    )
    assert remapped[0].citation_id == "C2"
    assert mapping["C1"] == "C2"
    assert remapped[0].citation_ref == first.citations[0].citation_ref


def test_application_citation_remap_does_not_change_canonical_bundle_hash() -> None:
    bundle = _bundle()
    canonical_hash = evidence_bundle_sha256(bundle)
    context, chunks, citations = RAGEvidenceContractReader.projections(bundle)
    normalized = {
        "evidence_contract_sha256": canonical_hash,
        "context": context.model_dump(mode="json"),
        "citations": [item.model_dump(mode="json") for item in citations],
        "retrieved_chunks": [item.model_dump(mode="json") for item in chunks],
    }
    registry = DocumentCitationRegistry.from_citations(
        [
            CitationSchema(
                citation_id="C1",
                source_type="document",
                doc_id="other_doc",
                quote_text="other evidence",
            )
        ]
    )

    remapped_context, _, remapped_citations, _ = registry.remap_bundle(
        context=context,
        chunks=chunks,
        citations=citations,
        normalized=normalized,
        scope="document",
        query=bundle.query,
    )

    assert remapped_citations[0].citation_id == "C2"
    assert remapped_citations[0].citation_ref == bundle.citations[0].citation_ref
    assert "evidence_contract" not in remapped_context.extra
    assert evidence_bundle_sha256(bundle) == canonical_hash


def test_document_bundle_survives_top_level_checkpoint_round_trip() -> None:
    bundle = _bundle(scope="document")
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task_phase4b",
        run_id="run_phase4b",
        document_evidence_bundle=bundle,
    )
    codec = SchemeGenerationGraphStateCodec()
    state = codec.build_initial_update(payload)
    runtime_state = {
        "task_id": payload.task_id,
        "run_id": payload.run_id,
        **state,
    }
    codec.assert_json_serializable(runtime_state)
    restored = codec.restore_payload(runtime_state)

    assert restored.document_evidence_bundle is not None
    assert restored.document_evidence_bundle == bundle
    assert restored.document_evidence_bundle.retrieval_scope == "document"
    assert restored.document_evidence_bundle.evidence_requirements is not None
    assert restored.document_evidence_bundle.citations[0].citation_ref == bundle.citations[0].citation_ref


def test_section_graph_active_bundle_survives_checkpoint_codec_round_trip() -> None:
    task_id = "task_phase4b_section_checkpoint"
    run_id = "run_phase4b_section_checkpoint"
    bundle = _bundle(scope="section")
    project_input = ProjectInputSchema(
        task_id=task_id,
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
    plan = SectionPlanSchema(
        section_id="section_phase4b_001",
        section_title="技术方案",
        section_order=1,
        citation_required=True,
    )
    shared_state = GraphStateSchema(
        task_id=task_id,
        run_id=run_id,
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(
                task_id=task_id,
                run_id=run_id,
                task_type="scheme_generation",
            ),
        ),
        created_at="2026-08-14T15:37:00+08:00",
    )
    payload = SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id="document_phase4b",
        project_input=project_input,
        section_plan=plan,
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(
            shared_state
        ),
        document_evidence_bundle=bundle,
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        active_evidence_bundle=bundle,
        active_context=bundle.context,
    )
    codec = SectionExecutionGraphStateCodec()
    runtime_state = codec.build_initial_state(payload)
    codec.assert_json_serializable(runtime_state)
    restored = codec.restore_payload(json.loads(json.dumps(runtime_state)))

    assert restored.document_evidence_bundle == bundle
    assert restored.active_evidence_bundle == bundle
    assert restored.active_evidence_bundle.citations[0].citation_ref == bundle.citations[0].citation_ref


def test_section_final_carrier_keeps_one_canonical_bundle_and_json_round_trips() -> None:
    bundle = _bundle(scope="recovery")
    section_evidence = SectionEvidenceBundleSchema(
        section_id="section_001",
        section_title="技术方案",
        evidence_bundle=bundle,
        evidence_contract_sha256=evidence_bundle_sha256(bundle),
    )
    serialized = json.loads(
        section_evidence.model_dump_json(round_trip=True)
    )
    restored = SectionEvidenceBundleSchema.model_validate(serialized)

    assert restored.evidence_bundle == bundle
    assert restored.evidence_bundle.retrieval_scope == "recovery"
    assert restored.evidence_contract_sha256 == evidence_bundle_sha256(
        restored.evidence_bundle
    )


def test_context_policy_consumes_typed_bundle_without_projection_rebuild() -> None:
    bundle = _bundle(scope="section")
    context = bundle.context.model_copy(deep=True)
    context.extra = {
        **dict(context.extra or {}),
        "evidence_contract": {"invalid_compatibility_projection": True},
    }
    project_input = ProjectInputSchema(
        task_id="task_phase4b_context",
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

    request = SectionGenerationContextPolicy().build_request(
        task_id="task_phase4b_context",
        run_id="run_phase4b_context",
        section_id="section_phase4b_context_001",
        section_title="技术方案",
        section_order=1,
        project_input=project_input,
        section_contract="仅使用提供证据",
        target_section_chars=800,
        rag_context=context,
        citations=list(bundle.citations),
        previous_sections=[],
        evidence_bundle=bundle,
    )

    evidence_items = [item for item in request.items if item.source_type == "evidence"]
    assert evidence_items
    assert evidence_items[0].item_id == bundle.selected_evidence_ids[0]
    assert evidence_items[0].metadata.get("compatibility_projection") is not True


def test_document_assembler_chunks_are_projected_from_document_and_final_section_bundles() -> None:
    document_bundle = _bundle(scope="document")
    final_section_bundle = RAGEvidenceContractBuilder.build(
        query="section evidence",
        rewritten_queries=[],
        selected_chunks=[_chunk(child="child_2", parent="parent_2")],
        dropped_chunks=[],
        citations=[
            CitationSchema(
                citation_id="upstream-section",
                source_type="document",
                source_document_id="doc_1",
                doc_id="doc_1",
                child_chunk_id="child_2",
                parent_chunk_id="parent_2",
                quote_text="section canonical evidence",
            )
        ],
        trace=None,
        max_context_chars=6000,
    ).model_copy(update={"retrieval_scope": "section"}, deep=True)
    section_evidence = SectionEvidenceBundleSchema(
        section_id="section_001",
        section_title="技术方案",
        evidence_bundle=final_section_bundle,
    )
    payload = SchemeGenerationGraphPayloadSchema(
        task_id="task_phase4b",
        run_id="run_phase4b",
        document_evidence_bundle=document_bundle,
        section_evidence=[section_evidence],
    )

    chunks = SchemeGenerationStageService._assembly_chunks_from_canonical_bundles(
        payload
    )
    assert [item.child_chunk_id for item in chunks] == ["child_1", "child_2"]


def test_retrieval_history_projection_never_persists_full_bundle_copies() -> None:
    bundle = _bundle(scope="recovery")
    normalized = {
        "query": bundle.query,
        "status": bundle.status.value,
        "evidence": bundle.model_dump(mode="json", round_trip=True),
        "context": bundle.context.model_dump(mode="json"),
        "retrieved_chunks": [{"chunk_id": "child_1"}],
        "citations": [{"citation_id": "C1"}],
    }

    history = SchemeEvidenceService.retrieval_history_projection(
        normalized,
        scope="recovery",
        evidence_bundle=bundle,
    )

    assert history["scope"] == "recovery"
    assert history["retrieval_trace_id"] == bundle.retrieval_trace_id
    assert history["evidence_contract_sha256"] == evidence_bundle_sha256(bundle)
    assert history["citation_count"] == len(bundle.citations)
    assert history["retrieved_chunk_count"] == len(bundle.selected_evidence_ids)
    assert not {"evidence", "context", "retrieved_chunks", "citations"} & set(history)


def test_production_graph_rejects_blocked_recovery_and_retains_permitted_active_bundle() -> None:
    active_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="active permitted query",
        scope="section",
        marker="ACTIVE_A",
    )
    candidate_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="candidate blocked query",
        scope="recovery",
        marker="CANDIDATE_B",
    )
    active_gate = _writer_gate(permitted=True, bundle=active_bundle)
    candidate_gate = _writer_gate(permitted=False, bundle=candidate_bundle)
    active_context, active_chunks, active_citations = (
        RAGEvidenceContractReader.projections(active_bundle)
    )

    restored = _run_graph_recovery(
        active_bundle=active_bundle,
        active_gate=active_gate,
        candidate_bundle=candidate_bundle,
        candidate_gate=candidate_gate,
        corrective=True,
    )

    assert restored.active_evidence_bundle == active_bundle
    assert restored.active_writer_gate == active_gate
    assert restored.active_query == active_bundle.query
    assert restored.active_scope == active_bundle.retrieval_scope
    assert restored.active_context == active_context
    assert restored.active_chunks == active_chunks
    assert restored.active_citations == active_citations
    assert restored.active_evidence_bundle.assessment.status is EvidenceStatus.SUFFICIENT
    assert restored.retrieval_metadata["corrective_retrieval_success"] is True
    assert restored.retrieval_metadata["corrective_recovery_accepted"] is False
    assert restored.retrieval_metadata["corrective_recovery_rejected"] == (
        "recovery_not_ordinary_generation_permitted"
    )
    assert restored.retrieval_metadata["recovery_candidate_retained_scope"] == "section"


def test_production_gateway_recovery_rejects_blocked_candidate_before_active_promotion() -> None:
    active_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="gateway active permitted query",
        scope="section",
        marker="GATEWAY_ACTIVE_A",
    )
    candidate_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="gateway candidate blocked query",
        scope="self_rag_recovery",
        marker="GATEWAY_CANDIDATE_B",
    )
    active_gate = _writer_gate(permitted=True, bundle=active_bundle)
    candidate_gate = _writer_gate(permitted=False, bundle=candidate_bundle)

    restored = _run_gateway_graph_recovery(
        active_bundle=active_bundle,
        active_gate=active_gate,
        candidate_bundle=candidate_bundle,
        candidate_gate=candidate_gate,
        corrective=False,
    )

    assert restored.active_evidence_bundle == active_bundle
    assert restored.active_writer_gate == active_gate
    assert restored.active_query == active_bundle.query
    assert restored.active_scope == active_bundle.retrieval_scope
    assert restored.active_evidence_bundle.assessment.status is EvidenceStatus.SUFFICIENT
    assert restored.section_state is not None
    assert restored.section_state.lifecycle_status is not SectionLifecycleStatus.BLOCKED
    assert restored.section_state.research.status is not ResearchProjectionStatus.INSUFFICIENT
    assert restored.retrieval_metadata["research_tool_retrieval_success"] is True
    assert restored.retrieval_metadata["self_rag_recovery_accepted"] is False
    assert restored.retrieval_metadata["self_rag_recovery_rejected"] == (
        "recovery_not_ordinary_generation_permitted"
    )


def test_production_graph_accepts_permitted_recovery_over_blocked_active_bundle() -> None:
    active_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="active blocked query",
        scope="section",
        marker="BLOCKED_A",
    )
    candidate_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="candidate permitted query",
        scope="recovery",
        marker="PERMITTED_B",
    )
    active_gate = _writer_gate(permitted=False, bundle=active_bundle)
    candidate_gate = _writer_gate(permitted=True, bundle=candidate_bundle)

    restored = _run_graph_recovery(
        active_bundle=active_bundle,
        active_gate=active_gate,
        candidate_bundle=candidate_bundle,
        candidate_gate=candidate_gate,
        corrective=True,
    )

    assert restored.active_evidence_bundle == candidate_bundle
    assert restored.active_writer_gate == candidate_gate
    assert restored.active_query == "candidate recovery query"
    assert restored.active_scope == "recovery"
    assert restored.active_evidence_bundle.assessment.status is EvidenceStatus.SUFFICIENT
    assert restored.retrieval_metadata["corrective_recovery_accepted"] is True
    assert restored.retrieval_metadata["recovery_candidate_generation_permitted"] is True


def test_production_graph_accepts_permitted_self_rag_recovery_over_permitted_active_bundle() -> None:
    active_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="active permitted query",
        scope="section",
        marker="PERMITTED_A",
    )
    candidate_bundle = _distinct_assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="candidate permitted query",
        scope="self_rag_recovery",
        marker="PERMITTED_B2",
    )
    active_gate = _writer_gate(permitted=True, bundle=active_bundle)
    candidate_gate = _writer_gate(permitted=True, bundle=candidate_bundle)

    restored = _run_graph_recovery(
        active_bundle=active_bundle,
        active_gate=active_gate,
        candidate_bundle=candidate_bundle,
        candidate_gate=candidate_gate,
        corrective=False,
    )

    assert restored.active_evidence_bundle == candidate_bundle
    assert restored.active_writer_gate == candidate_gate
    assert restored.active_query == "candidate recovery query"
    assert restored.active_scope == "self_rag_recovery"
    assert restored.active_evidence_bundle.assessment.status is EvidenceStatus.SUFFICIENT
    assert restored.retrieval_metadata["self_rag_recovery_accepted"] is True
    assert restored.retrieval_metadata["recovery_candidate_generation_permitted"] is True


def test_citation_ref_is_not_treated_as_agent_artifact_capability_ref() -> None:
    class _Invocation:
        def model_dump(self, mode: str = "python"):
            del mode
            return {
                "citation_ref": "stable-source-sha",
                "evidence_package_ref": "state://section-evidence/section_1/document",
            }

    refs = StatelessBusinessAgent._input_artifact_refs(_Invocation())
    assert "stable-source-sha" not in refs
    assert "state://section-evidence/section_1/document" in refs


def _phase4b_fix_payload(*, bundle, legacy_status: str) -> SectionExecutionGraphPayloadSchema:
    del legacy_status
    task_id = "task_phase4b_fix"
    run_id = "run_phase4b_fix"
    project_input = ProjectInputSchema(
        task_id=task_id,
        task_type="scheme_generation",
        user_query="生成建设方案",
        generation_requirements={"required_sections": ["技术方案"]},
        output_schema={"document_title": "建设方案", "required_sections": ["技术方案"]},
    )
    plan = SectionPlanSchema(
        section_id="section_phase4b_fix_001",
        section_title="技术方案",
        section_order=1,
    )
    shared_state = GraphStateSchema(
        task_id=task_id,
        run_id=run_id,
        task_type="scheme_generation",
        user_input="生成建设方案",
        context_bundle=ContextBundleSchema(
            user=UserContextSchema(user_query="生成建设方案"),
            task=TaskContextSchema(task_id=task_id, run_id=run_id, task_type="scheme_generation"),
        ),
        created_at="2026-08-14T19:00:00+08:00",
    )
    return SectionExecutionGraphPayloadSchema(
        task_id=task_id,
        run_id=run_id,
        document_id="document_phase4b_fix",
        project_input=project_input,
        section_plan=plan,
        shared_state_payload=SectionExecutionGraphStateCodec.dump_graph_state(shared_state),
        document_evidence_bundle=bundle,
        section_id=plan.section_id,
        section_title=plan.section_title,
        section_order=plan.section_order,
        section_state=SectionStateSchema(
            section_id=plan.section_id,
            section_title=plan.section_title,
            section_order=plan.section_order,
        ),
        active_evidence_bundle=bundle,
        active_context=bundle.context,
    )


def test_bundle_assessment_is_the_only_persisted_active_assessment_source() -> None:
    sufficient = _assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="canonical sufficient",
        scope="section",
    )
    sufficient_payload = _phase4b_fix_payload(
        bundle=sufficient,
        legacy_status="insufficient",
    )
    assert "active_assessment" not in SectionExecutionGraphPayloadSchema.model_fields
    assert bundle_first_assessment_projection(
        sufficient_payload.active_evidence_bundle,
    )["status"] == "sufficient"
    assert sufficient_payload.section_state is not None
    assert sufficient_payload.section_state.research.status == ResearchProjectionStatus.PENDING
    assert "assessment" not in ResearchStateSchema.model_fields

    insufficient = _assessed_bundle(
        status=EvidenceStatus.INSUFFICIENT,
        query="canonical insufficient",
        scope="section",
    )
    insufficient_payload = _phase4b_fix_payload(
        bundle=insufficient,
        legacy_status="sufficient",
    )
    assert bundle_first_assessment_projection(
        insufficient_payload.active_evidence_bundle,
    )["status"] == "insufficient"
    assert insufficient_payload.section_state is not None
    assert insufficient_payload.section_state.research.status == ResearchProjectionStatus.PENDING
    assert "assessment" not in ResearchStateSchema.model_fields


def test_typed_section_projection_does_not_persist_research_assessment_copy() -> None:
    bundle = _assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="canonical research assessment",
        scope="section",
    )
    payload = _phase4b_fix_payload(bundle=bundle, legacy_status="insufficient")

    assert payload.section_state is not None
    assert "assessment" not in ResearchStateSchema.model_fields
    assert bundle.assessment.status == EvidenceStatus.SUFFICIENT


def test_replan_initialization_derives_document_projection_from_bundle_only() -> None:
    bundle = _assessed_bundle(
        status=EvidenceStatus.SUFFICIENT,
        query="canonical query",
        scope="document",
    )
    payload = _phase4b_fix_payload(bundle=bundle, legacy_status="insufficient")
    for retired in (
        "document_rag_context",
        "document_retrieved_chunks",
        "document_citations",
        "document_rag_output",
        "document_evidence_assessment",
    ):
        assert retired not in SectionExecutionGraphPayloadSchema.model_fields
    revised_plan = payload.section_plan.model_copy(
        update={"metadata": {"replan_reason": "phase4b_fix_test"}},
        deep=True,
    )

    replanned = MainlineReplanAttempt.prepare(payload=payload, revised_plan=revised_plan)
    projected_context, projected_chunks, projected_citations = RAGEvidenceContractReader.projections(bundle)

    assert replanned.active_evidence_bundle == bundle
    assert replanned.active_query == "canonical query"
    assert replanned.active_scope == bundle.retrieval_scope
    assert replanned.active_evidence_bundle.assessment.status is EvidenceStatus.SUFFICIENT
    assert replanned.active_context == projected_context
    assert replanned.active_chunks == projected_chunks
    assert replanned.active_citations == projected_citations
    assert replanned.retrieval_metadata["evidence_contract_sha256"] == evidence_bundle_sha256(bundle)
    assert replanned.retrieval_metadata["evidence_projection_source"] == "canonical_bundle"
