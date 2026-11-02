"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_parent_identity')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 204, 'line': 134, 'path': 'backend/rag/plugins/candidate_enrichers/plugin.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '55aaa8d7186978ff909859a3e337a5ecc3225524cd15e005b69499e6a4fbd986', 'symbol': 'ParentChildCandidateEnricher.enrich', 'module': 'rag.plugins.candidate_enrichers.plugin', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 412, 'line': 288, 'path': 'backend/rag/schema/Retrieval_Result_Schema.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a8961d8c3c323e8d4864cb3c76a56c6d85c3842980da51005c659b0e4d32d0a6', 'symbol': 'build_retrieval_result_v2', 'module': 'rag.schema.Retrieval_Result_Schema', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V18': ['build_retrieval_result_v2', 'ParentChildCandidateEnricher.enrich', 'ContextPacker.pack']}, 'row_exact_final_consumer_symbols': {'V18': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_parent_identity.py', 'sha256': '69135be7d022bd5a705ab65eb97bea5b33c13cbcaddbc36d8bd799aa8007d226'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('mutation', ['missing_tenant', 'wrong_tenant', 'missing_kb', 'wrong_doc', 'missing_profile', 'wrong_source', 'wrong_profile', 'wrong_parent'])
def test_native_observed_bad_parent_falls_back_to_independently_valid_child(mutation, tmp_path, _fpbca_native_case_capture):
    try:
        child, parent = pair()
        if mutation == 'missing_tenant':
            parent.pop('tenant_id')
        elif mutation == 'wrong_tenant':
            parent['tenant_id'] = 'foreign'
        elif mutation == 'missing_kb':
            parent.pop('kb_id')
        elif mutation == 'wrong_doc':
            parent['doc_id'] = 'foreign'
        elif mutation == 'missing_profile':
            parent.pop('document_profile')
            parent['extra'].pop('document_profile', None)
        elif mutation == 'wrong_source':
            parent['document_profile']['source_sha256'] = 'f' * 64
        elif mutation == 'wrong_profile':
            parent['document_profile']['authority']['issuer'] = 'foreign'
        else:
            parent['parent_chunk_id'] = 'wrong'
        result = build_retrieval_result_v2(child_chunk=child, parent_chunk=parent, rank=1, score=1)
        assert result['text'] == child['text']
        assert result['parent_text'] == ''
        assert result['context_granularity'] == 'child'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V18'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_parent_identity.py::test_native_observed_bad_parent_falls_back_to_independently_valid_child', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_parent_identity.py', 'sha256': '69135be7d022bd5a705ab65eb97bea5b33c13cbcaddbc36d8bd799aa8007d226', 'function': 'test_bad_parent_falls_back_to_independently_valid_child', 'line': 25, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['result'])

def test_native_observed_exact_parent_retains_utility_and_identity(tmp_path, _fpbca_native_case_capture):
    try:
        child, parent = pair()
        result = build_retrieval_result_v2(child_chunk=child, parent_chunk=parent, rank=1, score=1)
        assert result['text'] == 'AUTHORIZED PARENT'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V18'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_parent_identity.py::test_native_observed_exact_parent_retains_utility_and_identity', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_parent_identity.py', 'sha256': '69135be7d022bd5a705ab65eb97bea5b33c13cbcaddbc36d8bd799aa8007d226', 'function': 'test_exact_parent_retains_utility_and_identity', 'line': 40, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['result'])

def test_native_observed_missing_child_identity_is_not_repaired_from_parent(tmp_path, _fpbca_native_case_capture):
    try:
        child, parent = pair()
        child.pop('tenant_id')
        with pytest.raises(ValueError):
            build_retrieval_result_v2(child_chunk=child, parent_chunk=parent, rank=1, score=1)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V18'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_parent_identity.py::test_native_observed_missing_child_identity_is_not_repaired_from_parent', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_parent_identity.py', 'sha256': '69135be7d022bd5a705ab65eb97bea5b33c13cbcaddbc36d8bd799aa8007d226', 'function': 'test_missing_child_identity_is_not_repaired_from_parent', 'line': 45, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_whole_matched_cohort_required_for_parent_expansion(tmp_path, _fpbca_native_case_capture):
    try:
        child, parent = pair()
        second = deepcopy(child)
        second['chunk_id'] = 'child-second'
        second['tenant_id'] = 'foreign'
        rows = [{'child_chunk': c, 'child_chunk_id': c['chunk_id'], 'parent_chunk_id': c['parent_chunk_id'], 'fusion_score': 1} for c in (child, second)]
        enricher = ParentChildCandidateEnricher(build_context={'resource_pool': SimpleNamespace(get_parent_store=lambda: SimpleNamespace(get=lambda _: parent))})
        result = enricher.enrich(CandidateSet(query='budget', source_name='test', candidates=rows))
        assert len(result.candidates) == 1
        assert result.candidates[0]['parent_text'] == ''
        assert 'foreign' not in str(result.candidates[0]['metadata'].get('matched_child_chunks'))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V18'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_parent_identity.py::test_native_observed_whole_matched_cohort_required_for_parent_expansion', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_parent_identity.py', 'sha256': '69135be7d022bd5a705ab65eb97bea5b33c13cbcaddbc36d8bd799aa8007d226', 'function': 'test_whole_matched_cohort_required_for_parent_expansion', 'line': 49, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
