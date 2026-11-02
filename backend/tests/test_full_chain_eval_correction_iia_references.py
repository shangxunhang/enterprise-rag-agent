"""Reference labels remain external while native finite pools feed the report."""
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession
from eval.full_chain.contracts import EvaluationRunIdentity, ObservationBatch
from eval.full_chain.references import ReferenceView
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import load_source_snapshot


@pytest.mark.parametrize('variant',['normal','negative','provisional','wrong_binding','ambiguous',
                                    'missing_results','child_only_forbidden','bad_field_path','invalid_k'])
def test_native_pool_reaches_review_bound_reference_calculator_without_runtime_labels(tmp_path,variant):
    root=tmp_path/'source'; root.mkdir()
    candidate={'child_chunk_id':'current-A','chunk_id':'current-A','doc_id':'document-A','tenant_id':'tenant-A','index_version':'index-A'}
    if variant in {'negative','child_only_forbidden'}:
        candidate={**candidate,'child_chunk_id':'foreign-B','chunk_id':'foreign-B'}
    if variant=='child_only_forbidden':
        candidate.pop('chunk_id')
    observer=DiagnosticSession(DiagnosticConfig(root/'capture',root,'native-reference',flush_records=1))
    with observer:
        observer.emit_pool({'evidence_key':'R-DENSE','operation_ref':'actual-fixed-retrieval','run_id':'fixed-run',
            'query':'actual query','round':0,'grain':'child','top_k':0 if variant=='invalid_k' else 2,'phase':'RESULT','declared_count':1},[candidate])
    raw=observer.path.read_bytes()
    assert b'reference_id' not in raw and b'relevant_allowed' not in raw
    manifest={'schema_version':'full_chain_source_manifest_v1','snapshot_id':'native-reference',
        'sources':[{'name':'diagnostics','family':'diagnostics','format':'diagnostic_jsonl','path':str(observer.path),'sha256':sha256(raw).hexdigest()}]}
    snapshot=load_source_snapshot(manifest,[root]); assert not snapshot.limitations
    preliminary=observe(snapshot,EvaluationRunIdentity('native-reference-read','fixed-run'))
    stage=next(s for s in preliminary.stage_quality if s.unit_id=='C04' and s.payload.get('candidates'))
    binding={'unit_id':'C04','operation_id':stage.operation_id,'query':stage.query,'round':stage.round,
        'source_raw_sha256':sha256(raw).hexdigest(),'field_paths':{'results':'candidates','grain':'grain','top_k':'top_k'}}
    if variant=='wrong_binding':
        binding['source_raw_sha256']='wrong-source-hash'
    if variant=='missing_results':
        binding['field_paths'].pop('results')
    if variant=='bad_field_path':
        binding['field_paths']['results']=None
    source='Independent fixture reference: current-A authorized, foreign-B forbidden.'
    record={'reference_id':'native-G2','family':'G2','source':source,'source_sha256':sha256(source.encode()).hexdigest(),
        'labels':{'relevant_allowed':['current-A'],'relevant_forbidden':['foreign-B']},
        'match_rules':{'native_observation':binding},'effective_review_status':'PROVISIONAL' if variant=='provisional' else 'SYNTHETIC_MECHANISM'}
    reference=ReferenceView((record,),{f'G{i}':'PROVISIONAL' for i in range(1,6)},())
    adapters={'02':lambda snap,**kwargs:ObservationBatch('02',stages=[stage,stage])} if variant=='ambiguous' else None
    report=observe(snapshot,EvaluationRunIdentity('native-reference-result','fixed-run'),reference_view=reference,adapters=adapters)
    metrics=[m for m in report.metrics if m.reference_ref=='native-G2']
    assert metrics
    if variant in {'wrong_binding','ambiguous','provisional','missing_results','bad_field_path','invalid_k'}:
        assert all(m.measurement_state=='UNMEASURED' for m in metrics),metrics
    else:
        assert next(m for m in metrics if m.metric_id=='recall_at_k').value==({'normal':1,'negative':0,'child_only_forbidden':0}[variant])
        assert next(m for m in metrics if m.metric_id=='forbidden_use_count').value==({'normal':0,'negative':1,'child_only_forbidden':1}[variant])
        assert all(m.representation=='SYNTHETIC_MECHANISM' for m in metrics)
    if variant in {'negative','child_only_forbidden'}:
        assert any(f.code=='REFERENCE_QUALITY_FAILURE' and f.evidence_level=='OBSERVED' for f in report.findings)
    assert set(report.reference_review.values())=={'PROVISIONAL'}
    assert report.diagnostic_readiness=='NOT_ESTABLISHED'
    assert observer.path.read_bytes()==raw
