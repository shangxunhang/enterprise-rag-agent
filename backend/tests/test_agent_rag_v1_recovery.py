"""Architecture Spec v1.0 Phase 7 recovery and exception acceptance tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    HumanReviewResumeRequestSchema,
    HumanReviewStateSchema,
    InvocationRecoveryAction,
    MultiAgentSharedStateSchema,
    PatchOperation,
    ResumeStage,
    ResumeTriggerKind,
    RunningInvocationRecoveryContextSchema,
    RunningInvocationRecoveryInputSchema,
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
    UnderlyingInvocationStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    EventReplayRecoveryService,
    HumanReviewResumeError,
    HumanReviewResumeService,
    InMemoryDomainEventLog,
    JsonCheckpointStore,
    RunningInvocationRecoveryService,
    StateGateway,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _section(section_id: str, order: int) -> SectionStateSchema:
    return SectionStateSchema(
        section_id=section_id,
        section_title=f"Section {order}",
        section_order=order,
        strategy=SectionStrategy.TECHNICAL_GENERATE,
        execution=SectionExecutionStateSchema(
            status=SectionStatus.RESEARCHING,
            execution_id=f"exec_{section_id}",
            active_attempt_id=f"attempt_{section_id}",
            active_plan_version=1,
            input_snapshot_version="snapshot_1",
        ),
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
            "compute": _section("compute", 1),
            "storage": _section("storage", 2),
        },
    )


def _patch(request_id: str, section_id: str, value: str) -> StatePatchRequestSchema:
    return StatePatchRequestSchema(
        request_id=request_id,
        task_id="task_1",
        section_id=section_id,
        actor=StatePatchActorSchema(
            agent_role=StateActorRole.RESEARCH,
            execution_id=f"exec_{section_id}",
            attempt_id=f"attempt_{section_id}",
        ),
        input_snapshot_version="snapshot_1",
        expected_domain_version=0,
        target_domain="section.research",
        operations=[
            StatePatchOperationSchema(
                operation=PatchOperation.REPLACE,
                path="/evidence_package_ref",
                value=value,
            )
        ],
        reason_code="recovery_test",
    )


def _running(
    *,
    result_persisted: bool = False,
    completion_event_persisted: bool = False,
    queryable: bool = False,
    status: UnderlyingInvocationStatus = UnderlyingInvocationStatus.UNKNOWN,
) -> RunningInvocationRecoveryInputSchema:
    return RunningInvocationRecoveryInputSchema(
        invocation_id="inv_1",
        task_id="task_1",
        run_id="run_1",
        execution_id="exec_compute",
        attempt_id="attempt_compute",
        input_snapshot_version="snapshot_1",
        idempotency_key="idem_1",
        completion_event_persisted=completion_event_persisted,
        result_persisted=result_persisted,
        result_ref="artifact://results/1" if result_persisted else None,
        underlying_queryable=queryable,
        underlying_status=status,
    )


def _context(**updates: object) -> RunningInvocationRecoveryContextSchema:
    values = {
        "task_id": "task_1",
        "run_id": "run_1",
        "input_snapshot_version": "snapshot_1",
        "active_execution_ids": ["exec_compute"],
        "active_attempt_ids": ["attempt_compute"],
    }
    values.update(updates)
    return RunningInvocationRecoveryContextSchema(**values)


def test_crash_recovery_replays_events_after_latest_checkpoint(tmp_path) -> None:
    event_log = InMemoryDomainEventLog()
    gateway = StateGateway(event_log=event_log, clock=lambda: NOW)
    checkpointed = gateway.commit(
        _state(),
        _patch("patch_compute", "compute", "artifact://evidence/compute"),
    ).state
    checkpoints = JsonCheckpointStore(tmp_path / "checkpoints")
    checkpoints.save(checkpointed)
    latest = gateway.commit(
        checkpointed,
        _patch("patch_storage", "storage", "artifact://evidence/storage"),
    ).state

    recovered = EventReplayRecoveryService(
        checkpoints=checkpoints,
        event_log=event_log,
        state_gateway=gateway,
    ).recover("task_1", "run_1")

    assert recovered == latest
    assert recovered.state_meta.event_sequence == 2
    assert recovered.section_states["storage"].research.evidence_package_ref == (
        "artifact://evidence/storage"
    )


@pytest.mark.parametrize(
    ("invocation", "context", "expected"),
    [
        (
            _running(result_persisted=True),
            _context(),
            InvocationRecoveryAction.SUPPLEMENT_EVENT,
        ),
        (
            _running(
                queryable=True,
                status=UnderlyingInvocationStatus.COMPLETED,
            ),
            _context(),
            InvocationRecoveryAction.LOAD_AND_COMMIT,
        ),
        (
            _running(
                queryable=True,
                status=UnderlyingInvocationStatus.RUNNING,
            ),
            _context(),
            InvocationRecoveryAction.RESUME_MONITORING,
        ),
        (
            _running(
                queryable=True,
                status=UnderlyingInvocationStatus.NOT_FOUND,
            ),
            _context(),
            InvocationRecoveryAction.IDEMPOTENT_RETRY,
        ),
        (
            _running(),
            _context(active_attempt_ids=["new_attempt"]),
            InvocationRecoveryAction.DISCARD_STALE,
        ),
        (
            _running(),
            _context(input_snapshot_version="snapshot_2"),
            InvocationRecoveryAction.DISCARD_STALE,
        ),
        (
            _running(),
            _context(draining=True),
            InvocationRecoveryAction.DO_NOT_RESUME,
        ),
    ],
)
def test_running_invocation_recovery_is_mutually_exclusive(
    invocation: RunningInvocationRecoveryInputSchema,
    context: RunningInvocationRecoveryContextSchema,
    expected: InvocationRecoveryAction,
) -> None:
    decision = RunningInvocationRecoveryService().decide(invocation, context)

    assert decision.action == expected
    assert decision.starts_new_call is (
        expected == InvocationRecoveryAction.IDEMPOTENT_RETRY
    )
    if decision.starts_new_call:
        assert decision.idempotency_key == invocation.idempotency_key


def test_human_review_resume_validates_decision_and_uses_minimum_stage() -> None:
    state = _state()
    state.human_reviews["review_1"] = HumanReviewStateSchema(
        review_id="review_1",
        scope="section",
        section_id="compute",
        status="waiting",
        request_ref="artifact://human-review/request/1",
    )
    result = HumanReviewResumeService().decide(
        state,
        HumanReviewResumeRequestSchema(
            task_id="task_1",
            review_id="review_1",
            input_snapshot_version="snapshot_1",
            decision_ref="artifact://human-review/decision/1",
            trigger_kind=ResumeTriggerKind.DRAFT_REVISED,
        ),
    )

    assert result.review.status == "resolved"
    assert result.review.decision_ref == "artifact://human-review/decision/1"
    assert result.resume_decision.stage == ResumeStage.VERIFIER
    assert result.resume_decision.affected_section_ids == ["compute"]
    assert state.human_reviews["review_1"].status == "waiting"

    with pytest.raises(HumanReviewResumeError, match="stale"):
        HumanReviewResumeService().decide(
            state,
            HumanReviewResumeRequestSchema(
                task_id="task_1",
                review_id="review_1",
                input_snapshot_version="snapshot_0",
                decision_ref="artifact://human-review/decision/2",
                trigger_kind=ResumeTriggerKind.DRAFT_REVISED,
            ),
        )
