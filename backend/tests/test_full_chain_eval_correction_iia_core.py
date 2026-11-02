"""Targeted correction contracts through public report and comparison paths."""
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from eval.full_chain.compare import compare_samples
from eval.full_chain.contracts import EvaluationEvidenceRef, EvaluationRunIdentity, EdgeObservation, ObservationBatch, StageObservation
from eval.full_chain.report import build_report, emit_report, render_markdown
from test_full_chain_eval_core import _paired


@pytest.mark.parametrize('case',['normal','second_count_fault','missing_seal'])
def test_repeated_native_pool_ids_keep_distinct_complete_occurrences(tmp_path,case):
    import subprocess
    import sys
    from test_full_chain_eval_runtime_iia_correction import capture,snapshot,formal,edge
    source=tmp_path/'source'; source.mkdir()
    session=capture(source)
    with session:
        session.emit_pool({'pool_id':'stable-native-package:selected','declared_count':2},[{'id':'first-A'},{'id':'first-B'}])
        session.emit_pool({'pool_id':'stable-native-package:selected','declared_count':3 if case=='second_count_fault' else 2},[{'id':'second-A'},{'id':'second-B'}])
    path=session.path
    if case=='missing_seal':
        path=session.path.parent/'unsealed.jsonl'
        path.write_bytes(b'\n'.join(session.path.read_bytes().splitlines()[:-1])+b'\n')
    original=path.read_bytes()
    report=formal(snapshot(source,session,capture_path=path))
    expected={'normal':True,'second_count_fault':False,'missing_seal':None}[case]
    assert edge(report,'X24').assertion_result is expected
    json_path,_=emit_report(report,tmp_path/'report',source_roots=[source])
    persisted=json.loads(json_path.read_text(encoding='utf-8'))
    assert any(row['edge_id']=='X24' and row['assertion_result'] is expected for row in persisted['edge_fidelity'])
    cli=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[2]/'scripts/run_full_chain_eval.py'),
        'observe','--source-manifest',str(source/'manifest.json'),'--allowed-root',str(source),
        '--output',str(tmp_path/'cli-report'),'--evaluation-id','pool-occurrence-'+case,'--subject-id','subject'],
        capture_output=True,text=True,encoding='utf-8')
    assert cli.returncode==0,cli.stdout+cli.stderr
    cli_data=json.loads((tmp_path/'cli-report/evaluation_report.json').read_text(encoding='utf-8'))
    assert any(row['edge_id']=='X24' and row['assertion_result'] is expected for row in cli_data['edge_fidelity'])
    assert path.read_bytes()==original


def test_long_original_reference_runs_serialize_losslessly_without_repeating_metadata(tmp_path):
    source=tmp_path/'native.jsonl'
    source.write_text(''.join(json.dumps({'sequence':i,'value':'original'})+'\n' for i in range(220)),encoding='utf-8')
    evidence=EvaluationEvidenceRef(str(source),raw_sha256=sha256(source.read_bytes()).hexdigest(),integrity='VALID',completeness='COMPLETE')
    pointers=list(range(200))+[5]+list(range(201,220))
    refs=tuple(replace(evidence,pointer='/'+str(i)) for i in pointers)
    edge=EdgeObservation('X24','subject',source_refs=refs,target_refs=refs,assertion_result=False,
        measurement_state='MEASURED',payload={'original_record_count':len(refs)})
    report=build_report(EvaluationRunIdentity('ref-serialization','subject'),[ObservationBatch('fixture',edges=[edge])])
    serialized=report.to_dict()['edge_fidelity'][0]['source_refs']
    assert len(serialized)<10
    restored=[]
    for ref in serialized:
        if 'record_selection' not in ref:
            restored.append(ref)
            continue
        selection=ref['record_selection']
        assert selection['representation']=='EXACT_ORDERED_ORIGINAL_POINTER_RANGE'
        original={k:v for k,v in ref.items() if k!='record_selection'}
        selected=[dict(original,pointer='/'+str(i)) for i in range(selection['start'],selection['end_exclusive'])]
        assert selection['count']==len(selected)
        assert selection['original_refs_sha256']==sha256(json.dumps(selected,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        restored.extend(selected)
    assert restored==[asdict(ref) for ref in refs]
    assert report.edge_fidelity[0].source_refs==refs
    assert len(json.dumps(serialized))<len(json.dumps(restored))/10
    assert source.read_text(encoding='utf-8').count('\n')==220
    observed=next(f for f in report.findings if f.code=='OBSERVED_EDGE_FAILURE')
    projected=next(f for f in report.to_dict()['findings'] if f['code']=='OBSERVED_EDGE_FAILURE')
    assert len(projected['evidence_refs'])<10
    restored_finding=[]
    for ref in projected['evidence_refs']:
        selection=ref.get('record_selection')
        if selection:
            metadata={key:value for key,value in ref.items() if key!='record_selection'}
            restored_finding.extend(dict(metadata,pointer='/'+str(i)) for i in range(selection['start'],selection['end_exclusive']))
        else:
            restored_finding.append(ref)
    assert restored_finding==[asdict(ref) for ref in observed.evidence_refs]


def _failed(edge_id, target=None):
    return EdgeObservation(edge_id, 'subject', target_key=target.key if target else None,
        target_refs=target.evidence_refs if target else (), assertion_result=False,
        measurement_state='MEASURED', completeness='COMPLETE', reason='persisted structural mismatch')


@pytest.mark.parametrize('edge_ids', [('X27', 'X03'), ('X03', 'X27')])
def test_passive_failure_set_has_no_edge_number_cause_or_reproduction(edge_ids):
    report = build_report(EvaluationRunIdentity('e', 'subject'), [ObservationBatch('fixture', edges=[_failed(e) for e in edge_ids])])
    observed = [f for f in report.findings if f.code == 'OBSERVED_EDGE_FAILURE']
    assert {f.edge_id for f in observed} == set(edge_ids)
    assert all(f.evidence_level == 'OBSERVED' for f in report.findings)
    assert all(any('EARLIEST_FAILURE_NOT_PROVEN' in u for u in f.unknowns) for f in observed)
    summary = next(f for f in report.findings if f.code == 'FIRST_OBSERVED_EDGE_FAILURE')
    assert summary.edge_id is None
    assert not any(f.code == 'EARLIEST_OBSERVED_FAILURE' for f in report.findings)


def _ordered_stage(sequence, *, segment='one', integrity='VALID'):
    ref = EvaluationEvidenceRef('fixture.jsonl', integrity=integrity, completeness='COMPLETE')
    return StageObservation('E01', 'subject', operation_id=str(sequence), evidence_refs=(ref,),
        payload={'session_id': 'session', 'segment_id': segment, 'sequence': sequence})


def test_recorded_sequence_orders_observed_failures_without_proving_first_cause():
    early, late = _ordered_stage(2), _ordered_stage(4)
    report = build_report(EvaluationRunIdentity('e', 'subject'), [ObservationBatch('fixture',
        stages=[late, early], edges=[_failed('X03', late), _failed('X27', early)])])
    found = next(f for f in report.findings if f.code == 'EARLIEST_OBSERVED_FAILURE')
    assert found.edge_id == 'X27' and found.evidence_level == 'OBSERVED'
    assert any('does not prove cause' in u for u in found.unknowns)


@pytest.mark.parametrize('variant', ['parallel_segment', 'invalid_ref', 'duplicate_target', 'same_sequence'])
def test_parallel_missing_or_ambiguous_order_never_selects_an_earliest_failure(variant):
    early, late = _ordered_stage(2), _ordered_stage(4)
    stages = [early, late]
    if variant == 'parallel_segment':
        late = _ordered_stage(4, segment='other'); stages = [early, late]
    elif variant == 'invalid_ref':
        late = _ordered_stage(4, integrity='UNKNOWN'); stages = [early, late]
    elif variant == 'duplicate_target':
        stages += [early]
    elif variant == 'same_sequence':
        late = replace(late, payload={**late.payload, 'sequence': 2}); stages = [early, late]
    report = build_report(EvaluationRunIdentity('e', 'subject'), [ObservationBatch('fixture',
        stages=stages, edges=[_failed('X27', early), _failed('X03', late)])])
    assert not any(f.code == 'EARLIEST_OBSERVED_FAILURE' for f in report.findings)
    assert next(f for f in report.findings if f.code == 'FIRST_OBSERVED_EDGE_FAILURE').edge_id is None


@pytest.mark.parametrize('unknown', [None, '', 'UNKNOWN', 'UNMEASURED', 'NOT_RECORDED',
                                      ' UNKNOWN ', '\tUnmeasured\n', '  NOT_RECORDED  '])
def test_unknown_code_fingerprint_retains_pair_but_never_computes_gain(unknown):
    row = dict(_paired(), code_hash=unknown)
    result = compare_samples([row], [row], [dict(row, value=2)])
    assert result['planned_count'] == 1 and result['comparable_count'] == 0
    assert result['overall_delta'] is None and result['pairs'][0]['delta'] is None


def test_undeclared_code_change_and_planned_baseline_drift_are_not_comparable():
    row = _paired()
    candidate = dict(row, code_hash='code-B', value=2)
    assert compare_samples([row], [row], [candidate])['overall_delta'] is None
    result = compare_samples([row], [candidate], [dict(candidate, executed_code_hash='code-B')], ['code_hash'])
    assert 'PLANNED_MISMATCH:code_hash' in result['pairs'][0]['reasons']


@pytest.mark.parametrize('factor',['index_hash','gold_hash','permission_hash','resource_hash'])
def test_unknown_fixed_fingerprint_cannot_become_a_valid_same_version_pair(factor):
    row=dict(_paired(),**{factor:'UNKNOWN'})
    comparison=compare_samples([row],[row],[dict(row,value=2)])
    assert comparison['status']=='NOT_COMPARABLE'
    assert comparison['overall_delta'] is None
    assert 'UNKNOWN:'+factor in comparison['pairs'][0]['reasons']


def test_large_unbound_payload_remains_available_in_serialized_report():
    original={'material':'unbound original '*700}
    report=build_report(EvaluationRunIdentity('unbound-report','subject'),[
        ObservationBatch('fixture',stages=[StageObservation('D05','subject',operation_id='unbound',payload=original)])])
    serialized=report.to_dict()
    actual=next(row['payload'] for row in serialized['stage_quality'] if row['operation_id']=='unbound')
    assert actual==original


@pytest.mark.parametrize('kind', ['FRESH', 'REPLAY'])
def test_declared_code_experiment_accepts_bound_execution_or_exact_recomputation(kind):
    row = _paired()
    candidate = dict(row, code_hash='code-B', executed_code_hash='code-B', value=2, execution_kind=kind)
    if kind == 'REPLAY':
        candidate.update(replay_kind='RECOMPUTE', replay_input_hash=row['input_hash'])
    result = compare_samples([row], [row], [candidate], ['code_hash'])
    assert result['status'] == 'COMPARABLE' and result['overall_delta'] == 1


@pytest.mark.parametrize('bad', [
    {'executed_code_hash': 'wrong'},
    {'execution_kind': 'UNKNOWN'},
    {'execution_kind': 'REPLAY'},
    {'execution_kind': 'REPLAY', 'replay_kind': 'RECOMPUTE', 'replay_input_hash': 'wrong'},
])
def test_declared_change_does_not_relabel_old_results_as_new_code(bad):
    row = _paired()
    candidate = dict(row, code_hash='code-B', executed_code_hash='code-B', value=2)
    candidate.update(bad)
    assert compare_samples([row], [row], [candidate], ['code_hash'])['overall_delta'] is None


@pytest.mark.parametrize('factor,carrier', [('index_hash', 'executed_index_hash'), ('retrieval_strategy_hash', 'executed_retrieval_strategy_hash')])
def test_changed_retrieval_factor_requires_the_actual_reexecuted_fingerprint(factor, carrier):
    row = dict(_paired(), **{factor: 'A'})
    candidate = dict(row, **{factor: 'B'}, value=2)
    assert compare_samples([row], [row], [candidate], [factor])['overall_delta'] is None
    candidate.update({carrier: 'B'})
    assert compare_samples([row], [row], [candidate], [factor])['overall_delta'] == 1
    candidate.update(execution_kind='REPLAY', replay_kind='RECOMPUTE', replay_input_hash=row['input_hash'])
    assert compare_samples([row], [row], [candidate], [factor])['overall_delta'] is None
    candidate['retrieval_execution_id'] = 'fixed-new-retrieval'
    assert compare_samples([row], [row], [candidate], [factor])['overall_delta'] == 1


def test_missing_failed_and_extra_samples_remain_in_code_bound_population():
    row = _paired()
    planned = [row, dict(row, sample_id='failed')]
    baseline = [row, dict(row, sample_id='failed', status='TECHNICAL_FAILURE', value=None)]
    comparison = compare_samples(planned, baseline, [dict(row, value=2), dict(row, sample_id='extra')])
    assert comparison['planned_count'] == 2 and comparison['comparable_count'] == 1
    assert comparison['overall_delta'] is None and comparison['unplanned']
    assert comparison['pairs'][1]['baseline']['status'] == 'TECHNICAL_FAILURE'


def test_report_payload_summary_preserves_exact_source_and_leaves_observation_unchanged(tmp_path):
    source = tmp_path / 'source.json'; source.write_text(json.dumps({'body': 'x' * 20000}), encoding='utf-8')
    ref = EvaluationEvidenceRef(str(source), raw_sha256=sha256(source.read_bytes()).hexdigest(), pointer='/body', integrity='VALID', completeness='COMPLETE')
    payload = {'event_type': 'MODEL_CLIENT_RAW_RESPONSE', 'material': {'body': 'x' * 20000}, 'items': list(range(300))}
    stage = StageObservation('D05', 'subject', evidence_refs=(ref,), payload=payload)
    report = build_report(EvaluationRunIdentity('e', 'subject'), [ObservationBatch('fixture', stages=[stage])])
    row = report.to_dict()['stage_quality'][0]
    assert report.stage_quality[0].payload == payload and len(report.stage_quality[0].payload['material']['body']) == 20000
    assert row['payload']['representation'] == 'EVALUATION_PAYLOAD_SUMMARY'
    assert row['payload']['collection_counts']['items'] == 300
    assert row['evidence_refs'][0]['raw_sha256'] == sha256(source.read_bytes()).hexdigest()
    assert row['payload']['elided_material_is_not_empty']
    json_path, md_path = emit_report(report, tmp_path / 'report', source_roots=[source])
    assert json.loads(json_path.read_text(encoding='utf-8')) == json.loads(json.dumps(report.to_dict()))
    assert md_path.read_text(encoding='utf-8') == render_markdown(report)
    assert 'x' * 1000 not in json_path.read_text(encoding='utf-8')
    assert 'evaluation_report.json#/stage_quality' in md_path.read_text(encoding='utf-8')
