"""Two small opted-in Fake Runs with real mainline owners and no old runtime."""
import json
import socket
import uuid
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.post_write import consistency as formal
from apps.enterprise_document.services.agent_rag_v1 import post_write_composition as composition_module
from apps.enterprise_document.services.agent_rag_v1.disk_guard import DiskBudgetGuard
from model_gateway.fake_llm_client import FakeLLMClient
from test_formal_guard_v2_isolation_native_20261018 import (
    _FixedNativeIngress, _new_root, _save, _tree,
)
from test_deterministic_review_report_native_20261017 import _runtime_paths
import test_multi_agent_v3_consistency_mainline as native


@pytest.mark.parametrize('ref_failure', [False, True], ids=['three-consumers', 'missing-ref-technical'])
def test_new_run_real_consistency_consumers_and_fixed_root(ref_failure):
    root = _new_root('ref-failure' if ref_failure else 'scopes')
    suffix = '-' + uuid.uuid4().hex
    task_id = 'task-consistency348-formal_eq' + suffix
    run_id = 'run-consistency348-formal_eq' + suffix
    sections = 1 if ref_failure else 2
    workflow = native.workflow_tests
    project = workflow._w13_current_new_task_project_input(
        workflow._w13_single_section_project_input(task_id) if sections == 1
        else workflow._w13_two_section_project_input(task_id))
    entry = _FixedNativeIngress(root, task_id, run_id, project.model_dump(mode='json'))
    fake_calls, guards, semantics, commits, identities, copies, sif13 = [], [], [], [], [], [], []
    generate = FakeLLMClient.generate
    evaluate = formal.FormalExecutabilityGuard.evaluate
    semantic = formal.SemanticConsistencyEvaluator.evaluate
    commit = formal.FormalExecutabilityGuard.commit
    identity = composition_module.validation_run_identity
    admit = DiskBudgetGuard.assert_new_durable_run_allowed
    from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine
    copy_hook = EnterpriseDocumentWorkflowEngine._maybe_seal_writer_review_copy

    def fake(client, request):
        fake_calls.append(request.extra.get('response_contract') or request.extra.get('call_purpose'))
        return generate(client, request)

    def guarded(self, *, binding, assertions_by_ref):
        before = len(fake_calls)
        operands = {} if ref_failure else assertions_by_ref
        result = evaluate(self, binding=binding, assertions_by_ref=operands)
        assert len(fake_calls) == before
        guards.append({'scope': binding.validation_scope, 'binding_ref': binding.binding_ref,
            'validation_run_id': binding.validation_run_id, 'producer': self.PRODUCER_REF,
            'state': result.state.value, 'reasons': result.reason_codes,
            'model_calls_before': before, 'model_calls_after': len(fake_calls)})
        return result

    def semantic_evaluate(self, **arguments):
        before = len(fake_calls)
        result = semantic(self, **arguments)
        assert len(fake_calls) == before
        semantics.append({'scope': arguments['binding'].validation_scope,
            'binding_ref': arguments['binding'].binding_ref,
            'same_returned_relation': arguments['relation'].value,
            'result_relation': result.relation.value, 'model_calls': before})
        return result

    def committed(self, **arguments):
        ref = commit(self, **arguments)
        commits.append({'producer': self.PRODUCER_REF, 'ref': ref.model_dump(mode='json')})
        return ref

    def identified(**arguments):
        result = identity(**arguments)
        identities.append({**arguments, 'validation_run_id': result,
            'quality_contract_ref': arguments['quality_contract_ref'].model_dump(mode='json')})
        return result

    def sealed(self, **arguments):
        result = copy_hook(self, **arguments)
        copies.append({'result': result, 'guard_evaluations_at_copy': len(guards)})
        return result

    def admitted(self, roots, scope_ref):
        result = admit(self, roots, scope_ref)
        sif13.append({'scope_ref': scope_ref,
            'roots': [str(p) for p in roots], 'states': [p.state.value for m, p in result]})
        return result

    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Native Fake attempted network')), \
         patch.object(FakeLLMClient, 'generate', fake), \
         patch.object(formal.FormalExecutabilityGuard, 'evaluate', guarded), \
         patch.object(formal.FormalExecutabilityGuard, 'commit', committed), \
         patch.object(formal.SemanticConsistencyEvaluator, 'evaluate', semantic_evaluate), \
         patch.object(composition_module, 'validation_run_identity', identified), \
         patch.object(EnterpriseDocumentWorkflowEngine, '_maybe_seal_writer_review_copy', sealed), \
         patch.object(DiskBudgetGuard, 'assert_new_durable_run_allowed', admitted):
        engine, calls, captures, summary, top = native._run(root, 'FORMAL_EQ',
            sections=sections, repeats=1, identity_suffix=suffix, fixed_ingress=entry,
            global_formal=True, body_padding='本节按既定工作流程逐步展开说明。' * 12,
            allow_terminal_error=ref_failure)
    assert guards and commits and identities and sif13
    assert all(row['producer'] == 'post-write-formal-executability:v2' for row in guards + commits)
    assert all(row['guard_evaluations_at_copy'] == 0 for row in copies)
    assert copies, 'unchanged Writer Copy hook must execute before Guard evaluations'
    actual_ids = {row['validation_run_id'] for row in identities}
    assert all(outcome[0].validation_run_id in actual_ids for args, item, facts, outcome in captures)
    if ref_failure:
        assert all(row['state'] == 'TECHNICAL_FAILURE' for row in guards)
        assert not semantics
        assert top.terminal_error is not None
        assert summary['status'] == 'failed'
        assert 'Consistency semantic failure receipt differs' in top.terminal_error.message
        assert not captures, 'the unchanged receipt validator rejects before Completion/Gate'
        with engine.stage_service.target_post_write_composition.database.read_transaction() as connection:
            committed_facts = [json.loads(row[0]) for row in connection.execute(
                'SELECT fact_json FROM validation_facts').fetchall()]
            assert not [f for f in committed_facts if f['family'] == 'ARTIFACT_CONSISTENCY']
            assert connection.execute('SELECT COUNT(*) FROM validation_completions').fetchone()[0] == 0
            assert connection.execute('SELECT COUNT(*) FROM delivery_gate_decisions').fetchone()[0] == 0
        for args, item, facts, outcome in captures:
            if args['scope'].value == 'SECTION':
                assert item.state.value == 'TECHNICAL_FAILURE'
                assert not facts and not item.fact_refs and not item.positive_proof_refs
    else:
        assert {row['scope'] for row in guards} == {'SECTION', 'DOCUMENT', 'DOCUMENT_GLOBAL'}
        assert all(row['state'] == 'NOT_EXECUTABLE' for row in guards)
        assert {row['binding_ref'] for row in guards} == {row['binding_ref'] for row in semantics}
        assert all(row['same_returned_relation'] == row['result_relation'] == 'CONSISTENT' for row in semantics)
        for args, item, facts, outcome in captures:
            assert item.state.value == 'PASS'
            assert facts and all(f.semantic_state.value == 'PASS' for f in facts)
            assert all('FORMAL_RELATION_FALSE' not in f.payload['observation_codes'] for f in facts)
    paths = _runtime_paths(engine, root)
    assert all(p['inside_whole_output_root'] for p in paths)
    tree = _tree(root)
    ready_copies = [row['result'] for row in copies if row['result'] is not None]
    assert ready_copies, 'original hook must return a sealed readable Writer Copy'
    from pathlib import Path
    import hashlib
    writer_files = {}
    for sealed_pair in ready_copies:
        md = Path(sealed_pair['markdown_path'])
        assert md.is_relative_to(root) and md.is_file()
        digest = hashlib.sha256(md.read_bytes()).hexdigest()
        assert digest == sealed_pair['receipt']['markdown_sha256']
        writer_files[str(md.relative_to(root))] = digest
    report_files = {name: digest for name, digest in tree.items() if 'review-report-' in name}
    _save('NATIVE_' + ('REF_FAILURE' if ref_failure else 'THREE_SCOPES') + '.json', {
        'evidence_level': 'ISOLATED_NATIVE_FAKE_REAL_OWNERS', 'output_root': str(root),
        'task_id': task_id, 'run_id': run_id, 'guards': guards, 'semantics': semantics,
        'guard_commit_refs': commits, 'actual_validation_id_inputs': identities,
        'runtime_paths': paths, 'sif13': sif13, 'fake_model_calls': len(fake_calls),
        'normal_consistency_requests': [p for p in fake_calls if p and 'consistency' in p],
        'new_real_provider_calls': 0, 'old_runtime_roots_used': [],
        'writer_copy_hooks': copies, 'writer_files': writer_files, 'report_files': report_files,
        'summary': summary, 'ref_failure_test_only': ref_failure,
        'terminal_error': top.terminal_error.model_dump(mode='json') if top.terminal_error else None,
        'persisted_consistency': [{'scope': args['scope'].value, 'state': item.state.value,
            'fact_count': len(facts), 'completion_ref': outcome[0].model_dump(mode='json')}
            for args, item, facts, outcome in captures]})
