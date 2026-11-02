"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_fpbca_a3_v2_1_recovery')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 3021, 'line': 2881, 'path': 'backend/apps/enterprise_document/workflows/top_level_business_graph.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': 'a87c37a119affd909a3698bd5ead3214777b97cb993c5d78ce8d6b0bc354da71', 'symbol': 'EnterpriseDocumentWorkflowEngine.recovery_plan', 'module': 'apps.enterprise_document.workflows.top_level_business_graph', 'owner': True, 'consumer': True, 'plain_method': True}, {'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V23': ['EnterpriseDocumentWorkflowEngine.recovery_plan']}, 'row_exact_final_consumer_symbols': {'V23': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a3_v2_1_recovery.py', 'sha256': '281c8468050b6619f14dcbc77668f622720896e87d06d1fa9237c775010b83b1'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

@pytest.mark.parametrize('target,source,raw,destination', [(True, 'final_artifact_review', 'continue', 'evaluate_document_gate'), (False, 'final_artifact_review', 'continue', 'decide_document_version'), (False, 'decide_document_version', 'continue', 'evaluate_document_gate'), (False, 'evaluate_document_gate', 'continue', 'build_generation_report'), (False, 'evaluate_document_gate', 'deliverable', 'build_generation_report'), (False, 'evaluate_document_gate', 'rejected', 'build_generation_report'), (False, 'evaluate_document_gate', 'blocked', 'build_generation_report'), (True, 'initialize_generation', 'continue', 'execute_sections'), (False, 'initialize_generation', 'continue', 'execute_sections'), (True, 'evaluate_document_gate', 'deliverable', 'persist_deliverable'), (True, 'evaluate_document_gate', 'rejected', 'persist_rejected'), (True, 'evaluate_document_gate', 'blocked', 'finalize_workflow_result'), (True, 'evaluate_document_gate', 'technical_failure', 'finalize_workflow_failure'), (False, 'build_generation_report', 'deliverable', 'persist_deliverable'), (False, 'build_generation_report', 'rejected', 'persist_rejected'), (False, 'build_generation_report', 'blocked', 'finalize_workflow_result'), (False, 'build_generation_report', 'technical_failure', 'finalize_workflow_failure'), (True, 'persist_deliverable', 'continue', 'finalize_workflow_result'), (True, 'persist_rejected', 'continue', 'finalize_workflow_result'), (True, 'persist_deliverable', 'technical_failure', 'finalize_workflow_failure'), (True, 'persist_rejected', 'technical_failure', 'finalize_workflow_failure')])
def test_native_observed_exact_legal_native_parent_route(target, source, raw, destination, tmp_path, _fpbca_native_case_capture):
    try:
        rows = [checkpoint_row('P', source, raw, None, 1), checkpoint_row('F', destination, 'technical_failure', 'P', 2, failed=destination)]
        assert _plan(rows, target=target)['predecessor'] == source
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V23'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a3_v2_1_recovery.py::test_native_observed_exact_legal_native_parent_route', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a3_v2_1_recovery.py', 'sha256': '281c8468050b6619f14dcbc77668f622720896e87d06d1fa9237c775010b83b1', 'function': 'test_exact_legal_native_parent_route', 'line': 195, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('target,source,route,destination', [(True, 'initialize_generation', 'continue', 'execute_sections'), (False, 'evaluate_document_gate', 'deliverable', 'build_generation_report'), (False, 'evaluate_document_gate', 'rejected', 'build_generation_report'), (False, 'evaluate_document_gate', 'blocked', 'build_generation_report')])
def test_native_observed_native_sqlite_parent_config_proves_focused_occurrence(tmp_path: Path, target, source, route, destination, _fpbca_native_case_capture):
    try:
        parent = checkpoint_row('01-parent', source, route, None, 1)
        failure = checkpoint_row('02-failed', destination, 'technical_failure', '01-parent', 2, failed=destination)
        resource = create_sqlite_checkpoint_resource(tmp_path / 'native-focused.sqlite3')
        try:
            engine, workflow = engine_from_rows([parent, failure], '02-failed', target=target)
            for row in (parent, failure):
                checkpoint = empty_checkpoint()
                checkpoint['id'] = row.config['configurable']['checkpoint_id']
                checkpoint['channel_values'] = row.checkpoint['channel_values']
                config = row.parent_config or {'configurable': {'thread_id': RUN, 'checkpoint_ns': NS}}
                resource.saver.put(config, checkpoint, row.metadata, {})
            engine.checkpointer = resource.saver
            native = resource.saver.get_tuple(_config('02-failed'))
            assert native.parent_config == _config('01-parent')
            plan = engine.recovery_plan(workflow, run_id=RUN)
            assert plan['predecessor'] == source
            assert plan['checkpoint_config']['configurable']['checkpoint_id'] == '01-parent'
            assert plan['checkpoint_config']['configurable']['workflow_namespace'] == NS
        finally:
            resource.close()
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V23'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a3_v2_1_recovery.py::test_native_observed_native_sqlite_parent_config_proves_focused_occurrence', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a3_v2_1_recovery.py', 'sha256': '281c8468050b6619f14dcbc77668f622720896e87d06d1fa9237c775010b83b1', 'function': 'test_native_sqlite_parent_config_proves_focused_occurrence', 'line': 373, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_nearest_wrong_branch_stops_before_older_legal_owner(tmp_path, _fpbca_native_case_capture):
    try:
        older = checkpoint_row('OLD', 'persist_deliverable', 'continue', None, 1)
        gate = checkpoint_row('G', 'evaluate_document_gate', 'deliverable', 'OLD', 2)
        failure = checkpoint_row('F', 'finalize_workflow_result', 'technical_failure', 'G', 3, failed='finalize_workflow_result')
        with pytest.raises(ValueError):
            _plan([older, gate, failure])
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V23'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a3_v2_1_recovery.py::test_native_observed_nearest_wrong_branch_stops_before_older_legal_owner', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a3_v2_1_recovery.py', 'sha256': '281c8468050b6619f14dcbc77668f622720896e87d06d1fa9237c775010b83b1', 'function': 'test_nearest_wrong_branch_stops_before_older_legal_owner', 'line': 229, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

@pytest.mark.parametrize('field,value', [('task_id', 'FOREIGN'), ('run_id', 'FOREIGN'), ('workflow_namespace', 'FOREIGN')])
def test_native_observed_wrong_parent_identity_rejects(field, value, tmp_path, _fpbca_native_case_capture):
    try:
        parent = checkpoint_row('P', 'initialize_generation', 'continue', None, 1)
        parent.checkpoint['channel_values'][field] = value
        failure = checkpoint_row('F', 'execute_sections', 'technical_failure', 'P', 2, failed='execute_sections')
        with pytest.raises(ValueError):
            _plan([parent, failure])
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V23'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_fpbca_a3_v2_1_recovery.py::test_native_observed_wrong_parent_identity_rejects', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_fpbca_a3_v2_1_recovery.py', 'sha256': '281c8468050b6619f14dcbc77668f622720896e87d06d1fa9237c775010b83b1', 'function': 'test_wrong_parent_identity_rejects', 'line': 203, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
