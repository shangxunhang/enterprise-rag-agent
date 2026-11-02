"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_quantitative_visibility')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 137, 'line': 112, 'path': 'backend/rag/context/context_gate.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '7ae488fe4ca12a9b8f474c291f95f4bd10b4fd197b0d928bb1f8a95ee0aa04ad', 'symbol': 'ContextGate.pack', 'module': 'rag.context.context_gate', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 520, 'line': 505, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'TableRowRecordChunker.chunk_records', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 190, 'line': 167, 'path': 'backend/rag/schema/prompt_evidence_candidate_v1.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'f8cc988e8ab1ae1224b0a62f3faf0ec9006f947d445f0d89516c3eb15b893840', 'symbol': 'build_prompt_evidence_candidate_v1', 'module': 'rag.schema.prompt_evidence_candidate_v1', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 356, 'line': 230, 'path': 'backend/rag/schema/retrieval_candidate_v2.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a2eb7a9b0102aa6da7a43bad42f4fc832f83883536a37362d13804748e652a11', 'symbol': 'build_retrieval_candidate_v2', 'module': 'rag.schema.retrieval_candidate_v2', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 412, 'line': 288, 'path': 'backend/rag/schema/Retrieval_Result_Schema.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a8961d8c3c323e8d4864cb3c76a56c6d85c3842980da51005c659b0e4d32d0a6', 'symbol': 'build_retrieval_result_v2', 'module': 'rag.schema.Retrieval_Result_Schema', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 309, 'line': 148, 'path': 'backend/rag/schema/structured_evidence.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'ff7adc24013a381344dd7c0bc6db8921948dc6917ce167b07bd40a990c037960', 'symbol': 'build_structured_evidence', 'module': 'rag.schema.structured_evidence', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 132, 'line': 64, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'parse_structured_table', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V10': ['build_prompt_evidence_candidate_v1', 'ContextGate.pack'], 'V12': ['build_retrieval_result_v2', 'build_retrieval_candidate_v2', 'build_prompt_evidence_candidate_v1'], 'V13': ['parse_structured_table', 'TableRowRecordChunker.chunk_records', 'build_structured_evidence', 'ContextPacker.pack']}, 'row_exact_final_consumer_symbols': {'V10': [], 'V12': [], 'V13': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_quantitative_visibility.py', 'sha256': '2bc3879c3cdc058d62d2389fc7b9b01cb63713dcf66c39b42da617ecaaee2a7c'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('source,visible,value,unit,expected', [('预算金额：250万元', '预算金额：250', '250', '万元', False), ('预算金额：250万元', '预算金额：250万元', '250', '万元', True), ('预算金额：约250万元', '预算金额：250万元', '250', '万元', False), ('预算金额：约250万元', '预算金额：约250万元', '250', '万元', True), ('预算金额：250万元', '预算金额：2.5百万元', '250', '万元', False), ('预算金额：２５０万元', '预算金额: 250 万元', '250', '万元', True), ('预算金额：250万元', '预算金额：250万元', '250万元', '万元', True), ('预算金额：250', '预算金额：250', '250', '万元', False)])
def test_native_observed_complete_declared_source_witness(source, visible, value, unit, expected, tmp_path, _fpbca_native_case_capture):
    try:
        row = retrieval_result(text=source, value=value, unit=unit)
        row['prompt_visible_text'] = visible
        candidate = build_prompt_evidence_candidate_v1(row)
        assert candidate.fact_visible_in_prompt is expected
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V10', 'V12'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_quantitative_visibility.py::test_native_observed_complete_declared_source_witness', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_quantitative_visibility.py', 'sha256': '2bc3879c3cdc058d62d2389fc7b9b01cb63713dcf66c39b42da617ecaaee2a7c', 'function': 'test_complete_declared_source_witness', 'line': 15, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=['candidate'])

def test_native_observed_component_fallback_without_source_span_requires_declared_unit(tmp_path, _fpbca_native_case_capture):
    try:
        row = retrieval_result(text='预算金额：250万元', value='250')
        row['child_chunk']['extra']['source_span'] = {}
        row['prompt_visible_text'] = '预算金额：250'
        assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is False
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V10'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_quantitative_visibility.py::test_native_observed_component_fallback_without_source_span_requires_declared_unit', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_quantitative_visibility.py', 'sha256': '2bc3879c3cdc058d62d2389fc7b9b01cb63713dcf66c39b42da617ecaaee2a7c', 'function': 'test_component_fallback_without_source_span_requires_declared_unit', 'line': 43, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_different_candidates_do_not_reconstruct_split_unit(tmp_path, _fpbca_native_case_capture):
    try:
        row = retrieval_result(text='预算金额：250万元', value='250')
        row['prompt_visible_text'] = '预算金额：250'
        assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is False
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V10'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_quantitative_visibility.py::test_native_observed_different_candidates_do_not_reconstruct_split_unit', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_quantitative_visibility.py', 'sha256': '2bc3879c3cdc058d62d2389fc7b9b01cb63713dcf66c39b42da617ecaaee2a7c', 'function': 'test_different_candidates_do_not_reconstruct_split_unit', 'line': 26, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_generated_delivery_qualifier_not_required_in_source_span(tmp_path, _fpbca_native_case_capture):
    try:
        row = retrieval_result(text='预算金额：250万元', value='250', fact_status='estimated')
        row['prompt_visible_text'] = '预算金额：250万元'
        assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is True
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V12'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_quantitative_visibility.py::test_native_observed_generated_delivery_qualifier_not_required_in_source_span', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_quantitative_visibility.py', 'sha256': '2bc3879c3cdc058d62d2389fc7b9b01cb63713dcf66c39b42da617ecaaee2a7c', 'function': 'test_generated_delivery_qualifier_not_required_in_source_span', 'line': 21, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('visible,expected', [('项目: 软件开发\n金额: 约250万元', True), ('项目: 软件开发\n金额: 250万元', False), ('项目: 软件开发\n金额: 约250', False)])
def test_native_observed_native_table_json_value_uses_complete_rendered_source_witness(visible, expected, tmp_path, _fpbca_native_case_capture):
    try:
        import json
        value = json.dumps({'项目': '软件开发', '金额': '约250万元'}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        row = retrieval_result(text='项目: 软件开发\n金额: 约250万元', value=value, predicate='table_row')
        row['prompt_visible_text'] = visible
        assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is expected
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V10', 'V12', 'V13'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_quantitative_visibility.py::test_native_observed_native_table_json_value_uses_complete_rendered_source_witness', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_quantitative_visibility.py', 'sha256': '2bc3879c3cdc058d62d2389fc7b9b01cb63713dcf66c39b42da617ecaaee2a7c', 'function': 'test_native_table_json_value_uses_complete_rendered_source_witness', 'line': 36, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
