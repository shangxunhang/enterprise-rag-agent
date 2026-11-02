"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_stage_d_evidence_governance_v2')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 959, 'line': 909, 'path': 'backend/rag/evidence/governance_v2.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '34636ce3e2a32ab3819f70d2ef7617026c7083dc6f8c7718431b27d7d9e96f2c', 'symbol': 'OnlineEvidenceGovernanceV2.evaluate', 'module': 'rag.evidence.governance_v2', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 278, 'line': 263, 'path': 'backend/rag/plugins/chunkers/template_section.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '9f2698d80c1a4fa5b1818cd4814a4d5a7da6479e17a2ccd867c9cfdb640450b7', 'symbol': 'TemplateSectionChunker.chunk_records', 'module': 'rag.plugins.chunkers.template_section', 'owner': True, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V14': ['TemplateSectionChunker.chunk_records', 'OnlineEvidenceGovernanceV2.evaluate', 'ContextPacker.pack']}, 'row_exact_final_consumer_symbols': {'V14': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_d_evidence_governance_v2.py', 'sha256': 'd3a63d164457665568b8e83b02f72cbcff2aac3747a13f166e92be5ae8033adc'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_template_evidence_only_supports_template_claims(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        profile = _profile(document_id='template-1', document_type='document_template', evidence_role='template_style', project_relation='general', authority_level='draft', allowed=['template_structure', 'style_guidance'], forbidden=['current_project_fact'], kb_id='template_style_kb')
        result = OnlineEvidenceGovernanceV2().evaluate(query='建设方案章节结构', results=[_result(child_id='template-child', document_id='template-1', text='建设方案章节结构包括项目背景、建设内容和投资估算', profile=profile, fact_category='template')], runtime_context=_context(topics=['章节结构'], claims=['template_structure']))
        assert result.item_assessments[0].eligibility is True
        assert _gate(result).delivery_mode is DeliveryMode.GROUNDED
        assert [item.value for item in _gate(result).allowed_claim_types] == ['style_guidance', 'template_structure']
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V14'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_stage_d_evidence_governance_v2.py::test_native_observed_template_evidence_only_supports_template_claims', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_stage_d_evidence_governance_v2.py', 'sha256': 'd3a63d164457665568b8e83b02f72cbcff2aac3747a13f166e92be5ae8033adc', 'function': 'test_template_evidence_only_supports_template_claims', 'line': 409, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
