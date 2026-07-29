"""Architecture Spec v1.0 Phase 2 基础设施验收测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    BudgetVectorSchema,
    MultiAgentSharedStateSchema,
    NormalizedToolResultSchema,
    PatchOperation,
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
    TaskBudgetLimitsSchema,
    TaskContextStateSchema,
    ToolProvenanceSchema,
    ToolResultErrorSchema,
    ToolResultStatus,
    WorkflowErrorCategory,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ArtifactIntegrityError,
    BudgetManager,
    FileArtifactStore,
    InMemoryDomainEventLog,
    JsonCheckpointStore,
    JsonlDomainEventLog,
    JsonlToolExecutionStore,
    StateGateway,
    StateGatewayError,
    TaskBudgetExhausted,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _section(section_id: str, order: int) -> SectionStateSchema:
    return SectionStateSchema(
        section_id=section_id,
        section_title=f"章节 {order}",
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


def _section_patch(
    *,
    request_id: str,
    section_id: str,
    target_domain: str = "section.research",
    role: StateActorRole = StateActorRole.RESEARCH,
    expected_version: int = 0,
    path: str = "/evidence_package_ref",
    value: object = "artifact://evidence/1",
    attempt_id: str | None = None,
    execution_id: str | None = None,
) -> StatePatchRequestSchema:
    return StatePatchRequestSchema(
        request_id=request_id,
        task_id="task_1",
        section_id=section_id,
        actor=StatePatchActorSchema(
            agent_role=role,
            execution_id=execution_id or f"exec_{section_id}",
            attempt_id=attempt_id or f"attempt_{section_id}",
        ),
        input_snapshot_version="snapshot_1",
        expected_domain_version=expected_version,
        target_domain=target_domain,
        operations=[
            StatePatchOperationSchema(
                operation=PatchOperation.REPLACE,
                path=path,
                value=value,
            )
        ],
        reason_code="test_commit",
    )


def _limits(**updates: int) -> TaskBudgetLimitsSchema:
    values = {
        "max_model_calls": 2,
        "max_tool_calls": 3,
        "max_total_replans": 1,
        "max_total_rewrites": 2,
        "max_document_repair_tasks": 1,
        "max_wall_clock_seconds": 60,
    }
    values.update(updates)
    return TaskBudgetLimitsSchema(**values)


def test_state_gateway_allows_parallel_section_domains_without_conflict() -> None:
    event_log = InMemoryDomainEventLog()
    gateway = StateGateway(event_log=event_log, clock=lambda: NOW)
    first = gateway.commit(
        _state(),
        _section_patch(request_id="patch_compute", section_id="compute"),
    )
    second = gateway.commit(
        first.state,
        _section_patch(
            request_id="patch_storage",
            section_id="storage",
            value="artifact://evidence/2",
        ),
    )

    assert first.committed is True
    assert second.state.domain_versions.sections["compute"].research == 1
    assert second.state.domain_versions.sections["storage"].research == 1
    assert second.state.section_states["compute"].research.evidence_package_ref == (
        "artifact://evidence/1"
    )
    assert second.state.section_states["storage"].research.evidence_package_ref == (
        "artifact://evidence/2"
    )
    assert [event.sequence for event in event_log.list_events("task_1", "run_1")] == [
        1,
        2,
    ]


def test_state_gateway_rejects_same_domain_old_version() -> None:
    event_log = InMemoryDomainEventLog()
    gateway = StateGateway(event_log=event_log, clock=lambda: NOW)
    first = gateway.commit(
        _state(),
        _section_patch(request_id="patch_1", section_id="compute"),
    )

    with pytest.raises(StateGatewayError) as captured:
        gateway.commit(
            first.state,
            _section_patch(
                request_id="patch_2",
                section_id="compute",
                expected_version=0,
                path="/evidence_gap_count",
                value=1,
            ),
        )

    assert captured.value.workflow_error.category == (
        WorkflowErrorCategory.STATE_VERSION_CONFLICT
    )
    assert len(event_log.list_events("task_1", "run_1")) == 1


def test_state_gateway_rejects_stale_attempt_and_wrong_owner() -> None:
    gateway = StateGateway(
        event_log=InMemoryDomainEventLog(),
        clock=lambda: NOW,
    )
    with pytest.raises(StateGatewayError) as stale:
        gateway.commit(
            _state(),
            _section_patch(
                request_id="patch_stale",
                section_id="compute",
                attempt_id="old_attempt",
            ),
        )
    assert stale.value.workflow_error.category == WorkflowErrorCategory.STALE_EXECUTION

    with pytest.raises(StateGatewayError) as denied:
        gateway.commit(
            _state(),
            _section_patch(
                request_id="patch_denied",
                section_id="compute",
                role=StateActorRole.WRITER,
            ),
        )
    assert denied.value.workflow_error.category == (
        WorkflowErrorCategory.PERMISSION_VIOLATION
    )


def test_state_gateway_rejects_invalid_section_transition() -> None:
    gateway = StateGateway(
        event_log=InMemoryDomainEventLog(),
        clock=lambda: NOW,
    )
    with pytest.raises(StateGatewayError, match="invalid section transition"):
        gateway.commit(
            _state(),
            _section_patch(
                request_id="patch_transition",
                section_id="compute",
                target_domain="section.execution",
                role=StateActorRole.ROUTER,
                path="/status",
                value=SectionStatus.ACCEPTED,
            ),
        )


def test_state_gateway_is_idempotent_by_patch_request_id() -> None:
    event_log = InMemoryDomainEventLog()
    gateway = StateGateway(event_log=event_log, clock=lambda: NOW)
    patch = _section_patch(request_id="patch_1", section_id="compute")
    first = gateway.commit(_state(), patch)
    replay = gateway.commit(first.state, patch)

    assert replay.committed is False
    assert replay.event.event_id == first.event.event_id
    assert replay.state == first.state
    assert len(event_log.list_events("task_1", "run_1")) == 1


def test_state_gateway_replays_event_when_checkpoint_precedes_commit() -> None:
    event_log = InMemoryDomainEventLog()
    gateway = StateGateway(event_log=event_log, clock=lambda: NOW)
    original_checkpoint = _state()
    patch = _section_patch(request_id="patch_1", section_id="compute")
    committed = gateway.commit(original_checkpoint, patch)

    replay = gateway.commit(original_checkpoint, patch)

    assert replay.committed is False
    assert replay.event == committed.event
    assert replay.state == committed.state
    assert replay.state.state_meta.event_sequence == 1


def test_state_gateway_does_not_publish_state_when_event_append_fails() -> None:
    class FailingEventLog(InMemoryDomainEventLog):
        def append(self, event):  # type: ignore[no-untyped-def]
            raise OSError("disk unavailable")

    original = _state()
    gateway = StateGateway(event_log=FailingEventLog(), clock=lambda: NOW)
    with pytest.raises(StateGatewayError) as captured:
        gateway.commit(
            original,
            _section_patch(request_id="patch_1", section_id="compute"),
        )

    assert captured.value.workflow_error.error_code == "DOMAIN_EVENT_COMMIT_FAILED"
    assert original.domain_versions.sections == {}
    assert original.section_states["compute"].research.evidence_package_ref is None


def test_jsonl_event_log_recovers_events_across_instances(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    first_log = JsonlDomainEventLog(path)
    result = StateGateway(event_log=first_log, clock=lambda: NOW).commit(
        _state(),
        _section_patch(request_id="patch_1", section_id="compute"),
    )

    second_log = JsonlDomainEventLog(path)
    restored = second_log.list_events("task_1", "run_1")
    assert restored == [result.event]


def test_artifact_store_is_immutable_and_detects_tampering(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    reference = store.put_json(
        {"paragraph": "服务器数量为 8 台"},
        artifact_type="paragraph",
        schema_version="paragraph_v1",
        created_by="writer",
        task_id="task_1",
        section_id="compute",
        attempt_id="attempt_compute",
        input_snapshot_version="snapshot_1",
        artifact_ref="artifact://task_1/paragraph/fixed",
    )

    assert store.get_json(
        reference.artifact_ref,
        task_id="task_1",
        section_id="compute",
        attempt_id="attempt_compute",
        input_snapshot_version="snapshot_1",
    ) == {"paragraph": "服务器数量为 8 台"}

    with pytest.raises(ArtifactIntegrityError, match="different content"):
        store.put_json(
            {"paragraph": "服务器数量为 6 台"},
            artifact_type="paragraph",
            schema_version="paragraph_v1",
            created_by="writer",
            task_id="task_1",
            section_id="compute",
            attempt_id="attempt_compute",
            input_snapshot_version="snapshot_1",
            artifact_ref=reference.artifact_ref,
        )

    target = store._path_for_ref(reference.artifact_ref)
    envelope = json.loads(target.read_text(encoding="utf-8"))
    envelope["reference"]["content_hash"] = "sha256:tampered"
    target.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="hash mismatch"):
        store.get_bytes(reference.artifact_ref, task_id="task_1")


def test_tool_execution_store_is_idempotent_and_rejects_collision(tmp_path) -> None:
    store = JsonlToolExecutionStore(tmp_path / "tool-results.jsonl")
    result = NormalizedToolResultSchema(
        call_id="call_1",
        status=ToolResultStatus.SUCCESS,
        data_type="excel_fact_query",
        data={"quantity": 8},
        provenance=ToolProvenanceSchema(
            source_refs=["fact://server/count"],
            input_snapshot_version="snapshot_1",
        ),
    )

    assert store.put(result) is True
    assert store.put(result) is False
    assert store.get("call_1") == result

    conflicting = NormalizedToolResultSchema(
        call_id="call_1",
        status=ToolResultStatus.STALE,
        data_type="excel_fact_query",
        provenance=ToolProvenanceSchema(
            input_snapshot_version="snapshot_0"
        ),
        error=ToolResultErrorSchema(error_code="STALE_RESULT"),
    )
    with pytest.raises(ValueError, match="identity collision"):
        store.put(conflicting)


def test_budget_manager_reserves_atomically_and_enters_draining() -> None:
    manager = BudgetManager(
        task_id="task_1",
        limits=_limits(),
        clock=lambda: NOW,
    )
    manager.reserve("r1", BudgetVectorSchema(model_calls=1))
    manager.reserve("r2", BudgetVectorSchema(model_calls=1))

    with pytest.raises(TaskBudgetExhausted) as captured:
        manager.reserve("r3", BudgetVectorSchema(model_calls=1))
    assert captured.value.resource == "model_calls"

    snapshot = manager.snapshot()
    assert snapshot.reserved.model_calls == 2
    assert snapshot.used.model_calls == 0
    assert snapshot.draining is True


def test_budget_manager_settles_actual_usage_and_releases_unused() -> None:
    manager = BudgetManager(
        task_id="task_1",
        limits=_limits(),
        clock=lambda: NOW,
    )
    manager.reserve(
        "batch_1",
        BudgetVectorSchema(model_calls=2, tool_calls=2),
    )
    manager.settle(
        "batch_1",
        BudgetVectorSchema(model_calls=1, tool_calls=2),
    )
    manager.reserve("batch_2", BudgetVectorSchema(tool_calls=1))
    manager.release("batch_2")

    snapshot = manager.snapshot()
    assert snapshot.reserved == BudgetVectorSchema()
    assert snapshot.used == BudgetVectorSchema(model_calls=1, tool_calls=2)


def test_budget_manager_enforces_wall_clock() -> None:
    current = [NOW]
    manager = BudgetManager(
        task_id="task_1",
        limits=_limits(max_wall_clock_seconds=10),
        clock=lambda: current[0],
    )
    current[0] = NOW + timedelta(seconds=10)

    with pytest.raises(TaskBudgetExhausted) as captured:
        manager.reserve("late", BudgetVectorSchema(tool_calls=1))
    assert captured.value.resource == "wall_clock_seconds"
    assert manager.snapshot().draining is True


def test_checkpoint_store_round_trips_latest_state_and_detects_tampering(
    tmp_path,
) -> None:
    checkpoint_store = JsonCheckpointStore(tmp_path / "checkpoints")
    state = _state()
    first_path = checkpoint_store.save(state)
    assert checkpoint_store.save(state) == first_path
    assert checkpoint_store.load_latest("task_1", "run_1") == state

    envelope = json.loads(first_path.read_text(encoding="utf-8"))
    envelope["state"]["state_meta"]["task_id"] = "tampered"
    first_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        checkpoint_store.load_latest("task_1", "run_1")
