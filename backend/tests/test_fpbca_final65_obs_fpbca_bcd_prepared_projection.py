"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_bcd_prepared_projection')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 137, 'line': 112, 'path': 'backend/rag/context/context_gate.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '7ae488fe4ca12a9b8f474c291f95f4bd10b4fd197b0d928bb1f8a95ee0aa04ad', 'symbol': 'ContextGate.pack', 'module': 'rag.context.context_gate', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 779, 'line': 750, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.admit_projection', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 721, 'line': 707, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.prepare_projection', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V25': ['ContextPacker.prepare_projection', 'ContextPacker.admit_projection', 'ContextGate.pack']}, 'row_exact_final_consumer_symbols': {'V25': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_prepared_projection.py', 'sha256': 'f59558beb6f955f92b30bc4b1943eab5c77888963b61b70d7e9039724a080b48'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_b4_borderline_strategy_independent_membership_real_final_cost(tmp_path, _fpbca_native_case_capture):
    try:
        packers = [ContextPacker(max_context_chars=10000, max_items=4, packing_strategy=s) for s in ('default', 'lost_in_middle_aware')]
        projection = packers[0].prepare_projection(rows())
        for budget in range(80, 750, 17):
            packs = [p.pack([], token_budget=budget, prepared_projection=projection) for p in packers]
            assert [sorted((r['child_chunk_id'] for r in p.items)) for p in packs][0] == [sorted((r['child_chunk_id'] for r in p.items)) for p in packs][1]
            assert [sorted((r['child_chunk_id'] for r in p.dropped_results)) for p in packs][0] == [sorted((r['child_chunk_id'] for r in p.dropped_results)) for p in packs][1]
            for pack in packs:
                assert pack.tokens_used <= budget
                assert pack.tokens_used == DeterministicTokenEstimator.estimate(pack.context)
                selected = sorted((int(r['rank']) for r in pack.items))
                assert selected == list(range(1, len(selected) + 1))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_prepared_projection.py::test_native_observed_b4_borderline_strategy_independent_membership_real_final_cost', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_prepared_projection.py', 'sha256': 'f59558beb6f955f92b30bc4b1943eab5c77888963b61b70d7e9039724a080b48', 'function': 'test_b4_borderline_strategy_independent_membership_real_final_cost', 'line': 36, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_b4_gate_measures_and_packs_same_prepared_projection(tmp_path, _fpbca_native_case_capture):
    try:
        default = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy='default')
        lost = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy='lost_in_middle_aware')
        projection = default.prepare_projection(rows())
        gate = ContextGate(default_packer=default, lost_in_middle_packer=lost, default_requirements=ContextRequirements(8192, 1024, 2000, 4, 10000))
        with patch.object(default, '_prompt_projection', side_effect=AssertionError('duplicate preparation')), patch.object(lost, '_prompt_projection', side_effect=AssertionError('duplicate preparation')):
            pack = gate.pack([], prepared_projection=projection)
        assert pack.tokens_used == DeterministicTokenEstimator.estimate(pack.context)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_prepared_projection.py::test_native_observed_b4_gate_measures_and_packs_same_prepared_projection', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_prepared_projection.py', 'sha256': 'f59558beb6f955f92b30bc4b1943eab5c77888963b61b70d7e9039724a080b48', 'function': 'test_b4_gate_measures_and_packs_same_prepared_projection', 'line': 25, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_b4_prepared_projection_reused_without_second_body_projection(tmp_path, _fpbca_native_case_capture):
    try:
        default = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy='default')
        lost = ContextPacker(max_context_chars=10000, max_items=4, packing_strategy='lost_in_middle_aware')
        projection = default.prepare_projection(rows())
        assert projection.estimated_tokens >= sum((DeterministicTokenEstimator.estimate(r['text']) for r in rows()))
        with patch.object(default, '_prompt_projection', side_effect=AssertionError('second projection')), patch.object(lost, '_prompt_projection', side_effect=AssertionError('second projection')):
            packs = [p.pack([], token_budget=1000, prepared_projection=projection) for p in (default, lost)]
        assert {r['child_chunk_id'] for r in packs[0].items} == {r['child_chunk_id'] for r in packs[1].items}
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_bcd_prepared_projection.py::test_native_observed_b4_prepared_projection_reused_without_second_body_projection', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_bcd_prepared_projection.py', 'sha256': 'f59558beb6f955f92b30bc4b1943eab5c77888963b61b70d7e9039724a080b48', 'function': 'test_b4_prepared_projection_reused_without_second_body_projection', 'line': 15, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
