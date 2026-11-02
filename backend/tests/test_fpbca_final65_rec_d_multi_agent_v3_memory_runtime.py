"""Fresh observation copies of unchanged original local native controls.
Original owner calls/assertions remain intact. No production implementation.
"""
import importlib as _fpbca_importlib
import pytest as _fpbca_pytest
from fpbca_final65_d2_native_capture import RemainingNativeCapture as _FPBCA_RemainingNativeCapture
_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_memory_runtime')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_composition.py', 'sha256': 'e64dffb3a14f53db7a31061574f5895b2518f393cd1f35d54c87c2a47b67468c', 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_composition', 'symbol': 'TargetDurableMemoryComposition.__init__', 'line': 49, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/tests/test_multi_agent_v3_memory_runtime.py', 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'module': 'test_multi_agent_v3_memory_runtime', 'symbol': '_FakePgConnection.__init__', 'line': 67, 'kind': 'COLD_CONSTRUCTOR', 'plain_method': True, 'instance_attributes': ['database'], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/tests/test_multi_agent_v3_memory_runtime.py', 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'module': 'test_multi_agent_v3_memory_runtime', 'symbol': '_FakePgConnection.commit', 'line': 77, 'kind': 'ACTUAL_DISPATCH', 'plain_method': True, 'instance_attributes': ['database', 'memory', 'operation_to_memory', 'selector_index'], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/tests/test_multi_agent_v3_memory_runtime.py', 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'module': 'test_multi_agent_v3_memory_runtime', 'symbol': '_FakePgConnection.rollback', 'line': 86, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': ['database'], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_composition.py', 'sha256': 'e64dffb3a14f53db7a31061574f5895b2518f393cd1f35d54c87c2a47b67468c', 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_composition', 'symbol': 'TargetDurableMemoryComposition.project_artifact', 'line': 473, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_store.py', 'sha256': 'd5a91efce45aa588e1ecf2febb7986dee5d9981a249b353fdc9cc777c5281b7b', 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_store', 'symbol': 'PostgresDurableMemoryStore.put_memory_record_if_absent_or_same', 'line': 369, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_runtime.py', 'sha256': 'eaf1c37234a16e897ec2ecaf9a1c969ba9732681449f223a3a4cb0acc2b426b4', 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_runtime', 'symbol': 'DurableMemoryRuntime.resolve_record', 'line': 831, 'kind': 'OWNER', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}, {'path': 'backend/apps/enterprise_document/services/agent_rag_v1/artifact_runtime.py', 'sha256': 'ad2d0f12b4774fa52d2b583516099aaf0338770be4bc6ad0ef1f254d5a146f3f', 'module': 'apps.enterprise_document.services.agent_rag_v1.artifact_runtime', 'symbol': 'ArtifactRuntime.resolve_active', 'line': 1100, 'kind': 'CURRENTNESS', 'plain_method': True, 'instance_attributes': [], 'owned_file_attributes': [], 'planned_export': False}], 'original_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_memory_runtime.py', 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97'}, 'row': 'D2/REC-D', 'source_refresh': 'PREPARATION_ONLY_FINAL_C2_D3_EPOCH_REBUILD_ROOT_REVIEW_REQUIRED'}
@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_d2_capture(tmp_path):
    capture = _FPBCA_RemainingNativeCapture(_FPBCA_DECLARATION, tmp_path).install(namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_postgres_transaction_commit_failure_rolls_back_canonical_record(tmp_path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        registry = _SourceRegistry()
        registry.add('f1', topic='network')
        db = _FakePgDatabase()
        runtime = _runtime(db, registry)
        db.fail_commit_once = True
        with pytest.raises(MemoryStoreUnavailable):
            runtime.project_source(_request(_source('f1')))
        assert db.memory == {}
        assert db.selector_index == {}
        assert db.rollbacks >= 1
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-D', selector='backend/tests/test_fpbca_final65_rec_d_multi_agent_v3_memory_runtime.py::test_native_observed_postgres_transaction_commit_failure_rolls_back_canonical_record', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_memory_runtime.py', 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'function': 'test_postgres_transaction_commit_failure_rolls_back_canonical_record', 'line': 730, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_DURABLE_MEMORY_NATIVE_STORE_WITH_CONTROLLED_DB_API_FAILURE; NOT_LIVE_POSTGRES', 'controlled_boundary': None, 'cold_reopen': True, 'exact_replay': False, 'replay_symbols': [], 'concurrent': False, 'native_local_names': ['record', 'reference', 'ref', 'result'], 'dispatch_scope': 'ACTUAL_CONTROLLED_DB_API_COMMIT_ATTEMPTS_WITH_RETURN_OR_EXCEPTION; NOT_SERVER_IO', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CONTROLLED_MEMORY_TRANSACTION_FIXTURE_HAS_NO_CANONICAL_PROVIDER_ACCOUNT', 'evidence_scope': 'EXACT_CONTROLLED_DB_API_ONLY; ROLLBACK_AND_COMMITS_REMAIN_SEPARATE_NATIVE_FACTS', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['TargetDurableMemoryComposition.__init__']})

def test_native_observed_index_failure_after_memory_commit_does_not_roll_back_memory(tmp_path, _fpbca_d2_capture) -> None:
    _fpbca_d2_capture.begin_flow()
    try:
        registry = _SourceRegistry()
        registry.add('f1', topic='network')
        db = _FakePgDatabase()
        runtime = _runtime(db, registry)
        db.fail_index_upsert_once = True
        with pytest.raises(MemoryIndexProjectionError) as exc_info:
            runtime.project_source(_request(_source('f1')))
        memory_ref = exc_info.value.memory_ref
        assert runtime.store.get_memory_record(memory_ref) is not None
        assert memory_ref.memory_id in db.memory
        assert memory_ref.memory_id not in db.selector_index
    finally:
        _fpbca_d2_capture.safe_emit(row='D2/REC-D', selector='backend/tests/test_fpbca_final65_rec_d_multi_agent_v3_memory_runtime.py::test_native_observed_index_failure_after_memory_commit_does_not_roll_back_memory', local_values=locals(), original_point={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_memory_runtime.py', 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'function': 'test_index_failure_after_memory_commit_does_not_roll_back_memory', 'line': 810, 'native_operation': 'EXACT_UNCHANGED_ORIGINAL_NATIVE_CONTROL_FLOW', 'native_refs': []}, flow={'scope': 'ACTUAL_DURABLE_MEMORY_NATIVE_STORE_WITH_CONTROLLED_DB_API_FAILURE; NOT_LIVE_POSTGRES', 'controlled_boundary': None, 'cold_reopen': True, 'exact_replay': False, 'replay_symbols': [], 'concurrent': False, 'native_local_names': ['record', 'reference', 'ref', 'result'], 'dispatch_scope': 'ACTUAL_CONTROLLED_DB_API_COMMIT_ATTEMPTS_WITH_RETURN_OR_EXCEPTION; NOT_SERVER_IO', 'field_dispositions': {'actual_settlement_count': {'status': 'SOURCE_BOUND_N_A', 'reason': 'CONTROLLED_MEMORY_TRANSACTION_FIXTURE_HAS_NO_CANONICAL_PROVIDER_ACCOUNT', 'evidence_scope': 'EXACT_CONTROLLED_DB_API_ONLY; ROLLBACK_AND_COMMITS_REMAIN_SEPARATE_NATIVE_FACTS', 'source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085', 'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'section': 'D5 required native fields WHERE RELEVANT'}}}, 'cold_symbols': ['TargetDurableMemoryComposition.__init__']})
