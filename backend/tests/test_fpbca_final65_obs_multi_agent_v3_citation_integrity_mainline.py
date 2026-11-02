"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_citation_integrity_mainline')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 85, 'line': 45, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/citation_association.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '887aa487d07e7f6652e723026337e58e6f77888658f7c0be6e0db44a9d34735c', 'symbol': 'associate_selected_citations', 'module': 'apps.enterprise_document.services.agent_rag_v1.citation_association', 'owner': True, 'consumer': False, 'plain_method': False}, {'async': False, 'end_line': 97, 'line': 88, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/citation_association.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '887aa487d07e7f6652e723026337e58e6f77888658f7c0be6e0db44a9d34735c', 'symbol': 'writer_allowed_aliases', 'module': 'apps.enterprise_document.services.agent_rag_v1.citation_association', 'owner': True, 'consumer': False, 'plain_method': False}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V07': ['associate_selected_citations', 'writer_allowed_aliases']}, 'row_exact_final_consumer_symbols': {'V07': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_citation_integrity_mainline.py', 'sha256': '0b70ec3d6404b948aa6ed322ec10b67971fecdff642cf3b687cb17fd143a0ec4'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_real_factory_multisection_sources_origins_reservation_and_output(tmp_path, monkeypatch, _fpbca_native_case_capture):
    try:
        from test_phase7_document_closure_mainline import _InspectingFactory
        from tools.fake_rag_tool import FakeRAGTool
        from test_multi_agent_v3_workflow_recovery import ProjectInputSchema
        names = ['安全设计', '技术方案', '建设内容']
        project = ProjectInputSchema(schema_version='project_input_v3', task_id='task-cit344-multi', task_type='scheme_generation', user_query='生成安全设计、技术方案及建设内容', generation_requirements={'required_sections': names, 'need_citation': True, 'citation_required_sections': names, 'extra': {'enable_section_aware_retrieval': True, 'enable_corrective_section_retrieval': False}, 'need_human_review': False, 'min_section_chars': 80}, output_schema={'document_title': '多章节引用', 'required_sections': names})
        calls, captures, sources = ([], [], [])
        original_generate, original_rag = (FakeLLMClient.generate, FakeRAGTool.run)
        original_complete = TargetPostWriteComposition._complete_validation

        def rag_provider(tool, call):
            title = (call.tool_input.get('evidence_requirements') or {}).get('section_title', '')
            source_title = '安全设计' if title == '建设内容' else title
            tool_input = {**call.tool_input, 'evidence_requirements': {**(call.tool_input.get('evidence_requirements') or {}), 'section_title': source_title}}
            response = original_rag(tool, call.model_copy(update={'tool_call_id': 'fixture-source-' + source_title, 'tool_input': tool_input}))
            sources.append((title, response.model_dump(mode='json')))
            return response.model_copy(update={'tool_call_id': call.tool_call_id})

        def provider(client, request):
            calls.append(request)
            response = original_generate(client, request)
            if request.extra.get('call_purpose') == 'scheme_section_generation':
                return response.model_copy(update={'content': TEXT * 5 + '依据[C1]，待核对[C2]。'})
            return response

        def complete(composition, **arguments):
            before = len(calls)
            source_bytes = tuple((row.model_dump_json() for row in arguments['source_sections']))
            result = original_complete(composition, **arguments)
            added = calls[before:]
            assert [request.extra.get('response_contract') for request in added] == ['requirement_adherence_batch_v1', 'citation_claim_association_v1']
            assert source_bytes == tuple((row.model_dump_json() for row in arguments['source_sections']))
            captures.append((arguments, composition.validation_store.resolve_completion(result[0])))
            return result
        for name, value in {'USE_REAL_RAG_TOOL': 'false', 'FAKE_RAG_SCENARIO': 'citation_collision', 'FAKE_LLM_SCENARIO': 'default', 'ENABLE_AGENT_SELF_RAG': 'false', 'ENABLE_SEMANTIC_GATE': 'false'}.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(FakeRAGTool, 'run', rag_provider)
        monkeypatch.setattr(FakeLLMClient, 'generate', provider)
        monkeypatch.setattr(TargetPostWriteComposition, '_complete_validation', complete)
        monkeypatch.setenv('GROUNDED_GENERATION_POLICY_FILE', str(workflow_tests._w15_parallel_budget_policy_file(tmp_path)))
        settings = replace(get_settings(), data_root=tmp_path, default_model_name='fake_llm', supervisor_model_name='fake_llm', enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True)
        factory = _InspectingFactory()
        summary = MainlineApplicationService(supervisor_factory=factory).run(project_root=Path(__file__).resolve().parents[2], task_id=project.task_id, run_id='run-cit344-multi', user_input=project.user_query, output_root=tmp_path, clean_existing=True, settings=settings, project_input=project.model_dump(mode='json'), allow_demo_defaults=False, enable_agent_self_rag=False)
        engine = factory.engines[0]
        top = workflow_tests._latest_w13_top_level_payload(engine)
        assert top.terminal_error is None, top.terminal_error or summary
        assert len(top.section_execution_results) == 3
        assert {title for title, _ in sources} >= set(names)
        composition = engine.stage_service.target_post_write_composition
        final = composition.artifact_store.resolve_exact(top.target_document_artifact_ref)
        projection = final.canonical_payload_ref['citation_projection']
        catalogs = projection['source_catalogs']
        assert [catalog['entries'][0]['alias'] for catalog in catalogs] == ['C1'] * 3
        stable = [catalog['entries'][0]['citation_ref'] for catalog in catalogs]
        assert stable[0] == stable[2] and stable[0] != stable[1]
        entries = projection['catalog']['entries']
        assert len(entries) == 2
        assert {entry['alias'] for entry in entries} == {'C1', 'C3'}
        assert sorted((len(entry['origin_refs']) for entry in entries)) == [1, 2]
        assert [item['mapping'] for item in projection['section_alias_maps']] == [{'C1': 'C1'}, {'C1': 'C3'}, {'C1': 'C1'}]
        visible = final.canonical_payload_ref['included_sections']
        assert [item['visible_payload']['content'] for item in visible] == [TEXT * 5 + '依据[C1]，待核对[C2]。', TEXT * 5 + '依据[C3]，待核对[C2]。', TEXT * 5 + '依据[C1]，待核对[C2]。']
        assert len({completion.subject_hash for _, completion in captures}) == 4
        for arguments, completion in captures:
            item = next((row for row in completion.items if row.expected_validation_item_ref.startswith('citation:integrity:')))
            facts = [composition.validation_store.resolve_fact(ref) for ref in item.fact_refs]
            assert item.state.value == 'FAIL'
            assert len(facts) == (15 if arguments['scope'].value == 'DOCUMENT' else 5)
            assert all((fact.subject_hash == completion.subject_hash for fact in facts))
        for section in top.section_execution_results:
            writer = composition.writer_execution_store.resolve_writer_execution(section.writer_execution_ref)
            assert writer.artifact.content == section.section.content == TEXT * 5 + '依据[C1]，待核对[C2]。'
        assert summary['target_release_publication']['manifest']['release_mode'] == 'BEST_EFFORT'
        binding = final.validated_output_bindings[0]
        stored = composition.output_store.get_bytes(binding.output_artifact_ref.artifact_ref, task_id=top.task_id, run_id=top.run_id)
        actual_output = json.loads(stored) if binding.output_format == 'json' else composition._extract_markdown_visible_payload(stored)
        assert actual_output == final.visible_projection.visible_payload
        assert actual_output['included_sections'] == visible
        rendered = composition._render_markdown_visible_payload(final.visible_projection.visible_payload)
        assert composition._extract_markdown_visible_payload(rendered) == final.visible_projection.visible_payload
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V07'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_citation_integrity_mainline.py::test_native_observed_real_factory_multisection_sources_origins_reservation_and_output', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_citation_integrity_mainline.py', 'sha256': '0b70ec3d6404b948aa6ed322ec10b67971fecdff642cf3b687cb17fd143a0ec4', 'function': 'test_real_factory_multisection_sources_origins_reservation_and_output', 'line': 440, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
