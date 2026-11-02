from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.runtime.graph_state import GraphStateSchema
from apps.enterprise_document.schemas.agent_rag_v1 import (
    CanonicalBudgetVectorSchema,
    CanonicalEvidenceBundleBindingSchema,
    ResearchRoundRefSchema,
    ToolHandlerOutputSchema,
    ToolResultStatus,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    ActiveExecutionLineageSchema,
    AttemptLifecycleStatus,
    RunLifecycleStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    CanonicalInvocationBudgetAdapter,
    InMemoryToolExecutionStore,
    JsonlToolExecutionStore,
    ToolGateway,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_adapter import (
    build_round4_fake_section_agent_adapter,
)
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    BoundResearchToolRequest,
    MainlineResearchToolGateway,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import (
    SchemeEvidenceService,
)
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from rag.store.evidence_bundle_store import EvidenceBundleSnapshotStore
from schemas.rag import EvidenceBundleSchema

from backend.tests.test_rag_interface_v2_phase5_id_namespace import (
    _bundle,
    _gateway_context,
    _graph_payload,
    _project_input,
    _section_plan,
    _shared_state,
)


NOW = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


class _CanonicalEvidenceService:
    def __init__(self, bundle: EvidenceBundleSchema, root) -> None:
        self.bundle = bundle
        self.store = EvidenceBundleSnapshotStore(root)
        self.retrieve_calls = 0

    def retrieve(self, *args, **kwargs):
        del args, kwargs
        self.retrieve_calls += 1
        return self.bundle

    def canonical_bundle_binding(
        self,
        bundle: EvidenceBundleSchema,
    ) -> CanonicalEvidenceBundleBindingSchema:
        snapshot = self.store.persist(bundle)
        return CanonicalEvidenceBundleBindingSchema(
            evidence_bundle_handle=snapshot.handle,
            evidence_bundle_sha256=snapshot.evidence_bundle_sha256,
        )

    def resolve_canonical_bundle(self, handle: str) -> EvidenceBundleSchema:
        return self.store.resolve(handle)

    def extract_rag_output(
        self,
        shared_state: GraphStateSchema,
        bundle: EvidenceBundleSchema,
    ):
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


def _budget_manager(tmp_path) -> BudgetManager:
    return BudgetManager.canonical(
        task_id="task_phase5",
        run_id="run_phase5",
        limits=CanonicalBudgetVectorSchema(
            logical_model_calls=2,
            logical_tool_calls=4,
            model_tokens_total=4096,
            research_rounds=4,
            repair_attempts=2,
            generation_attempts=4,
            replan_attempts=2,
            true_retry_attempts=2,
        ),
        max_elapsed_seconds=600,
        storage_path=tmp_path / "budget.jsonl",
        policy_ref="budget-policy://step6-research",
        policy_version="v1",
        clock=lambda: NOW,
    )


def _round_ref():
    context = _gateway_context()
    return ResearchRoundRefSchema(
        run_id=context.run_id,
        section_id=str(context.section_id),
        attempt_scope_ref=f"section:{context.section_id}",
        attempt_id=context.attempt_id,
        research_round_id="rr_step6_canonical_tool_bridge",
    )


def _request(round_ref: ResearchRoundRefSchema) -> BoundResearchToolRequest:
    return BoundResearchToolRequest(
        shared_state=_shared_state(),
        project_input=_project_input(),
        section_id=round_ref.section_id,
        section_title="技术方案",
        query="canonical research query",
        scope="section",
        call_suffix="step6",
        citation_registry=[],
        research_round_ref=round_ref,
    )


def _arguments(round_ref: ResearchRoundRefSchema) -> dict:
    return {
        "query": "canonical research query",
        "scope": "section",
        "section_id": round_ref.section_id,
        "section_title": "技术方案",
        "research_round": 1,
        "research_mode": "initial",
    }


class _CaptureRAGService:
    def __init__(self) -> None:
        self.requests = []

    def retrieve(self, request):
        self.requests.append(request)
        return None


def _canonical_requirements() -> EvidenceRequirementsSnapshotSchema:
    return EvidenceRequirementsSnapshotSchema(
        requirements_sha256="c" * 64,
        source_profile_ref="profile://step6",
        source_task_ref="task-contract://step6",
        requirements=(
            EvidenceRequirementSchema(
                requirement_id="req-canonical-1",
                criteria=(
                    EvidenceCriterionSchema(
                        criterion_id="criterion-canonical-1",
                        criterion="必须说明计算资源规模",
                    ),
                    EvidenceCriterionSchema(
                        criterion_id="criterion-canonical-2",
                        criterion="必须给出资源来源依据",
                    ),
                ),
            ),
        ),
    )


def test_scheme_evidence_service_projects_exact_canonical_requirements_into_rag() -> None:
    rag = _CaptureRAGService()
    service = SchemeEvidenceService(
        rag_service=rag,  # type: ignore[arg-type]
        agent_name="ResearchAgent",
    )
    snapshot = _canonical_requirements()

    result = service.retrieve(
        _shared_state(),
        _project_input(),
        query="资源规模",
        scope="section",
        section_id="section_phase5_001",
        section_title="技术方案",
        canonical_evidence_requirements=snapshot,
        canonical_evidence_requirement_ids=("req-canonical-1",),
    )

    assert result is None
    assert len(rag.requests) == 1
    projected = rag.requests[0].evidence_requirements
    assert projected is not None
    assert [item.requirement_id for item in projected.requirements] == [
        "req-canonical-1"
    ]
    assert "必须说明计算资源规模" in projected.requirements[0].topic
    assert "必须给出资源来源依据" in projected.requirements[0].topic


def test_scheme_evidence_service_rejects_unknown_canonical_requirement_before_rag() -> None:
    rag = _CaptureRAGService()
    service = SchemeEvidenceService(
        rag_service=rag,  # type: ignore[arg-type]
        agent_name="ResearchAgent",
    )

    with pytest.raises(ValueError, match="absent from snapshot"):
        service.retrieve(
            _shared_state(),
            _project_input(),
            query="资源规模",
            scope="section",
            section_id="section_phase5_001",
            section_title="技术方案",
            canonical_evidence_requirements=_canonical_requirements(),
            canonical_evidence_requirement_ids=("req-unknown",),
        )

    assert rag.requests == []


def test_adapter_rebinds_agent_context_to_canonical_attempt_identity() -> None:
    bundle = _bundle("step6_context", selected_id="E_CONTEXT")
    payload = _graph_payload(bundle)
    assert payload.section_state is not None
    lineage = ActiveExecutionLineageSchema(
        run_id="run_phase5",
        run_lifecycle_revision=2,
        run_status=RunLifecycleStatus.RUNNING,
        attempt_scope_ref="section:section_phase5_001",
        attempt_id="canonical-attempt-step6",
        attempt_lifecycle_revision=3,
        attempt_status=AttemptLifecycleStatus.RUNNING,
        input_snapshot_version="canonical-input-snapshot-step6",
    )
    round_ref = ResearchRoundRefSchema(
        run_id="run_phase5",
        section_id="section_phase5_001",
        attempt_scope_ref=lineage.attempt_scope_ref,
        attempt_id=lineage.attempt_id,
        research_round_id="rr_step6_context_rebind",
    )
    adapter = build_round4_fake_section_agent_adapter()

    outcome = adapter.research(
        node_name="step6_context_rebind",
        task_id="task_phase5",
        run_id="run_phase5",
        project_input=_project_input(),
        section_plan=_section_plan(),
        section_state=payload.section_state,
        section_id="section_phase5_001",
        section_title="技术方案",
        retrieval_scope="section",
        research_mode="initial",
        research_round=1,
        citation_required=True,
        assessment={"status": "not_assessed", "details": {}},
        accepted_evidence_ids=list(bundle.selected_evidence_ids),
        rejected_evidence_ids=[],
        evidence_contract_sha256=evidence_bundle_sha256(bundle),
        tool_call_ids=[],
        retrieval_succeeded=True,
        prior_evidence_package_ref=None,
        budget={},
        query="",
        research_round_ref=round_ref,
        active_execution_lineage=lineage,
    )

    assert outcome.payload.agent_metadata["attempt_id"] == lineage.attempt_id
    assert outcome.payload.agent_metadata["input_snapshot_version"] == (
        lineage.input_snapshot_version
    )


def test_canonical_research_tool_bridge_uses_stable_operation_budget_and_replay(
    tmp_path,
) -> None:
    bundle = _bundle("step6_tool_bridge", selected_id="E_STEP6")
    service = _CanonicalEvidenceService(bundle, tmp_path / "bundle-store")
    manager = _budget_manager(tmp_path)
    tool_log = tmp_path / "research-tool-results.jsonl"

    def provider(context):
        # Reopen the operational store for every invocation to model process
        # restart rather than relying on one process-local cache.
        return ToolGateway(
            execution_store=JsonlToolExecutionStore(tool_log),
            budget_runtime=CanonicalInvocationBudgetAdapter(manager),
            authorization_validator=lambda ctx, registration, arguments: (
                None
                if (
                    ctx.run_id == context.run_id
                    and registration.name == MainlineResearchToolGateway.tool_name
                    and arguments["section_id"] == context.section_id
                )
                else (_ for _ in ()).throw(PermissionError("grant mismatch"))
            ),
            currentness_validator=lambda ctx: (
                None
                if ctx.attempt_id == context.attempt_id
                else (_ for _ in ()).throw(RuntimeError("stale attempt"))
            ),
        )

    gateway = MainlineResearchToolGateway(
        evidence_service=service,  # type: ignore[arg-type]
        canonical_gateway_provider=provider,
    )
    round_ref = _round_ref()
    context = _gateway_context()
    request = _request(round_ref)

    with gateway.bind(request):
        first = gateway.session(context).call(
            MainlineResearchToolGateway.tool_name,
            _arguments(round_ref),
        )

    assert first.status is ToolResultStatus.SUCCESS
    assert first.tool_operation_id is not None
    assert first.budget_settlement is not None
    assert request.execution is not None
    assert request.execution.canonical_bundle_binding is not None
    assert request.execution.tool_operation_refs[0].logical_operation_id == (
        first.tool_operation_id
    )
    assert request.execution.budget_receipt_refs == (
        first.budget_settlement.reservation_id,
    )
    assert request.execution.canonical_bundle_binding.evidence_bundle_sha256 == (
        "sha256:" + evidence_bundle_sha256(bundle)
    )
    assert service.retrieve_calls == 1
    assert manager.canonical_state().used.logical_tool_calls == 1

    # Physical/correlation invocation aliases may change across restart/replay.
    # The exact ResearchRound occurrence + call slot retains one semantic Tool op.
    replay_context = context.model_copy(
        update={
            "invocation_id": "inv_phase5_gateway_replay",
            "execution_id": "exec_phase5_gateway_replay",
        },
        deep=True,
    )
    replay_request = _request(round_ref)
    with gateway.bind(replay_request):
        replay = gateway.session(replay_context).call(
            MainlineResearchToolGateway.tool_name,
            _arguments(round_ref),
        )

    assert replay.tool_operation_id == first.tool_operation_id
    assert replay.tool_semantic_fingerprint == first.tool_semantic_fingerprint
    assert replay_request.execution is not None
    assert replay_request.execution.evidence_bundle == bundle
    assert replay_request.execution.tool_operation_refs == (
        request.execution.tool_operation_refs
    )
    assert replay_request.execution.budget_receipt_refs == (
        request.execution.budget_receipt_refs
    )
    assert service.retrieve_calls == 1
    assert manager.canonical_state().used.logical_tool_calls == 1


def test_canonical_research_tool_never_falls_back_without_generic_gateway(
    tmp_path,
) -> None:
    service = _CanonicalEvidenceService(
        _bundle("step6_no_gateway", selected_id="E_NO_GATEWAY"),
        tmp_path / "bundle-store",
    )
    gateway = MainlineResearchToolGateway(
        evidence_service=service,  # type: ignore[arg-type]
    )
    round_ref = _round_ref()

    with gateway.bind(_request(round_ref)):
        with pytest.raises(
            RuntimeError,
            match="canonical ToolGateway provider is not configured",
        ):
            gateway.session(_gateway_context()).call(
                MainlineResearchToolGateway.tool_name,
                _arguments(round_ref),
            )

    assert service.retrieve_calls == 0


def test_canonical_research_tool_requires_canonical_budget_runtime(tmp_path) -> None:
    service = _CanonicalEvidenceService(
        _bundle("step6_no_budget", selected_id="E_NO_BUDGET"),
        tmp_path / "bundle-store",
    )
    execution_store = InMemoryToolExecutionStore()

    def provider(context):
        del context
        return ToolGateway(
            execution_store=execution_store,
            authorization_validator=lambda ctx, registration, arguments: None,
            currentness_validator=lambda ctx: None,
        )

    gateway = MainlineResearchToolGateway(
        evidence_service=service,  # type: ignore[arg-type]
        canonical_gateway_provider=provider,
    )
    round_ref = _round_ref()

    with gateway.bind(_request(round_ref)):
        with pytest.raises(
            RuntimeError,
            match="requires canonical Budget runtime",
        ):
            gateway.session(_gateway_context()).call(
                MainlineResearchToolGateway.tool_name,
                _arguments(round_ref),
            )

    assert service.retrieve_calls == 0
