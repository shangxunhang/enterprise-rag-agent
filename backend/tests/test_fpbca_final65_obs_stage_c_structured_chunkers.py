"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_stage_c_structured_chunkers')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 959, 'line': 909, 'path': 'backend/rag/evidence/governance_v2.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '34636ce3e2a32ab3819f70d2ef7617026c7083dc6f8c7718431b27d7d9e96f2c', 'symbol': 'OnlineEvidenceGovernanceV2.evaluate', 'module': 'rag.evidence.governance_v2', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 363, 'line': 348, 'path': 'backend/rag/plugins/chunkers/policy_article_clause.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd26b4de1ba9ccf480d1f97fe4fc00a00dd339ac853a0731be093ff5eed7dc2e9', 'symbol': 'PolicyArticleClauseChunker.chunk_records', 'module': 'rag.plugins.chunkers.policy_article_clause', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 520, 'line': 505, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'TableRowRecordChunker.chunk_records', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 278, 'line': 263, 'path': 'backend/rag/plugins/chunkers/template_section.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '9f2698d80c1a4fa5b1818cd4814a4d5a7da6479e17a2ccd867c9cfdb640450b7', 'symbol': 'TemplateSectionChunker.chunk_records', 'module': 'rag.plugins.chunkers.template_section', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 309, 'line': 148, 'path': 'backend/rag/schema/structured_evidence.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'ff7adc24013a381344dd7c0bc6db8921948dc6917ce167b07bd40a990c037960', 'symbol': 'build_structured_evidence', 'module': 'rag.schema.structured_evidence', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 132, 'line': 64, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'parse_structured_table', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V11': ['TableRowRecordChunker.chunk_records', 'PolicyArticleClauseChunker.chunk_records', 'ContextPacker.pack'], 'V13': ['parse_structured_table', 'TableRowRecordChunker.chunk_records', 'build_structured_evidence', 'ContextPacker.pack'], 'V14': ['TemplateSectionChunker.chunk_records', 'OnlineEvidenceGovernanceV2.evaluate', 'ContextPacker.pack'], 'V15': ['PolicyArticleClauseChunker.chunk_records', 'build_structured_evidence', 'ContextPacker.pack']}, 'row_exact_final_consumer_symbols': {'V11': [], 'V13': [], 'V14': [], 'V15': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_c_structured_chunkers.py', 'sha256': '99171d10c101b06ad47c39df4e57385f0cce47b6c2f40538afc19995cbe599d9'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_policy_chunker_discloses_fallback_when_structure_is_absent(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        row = _unit(document_id='doc-policy-fallback', strategy='policy_article_clause', order=1, text='本文件说明一般工作原则，但没有条款编号。', kb_id='policy_standard_kb', evidence_role='policy_standard')
        result = PolicyArticleClauseChunker().chunk_records([row])
        chunking = result.children[0]['extra']['type_aware_chunking']
        assert chunking['fallback_used'] is True
        assert chunking['fallback_reason'] == 'no_policy_clause_structure'
        assert chunking['actual_strategy'] == 'paragraph_text_evidence'
        assert chunking['representation_type'] == 'text_evidence'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V15'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_c_structured_chunkers.py::test_native_observed_policy_chunker_discloses_fallback_when_structure_is_absent', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_c_structured_chunkers.py', 'sha256': '99171d10c101b06ad47c39df4e57385f0cce47b6c2f40538afc19995cbe599d9', 'function': 'test_policy_chunker_discloses_fallback_when_structure_is_absent', 'line': 186, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_policy_chunker_filters_toc_and_preserves_article_clause_path(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        rows = [_unit(document_id='doc-policy', strategy='policy_article_clause', order=1, text='目录', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=2, text='第一章 总则 ........ 1', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=3, text='第一章 总则', unit_type='title', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=4, text='第一条 适用范围', unit_type='title', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=5, text='本办法适用于测试范围。', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=6, text='（一）申请人应提交材料。', unit_type='list', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=7, text='第二条 审核要求', unit_type='title', kb_id='policy_standard_kb', evidence_role='policy_standard'), _unit(document_id='doc-policy', strategy='policy_article_clause', order=8, text='审核机关应依法办理。', kb_id='policy_standard_kb', evidence_role='policy_standard')]
        result = PolicyArticleClauseChunker().chunk_records(rows)
        assert len(result.parents) == 2
        assert all((child['extra']['type_aware_chunking']['representation_type'] == 'policy_clause' for child in result.children))
        assert all((child['extra']['is_table_of_contents'] is False for child in result.children))
        assert all(('........ 1' not in child['text'] for child in result.children))
        article_ids = {child['extra']['article_id'] for child in result.children}
        assert article_ids == {'article_一', 'article_二'}
        assert any((child['extra']['clause_id'] == '（一）' for child in result.children))
        assert not any(('第二条' in child['text'] and '第一条' in child['text'] for child in result.children))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V11', 'V15'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_c_structured_chunkers.py::test_native_observed_policy_chunker_filters_toc_and_preserves_article_clause_path', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_c_structured_chunkers.py', 'sha256': '99171d10c101b06ad47c39df4e57385f0cce47b6c2f40538afc19995cbe599d9', 'function': 'test_policy_chunker_filters_toc_and_preserves_article_clause_path', 'line': 156, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_structured_table_binds_headers_rows_and_merged_cells(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        payload = {'column_count': 3, 'row_count': 4, 'merged_cells': [{'row': 3, 'column': 1, 'status': 'merged_or_unavailable'}], 'rows': [['设备', '数量', '单位'], ['服务器', '4', '台'], ['', '2', '台'], ['交换机', '8', '台']]}
        header, rows = parse_structured_table(payload)
        assert header == ['设备', '数量', '单位']
        assert len(rows) == 3
        assert rows[1].normalized_fields['设备'] == '服务器'
        assert rows[1].inherited_cells == {'设备': '服务器'}
        unit = _unit(document_id='doc-table', strategy='table_row_record', order=1, text='| 设备 | 数量 | 单位 |', unit_type='table', extra={'structured_table': payload, 'structured_table_asset': {'uri': 'tables/doc-table.json', 'sha256': 'd' * 64}})
        result = TableRowRecordChunker().chunk_records([unit])
        assert len(result.children) == 3
        assert [child['extra']['row_index'] for child in result.children] == [2, 3, 4]
        assert all((child['extra']['type_aware_chunking']['representation_type'] == 'table_row' for child in result.children))
        assert all((child['extra']['type_aware_chunking']['fallback_used'] is False for child in result.children))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V13'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_c_structured_chunkers.py::test_native_observed_structured_table_binds_headers_rows_and_merged_cells', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_c_structured_chunkers.py', 'sha256': '99171d10c101b06ad47c39df4e57385f0cce47b6c2f40538afc19995cbe599d9', 'function': 'test_structured_table_binds_headers_rows_and_merged_cells', 'line': 203, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_table_row_parent_chunks_obey_configured_char_budget(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        payload = {'column_count': 2, 'row_count': 7, 'merged_cells': [], 'rows': [['字段', '值'], *[[f'字段{index}', '甲' * 180] for index in range(1, 7)]]}
        unit = _unit(document_id='doc-table-budget', strategy='table_row_record', order=1, text='| 字段 | 值 |', unit_type='table', extra={'structured_table': payload})
        result = TableRowRecordChunker(parent_chunk_size=450).chunk_records([unit])
        assert len(result.children) == 6
        assert len(result.parents) == 3
        assert all((len(parent['text']) <= 450 for parent in result.parents))
        assert sum((len(parent['child_chunk_ids']) for parent in result.parents)) == 6
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V11'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_c_structured_chunkers.py::test_native_observed_table_row_parent_chunks_obey_configured_char_budget', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_c_structured_chunkers.py', 'sha256': '99171d10c101b06ad47c39df4e57385f0cce47b6c2f40538afc19995cbe599d9', 'function': 'test_table_row_parent_chunks_obey_configured_char_budget', 'line': 248, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_template_sections_keep_placeholders_and_never_gain_fact_claims(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        allowed = ['template_structure', 'style_guidance']
        rows = [_unit(document_id='doc-template', strategy='template_section', order=1, text='第一章 项目概况', unit_type='title', kb_id='template_style_kb', evidence_role='template_style', allowed=allowed), _unit(document_id='doc-template', strategy='template_section', order=2, text='项目名称：{{project_name}}\n必填：建设单位\n示例投资100万元。正文采用宋体。', kb_id='template_style_kb', evidence_role='template_style', allowed=allowed)]
        result = TemplateSectionChunker().chunk_records(rows)
        child = result.children[0]
        assert child['kb_id'] == 'template_style_kb'
        assert child['extra']['document_profile']['evidence_role'] == 'template_style'
        assert set(child['extra']['template_claim_types']) == set(allowed)
        assert 'project_name' in child['extra']['placeholder_fields']
        assert child['extra']['is_example_content'] is True
        assert '100万元' in child['extra']['example_values']
        assert 'current_project_fact' not in child['extra']['template_claim_types']
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V11', 'V14'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_c_structured_chunkers.py::test_native_observed_template_sections_keep_placeholders_and_never_gain_fact_claims', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_c_structured_chunkers.py', 'sha256': '99171d10c101b06ad47c39df4e57385f0cce47b6c2f40538afc19995cbe599d9', 'function': 'test_template_sections_keep_placeholders_and_never_gain_fact_claims', 'line': 392, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
