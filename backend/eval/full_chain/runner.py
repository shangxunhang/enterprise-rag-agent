"""Explicit pure extraction. One reader failure does not hide other domains."""
from __future__ import annotations
import importlib
import json
from pathlib import Path
from .contracts import EvaluationRunIdentity, Finding, ObservationBatch, StageObservation
from .report import build_report

_ADAPTERS={'01':('ingestion_index','extract_ingestion'),'02':('retrieval','extract_retrieval'),
    '03':('planning_prewrite','extract_planning_prewrite'),'04':('generation_claim','extract_generation_claim'),
    '05':('postwrite_release','extract_postwrite'),'06':('runtime_reliability','extract_runtime_observations')}

def load_catalog(path=None):
    path=Path(path) if path else Path(__file__).resolve().parents[3]/'data'/'eval'/'full_chain'/'unit_edge_catalog.json'
    catalog=json.loads(path.read_text(encoding='utf-8'))
    units=[u for d in catalog['domains'] for u in d['units']]
    edges=[e for d in catalog['domains'] for e in d['owned_edges']]
    cases=[c for d in catalog['domains'] for c in d['cases']]
    if len(units)!=55 or len(set(units))!=55 or sorted(edges)!=[f'X{i:02d}' for i in range(1,31)] or len(set(cases))!=97:
        raise ValueError('FROZEN_CATALOG_MISMATCH')
    if {k:len(v) for k,v in catalog['unit_dispositions'].items()}!={'KEEP_V1':36,'SIMPLIFY':15,'MERGE':3,'DEFER':1,'REMOVE':0}:
        raise ValueError('FROZEN_DISPOSITION_MISMATCH')
    return catalog

def observe(snapshot, identity: EvaluationRunIdentity, *, reference_view=None,catalog=None,adapters=None,comparison=None):
    if identity.mode!='OBSERVE': raise ValueError('OBSERVE_IDENTITY_REQUIRED')
    catalog=catalog or load_catalog(); batches=[]
    for domain in catalog['domains']:
        domain_id=domain['id']
        if domain_id=='07':
            batches.append(ObservationBatch('07',stages=[StageObservation('F07',identity.subject_id,
                operation_id=identity.evaluation_id,executed=True,measurement_state='MEASURED',
                observed_execution_status='EXTRACTED',payload={'snapshot_id':snapshot.snapshot_id})])); continue
        try:
            if adapters is not None and domain_id in adapters:
                adapter=adapters[domain_id]
            else:
                module,name=_ADAPTERS[domain_id]
                adapter=getattr(importlib.import_module('.domains.'+module,__package__),name)
            batch=adapter(snapshot,subject_id=identity.subject_id)
            if not isinstance(batch,ObservationBatch): raise TypeError('DOMAIN_BATCH_CONTRACT_REQUIRED')
            batches.append(batch)
        except Exception as error:
            batches.append(ObservationBatch(domain_id,findings=[Finding('DOMAIN_READ_FAILURE',identity.subject_id,
                type(error).__name__+':'+str(error),origin='evaluation')],limitations=[domain_id+':DOMAIN_READ_FAILURE']))
    if snapshot.limitations:
        batches.append(ObservationBatch('snapshot',limitations=list(snapshot.limitations)))
    from .reference_metrics import evaluate_references
    batches.append(evaluate_references(snapshot,reference_view,subject_id=identity.subject_id,
        stages=tuple(stage for batch in batches for stage in batch.stages)))
    return build_report(identity,batches,comparison,catalog=catalog,reference_view=reference_view)
