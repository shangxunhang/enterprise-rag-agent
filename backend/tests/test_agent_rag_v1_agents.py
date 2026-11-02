"""Architecture Spec v1.0 Phase 4 四业务 Agent 与 Tool Gateway 验收。"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.enterprise_document.agents.agent_rag_v1 import (
    PlannerAgent,
    ResearchAgent,
    VerifierAgent,
    WriterAgent,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentInvocationStatus,
    AgentRole,
    MultiAgentSharedStateSchema,
    ParagraphArtifactSchema,
    PlannerInvocationInputSchema,
    ResearchInvocationInputSchema,
    SectionExecutionStateSchema,
    SectionStateSchema,
    SectionStatus,
    SectionStrategy,
    SourceSnapshotStateSchema,
    StateMetaSchema,
    TaskContextStateSchema,
    ToolHandlerOutputSchema,
    ToolResultStatus,
    VerifierExecutionMode,
    VerifierInvocationInputSchema,
    WriterInvocationInputSchema,
    WorkflowErrorCategory,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    FileArtifactStore,
    InMemoryDomainEventLog,
    InMemoryToolExecutionStore,
    StateGateway,
    ToolGateway,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


class StaticReasoner:
    def __init__(self, response):
        self.response = response

    def invoke(self, *, context, invocation_input, tools):
        del context, invocation_input, tools
        return self.response


class ResearchToolReasoner(StaticReasoner):
    def invoke(self, *, context, invocation_input, tools):
        result = tools.call(
            "internal.search",
            {"section_id": invocation_input.section_id},
        )
        assert result.status == ToolResultStatus.SUCCESS
        return self.response


def _tool_gateway() -> ToolGateway:
    gateway = ToolGateway(execution_store=InMemoryToolExecutionStore())
    gateway.register(
        name="internal.search",
        version="v1",
        max_retries=0,
        handler=lambda call: ToolHandlerOutputSchema(
            data_type="evidence_candidates",
            data={"ids": ["evidence_1"]},
            source_refs=["source://internal/1"],
        ),
    )
    return gateway


def _context(
    *,
    role: AgentRole,
    invocation_id: str,
    section_id: str | None,
    expected_domain: str,
    writable_fields: list[str],
    authorized_refs: list[str],
    authorized_tools: list[str] | None = None,
    execution_mode: str = "default",
) -> AgentExecutionContextSchema:
    return AgentExecutionContextSchema(
        invocation_id=invocation_id,
        logical_operation_id=invocation_id,
        task_id="task_1",
        run_id="run_1",
        section_id=section_id,
        agent_role=role,
        execution_mode=execution_mode,
        execution_id=f"exec_{section_id or 'task'}",
        attempt_id=f"attempt_{section_id or 'task'}",
        input_snapshot_version="snapshot_1",
        expected_domain_versions={expected_domain: 0},
        task_context_ref="artifact://task-context/1",
        source_snapshot_ref="artifact://source-snapshot/1",
        execution_plan_ref="artifact://plan/current",
        section_state_ref=(
            f"artifact://section-state/{section_id}" if section_id else None
        ),
        authorized_artifact_refs=authorized_refs,
        authorized_tool_names=authorized_tools or [],
        writable_state_paths=[
            f"/{expected_domain.replace('.', '/')}/{field}"
            for field in writable_fields
        ],
    )


def _state() -> MultiAgentSharedStateSchema:
    return MultiAgentSharedStateSchema(
        state_meta=StateMetaSchema(
            task_id="task_1",
            run_id="run_1",
            input_snapshot_version="snapshot_1",
            created_at=NOW,
            updated_at=NOW,
        ),
        task_context=TaskContextStateSchema(
            task_context_ref="artifact://task-context/1"
        ),
        source_snapshot=SourceSnapshotStateSchema(
            source_snapshot_ref="artifact://source-snapshot/1",
            fact_snapshot_ref="artifact://facts/1",
            template_schema_ref="artifact://template/1",
            source_inventory_ref="artifact://inventory/1",
        ),
        section_states={
            "compute": SectionStateSchema(
                section_id="compute",
                section_title="计算资源",
                section_order=1,
                strategy=SectionStrategy.TECHNICAL_GENERATE,
                execution=SectionExecutionStateSchema(
                    status=SectionStatus.RESEARCHING,
                    execution_id="exec_compute",
                    active_attempt_id="attempt_compute",
                    active_plan_version=1,
                    input_snapshot_version="snapshot_1",
                ),
            )
        },
    )


def test_tool_gateway_enforces_role_and_context_authorization(tmp_path) -> None:
    calls = []
    gateway = ToolGateway(
        execution_store=InMemoryToolExecutionStore(),
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    )

    def handler(call):
        calls.append(call.call_id)
        return ToolHandlerOutputSchema(
            data_type="search_result",
            data={"ids": ["e1"]},
            raw_result={"provider_payload": "raw"},
        )

    gateway.register(
        name="internal.search",
        version="v1",
        handler=handler,
        max_retries=0,
    )
    research_context = _context(
        role=AgentRole.RESEARCH,
        invocation_id="research_1",
        section_id="compute",
        expected_domain="section.research",
        writable_fields=["evidence_package_ref"],
        authorized_refs=[],
        authorized_tools=["internal.search"],
    )
    first = gateway.invoke(
        research_context,
        call_id="call_1",
        tool_name="internal.search",
        arguments={"query": "计算资源"},
    )
    replay = gateway.invoke(
        research_context,
        call_id="call_1",
        tool_name="internal.search",
        arguments={"query": "计算资源"},
    )

    assert first == replay
    assert calls == ["call_1"]
    assert first.provenance.raw_artifact_ref is not None

    planner_context = research_context.model_copy(
        update={
            "invocation_id": "planner_1",
            "agent_role": AgentRole.PLANNER,
        }
    )
    rejected = gateway.invoke(
        planner_context,
        call_id="call_2",
        tool_name="internal.search",
        arguments={"query": "计算资源"},
    )
    assert rejected.status == ToolResultStatus.REJECTED
    assert rejected.error.error_code == "TOOL_PERMISSION_DENIED"


def test_four_agents_emit_strong_payloads_and_gateway_patches() -> None:
    tool_gateway = _tool_gateway()
    state_gateway = StateGateway(
        event_log=InMemoryDomainEventLog(),
        clock=lambda: NOW,
    )
    state = _state()

    planner_context = _context(
        role=AgentRole.PLANNER,
        invocation_id="planner_1",
        section_id=None,
        expected_domain="execution_plan",
        writable_fields=[
            "plan_ref",
            "plan_version",
            "active_plan_revision_id",
            "section_order",
        ],
        authorized_refs=[
            "artifact://template/1",
            "artifact://inventory/1",
            "artifact://facts/1",
        ],
        execution_mode="initial_plan",
    )
    planner = PlannerAgent(
        reasoner=StaticReasoner(
            {
                "plan_ref": "artifact://plan/1",
                "plan_revision": 1,
                "section_task_ids": ["compute"],
            }
        ),
        tool_gateway=tool_gateway,
    )
    planner_result = planner.run(
        planner_context,
        PlannerInvocationInputSchema(
            template_schema_ref="artifact://template/1",
            source_inventory_ref="artifact://inventory/1",
            fact_snapshot_ref="artifact://facts/1",
        ),
    )
    assert planner_result.status == AgentInvocationStatus.COMPLETED
    state = state_gateway.commit(
        state,
        planner_result.proposed_state_patches[0],
    ).state

    research_context = _context(
        role=AgentRole.RESEARCH,
        invocation_id="research_1",
        section_id="compute",
        expected_domain="section.research",
        writable_fields=[
            "evidence_package_ref",
            "evidence_ids",
            "evidence_gap_count",
        ],
        authorized_refs=[
            "artifact://section-task/compute",
            "artifact://facts/1",
            "artifact://inventory/1",
        ],
        authorized_tools=["internal.search"],
        execution_mode="section_research",
    )
    research = ResearchAgent(
        reasoner=ResearchToolReasoner(
            {
                "section_id": "compute",
                "evidence_package_ref": "artifact://evidence/compute/1",
                "evidence_summary": {
                    "section_id": "compute",
                    "accepted_evidence_ids": ["evidence_1"],
                    "supported_topics": ["计算资源"],
                    "research_status": "sufficient",
                    "recommended_action": "write",
                },
            }
        ),
        tool_gateway=tool_gateway,
    )
    research_result = research.run(
        research_context,
        ResearchInvocationInputSchema(
            section_id="compute",
            section_task_ref="artifact://section-task/compute",
            fact_snapshot_ref="artifact://facts/1",
            source_inventory_ref="artifact://inventory/1",
            evidence_topics=["计算资源"],
        ),
    )
    assert research_result.tool_call_refs == [
        "research_1:tool:001:internal.search"
    ]
    state = state_gateway.commit(
        state,
        research_result.proposed_state_patches[0],
    ).state

    writer_context = _context(
        role=AgentRole.WRITER,
        invocation_id="writer_1",
        section_id="compute",
        expected_domain="section.writing",
        writable_fields=[
            "draft_ref",
            "draft_version",
            "paragraph_refs",
            "paragraph_versions",
            "expected_paragraph_ids",
            "generation_complete",
            "change_manifest_ref",
        ],
        authorized_refs=[
            "artifact://facts/1",
            "artifact://evidence/compute/1",
        ],
        execution_mode="technical_generate",
    )
    paragraph = ParagraphArtifactSchema(
        paragraph_id="p_stable_1",
        paragraph_version=1,
        order=1,
        role="body",
        content_ref="artifact://paragraph/compute/1",
        content_hash="sha256:paragraph",
    )
    writer = WriterAgent(
        reasoner=StaticReasoner(
            {
                "section_id": "compute",
                "draft_ref": "artifact://draft/compute/1",
                "draft_version": 1,
                "paragraphs": [paragraph.model_dump(mode="json")],
                "generation_complete": True,
                "expected_paragraph_ids": ["p_stable_1"],
            }
        ),
        tool_gateway=tool_gateway,
    )
    writer_result = writer.run(
        writer_context,
        WriterInvocationInputSchema(
            section_id="compute",
            strategy=SectionStrategy.TECHNICAL_GENERATE,
            fact_snapshot_ref="artifact://facts/1",
            evidence_package_ref="artifact://evidence/compute/1",
        ),
    )
    assert writer_result.status == AgentInvocationStatus.COMPLETED
    state = state_gateway.commit(
        state,
        writer_result.proposed_state_patches[0],
    ).state

    verifier_context = _context(
        role=AgentRole.VERIFIER,
        invocation_id="verifier_1",
        section_id="compute",
        expected_domain="section.verification",
        writable_fields=[
            "execution_mode",
            "recommended_action",
            "verification_ref",
            "issue_ids",
            "accepted_paragraph_ids",
            "rejected_paragraph_ids",
        ],
        authorized_refs=[
            "artifact://draft/compute/1",
            "artifact://facts/1",
        ],
        execution_mode="section_verify",
    )
    verifier = VerifierAgent(
        reasoner=StaticReasoner(
            {
                "execution_mode": "section_verify",
                "action": "accept",
                "accepted_paragraph_ids": ["p_stable_1"],
                "verification_ref": "artifact://verification/compute/1",
            }
        ),
        tool_gateway=tool_gateway,
    )
    verifier_result = verifier.run(
        verifier_context,
        VerifierInvocationInputSchema(
            execution_mode=VerifierExecutionMode.SECTION_VERIFY,
            section_id="compute",
            target_artifact_ref="artifact://draft/compute/1",
            fact_snapshot_ref="artifact://facts/1",
        ),
    )
    assert verifier_result.emitted_signals == ["accept"]
    state = state_gateway.commit(
        state,
        verifier_result.proposed_state_patches[0],
    ).state

    assert state.execution_plan.plan_ref == "artifact://plan/1"
    assert (
        state.section_states["compute"].research.evidence_package_ref
        == "artifact://evidence/compute/1"
    )
    assert state.section_states["compute"].writing.paragraph_refs == {
        "p_stable_1": "artifact://paragraph/compute/1"
    }
    assert (
        state.section_states["compute"].verification.recommended_action.value
        == "accept"
    )
    assert set(planner.__dict__) == {"reasoner", "tool_gateway"}


def test_writer_rejects_false_generation_completion() -> None:
    writer = WriterAgent(
        reasoner=StaticReasoner(
            {
                "section_id": "compute",
                "draft_ref": "artifact://draft/compute/1",
                "draft_version": 1,
                "paragraphs": [],
                "generation_complete": True,
                "expected_paragraph_ids": ["missing"],
            }
        ),
        tool_gateway=_tool_gateway(),
    )
    context = _context(
        role=AgentRole.WRITER,
        invocation_id="writer_invalid",
        section_id="compute",
        expected_domain="section.writing",
        writable_fields=["draft_ref"],
        authorized_refs=[
            "artifact://facts/1",
            "artifact://evidence/compute/1",
        ],
    )
    result = writer.run(
        context,
        WriterInvocationInputSchema(
            section_id="compute",
            strategy=SectionStrategy.TECHNICAL_GENERATE,
            fact_snapshot_ref="artifact://facts/1",
            evidence_package_ref="artifact://evidence/compute/1",
        ),
    )

    assert result.status == AgentInvocationStatus.FAILED
    assert result.payload is None
    assert result.proposed_state_patches == []
    assert result.error is not None
    assert result.error.category == WorkflowErrorCategory.AGENT_OUTPUT_INVALID


def test_document_verifier_writes_document_validation_only() -> None:
    verifier = VerifierAgent(
        reasoner=StaticReasoner(
            {
                "execution_mode": "document_verify",
                "action": "rewrite",
                "issue_ids": ["cross_section_conflict"],
                "verification_ref": "artifact://verification/document/1",
            }
        ),
        tool_gateway=_tool_gateway(),
    )
    context = _context(
        role=AgentRole.VERIFIER,
        invocation_id="document_verify_1",
        section_id=None,
        expected_domain="document_validation",
        writable_fields=["verification_ref", "status", "issue_ids"],
        authorized_refs=[
            "artifact://draft/document/1",
            "artifact://facts/1",
        ],
        execution_mode="document_verify",
    )
    result = verifier.run(
        context,
        VerifierInvocationInputSchema(
            execution_mode=VerifierExecutionMode.DOCUMENT_VERIFY,
            target_artifact_ref="artifact://draft/document/1",
            fact_snapshot_ref="artifact://facts/1",
        ),
    )

    assert result.status == AgentInvocationStatus.COMPLETED
    assert result.proposed_state_patches[0].target_domain == "document_validation"
    assert result.proposed_state_patches[0].section_id is None
