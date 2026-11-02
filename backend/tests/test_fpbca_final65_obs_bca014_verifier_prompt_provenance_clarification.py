"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_bca014_verifier_prompt_provenance_clarification')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 500, 'line': 56, 'path': 'backend/model_gateway/fake_llm_client.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd8373490a7393d2a32aab9577f79a861d236d2fcc0b611ca28c6c662cb93e198', 'symbol': 'FakeLLMClient.generate', 'module': 'model_gateway.fake_llm_client', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V22': ['FakeLLMClient.generate']}, 'row_exact_final_consumer_symbols': {'V22': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_bca014_verifier_prompt_provenance_clarification.py', 'sha256': 'fe73cc497a204b3b4d1cb8d097cc2dc6e81ee9a42a8e090f1093e6480bb13697'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('relation', list(SupportRelation))
@pytest.mark.parametrize('supporting', [(), (E1,), (E2,), (E1, E2)])
@pytest.mark.parametrize('scenario', ['supports', 'context_only', 'no_material_support', 'contradicts', 'conflicting_observations', 'ambiguous_kind'])
def test_native_observed_positive_projection_equals_existing_non_verified_admission_guard(relation, supporting, scenario, tmp_path, _fpbca_native_case_capture):
    try:
        binding = _matrix_binding(scenario)
        support = SimpleNamespace(relation=relation, supporting_refs=supporting)
        broad = _guard_candidate(binding, ()).allowed_authority_refs
        expected = []
        for ref in broad:
            try:
                ClaimVerificationRuntime.validate_v2_provenance(binding, support, _guard_candidate(binding, (ref,)))
            except ClaimBindingError:
                continue
            expected.append(ref)
        projected = composition.admissible_positive_basis_refs(binding, support, PI)
        assert projected == tuple(expected)
        assert SNAPSHOT not in projected and PACKAGE not in projected
        assert FACT in projected and PI in projected
        ClaimVerificationRuntime.validate_v2_provenance(binding, support, _guard_candidate(binding, projected))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V22'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_bca014_verifier_prompt_provenance_clarification.py::test_native_observed_positive_projection_equals_existing_non_verified_admission_guard', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_bca014_verifier_prompt_provenance_clarification.py', 'sha256': 'fe73cc497a204b3b4d1cb8d097cc2dc6e81ee9a42a8e090f1093e6480bb13697', 'function': 'test_positive_projection_equals_existing_non_verified_admission_guard', 'line': 91, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
