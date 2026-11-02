"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_faithfulness_fth03_mainline')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V16': []}, 'row_exact_final_consumer_symbols': {'V16': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_faithfulness_fth03_mainline.py', 'sha256': 'f5fe0f63e2143a619464ab2d61767efd1bf8c42010590da094b1d9fae92c7aa0'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize(('certainty', 'expected'), [('ALIGNED', 'PASS'), ('INDETERMINATE', 'INDETERMINATE')])
def test_native_observed_fth03_actual_factory_section_and_document_are_independent_without_new_model_call(tmp_path, certainty, expected, _fpbca_native_case_capture):
    try:
        engine, calls, captures, _summary, top = _run(tmp_path, certainty=certainty)
        assert top.terminal_error is None
        assert len(captures) == 2
        assert {item[0]['scope'].value for item in captures} == {'SECTION', 'DOCUMENT'}
        support_calls = [request for request in calls if request.extra.get('call_purpose') == 'claim_support']
        assert len(support_calls) == 1
        assert not [request for request in calls if 'faithfulness' in str(request.extra.get('call_purpose') or '').lower()]
        fact_ids = []
        receipt_refs = []
        for _kwargs, fth03, facts, _shared, _shared_facts, _outcome in captures:
            assert fth03.state.value == expected
            assert facts
            assert {fact.semantic_state.value for fact in facts} == {expected}
            assert {tuple(fact.payload['rule_ids']) for fact in facts} == {('PW-R-FTH-03',)}
            for fact in facts:
                observation = fact.payload['certainty_observation']
                assert observation['certainty_relation'] == certainty
                assert observation['applicable_requirement_ids'] == []
                receipt_refs.append(observation['physical_support_receipt_ref'])
                fact_ids.append(fact.fact_id)
        assert len(set(receipt_refs)) == 1
        assert len(set(fact_ids)) == 2
        composition = engine.stage_service.target_post_write_composition
        final_control = composition.control_store.resolve_exact(top.target_post_write_control_decision_ref)
        final_gate = composition.gate_store.resolve_exact(final_control.delivery_gate_decision_ref)
        condition = next((item for item in final_gate.quality_policy_projection.condition_results if item.dimension_id == 'faithfulness'))
        assert condition.state.value == ('SATISFIED' if certainty == 'ALIGNED' else 'UNRESOLVED')
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_faithfulness_fth03_mainline.py::test_native_observed_fth03_actual_factory_section_and_document_are_independent_without_new_model_call', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_faithfulness_fth03_mainline.py', 'sha256': 'f5fe0f63e2143a619464ab2d61767efd1bf8c42010590da094b1d9fae92c7aa0', 'function': 'test_fth03_actual_factory_section_and_document_are_independent_without_new_model_call', 'line': 200, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_fth03_escalated_is_a_real_faithfulness_failure_even_with_supported_claim(tmp_path, _fpbca_native_case_capture):
    try:
        _engine, _calls, captures, _summary, _top = _run(tmp_path, certainty='ESCALATED', support_relation='SUPPORTED')
        assert captures
        first = captures[0]
        _kwargs, fth03, facts, shared, shared_facts, _outcome = first
        assert fth03.state.value == 'FAIL'
        assert facts and {fact.semantic_state.value for fact in facts} == {'FAIL'}
        assert shared.state.value == 'PASS'
        assert shared_facts and 'PASS' in {fact.semantic_state.value for fact in shared_facts}
        assert all((fact.payload['support_relation'] == 'SUPPORTED' for fact in facts))
        assert all((fact.payload['certainty_observation']['certainty_relation'] == 'ESCALATED' for fact in facts))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V16'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_faithfulness_fth03_mainline.py::test_native_observed_fth03_escalated_is_a_real_faithfulness_failure_even_with_supported_claim', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_faithfulness_fth03_mainline.py', 'sha256': 'f5fe0f63e2143a619464ab2d61767efd1bf8c42010590da094b1d9fae92c7aa0', 'function': 'test_fth03_escalated_is_a_real_faithfulness_failure_even_with_supported_claim', 'line': 258, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
