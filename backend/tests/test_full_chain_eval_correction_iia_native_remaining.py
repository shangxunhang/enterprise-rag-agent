"""Original owner records and bounded method observations through observe/CLI."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import load_source_snapshot


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def _observe_files(tmp_path, sources, edge_id, mutation):
    specs=[]
    originals={}
    frozen=tmp_path/'frozen-source'; frozen.mkdir()
    for family, path, format_ in sources:
        raw=path.read_bytes(); originals[path]=raw
        exact=frozen/(family+path.suffix); exact.write_bytes(raw); originals[exact]=raw
        spec={'name':family,'family':family,'path':str(exact),'format':format_,'sha256':sha256(raw).hexdigest()}
        if format_=='diagnostic_jsonl':
            representations=sorted({row['payload_ref']['relative_path'] for row in
                map(json.loads,raw.splitlines()) if row.get('payload_ref')})
            spec['representation_files']=representations
            for relative in representations:
                original_blob=path.parent/relative
                assert original_blob.resolve().is_relative_to(path.parent.resolve())
                content=original_blob.read_bytes(); originals[original_blob]=content
                copied=frozen/relative; copied.parent.mkdir(parents=True,exist_ok=True)
                copied.write_bytes(content); originals[copied]=content
        if format_=='sqlite':
            spec.update(frozen=True,schema_version='gate_v2_post_write_store_v4',tables=[
                'post_write_metadata','artifact_states','post_write_entries','validation_completions',
                'validation_facts','delivery_gate_decisions','post_write_control_decisions',
                'requirement_snapshots','quality_delivery_contracts'])
        specs.append(spec)
    manifest={'schema_version':'full_chain_source_manifest_v1','snapshot_id':'actual-owners-'+edge_id,'sources':specs}
    manifest_path=_json(frozen/'source_manifest.json',manifest)
    snapshot=load_source_snapshot(manifest_path,[frozen],output_root=tmp_path/'snapshot-copy')
    assert not snapshot.limitations, snapshot.limitations
    report=observe(snapshot,EvaluationRunIdentity('remaining-'+edge_id+'-'+mutation,'fixture'))
    assert not any(f.code=='DOMAIN_READ_FAILURE' for f in report.findings)
    expected={'normal':True,'negative':False,'missing':None}[mutation]
    edges=[e for e in report.edge_fidelity if e.edge_id==edge_id]
    assert any(e.assertion_result is expected for e in edges), edges
    if expected is None:
        assert all(e.assertion_result is None for e in edges), edges
    if expected is not None:
        assert any(e.assertion_result is expected and e.source_refs and e.target_refs for e in edges)
    if expected is False:
        assert any(f.edge_id==edge_id for f in report.findings)
    output=tmp_path/'cli-report'
    cli=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[2]/'scripts/run_full_chain_eval.py'),
        'observe','--source-manifest',str(manifest_path),'--allowed-root',str(frozen),
        '--output',str(output),'--evaluation-id','native-'+edge_id+'-'+mutation,'--subject-id','fixture'],
        capture_output=True,text=True,encoding='utf-8')
    assert cli.returncode==0, cli.stdout+cli.stderr
    machine=json.loads((output/'evaluation_report.json').read_text(encoding='utf-8'))
    assert any(e['edge_id']==edge_id and e['assertion_result'] is expected for e in machine['edge_fidelity'])
    assert machine['diagnostic_readiness']=='NOT_ESTABLISHED'
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    return report


@pytest.mark.parametrize('mutation',['normal','negative','missing'])
def test_actual_writer_delivery_and_paragraph_owner_reach_official_edge(tmp_path,mutation):
    from core.diagnostics import DiagnosticConfig,DiagnosticSession
    from test_full_chain_eval_capture import FixedClient,gateway,request
    from test_multi_agent_v3_paragraph_runtime import _runtime,_candidate,_commit_promote
    env,paragraphs,*_= _runtime(tmp_path/'actual-owner')
    client=FixedClient()
    observer=DiagnosticSession(DiagnosticConfig(tmp_path/'capture',tmp_path,'actual-writer-delivery',flush_records=1))
    original=request().model_copy(update={'run_id':env['lineage'].run_id,
        'extra':{'section_id':'s1','call_purpose':'scheme_section_generation'}},deep=True)
    with observer:
        response=gateway(client).generate(original)
        observer.capture_model('WRITER_DELIVERY_PROJECTION',client.calls[0],response=response)
    candidate=_candidate(env,paragraphs,text=response.content)
    version,_=_commit_promote(env,paragraphs,candidate)
    content,_=env['artifact'].read_version(version)
    exported={'version_ref':version.model_dump(mode='json'),'content':content}
    if mutation=='negative':
        exported['content']=deepcopy(content); exported['content']['text']='altered delivered paragraph'
    sources=[('diagnostics',observer.path,'diagnostic_jsonl')]
    if mutation!='missing':
        sources.append(('paragraph_content',_json(tmp_path/'paragraph.json',exported),'json'))
    _observe_files(tmp_path,sources,'X10',mutation)
    assert len(client.calls)==1


@pytest.mark.parametrize('mutation',['normal','negative','missing'])
def test_actual_paragraph_manifest_claim_coverage_structure_reaches_official_edge(tmp_path,mutation):
    import test_multi_agent_v3_claim_materialization_mainline as fixture
    env=fixture._unit_env(tmp_path/'actual-owner')
    fixture._extract(env)
    from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import SECTION_PARAGRAPH_MANIFEST_SEAL
    operation=env.paragraphs.manifest_operation(manifest_ref=env.manifest,
        operation_kind=SECTION_PARAGRAPH_MANIFEST_SEAL,authorization_ref='workflow-generation-complete://iia',
        stable_slot_ref='native-iia-seal')
    env.paragraphs.seal_manifest(operation=operation,expected_manifest_revision=1,
        expected_members=(env.version,),active_lineage=env.context.active_lineage)
    rows=[json.loads(line) for line in env.runtime.ledger_path.read_bytes().splitlines()]
    if mutation=='negative':
        rows[0]['coverage_accounting']['paragraph_version_refs'][0]['content_hash']='sha256:'+'b'*64
    sources=[('paragraph_runtime',env.paragraphs.manifest_ledger_path,'jsonl')]
    if mutation!='missing':
        path=tmp_path/'claims.jsonl'
        path.write_bytes(b''.join(json.dumps(row).encode()+b'\n' for row in rows))
        sources.append(('claim_runtime',path,'jsonl'))
    report=_observe_files(tmp_path,sources,'X11',mutation)
    assert all(m.measurement_state=='UNMEASURED' for m in report.metrics if m.unit_id=='D07'
        and m.metric_id in {'recall','precision','f1'})
    assert len(env.gateway.calls)==1


@pytest.mark.parametrize('mutation',['normal','negative','missing'])
def test_actual_document_verifier_boundary_reaches_official_incremental_edge(tmp_path,mutation):
    from apps.enterprise_document.services.agent_rag_v1.document_verifier import DocumentVerifierService
    from test_phase7_document_closure_mainline import _section,_accepted_state,_project_input
    sections=[_section('scope','建设范围',1,'本项目配置服务器 8 台。'),
        _section('compute','计算资源',2,'本项目配置服务器 8 台。')]
    states={s.section_id:_accepted_state(s.section_id,s.section_title,s.section_order) for s in sections}
    project=_project_input()
    result=DocumentVerifierService().verify_mainline(sections=sections,section_states=states,
        project_input=project,mode='incremental_logic',changed_section_ids={'scope'})
    boundary={'owner_method':'apps.enterprise_document.services.agent_rag_v1.document_verifier.DocumentVerifierService.verify_mainline',
        'input':{'sections':[s.model_dump(mode='json') for s in sections],
            'section_states':{k:v.model_dump(mode='json') for k,v in states.items()},
            'project_input':project.model_dump(mode='json'),'mode':'incremental_logic','changed_section_ids':['scope']},
        'result':result.model_dump(mode='json')}
    if mutation=='negative': boundary['result']['checked_section_ids']=[]
    if mutation=='missing': boundary.pop('result')
    path=_json(tmp_path/'document-precheck.json',boundary)
    _observe_files(tmp_path,[('document_precheck',path,'json')],'X16',mutation)


@pytest.mark.parametrize('mutation',['normal','negative','missing'])
def test_actual_control_and_assembly_manifest_reach_official_selection_edge(tmp_path,mutation):
    from test_bca014_i8_assembly_provenance import _entries
    fx,builder,store,controls,entries=_entries(tmp_path/'actual-owner','PASS')
    ref=builder.build_and_persist(document_logical_id='document-logical:actual-iia',
        required_section_keys=('section-control-1',),ordered_section_entries=(entries[0],))
    manifest=store.resolve_assembly_manifest(ref).model_dump(mode='json')
    control=controls[0].model_dump(mode='json')
    if mutation=='negative':
        manifest['ordered_section_entries'][0]['selected_section_control_decision_ref']['control_decision_hash']='b'*64
    sources=[('control_records',_json(tmp_path/'control.json',control),'json')]
    if mutation!='missing': sources.append(('assembly_manifest',_json(tmp_path/'assembly.json',manifest),'json'))
    _observe_files(tmp_path,sources,'X19',mutation)


@pytest.mark.parametrize('mutation',['normal','negative','missing'])
def test_actual_document_assembly_completion_assessment_reach_official_edge(tmp_path,mutation):
    from test_multi_agent_v3_release_lineage import _assessment_fixture
    fx=_assessment_fixture(tmp_path/'actual-owner')
    pw=fx['post_write']; document=pw['document']
    manifest=pw['env']['binding_store'].resolve_assembly_manifest(document.manifest_ref).model_dump(mode='json')
    assessment=fx['record'].model_dump(mode='json')
    if mutation=='negative':
        assessment['document_artifact_ref']['exact_ref_payload']['artifact_hash']='b'*64
    sources=[('assembly_manifest',_json(tmp_path/'assembly.json',manifest),'json'),
        ('postwrite',pw['env']['database'].path,'sqlite')]
    if mutation!='missing':
        path=tmp_path/'assessment.jsonl'
        path.write_bytes(json.dumps(assessment).encode()+b'\n')
        sources.append(('run_completion_assessments',path,'jsonl'))
    _observe_files(tmp_path,sources,'X20',mutation)


@pytest.fixture(scope='module')
def original_cross_section_owners(tmp_path_factory):
    from unittest.mock import patch
    from apps.enterprise_document.services.agent_rag_v1.context_runtime import ApplicationContextRuntime
    from test_multi_agent_v3_cross_section_context_mainline import _run_dependency_mainline
    packages=[]
    original=ApplicationContextRuntime.materialize_context_package
    def collect(owner,*args,**kwargs):
        result=original(owner,*args,**kwargs)
        if result.consumer_authorization_decisions:
            packages.append(result.model_dump(mode='json'))
        return result
    with patch.object(ApplicationContextRuntime,'materialize_context_package',collect):
        fixture=_run_dependency_mainline(tmp_path_factory,'VERIFIED')
    assert packages
    assert len(fixture['results'])==3
    path=fixture['runtime'].claim_runtime.ledger_path
    return packages,path,path.read_bytes()


@pytest.mark.parametrize('mutation',['normal','negative','missing'])
def test_actual_cross_section_consumption_authorization_reaches_official_edge(tmp_path,mutation,original_cross_section_owners):
    packages,original_path,original_raw=original_cross_section_owners
    path=tmp_path/'claim-runtime.jsonl'; path.write_bytes(original_raw)
    sources=[('claim_runtime',path,'jsonl')]
    if mutation!='missing':
        copied=deepcopy(packages)
        if mutation=='negative':
            for package in copied:
                for decision in package['consumer_authorization_decisions']:
                    decision['consumer_role']='VERIFIER'
        sources.append(('context_packages',_json(tmp_path/'context-packages.json',copied),'json'))
    _observe_files(tmp_path,sources,'X15',mutation)
    assert original_path.read_bytes()==original_raw
