"""TXT-only new native observation controls; old source/tests stay unchanged.

Original assertions and native owner calls are copied exactly. Only observational
capture is added; no new business owner/store or semantic operation is added.
"""
import importlib as _fpbca_importlib
from pathlib import Path as _FPBCA_Path
import pytest as _fpbca_pytest
from fpbca_final65_native_capture import NativeCapture as _FPBCA_NativeCapture

_fpbca_base = _fpbca_importlib.import_module('test_multi_agent_v3_target_release_summary')
for _fpbca_name, _fpbca_value in vars(_fpbca_base).items():
    if not _fpbca_name.startswith('__') and not _fpbca_name.startswith('test_'):
        globals().setdefault(_fpbca_name, _fpbca_value)
_FPBCA_DECLARATION = {'hooks': [{'async': False, 'end_line': 78, 'line': 28, 'path': 'backend/model_gateway/model_invoker.py', 'proof_scope': 'ACTUAL_DEF_AST_BINDING_ONLY_NOT_NATIVE_EXECUTION_PROOF', 'sha256': '888618af70072418f93d1fc637b34dbc12a8f720b73d874fdcc0b125a18870c9', 'symbol': 'ModelInvoker.invoke', 'module': 'model_gateway.model_invoker', 'owner': False, 'consumer': False, 'plain_method': True}], 'explicit_local_names': ['actual_request', 'after', 'bad_evidence', 'bad_requirement', 'before', 'bundle', 'calls', 'candidate', 'child', 'chosen', 'chunking', 'content', 'content_v2', 'current', 'destination', 'engine', 'entry', 'env', 'evidence', 'expected', 'fact_v2', 'factory', 'failure', 'frame', 'frozen', 'gate', 'head', 'header', 'invocation', 'kwargs', 'manifest', 'marker', 'material', 'observations', 'old_lineage', 'opened', 'original_hash', 'pack', 'package', 'packer', 'packs', 'parent', 'payload', 'process', 'projection', 'prompt', 'proof', 'provider_calls', 'publication', 'raw', 'record', 'request', 'result', 'retrieval_v2', 'role', 'route', 'row', 'rows', 'run', 'selected', 'source', 'state', 'structured', 'summary', 'support', 'support_payload', 'terminal', 'trace', 'unit', 'verifier', 'versions', 'view', 'writer'], 'pure_projection_rows': ['V08', 'V09', 'V10', 'V11', 'V12', 'V13', 'V14', 'V15', 'V18', 'V25'], 'row_native_symbols': {'V26': []}, 'row_exact_final_consumer_symbols': {'V26': []}, 'generic_fields': ['exact_producer_input_material', 'positive_exact_owner_result', 'negative_mutation_and_rejection', 'final_consumer_observation', 'actual_control_revisions', 'physical_dispatch_count', 'semantic_commit_delta'], 'D5_source_decision': {'path': 'D:\\MyCode\\rag-agent-shared\\docs\\fpbca_bcd_closure_design_v0_2_20261007\\03_D_VERIFICATION_DESIGN_V0_2.md', 'role': 'FROZEN_D1_D2_D5_DISPATCH_MUTATION_ACCOUNTING_WHERE_RELEVANT', 'sha256': '86c300eb36233b96858168d33aa2d69167de1c93c18a115b1fc4dc387b9aa085'}, 'original_module_source': {'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_target_release_summary.py', 'sha256': 'ce7d59e2913c21f4216ee42777893399b800650f4195d63cd415190347efc657'}}

@_fpbca_pytest.fixture(scope='function', autouse=True)
def _fpbca_native_case_capture():
    capture = _FPBCA_NativeCapture(_FPBCA_DECLARATION).install(native_namespaces=(globals(), vars(_fpbca_base)))
    try:
        yield capture
    finally:
        capture.close()

def test_native_observed_actual_target_release_files_hashes_and_final_lineage(target_run, tmp_path, _fpbca_native_case_capture):
    try:
        _, summary, engine, _, publication, payload = target_run
        composition = engine.stage_service.target_post_write_composition
        manifest = publication.manifest
        identity = {'task_id': manifest.task_id, 'run_id': manifest.run_id}
        artifacts = (publication.manifest_artifact, manifest.release_payload_artifact, manifest.release_input_proof_artifact)
        before = {}
        for reference in artifacts:
            stored_ref, content = composition.release_store.get_bytes_with_reference(reference.artifact_ref, **identity)
            assert stored_ref == reference
            assert stored_ref.content_hash == 'sha256:' + hashlib.sha256(content).hexdigest()
            before[reference.artifact_ref] = content
        assert len(list(composition.release_store.root.rglob('*.json'))) == 3
        assert composition.release_store.get_json(publication.manifest_artifact.artifact_ref, **identity) == manifest.model_dump(mode='json')
        assert hashlib.sha256(before[manifest.release_payload_artifact.artifact_ref]).hexdigest() == manifest.release_payload_sha256
        proof = PostWriteReleaseInputProofSchema.model_validate(composition.release_store.get_json(manifest.release_input_proof_artifact.artifact_ref, **identity))
        assert proof.proof_sha256 == manifest.release_input_proof_sha256
        assert proof.document_artifact_ref == manifest.document_artifact_ref
        assert manifest.document_artifact_ref.model_dump(mode='json') == payload['target_document_artifact_ref']
        assert manifest.post_write_control_decision_ref.model_dump(mode='json') == payload['target_post_write_control_decision_ref']
        assert manifest.run_completion_assessment_ref.model_dump(mode='json') == payload['target_run_completion_assessment_ref']
        control = composition.control_store.resolve_exact(manifest.post_write_control_decision_ref)
        assert control.enforcement_action == manifest.enforcement_action_passthrough
        terminal = engine.lifecycle_service.load_run_lifecycle(manifest.run_id)
        assert terminal.status.value == 'success'
        assert terminal.lifecycle_revision == manifest.run_lifecycle_revision
        assert terminal.run_completion_assessment_ref == manifest.run_completion_assessment_ref
        assert summary['status'] == 'partial_success', 'Run success does not upgrade semantic quality'
        for reference in artifacts:
            assert composition.release_store.get_bytes(reference.artifact_ref, **identity) == before[reference.artifact_ref]
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V26'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_target_release_summary.py::test_native_observed_actual_target_release_files_hashes_and_final_lineage', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_target_release_summary.py', 'sha256': 'ce7d59e2913c21f4216ee42777893399b800650f4195d63cd415190347efc657', 'function': 'test_actual_target_release_files_hashes_and_final_lineage', 'line': 74, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_continuation_summary_retains_same_target_publication(target_run, tmp_path, _fpbca_native_case_capture):
    try:
        root, summary, _, source, _, _ = target_run
        actual = MainlineApplicationService()._continuation_summary(workspace=SimpleNamespace(paths=lambda: summary['paths']), task_manager=JsonlTaskManager(root / 'tasks'), result=AgentResultSchema.model_validate(source))
        assert actual.get('target_release_publication') == source['result']['target_release_publication']
        assert actual['release_publication'] is None
        assert actual['status'] == 'partial_success'
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V26'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_target_release_summary.py::test_native_observed_continuation_summary_retains_same_target_publication', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_target_release_summary.py', 'sha256': 'ce7d59e2913c21f4216ee42777893399b800650f4195d63cd415190347efc657', 'function': 'test_continuation_summary_retains_same_target_publication', 'line': 114, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])

def test_native_observed_initial_summary_retains_exact_target_publication_without_legacy_alias(target_run, tmp_path, _fpbca_native_case_capture):
    try:
        _, summary, _, source, publication, payload = target_run
        assert summary.get('target_release_publication') == source['result']['target_release_publication']
        assert summary['target_release_publication'] == payload['target_release_publication']
        assert summary['release_publication'] is None
        assert summary['status'] == 'partial_success'
        assert publication.verified_after_write is True
        assert publication.reuse_state.value == 'NEW_COMMIT'
        assert publication.manifest.immutable is True
        assert publication.manifest.release_mode.value == 'BEST_EFFORT'
        assert publication.manifest.quality_state_passthrough.value == 'UNRESOLVED'
        assert publication.manifest.enforcement_action_passthrough.value == 'OUTPUT_BEST_EFFORT'
        assert publication.manifest.unresolved_reason_refs
        assert len(payload['section_execution_results']) == 8
        assert payload['artifact_route'] == 'deliverable'
        assert payload['terminal_agent_result']['metadata']['release_verified_after_write'] is True
    finally:
        _fpbca_native_case_capture.emit(root=tmp_path, rows=['V26'], local_values=locals(), selector='backend/tests/test_fpbca_final65_obs_multi_agent_v3_target_release_summary.py::test_native_observed_initial_summary_retains_exact_target_publication_without_legacy_alias', native_source={'path': 'D:\\MyCode\\rag-agent-shared\\backend\\tests\\test_multi_agent_v3_target_release_summary.py', 'sha256': 'ce7d59e2913c21f4216ee42777893399b800650f4195d63cd415190347efc657', 'function': 'test_initial_summary_retains_exact_target_publication_without_legacy_alias', 'line': 56, 'native_operation': 'EXACT_ORIGINAL_NATIVE_TEST_FLOW', 'native_refs': []}, native_local_names=[])
