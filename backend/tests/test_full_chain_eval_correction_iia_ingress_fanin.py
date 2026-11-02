"""Actual ingress/fan-in producers, safe capture reader, official observe/CLI."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession
from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.runner import observe
from eval.full_chain.report import emit_report
from eval.full_chain.snapshot import load_source_snapshot


def _snapshot(observer, root):
    manifest = {'schema_version': 'full_chain_source_manifest_v1', 'snapshot_id': 'native-fixed-boundary',
        'sources': [{'name': 'diagnostics', 'family': 'diagnostics', 'format': 'diagnostic_jsonl',
            'path': str(observer.path), 'sha256': sha256(observer.path.read_bytes()).hexdigest()}]}
    path = root / 'manifest.json'; path.write_text(json.dumps(manifest), encoding='utf-8')
    snapshot = load_source_snapshot(path, [root])
    assert not snapshot.limitations, snapshot.limitations
    return snapshot, path


@pytest.mark.parametrize('mutation', ['normal', 'negative', 'missing'])
def test_native_ingress_identity_from_actual_producer_reaches_official_report(tmp_path, monkeypatch, mutation):
    import application.mainline_service as mainline
    capture = mainline.capture_event
    def fixed_boundary(kind, payload, **kwargs):
        payload = dict(payload)
        if kind == 'P-INGRESS' and payload.get('phase') == 'error':
            if mutation == 'missing':
                return
            if mutation == 'negative':
                payload['subject_id'] = 'foreign-ingress-subject'
        return capture(kind, payload, **kwargs)
    monkeypatch.setattr(mainline, 'capture_event', fixed_boundary)
    @mainline._observe_new_run
    def reject_before_task(self, **kwargs):
        raise ValueError('fixed-ingress-refusal-before-any-task-owner')
    root = tmp_path / 'source'; root.mkdir()
    observer = DiagnosticSession(DiagnosticConfig(root/'capture', root, 'actual-ingress', flush_records=1))
    with observer, pytest.raises(ValueError, match='fixed-ingress-refusal'):
        reject_before_task(object(), user_input='original fixed input', project_input={'project': 'fixed'})
    before = observer.path.read_bytes()
    snapshot, manifest_path = _snapshot(observer, root)
    report = observe(snapshot, EvaluationRunIdentity('iia-ingress-'+mutation, 'fixture'))
    edges = [e for e in report.edge_fidelity if e.edge_id == 'X25']
    expected = {'normal': True, 'negative': False, 'missing': None}[mutation]
    assert any(e.assertion_result is expected for e in edges), edges
    if expected is None:
        assert all(e.assertion_result is None for e in edges)
    if expected is False:
        assert any(f.edge_id == 'X25' and f.evidence_level == 'OBSERVED' for f in report.findings)
    machine_path, _ = emit_report(report, tmp_path/'report', source_roots=[root])
    persisted = json.loads(machine_path.read_text(encoding='utf-8'))
    assert any(e['edge_id'] == 'X25' and e['assertion_result'] is expected for e in persisted['edge_fidelity'])
    cli = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[2]/'scripts/run_full_chain_eval.py'),
        'observe', '--source-manifest', str(manifest_path), '--allowed-root', str(root),
        '--output', str(tmp_path/'cli-report'), '--evaluation-id', 'iia-ingress-cli-'+mutation,
        '--subject-id', 'fixture'], capture_output=True, text=True, encoding='utf-8')
    assert cli.returncode == 0, cli.stdout+cli.stderr
    cli_report = json.loads((tmp_path/'cli-report/evaluation_report.json').read_text(encoding='utf-8'))
    assert any(e['edge_id'] == 'X25' and e['assertion_result'] is expected for e in cli_report['edge_fidelity'])
    assert cli_report['diagnostic_readiness'] == 'NOT_ESTABLISHED'
    assert observer.path.read_bytes() == before


def _actual_fanin_source(root, monkeypatch, mutation):
    import apps.enterprise_document.services.scheme_writer.stage_service as stage_module
    from test_rag_interface_v2_phase4c_compatibility_removal import _bundle, _citation
    from apps.enterprise_document.schemas.scheme_writer.section_execution import SectionExecutionResultSchema
    from apps.enterprise_document.schemas.scheme_writer.evidence import SectionEvidenceBundleSchema
    from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
    from apps.enterprise_document.services.scheme_writer.document_citation_registry import DocumentCitationRegistry
    from contracts.evidence import evidence_bundle_sha256
    from schemas.status import ExecutionStatus
    capture = stage_module.capture_event
    def fixed_boundary(kind, payload, **kwargs):
        payload = dict(payload)
        if kind == 'P-FANIN' and payload.get('phase') == 'after':
            if mutation == 'missing':
                return
            if mutation == 'negative':
                payload['after'] = {**payload['after'], 'content': 'altered source body [C2]'}
        return capture(kind, payload, **kwargs)
    monkeypatch.setattr(stage_module, 'capture_event', fixed_boundary)
    bundle = _bundle('actual-accepted-fixed', scope='section')
    section = SchemeSectionSchema(section_id='fixed-section', section_title='Fixed', section_order=1,
        content='Original text [C1]', model_output='Original text [C1]', prompt='Source [C1]', status=ExecutionStatus.SUCCESS)
    result = SectionExecutionResultSchema(section=section,
        evidence=SectionEvidenceBundleSchema(section_id=section.section_id, section_title=section.section_title,
            evidence_bundle=bundle, evidence_contract_sha256=evidence_bundle_sha256(bundle)),
        citation_registry=[bundle.citations[0]])
    registry = DocumentCitationRegistry.from_citations([_citation('preexisting-fixed', citation_id='C1')])
    stage = object.__new__(stage_module.SchemeGenerationStageService)
    observer = DiagnosticSession(DiagnosticConfig(root/'capture', root, 'actual-fanin', flush_records=1))
    with observer:
        merged = stage._merge_parallel_section_result(result, document_id='fixed-document', citation_registry=registry)
    assert merged.section.content == 'Original text [C2]'
    return observer


@pytest.mark.parametrize('mutation', ['normal', 'negative', 'missing'])
def test_actual_native_fanin_reaches_official_observe_and_cli(tmp_path, monkeypatch, mutation):
    root = tmp_path / 'source'; root.mkdir()
    observer = _actual_fanin_source(root, monkeypatch, mutation)
    before = observer.path.read_bytes()
    snapshot, manifest_path = _snapshot(observer, root)
    report = observe(snapshot, EvaluationRunIdentity('iia-fanin-'+mutation, 'fixture'))
    edges = [e for e in report.edge_fidelity if e.edge_id == 'X26']
    expected = {'normal': True, 'negative': False, 'missing': None}[mutation]
    assert edges and all(e.assertion_result is expected for e in edges), edges
    if expected is False:
        assert any(f.edge_id == 'X26' and f.evidence_level == 'OBSERVED' for f in report.findings)
    output = tmp_path / 'cli-report'
    command = [sys.executable, '-B', str(Path(__file__).resolve().parents[2]/'scripts/run_full_chain_eval.py'),
        'observe', '--source-manifest', str(manifest_path), '--allowed-root', str(root),
        '--output', str(output), '--evaluation-id', 'native-fanin-cli-'+mutation, '--subject-id', 'fixture']
    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    machine = json.loads((output/'evaluation_report.json').read_text(encoding='utf-8'))
    assert [e['assertion_result'] for e in machine['edge_fidelity'] if e['edge_id'] == 'X26'] == [expected]
    assert machine['diagnostic_readiness'] == 'NOT_ESTABLISHED'
    assert observer.path.read_bytes() == before
