"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_writer_input_boundary')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 500, 'line': 56, 'path': 'backend/model_gateway/fake_llm_client.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd8373490a7393d2a32aab9577f79a861d236d2fcc0b611ca28c6c662cb93e198', 'symbol': 'FakeLLMClient.generate', 'module': 'model_gateway.fake_llm_client', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1148, 'line': 1099, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/writer_context.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '36fa16c7ae1a36ccbd7e84679ffdf6a41b9ba474a2253cad9e02673987748b94', 'symbol': 'TargetWriterContextComposer.final_messages', 'module': 'apps.enterprise_document.services.agent_rag_v1.writer_context', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 85, 'line': 45, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/citation_association.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '887aa487d07e7f6652e723026337e58e6f77888658f7c0be6e0db44a9d34735c', 'symbol': 'associate_selected_citations', 'module': 'apps.enterprise_document.services.agent_rag_v1.citation_association', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 97, 'line': 88, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/citation_association.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '887aa487d07e7f6652e723026337e58e6f77888658f7c0be6e0db44a9d34735c', 'symbol': 'writer_allowed_aliases', 'module': 'apps.enterprise_document.services.agent_rag_v1.citation_association', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V06': ['TargetWriterContextComposer.final_messages', 'FakeLLMClient.generate'], 'V07': ['associate_selected_citations', 'writer_allowed_aliases'], 'V17': ['ContextPacker.pack', 'TargetWriterContextComposer.final_messages']}, 'row_exact_final_consumer_symbols': {'V06': ['ModelInvoker.invoke'], 'V07': [], 'V17': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_input_boundary.py', 'sha256': 'ceca4b300fb6dea6be1a62c512fd0c9ab9ebc7aa7bf89aef213f52bf28aa8945'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_r1_actual_provider_is_control_free_and_byte_bound(r1_mainline, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        _, request, package, _, _, _, _, _ = r1_mainline
        text = '\n'.join((item['content'] for item in request.messages))
        for forbidden in ('"writer_gate_binding"', '"writer_admission_decision"', '"pre_write_route_decision"', '"writer_admission_decision_ref"', '"route_decision_ref"', '"selected_route"', '"generation_class"', 'CONTROL_REF=', 'NORMAL_WRITE', 'FALLBACK_WRITE'):
            assert forbidden not in text
        expected = package.model_input.final_input_material.inline_final_input_payload
        assert expected == {'input_kind': 'MESSAGES', 'messages': request.messages}
        assert canonical_sha256(expected) == package.model_input.final_model_input_sha256
        assert package.policy_binding.representation_policy_version == 'v4'
        assert package.instruction_binding.dynamic_control_bindings == ()
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V06', 'V17'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_writer_input_boundary.py::test_native_observed_r1_actual_provider_is_control_free_and_byte_bound', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_input_boundary.py', 'sha256': 'ceca4b300fb6dea6be1a62c512fd0c9ab9ebc7aa7bf89aef213f52bf28aa8945', 'function': 'test_r1_actual_provider_is_control_free_and_byte_bound', 'line': 321, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_r1_tampered_final_input_still_fails_package_integrity(r1_mainline, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        _, _, package, _, _, _, _, _ = r1_mainline
        raw = package.model_dump(mode='json')
        raw['model_input']['final_input_material']['inline_final_input_payload']['messages'][-1]['content'] += ' tampered'
        with pytest.raises(ValidationError):
            ContextPackageSchema.model_validate(raw)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V06'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_writer_input_boundary.py::test_native_observed_r1_tampered_final_input_still_fails_package_integrity', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_input_boundary.py', 'sha256': 'ceca4b300fb6dea6be1a62c512fd0c9ab9ebc7aa7bf89aef213f52bf28aa8945', 'function': 'test_r1_tampered_final_input_still_fails_package_integrity', 'line': 445, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_src_rev_04_actual_model_request_has_bound_citation_usage(r1_mainline, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        _, request, package, _, _, _, _, _ = r1_mainline
        families = {item.candidate_id: item.source_binding.source_family for item in package.candidate_decisions}
        evidence = [item for item in package.selected_materializations if families[item.candidate_id] is ContextSourceFamily.EVIDENCE_PACKAGE]
        assert len(evidence) == 1
        payload = evidence[0].materialized_payload
        projected = payload.get('evidence_bundle', payload)
        usage = projected['citation_usage']
        assert usage['scope'] == 'current_section'
        assert usage['allowed_aliases']
        assert request.extra['available_citation_ids'] == usage['allowed_aliases']
        message_text = '\n'.join((item['content'] for item in request.messages))
        assert 'citation_usage.allowed_aliases' in message_text
        assert all((line in message_text for line in projected['context_text'].splitlines()))
        for alias in usage['allowed_aliases']:
            assert any((item['citation_id'] == alias for item in projected['citations']))
            assert any((alias in item['citation_ids'] for item in projected['sources']))
            assert alias in message_text
        assert request.messages == package.model_input.final_input_material.inline_final_input_payload['messages']
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V07'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_writer_input_boundary.py::test_native_observed_src_rev_04_actual_model_request_has_bound_citation_usage', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_input_boundary.py', 'sha256': 'ceca4b300fb6dea6be1a62c512fd0c9ab9ebc7aa7bf89aef213f52bf28aa8945', 'function': 'test_src_rev_04_actual_model_request_has_bound_citation_usage', 'line': 418, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_src_rev_04_tampered_citation_usage_fails_locked_context_integrity(r1_mainline, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        _, _, package, _, _, _, _, _ = r1_mainline
        raw = package.model_dump(mode='json')
        evidence = next((item for item in raw['selected_materializations'] if item['candidate_id'] == 'writer-current-evidence-package'))
        payload = evidence['materialized_payload']
        projected = payload.get('evidence_bundle', payload)
        projected['citation_usage']['allowed_aliases'].append('C999')
        with pytest.raises(ValidationError, match='materialized payload hash mismatch'):
            ContextPackageSchema.model_validate(raw)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V06'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_writer_input_boundary.py::test_native_observed_src_rev_04_tampered_citation_usage_fails_locked_context_integrity', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_input_boundary.py', 'sha256': 'ceca4b300fb6dea6be1a62c512fd0c9ab9ebc7aa7bf89aef213f52bf28aa8945', 'function': 'test_src_rev_04_tampered_citation_usage_fails_locked_context_integrity', 'line': 453, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
