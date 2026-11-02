from __future__ import annotations

from pathlib import Path

import test_gate_v2_pre_write_authority_static as gate_static
import test_multi_agent_v3_artifact_runtime as artifact_tests
import test_multi_agent_v3_budget_runtime as budget_tests
import test_multi_agent_v3_claim_verification_runtime as claim_tests
import test_multi_agent_v3_lifecycle_sif02_authority_static as lifecycle_static
import test_multi_agent_v3_memory_runtime as memory_tests
import test_multi_agent_v3_model_tool_runtime as model_tool_tests
import test_multi_agent_v3_paragraph_runtime as paragraph_tests
import test_multi_agent_v3_prewrite_gate_integration as prewrite_tests
import test_multi_agent_v3_storage_lifecycle as storage_tests
import test_multi_agent_v3_trace_typed_correlation as trace_tests
import test_post_write_release_authority_fence as release_fence
import test_multi_agent_v3_workflow_recovery as workflow_tests


FORBIDDEN_AUTHORITY_ATTACK_COUNT = 10


def test_b12_state_projection_cannot_set_active_attempt_authority() -> None:
    lifecycle_static.test_static_s05_state_attempt_is_projection_when_lifecycle_service_is_injected()


def test_b12_checkpoint_paragraph_projection_cannot_override_canonical_artifact(
    tmp_path: Path,
) -> None:
    paragraph_tests.test_graph_section_snapshot_ignores_checkpoint_paragraph_projection(
        tmp_path / "checkpoint-paragraph"
    )


def test_b12_memory_projection_cannot_resurrect_or_override_source_truth() -> None:
    memory_tests.test_active_query_never_uses_stale_memory_as_checkpoint_like_fallback()
    memory_tests.test_reconcile_cannot_reconstruct_missing_canonical_source_from_memory_ref()


def test_b12_trace_cannot_override_gate_truth() -> None:
    gate_static.test_static_09_gate_calculators_do_not_read_trace_as_truth_input()
    trace_tests.test_gate_route_ref_is_typed_correlation_not_trace_authority()


def test_b12_legacy_generation_report_hard_gate_release_authority_is_unreachable(
    tmp_path: Path,
) -> None:
    release_fence.test_target_release_imports_no_legacy_release_or_hard_gate_authority()
    workflow_tests.test_w13_single_section_live_cutover_uses_context_paragraph_claim_owners(
        tmp_path / "legacy-release-authority"
    )


def test_b12_provider_or_technical_failure_cannot_impersonate_gate_fallback(
    tmp_path: Path,
) -> None:
    model_tool_tests.test_availability_policy_rejects_budget_or_unknown_origin()
    prewrite_tests.test_writer_technical_failure_never_becomes_gate_fallback_or_exact_success_ref(
        tmp_path / "writer-technical-failure"
    )


def test_b12_compatibility_budget_projection_cannot_manufacture_capacity(
    tmp_path: Path,
) -> None:
    budget_tests.test_returned_state_projection_cannot_manufacture_capacity(
        tmp_path / "budget-state-projection"
    )
    budget_tests.test_checkpoint_projection_has_no_canonical_restore_authority(
        tmp_path / "budget-checkpoint-projection"
    )
    budget_tests.test_workflow_budget_cannot_override_canonical_budget(
        tmp_path / "workflow-budget-projection"
    )


def test_b12_old_claim_revision_cannot_roll_current_head_backward(
    tmp_path: Path,
) -> None:
    claim_tests.test_claim_head_expected_revision_is_strict_cas(
        tmp_path / "claim-head-cas"
    )
    claim_tests.test_reverification_same_semantics_gets_new_result_and_historical_replay_no_rollback(
        tmp_path / "claim-history-no-rollback"
    )


def test_b12_old_artifact_promotion_cannot_reactivate_historical_version(
    tmp_path: Path,
) -> None:
    artifact_tests.test_promotion_supersession_and_old_promotion_replay_do_not_roll_back_head(
        tmp_path / "artifact-head-no-rollback"
    )


def test_b12_gc_plan_before_release_protection_cannot_delete_newly_protected_object() -> None:
    storage_tests.test_w14_release_protection_added_after_plan_revokes_delete()


def test_b12_forbidden_authority_takeover_manifest_is_exact() -> None:
    attack_ids = (
        "state_attempt_authority",
        "checkpoint_paragraph_authority",
        "memory_source_resurrection",
        "trace_gate_authority",
        "legacy_release_authority",
        "provider_failure_gate_fallback",
        "compatibility_budget_authority",
        "old_claim_head_rollback",
        "old_artifact_head_rollback",
        "stale_gc_plan_release_race",
    )
    assert len(attack_ids) == FORBIDDEN_AUTHORITY_ATTACK_COUNT
    assert len(set(attack_ids)) == FORBIDDEN_AUTHORITY_ATTACK_COUNT
