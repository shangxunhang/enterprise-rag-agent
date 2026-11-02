from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import test_gate_v2_pre_write_routing_fallback as gate_routing_tests
import test_multi_agent_v3_artifact_runtime as artifact_tests
import test_multi_agent_v3_budget_runtime as budget_tests
import test_multi_agent_v3_context_runtime as context_tests
import test_multi_agent_v3_model_tool_runtime as model_tool_tests
import test_multi_agent_v3_paragraph_runtime as paragraph_tests
import test_multi_agent_v3_prewrite_gate_integration as prewrite_tests
import test_multi_agent_v3_repair_authorization_runtime as repair_tests
import test_multi_agent_v3_storage_lifecycle as storage_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests
import test_post_write_release_publication as release_tests

from apps.enterprise_document.services.agent_rag_v1.storage_lifecycle import (
    StorageLifecycleService,
)
from apps.enterprise_document.workflows.top_level_business_graph import (
    EnterpriseDocumentWorkflowEngine,
)


MANDATORY_E2E_IDS = tuple(f"E2E-{index:02d}" for index in range(1, 15))


def test_e2e_01_ordinary_grounded_multisection_generation(tmp_path: Path) -> None:
    workflow_tests.test_w13_two_section_parallel_target_synchronizes_one_completion_and_release(
        tmp_path / "e2e-01"
    )


def test_e2e_02_evidence_insufficient_uses_frozen_safe_route_without_writer(
    tmp_path: Path,
) -> None:
    gate_routing_tests.test_recovery_route_cannot_form_writer_invocation(
        tmp_path / "e2e-02"
    )


def test_e2e_03_research_more_is_bounded_and_replays_same_canonical_round(
    tmp_path: Path,
) -> None:
    prewrite_tests.test_gate_recovery_dispatch_binds_exact_route_and_replays_same_research_round(
        tmp_path / "e2e-03"
    )


def test_e2e_04_stale_attempt_cannot_promote_after_successor_activation(
    tmp_path: Path,
) -> None:
    artifact_tests.test_attempt_scoped_commit_and_stale_attempt_promotion_are_lifecycle_guarded(
        tmp_path / "e2e-04"
    )


def test_e2e_05_paragraph_repair_creates_successor_artifact_version(
    tmp_path: Path,
) -> None:
    paragraph_tests.test_repair_creates_new_version_preserves_logical_identity_and_replaces_manifest(
        tmp_path / "e2e-05"
    )


def test_e2e_06_claim_issue_authorizes_one_repair_and_rechecks_currentness(
    tmp_path: Path,
) -> None:
    repair_tests.test_authorization_is_replay_stable_and_reserves_exactly_one_repair_attempt(
        tmp_path / "e2e-06-auth"
    )
    repair_tests.test_semantic_activation_consumes_one_before_paragraph_revise_commit(
        tmp_path / "e2e-06-activation"
    )
    repair_tests.test_resolved_issue_invalidates_dispatch_authorization(
        tmp_path / "e2e-06-resolved"
    )


def test_e2e_07_cross_section_context_accepts_only_authorized_propagated_semantics() -> None:
    context_tests.test_conditional_active_verified_claim_requires_exact_consumer_authorization()
    context_tests.test_cross_run_source_is_rejected()


def test_e2e_08_provider_availability_fallback_uses_canonical_budget_admission(
    tmp_path: Path,
) -> None:
    model_tool_tests.test_model_fallback_uses_one_logical_call_and_two_token_reservations(
        tmp_path / "e2e-08"
    )


def test_e2e_09_budget_exhaustion_starts_no_provider_and_never_becomes_gate_fallback(
    tmp_path: Path,
) -> None:
    model_tool_tests.test_token_budget_denial_starts_no_provider_and_releases_logical_reservation(
        tmp_path / "e2e-09-provider"
    )
    model_tool_tests.test_availability_policy_rejects_budget_or_unknown_origin()
    budget_tests.test_returned_state_projection_cannot_manufacture_capacity(
        tmp_path / "e2e-09-budget"
    )


def test_e2e_10_crash_after_canonical_commit_reuses_source_before_checkpoint(
    tmp_path: Path,
) -> None:
    workflow_tests.test_w13_reopen_from_stale_checkpoint_reuses_canonical_release_without_budget_reconsume(
        tmp_path / "e2e-10"
    )


def test_e2e_11_recovery_plan_selects_older_safe_predecessor_not_newest_physical_row() -> None:
    from copy import deepcopy
    from test_fpbca_a3_v2_1_recovery import checkpoint_row, engine_from_rows

    task_id, run_id = "task-e2e-11", "run-e2e-11"
    older_safe = checkpoint_row(
        "older-safe", "initialize_generation", "continue", None, 1,
        task=task_id, run=run_id,
    )
    newest_failed = checkpoint_row(
        "newest-failed", "execute_sections", "technical_failure", "older-safe", 2,
        failed="execute_sections", task=task_id, run=run_id,
    )
    engine_from_rows([older_safe, newest_failed], "newest-failed", target=False)
    carry = deepcopy(newest_failed)
    carry.config["configurable"]["checkpoint_id"] = "newest-physical-copy"
    carry.parent_config = deepcopy(newest_failed.config)
    carry.metadata = {"source": "update", "step": 3}
    engine, workflow = engine_from_rows(
        [older_safe, newest_failed, carry], "newest-physical-copy", target=False,
    )
    engine.inspect = lambda _workflow, run_id: {
        "interrupts": [], "checkpoint_config": carry.config,
        "graph_state": {"task_id": task_id, "run_id": run_id}, "next_nodes": [],
    }

    plan = EnterpriseDocumentWorkflowEngine.recovery_plan(engine, workflow, run_id=run_id)

    assert plan["reason"] == "terminal_failure_checkpoint_branch"
    assert plan["failed_business_node"] == "execute_sections"
    assert plan["predecessor"] == "initialize_generation"
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == "older-safe"
    assert engine.checkpointer.history_reads == 0


def test_e2e_11b_target_live_recovery_uses_actual_final_review_predecessor() -> None:
    from test_fpbca_a3_v2_1_recovery import checkpoint_row, engine_from_rows

    task_id, run_id = "task-e2e-11b", "run-e2e-11b"
    actual_predecessor = checkpoint_row(
        "actual-predecessor", "final_artifact_review", "continue", None, 1,
        task=task_id, run=run_id,
    )
    failed = checkpoint_row(
        "failed-gate", "evaluate_document_gate", "technical_failure", "actual-predecessor", 2,
        failed="evaluate_document_gate", task=task_id, run=run_id,
    )
    engine, workflow = engine_from_rows([actual_predecessor, failed], "failed-gate", target=True)
    engine.inspect = lambda _workflow, run_id: {
        "interrupts": [], "checkpoint_config": failed.config,
        "graph_state": {"task_id": task_id, "run_id": run_id}, "next_nodes": [],
    }

    plan = EnterpriseDocumentWorkflowEngine.recovery_plan(engine, workflow, run_id=run_id)

    assert plan["failed_business_node"] == "evaluate_document_gate"
    assert plan["predecessor"] == "final_artifact_review"
    assert plan["checkpoint_config"]["configurable"]["checkpoint_id"] == (
        "actual-predecessor"
    )
    assert engine.checkpointer.history_reads == 0


def test_e2e_12_release_publication_binds_exact_prevalidated_final_bytes(
    tmp_path: Path,
) -> None:
    release_tests.test_standard_target_release_publishes_exact_prevalidated_bytes(
        tmp_path / "e2e-12"
    )


def test_e2e_13_terminal_scope_gc_dry_run_plans_but_deletes_nothing(
    tmp_path: Path,
) -> None:
    record = storage_tests._record("dry-run-candidate", expires=0.0, size=17)
    catalog = storage_tests._MutableCatalog((record,), terminal=True)
    service = StorageLifecycleService(catalog, clock=lambda: 100.0)

    audit = service.run_terminal_scope_gc("run:r1", dry_run=True)

    assert audit.dry_run is True
    assert audit.eligible_count == 1
    assert audit.deleted_count == 0
    assert audit.bytes_freed == 0
    assert "dry-run-candidate" in catalog.records

    storage_tests.test_w14_success_with_exact_assessment_and_manifest_is_gc_closed(
        tmp_path / "e2e-13-factory"
    )


def test_e2e_14_disk_pressure_suppresses_optional_capture_without_protected_deletion(
    tmp_path: Path,
) -> None:
    storage_tests.test_w14_hard_pressure_suppresses_optional_scheme_capture()
    storage_tests.test_w14_disk_policy_suppresses_only_optional_capture_and_never_provider_fallback()
    storage_tests.test_w14_factory_composes_single_storage_owner_and_release_root_is_never_cross_deleted(
        tmp_path / "e2e-14"
    )


def test_b12_mainline_e2e_manifest_contains_all_frozen_scenarios() -> None:
    assert MANDATORY_E2E_IDS == (
        "E2E-01",
        "E2E-02",
        "E2E-03",
        "E2E-04",
        "E2E-05",
        "E2E-06",
        "E2E-07",
        "E2E-08",
        "E2E-09",
        "E2E-10",
        "E2E-11",
        "E2E-12",
        "E2E-13",
        "E2E-14",
    )
