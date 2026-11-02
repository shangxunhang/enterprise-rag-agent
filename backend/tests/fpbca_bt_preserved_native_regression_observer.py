"""Finite pytest observer for existing BT24–27 native regression selectors.

No owner read/recovery is introduced. The same original methods receive the
same args once. Original returned DTOs/exceptions and actual raw JSONL custody
are emitted. Unknown input objects retain UNMEASURED diagnostics. Count scope
never extends to child/custom invokers or absent native boundaries.
"""
from __future__ import annotations
import dataclasses
from datetime import date,datetime
from enum import Enum
import functools
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import threading
import time
from contextlib import ExitStack
from unittest.mock import patch
import pytest

ROOT=Path(__file__).resolve().parents[2]
_HOOKS=[{'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.claim_new_run', 'line': 1033, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'claim_new_run'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.transition_run', 'line': 1101, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'transition_run'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.open_attempt', 'line': 1252, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'open_attempt'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.activate_attempt', 'line': 1368, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'activate_attempt'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.terminalize_noncancel_failure', 'line': 694, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'terminalize_noncancel_failure'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.resume_retryable_with_successor', 'line': 709, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'resume_retryable_with_successor'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.load_run_lifecycle', 'line': 1063, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'load_run_lifecycle'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.load_attempt', 'line': 1339, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'load_attempt'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.resolve_active_lineage', 'line': 1515, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'resolve_active_lineage'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.assert_attempt_committable', 'line': 1547, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'assert_attempt_committable'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.assert_run_committable', 'line': 1236, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'assert_run_committable'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'function': 'RunAttemptLifecycleService.budget_preimage', 'line': 950, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'class_name': 'RunAttemptLifecycleService', 'method': 'budget_preimage'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\running_invocation_recovery.py', 'sha256': '8dd44ed478dafe3b47e0af08bee1bd9583b1bead284cc95a33e4a1d4858c1a94', 'function': 'RunningInvocationRecoveryService.reconcile_writer', 'line': 99, 'module': 'apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery', 'class_name': 'RunningInvocationRecoveryService', 'method': 'reconcile_writer'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\running_invocation_recovery.py', 'sha256': '8dd44ed478dafe3b47e0af08bee1bd9583b1bead284cc95a33e4a1d4858c1a94', 'function': 'RunningInvocationRecoveryService.reconcile_support', 'line': 141, 'module': 'apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery', 'class_name': 'RunningInvocationRecoveryService', 'method': 'reconcile_support'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\running_invocation_recovery.py', 'sha256': '8dd44ed478dafe3b47e0af08bee1bd9583b1bead284cc95a33e4a1d4858c1a94', 'function': 'RunningInvocationRecoveryService.reconcile_verification', 'line': 158, 'module': 'apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery', 'class_name': 'RunningInvocationRecoveryService', 'method': 'reconcile_verification'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\running_invocation_recovery.py', 'sha256': '8dd44ed478dafe3b47e0af08bee1bd9583b1bead284cc95a33e4a1d4858c1a94', 'function': 'RunningInvocationRecoveryService.decide', 'line': 169, 'module': 'apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery', 'class_name': 'RunningInvocationRecoveryService', 'method': 'decide'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'function': 'SQLitePreWriteExecutionStore.get_writer_execution', 'line': 991, 'module': 'core.runtime.pre_write_execution_store', 'class_name': 'SQLitePreWriteExecutionStore', 'method': 'get_writer_execution'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'function': 'SQLitePreWriteExecutionStore.resolve_writer_execution', 'line': 1005, 'module': 'core.runtime.pre_write_execution_store', 'class_name': 'SQLitePreWriteExecutionStore', 'method': 'resolve_writer_execution'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'function': 'SQLitePreWriteExecutionStore.resolve_writer_generation_invocation', 'line': 1033, 'module': 'core.runtime.pre_write_execution_store', 'class_name': 'SQLitePreWriteExecutionStore', 'method': 'resolve_writer_generation_invocation'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\execution_control.py', 'sha256': 'dfb5b2a443caf25ca614d7f9a78eaace716cebde02e26fb943e3b7428f57cdb9', 'function': 'WorkflowExecutionControl.cancel', 'line': 131, 'module': 'core.runtime.execution_control', 'class_name': 'WorkflowExecutionControl', 'method': 'cancel'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\execution_control.py', 'sha256': 'dfb5b2a443caf25ca614d7f9a78eaace716cebde02e26fb943e3b7428f57cdb9', 'function': 'WorkflowExecutionControl.defer_external_commit', 'line': 144, 'module': 'core.runtime.execution_control', 'class_name': 'WorkflowExecutionControl', 'method': 'defer_external_commit'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\execution_control.py', 'sha256': 'dfb5b2a443caf25ca614d7f9a78eaace716cebde02e26fb943e3b7428f57cdb9', 'function': 'WorkflowExecutionControl.finalize_external_commits', 'line': 186, 'module': 'core.runtime.execution_control', 'class_name': 'WorkflowExecutionControl', 'method': 'finalize_external_commits'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\core\\runtime\\execution_control.py', 'sha256': 'dfb5b2a443caf25ca614d7f9a78eaace716cebde02e26fb943e3b7428f57cdb9', 'function': 'WorkflowExecutionControl.checkpoint', 'line': 288, 'module': 'core.runtime.execution_control', 'class_name': 'WorkflowExecutionControl', 'method': 'checkpoint'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'function': 'BudgetManager.canonical_state', 'line': 817, 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'class_name': 'BudgetManager', 'method': 'canonical_state'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'function': 'BudgetManager.reserve', 'line': 1037, 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'class_name': 'BudgetManager', 'method': 'reserve'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'function': 'BudgetManager.consume', 'line': 1165, 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'class_name': 'BudgetManager', 'method': 'consume'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'function': 'BudgetManager.release', 'line': 1268, 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'class_name': 'BudgetManager', 'method': 'release'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'function': 'BudgetManager._append_canonical_state_unlocked', 'line': 743, 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'class_name': 'BudgetManager', 'method': '_append_canonical_state_unlocked'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\agent\\runtime\\business_node_execution_runner.py', 'sha256': '31a352e2ab208307e458b5ea8451a5d691efc9a2bdb0437bd76f75aa08cc70c3', 'function': 'WorkflowNodeExecutionRunner.execute', 'line': 142, 'module': 'agent.runtime.business_node_execution_runner', 'class_name': 'WorkflowNodeExecutionRunner', 'method': 'execute'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\agent\\runtime\\business_node_execution_runner.py', 'sha256': '31a352e2ab208307e458b5ea8451a5d691efc9a2bdb0437bd76f75aa08cc70c3', 'function': 'WorkflowNodeExecutionRunner.cancel_active_workers', 'line': 74, 'module': 'agent.runtime.business_node_execution_runner', 'class_name': 'WorkflowNodeExecutionRunner', 'method': 'cancel_active_workers'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\agent\\runtime\\business_node_execution_runner.py', 'sha256': '31a352e2ab208307e458b5ea8451a5d691efc9a2bdb0437bd76f75aa08cc70c3', 'function': 'WorkflowNodeExecutionRunner.wait_for_idle', 'line': 81, 'module': 'agent.runtime.business_node_execution_runner', 'class_name': 'WorkflowNodeExecutionRunner', 'method': 'wait_for_idle'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\model_gateway\\model_invoker.py', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'function': 'ModelInvoker.invoke', 'line': 28, 'module': 'model_gateway.model_invoker', 'class_name': 'ModelInvoker', 'method': 'invoke'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\scheme_writer\\stage_service.py', 'sha256': '84b04ec77bdabb5ba6091a2bf2a4f807c325dfdc675cd3fb26d5891109f3776c', 'function': 'SchemeGenerationStageService.finalize_workflow_failure', 'line': 2990, 'module': 'apps.enterprise_document.services.scheme_writer.stage_service', 'class_name': 'SchemeGenerationStageService', 'method': 'finalize_workflow_failure'}, {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\workflows\\top_level_business_graph.py', 'sha256': 'a87c37a119affd909a3698bd5ead3214777b97cb993c5d78ce8d6b0bc354da71', 'function': 'EnterpriseDocumentWorkflowEngine._error_from_outcome', 'line': 866, 'module': 'apps.enterprise_document.workflows.top_level_business_graph', 'class_name': 'EnterpriseDocumentWorkflowEngine', 'method': '_error_from_outcome'}]
_SELECTOR_ROWS={'backend/tests/test_fpbca_bcd_budget_topup_application.py::test_native_approval_explicit_cancel_held_fence_decides_order': ['BT-26'], 'backend/tests/test_fpbca_bcd_budget_topup_application.py::test_native_approval_then_explicit_cancel_keeps_grant_but_forbids_document_dispatch': ['BT-26'], 'backend/tests/test_fpbca_bcd_budget_topup_application.py::test_native_resume_current_child_or_document_head_change_forbids_dispatch': ['BT-26'], 'backend/tests/test_fpbca_bcd_budget_topup_application.py::test_native_supervisor_ordinary_recovery_rejects_budget_before_failure_projection': ['BT-26'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_actual_stage_noncancel_failure_uses_composite_owner': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_actual_stage_user_cancel_keeps_existing_a4_path': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_complete_bad_frame_rejected_before_any_result_is_exposed': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_failure_0_1_n_and_finalizing_one_durable_frame': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_failure_full_set_cas_and_trigger_exactness': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_failure_replay_is_exact_and_changed_request_conflicts': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_finalizing_with_current_attempt_rejects_without_repair': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_frame_fsynced_before_lost_return_can_replay': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_open_before_snapshot_is_included_and_open_after_terminal_is_denied': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_resume_atomically_opens_pending_successor_and_old_never_regains_authority': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_resume_replay_conflict_and_mixed_v2_history': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_resume_with_unresolved_current_attempt_fails_closed': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_torn_composite_never_exposes_partial_members': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_composite.py::test_two_failure_contenders_have_one_cas_winner': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_transport.py::test_actual_stage_n_current_requires_exact_producer_trigger': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_transport.py::test_actual_top_level_error_mapping_retains_exact_dispatch_trigger': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_transport.py::test_actual_top_level_supervisor_task_keep_same_primary_composite_receipt': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_transport.py::test_composite_receipt_projects_exact_result_records_and_replays': ['BT-24'], 'backend/tests/test_fpbca_bcd_c1_transport.py::test_late_failure_preserves_sealed_canonical_run_without_new_event': ['BT-24'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_committed_support_recovery_never_evaluates_or_replays_commit': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_committed_verifier_recovery_requires_exact_result_and_revision': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r1_reserved_observation_does_not_authorize_retry_before_late_native_cas': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r2_reserved_reopen_and_absent_remain_indeterminate': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r3_dispatched_without_exact_result_remains_indeterminate': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r4_exact_durable_writer_success_is_recovered_without_redispatch': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r5_exact_terminal_technical_failure_is_durable_failure': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r6_exact_native_and_caller_identity_drift_is_rejected': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation.py::test_r8_successor_attempt_does_not_grant_old_unresolved_external_retry': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation_runtime.py::test_actual_application_unproved_reservation_blocks_before_recovery_event': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation_runtime.py::test_actual_owner_failure_is_read_before_c1_and_primary_error_remains': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation_runtime.py::test_actual_production_owner_wiring_and_current_native_result_read': ['BT-25'], 'backend/tests/test_fpbca_bcd_reconciliation_runtime.py::test_native_r7_exact_read_retains_revocation_and_never_calls_deferred_effect': ['BT-25'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_existing_evaluation_incomplete_surface_preserves_native_lifecycle_and_owner_facts': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_existing_reservation_settlement_after_elapsed_retains_exact_accounting': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_expired_canonical_admits_consumes_releases_and_replays_without_policy_rewrite': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_expired_canonical_preserves_each_finite_capacity_authority': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_expired_clock_cannot_bypass_revision_or_semantic_identity': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_expired_reopen_is_read_only_and_still_rejects_immutable_policy_drift': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_explicit_canonical_draining_still_denies_after_elapsed': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_legacy_elapsed_observation_does_not_drain_or_deny_work': ['BT-27'], 'backend/tests/test_fpbca_bcd_wallclock_budget.py::test_legacy_expired_clock_preserves_finite_count_exhaustion': ['BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_actual_cancel_stops_controller_but_keeps_physical_worker_tracked': ['BT-25', 'BT-26', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_actual_parallel_native_results_cross_parent_threshold_then_continue_without_harness_mutation': ['BT-25', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_elapsed_control_checkpoint_and_commit_keep_exact_authority': ['BT-25', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_explicit_or_historically_revoked_control_never_regains_commit_authority': ['BT-25', 'BT-26', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_generic_graph_accepts_actual_success_after_elapsed_and_reports_observation': ['BT-25', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_healthy_worker_stays_registered_and_authorized_after_aggregate_threshold': ['BT-25', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_historical_typed_timeout_still_suppresses_overlap_retry': ['BT-25', 'BT-26', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_late_concrete_owner_exception_retains_identity_and_is_not_clock_timeout': ['BT-25', 'BT-26', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_late_external_commit_error_is_kept_as_concrete_owner_failure': ['BT-25', 'BT-26', 'BT-27'], 'backend/tests/test_fpbca_wall_clock_runtime.py::test_native_duplicate_claim_wait_survives_elapsed_and_honors_explicit_cancel': ['BT-25', 'BT-27']}


def _closed(value,depth=0):
    if depth>16:raise TypeError("closed native value depth exceeded")
    if value is None or type(value) in (str,bool,int,float):return value
    if isinstance(value,Enum):return _closed(value.value,depth+1)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,Path):return str(value)
    if isinstance(value,bytes):return {"bytes":len(value),"sha256":hashlib.sha256(value).hexdigest()}
    if isinstance(value,BaseException):
        attributes={k:_closed(value.__dict__[k],depth+1) for k in ("c1_triggering_attempt_id","error_code","failure_category","failure_origin") if k in value.__dict__}
        return {"type":type(value).__module__+"."+type(value).__qualname__,"message":str(value),"exact_declared_native_exception_attributes":attributes}
    if type(value) in (tuple,list):return [_closed(x,depth+1) for x in value]
    if type(value) is dict:
        if any(type(k) not in (str,int) for k in value):raise TypeError("undeclared native dict key")
        return {str(k):_closed(v,depth+1) for k,v in value.items()}
    from core.runtime.execution_control import WorkflowExecutionControl
    if type(value) is WorkflowExecutionControl:return _control(value)
    from pydantic import BaseModel
    if isinstance(value,BaseModel):return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value,type):
        return {field.name:_closed(getattr(value,field.name),depth+1) for field in dataclasses.fields(value)}
    raise TypeError("undeclared native object "+type(value).__module__+"."+type(value).__qualname__)


def _value(value):
    try:return {"status":"MEASURED","value":_closed(value)}
    except Exception as error:return {"status":"UNMEASURED","error_type":type(error).__name__,"error":str(error)}


def _control(control):
    return {"execution_id":control.execution_id,"commit_scope_id":control.commit_scope_id,
        "deadline_monotonic":control.deadline_monotonic,"observed_monotonic":time.monotonic(),
        "cancel_requested":control._cancel_event.is_set(),"cancel_reason":control._reason,
        "external_commit_state":control._external_commit_state,
        "deferred_commit_ids":[row.side_effect_id for row in control._deferred_commits],
        "scope":"EXACT_KNOWN_NATIVE_CONTROL_FIELDS_PASSIVE_NOT_A_NEW_CANCEL_OR_CLOCK_AUTHORITY"}


def _raw(owner,binding):
    # Only actual existing plain JSONL owner attributes, never SQLite opening,
    # replay, recovery, projection or inferred quiescence.
    cls=binding["class_name"]
    path=owner.path if cls=="RunAttemptLifecycleService" else (
        owner.storage_path if cls=="BudgetManager" and owner._canonical_mode else None)
    if path is None:return {"status":"SOURCE_BOUND_N_A","reason":"NO_DECLARED_NATIVE_JSONL_PATH_FOR_THIS_OWNER"}
    path=Path(path)
    try:
        data=path.read_bytes()
        return {"status":"MEASURED","path":str(path),"bytes":len(data),
            "sha256":hashlib.sha256(data).hexdigest(),"raw":data}
    except OSError as error:return {"status":"UNMEASURED","path":str(path),"error_type":type(error).__name__,"error":str(error)}


def _wire_raw(raw):
    return {key:value for key,value in raw.items() if key!="raw"}


def _point(binding):
    return {key:binding[key] for key in ("path","sha256","function","line")}|{
        "native_operation":"ACTUAL_UNCHANGED_REGRESSION_OWNER_CALL_RETURN_OR_EXCEPTION",
        "native_refs":[]}


class _Capture:
    def __init__(self):
        self.lock=threading.RLock();self.calls=[];self.gaps=[];self.stack=ExitStack()
    def install(self):
        for binding in _HOOKS:
            path=Path(binding["path"])
            assert hashlib.sha256(path.read_bytes()).hexdigest()==binding["sha256"]
            module=importlib.import_module(binding["module"])
            assert Path(module.__file__).resolve()==path.resolve()
            owner=getattr(module,binding["class_name"]);name=binding["method"]
            descriptor=inspect.getattr_static(owner,name)
            assert not isinstance(descriptor,(classmethod,staticmethod))
            original=getattr(owner,name)
            assert inspect.isfunction(original) and not inspect.iscoroutinefunction(original)
            assert Path(original.__code__.co_filename).resolve()==path.resolve()
            assert original.__code__.co_firstlineno==binding["line"]
            assert next(iter(inspect.signature(original).parameters))=="self"
            @functools.wraps(original)
            def observed(*args,_original=original,_binding=binding,**kwargs):
                receiver=args[0]
                event={"point":_point(_binding),"actual_pid":os.getpid(),"actual_thread_id":threading.get_ident(),
                    "owner_instance_id":id(receiver),"actual_args":_value(args[1:]),"actual_kwargs":_value(kwargs)}
                try:
                    before_raw=_raw(receiver,_binding)
                    if _binding["class_name"]=="WorkflowExecutionControl":event["control_before"]=_control(receiver)
                except Exception as error:
                    before_raw={"status":"UNMEASURED","error_type":type(error).__name__,"error":str(error)}
                event["raw_before"]=_wire_raw(before_raw)
                if _binding["function"]=="BudgetManager._append_canonical_state_unlocked" and before_raw.get("status")=="MEASURED":
                    try:
                        raw=before_raw["raw"]
                        if not raw.endswith(b"\n"):raise ValueError("prior native Budget tail not a complete event")
                        prior_event=json.loads(raw.splitlines()[-1])
                        event["actual_prior_complete_budget_state"]=_value(prior_event["state"])
                    except Exception as error:
                        event["actual_prior_complete_budget_state"]={"status":"UNMEASURED","error_type":type(error).__name__,"error":str(error)}
                with self.lock:event["entry_index"]=len(self.calls);self.calls.append(event)
                try:
                    actual=_original(*args,**kwargs)
                except BaseException as error:
                    event["actual_exception"]={"type":type(error).__module__+"."+type(error).__qualname__,"message":str(error)}
                    raise
                else:
                    event["actual_return"]=_value(actual)
                    return actual
                finally:
                    try:
                        after_raw=_raw(receiver,_binding);event["raw_after"]=_wire_raw(after_raw)
                        if before_raw.get("status")==after_raw.get("status")=="MEASURED":
                            before,after=before_raw["raw"],after_raw["raw"]
                            event["actual_raw_byte_relation"]={"bytes_equal":before==after,"after_startswith_before":after.startswith(before)}
                            if after.startswith(before):
                                suffix=after[len(before):]
                                event["actual_appended_bytes"]={"bytes":len(suffix),"sha256":hashlib.sha256(suffix).hexdigest(),"hex":suffix.hex()}
                        if _binding["class_name"]=="WorkflowExecutionControl":event["control_after"]=_control(receiver)
                    except Exception as error:
                        event["observer_exit_error"]={"status":"UNMEASURED","error_type":type(error).__name__,"error":str(error)}
            self.stack.enter_context(patch.object(owner,name,observed))
        return self
    def close(self):self.stack.close()
    def emit(self,root,selector,rows):
        # No native calls, joins or repairs during emission.
        with self.lock:calls=list(self.calls)
        physical=[x for x in calls if x["point"]["function"]=="ModelInvoker.invoke"]
        committed_states=[]
        for event in calls:
            if (event["point"]["function"]=="BudgetManager._append_canonical_state_unlocked"
                    and "actual_exception" not in event and event.get("actual_kwargs",{}).get("status")=="MEASURED"):
                material=event["actual_kwargs"]["value"]
                if isinstance(material.get("state"),dict):committed_states.append(material["state"])
        consumed={};released={};transitions=[];transition_gaps=[];actual_transition_windows=0
        for event in calls:
            if event["point"]["function"]!="BudgetManager._append_canonical_state_unlocked" or "actual_exception" in event:continue
            prior=event.get("actual_prior_complete_budget_state",{})
            material=event.get("actual_kwargs",{})
            if prior.get("status")!="MEASURED" or material.get("status")!="MEASURED":
                transition_gaps.append({"entry_index":event["entry_index"],"prior":prior,"native_argument_status":material.get("status")});continue
            before=prior["value"];after=material["value"]["state"]
            actual_transition_windows+=1
            old={x["reservation_ref"]["reservation_id"]:x for x in before["reservations"]}
            for reservation in after["reservations"]:
                ref=reservation["reservation_ref"];previous=old.get(ref["reservation_id"])
                if (previous is not None and previous["status"]=="active" and reservation["status"] in {"consumed","released"}
                        and reservation["terminal_budget_revision"]==after["revision"] and after["revision"]>before["revision"]):
                    key=(ref["task_id"],ref["run_id"],ref["reservation_id"],reservation["terminal_budget_revision"])
                    row={"before":previous,"after":reservation,"before_budget_revision":before["revision"],"after_budget_revision":after["revision"],"native_append_entry_index":event["entry_index"]}
                    transitions.append(row)
                    if reservation["status"]=="consumed":consumed[key]=row
                    else:released[key]=row
        records=[]
        for event in calls:
            value={"actual_owner_call":event,"test_selector":selector,"rows":rows,
                "normative_success":"NOT_INFERRED_FROM_TEST_PASS_OR_LOCAL_FIXTURE_DATA"}
            records.append({"status":"MEASURED","native_point":event["point"],"native_value":value,
                "evidence_scope":"EXACT_NATIVE_CALL_INPUT_PROJECTION_RETURN_EXCEPTION_RAW_CUSTODY; UNKNOWN_SUBVALUES_REMAIN_UNMEASURED"})
        body={"schema_version":"bt_preserved_native_regression_observation_v1","selector":selector,"rows":rows,
            "status":"NATIVE_CALLS_OBSERVED" if records else "UNMEASURED_UNREACHED",
            "records":records,"native_calls":calls,
            "physical_observation":{"status":"MEASURED" if physical else "UNMEASURED_BOUNDARY_NOT_REACHED",
                "actual_entries":physical,"actual_count":len(physical) if physical else None,
                "scope":"ONLY_REGISTERED_PARENT_MODEL_INVOKER_ENTRIES; CUSTOM_HANDLERS_AND_CHILD_PROCESSES_SEPARATE"},
            "settlement_observation":{"status":"MEASURED" if actual_transition_windows else "UNMEASURED_NATIVE_TRANSITION_WINDOW_NOT_REACHED",
                "committed_native_state_arguments":committed_states,"actual_unique_consumed_reservations":list(consumed.values()),
                "actual_unique_released_reservations":list(released.values()),
                "actual_consumed_count":len(consumed) if actual_transition_windows else None,
                "actual_active_to_terminal_transitions":transitions,"actual_complete_transition_windows":actual_transition_windows,"transition_observer_gaps":transition_gaps,
                "scope":"ACTUAL_COMPLETE_PRE_APPEND_NATIVE_STATE_ACTIVE_TO_CONSUMED_REVISION_TRANSITIONS; RELEASED_SEPARATE; NO_EXISTING_CONSUMED_SNAPSHOT_OR_CALL_OR_BALANCE_INFERENCE"},
            "observer_gaps":self.gaps,"semantic_or_global_verdict":"NOT_GRANTED",
            "child_process_or_custom_provider_count":"UNMEASURED_UNLESS_INDEPENDENT_NATIVE_RECEIPT"}
        target=Path(root)/"bt_preserved_native_regression_observation.json"
        data=(json.dumps(body,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
        with target.open("xb") as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())


@pytest.fixture(autouse=True)
def _bt_preserved_native_owner_regression_capture(request,tmp_path):
    selector=request.node.nodeid
    rows=_SELECTOR_ROWS.get(selector) or _SELECTOR_ROWS.get(selector.split("[")[0])
    if not rows:yield None;return
    capture=_Capture().install()
    try:yield capture
    finally:
        capture.close()
        capture.emit(tmp_path,selector,rows)
