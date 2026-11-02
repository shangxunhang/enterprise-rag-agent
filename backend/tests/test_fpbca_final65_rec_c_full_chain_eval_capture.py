"""Fresh observation copies of unchanged original local native controls.
Original owner calls/assertions remain intact. No production implementation.
"""
import importlib as _fpbca_importlib
import pytest as _fpbca_pytest
from fpbca_final65_d2_native_capture import RemainingNativeCapture as _FPBCA_RemainingNativeCapture
_fpbca_base = _fpbca_importlib.import_module('test_full_chain_eval_capture')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'path': 'backend/model_gateway/model_invoker.py', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'module': 'model_gateway.model_invoker', 'symbol': 'ModelInvoker.invoke', 'line': 28, 'kind': 'INVOKER_SUPPORTING', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/tests/test_full_chain_eval_capture.py', 'sha256': 'd7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a', 'module': 'test_full_chain_eval_capture', 'symbol': 'FixedClient.generate', 'line': 33, 'kind': 'ACTUAL_DISPATCH', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/eval/full_chain/domains/runtime_reliability.py', 'sha256': '1f207c23c6520cf2c530875c2d20d4e4d44a54f5d7c9cb62251dea8cbcf30137', 'module': 'eval.full_chain.domains.runtime_reliability', 'symbol': 'read_diagnostic_prefix', 'line': 23, 'kind': 'CURRENTNESS', 'plain_method': False, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}], 'original_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_full_chain_eval_capture.py', 'sha256': 'd7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a'}, 'row': 'D2/REC-C', 'source_refresh': 'PREPARATION_ONLY_FINAL_C2_D3_EPOCH_REBUILD_ROOT_REVIEW_REQUIRED'}
@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_d2_capture(tmp_path):
    capture = _FPBCA_RemainingNativeCapture(_FPBCA_DECLARATION, tmp_path).install(namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_T02_raw_prefix_survives_downstream_business_commit_failure(tmp_path, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        observer = session(tmp_path)
        with pytest.raises(RuntimeError, match='downstream commit'), observer:
            result = gateway(FixedClient()).generate(request())
            assert result.success
            observer.flush()
            prefix = read_diagnostic_prefix(observer.path, [tmp_path])
            assert representation(prefix, 'MODEL_CLIENT_RAW_RESPONSE')[0]['material']['content'] == 'A'
            assert prefix['completeness'] == 'CAPTURE_INCOMPLETE'
            raise RuntimeError('downstream commit failed')
        assert representation(captured(observer, tmp_path), 'MODEL_CLIENT_RAW_RESPONSE')[0]['material']['content'] == 'A'
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_full_chain_eval_capture.py::test_native_observed_T02_raw_prefix_survives_downstream_business_commit_failure', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_full_chain_eval_capture.py', 'sha256': 'd7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a', 'function': 'test_T02_raw_prefix_survives_downstream_business_commit_failure', 'line': 166, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_GATEWAY_FIXED_CLIENT_AND_CAPTURE_PREFIX_READER; CAPTURE_INCOMPLETE_IS_PRESERVED', 'controlled_boundary': 'EXACT_EXISTING_CAPTURE_FAILURE_OR_DOWNSTREAM_COMMIT_FAILURE', 'cold_reopen': False, 'exact_replay': False, 'replay_symbols': [], 'concurrent': False, 'native_local_names': ['response', 'prefix', 'raw', 'projected', 'rows', 'batch', 'groups'], 'dispatch_scope': 'ACTUAL_FIXED_FAKE_CLIENT_GENERATE_ENTRIES_THIS_CAPTURE_CONTROL; NO_REAL_PROVIDER', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'UNCHANGED_CAPTURE_GATEWAY_FIXTURE_HAS_NO_CANONICAL_BUDGET_ADAPTER', 'evidence_scope': 'EXACT_FIXED_CLIENT_CAPTURE_SCOPE_ONLY; NO_INVENTED_SETTLEMENT_ZERO', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__']})

@pytest.mark.parametrize('failure', ['representation-cap', 'queue-drop', 'no-seal', 'disk-error'])
def test_native_observed_T11_capture_failure_keeps_business_output_and_exposes_incomplete(tmp_path, monkeypatch, failure, _fpbca_d2_capture):
    _fpbca_d2_capture.begin_flow()
    try:
        kwargs = {'max_representation_bytes': 100} if failure == 'representation-cap' else {}
        observer = session(tmp_path, **kwargs)
        if failure == 'queue-drop':
            monkeypatch.setattr(observer, 'config', replace(observer.config, max_queue_events=1))
            with observer._condition:
                observer.emit({'event_type': 'fill'})
                observer.emit({'event_type': 'dropped'})
        if failure == 'disk-error':
            monkeypatch.setattr(observer, '_blob_root', tmp_path / 'missing-parent')
        with observer:
            result = gateway(FixedClient()).generate(request())
        assert result.content == 'A' and result.success
        if failure == 'no-seal':
            lines = observer.path.read_bytes().splitlines(keepends=True)
            unsealed = tmp_path / 'unsealed.jsonl'
            unsealed.write_bytes(b''.join(lines[:-1]))
            prefix = read_diagnostic_prefix(unsealed, [tmp_path])
        else:
            prefix = read_diagnostic_prefix(observer.path, [tmp_path])
        assert prefix['completeness'] == 'CAPTURE_INCOMPLETE'
        groups, batch = check_generation_reader(prefix)
        assert all((g['raw_state'] != 'MEASURED' for g in groups['physical_attempts']))
        if failure == 'representation-cap':
            assert representation(prefix, 'MODEL_CLIENT_RAW_RESPONSE') == []
        assert observer.health()['lost_count'] > 0 or failure == 'no-seal'
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_full_chain_eval_capture.py::test_native_observed_T11_capture_failure_keeps_business_output_and_exposes_incomplete', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_full_chain_eval_capture.py', 'sha256': 'd7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a', 'function': 'test_T11_capture_failure_keeps_business_output_and_exposes_incomplete', 'line': 179, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_GATEWAY_FIXED_CLIENT_AND_CAPTURE_PREFIX_READER; CAPTURE_INCOMPLETE_IS_PRESERVED', 'controlled_boundary': 'EXACT_EXISTING_CAPTURE_FAILURE_OR_DOWNSTREAM_COMMIT_FAILURE', 'cold_reopen': False, 'exact_replay': False, 'replay_symbols': [], 'concurrent': False, 'native_local_names': ['response', 'prefix', 'raw', 'projected', 'rows', 'batch', 'groups'], 'dispatch_scope': 'ACTUAL_FIXED_FAKE_CLIENT_GENERATE_ENTRIES_THIS_CAPTURE_CONTROL; NO_REAL_PROVIDER', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'UNCHANGED_CAPTURE_GATEWAY_FIXTURE_HAS_NO_CANONICAL_BUDGET_ADAPTER', 'evidence_scope': 'EXACT_FIXED_CLIENT_CAPTURE_SCOPE_ONLY; NO_INVENTED_SETTLEMENT_ZERO', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__']})
