"""Fresh observation copies of unchanged original local native controls.
Original owner calls/assertions remain intact. No production implementation.
"""
import importlib as _fpbca_importlib
import pytest as _fpbca_pytest
from fpbca_final65_d2_native_capture import RemainingNativeCapture as _FPBCA_RemainingNativeCapture
_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_reconciliation_runtime')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.__init__', 'line': 411, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.reserve_writer_execution', 'line': 822, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.mark_writer_dispatched', 'line': 869, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.persist_writer_result', 'line': 916, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.get_writer_execution', 'line': 991, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.resolve_writer_execution', 'line': 1005, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/core/runtime/pre_write_execution_store.py', 'sha256': '65d010ec5217e95912449f4fddf09d76c9d999d73272c30dd508e039afc8aad8', 'module': 'core.runtime.pre_write_execution_store', 'symbol': 'SQLitePreWriteExecutionStore.resolve_writer_generation_invocation', 'line': 1033, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['database_path'], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/running_invocation_recovery.py', 'sha256': '8dd44ed478dafe3b47e0af08bee1bd9583b1bead284cc95a33e4a1d4858c1a94', 'module': 'apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery', 'symbol': 'RunningInvocationRecoveryService.reconcile_writer', 'line': 99, 'kind': 'REC_CONSUMER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/running_invocation_recovery.py', 'sha256': '8dd44ed478dafe3b47e0af08bee1bd9583b1bead284cc95a33e4a1d4858c1a94', 'module': 'apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery', 'symbol': 'RunningInvocationRecoveryService.decide', 'line': 169, 'kind': 'REC_CONSUMER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'symbol': 'RunAttemptLifecycleService.load_run_lifecycle', 'line': 1063, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'symbol': 'RunAttemptLifecycleService.load_attempt', 'line': 1339, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/model_gateway/model_invoker.py', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'module': 'model_gateway.model_invoker', 'symbol': 'ModelInvoker.invoke', 'line': 28, 'kind': 'INVOKER_SUPPORTING', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'symbol': 'BudgetManager._append_canonical_state_unlocked', 'line': 743, 'kind': 'BUDGET_APPEND', 'plain_method': True, 'instance_attributes': ['task_id', 'run_id'], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/model_gateway/fake_llm_client.py', 'sha256': 'd8373490a7393d2a32aab9577f79a861d236d2fcc0b611ca28c6c662cb93e198', 'module': 'model_gateway.fake_llm_client', 'symbol': 'FakeLLMClient.generate', 'line': 56, 'kind': 'ACTUAL_DISPATCH', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}], 'original_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_reconciliation_runtime.py', 'sha256': 'f5aa0e47bfc5405ecf36d0949eed7df44bbd11485cad37a5eac278138bf8341b'}, 'row': 'D2/REC-W', 'source_refresh': 'PREPARATION_ONLY_FINAL_C2_D3_EPOCH_REBUILD_ROOT_REVIEW_REQUIRED'}
@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_d2_capture(tmp_path):
    capture = _FPBCA_RemainingNativeCapture(_FPBCA_DECLARATION, tmp_path).install(namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_actual_production_owner_wiring_and_current_native_result_read(production_writer_cut, tmp_path, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        cut = production_writer_cut
        graph, engine = (cut['graph'], cut['engine'])
        recovery = graph.running_invocation_recovery
        coordinator = engine.stage_service.section_execution_coordinator
        service = recovery.service
        assert recovery.lifecycle_service is engine.lifecycle_service
        assert service.writer_execution_store is coordinator.require_pre_write_services().writer_invocation.execution_store
        assert service.support_runtime is coordinator.claim_support_composition.runtime
        assert service.verification_runtime is coordinator.claim_verification_runtime
        before = cut['store'].database_path.read_bytes()
        decision = graph.recover_running_invocation(workflow_namespace=cut['workflow_namespace'], run_id=cut['run_id'], section_id=cut['payload'].section_id, invocation=cut['invocation'])
        assert decision.action is InvocationRecoveryAction.NO_ACTION
        assert decision.reason_code == 'RECOVERED_DURABLE_RESULT' and (not decision.starts_new_call)
        assert cut['store'].database_path.read_bytes() == before
        assert len(cut['calls']) == 1 and (not cut['control'].is_cancelled)
        for field in ('task_id', 'run_id', 'attempt_id', 'input_snapshot_version', 'idempotency_key'):
            changed = cut['invocation'].model_copy(update={field: 'changed'})
            view = service.reconcile_writer(changed, cut['key'], cut['fingerprint'], result_ref=cut['ref'], generation_invocation_ref=cut['generation_ref'], generation_invocation=cut['payload'].pending_writer_generation_invocation)
            assert view['outcome'] == 'INDETERMINATE', field
        assert cut['store'].database_path.read_bytes() == before
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-W', selector='backend/tests/test_fpbca_final65_rec_w_fpbca_bcd_reconciliation_runtime.py::test_native_observed_actual_production_owner_wiring_and_current_native_result_read', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_reconciliation_runtime.py', 'sha256': 'f5aa0e47bfc5405ecf36d0949eed7df44bbd11485cad37a5eac278138bf8341b', 'function': 'test_actual_production_owner_wiring_and_current_native_result_read', 'line': 201, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_FACTORY_WRITER_NATIVE_SQLITE_RESULT_PRE_C1_AND_REC_CONSUMER; OFFLINE_FAKE_PROVIDER', 'controlled_boundary': 'NATIVE_WRITER_RESULT_DURABLE_BEFORE_CONTROLLER_PROJECTION', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['RunningInvocationRecoveryService.reconcile_writer'], 'concurrent': False, 'native_local_names': ['decision', 'view', 'changed', 'error'], 'dispatch_scope': 'ACTUAL_FAKE_LLM_CLIENT_GENERATE_ENTRIES_IN_THIS_UNCHANGED_FACTORY_CASE; EXACT_NATIVE_MODEL_INVOKER_REQUESTS_RETAINED; NO_REAL_PROVIDER', 'field_dispositions': {}, 'cold_symbols': ['SQLitePreWriteExecutionStore.__init__'], 'native_cut_local_projection': {'production_writer_cut': ['key', 'fingerprint', 'ref', 'generation_ref', 'cold_reopen_exact', 'workflow_namespace']}})

@pytest.mark.parametrize('production_writer_cut', [True], indirect=True)
def test_native_observed_actual_owner_failure_is_read_before_c1_and_primary_error_remains(production_writer_cut, tmp_path, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        cut = production_writer_cut
        service = cut['graph'].running_invocation_recovery.service
        before = cut['store'].database_path.read_bytes()
        view = service.reconcile_writer(cut['invocation'], cut['key'], cut['fingerprint'], result_ref=cut['ref'], generation_invocation=cut['payload'].pending_writer_generation_invocation)
        assert view['outcome'] == 'DURABLE_FAILURE' and view['result'] == cut['result']
        assert cut['result'].status is WriterExecutionStatus.TECHNICAL_FAILURE
        assert cut['store'].database_path.read_bytes() == before
        assert cut['engine'].lifecycle_service.load_run_lifecycle(cut['run_id']).status.value == 'running'
        cut['finish']()
        life = cut['engine'].lifecycle_service
        assert life.load_run_lifecycle(cut['run_id']).status.value == 'failed'
        assert life.load_attempt(cut['invocation'].attempt_id).status.value == 'failed'
        error = JsonlTaskManager(cut['root'] / 'tasks').get_task(cut['task_id']).error
        assert error.error_code == cut['result'].error.error_code
        assert error.error_type == 'TimeoutError' and error.message == cut['result'].error.message
        assert error.details['c1_triggering_attempt_id'] == cut['invocation'].attempt_id
        assert len(cut['calls']) == 1
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-W', selector='backend/tests/test_fpbca_final65_rec_w_fpbca_bcd_reconciliation_runtime.py::test_native_observed_actual_owner_failure_is_read_before_c1_and_primary_error_remains', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_reconciliation_runtime.py', 'sha256': 'f5aa0e47bfc5405ecf36d0949eed7df44bbd11485cad37a5eac278138bf8341b', 'function': 'test_actual_owner_failure_is_read_before_c1_and_primary_error_remains', 'line': 227, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_FACTORY_WRITER_NATIVE_SQLITE_RESULT_PRE_C1_AND_REC_CONSUMER; OFFLINE_FAKE_PROVIDER', 'controlled_boundary': 'NATIVE_WRITER_RESULT_DURABLE_BEFORE_CONTROLLER_PROJECTION', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['RunningInvocationRecoveryService.reconcile_writer'], 'concurrent': False, 'native_local_names': ['decision', 'view', 'changed', 'error'], 'dispatch_scope': 'ACTUAL_FAKE_LLM_CLIENT_GENERATE_ENTRIES_IN_THIS_UNCHANGED_FACTORY_CASE; EXACT_NATIVE_MODEL_INVOKER_REQUESTS_RETAINED; NO_REAL_PROVIDER', 'field_dispositions': {}, 'cold_symbols': ['SQLitePreWriteExecutionStore.__init__'], 'native_cut_local_projection': {'production_writer_cut': ['key', 'fingerprint', 'ref', 'generation_ref', 'cold_reopen_exact', 'workflow_namespace']}})
