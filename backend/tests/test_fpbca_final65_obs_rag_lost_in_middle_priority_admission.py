"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_rag_lost_in_middle_priority_admission')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 137, 'line': 112, 'path': 'backend/rag/context/context_gate.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '7ae488fe4ca12a9b8f474c291f95f4bd10b4fd197b0d928bb1f8a95ee0aa04ad', 'symbol': 'ContextGate.pack', 'module': 'rag.context.context_gate', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 779, 'line': 750, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.admit_projection', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 721, 'line': 707, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.prepare_projection', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V09': ['ContextPacker.prepare_projection', 'ContextPacker.pack', 'ContextGate.pack'], 'V25': ['ContextPacker.prepare_projection', 'ContextPacker.admit_projection', 'ContextGate.pack']}, 'row_exact_final_consumer_symbols': {'V09': [], 'V25': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_rag_lost_in_middle_priority_admission.py', 'sha256': 'b2f1fec548e2091af2325029a3f2ff720202296ad3e991ba62a6ac98fd9f30d7'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('limiter', ['token', 'char'])
def test_native_observed_d02_tight_equal_size_admits_priority_top_two(limiter: str, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        rows, lost = (_results(), _packer())
        pair = _oracle(lost, rows[:2])
        kwargs = {'token_budget': pair.tokens if limiter == 'token' else 12000, 'char_budget': pair.chars if limiter == 'char' else 12000}
        pack = lost.pack(rows, **kwargs)
        assert _ids(pack) == ['c1', 'c2']
        assert len(pack.dropped_results) == 3
        assert not pack.truncated_item_ids
        _assert_exact(pack, pair)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_rag_lost_in_middle_priority_admission.py::test_native_observed_d02_tight_equal_size_admits_priority_top_two', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_rag_lost_in_middle_priority_admission.py', 'sha256': 'b2f1fec548e2091af2325029a3f2ff720202296ad3e991ba62a6ac98fd9f30d7', 'function': 'test_d02_tight_equal_size_admits_priority_top_two', 'line': 266, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('strategy', STRATEGIES)
def test_native_observed_d05_metadata_headers_are_part_of_exact_cost(strategy: str, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        rows, packer = (_results(), _packer(strategy, metadata=True))
        rows[1]['title'] = '精确来源标题' * 20
        rows[1]['metadata']['retrieval_sources'] = ['dense', 'bm25']
        pair = _oracle(packer, rows[:2])
        envelope = _admission_envelope(packer, rows[:2])
        pack = packer.pack(rows, token_budget=12000, char_budget=envelope.chars)
        assert _ids(pack) == ['c1', 'c2']
        assert "retrieval_sources: ['dense', 'bm25']" in pack.context
        assert rows[1]['title'] in pack.context
        _assert_exact(pack, pair)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_rag_lost_in_middle_priority_admission.py::test_native_observed_d05_metadata_headers_are_part_of_exact_cost', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_rag_lost_in_middle_priority_admission.py', 'sha256': 'b2f1fec548e2091af2325029a3f2ff720202296ad3e991ba62a6ac98fd9f30d7', 'function': 'test_d05_metadata_headers_are_part_of_exact_cost', 'line': 301, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('strategy', STRATEGIES)
def test_native_observed_d06_multiple_citations_are_costed_and_bound_to_selected_sources(strategy: str, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        rows, packer = (_results(), _packer(strategy, metadata=True))
        for row in rows:
            row['metadata']['matched_child_chunks'] = [{'child_chunk_id': f"{row['chunk_id']}s{i}", 'text': f'引用证据{i}'} for i in range(1, 7)]
        pair = _oracle(packer, rows[:2])
        envelope = _admission_envelope(packer, rows[:2])
        pack = packer.pack(rows, token_budget=envelope.tokens)
        assert _ids(pack) == ['c1', 'c2']
        assert len(pack.citations) == 14
        assert all((row['parent_chunk_id'] in {'p1', 'p2'} for row in pack.citations))
        _assert_exact(pack, pair)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_rag_lost_in_middle_priority_admission.py::test_native_observed_d06_multiple_citations_are_costed_and_bound_to_selected_sources', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_rag_lost_in_middle_priority_admission.py', 'sha256': 'b2f1fec548e2091af2325029a3f2ff720202296ad3e991ba62a6ac98fd9f30d7', 'function': 'test_d06_multiple_citations_are_costed_and_bound_to_selected_sources', 'line': 315, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('strategy', STRATEGIES)
def test_native_observed_d07_first_selected_truncation_is_maximal_and_auditable(strategy: str, tmp_path, _fpbca_native_case_capture) -> None:
    try:
        rows, packer = (_results(count=1, body_chars=1800), _packer(strategy, metadata=True))
        expected = _maximum_prefix(packer, rows[0], tokens=1000, chars=480)
        assert expected is not None
        assert _admission_envelope(packer, rows).chars > 480
        pack = packer.pack(rows, token_budget=1000, char_budget=480)
        assert _ids(pack) == ['c1']
        chosen = pack.items[0]
        assert chosen['prompt_visible_text'] == expected == chosen['context_text']
        assert chosen['metadata']['context_truncated'] is True
        assert chosen['metadata']['truncated_source_id'] == 'p1'
        assert chosen['metadata']['truncation_reason'] == 'token_or_character_budget'
        assert chosen['metadata']['prompt_visible_char_count'] == len(expected)
        assert chosen['metadata']['prompt_visible_token_count'] == Estimator.estimate(expected)
        assert pack.truncated_item_ids == ['p1']
        _assert_exact(pack, _oracle(packer, rows, texts={'c1': expected}))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V09'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_rag_lost_in_middle_priority_admission.py::test_native_observed_d07_first_selected_truncation_is_maximal_and_auditable', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_rag_lost_in_middle_priority_admission.py', 'sha256': 'b2f1fec548e2091af2325029a3f2ff720202296ad3e991ba62a6ac98fd9f30d7', 'function': 'test_d07_first_selected_truncation_is_maximal_and_auditable', 'line': 332, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_d24_maximal_frozen_prefix_hash_survives_all_lower_rank_trials(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        rows, lost = (_results(count=3, body_chars=1800), _packer(metadata=True))
        rows[1]['text'], rows[2]['text'] = ('后续候选' * 100, '小')
        expected = _maximum_prefix(lost, rows[0], tokens=1000, chars=480)
        assert expected is not None
        before = lost.pack(rows[:1], token_budget=1000, char_budget=480)
        original_hash = hashlib.sha256(expected.encode('utf-8')).hexdigest()
        for later in rows[1:]:
            trial = _oracle(lost, [rows[0], later], texts={'c1': expected})
            assert trial.chars > 480 or trial.tokens > 1000
        after = lost.pack(rows, token_budget=1000, char_budget=480)
        assert _ids(after) == ['c1']
        assert {row['chunk_id'] for row in after.dropped_results} == {'c2', 'c3'}
        frozen = after.items[0]['prompt_visible_text']
        assert frozen == expected == before.items[0]['prompt_visible_text']
        assert hashlib.sha256(frozen.encode('utf-8')).hexdigest() == original_hash
        assert after.items[0]['metadata'] == before.items[0]['metadata']
        assert after.citations == before.citations
        _assert_exact(after, _oracle(lost, rows[:1], texts={'c1': expected}))
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V09', 'V25'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_rag_lost_in_middle_priority_admission.py::test_native_observed_d24_maximal_frozen_prefix_hash_survives_all_lower_rank_trials', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_rag_lost_in_middle_priority_admission.py', 'sha256': 'b2f1fec548e2091af2325029a3f2ff720202296ad3e991ba62a6ac98fd9f30d7', 'function': 'test_d24_maximal_frozen_prefix_hash_survives_all_lower_rank_trials', 'line': 670, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
