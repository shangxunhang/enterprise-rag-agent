"""Architecture Spec v1.0 Phase 1 协议验收测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentExecutionResultSchema,
    AgentInvocationStatus,
    AgentPayloadType,
    AgentRole,
    ArtifactReferenceSchema,
    ClaimLifecycleStatus,
    ClaimSchema,
    ClaimSourceBindingSchema,
    ClaimSupportType,
    ClaimVerificationStatus,
    DomainEventSchema,
    MultiAgentSharedStateSchema,
    NormalizedToolResultSchema,
    PatchOperation,
    PlannerResultPayloadSchema,
    SectionExecutionStateSchema,
    SectionStateSchema,
    SectionStatus,
    SectionStrategy,
    SourceSnapshotStateSchema,
    StateActorRole,
    StateMetaSchema,
    StatePatchActorSchema,
    StatePatchOperationSchema,
    StatePatchRequestSchema,
    TaskContextStateSchema,
    ToolProvenanceSchema,
    ToolResultErrorSchema,
    ToolResultStatus,
    WorkflowErrorCategory,
    WorkflowErrorSchema,
    WorkflowErrorScope,
    WorkflowErrorSource,
    WorkflowRetryability,
    WriterResultPayloadSchema,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _context(**updates: object) -> AgentExecutionContextSchema:
    payload: dict[str, object] = {
        "invocation_id": "inv_1",
        "logical_operation_id": "plan_initial",
        "task_id": "task_1",
        "run_id": "run_1",
        "agent_role": AgentRole.PLANNER,
        "execution_mode": "initial_plan",
        "execution_id": "exec_1",
        "attempt_id": "attempt_1",
        "input_snapshot_version": "snapshot_1",
        "expected_domain_versions": {"execution_plan": 0},
        "task_context_ref": "artifact://task-context/1",
        "source_snapshot_ref": "artifact://source-snapshot/1",
        "authorized_artifact_refs": ["artifact://source-snapshot/1"],
        "authorized_tool_names": ["template.read"],
        "writable_state_paths": ["/execution_plan/plan_ref"],
        "budget": {"max_initial_runs": 1},
        "trigger": {"kind": "task_started"},
        "trace_context": {"trace_id": "trace_1"},
    }
    payload.update(updates)
    return AgentExecutionContextSchema.model_validate(payload)


def _workflow_error() -> WorkflowErrorSchema:
    return WorkflowErrorSchema(
        error_id="err_1",
        source=WorkflowErrorSource.AGENT,
        category=WorkflowErrorCategory.AGENT_OUTPUT_INVALID,
        scope=WorkflowErrorScope.SECTION,
        retryability=WorkflowRetryability.RERUN_CURRENT_NODE,
        affected_section_ids=["section_1"],
        error_code="AGENT_OUTPUT_INVALID",
        message="agent payload failed schema validation",
    )


def _planner_payload() -> PlannerResultPayloadSchema:
    return PlannerResultPayloadSchema(
        plan_ref="artifact://plan/1",
        plan_revision=1,
        section_task_ids=["section_1"],
    )


def _section_state(
    *,
    section_id: str = "section_1",
    strategy: SectionStrategy = SectionStrategy.TECHNICAL_GENERATE,
    status: SectionStatus = SectionStatus.PLANNED,
) -> SectionStateSchema:
    return SectionStateSchema(
        section_id=section_id,
        section_title="计算资源",
        section_order=1,
        strategy=strategy,
        execution=SectionExecutionStateSchema(
            status=status,
            input_snapshot_version="snapshot_1",
        ),
    )


def _shared_state() -> MultiAgentSharedStateSchema:
    section = _section_state()
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
        section_states={"section_1": section},
    )


def test_execution_context_is_minimal_and_rejects_shared_state() -> None:
    context = _context()

    assert context.schema_version == "agent_execution_context_v1"
    assert "shared_state" not in AgentExecutionContextSchema.model_fields

    with pytest.raises(ValidationError, match="extra_forbidden"):
        _context(shared_state={"task_id": "task_1"})


def test_execution_context_rejects_invalid_domain_version_and_section_ref() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        _context(expected_domain_versions={"execution_plan": -1})

    with pytest.raises(ValidationError, match="requires section_id"):
        _context(section_state_ref="artifact://section-state/1")


def test_agent_result_requires_matching_strong_payload() -> None:
    result = AgentExecutionResultSchema(
        invocation_id="inv_1",
        agent_role=AgentRole.PLANNER,
        execution_mode="initial_plan",
        execution_id="exec_1",
        attempt_id="attempt_1",
        input_snapshot_version="snapshot_1",
        status=AgentInvocationStatus.COMPLETED,
        payload_type=AgentPayloadType.PLANNER_RESULT,
        payload=_planner_payload(),
        artifact_refs=["artifact://plan/1"],
        trace_ref="trace://inv_1",
    )

    assert isinstance(result.payload, PlannerResultPayloadSchema)

    with pytest.raises(ValidationError, match="requires PlannerResultPayloadSchema"):
        AgentExecutionResultSchema(
            invocation_id="inv_2",
            agent_role=AgentRole.WRITER,
            execution_mode="technical_generate",
            execution_id="exec_2",
            attempt_id="attempt_2",
            input_snapshot_version="snapshot_1",
            status=AgentInvocationStatus.COMPLETED,
            payload_type=AgentPayloadType.PLANNER_RESULT,
            payload=WriterResultPayloadSchema(
                section_id="section_1",
                draft_ref="artifact://draft/1",
                draft_version=1,
            ),
            trace_ref="trace://inv_2",
        )


def test_failed_agent_result_requires_workflow_error() -> None:
    common = {
        "invocation_id": "inv_1",
        "agent_role": AgentRole.PLANNER,
        "execution_mode": "initial_plan",
        "execution_id": "exec_1",
        "attempt_id": "attempt_1",
        "input_snapshot_version": "snapshot_1",
        "status": AgentInvocationStatus.FAILED,
        "payload_type": AgentPayloadType.PLANNER_RESULT,
        "payload": _planner_payload(),
        "trace_ref": "trace://inv_1",
    }

    with pytest.raises(ValidationError, match="requires WorkflowError"):
        AgentExecutionResultSchema.model_validate(common)

    result = AgentExecutionResultSchema.model_validate(
        {**common, "error": _workflow_error()}
    )
    assert result.error is not None
    assert result.error.category == WorkflowErrorCategory.AGENT_OUTPUT_INVALID


def test_state_patch_is_field_level_and_section_scoped() -> None:
    patch = StatePatchRequestSchema(
        request_id="patch_1",
        task_id="task_1",
        section_id="section_1",
        actor=StatePatchActorSchema(
            agent_role=StateActorRole.WRITER,
            execution_id="exec_1",
            attempt_id="attempt_1",
        ),
        input_snapshot_version="snapshot_1",
        expected_domain_version=0,
        target_domain="section.writing",
        operations=[
            StatePatchOperationSchema(
                operation=PatchOperation.REPLACE,
                path="/draft_ref",
                value="artifact://draft/1",
            )
        ],
        reason_code="paragraph_completed",
    )

    assert patch.operations[0].path == "/draft_ref"

    with pytest.raises(ValidationError, match="non-root JSON Pointer"):
        StatePatchOperationSchema(
            operation=PatchOperation.REPLACE,
            path="/",
            value={},
        )

    with pytest.raises(ValidationError, match="requires section_id"):
        StatePatchRequestSchema.model_validate(
            {
                **patch.model_dump(mode="python"),
                "section_id": None,
            }
        )


def test_patch_request_rejects_duplicate_paths() -> None:
    with pytest.raises(ValidationError, match="same path twice"):
        StatePatchRequestSchema(
            request_id="patch_1",
            task_id="task_1",
            section_id="section_1",
            actor=StatePatchActorSchema(
                agent_role=StateActorRole.RESEARCH,
                execution_id="exec_1",
                attempt_id="attempt_1",
            ),
            input_snapshot_version="snapshot_1",
            expected_domain_version=0,
            target_domain="section.research",
            operations=[
                StatePatchOperationSchema(
                    operation=PatchOperation.ADD,
                    path="/evidence_ids",
                    value=["evidence_1"],
                ),
                StatePatchOperationSchema(
                    operation=PatchOperation.REPLACE,
                    path="/evidence_ids",
                    value=["evidence_2"],
                ),
            ],
            reason_code="evidence_ready",
        )


def test_shared_state_uses_specified_top_level_domains() -> None:
    state = _shared_state()

    assert set(MultiAgentSharedStateSchema.model_fields) == {
        "schema_version",
        "state_meta",
        "task_context",
        "source_snapshot",
        "execution_plan",
        "section_states",
        "document_validation",
        "artifact_state",
        "runtime_control",
        "human_reviews",
        "final_result",
        "domain_versions",
    }
    assert state.section_states["section_1"].execution.status == SectionStatus.PLANNED


def test_shared_state_rejects_mismatched_section_key() -> None:
    payload = _shared_state().model_dump(mode="python")
    payload["section_states"] = {"wrong_key": _section_state()}

    with pytest.raises(ValidationError, match="keys must match section_id"):
        MultiAgentSharedStateSchema.model_validate(payload)


def test_skip_strategy_cannot_enter_generation() -> None:
    with pytest.raises(ValidationError, match="cannot enter an executable status"):
        _section_state(
            strategy=SectionStrategy.SKIP,
            status=SectionStatus.GENERATING,
        )


def test_tool_result_enforces_normalized_error_contract() -> None:
    success = NormalizedToolResultSchema(
        call_id="call_1",
        status=ToolResultStatus.SUCCESS,
        data_type="fact_query",
        data={"fact_ids": ["fact_1"]},
        provenance=ToolProvenanceSchema(
            source_refs=["fact_1"],
            raw_artifact_ref="artifact://tool-raw/1",
            input_snapshot_version="snapshot_1",
        ),
    )
    assert success.error.error_code is None

    with pytest.raises(ValidationError, match="requires error_code"):
        NormalizedToolResultSchema(
            call_id="call_2",
            status=ToolResultStatus.RETRYABLE_ERROR,
            data_type="internal_search",
            provenance=ToolProvenanceSchema(
                input_snapshot_version="snapshot_1"
            ),
        )

    stale = NormalizedToolResultSchema(
        call_id="call_3",
        status=ToolResultStatus.STALE,
        data_type="internal_search",
        provenance=ToolProvenanceSchema(
            input_snapshot_version="snapshot_1"
        ),
        error=ToolResultErrorSchema(error_code="STALE_RESULT"),
    )
    assert stale.status == ToolResultStatus.STALE


def test_artifact_reference_is_immutable_by_contract() -> None:
    artifact = ArtifactReferenceSchema(
        artifact_ref="artifact://paragraph/1",
        artifact_type="paragraph",
        content_hash="sha256:abc",
        schema_version="paragraph_artifact_v1",
        size_bytes=128,
        created_by="writer",
        task_id="task_1",
        section_id="section_1",
        attempt_id="attempt_1",
        input_snapshot_version="snapshot_1",
    )

    assert artifact.immutable is True
    assert artifact.reference_schema_version == "artifact_reference_v1"

    with pytest.raises(ValidationError):
        ArtifactReferenceSchema.model_validate(
            {**artifact.model_dump(mode="python"), "immutable": False}
        )


def test_claim_contract_preserves_authority_and_lifecycle() -> None:
    claim = ClaimSchema(
        claim_id="claim_1",
        section_id="section_1",
        paragraph_id="paragraph_1",
        claim_type="device_quantity",
        text_span_ref="artifact://span/1",
        normalized_value=8,
        unit="台",
        source_bindings=[
            ClaimSourceBindingSchema(
                source_type="excel_fact",
                source_ref="fact://device/server/count",
                support_type=ClaimSupportType.AUTHORITATIVE,
            )
        ],
        verification_status=ClaimVerificationStatus.VERIFIED,
        lifecycle_status=ClaimLifecycleStatus.ACTIVE,
    )

    assert claim.normalized_value == 8
    assert claim.source_bindings[0].support_type == ClaimSupportType.AUTHORITATIVE


def test_domain_event_carries_patch_version_and_snapshot_identity() -> None:
    event = DomainEventSchema(
        event_id="event_1",
        event_type="state_patch_committed",
        sequence=1,
        task_id="task_1",
        run_id="run_1",
        section_id="section_1",
        actor_role=StateActorRole.WRITER,
        execution_id="exec_1",
        attempt_id="attempt_1",
        input_snapshot_version="snapshot_1",
        target_domain="section.writing",
        domain_version=1,
        patch_request_id="patch_1",
        occurred_at=NOW,
    )

    assert event.sequence == 1
    assert event.domain_version == 1
