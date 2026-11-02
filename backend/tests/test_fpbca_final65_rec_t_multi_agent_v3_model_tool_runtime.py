"""Fresh observation copies of unchanged original local native controls.
Original owner calls/assertions remain intact. No production implementation.
"""
import importlib as _fpbca_importlib
import pytest as _fpbca_pytest
from fpbca_final65_d2_native_capture import RemainingNativeCapture as _FPBCA_RemainingNativeCapture
_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_model_tool_runtime')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'path': 'backend/apps/enterprise_document/services/agent_rag_v1/tool_gateway.py', 'sha256': '8724544b36fbc487245ec6bd2cddb82f416363e8cdcb88f7755cb5cb0d2f5761', 'module': 'apps.enterprise_document.services.agent_rag_v1.tool_gateway', 'symbol': 'ToolGateway.invoke', 'line': 133, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/budget_manager.py', 'sha256': '903dd686a16dce9350c105c564f72e1f78ef1316ba45fa1798560d58e5b119a6', 'module': 'apps.enterprise_document.services.agent_rag_v1.budget_manager', 'symbol': 'BudgetManager._append_canonical_state_unlocked', 'line': 743, 'kind': 'BUDGET_APPEND', 'plain_method': True, 'instance_attributes': ['task_id', 'run_id'], 'owned_file_attributes': [], 'planned_export': False}], 'original_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_model_tool_runtime.py', 'sha256': 'ca0468d89e22d9aa102cddab23d2ca791f8a0033ad5af00cb07a8b17be6f5071'}, 'row': 'D2/REC-T', 'source_refresh': 'PREPARATION_ONLY_FINAL_C2_D3_EPOCH_REBUILD_ROOT_REVIEW_REQUIRED'}
@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_d2_capture(tmp_path):
    capture = _FPBCA_RemainingNativeCapture(_FPBCA_DECLARATION, tmp_path).install(namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_target_tool_operation_without_capability_grant_guard_fails_closed(tmp_path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        manager = _manager(tmp_path)
        calls = []
        store = InMemoryToolExecutionStore()
        gateway = ToolGateway(execution_store=store, budget_runtime=CanonicalInvocationBudgetAdapter(manager), currentness_validator=lambda context: None)
        gateway.register(name='internal.search', version='v1', max_retries=0, handler=lambda invocation: calls.append(invocation))
        rejected = gateway.invoke(_tool_context(authorized=True), call_id='display-no-grant', tool_operation_id='tool-operation-no-grant', tool_name='internal.search', arguments={'query': 'x'})
        assert rejected.status is ToolResultStatus.REJECTED
        assert rejected.error is not None
        assert rejected.error.error_code == 'TOOL_CAPABILITY_GRANT_REQUIRED'
        assert calls == []
        with pytest.raises(KeyError):
            store.get('tool-operation-no-grant')
        assert manager.canonical_state().used.logical_tool_calls == 0
    finally:
        if "rejected" in locals():
            actual_native_guard_return = _fpbca_d2_capture.safe(rejected, "ACTUAL_ALREADY_RETURNED_NATIVE_TOOL_GUARD_DTO")
        _fpbca_d2_capture.safe_emit(row='D2/REC-T', selector='backend/tests/test_fpbca_final65_rec_t_multi_agent_v3_model_tool_runtime.py::test_native_observed_target_tool_operation_without_capability_grant_guard_fails_closed', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_model_tool_runtime.py', 'sha256': 'ca0468d89e22d9aa102cddab23d2ca791f8a0033ad5af00cb07a8b17be6f5071', 'function': 'test_target_tool_operation_without_capability_grant_guard_fails_closed', 'line': 699, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'SOURCE_BOUND_EXISTING_TOOL_GUARD_SUPPORT_ONLY; NOT_EFFECTFUL_HANDLER_CRASH_PASS', 'controlled_boundary': None, 'cold_reopen': False, 'exact_replay': False, 'replay_symbols': [], 'concurrent': False, 'native_local_names': ['rejected', 'stale'], 'dispatch_scope': 'ACTUAL_NATIVE_LOCAL_EFFECT_BOUNDARY_ENTRIES_ONLY', 'field_dispositions': {'actual_physical_dispatch_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'FROZEN_READINESS_REC_T_HAS_NO_ADDITIONAL_PRODUCTION_EFFECTFUL_HANDLER_REGISTRATION', 'evidence_scope': 'BOUNDED_REGISTRATION_INVENTORY_AND_GUARD_SUPPORT_ONLY; NOT_CLASS_WIDE_CRASH_PASS', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': [], 'native_cut_local_projection': {'actual_native_guard_return': ['schema_version', 'status', 'error', 'call_id', 'tool_operation_id', 'physical_attempt_refs']}})

def test_native_observed_target_tool_operation_without_currentness_guard_fails_closed(tmp_path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        manager = _manager(tmp_path)
        calls = []
        store = InMemoryToolExecutionStore()
        gateway = ToolGateway(execution_store=store, budget_runtime=CanonicalInvocationBudgetAdapter(manager), authorization_validator=lambda context, registration, arguments: None)
        gateway.register(name='internal.search', version='v1', max_retries=0, handler=lambda invocation: calls.append(invocation))
        stale = gateway.invoke(_tool_context(authorized=True), call_id='display-no-guard', tool_operation_id='tool-operation-no-guard', tool_name='internal.search', arguments={'query': 'x'})
        assert stale.status is ToolResultStatus.STALE
        assert stale.error is not None
        assert stale.error.error_code == 'TOOL_CURRENTNESS_GUARD_REQUIRED'
        assert calls == []
        with pytest.raises(KeyError):
            store.get('tool-operation-no-guard')
        assert manager.canonical_state().used.logical_tool_calls == 0
    finally:
        if "stale" in locals():
            actual_native_guard_return = _fpbca_d2_capture.safe(stale, "ACTUAL_ALREADY_RETURNED_NATIVE_TOOL_GUARD_DTO")
        _fpbca_d2_capture.safe_emit(row='D2/REC-T', selector='backend/tests/test_fpbca_final65_rec_t_multi_agent_v3_model_tool_runtime.py::test_native_observed_target_tool_operation_without_currentness_guard_fails_closed', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_model_tool_runtime.py', 'sha256': 'ca0468d89e22d9aa102cddab23d2ca791f8a0033ad5af00cb07a8b17be6f5071', 'function': 'test_target_tool_operation_without_currentness_guard_fails_closed', 'line': 734, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'SOURCE_BOUND_EXISTING_TOOL_GUARD_SUPPORT_ONLY; NOT_EFFECTFUL_HANDLER_CRASH_PASS', 'controlled_boundary': None, 'cold_reopen': False, 'exact_replay': False, 'replay_symbols': [], 'concurrent': False, 'native_local_names': ['rejected', 'stale'], 'dispatch_scope': 'ACTUAL_NATIVE_LOCAL_EFFECT_BOUNDARY_ENTRIES_ONLY', 'field_dispositions': {'actual_physical_dispatch_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'FROZEN_READINESS_REC_T_HAS_NO_ADDITIONAL_PRODUCTION_EFFECTFUL_HANDLER_REGISTRATION', 'evidence_scope': 'BOUNDED_REGISTRATION_INVENTORY_AND_GUARD_SUPPORT_ONLY; NOT_CLASS_WIDE_CRASH_PASS', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': [], 'native_cut_local_projection': {'actual_native_guard_return': ['schema_version', 'status', 'error', 'call_id', 'tool_operation_id', 'physical_attempt_refs']}})
