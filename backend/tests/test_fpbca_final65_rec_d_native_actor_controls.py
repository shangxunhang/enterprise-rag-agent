"""Finite two-actor Memory native control; controlled DB API, no live PG."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
import pytest
from apps.enterprise_document.services.agent_rag_v1.memory_composition import TargetDurableMemoryComposition
from apps.enterprise_document.services.agent_rag_v1.memory_runtime import DurableMemoryRuntime
from test_step7_durable_memory_persistence import _setup, _commit
from fpbca_final65_d2_native_capture import RemainingNativeCapture
DECLARATION={'hooks': [{'instance_attributes': [], 'kind': 'COLD_CONSTRUCTOR', 'line': 49, 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_composition', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_composition.py', 'plain_method': True, 'planned_export': False, 'sha256': 'e64dffb3a14f53db7a31061574f5895b2518f393cd1f35d54c87c2a47b67468c', 'symbol': 'TargetDurableMemoryComposition.__init__'}, {'instance_attributes': ['database'], 'kind': 'COLD_CONSTRUCTOR', 'line': 67, 'module': 'test_multi_agent_v3_memory_runtime', 'owned_file_attributes': [], 'path': 'backend/tests/test_multi_agent_v3_memory_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'symbol': '_FakePgConnection.__init__'}, {'instance_attributes': ['database', 'memory', 'operation_to_memory', 'selector_index'], 'kind': 'ACTUAL_DISPATCH', 'line': 77, 'module': 'test_multi_agent_v3_memory_runtime', 'owned_file_attributes': [], 'path': 'backend/tests/test_multi_agent_v3_memory_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'symbol': '_FakePgConnection.commit'}, {'instance_attributes': ['database'], 'kind': 'OWNER', 'line': 86, 'module': 'test_multi_agent_v3_memory_runtime', 'owned_file_attributes': [], 'path': 'backend/tests/test_multi_agent_v3_memory_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': 'dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97', 'symbol': '_FakePgConnection.rollback'}, {'instance_attributes': [], 'kind': 'CURRENTNESS', 'line': 473, 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_composition', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_composition.py', 'plain_method': True, 'planned_export': False, 'sha256': 'e64dffb3a14f53db7a31061574f5895b2518f393cd1f35d54c87c2a47b67468c', 'symbol': 'TargetDurableMemoryComposition.project_artifact'}, {'instance_attributes': [], 'kind': 'OWNER', 'line': 369, 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_store', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_store.py', 'plain_method': True, 'planned_export': False, 'sha256': 'd5a91efce45aa588e1ecf2febb7986dee5d9981a249b353fdc9cc777c5281b7b', 'symbol': 'PostgresDurableMemoryStore.put_memory_record_if_absent_or_same'}, {'instance_attributes': [], 'kind': 'OWNER', 'line': 831, 'module': 'apps.enterprise_document.services.agent_rag_v1.memory_runtime', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/memory_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': 'eaf1c37234a16e897ec2ecaf9a1c969ba9732681449f223a3a4cb0acc2b426b4', 'symbol': 'DurableMemoryRuntime.resolve_record'}, {'instance_attributes': [], 'kind': 'CURRENTNESS', 'line': 1100, 'module': 'apps.enterprise_document.services.agent_rag_v1.artifact_runtime', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/artifact_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': 'ad2d0f12b4774fa52d2b583516099aaf0338770be4bc6ad0ef1f254d5a146f3f', 'symbol': 'ArtifactRuntime.resolve_active'}, {'instance_attributes': [], 'kind': 'CURRENTNESS', 'line': 1063, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'symbol': 'RunAttemptLifecycleService.load_run_lifecycle'}, {'instance_attributes': [], 'kind': 'CURRENTNESS', 'line': 1339, 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'owned_file_attributes': [], 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py', 'plain_method': True, 'planned_export': False, 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'symbol': 'RunAttemptLifecycleService.load_attempt'}], 'row': 'D2/REC-D'}
FLOW={'cold_reopen': False, 'cold_symbols': ['TargetDurableMemoryComposition.__init__'], 'concurrent': True, 'controlled_boundary': None, 'dispatch_scope': 'ACTUAL_CONTROLLED_DB_API_CONNECTION_COMMIT_ENTRIES_DURING_TWO_NATIVE_PROJECTION_CALLS; NOT_SERVER_IO_OR_MODEL_PROVIDER', 'exact_replay': False, 'field_dispositions': {'actual_settlement_count': {'evidence_scope': 'EXACT_REGISTERED_MEMORY_PROJECTION_ONLY; DB_COMMITS_ARE_NOT_PROVIDER_SETTLEMENTS', 'reason': 'CONTROLLED_DURABLE_MEMORY_DB_API_OWNER_FLOW_HAS_NO_MODEL_PROVIDER_TOKEN_ACCOUNT', 'source_decision': {'authority': 'D5_PHYSICAL_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'section': 'D5 required native fields WHERE RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'status': 'SOURCE_BOUND_N_A'}}, 'native_local_names': ['source_ref', 'results', 'records', 'native_before', 'native_after', 'actor_instances'], 'replay_symbols': ['TargetDurableMemoryComposition.project_artifact', 'PostgresDurableMemoryStore.put_memory_record_if_absent_or_same'], 'scope': 'ACTUAL_TWO_INDEPENDENT_NATIVE_MEMORY_COMPOSITIONS_WITH_EXISTING_CONTROLLED_DB_API_FIXTURE; NO_LIVE_PG_OR_SERVER_ATOMICITY_CLAIM'}
POINT={'function': 'TargetDurableMemoryComposition.project_artifact', 'line': 473, 'native_operation': 'ACTUAL_TWO_ACTOR_NATIVE_MEMORY_PROJECTION_ENTRY', 'native_refs': [], 'path': 'D:\\MyCode\\rag-agent-shared\\backend\\apps\\enterprise_document\\services\\agent_rag_v1\\memory_composition.py', 'sha256': 'e64dffb3a14f53db7a31061574f5895b2518f393cd1f35d54c87c2a47b67468c'}
@pytest.fixture(autouse=True)
def native_capture(tmp_path):
    capture=RemainingNativeCapture(DECLARATION,tmp_path).install()
    try:
        yield capture
    finally:
        capture.close()

def test_two_independent_native_memory_actors_same_source_preserve_exact_result_and_original_source(tmp_path,native_capture):
    fact,db,first=_setup(tmp_path/'native')
    source_ref=_commit(fact)
    second=TargetDurableMemoryComposition(required=True,
        runtime=DurableMemoryRuntime(store=first.runtime.store),fact_snapshot_runtime=fact,
        lifecycle_service=fact.artifact_runtime.lifecycle_service)
    actor_instances=[first,second]
    native_before=fact.artifact_runtime.ledger_path.read_bytes()
    native_capture.begin_flow()
    try:
        barrier=Barrier(2)
        def worker(index):
            barrier.wait(timeout=5)
            value=actor_instances[index].project_artifact(source_ref)
            return {'actual_thread_id':get_ident(),'actual_memory_refs':value}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(worker,(0,1)))
        records=[actor_instances[i].runtime.resolve_record(row['actual_memory_refs'][0])
                 for i,row in enumerate(results)]
        native_after=fact.artifact_runtime.ledger_path.read_bytes()
        assert first is not second and first.runtime is not second.runtime
        assert len({row['actual_thread_id'] for row in results})==2
        assert results[0]['actual_memory_refs']==results[1]['actual_memory_refs']
        assert len(results[0]['actual_memory_refs'])==1 and len(db.memory)==1
        assert records[0]==records[1]
        assert records[0].source_binding.primary_source.exact_source_ref==source_ref.model_dump(mode='json')
        assert native_before==native_after
        # No assertion of server transaction counts, livePG atomics, Model0,
        # or accounting0. The existing controlled API entry counts are observed.
    finally:
        native_capture.safe_emit(row='D2/REC-D',selector='backend/tests/test_fpbca_final65_rec_d_native_actor_controls.py::test_two_independent_native_memory_actors_same_source_preserve_exact_result_and_original_source',
            local_values=locals(),original_point=POINT,flow=FLOW)
