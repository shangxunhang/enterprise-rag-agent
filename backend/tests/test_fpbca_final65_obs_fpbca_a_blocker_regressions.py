"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_a_blocker_regressions')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 137, 'line': 112, 'path': 'backend/rag/context/context_gate.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '7ae488fe4ca12a9b8f474c291f95f4bd10b4fd197b0d928bb1f8a95ee0aa04ad', 'symbol': 'ContextGate.pack', 'module': 'rag.context.context_gate', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 821, 'line': 786, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.pack', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 721, 'line': 707, 'path': 'backend/rag/context/context_packer.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'dc4401cce4d552e7e110a244d9a8255135ecb1943ad43cd31ff605ff9f355e05', 'symbol': 'ContextPacker.prepare_projection', 'module': 'rag.context.context_packer', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 3021, 'line': 2881, 'path': 'backend/apps/enterprise_document/workflows/top_level_business_graph.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a87c37a119affd909a3698bd5ead3214777b97cb993c5d78ce8d6b0bc354da71', 'symbol': 'EnterpriseDocumentWorkflowEngine.recovery_plan', 'module': 'apps.enterprise_document.workflows.top_level_business_graph', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 500, 'line': 56, 'path': 'backend/model_gateway/fake_llm_client.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'd8373490a7393d2a32aab9577f79a861d236d2fcc0b611ca28c6c662cb93e198', 'symbol': 'FakeLLMClient.generate', 'module': 'model_gateway.fake_llm_client', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1250, 'line': 1236, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/lifecycle_runtime.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169', 'symbol': 'RunAttemptLifecycleService.assert_run_committable', 'module': 'apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime', 'owner': True, 'consumer': False, 'plain_method': True}, {'async': False, 'end_line': 1148, 'line': 1099, 'path': 'backend/apps/enterprise_document/services/agent_rag_v1/writer_context.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '36fa16c7ae1a36ccbd7e84679ffdf6a41b9ba474a2253cad9e02673987748b94', 'symbol': 'TargetWriterContextComposer.final_messages', 'module': 'apps.enterprise_document.services.agent_rag_v1.writer_context', 'owner': True, 'consumer': True, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V09': ['ContextPacker.prepare_projection', 'ContextPacker.pack', 'ContextGate.pack'], 'V21': ['TargetWriterContextComposer.final_messages'], 'V22': ['FakeLLMClient.generate'], 'V23': ['EnterpriseDocumentWorkflowEngine.recovery_plan'], 'V24': ['RunAttemptLifecycleService.assert_run_committable']}, 'row_exact_final_consumer_symbols': {'V09': [], 'V21': ['ModelInvoker.invoke'], 'V22': [], 'V23': [], 'V24': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a_blocker_regressions.py', 'sha256': '7a6bfdcf0cda6469b028de8e7706ebd56f9ccbf75e6a7fe0bf914a4835fd22cb'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_cr_a_001_claim_semantic_material_is_writer_visible_only(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        item = SimpleNamespace(evidence_id='E1', doc_id='doc-1', title='历史方案', match_text=RAW, context_text=VISIBLE)
        material, observed = TargetClaimRuntimeValidators._visible_evidence_material(item, _Assessment())
        encoded = json.dumps(material, ensure_ascii=False)
        assert RAW not in encoded
        assert 'match_text' not in material
        assert material['context_text'] == observed == VISIBLE
        assert material['context_text_sha256'] == canonical_sha256(VISIBLE)
        assert material['assessment']['usage_constraints'] == {'fact_visible_in_prompt': False, 'required_qualifiers': ['historical only']}
        assert 'metadata' not in material['assessment']
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V22'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a_blocker_regressions.py::test_native_observed_cr_a_001_claim_semantic_material_is_writer_visible_only', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a_blocker_regressions.py', 'sha256': '7a6bfdcf0cda6469b028de8e7706ebd56f9ccbf75e6a7fe0bf914a4835fd22cb', 'function': 'test_cr_a_001_claim_semantic_material_is_writer_visible_only', 'line': 115, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_cr_a_001_physical_support_and_verifier_never_receive_raw_match_text(tmp_path: Path, _fpbca_native_case_capture) -> None:
    try:
        task_id = 'task-fpbca-visible-authority'
        run_id = 'run-fpbca-visible-authority'
        project = workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
        raw_project = project.model_dump(mode='json')
        raw_project['generation_requirements']['need_citation'] = True
        raw_project['generation_requirements']['citation_required_sections'] = list(raw_project['generation_requirements']['required_sections'])
        project = ProjectInputSchema.model_validate(raw_project)
        calls = []
        original_generate = FakeLLMClient.generate
        original_retrieve = FakeRAGService.retrieve

        def evidence_fixture(service, req):
            raw = original_retrieve(service, req).model_dump(mode='json')
            item = raw['items'][0]
            item['match_text'] = RAW
            item['context_text'] = VISIBLE
            item['title'] = '历史方案'
            metadata = dict(item.get('metadata') or {})
            metadata.update(prompt_visible_text=VISIBLE, prompt_projection_mode='historical_reference_restricted', raw_fact_visibility='restricted')
            item['metadata'] = metadata
            for assessment in raw['item_assessments']:
                assessment['evidence_role'] = 'historical_case'
                assessment['project_relation'] = 'other_project'
                assessment['supported_claim_types'] = ['general_method']
                assessment['forbidden_claim_types'] = sorted(set(assessment.get('forbidden_claim_types') or []) | {'current_project_quantity', 'current_project_configuration', 'current_project_cost', 'current_project_schedule', 'current_project_fact'})
                assessment.setdefault('metadata', {})['structured_evidence'] = {'source_span': {'text': RAW}}
            raw['assessment']['supported_claim_types'] = ['general_method']
            raw['assessment']['historical_or_reference_evidence_present'] = True
            raw['assessment']['authoritative_evidence_present'] = False
            raw['context']['context_text'] = VISIBLE
            raw['context']['used_context_chars'] = len(VISIBLE)
            return EvidenceBundleSchema.model_validate(raw)

        def provider(client, req):
            calls.append(req.model_copy(deep=True))
            response = original_generate(client, req)
            purpose = req.extra.get('response_contract')
            if req.extra.get('call_purpose') == 'scheme_section_generation':
                return response.model_copy(update={'content': CLAIM * 8 + ' [C1]'}, deep=True)
            if purpose == extraction_tests.CONTRACT:
                units = extraction_tests._task_contract(req)['coverage_units']
                payload = {'units': [{'unit_id': unit['unit_id'], 'accounting_state': 'FACTUAL_CLAIMS_MATERIALIZED', 'claims': [{'quote': CLAIM, 'occurrence_index': 0, 'claim_type': 'general_method'}]} for unit in units]}
                return response.model_copy(update={'content': json.dumps(payload, ensure_ascii=False)}, deep=True)
            if purpose == SUPPORT_RESPONSE_CONTRACT:
                payload = support_tests._json_message(req, 'support_evidence')
                refs = [item['exact_source_ref'] for item in payload['support_evidence']]
                assert refs
                answer = {'relation': 'SUPPORTED', 'supporting_refs': refs[:1], 'contradicting_refs': [], 'certainty_relation': 'ALIGNED', 'certainty_reason_codes': ['VISIBLE_AUTHORITY_REGRESSION']}
                return response.model_copy(update={'content': json.dumps(answer)}, deep=True)
            if purpose == VERIFIER_RESPONSE_CONTRACT:
                basis = support_tests._json_message(req, 'admissible_positive_basis_refs')['admissible_positive_basis_refs']
                evidence_basis = next((ref for ref in basis if ref.startswith('evidence-item://')))
                answer = {'verdict': 'VERIFIED', 'positive_basis_refs': [evidence_basis], 'rationale': 'Controlled visible-evidence regression.', 'issues': []}
                return response.model_copy(update={'content': json.dumps(answer)}, deep=True)
            return response
        factory = workflow_tests._W13InspectingFactory()
        settings = replace(get_settings(), data_root=tmp_path, default_model_name='fake_llm', supervisor_model_name='fake_llm', enable_llm_routing=False, trace_enabled=True, data_capture_enabled=True, gate_enforcement_mode='strict', gate_recovery_rounds_limit=0)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'USE_REAL_RAG_TOOL': 'false', 'FAKE_RAG_SCENARIO': 'default', 'FAKE_LLM_SCENARIO': 'default', 'ENABLE_AGENT_SELF_RAG': 'false', 'ENABLE_SEMANTIC_GATE': 'false'}))
            stack.enter_context(patch.object(FakeRAGService, 'retrieve', evidence_fixture))
            stack.enter_context(patch.object(FakeLLMClient, 'generate', provider))
            MainlineApplicationService(supervisor_factory=factory).run(project_root=Path(__file__).resolve().parents[2], user_input=project.user_query, task_id=task_id, run_id=run_id, output_root=tmp_path, clean_existing=True, settings=settings, project_input=project.model_dump(mode='json'), allow_demo_defaults=False, enable_agent_self_rag=False)
        writer = [request for request in calls if request.extra.get('call_purpose') == 'scheme_section_generation']
        support = [request for request in calls if request.extra.get('call_purpose') == 'claim_support']
        verifier = [request for request in calls if request.extra.get('call_purpose') == 'claim_verification']
        assert len(writer) == len(support) == len(verifier) == 1
        writer_wire = '\n'.join((message['content'] for message in writer[0].messages))
        support_wire = '\n'.join((message['content'] for message in support[0].messages))
        verifier_wire = '\n'.join((message['content'] for message in verifier[0].messages))
        support_payload = support_tests._json_message(support[0], 'support_evidence')['support_evidence']
        assert RAW not in writer_wire
        assert RAW not in support_wire
        assert RAW not in verifier_wire
        assert support_payload[0]['context_text'] == VISIBLE
        assert support_payload[0]['context_text_sha256'] == canonical_sha256(VISIBLE)
        assert 'match_text' not in support_payload[0]
        assert 'metadata' not in support_payload[0]['assessment']
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V09', 'V22'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a_blocker_regressions.py::test_native_observed_cr_a_001_physical_support_and_verifier_never_receive_raw_match_text', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a_blocker_regressions.py', 'sha256': '7a6bfdcf0cda6469b028de8e7706ebd56f9ccbf75e6a7fe0bf914a4835fd22cb', 'function': 'test_cr_a_001_physical_support_and_verifier_never_receive_raw_match_text', 'line': 231, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_fpa_rec_001_target_live_recovery_uses_actual_route_predecessor(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        import test_fpbca_a3_v2_1_recovery as native_recovery
        rows = [native_recovery.checkpoint_row('actual-predecessor', 'final_artifact_review', 'continue', None, 1), native_recovery.checkpoint_row('failed-gate', 'evaluate_document_gate', 'technical_failure', 'actual-predecessor', 2, failed='evaluate_document_gate')]
        engine, workflow = native_recovery.engine_from_rows(rows, 'failed-gate', target=True)
        plan = engine.recovery_plan(workflow, run_id=native_recovery.RUN)
        assert plan['predecessor'] == 'final_artifact_review'
        assert plan['checkpoint_config']['configurable']['checkpoint_id'] == 'actual-predecessor'
        assert plan['checkpoint_config']['configurable']['thread_id'] == native_recovery.RUN
        assert plan['checkpoint_config']['configurable']['checkpoint_ns'] == ''
        assert plan['checkpoint_config']['configurable']['workflow_namespace'] == native_recovery.NS
        native_config = engine._recovery_native_config(workflow, plan['checkpoint_config'], run_id=native_recovery.RUN)
        assert native_config['configurable']['checkpoint_ns'] == native_recovery.NS
        assert rows[0].config['configurable']['checkpoint_ns'] == native_recovery.NS
        assert engine.checkpointer.history_reads == 0
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V23'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a_blocker_regressions.py::test_native_observed_fpa_rec_001_target_live_recovery_uses_actual_route_predecessor', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a_blocker_regressions.py', 'sha256': '7a6bfdcf0cda6469b028de8e7706ebd56f9ccbf75e6a7fe0bf914a4835fd22cb', 'function': 'test_fpa_rec_001_target_live_recovery_uses_actual_route_predecessor', 'line': 159, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_fpa_rec_002_cancel_terminalizes_run_task_and_commit_authority(tmp_path: Path, _fpbca_native_case_capture) -> None:
    try:
        original = SectionExecutionGraph._prepare_section

        def cancel_after_prepare(graph, state):
            value = original(graph, state)
            control = current_execution_control()
            assert control is not None
            control.cancel('fpbca-regression-cancel')
            return value
        summary, factory = _run_production_fake(tmp_path / 'cancel-runtime', run_id='fpbca-rec-002', patchers=(patch.object(SectionExecutionGraph, '_prepare_section', cancel_after_prepare),))
        engine = factory.engines[0]
        assert engine.wait_for_idle(timeout_seconds=2.0)
        assert engine.active_worker_count == 0
        run = engine.lifecycle_service.load_run_lifecycle(summary['run_id'])
        assert summary['terminal_outcome']['status'] == 'cancelled'
        assert summary['terminal_outcome']['execution_status'] == 'cancelled'
        assert summary['terminal_outcome']['run_status'] == 'cancelled'
        assert summary['task_state']['status'] == 'cancelled'
        assert summary['status'] == 'cancelled'
        assert run.status is RunLifecycleStatus.CANCELLED
        assert run.finished_at is not None
        with pytest.raises(LifecycleCurrentnessError):
            engine.lifecycle_service.assert_run_committable(summary['run_id'])
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V24'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a_blocker_regressions.py::test_native_observed_fpa_rec_002_cancel_terminalizes_run_task_and_commit_authority', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a_blocker_regressions.py', 'sha256': '7a6bfdcf0cda6469b028de8e7706ebd56f9ccbf75e6a7fe0bf914a4835fd22cb', 'function': 'test_fpa_rec_002_cancel_terminalizes_run_task_and_commit_authority', 'line': 192, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_fpbca_ew_001_source_span_text_is_not_writer_semantic_payload(tmp_path, _fpbca_native_case_capture) -> None:
    try:
        structured = {'schema_version': 'structured_evidence_v1', 'representation_type': 'fact_record', 'original_strategy': 'fact_record', 'actual_strategy': 'fact_record', 'fact': {'record_id': 'fact-1', 'fact_category': 'cost', 'fact_status': 'estimated', 'source_span': {'unit_order_start': 7, 'unit_order_end': 7, 'text': RAW}}, 'fallback': {'used': False}}
        projected = _structured(structured, '/candidate/structured_evidence')
        encoded = json.dumps(projected, ensure_ascii=False)
        assert RAW not in encoded
        assert projected['fact']['source_span'] == {'unit_order_start': 7, 'unit_order_end': 7}
        assert projected['fact']['source_span_ref']['sha256'] == canonical_sha256(structured['fact']['source_span'])
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V21'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a_blocker_regressions.py::test_native_observed_fpbca_ew_001_source_span_text_is_not_writer_semantic_payload', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a_blocker_regressions.py', 'sha256': '7a6bfdcf0cda6469b028de8e7706ebd56f9ccbf75e6a7fe0bf914a4835fd22cb', 'function': 'test_fpbca_ew_001_source_span_text_is_not_writer_semantic_payload', 'line': 62, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
