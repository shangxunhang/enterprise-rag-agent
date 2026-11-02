"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_claim_verification_runtime')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 1437, 'line': 1422, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/context_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '1c568cfec0ed33bcc70444f6e53502bc293f529910a38d1f937ea8d56a0c396a', 'symbol': 'ApplicationContextRuntime.validate_pre_model_call_current', 'module': 'apps.enterprise_document.services.agent_rag_v1.context_runtime', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1379, 'line': 1169, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/research_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a866afb39aa8236475c469d088efb5001e4d72c679ab62bd631ed0de958b4a4c', 'symbol': 'ResearchRuntime.commit_round', 'module': 'apps.enterprise_document.services.agent_rag_v1.research_runtime', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1337, 'line': 1252, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'symbol': 'RunAttemptLifecycleService.open_attempt', 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'owner': True, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V19': ['ApplicationContextRuntime.validate_pre_model_call_current', 'RunAttemptLifecycleService.open_attempt', 'ResearchRuntime.commit_round']}, 'row_exact_final_consumer_symbols': {'V19': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_claim_verification_runtime.py', 'sha256': 'bc6b530d3bb53beea70440ab0d674fab14764e1f2f49f3de937e53c048b73d91'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_binding_source_staleness_preserves_history_but_blocks_new_identity(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        env, _paragraph, runtime, _version, _manifest, _candidate0, result, _checks, source_state = _commit_claim(tmp_path)
        claim = result.records[0]
        original = _binding_candidate(runtime, claim, source_ref='evidence:C1')
        original_context = _context(env, authorization_ref='claim-binding://current', purpose='claim-binding-current')
        first = runtime.commit_binding_facts(original, original_context)
        source_state['current'] = False
        replay = runtime.commit_binding_facts(original, original_context)
        assert replay == first
        assert runtime.resolve_binding_facts(first.binding_facts_ref) == first
        new_identity = _binding_candidate(runtime, claim, source_ref='evidence:C2')
        with pytest.raises(ClaimCurrentnessError, match='source became stale'):
            runtime.commit_binding_facts(new_identity, _context(env, authorization_ref='claim-binding://stale-new-source', purpose='claim-binding-stale-new-source'))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V19'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_claim_verification_runtime.py::test_native_observed_binding_source_staleness_preserves_history_but_blocks_new_identity', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_claim_verification_runtime.py', 'sha256': 'bc6b530d3bb53beea70440ab0d674fab14764e1f2f49f3de937e53c048b73d91', 'function': 'test_binding_source_staleness_preserves_history_but_blocks_new_identity', 'line': 507, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
