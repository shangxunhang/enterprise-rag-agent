"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_stage_d_structured_evidence_projection')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 363, 'line': 348, 'path': 'backend/rag/plugins/chunkers/policy_article_clause.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd26b4de1ba9ccf480d1f97fe4fc00a00dd339ac853a0731be093ff5eed7dc2e9', 'symbol': 'PolicyArticleClauseChunker.chunk_records', 'module': 'rag.plugins.chunkers.policy_article_clause', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 520, 'line': 505, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'TableRowRecordChunker.chunk_records', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 190, 'line': 167, 'path': 'backend/rag/schema/prompt_evidence_candidate_v1.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'f8cc988e8ab1ae1224b0a62f3faf0ec9006f947d445f0d89516c3eb15b893840', 'symbol': 'build_prompt_evidence_candidate_v1', 'module': 'rag.schema.prompt_evidence_candidate_v1', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 356, 'line': 230, 'path': 'backend/rag/schema/retrieval_candidate_v2.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a2eb7a9b0102aa6da7a43bad42f4fc832f83883536a37362d13804748e652a11', 'symbol': 'build_retrieval_candidate_v2', 'module': 'rag.schema.retrieval_candidate_v2', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 412, 'line': 288, 'path': 'backend/rag/schema/Retrieval_Result_Schema.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a8961d8c3c323e8d4864cb3c76a56c6d85c3842980da51005c659b0e4d32d0a6', 'symbol': 'build_retrieval_result_v2', 'module': 'rag.schema.Retrieval_Result_Schema', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 309, 'line': 148, 'path': 'backend/rag/schema/structured_evidence.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'ff7adc24013a381344dd7c0bc6db8921948dc6917ce167b07bd40a990c037960', 'symbol': 'build_structured_evidence', 'module': 'rag.schema.structured_evidence', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 132, 'line': 64, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'parse_structured_table', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V11': ['TableRowRecordChunker.chunk_records', 'PolicyArticleClauseChunker.chunk_records', 'ContextPacker.pack'], 'V12': ['build_retrieval_result_v2', 'build_retrieval_candidate_v2', 'build_prompt_evidence_candidate_v1'], 'V13': ['parse_structured_table', 'TableRowRecordChunker.chunk_records', 'build_structured_evidence', 'ContextPacker.pack']}, 'row_exact_final_consumer_symbols': {'V11': [], 'V12': [], 'V13': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_d_structured_evidence_projection.py', 'sha256': '4c900f2de2c7ae14168e163575eb8055df5ef7d0d92c8ae5ed911c38589679e2'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize(('representation_type', 'expected_field'), [('fact_record', 'fact'), ('table_row', 'table'), ('policy_clause', 'policy'), ('template_section', 'template'), ('text_evidence', 'fallback'), ('paragraph_text', 'fallback')])
def test_native_observed_all_stage_c_representation_types_have_stable_structured_contract(representation_type: str, expected_field: str, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        profile = document_profile()
        if representation_type == 'policy_clause':
            profile = document_profile(kb_id='policy_standard_kb', document_type='policy_standard', evidence_role='policy_standard', project_relation='general', project_id=None, authority_level='official', allowed_claim_types=['policy_requirement'])
        elif representation_type == 'template_section':
            profile = document_profile(kb_id='template_style_kb', document_type='document_template', evidence_role='template_style', project_relation='general', project_id=None, authority_level='draft', allowed_claim_types=['template_structure', 'style_guidance'])
        result = retrieval_result(profile=profile, representation_type=representation_type)
        structured = build_structured_evidence(result)
        payload = structured.model_dump(mode='json', exclude_none=True)
        assert payload['schema_version'] == 'structured_evidence_v1'
        assert payload['representation_type'] == representation_type
        assert expected_field in payload
        if representation_type == 'text_evidence':
            assert payload['fallback']['used'] is True
            assert payload['fallback']['reason'] == 'unsupported_fact_structure'
        if representation_type == 'paragraph_text':
            assert payload['fallback']['used'] is False
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V11', 'V13'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_d_structured_evidence_projection.py::test_native_observed_all_stage_c_representation_types_have_stable_structured_contract', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_d_structured_evidence_projection.py', 'sha256': '4c900f2de2c7ae14168e163575eb8055df5ef7d0d92c8ae5ed911c38589679e2', 'function': 'test_all_stage_c_representation_types_have_stable_structured_contract', 'line': 117, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['structured'])

def test_native_observed_real_fact_shape_propagates_to_result_candidate_and_prompt(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        result = retrieval_result(text='投资估算：250万元', fact_status='estimated', value='250万元', unit='万元')
        child = normalize_child_chunk_record(result['child_chunk'])
        retrieval_v2 = build_retrieval_result_v2(child_chunk=child, parent_chunk={'parent_chunk_id': result['parent_chunk_id'], 'doc_id': result['doc_id'], 'tenant_id': result['tenant_id'], 'kb_id': result['kb_id'], 'text': '投资估算章节。投资估算：250万元', 'extra': {'document_profile': result['extra']['document_profile']}}, rank=1, score=0.9)
        candidate = build_retrieval_candidate_v2(retrieval_v2)
        selected = prompt_selected(retrieval_v2, prompt_text='预算金额：250万元')
        prompt = build_prompt_evidence_candidate_v1(selected)
        assert retrieval_v2['structured_evidence']['fact']['fact_status'] == 'estimated'
        assert candidate.structured_evidence.fact is not None
        assert candidate.structured_evidence.fact.predicate == '预算金额'
        assert candidate.structured_evidence.fact.value == '250万元'
        assert prompt.structured_evidence == candidate.structured_evidence
        assert prompt.fact_visible_in_prompt is True
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V12'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_d_structured_evidence_projection.py::test_native_observed_real_fact_shape_propagates_to_result_candidate_and_prompt', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_d_structured_evidence_projection.py', 'sha256': '4c900f2de2c7ae14168e163575eb8055df5ef7d0d92c8ae5ed911c38589679e2', 'function': 'test_real_fact_shape_propagates_to_result_candidate_and_prompt', 'line': 70, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['candidate', 'prompt', 'retrieval_v2'])
