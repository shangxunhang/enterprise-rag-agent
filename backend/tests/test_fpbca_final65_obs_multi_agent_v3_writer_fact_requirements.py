"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_writer_fact_requirements')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 581, 'line': 516, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/research_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a866afb39aa8236475c469d088efb5001e4d72c679ab62bd631ed0de958b4a4c', 'symbol': 'ResearchRuntime.commit_evidence_package', 'module': 'apps.enterprise_document.services.agent_rag_v1.research_runtime', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1148, 'line': 1099, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/writer_context.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '36fa16c7ae1a36ccbd7e84679ffdf6a41b9ba474a2253cad9e02673987748b94', 'symbol': 'TargetWriterContextComposer.final_messages', 'module': 'apps.enterprise_document.services.agent_rag_v1.writer_context', 'owner': True, 'consumer': True, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V02': ['ResearchRuntime.commit_evidence_package', 'TargetWriterContextComposer.final_messages'], 'V16': []}, 'row_exact_final_consumer_symbols': {'V02': ['TargetWriterContextComposer.final_messages'], 'V16': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_fact_requirements.py', 'sha256': '9451677191a39d38c29dc90964edd4ca1490c8445d60a9bbe04bf10114c75514'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_authored_requirements_reach_real_writer_with_exact_facts_and_missing_list(fact_mainline, tmp_path, _fpbca_native_case_capture):
    try:
        run_id, route, request, package, invocation, summary, requirements = fact_mainline
        families = {item.candidate_id: item.source_binding.source_family for item in package.candidate_decisions}
        fact_payload = next((item.materialized_payload for item in package.selected_materializations if families[item.candidate_id] is ContextSourceFamily.FACT_SNAPSHOT))
        plan_payload = next((item.materialized_payload for item in package.selected_materializations if families[item.candidate_id] is ContextSourceFamily.SECTION_PLAN))
        expected = [item.model_dump(mode='json') for item in requirements]
        assert plan_payload['section']['fact_requirements'] == expected
        assert fact_payload['fact_requirements'] == expected
        assert [item['fact_id'] for item in fact_payload['facts']] == [f'fact_{run_id}_hardware']
        assert fact_payload['facts'][0]['normalized_value'] == '2台服务器X，每台8张卡。'
        assert fact_payload['unresolved_fact_requirements'] == [requirements[1].model_dump(mode='json')]
        text = '\n'.join((item['content'] for item in request.messages))
        assert '2台服务器X，每台8张卡。' in text
        assert 'project_input.organization_scale' in text
        assert '"writer_admission_decision"' not in text
        assert '"route_decision_ref"' not in text
        actual = {'input_kind': 'MESSAGES', 'messages': request.messages}
        assert actual == package.model_input.final_input_material.inline_final_input_payload
        assert canonical_sha256(actual) == package.model_input.final_model_input_sha256
        assert package.policy_binding.representation_policy_version == 'v4'
        assert invocation.generation_class.value == ('normal_write' if route == 'normal' else 'fallback_write')
        assert summary['status'] == 'partial_success', 'Fact transport is not semantic-quality PASS'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V02', 'V16'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_writer_fact_requirements.py::test_native_observed_authored_requirements_reach_real_writer_with_exact_facts_and_missing_list', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_writer_fact_requirements.py', 'sha256': '9451677191a39d38c29dc90964edd4ca1490c8445d60a9bbe04bf10114c75514', 'function': 'test_authored_requirements_reach_real_writer_with_exact_facts_and_missing_list', 'line': 416, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
