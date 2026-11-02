"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_a1_v2_2_writer_string_firewall')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 500, 'line': 56, 'path': 'backend/model_gateway/fake_llm_client.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd8373490a7393d2a32aab9577f79a861d236d2fcc0b611ca28c6c662cb93e198', 'symbol': 'FakeLLMClient.generate', 'module': 'model_gateway.fake_llm_client', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 959, 'line': 909, 'path': 'backend/rag/evidence/governance_v2.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '34636ce3e2a32ab3819f70d2ef7617026c7083dc6f8c7718431b27d7d9e96f2c', 'symbol': 'OnlineEvidenceGovernanceV2.evaluate', 'module': 'rag.evidence.governance_v2', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 363, 'line': 348, 'path': 'backend/rag/plugins/chunkers/policy_article_clause.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd26b4de1ba9ccf480d1f97fe4fc00a00dd339ac853a0731be093ff5eed7dc2e9', 'symbol': 'PolicyArticleClauseChunker.chunk_records', 'module': 'rag.plugins.chunkers.policy_article_clause', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 581, 'line': 516, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/research_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a866afb39aa8236475c469d088efb5001e4d72c679ab62bd631ed0de958b4a4c', 'symbol': 'ResearchRuntime.commit_evidence_package', 'module': 'apps.enterprise_document.services.agent_rag_v1.research_runtime', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 673, 'line': 611, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/research_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a866afb39aa8236475c469d088efb5001e4d72c679ab62bd631ed0de958b4a4c', 'symbol': 'ResearchRuntime.validate_evidence_package_binding_current', 'module': 'apps.enterprise_document.services.agent_rag_v1.research_runtime', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 520, 'line': 505, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'TableRowRecordChunker.chunk_records', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1148, 'line': 1099, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/writer_context.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '36fa16c7ae1a36ccbd7e84679ffdf6a41b9ba474a2253cad9e02673987748b94', 'symbol': 'TargetWriterContextComposer.final_messages', 'module': 'apps.enterprise_document.services.agent_rag_v1.writer_context', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 1097, 'line': 1082, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/writer_context.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '36fa16c7ae1a36ccbd7e84679ffdf6a41b9ba474a2253cad9e02673987748b94', 'symbol': 'TargetWriterContextComposer.validate_pre_model_call', 'module': 'apps.enterprise_document.services.agent_rag_v1.writer_context', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 278, 'line': 263, 'path': 'backend/rag/plugins/chunkers/template_section.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '9f2698d80c1a4fa5b1818cd4814a4d5a7da6479e17a2ccd867c9cfdb640450b7', 'symbol': 'TemplateSectionChunker.chunk_records', 'module': 'rag.plugins.chunkers.template_section', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 309, 'line': 148, 'path': 'backend/rag/schema/structured_evidence.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'ff7adc24013a381344dd7c0bc6db8921948dc6917ce167b07bd40a990c037960', 'symbol': 'build_structured_evidence', 'module': 'rag.schema.structured_evidence', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 132, 'line': 64, 'path': 'backend/rag/plugins/chunkers/table_row_record.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '4ea679f9a17c3699b3144bfbda518d2e36d604f1e537259221b12bd15ac23bab', 'symbol': 'parse_structured_table', 'module': 'rag.plugins.chunkers.table_row_record', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V03': ['ResearchRuntime.validate_evidence_package_binding_current', 'ResearchRuntime.commit_evidence_package'], 'V05': ['TargetWriterContextComposer.validate_pre_model_call', 'TargetWriterContextComposer.final_messages', 'FakeLLMClient.generate'], 'V08': ['TargetWriterContextComposer.final_messages'], 'V13': ['parse_structured_table', 'TableRowRecordChunker.chunk_records', 'build_structured_evidence', 'ContextPacker.pack'], 'V14': ['TemplateSectionChunker.chunk_records', 'OnlineEvidenceGovernanceV2.evaluate', 'ContextPacker.pack'], 'V15': ['PolicyArticleClauseChunker.chunk_records', 'build_structured_evidence', 'ContextPacker.pack'], 'V17': ['ContextPacker.pack', 'TargetWriterContextComposer.final_messages'], 'V21': ['TargetWriterContextComposer.final_messages']}, 'row_exact_final_consumer_symbols': {'V03': [], 'V05': ['ModelInvoker.invoke'], 'V08': ['ModelInvoker.invoke'], 'V13': [], 'V14': [], 'V15': [], 'V17': [], 'V21': ['ModelInvoker.invoke']}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('kind', ['policy', 'template'])
def test_native_observed_actual_chunker_governance_hidden_outer_body(kind, tmp_path, _fpbca_native_case_capture):
    try:
        marker = fresh(kind)
        bundle = producer_bundle(kind, marker)
        frame = frame_for(bundle, marker)
        result = project(frame, 'actual_' + kind + '_outer_body')
        assert marker not in serialized(result), 'Producer BODY locator bypasses governed context'
        assert_final_string_firewall(result, frame)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V08', 'V13', 'V14', 'V15'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_actual_chunker_governance_hidden_outer_body', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_actual_chunker_governance_hidden_outer_body', 'line': 446, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_actual_fresh_v5_keeps_locked_model_request_and_original_route(actual_v22_predispatch_matrix, tmp_path, _fpbca_native_case_capture):
    try:
        observations, provider_calls, result = actual_v22_predispatch_matrix
        actual_request = result[1]
        assert len([row for row in provider_calls if row['purpose'] == 'scheme_section_generation']) == 1
        assert not any((row['during_identity_probe'] for row in provider_calls))
        for observation in observations:
            current = observation['fresh']
            evidence = next((item for item in current.selected_materializations if item.candidate_id == 'writer-current-evidence-package'))
            assert (evidence.projection_schema_id, evidence.projection_schema_version) == ('writer_evidence_package_compact_v5', 'v5')
            assert observation['messages'] == actual_request.messages
            assert current.model_input.final_input_material.inline_final_input_payload == {'input_kind': 'MESSAGES', 'messages': actual_request.messages}
        invocation = result[5]
        route = result[3].route_decision_store.resolve_exact(invocation.route_decision_ref)
        expected_route = 'fallback' if invocation.writer_subject_ref.run_id.endswith('fallback') else 'normal'
        assert route.selected_route.value == expected_route
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V05'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_actual_fresh_v5_keeps_locked_model_request_and_original_route', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_actual_fresh_v5_keeps_locked_model_request_and_original_route', 'line': 901, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('schema_id,schema_version', V22_IDENTITY_PAIRS)
@pytest.mark.parametrize('reader', ('validate_pre_model_call', 'final_messages'))
def test_native_observed_actual_predispatch_accepts_only_complete_v5_pair(actual_v22_predispatch_matrix, schema_id, schema_version, reader, tmp_path, _fpbca_native_case_capture):
    try:
        observations, provider_calls, _ = actual_v22_predispatch_matrix
        assert not any((row['during_identity_probe'] for row in provider_calls)), 'Stale/mixed identity probe dispatched a model'
        expected_acceptance = (schema_id, schema_version) == ('writer_evidence_package_compact_v5', 'v5')
        for observation in observations:
            record = next((row for row in observation['records'] if (row['schema_id'], row['schema_version'], row['reader']) == (schema_id, schema_version, reader)))
            assert record['accepted'] is expected_acceptance, record
            if not expected_acceptance:
                assert record['error_type'] == 'TargetWriterContextIntegrityError'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V05'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_actual_predispatch_accepts_only_complete_v5_pair', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_actual_predispatch_accepts_only_complete_v5_pair', 'line': 889, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_complete_final_values_keys_and_refs_have_independent_category_proof(tmp_path, _fpbca_native_case_capture):
    try:
        marker = fresh('inventory')
        frame = frame_for(built_bundle(marker), marker)
        result = project(frame, 'complete_final_inventory')
        trace = assert_final_string_firewall(result, frame)
        assert any((item['category'] == 'closed-key' for item in trace))
        assert any((item['category'] == 'exact-canonical-value-hash' for item in trace))
        path = artifact_dir('classification_trace')
        write_case(path, 'inventory.json', trace)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V08', 'V17', 'V21'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_complete_final_values_keys_and_refs_have_independent_category_proof', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_complete_final_values_keys_and_refs_have_independent_category_proof', 'line': 646, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('kind,field', STRUCTURED_FREE_FIELDS)
def test_native_observed_every_structured_free_string_and_nested_key_remains_exact_ref(kind, field, tmp_path, _fpbca_native_case_capture):
    try:
        from rag.schema.structured_evidence import StructuredEvidenceSchema
        marker = fresh(kind + '_' + field)
        if field in {'header', 'cells', 'merged_cell_relations', 'clause_path', 'section_path', 'required_fields', 'placeholder_fields', 'style_hints'}:
            value = [marker]
        elif field in {'source_span', 'normalized_fields', 'inherited_cells', 'calculation_record'}:
            value = {marker: marker, 'text': marker}
        elif field == 'value':
            value = {marker: [marker]}
        else:
            value = marker
        structured = StructuredEvidenceSchema.model_validate({'representation_type': {'fact': 'fact_record', 'table': 'table_row', 'policy': 'policy_clause', 'template': 'template_section'}[kind], kind: {field: value}}).model_dump(mode='json')
        bundle = built_bundle(marker)
        candidate = {'candidate_id': 'source-candidate', 'citation_ids': ['C1'], 'retrieval_candidate': {}, 'structured_evidence': structured}
        bundle.assessment.details['evidence_governance_v2'] = {'prompt_evidence_candidates': [candidate]}
        frame = frame_for(bundle, marker)
        view = project(frame, 'structured_' + kind + '_' + field)
        assert marker not in serialized(view)
        assert view['source_constraints']['candidates'][0]['structured_evidence'][kind]['support_ref'] == {'bundle_json_pointer': '/assessment/details/evidence_governance_v2/prompt_evidence_candidates/0/structured_evidence/' + kind, 'sha256': canonical_sha256(structured[kind])}
        assert_final_string_firewall(view, frame)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V14', 'V15'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_every_structured_free_string_and_nested_key_remains_exact_ref', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_every_structured_free_string_and_nested_key_remains_exact_ref', 'line': 693, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('attack', ['evidence_free', 'evidence_E999', 'citation_free', 'citation_C999', 'selected_order', 'item_order', 'citation_source_duplicate', 'item_citation_mismatch'])
def test_native_observed_external_typed_bundle_nonfixed_alias_or_mapping_fail_closed(attack, tmp_path, _fpbca_native_case_capture):
    try:
        marker = fresh(attack)
        bundle = built_bundle(marker, count=2)
        raw = canonical(bundle)
        forbidden = marker
        if attack.startswith('evidence_'):
            forbidden = marker if attack == 'evidence_free' else 'E999'
            raw['items'][0]['evidence_id'] = forbidden
            raw['selected_evidence_ids'][0] = forbidden
        elif attack.startswith('citation_') and attack != 'citation_source_duplicate':
            forbidden = marker if attack == 'citation_free' else 'C999'
            raw['citations'][0]['citation_id'] = forbidden
            raw['items'][0]['citation_ids'] = [forbidden]
        elif attack == 'selected_order':
            raw['selected_evidence_ids'].reverse()
        elif attack == 'item_order':
            raw['items'].reverse()
        elif attack == 'citation_source_duplicate':
            raw['citations'][1] = deepcopy(raw['citations'][0])
            raw['citations'][1]['citation_id'] = 'C2'
            raw['items'][0]['citation_ids'] = ['C1', 'C2']
            raw['items'][1]['citation_ids'] = []
        else:
            raw['items'][0]['citation_ids'] = ['C2']
        external = EvidenceBundleSchema.model_validate(raw)
        frame = frame_for(external, marker)
        try:
            result = project(frame, 'external_nonfixed_' + attack)
        except ValueError:
            return
        assert not result.get('sources') and (not result.get('citations')) and (not result.get('citation_usage', {}).get('allowed_aliases')), 'External nonfixed mapping was not denied before model-visible aliases'
        assert forbidden not in serialized(result)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V03'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_external_typed_bundle_nonfixed_alias_or_mapping_fail_closed', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_external_typed_bundle_nonfixed_alias_or_mapping_fail_closed', 'line': 457, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('attack', ['dict_key', 'arbitrary_string', 'fake_hash', 'wrong_pointer', 'raw_label_pointer', 'source_uri', 'coercion'])
def test_native_observed_independent_oracle_rejects_its_own_unclassified_mutations(attack, tmp_path, _fpbca_native_case_capture):
    try:
        marker = fresh('oracle_self_attack')
        frame = frame_for(built_bundle(marker), marker)
        view = {'context_text': frame.bundle.context.context_text, 'binding': {'sha256': canonical_sha256(canonical(frame.binding))}}
        assert_final_string_firewall(view, frame)
        if attack == 'dict_key':
            view[marker] = True
        elif attack == 'arbitrary_string':
            view['scope'] = marker
        elif attack == 'fake_hash':
            view['source_sha256'] = 'a' * 64
        elif attack == 'wrong_pointer':
            view['source_ref'] = {'bundle_json_pointer': '/items/0', 'sha256': 'a' * 64}
        elif attack == 'raw_label_pointer':
            view['source_ref'] = {'bundle_json_pointer': '/items/' + marker, 'sha256': 'a' * 64}
        elif attack == 'source_uri':
            view['source_locator_ref'] = marker + '://sha256/' + canonical_sha256(canonical(frame.bundle))
        else:
            view['scope'] = SimpleNamespace(__str__=lambda: marker)
        with pytest.raises(AssertionError):
            assert_final_string_firewall(view, frame)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V21'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_independent_oracle_rejects_its_own_unclassified_mutations', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_independent_oracle_rejects_its_own_unclassified_mutations', 'line': 658, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_legitimate_governed_body_and_title_are_preserved_once(tmp_path, _fpbca_native_case_capture):
    try:
        marker = fresh('visible_title_body')
        bundle = built_bundle(marker)
        raw = canonical(bundle)
        text = '[C1]\n合法政策章节标题 ' + marker + '\n应核验材料。'
        raw['context']['context_text'] = text
        raw['context']['used_context_chars'] = len(text)
        bundle = EvidenceBundleSchema.model_validate(raw)
        frame = frame_for(bundle, marker)
        view = project(frame, 'visible_title_body')
        assert view['context_text'] == text
        assert serialized(view).count(marker) == 1
        assert_final_string_firewall(view, frame)
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V08', 'V21'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a1_v2_2_writer_string_firewall.py::test_native_observed_legitimate_governed_body_and_title_are_preserved_once', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a1_v2_2_writer_string_firewall.py', 'sha256': 'ab42165a6dedf6f360a4fe115429a0b468a4c386fb2c7143f08a25f853ef02c3', 'function': 'test_legitimate_governed_body_and_title_are_preserved_once', 'line': 764, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
