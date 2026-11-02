"""Fresh observation copies of unchanged original local native controls.
Original owner calls/assertions remain intact. No production implementation.
"""
import importlib as _fpbca_importlib
import pytest as _fpbca_pytest
from fpbca_final65_d2_native_capture import RemainingNativeCapture as _FPBCA_RemainingNativeCapture
_fpbca_base = _fpbca_importlib.import_module('test_p1_security_durability_identity_closure')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'module': 'core.persistence.crash_safe_jsonl', 'symbol': 'CrashSafeJsonlRecordSink._append_record_unlocked', 'line': 150, 'kind': 'ACTUAL_DISPATCH', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'module': 'core.persistence.crash_safe_jsonl', 'symbol': 'CrashSafeJsonlEventLog.append_serialized', 'line': 84, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/rag/data_capture/jsonl_writer.py', 'sha256': '43538ed75e1c53c2110ba86de8d064544e8410e7f05c72bada6be392845234c6', 'module': 'rag.data_capture.jsonl_writer', 'symbol': 'JsonlWriter.write_once', 'line': 101, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/runtime/side_effects.py', 'sha256': '4b28e7b21d9de74711c18d1442b949ab1b54c4e1ed6b98fa634f403d0587d4b1', 'module': 'core.runtime.side_effects', 'symbol': 'JsonlSideEffectCommitStore.__init__', 'line': 458, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/core/runtime/side_effects.py', 'sha256': '4b28e7b21d9de74711c18d1442b949ab1b54c4e1ed6b98fa634f403d0587d4b1', 'module': 'core.runtime.side_effects', 'symbol': 'JsonlSideEffectCommitStore.mark_committed', 'line': 748, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/runtime/execution_control.py', 'sha256': 'dfb5b2a443caf25ca614d7f9a78eaace716cebde02e26fb943e3b7428f57cdb9', 'module': 'core.runtime.execution_control', 'symbol': 'WorkflowExecutionControl.finalize_external_commits', 'line': 186, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': ['execution_id', 'commit_scope_id'], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/rag/data_capture/jsonl_writer.py', 'sha256': '43538ed75e1c53c2110ba86de8d064544e8410e7f05c72bada6be392845234c6', 'module': 'rag.data_capture.jsonl_writer', 'symbol': 'JsonlWriter.__init__', 'line': 70, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'module': 'core.persistence.crash_safe_jsonl', 'symbol': 'CrashSafeJsonlRecordSink.read_records', 'line': 144, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}], 'original_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8'}, 'row': 'D2/REC-C', 'source_refresh': 'PREPARATION_ONLY_FINAL_C2_D3_EPOCH_REBUILD_ROOT_REVIEW_REQUIRED'}
@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_d2_capture(tmp_path):
    capture = _FPBCA_RemainingNativeCapture(_FPBCA_DECLARATION, tmp_path).install(namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_jsonl_writer_recovers_torn_tail_before_idempotent_append(tmp_path: Path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        path = tmp_path / 'records.jsonl'
        path.write_bytes(b'{"capture_id":"effect-1"')
        JsonlWriter(path).write_once({'capture_id': 'effect-1', 'value': 1}, id_field='capture_id')
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        assert records == [{'capture_id': 'effect-1', 'value': 1}]
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_jsonl_writer_recovers_torn_tail_before_idempotent_append', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8', 'function': 'test_jsonl_writer_recovers_torn_tail_before_idempotent_append', 'line': 142, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_CAPTURE_JSONL_SINK_AND_SIDE_EFFECT_OWNER_AUTHORED_LOCAL_TORN_OR_ACK_BOUNDARY', 'controlled_boundary': 'UNCHANGED_TEST_OWNED_LITERAL_TORN_OR_COMPLETE_CORRUPT_CAPTURE_BYTES', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once', 'JsonlSideEffectCommitStore.mark_committed'], 'concurrent': False, 'native_local_names': ['records', 'record', 'staged', 'summary', 'committed_ids', 'side_effect_id'], 'dispatch_scope': 'ACTUAL_CRASH_SAFE_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_ONLY; NOT_MODEL_OR_PROVIDER_DISPATCH', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_FILE_SIDE_EFFECT_FLOW_HAS_NO_CANONICAL_MODEL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_CAPTURE_FILE_OWNER_ONLY; SEMANTIC_ACK_AND_NATIVE_FRAMES_RETAINED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__']})

def test_native_observed_data_capture_side_effect_recovers_torn_tail_before_commit(tmp_path: Path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        ledger_path = tmp_path / 'side-effects.jsonl'
        control = WorkflowExecutionControl.with_timeout(execution_id='attempt', timeout_seconds=2, commit_scope_id='task:step', side_effect_store=JsonlSideEffectCommitStore(ledger_path))
        recorder = JsonlDataCaptureRecorder(tmp_path / 'captures')
        with activate_execution_control(control):
            record = recorder.record(capture_type='eval_samples', capture_stage='candidate', task_id='task', run_id='run')
        path = tmp_path / 'captures' / 'eval_samples' / 'run_eval_samples.jsonl'
        path.write_bytes(b'{"record_id":"torn"')
        summary = control.finalize_external_commits(attempt_succeeded=True)
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        side_effect_id = record.metadata['execution_attempt']['side_effect_id']
        assert summary['committed'] == 1
        assert [item['record_id'] for item in records] == [record.record_id]
        assert _ledger_terminal(ledger_path, side_effect_id) == 'committed'
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_data_capture_side_effect_recovers_torn_tail_before_commit', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8', 'function': 'test_data_capture_side_effect_recovers_torn_tail_before_commit', 'line': 160, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_CAPTURE_JSONL_SINK_AND_SIDE_EFFECT_OWNER_AUTHORED_LOCAL_TORN_OR_ACK_BOUNDARY', 'controlled_boundary': 'UNCHANGED_TEST_OWNED_LITERAL_TORN_OR_COMPLETE_CORRUPT_CAPTURE_BYTES', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once', 'JsonlSideEffectCommitStore.mark_committed'], 'concurrent': False, 'native_local_names': ['records', 'record', 'staged', 'summary', 'committed_ids', 'side_effect_id'], 'dispatch_scope': 'ACTUAL_CRASH_SAFE_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_ONLY; NOT_MODEL_OR_PROVIDER_DISPATCH', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_FILE_SIDE_EFFECT_FLOW_HAS_NO_CANONICAL_MODEL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_CAPTURE_FILE_OWNER_ONLY; SEMANTIC_ACK_AND_NATIVE_FRAMES_RETAINED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__']})

def test_native_observed_rag_capture_side_effect_recovers_torn_tail_before_commit(tmp_path: Path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        ledger_path = tmp_path / 'side-effects.jsonl'
        output = tmp_path / 'rag-runs.jsonl'
        output.write_bytes(b'{"capture_id":"torn"')
        control = WorkflowExecutionControl.with_timeout(execution_id='attempt', timeout_seconds=2, commit_scope_id='task:rag', side_effect_store=JsonlSideEffectCommitStore(ledger_path))
        with activate_execution_control(control):
            staged = RagRunCapture(output).capture({'run_id': 'rag-run', 'query': 'hybrid'})
        summary = control.finalize_external_commits(attempt_succeeded=True)
        records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
        assert summary['committed'] == 1
        assert [item['capture_id'] for item in records] == [staged['capture_id']]
        assert _ledger_terminal(ledger_path, staged['capture_id']) == 'committed'
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_rag_capture_side_effect_recovers_torn_tail_before_commit', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8', 'function': 'test_rag_capture_side_effect_recovers_torn_tail_before_commit', 'line': 198, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_CAPTURE_JSONL_SINK_AND_SIDE_EFFECT_OWNER_AUTHORED_LOCAL_TORN_OR_ACK_BOUNDARY', 'controlled_boundary': 'UNCHANGED_TEST_OWNED_LITERAL_TORN_OR_COMPLETE_CORRUPT_CAPTURE_BYTES', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once', 'JsonlSideEffectCommitStore.mark_committed'], 'concurrent': False, 'native_local_names': ['records', 'record', 'staged', 'summary', 'committed_ids', 'side_effect_id'], 'dispatch_scope': 'ACTUAL_CRASH_SAFE_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_ONLY; NOT_MODEL_OR_PROVIDER_DISPATCH', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_FILE_SIDE_EFFECT_FLOW_HAS_NO_CANONICAL_MODEL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_CAPTURE_FILE_OWNER_ONLY; SEMANTIC_ACK_AND_NATIVE_FRAMES_RETAINED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__']})

def test_native_observed_torn_recovery_plus_ack_retry_keeps_rag_record_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        ledger_path = tmp_path / 'side-effects.jsonl'
        ledger = JsonlSideEffectCommitStore(ledger_path)
        original_mark = ledger.mark_committed
        calls = 0

        def fail_first_ack(claim) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('injected acknowledgement failure')
            return original_mark(claim)
        monkeypatch.setattr(ledger, 'mark_committed', fail_first_ack)
        output = tmp_path / 'rag-runs.jsonl'
        output.write_bytes(b'{"capture_id":"torn"')
        capture = RagRunCapture(output)

        def stage(execution_id: str) -> WorkflowExecutionControl:
            control = WorkflowExecutionControl.with_timeout(execution_id=execution_id, timeout_seconds=2, commit_scope_id='stable:rag', side_effect_store=ledger)
            with activate_execution_control(control):
                capture.capture({'run_id': 'run', 'query': 'query'})
            return control
        with pytest.raises(OSError, match='acknowledgement failure'):
            stage('first').finalize_external_commits(attempt_succeeded=True)
        summary = stage('retry').finalize_external_commits(attempt_succeeded=True)
        records = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
        committed_ids = [json.loads(line)['side_effect_id'] for line in ledger_path.read_text(encoding='utf-8').splitlines() if json.loads(line)['event_type'] == 'committed']
        assert summary['committed'] == 1
        assert len(records) == 1
        assert len(committed_ids) == 1
        assert _ledger_terminal(ledger_path, committed_ids[0]) == 'committed'
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_torn_recovery_plus_ack_retry_keeps_rag_record_exactly_once', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8', 'function': 'test_torn_recovery_plus_ack_retry_keeps_rag_record_exactly_once', 'line': 226, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_CAPTURE_JSONL_SINK_AND_SIDE_EFFECT_OWNER_AUTHORED_LOCAL_TORN_OR_ACK_BOUNDARY', 'controlled_boundary': 'UNCHANGED_TEST_OWNED_LITERAL_TORN_OR_COMPLETE_CORRUPT_CAPTURE_BYTES', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once', 'JsonlSideEffectCommitStore.mark_committed'], 'concurrent': False, 'native_local_names': ['records', 'record', 'staged', 'summary', 'committed_ids', 'side_effect_id'], 'dispatch_scope': 'ACTUAL_CRASH_SAFE_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_ONLY; NOT_MODEL_OR_PROVIDER_DISPATCH', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_FILE_SIDE_EFFECT_FLOW_HAS_NO_CANONICAL_MODEL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_CAPTURE_FILE_OWNER_ONLY; SEMANTIC_ACK_AND_NATIVE_FRAMES_RETAINED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__'], 'native_capture_semantic_replay': {'projection_point': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\rag\\data_capture\\rag_run_capture.py', 'sha256': '66f5ba2be688348a03a1fd5431780e571a751fc33be129e97408412e84a94c63', 'function': 'rag_run_semantic_projection', 'line': 114}}})

def test_native_observed_jsonl_writer_fails_closed_on_committed_middle_corruption(tmp_path: Path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        path = tmp_path / 'records.jsonl'
        path.write_text('{"capture_id":"one"}\n{"broken":\n{"capture_id":"two"}\n', encoding='utf-8')
        with pytest.raises(ValueError, match='invalid committed JSONL record'):
            JsonlWriter(path).write_once({'capture_id': 'three'}, id_field='capture_id')
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_jsonl_writer_fails_closed_on_committed_middle_corruption', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8', 'function': 'test_jsonl_writer_fails_closed_on_committed_middle_corruption', 'line': 277, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_CAPTURE_JSONL_SINK_AND_SIDE_EFFECT_OWNER_AUTHORED_LOCAL_TORN_OR_ACK_BOUNDARY', 'controlled_boundary': 'UNCHANGED_TEST_OWNED_LITERAL_TORN_OR_COMPLETE_CORRUPT_CAPTURE_BYTES', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once', 'JsonlSideEffectCommitStore.mark_committed'], 'concurrent': False, 'native_local_names': ['records', 'record', 'staged', 'summary', 'committed_ids', 'side_effect_id'], 'dispatch_scope': 'ACTUAL_CRASH_SAFE_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_ONLY; NOT_MODEL_OR_PROVIDER_DISPATCH', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_FILE_SIDE_EFFECT_FLOW_HAS_NO_CANONICAL_MODEL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_CAPTURE_FILE_OWNER_ONLY; SEMANTIC_ACK_AND_NATIVE_FRAMES_RETAINED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__'], 'native_integrity_read_refusal': {'owner_symbol': 'JsonlWriter.write_once', 'reader': {'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'frame_function': '_read_records_unlocked'}}})

def test_native_observed_jsonl_writer_fails_closed_on_duplicate_committed_identity(tmp_path: Path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        path = tmp_path / 'records.jsonl'
        path.write_text('{"capture_id":"same","value":1}\n{"capture_id":"same","value":2}\n', encoding='utf-8')
        with pytest.raises(ValueError, match='duplicate logical record'):
            JsonlWriter(path).write_once({'capture_id': 'same', 'value': 3}, id_field='capture_id')
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-C', selector='backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_jsonl_writer_fails_closed_on_duplicate_committed_identity', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_p1_security_durability_identity_closure.py', 'sha256': '8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8', 'function': 'test_jsonl_writer_fails_closed_on_duplicate_committed_identity', 'line': 293, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_CAPTURE_JSONL_SINK_AND_SIDE_EFFECT_OWNER_AUTHORED_LOCAL_TORN_OR_ACK_BOUNDARY', 'controlled_boundary': 'UNCHANGED_TEST_OWNED_LITERAL_TORN_OR_COMPLETE_CORRUPT_CAPTURE_BYTES', 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once', 'JsonlSideEffectCommitStore.mark_committed'], 'concurrent': False, 'native_local_names': ['records', 'record', 'staged', 'summary', 'committed_ids', 'side_effect_id'], 'dispatch_scope': 'ACTUAL_CRASH_SAFE_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_ONLY; NOT_MODEL_OR_PROVIDER_DISPATCH', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_FILE_SIDE_EFFECT_FLOW_HAS_NO_CANONICAL_MODEL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_CAPTURE_FILE_OWNER_ONLY; SEMANTIC_ACK_AND_NATIVE_FRAMES_RETAINED_SEPARATELY', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__', 'JsonlSideEffectCommitStore.__init__'], 'native_integrity_read_refusal': {'owner_symbol': 'JsonlWriter.write_once', 'reader': {'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'frame_function': '_read_records_unlocked'}}})
