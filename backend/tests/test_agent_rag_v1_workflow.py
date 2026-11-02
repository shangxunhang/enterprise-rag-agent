"""Architecture Spec v1.0 Phase 3 工作流骨架验收测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ChangeSetSchema,
    ResumeRequestSchema,
    ResumeStage,
    ResumeTriggerKind,
    RouterAction,
    RouterSignalKind,
    RouterSignalSchema,
    SectionBindingSnapshotSchema,
    SectionImpactAction,
    SectionStatus,
    TaskStatus,
)
from apps.enterprise_document.schemas.scheme_writer import (
    DocumentPlanSchema,
    SectionPlanSchema,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    ImpactAnalyzer,
    ResumeRouter,
    SectionDagError,
    SectionDagScheduler,
    SectionGroupAggregator,
    SupervisorStateAdapter,
    VersionPolicyService,
    WorkflowRouter,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _plan() -> DocumentPlanSchema:
    return DocumentPlanSchema(
        plan_id="plan_1",
        document_id="document_1",
        document_title="建设方案",
        created_at=NOW.isoformat(),
        sections=[
            SectionPlanSchema(
                section_id="compute",
                section_title="计算资源",
                section_order=1,
                execution_group="technical",
                can_run_in_parallel=True,
                requires_global_context=False,
                metadata={"strategy": "technical_generate"},
            ),
            SectionPlanSchema(
                section_id="storage",
                section_title="存储资源",
                section_order=2,
                execution_group="technical",
                can_run_in_parallel=True,
                requires_global_context=False,
                metadata={"strategy": "technical_generate"},
            ),
            SectionPlanSchema(
                section_id="summary",
                section_title="方案总结",
                section_order=3,
                metadata={"strategy": "rag_generate"},
            ),
        ],
    )


def _state():
    return SupervisorStateAdapter(clock=lambda: NOW).initialize(
        task_id="task_1",
        run_id="run_1",
        input_snapshot_version="snapshot_1",
        task_context_ref="artifact://task-context/1",
        source_snapshot_ref="artifact://source-snapshot/1",
        fact_snapshot_ref="artifact://facts/1",
        template_schema_ref="artifact://template/1",
        source_inventory_ref="artifact://inventory/1",
        plan=_plan(),
    )


def test_supervisor_adapter_initializes_state_without_new_supervisor() -> None:
    state = _state()

    assert state.task_context.task_status == TaskStatus.PENDING
    assert list(state.section_states) == ["compute", "storage", "summary"]
    assert state.section_states["compute"].execution.status == SectionStatus.PLANNED
    assert state.section_states["compute"].can_run_in_parallel is True
    assert state.execution_plan.plan_version == 0


def test_dag_scheduler_selects_safe_parallel_group_deterministically() -> None:
    batch = SectionDagScheduler().next_batch(
        _state(),
        max_parallel_sections=4,
    )

    assert batch.section_ids == ["compute", "storage"]
    assert batch.blocked_section_ids == []


def test_dag_scheduler_waits_for_hard_dependency_and_detects_cycles() -> None:
    state = _state()
    state.section_states["summary"].execution.hard_dependencies = ["compute"]

    batch = SectionDagScheduler().next_batch(
        state,
        max_parallel_sections=4,
    )
    assert batch.section_ids == ["compute", "storage"]

    state.section_states["compute"].execution.hard_dependencies = ["summary"]
    with pytest.raises(SectionDagError, match="cycle"):
        SectionDagScheduler().validate(state)


def test_dag_scheduler_does_not_start_new_work_while_draining() -> None:
    state = _state()
    state.runtime_control.draining = True

    assert SectionDagScheduler().next_batch(
        state,
        max_parallel_sections=4,
    ).section_ids == []


def test_workflow_router_honors_priority_before_business_actions() -> None:
    decision = WorkflowRouter().decide(
        _state(),
        [
            RouterSignalSchema(
                signal_id="rewrite",
                kind=RouterSignalKind.REWRITE,
                section_id="compute",
            ),
            RouterSignalSchema(
                signal_id="budget",
                kind=RouterSignalKind.TASK_BUDGET_EXHAUSTED,
            ),
            RouterSignalSchema(
                signal_id="snapshot",
                kind=RouterSignalKind.INPUT_SNAPSHOT_UPDATED,
            ),
        ],
    )

    assert decision is not None
    assert decision.action == RouterAction.DISCARD_STALE
    assert decision.selected_signal_id == "snapshot"
    assert decision.priority == 1
    assert decision.discarded_signal_ids == ["budget", "rewrite"]


def test_workflow_router_derives_runtime_draining_signal() -> None:
    state = _state()
    state.runtime_control.draining = True
    decision = WorkflowRouter().decide(
        state,
        [
            RouterSignalSchema(
                signal_id="accept",
                kind=RouterSignalKind.ACCEPT,
                section_id="compute",
            )
        ],
    )

    assert decision is not None
    assert decision.action == RouterAction.START_DRAINING
    assert decision.priority == 3


def test_section_group_aggregation_supports_partial_and_waiting_human() -> None:
    state = _state()
    state.section_states["compute"].execution.status = SectionStatus.ACCEPTED
    state.section_states["storage"].execution.status = SectionStatus.FAILED
    aggregator = SectionGroupAggregator()

    partial = aggregator.aggregate(
        state,
        group_id="technical",
        section_ids=["compute", "storage"],
    )
    assert partial.status == SectionStatus.PARTIALLY_ACCEPTED
    assert partial.deliverable_section_ids == ["compute"]

    state.section_states["storage"].execution.status = SectionStatus.WAITING_HUMAN
    blocked = aggregator.aggregate(
        state,
        group_id="technical",
        section_ids=["compute", "storage"],
    )
    assert blocked.status == SectionStatus.BLOCKED

    downgraded = aggregator.aggregate(
        state,
        group_id="technical",
        section_ids=["compute", "storage"],
        waiting_human_downgraded=True,
    )
    assert downgraded.status == SectionStatus.PARTIALLY_ACCEPTED


@pytest.mark.parametrize(
    ("trigger", "stage", "invalidated"),
    [
        (
            ResumeTriggerKind.EXCEL_CHANGED,
            ResumeStage.PLANNER,
            "execution_plan",
        ),
        (
            ResumeTriggerKind.NEW_MATERIAL,
            ResumeStage.RESEARCH,
            "section.research",
        ),
        (
            ResumeTriggerKind.EVIDENCE_READY_REWRITE,
            ResumeStage.WRITER,
            "section.writing",
        ),
        (
            ResumeTriggerKind.DRAFT_REVISED,
            ResumeStage.VERIFIER,
            "section.verification",
        ),
    ],
)
def test_resume_router_selects_minimum_stage(
    trigger,
    stage,
    invalidated,
) -> None:
    decision = ResumeRouter().decide(
        ResumeRequestSchema(
            task_id="task_1",
            section_ids=["compute"],
            trigger_kind=trigger,
        )
    )

    assert decision.stage == stage
    assert invalidated in decision.invalidated_domains


def test_impact_analyzer_limits_regeneration_and_propagates_reverify() -> None:
    bindings = [
        SectionBindingSnapshotSchema(
            section_id="compute",
            fact_refs=["fact_server_count"],
        ),
        SectionBindingSnapshotSchema(
            section_id="summary",
            hard_dependencies=["compute"],
        ),
        SectionBindingSnapshotSchema(section_id="storage"),
    ]
    analysis = ImpactAnalyzer().analyze(
        ChangeSetSchema(changed_fact_refs=["fact_server_count"]),
        bindings,
    )
    actions = {
        item.section_id: item.action for item in analysis.section_impacts
    }

    assert actions == {
        "compute": SectionImpactAction.REGENERATE,
        "storage": SectionImpactAction.REUSE,
        "summary": SectionImpactAction.REVERIFY_ONLY,
    }
    assert analysis.full_regeneration is False
    assert analysis.affected_ratio == pytest.approx(2 / 3)


def test_impact_analyzer_escalates_unscoped_major_change() -> None:
    bindings = [
        SectionBindingSnapshotSchema(section_id="compute"),
        SectionBindingSnapshotSchema(section_id="storage"),
    ]
    analysis = ImpactAnalyzer().analyze(
        ChangeSetSchema(excel_scope_changed=True),
        bindings,
    )

    assert analysis.major_change is True
    assert analysis.full_regeneration is True
    assert all(
        item.action == SectionImpactAction.REGENERATE
        for item in analysis.section_impacts
    )


def test_version_policy_creates_initial_minor_and_major_versions() -> None:
    service = VersionPolicyService()

    initial = service.decide(previous_version=None)
    minor = service.decide(
        previous_version=initial.next_version,
        changes=ChangeSetSchema(changed_evidence_topics=["network"]),
    )
    major = service.decide(
        previous_version=minor.next_version,
        changes=ChangeSetSchema(device_categories_changed=True),
    )

    assert initial.next_version == "v1.0"
    assert minor.next_version == "v1.1"
    assert major.next_version == "v2.0"
    assert major.change_level == "major"
