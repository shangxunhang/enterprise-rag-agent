import pytest
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import AttemptLifecycleStatus
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError
import test_multi_agent_v3_workflow_recovery as w13


def test_completed_source_read_does_not_reactivate_producer(tmp_path):
    _, lifecycle, _, _, _, workflow, _ = w13._w13_runtime(tmp_path)
    projection = workflow.ensure_section_plan(task_id="task-w13", run_id="run-w13",
        document_plan=w13._w13_document_plan(), structured_facts=w13._w13_facts())
    lineage, _ = workflow.ensure_section_execution_prerequisites(projection.section_plan_bindings[0])
    completed = lifecycle.transition_attempt(lineage.attempt_id, lineage.attempt_lifecycle_revision,
        AttemptLifecycleStatus.COMPLETED)
    before = lifecycle.path.read_bytes()
    read = lifecycle.resolve_read_lineage(lineage.run_id, lineage.attempt_scope_ref,
        expected_attempt_id=lineage.attempt_id)
    assert read.attempt_status is AttemptLifecycleStatus.COMPLETED
    assert read.attempt_lifecycle_revision == completed.lifecycle_revision
    with pytest.raises(LifecycleCurrentnessError):
        lifecycle.assert_attempt_committable(run_id=read.run_id, attempt_scope_ref=read.attempt_scope_ref,
            attempt_id=read.attempt_id, input_snapshot_version=read.input_snapshot_version)
    assert lifecycle.path.read_bytes() == before


def test_superseded_producer_is_never_read_current(tmp_path):
    _, lifecycle, _, _, _, workflow, _ = w13._w13_runtime(tmp_path)
    projection = workflow.ensure_section_plan(task_id="task-w13", run_id="run-w13",
        document_plan=w13._w13_document_plan(), structured_facts=w13._w13_facts())
    lineage, _ = workflow.ensure_section_execution_prerequisites(projection.section_plan_bindings[0])
    replacement = lifecycle.open_attempt(lineage.run_id, lineage.attempt_scope_ref, "350-new-snapshot")
    lifecycle.activate_attempt(replacement.identity.attempt_id, replacement.lifecycle_revision)
    before = lifecycle.path.read_bytes()
    for run, scope, attempt in [(lineage.run_id, lineage.attempt_scope_ref, lineage.attempt_id),
            ("wrong-run", lineage.attempt_scope_ref, replacement.identity.attempt_id),
            (lineage.run_id, "section:wrong-section", replacement.identity.attempt_id)]:
        with pytest.raises(LifecycleCurrentnessError):
            lifecycle.resolve_read_lineage(run, scope, expected_attempt_id=attempt)
    assert lifecycle.path.read_bytes() == before
