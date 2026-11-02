from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from eval.full_chain.contracts import StageObservation, execution_measurement
from eval.full_chain.execution import AuthorizationError, invoke_scenario, authorize_mode
from eval.full_chain.snapshot import load_source_snapshot, read_jsonl_prefix, SnapshotError
from eval.full_chain.metrics import match_sets,ranked_metrics
from eval.full_chain.compare import compare_samples
from eval.full_chain.report import build_report,emit_report,render_markdown
from eval.full_chain.runner import load_catalog,observe
from eval.full_chain.contracts import EvaluationRunIdentity,ObservationBatch
from eval.full_chain.snapshot import SnapshotView


@pytest.mark.parametrize('case', ['K01', 'K11'])
def test_identity_preserves_segment_scope_round(case):
    base = StageObservation('D10', 'subject', operation_id='op', scope='section1', round=0, source_segment='s1')
    variants = [base, replace(base, scope='section2'), replace(base, round=1), replace(base, source_segment='s2')]
    assert len({item.key for item in variants}) == 4


@pytest.mark.parametrize('case', ['K02'])
def test_execution_axes_do_not_infer_absence(case):
    assert execution_measurement() == 'UNMEASURED'
    assert execution_measurement(executed=False, evidence_complete=True) == 'NOT_RUN'
    assert execution_measurement(enabled=False) == 'N/A'
    assert execution_measurement(technical_failure=True) == 'TECHNICAL_FAILURE'
    assert execution_measurement(executed=True, evidence_complete=True) == 'MEASURED'


@pytest.mark.parametrize('case', ['K07'])
def test_mode_rejects_before_factory_execution(case):
    calls = []
    factories = {'fixed': lambda x: calls.append(x)}
    with pytest.raises(AuthorizationError, match='OBSERVE_EXECUTION_FORBIDDEN'):
        invoke_scenario('OBSERVE', 'fixed', factories, {})
    with pytest.raises(AuthorizationError, match='PENDING_RUN_AUTHORIZATION'):
        authorize_mode('FRESH_BENCHMARK', {'profile': 'configured'})
    with pytest.raises(AuthorizationError, match='LIVE_PROVIDER'):
        invoke_scenario('ISOLATED_REPLAY', 'fixed', factories, {'provider': 'openai'})
    assert calls == []


@pytest.mark.parametrize('case', ['K09'])
def test_jsonl_prefix_and_path_are_non_repairing(tmp_path, case):
    source = tmp_path / 'source'; source.mkdir()
    path = source / 'records.jsonl'; raw = b'{"record_id":"one","command":"do not execute"}\n{"torn":'
    path.write_bytes(raw)
    manifest = {'schema_version': 'full_chain_source_manifest_v1', 'snapshot_id': 's',
        'sources': [{'name': 'events', 'path': str(path), 'format': 'jsonl'}]}
    view = load_source_snapshot(manifest, [source])
    assert view.records('events')[0]['record_id'] == 'one'
    assert 'events:TORN_TAIL_UNCOMMITTED' in view.limitations
    assert path.read_bytes() == raw
    manifest['sources'][0]['path'] = str(tmp_path / 'unauthorized.json')
    (tmp_path / 'unauthorized.json').write_text('{}')
    view = load_source_snapshot(manifest, [source])
    assert 'OUTSIDE_ALLOWED_ROOT' in view.limitations[0]
    assert view.sources['events'].evidence.access == 'UNREADABLE'


def test_committed_bad_json_and_duplicate_conflict_are_not_silently_skipped():
    rows, limits = read_jsonl_prefix(b'{"record_id":"a","x":1}\n{bad}\n{"x":3}\n')
    assert len(rows) == 1 and limits == ('INVALID_COMMITTED_RECORD:2',)
    rows, limits = read_jsonl_prefix(b'{"record_id":"a","x":1}\n{"record_id":"a","x":2}\n')
    assert len(rows) == 1 and limits == ('CONFLICTING_RECORD_ID:2',)


def test_sqlite_no_constructor_no_wal_ignoring(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    path = source / 'state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE facts (ref TEXT)'); db.execute("INSERT INTO facts VALUES ('old')")
    before = path.read_bytes()
    declaration = {'name':'postwrite','path':str(path),'format':'sqlite','tables':['facts'],
                   'schema_version':'fixture_v1','frozen':True}
    manifest = {'schema_version':'full_chain_source_manifest_v1','snapshot_id':'s','sources':[declaration]}
    view = load_source_snapshot(manifest,[source])
    assert view.get('postwrite')['facts'] == [{'ref':'old'}]
    assert path.read_bytes() == before
    Path(str(path)+'-wal').write_bytes(b'badwal')
    view = load_source_snapshot(manifest,[source])
    assert 'WAL_CONSISTENT_SNAPSHOT_NOT_PROVEN' in view.limitations[0]
    assert Path(str(path)+'-wal').read_bytes() == b'badwal'


@pytest.mark.parametrize('case', ['K10'])
def test_exact_replay_cannot_clean_request_or_leak_gold(case):
    with pytest.raises(AuthorizationError, match='MISMATCH'):
        invoke_scenario('isolated-replay','fixed',{'fixed':lambda x:x},{},exact_request_hash='new',recorded_request_hash='old')
    with pytest.raises(AuthorizationError, match='REFERENCE_LEAKAGE'):
        invoke_scenario('isolated-replay','fixed',{'fixed':lambda x:x},{'labels':['gold']})


@pytest.mark.parametrize('case',['K03'])
def test_missing_empty_partial_gold_and_duplicate_matching(case):
    assert ranked_metrics([],None)[0].value is None
    empty=ranked_metrics([{'chunk_id':'x'}],[],synthetic=True)
    assert empty[0].measurement_state=='N/A' and empty[1].value==1
    assert ranked_metrics([],['x'],synthetic=True,judged_complete=False)[0].value is None
    metrics=match_sets(['x','x'],['x'],synthetic=True)
    assert metrics[0].value==.5 and metrics[1].value==1
    legacy=ranked_metrics([{'chunk_id':'x'},{'chunk_id':'y'}],['x'],synthetic=True,top_k=2)
    assert next(m for m in legacy if m.metric_id=='recall_at_k').value==1


def _paired(value=1,status='MEASURED'):
    from hashlib import sha256
    from eval.full_chain import compare
    code_hash = sha256(Path(compare.__file__).read_bytes()).hexdigest()
    return {'sample_id':'a','replicate_id':'1','unit_id':'D05','scope':'section',
        'gold_hash':'gold','top_k':2,'grain':'claim','input_hash':'in','model':'model',
        'prompt_hash':'prompt','index_hash':'idx','permission_hash':'perm','resource_hash':'resources',
        'metric_version':'v1','code_hash':code_hash,'status':status,'value':value,'execution_kind':'FRESH'}


@pytest.mark.parametrize('case',['K04','K05'])
def test_paired_outer_join_failures_and_prompt_replay(case):
    planned=[_paired(),{**_paired(),'sample_id':'failed'}]
    left=[_paired(),{**_paired(),'sample_id':'failed','status':'TECHNICAL_FAILURE','value':None}]
    result=compare_samples(planned,left,[_paired(2)])
    assert result['planned_count']==2 and result['comparable_count']==1 and result['overall_delta'] is None
    assert result['pairs'][1]['candidate'] is None and result['pairs'][1]['baseline']['status']=='TECHNICAL_FAILURE'
    result=compare_samples([_paired()],[_paired()],[{**_paired(2),'prompt_hash':'new','execution_kind':'REPLAY'}],['prompt_hash'])
    assert result['status']=='NOT_COMPARABLE' and result['pairs'][0]['delta'] is None
    result=compare_samples([_paired()],[_paired()],[{**_paired(2),'model':'candidate','request_hash':'fresh-candidate-request','response_request_hash':'fresh-candidate-request','provider_attempt_id':'fresh-fixed-boundary'}],['model'])
    assert result['overall_delta']==1
    assert compare_samples([_paired()],[_paired()],[{**_paired(2),'model':'candidate'}],['model'])['overall_delta'] is None


@pytest.mark.parametrize('case',['K06','K08','K12'])
def test_domain_failure_and_deterministic_json_markdown(tmp_path,case):
    catalog=load_catalog()
    def fail(snapshot,**kwargs): raise ValueError('broken fixture')
    adapters={d['id']:(fail if d['id']=='01' else lambda s,**kw:ObservationBatch('fixture')) for d in catalog['domains'] if d['id']!='07'}
    identity=EvaluationRunIdentity('e','pre-task')
    report=observe(SnapshotView.from_records({}),identity,adapters=adapters,catalog=catalog)
    assert report.execution_coverage['read_status']=='PARTIAL'
    assert report.diagnostic_readiness=='NOT_ESTABLISHED'
    assert any(f.code=='DOMAIN_READ_FAILURE' for f in report.findings)
    repeat=observe(SnapshotView.from_records({}),identity,adapters=adapters,catalog=catalog)
    assert report.to_dict()==repeat.to_dict()
    json_path,md_path=emit_report(report,tmp_path/'report')
    assert json.loads(json_path.read_text())['identity']['subject_id']=='pre-task'
    assert md_path.read_text()==render_markdown(report)
    with pytest.raises(FileExistsError): emit_report(report,tmp_path/'report')


def test_references_cannot_self_sign_or_infer_gold(tmp_path):
    from eval.full_chain.references import load_references
    reference_root=Path(__file__).resolve().parents[2]/'data'/'eval'/'full_chain'
    view=load_references(reference_root/'reference_manifest.json',[reference_root])
    assert set(view.review_status)=={f'G{i}' for i in range(1,6)}
    assert set(view.review_status.values())=={'PROVISIONAL'}

def test_existing_baseline_manager_requires_paired_aggregate_identity():
    from types import SimpleNamespace
    from eval.full_chain.compare import baseline_rows
    paired=compare_samples([_paired()],[_paired()],[_paired(2)])
    def report(name,value):
        return SimpleNamespace(experiment_id=name,sample_count=1,failure_count=0,success_count=1,
            aggregate_metrics={'metric':value},static_spec_id='fixed',status='success',
            static_retrieval_spec_hash='a'*64,duration_ms=1.0,
            samples=[SimpleNamespace(sample_id='a',success=True,metrics={'metric':1 if name=='base' else 2},metadata={'full_chain_pair_identity':{k:_paired()[k] for k in ('sample_id','replicate_id','unit_id','scope')}})])
    rows=baseline_rows(paired,[report('base',1),report('candidate',2)],['metric'],baseline_experiment_id='base')
    assert rows[1].baseline_deltas['metric']==1
    with pytest.raises(ValueError,match='UNVERIFIED_AGGREGATE_METRIC'):
        baseline_rows(paired,[report('base',1),report('candidate',999)],['metric'],baseline_experiment_id='base')
    forged=report('candidate',2);forged.samples[0].sample_id='wrong-same-mean'
    with pytest.raises(ValueError,match='RAW_SAMPLE_IDENTITY_MISMATCH'):
        baseline_rows(paired,[report('base',1),forged],['metric'],baseline_experiment_id='base')


def test_compared_invariants_planned_manifest_extras_nonfinite():
    good=_paired()
    both_changed=dict(good,input_hash='wrong-planned-input')
    assert compare_samples([good],[both_changed],[both_changed])['overall_delta'] is None
    extra=dict(good,sample_id='unplanned')
    assert compare_samples([good],[good],[good,extra])['status']=='NOT_COMPARABLE'
    assert compare_samples([good],[good],[dict(good,value=float('nan'))])['overall_delta'] is None


@pytest.mark.parametrize('row',[{'begin_ns':20,'end_ns':10,'clock_domain':'c'},{'begin_ns':10,'end_ns':20}])
def test_invalid_workflow_clock_window_is_not_measured(row):
    from eval.full_chain.report import build_report
    batch=ObservationBatch('06',stages=[StageObservation('F06','s',payload={'event_type':'SPAN_RESULT','category':'WORKFLOW_WALL',**row})])
    report=build_report(EvaluationRunIdentity('e','s'),[batch])
    assert report.performance_cost['workflow_wall_state']=='UNMEASURED'
    assert report.performance_cost['workflow_wall_ns'] is None
