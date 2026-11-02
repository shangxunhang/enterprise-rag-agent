"""Fresh native Capture cold/replay/concurrency; not Model accounting."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import pytest
from rag.data_capture.jsonl_writer import JsonlWriter
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink
from fpbca_final65_d2_native_capture import RemainingNativeCapture
DECLARATION={'hooks': [{'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'module': 'core.persistence.crash_safe_jsonl', 'symbol': 'CrashSafeJsonlRecordSink._append_record_unlocked', 'line': 150, 'kind': 'ACTUAL_DISPATCH', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'module': 'core.persistence.crash_safe_jsonl', 'symbol': 'CrashSafeJsonlEventLog.append_serialized', 'line': 84, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/rag/data_capture/jsonl_writer.py', 'sha256': '43538ed75e1c53c2110ba86de8d064544e8410e7f05c72bada6be392845234c6', 'module': 'rag.data_capture.jsonl_writer', 'symbol': 'JsonlWriter.write_once', 'line': 101, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/runtime/side_effects.py', 'sha256': '4b28e7b21d9de74711c18d1442b949ab1b54c4e1ed6b98fa634f403d0587d4b1', 'module': 'core.runtime.side_effects', 'symbol': 'JsonlSideEffectCommitStore.__init__', 'line': 458, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/core/runtime/side_effects.py', 'sha256': '4b28e7b21d9de74711c18d1442b949ab1b54c4e1ed6b98fa634f403d0587d4b1', 'module': 'core.runtime.side_effects', 'symbol': 'JsonlSideEffectCommitStore.mark_committed', 'line': 748, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/runtime/execution_control.py', 'sha256': 'dfb5b2a443caf25ca614d7f9a78eaace716cebde02e26fb943e3b7428f57cdb9', 'module': 'core.runtime.execution_control', 'symbol': 'WorkflowExecutionControl.finalize_external_commits', 'line': 186, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': ['execution_id', 'commit_scope_id'], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/rag/data_capture/jsonl_writer.py', 'sha256': '43538ed75e1c53c2110ba86de8d064544e8410e7f05c72bada6be392845234c6', 'module': 'rag.data_capture.jsonl_writer', 'symbol': 'JsonlWriter.__init__', 'line': 70, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}, {'path': 'backend/core/persistence/crash_safe_jsonl.py', 'sha256': 'd6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401', 'module': 'core.persistence.crash_safe_jsonl', 'symbol': 'CrashSafeJsonlRecordSink.read_records', 'line': 144, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': ['path'], 'planned_export': False}], 'row': 'D2/REC-C'}
FLOW={'scope': 'ACTUAL_INDEPENDENT_NATIVE_CAPTURE_WRITER_AND_CRASH_SAFE_SINK_LOCAL_CONTROLS', 'controlled_boundary': None, 'cold_reopen': True, 'exact_replay': True, 'replay_symbols': ['JsonlWriter.write_once'], 'concurrent': True, 'native_local_names': ['records', 'results', 'before', 'after'], 'dispatch_scope': 'ACTUAL_CAPTURE_RECORD_APPEND_LEAF_ENTRIES_FROM_FLOW_ENTRY_TO_FLOW_EXIT; NO_MODEL_PROVIDER', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CAPTURE_RECORD_OWNER_HAS_NO_CANONICAL_PROVIDER_TOKEN_ACCOUNT', 'evidence_scope': 'EXACT_NATIVE_CAPTURE_RECORD_OWNER_ONLY; NO_PROVIDER_ACCOUNTING_ZERO', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['JsonlWriter.__init__']}
POINT={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\rag\\data_capture\\jsonl_writer.py', 'sha256': '43538ed75e1c53c2110ba86de8d064544e8410e7f05c72bada6be392845234c6', 'function': 'JsonlWriter.write_once', 'line': 101, 'native_operation': 'ACTUAL_NEW_BOUNDED_NATIVE_CAPTURE_OWNER_CONTROL', 'native_refs': []}
@pytest.fixture(autouse=True)
def native_capture(tmp_path):
    capture=RemainingNativeCapture(DECLARATION,tmp_path).install()
    try:
        yield capture
    finally:
        capture.close()

def test_native_capture_independent_cold_exact_replay_preserves_original_bytes(tmp_path,native_capture):
    native_capture.begin_flow()
    try:
        path=tmp_path/"records.jsonl"
        first=JsonlWriter(path)
        record={"capture_id":"exact-native-capture","payload":{"value":1}}
        first.write_once(record,id_field="capture_id")
        before=path.read_bytes()
        cold=JsonlWriter(path)
        assert cold is not first
        cold.write_once(record,id_field="capture_id")
        after=path.read_bytes()
        records=CrashSafeJsonlRecordSink(path).read_records()
        assert before==after and records==[record]
    finally:
        native_capture.safe_emit(row="D2/REC-C",selector='backend/tests/test_fpbca_final65_rec_c_native_capture_controls.py'+"::test_native_capture_independent_cold_exact_replay_preserves_original_bytes",local_values=locals(),original_point=POINT,flow={**FLOW,"concurrent":False})

def test_native_capture_two_independent_writers_same_identity_single_native_append(tmp_path,native_capture):
    native_capture.begin_flow()
    try:
        path=tmp_path/"records.jsonl"
        owners=[JsonlWriter(path),JsonlWriter(path)]
        record={"capture_id":"concurrent-native-capture","payload":{"value":1}}
        barrier=Barrier(2)
        def worker(index):
            barrier.wait(timeout=5)
            return owners[index].write_once(record,id_field="capture_id")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(worker,(0,1)))
        records=CrashSafeJsonlRecordSink(path).read_records()
        assert results==[path,path] and records==[record]
    finally:
        native_capture.safe_emit(row="D2/REC-C",selector='backend/tests/test_fpbca_final65_rec_c_native_capture_controls.py'+"::test_native_capture_two_independent_writers_same_identity_single_native_append",local_values=locals(),original_point=POINT,flow=FLOW)
