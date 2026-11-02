"""FCI01-FCI08: real isolated owners, then passive frozen-file evaluation."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import pytest

from core.diagnostics import DiagnosticConfig,DiagnosticSession
from eval.full_chain.contracts import EvaluationRunIdentity,canonical_hash
from eval.full_chain.domains.generation_claim import check_generation_edge,model_representations
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import SnapshotView,load_source_snapshot
from eval.full_chain.references import load_references
from eval.full_chain.report import emit_report,render_markdown

REPO=Path(__file__).resolve().parents[2]
REF_ROOT=REPO/'data'/'eval'/'full_chain'

def file_hashes(root):
    return {str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

def freeze_sources(root,files,*,snapshot_id='isolated-fake'):
    declarations=[]
    for name,path,family,fmt in files:
        declaration={'name':name,'family':family,'format':fmt,'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest(),
            'owner':family,'schema_version':'diagnostic_event_v1' if fmt=='diagnostic_jsonl' else 'DECLARED_ORIGINAL_OWNER_RECORDS'}
        if fmt=='diagnostic_jsonl':
            rows=[json.loads(line) for line in path.read_bytes().splitlines()]
            declaration['representation_files']=sorted({r['payload_ref']['relative_path'] for r in rows if isinstance(r.get('payload_ref'),dict)})
        declarations.append(declaration)
    manifest={'schema_version':'full_chain_source_manifest_v1','snapshot_id':snapshot_id,'sources':declarations}
    path=root/'source_manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    return path

def test_FCI01_actual_eight_section_fake_freeze_cli_observe(tmp_path,monkeypatch):
    from test_phase3_section_execution_subgraph import _run_fake
    source=tmp_path/'source';source.mkdir()
    observer=DiagnosticSession(DiagnosticConfig(source/'capture',source,'FCI01',flush_records=64,max_queue_events=512,max_queue_bytes=8*1024*1024,max_event_bytes=2*1024*1024,max_depth=32,max_representation_total_bytes=12*1024*1024,max_session_bytes=96*1024*1024,max_events=131072,window_s=180))
    with observer:
        summary,factory=_run_fake(source/'business',run_id='FCI01-isolated-eight')
    assert len(summary['generated_draft']['sections'])==8
    assert summary.get('deliverable_final_result') is not None
    summary_path=source/'summary.json';summary_path.write_text(json.dumps(summary,ensure_ascii=False),encoding='utf-8')
    files=[('diagnostics',observer.path,'diagnostics','diagnostic_jsonl'),('public_summary',summary_path,'public_summary','json')]
    # Read exact native append-only Claim/Support/Artifact ledgers; no output is
    # promoted into an independent expected business verdict.
    for ordinal,path in enumerate(sorted((source/'business').rglob('*.jsonl'))):
        if path.stat().st_size<4*1024*1024:
            family={'artifact_runtime.jsonl':'artifact_runtime','research_runtime.jsonl':'research_runtime',
                'FCI01-isolated-eight_trace.jsonl':'runtime_trace'}.get(path.name,'owner_records')
            files.append(('owner_'+str(ordinal),path,family,'jsonl'))
    manifest=freeze_sources(source,files)
    before=file_hashes(source)
    from model_gateway.model_gateway import ModelGateway
    from bootstrap.supervisor_factory import SupervisorFactory
    def forbidden(*a,**k): raise AssertionError('OBSERVE_CONSTRUCTED_OR_INVOKED_OWNER')
    monkeypatch.setattr(ModelGateway,'generate',forbidden)
    monkeypatch.setattr(SupervisorFactory,'build',forbidden)
    snapshot=load_source_snapshot(manifest,[source],max_bytes=128*1024*1024,max_records=131073)
    assert observer.health()['completeness']=='COMPLETE' and observer.health()['lost_count']==0
    public=[row for row in snapshot.records('diagnostics') if row.get('event_type')=='S-PUBLIC']
    assert len(public)==1
    assert public[0]['payload']=={key:summary.get(key) for key in (
        'task_id','run_id','status','terminal_outcome','release_publication',
        'target_release_publication','deliverable_final_result','shutdown_diagnostics')}
    physical_counts={kind:sum(row.get('event_type')==kind for row in snapshot.records('diagnostics'))
        for kind in ('MODEL_ATTEMPT_REQUEST','MODEL_CLIENT_RAW_RESPONSE','MODEL_GATEWAY_PROJECTED_RESPONSE')}
    assert set(physical_counts.values())=={43}
    refs=load_references(REF_ROOT/'reference_manifest.json',[REF_ROOT])
    report=observe(snapshot,EvaluationRunIdentity('FCI01','FCI01-isolated-eight',source_snapshot_id=snapshot.snapshot_id),reference_view=refs)
    assert not any(f.code=='DOMAIN_READ_FAILURE' for f in report.findings)
    assert report.execution_coverage['expected_unit_count']==55
    assert not report.execution_coverage['missing_units']
    assert set(report.execution_coverage['edge_ids'])=={f'X{i:02d}' for i in range(1,31)}
    assert report.performance_cost['workflow_wall_state']=='MEASURED'
    assert report.performance_cost['timeline']['provider_union_ns']
    assert any(s.unit_id=='D07' and s.payload.get('record_origin')=='PERSISTED_OWNER' for s in report.stage_quality)
    for unit in ('B02','B03','B04','C13','D03'):
        assert any(s.unit_id==unit and s.measurement_state=='MEASURED' and s.evidence_refs and
            s.identity_quality==('ORIGINAL_TRACE_REF' if unit=='B02' else 'ORIGINAL_OWNER_REF')
            for s in report.stage_quality),unit
    route_metrics={metric.metric_id:metric for metric in report.metrics if metric.unit_id=='B02'}
    assert route_metrics['route_contract_conformance'].assertion_result is True
    assert route_metrics['task_classification_accuracy'].measurement_state=='N/A'
    assert route_metrics['router_llm_call_count'].measurement_state=='UNMEASURED'
    assert report.diagnostic_readiness=='NOT_ESTABLISHED'
    assert set(report.reference_review.values())=={'PROVISIONAL'}
    paths=emit_report(report,tmp_path/'report',source_roots=[source])
    assert paths[1].read_text(encoding='utf-8')==render_markdown(report)
    cli=subprocess.run([sys.executable,str(REPO/'scripts'/'run_full_chain_eval.py'),'observe',
        '--source-manifest',str(manifest),'--allowed-root',str(source),'--output',str(tmp_path/'cli_report'),
        '--references',str(REF_ROOT/'reference_manifest.json'),'--max-source-bytes',str(128*1024*1024),
        '--max-source-records','131073',
        '--evaluation-id','FCI01-cli','--subject-id','FCI01-isolated-eight'],capture_output=True,text=True,encoding='utf-8')
    assert cli.returncode==0,cli.stdout+cli.stderr
    assert json.loads(cli.stdout)['diagnostic_readiness']=='NOT_ESTABLISHED'
    assert file_hashes(source)==before
    receipt={'scenario':'FCI01','original_status':summary['status'],'sections':8,'physical_fixed_calls':sum(r.get('event_type')=='MODEL_ATTEMPT_REQUEST' for r in snapshot.records('diagnostics')),
        'real_provider_calls':0,'observe_source_before':before,'observe_source_after':file_hashes(source),
        'observe_owner_construction_and_gateway_probes':'PASS','report_paths':[str(p) for p in paths],
        'capture_health':observer.health(),'missing_material_is_not_business_pass':True}
    (tmp_path/'FCI01_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')

@pytest.mark.parametrize('edge_id',[f'X{i:02d}' for i in range(10,16)])
def test_FCI04_generation_edges_normal_single_mutation_missing(tmp_path,edge_id):
    source,target,physical=generation_edge_fixture(tmp_path,edge_id)
    assert check_generation_edge(edge_id,source,target,physical=physical).assertion_result is True
    changed=deepcopy(target)
    if edge_id=='X10': changed['paragraphs']=[{'text':'wrong same-cardinality'}]
    elif edge_id=='X11': changed['claim_ids']=['omitted']
    elif edge_id=='X12': changed['binding']['project_id']='foreign'
    elif edge_id=='X13': changed['support']['binding_ref']='other-binding'
    elif edge_id=='X14': changed['result']['positive_basis_refs']=['context-only']
    else: changed['consumer']['role']='unaccepted-role'
    assert check_generation_edge(edge_id,source,changed,physical=physical).assertion_result is False
    assert check_generation_edge(edge_id,source,None,physical=physical).assertion_result is None

def generation_edge_fixture(tmp_path,edge_id):
    physical=None
    if edge_id=='X10':
        from test_full_chain_eval_capture import FixedClient,gateway,request,session,captured
        client=FixedClient();observer=session(tmp_path,'edge-X10')
        with observer:
            response=gateway(client).generate(request())
            observer.capture_model('WRITER_DELIVERY_PROJECTION',client.calls[0],response)
        prefix=captured(observer,tmp_path)
        physical=model_representations(prefix['events'])['physical_attempts'][0]
        source={'physical_attempt':physical};target={'paragraphs':[{'text':'A'}]}
    elif edge_id=='X11': source={'expected_claim_ids':['capacity']};target={'claim_ids':['capacity']}
    elif edge_id=='X12':
        fields={k:'exact' for k in ('claim_ref','project_id','scope','source_ref','version','time','unit')}
        source={'claim':fields};target={'binding':deepcopy(fields)}
    elif edge_id=='X13': source={'binding':{'binding_ref':'b1','allowed_refs':['A']}};target={'support':{'binding_ref':'b1','supporting_refs':['A']}}
    elif edge_id=='X14': source={'support':{'support_ref':'s1','supporting_refs':['A']}};target={'result':{'support_ref':'s1','verdict':'VERIFIED','positive_basis_refs':['A']}}
    else:
        source={'verified_source':{'verdict':'VERIFIED','revision':1,'allowed_roles':['dependency'],'allowed_scopes':['section2']}}
        target={'consumer':{'accepted_dependency':True,'source_revision':1,'role':'dependency','scope':'section2','memory_enabled':False}}
    return source,target,physical

@pytest.mark.parametrize('edge_id',[f'X{i:02d}' for i in range(1,31)])
def test_FCI04_all_thirty_edges_use_domain_readers_and_negative_oracle(tmp_path,edge_id):
    """Each node reads endpoint material and checks a related single corruption."""
    number=int(edge_id[1:])
    if edge_id=='X01':
        from test_full_chain_eval_ingestion_index import test_x01_reads_actual_router_endpoints_and_computes_coverage
        for mutation in (None,'unknown_routing','missing_prefilter'):
            root=tmp_path/str(mutation);root.mkdir()
            test_x01_reads_actual_router_endpoints_and_computes_coverage(mutation,root)
        return
    if edge_id=='X02':
        from test_full_chain_eval_ingestion_index import ingestion_lineage_snapshot
        normal=ingestion_lineage_snapshot();negative=ingestion_lineage_snapshot(mutation='row');missing=ingestion_lineage_snapshot(mutation='missing')
    elif 3<=number<=7:
        from test_full_chain_eval_retrieval import test_real_retrieval_edges_normal_and_single_missing_or_visibility_mutation
        test_real_retrieval_edges_normal_and_single_missing_or_visibility_mutation(edge_id,tmp_path)
        return
    elif edge_id in ('X08','X09','X25','X26','X30'):
        from test_full_chain_eval_planning_prewrite import planning_edge_snapshot
        normal=planning_edge_snapshot(edge_id);negative=planning_edge_snapshot(edge_id,negative=True);missing=SnapshotView.from_records({})
    elif edge_id in ('X16','X17','X18','X19','X20','X21','X27'):
        from test_full_chain_eval_postwrite_release import postwrite_edge_snapshot
        normal=postwrite_edge_snapshot(edge_id);negative=postwrite_edge_snapshot(edge_id,negative=True);missing=SnapshotView.from_records({})
    elif edge_id in ('X22','X23','X24','X28','X29'):
        from test_full_chain_eval_runtime_reliability import runtime_edge_fixture
        def make(bad):
            source,target=runtime_edge_fixture(edge_id,bad)
            return SnapshotView.from_records({'runtime_edges':[{'edge_id':edge_id,'source':source,'target':target,'assertion_result':True}]})
        normal=make(False);negative=make(True);missing=SnapshotView.from_records({})
    else:
        source,target,physical=generation_edge_fixture(tmp_path,edge_id)
        keys={'X10':('W-RAW','W-PARA'),'X11':('W-PARA','W-CLAIM'),'X12':('W-CLAIM','W-BIND'),
            'X13':('W-BIND','W-SUPPORT'),'X14':('W-SUPPORT','W-VERIFY'),'X15':('W-VERIFY','W-CROSS')}
        left,right=keys[edge_id];rows={left:source,right:target}
        if edge_id=='X10':
            from eval.full_chain.domains.runtime_reliability import read_diagnostic_prefix
            prefix=read_diagnostic_prefix(tmp_path/'edge-X10'/'process-0.jsonl',[tmp_path])
            rows['diagnostics']=prefix['events']+[prefix['seal']]
        normal=SnapshotView.from_records(rows);changed=deepcopy(rows)
        if edge_id=='X10': changed[right]['paragraphs'][0]['text']='changed'
        elif edge_id=='X11': changed[right]['claim_ids']=[]
        elif edge_id=='X12': changed[right]['binding']['scope']='wrong'
        elif edge_id=='X13': changed[right]['support']['supporting_refs']=['foreign']
        elif edge_id=='X14': changed[right]['result']['positive_basis_refs']=['context-only']
        else: changed[right]['consumer']['source_revision']=2
        negative=SnapshotView.from_records(changed);missing=SnapshotView.from_records({})
    for label,snapshot,expected in [('normal',normal,True),('negative',negative,False),('missing',missing,None)]:
        report=observe(snapshot,EvaluationRunIdentity('FCI04-'+edge_id+'-'+label,'fixture'))
        edge=next(e for e in report.edge_fidelity if e.edge_id==edge_id)
        assert edge.assertion_result is expected,(edge_id,label,edge)
        if expected is None: assert edge.measurement_state=='UNMEASURED'

def test_existing_strategy_runner_factory_port_and_label_separation(tmp_path):
    from eval.full_chain.execution import guarded_runtime_factory,AuthorizationError
    from eval.rag.strategy.runner import StrategyEvalRunner
    from eval.rag.strategy.schemas import ExperimentConfig,ExperimentMatrixConfig,RAGEvalSample
    spec=tmp_path/'spec.yaml';spec.write_bytes((REPO/'backend/rag/config/static_retrieval_v1.yaml').read_bytes())
    sample=RAGEvalSample(sample_id='fixed',query='actual query',expected_child_chunk_ids=['child1'],reference_answer='private gold',source_refs=['private ref'],metadata={'review':'private note'})
    dataset=tmp_path/'dataset.jsonl';dataset.write_text(json.dumps(sample.model_dump(mode='json'))+'\n',encoding='utf-8')
    calls=[]
    class FixedRuntime:
        def run(self,runtime_sample,experiment):
            assert runtime_sample.query=='actual query'
            assert not runtime_sample.expected_child_chunk_ids and not runtime_sample.source_refs
            assert not runtime_sample.reference_answer and not runtime_sample.metadata
            calls.append(runtime_sample)
            return {'retrieval_results':[{'child_chunk_id':'child1'}]}
        def metadata(self):
            from rag.config.static_retrieval import StaticRetrievalSpecLoader
            actual=StaticRetrievalSpecLoader().load(spec,project_root=tmp_path)
            return {'static_spec_id':actual.spec_id,'static_spec_version':actual.spec_version,
                'static_retrieval_spec_file':str(spec),'static_retrieval_spec_hash':actual.config_hash(),
                'components':actual.canonical_dict(),'provider':'fixed','real_provider_calls':0}
        def close(self): calls.append('closed')
    class FixedFactory:
        def build(self,**kwargs): return FixedRuntime()
    config=ExperimentMatrixConfig(matrix_id='full-chain-port',dataset_path=str(dataset),dataset_version='d1',eval_set_version='g1',index_version='i1',output_dir=str(tmp_path/'runner_output'),metrics=['hit_at_k'],experiments=[ExperimentConfig(experiment_id='fixed',static_retrieval_spec_file=str(spec))])
    guarded=guarded_runtime_factory('ISOLATED_REPLAY',FixedFactory(),resources={'provider':'fixed'})
    report=StrategyEvalRunner(runtime_factory=guarded).run(config,project_root=tmp_path,matrix_config_path=tmp_path/'matrix.json',matrix_config_hash='a'*64)
    assert report.rows[0].metrics['hit_at_k']==1 and len(calls)==2
    blocked=guarded_runtime_factory('OBSERVE',FixedFactory(),resources={'provider':'fixed'})
    with pytest.raises(AuthorizationError,match='OBSERVE_EXECUTION_FORBIDDEN'): blocked.build()

def test_FCI02_timeout_missing_stages_remain_unknown():
    report=observe(SnapshotView.from_records({'S-ITEM':{'dimension':'consistency','parse_error':'timeout','run_id':'fake-r'}}),EvaluationRunIdentity('FCI02','fake-r'))
    assert any(s.measurement_state=='TECHNICAL_FAILURE' for s in report.stage_quality)
    assert any(s.measurement_state=='UNMEASURED' for s in report.stage_quality)
    assert report.execution_coverage['measurement_counts']['UNMEASURED']>0

@pytest.mark.parametrize('action',['OUTPUT_BEST_EFFORT','BLOCK'])
def test_FCI03_legal_policy_retains_negative_quality(action):
    report=observe(SnapshotView.from_records({'S-CONTROL':{'run_id':'r1','quality_state':'FAIL','action':action,'allowed_actions':[action],'policy_version':'fixed'}}),EvaluationRunIdentity('FCI03','r1'))
    stage=next(s for s in report.stage_quality if s.operation_id=='S-CONTROL')
    assert stage.observed_quality_status=='FAIL' and stage.observed_control_action==action

def test_FCI05_independent_claim_omission_and_illegal_basis():
    refs=load_references(REF_ROOT/'reference_manifest.json',[REF_ROOT])
    selected=[refs.for_family('G3')[0],refs.for_family('G4')[0]]
    rows=[{'reference_id':r['reference_id'],'source_sha256':r['source_sha256'],'input_completeness':'COMPLETE'} for r in selected]
    rows[0]['claim_ids']=['survey-proposed']
    rows[1].update(binding={'allowed_refs':['A','B']},support={'relation':'SUPPORTED','supporting_refs':['A']},result={'verdict':'VERIFIED','positive_basis_refs':['B']})
    report=observe(SnapshotView.from_records({'reference_observations':rows}),EvaluationRunIdentity('FCI05','synthetic'),reference_view=refs)
    assert any(m.metric_id=='recall' and m.unit_id=='D07' and m.value==0 for m in report.metrics)
    assert any(m.metric_id=='provenance_verifier' and m.assertion_result is False for m in report.metrics)
    assert any(m.metric_id=='provenance_binding' and m.measurement_state=='UNMEASURED' for m in report.metrics)
    assert report.diagnostic_readiness=='NOT_ESTABLISHED'

@pytest.mark.parametrize('family',['G1','G2','G3','G4','G5'])
def test_reference_families_normal_single_error_and_hash_binding(family):
    from eval.full_chain.reference_metrics import evaluate_references
    refs=load_references(REF_ROOT/'reference_manifest.json',[REF_ROOT]);ref=refs.for_family(family)[0]
    row={'reference_id':ref['reference_id'],'source_sha256':ref['source_sha256'],'input_completeness':'COMPLETE'}
    if family=='G1': row['retained_atoms']=ref['labels']['required_atoms'][:]
    elif family=='G2': row.update(grain='child',top_k=2,results=[{'child_chunk_id':'current-A','chunk_id':'current-A'}])
    elif family=='G3': row['claim_ids']=ref['labels']['expected_claims'][:]
    elif family=='G4': row.update(binding={**ref['labels']['binding'],'allowed_refs':['A','B']},support={'relation':'SUPPORTED','supporting_refs':['A']},result={'verdict':'VERIFIED','positive_basis_refs':['A']})
    else: row.update(requirements=['R1'],citation_refs=['A'],scope='DOCUMENT')
    normal=evaluate_references(SnapshotView.from_records({'reference_observations':[row]}),refs)
    assert normal.metrics and all(m.assertion_result is not False for m in normal.metrics)
    assert all(m.representation=='SYNTHETIC_MECHANISM' for m in normal.metrics)
    bad=deepcopy(row)
    if family=='G1': bad['retained_atoms'].pop()
    elif family=='G2': bad['results'][0].update(chunk_id='foreign-B',child_chunk_id='foreign-B')
    elif family=='G3': bad['claim_ids']=['survey-proposed']
    elif family=='G4': bad['result']['positive_basis_refs']=['B']
    else: bad['citation_refs']=['B']
    negative=evaluate_references(SnapshotView.from_records({'reference_observations':[bad]}),refs)
    assert any(m.assertion_result is False for m in negative.metrics)
    assert negative.findings
    bad['source_sha256']='wrong-source'
    unbound=evaluate_references(SnapshotView.from_records({'reference_observations':[bad]}),refs)
    assert all(m.measurement_state=='UNMEASURED' for m in unbound.metrics)
    assert set(refs.review_status.values())=={'PROVISIONAL'}

def test_FCI06_real_capture_timelines_keep_cpu_and_wall_scopes(tmp_path):
    from test_full_chain_eval_capture import FixedClient,gateway,request,session,captured
    observer=session(tmp_path,'timeline')
    with observer:
        with observer.span('workflow','WORKFLOW_WALL'):
            gateway(FixedClient()).generate(request())
    prefix=captured(observer,tmp_path)
    report=observe(SnapshotView.from_records({'diagnostics':prefix['events']+[prefix['seal']]}),EvaluationRunIdentity('FCI06','fixed'))
    timeline=report.performance_cost['timeline']
    assert report.performance_cost['workflow_wall_state']=='MEASURED'
    assert timeline['provider_union_ns'] and timeline['cpu_is_wall_partition'] is False
    assert timeline['critical_path_state']=='UNKNOWN'

def test_FCI07_planned_failure_cannot_be_omitted_from_gain():
    from eval.full_chain.compare import compare_samples
    from test_full_chain_eval_core import _paired
    planned=[_paired(),dict(_paired(),sample_id='missing')]
    comparison=compare_samples(planned,planned,[_paired(2)])
    report=observe(SnapshotView.from_records({}),EvaluationRunIdentity('FCI07','fixed'),comparison=comparison)
    assert report.comparison['planned_count']==2 and report.comparison['overall_delta'] is None
    assert report.comparison['pairs'][1]['candidate'] is None

def test_FCI08_torn_source_no_constructor_and_cli_isolated_replay(tmp_path,monkeypatch):
    from model_gateway.model_gateway import ModelGateway
    source=tmp_path/'torn-source';source.mkdir();path=source/'torn.jsonl'
    path.write_bytes(b'{"unit_id":"F02","completeness":"COMPLETE"}\n{"unfinished":')
    manifest=freeze_sources(source,[('runtime',path,'runtime','jsonl')])
    before=file_hashes(source)
    with monkeypatch.context() as scoped:
        scoped.setattr(ModelGateway,'__init__',lambda *a,**k:(_ for _ in ()).throw(AssertionError('observe gateway constructor')))
        snap=load_source_snapshot(manifest,[source]);report=observe(snap,EvaluationRunIdentity('FCI08','prefix'))
    assert 'TORN_TAIL' in '|'.join(report.limitations)
    assert report.execution_coverage['read_status']=='PARTIAL' and file_hashes(source)==before
    from test_full_chain_eval_capture import request
    material=request().model_dump(mode='json');material['extra']={}
    replay={'request':material,'request_hash':canonical_hash(material),'recorded_request_hash':canonical_hash(material),'fixed_response':'fixed original'}
    replay_path=tmp_path/'request.json';replay_path.write_text(json.dumps(replay),encoding='utf-8')
    result=subprocess.run([sys.executable,str(REPO/'scripts'/'run_full_chain_eval.py'),'isolated-replay',
        '--source-manifest',str(replay_path),'--allowed-root',str(tmp_path),'--output',str(tmp_path/'unused-report'),
        '--capture-output',str(tmp_path/'isolated-capture'),'--scenario','fixed-gateway','--evaluation-id','FCI08-replay'],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['fixed_physical_calls']==1
    from eval.full_chain.domains.runtime_reliability import read_diagnostic_prefix
    prefix=read_diagnostic_prefix(json.loads(result.stdout)['capture_path'],[tmp_path])
    physical=model_representations(prefix['events'])['physical_attempts'][0]
    assert set(physical['representations'])=={'MODEL_ATTEMPT_REQUEST','MODEL_CLIENT_RAW_RESPONSE','MODEL_GATEWAY_PROJECTED_RESPONSE'}
    assert 'WRITER_DELIVERY_PROJECTION' not in physical['representations']
