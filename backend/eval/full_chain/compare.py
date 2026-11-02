"""Planned-sample outer joins precede any delta calculation."""
from __future__ import annotations
from collections import Counter
from typing import Any, Mapping, Sequence
import math

_KEY = ('sample_id','replicate_id','unit_id','scope')
_INVARIANTS = ('gold_hash','top_k','grain','scope','input_hash','model','prompt_hash',
               'index_hash','permission_hash','resource_hash','metric_version','code_hash')
_OPTIONAL_FACTORS = ('retrieval_strategy_hash',)


def _known_fingerprint(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().upper() not in {'UNKNOWN', 'UNMEASURED', 'NOT_RECORDED'}


def _changed_execution_reasons(row, changes):
    """Changed code/index/strategy must be bound to the executed computation."""
    reasons = []
    carriers = {'code_hash': 'executed_code_hash', 'index_hash': 'executed_index_hash',
                'retrieval_strategy_hash': 'executed_retrieval_strategy_hash'}
    changed = changes & carriers.keys()
    if not changed:
        return reasons
    kind = row.get('execution_kind')
    if kind not in {'FRESH', 'REPLAY'}:
        reasons.append('CHANGED_FACTOR_EXECUTION_NOT_PROVEN')
    for factor in sorted(changed):
        if not _known_fingerprint(row.get(factor)) or row.get(carriers[factor]) != row.get(factor):
            reasons.append('EXECUTED_FINGERPRINT_NOT_BOUND:' + factor)
    if kind == 'REPLAY':
        # A recomputation under different evaluator code is a valid code A/B.
        # Relabelling an old result as the new implementation is not.
        if row.get('replay_kind') != 'RECOMPUTE' or row.get('replay_input_hash') != row.get('input_hash'):
            reasons.append('REPLAY_RECOMPUTATION_LINEAGE_NOT_PROVEN')
        if changed & {'index_hash', 'retrieval_strategy_hash'} and not row.get('retrieval_execution_id'):
            reasons.append('RETRIEVAL_REEXECUTION_NOT_PROVEN')
    return reasons


def _key(row):
    return tuple(row.get(k) for k in _KEY)


def compare_samples(planned_sample_manifest: Sequence[Mapping[str, Any]], baseline,
                    candidate, declared_changes=()) -> dict[str, Any]:
    planned_keys = [_key(r) for r in planned_sample_manifest]
    if any(any(v is None for v in k) for k in planned_keys):
        raise ValueError('PLANNED_COMPOUND_KEY_REQUIRED')
    if len(set(planned_keys)) != len(planned_keys):
        raise ValueError('DUPLICATE_PLANNED_SAMPLE')
    tables = []
    for rows in (baseline,candidate):
        table={}
        for row in rows:
            key=_key(row)
            if key in table:
                raise ValueError('DUPLICATE_OBSERVED_SAMPLE')
            table[key]=dict(row)
        tables.append(table)
    changes = set(declared_changes)
    if changes - set(_INVARIANTS + _OPTIONAL_FACTORS):
        raise ValueError('UNKNOWN_DECLARED_FACTOR')
    pairs=[]
    for planned in planned_sample_manifest:
        key=_key(planned); left=tables[0].get(key); right=tables[1].get(key)
        reasons=[]
        if left is None: reasons.append('BASELINE_MISSING')
        if right is None: reasons.append('CANDIDATE_MISSING')
        if not _known_fingerprint(planned.get('code_hash')):
            reasons.append('PLANNED_CODE_FINGERPRINT_UNKNOWN')
        for label,row in (('BASELINE',left),('CANDIDATE',right)):
            if row is not None and (row.get('status')!='MEASURED' or not isinstance(row.get('value'),(float,int)) or not math.isfinite(row['value'])):
                reasons.append(label+'_FAILED_OR_UNMEASURED')
        if left is not None and right is not None:
            fields = _INVARIANTS + tuple(f for f in _OPTIONAL_FACTORS if f in changes or any(f in r for r in (planned,left,right)))
            for field in fields:
                if field in planned and left.get(field)!=planned[field]:
                    reasons.append('PLANNED_MISMATCH:'+field)
                if left.get(field) is None or right.get(field) is None or (field.endswith('_hash') and not all(_known_fingerprint(r.get(field)) for r in (left,right))):
                    reasons.append('UNKNOWN:'+field)
                elif field not in changes and left[field]!=right[field]:
                    reasons.append('MISMATCH:'+field)
            for row in (left,right):
                if row.get('response_request_hash') is not None and row['response_request_hash']!=row.get('request_hash'):
                    reasons.append('OLD_RESPONSE_NEW_REQUEST')
            if any(f in changes for f in ('model','prompt_hash')) and right.get('execution_kind')!='FRESH':
                reasons.append('REPLAY_CANNOT_PROVE_FRESH_SEMANTIC_GAIN')
            if any(f in changes for f in ('model','prompt_hash')) and right.get('execution_kind')=='FRESH':
                if not right.get('request_hash') or right.get('response_request_hash')!=right.get('request_hash') or not right.get('provider_attempt_id'):
                    reasons.append('FRESH_PHYSICAL_REQUEST_LINEAGE_NOT_PROVEN')
            reasons.extend(_changed_execution_reasons(right, changes))
        pairs.append({'key':dict(zip(_KEY,key)),'baseline':left,'candidate':right,
            'status':'NOT_COMPARABLE' if reasons else 'COMPARABLE','reasons':sorted(set(reasons)),
            'delta':None if reasons else float(right['value'])-float(left['value'])})
    comparable=[p for p in pairs if p['status']=='COMPARABLE']
    extras=[{'side':side,'sample':row} for side,table in zip(('baseline','candidate'),tables)
            for key,row in table.items() if key not in set(planned_keys)]
    return {'planned_count':len(pairs),'comparable_count':len(comparable),'pairs':pairs,'unplanned':extras,
            'paired_subset_mean_delta':sum(p['delta'] for p in comparable)/len(comparable) if comparable else None,
            'overall_delta':sum(p['delta'] for p in comparable)/len(comparable) if len(comparable)==len(pairs) and pairs and not extras else None,
            'status':'COMPARABLE' if len(comparable)==len(pairs) and pairs and not extras else 'NOT_COMPARABLE',
            'coverage':{side:dict(Counter(row.get('status','UNKNOWN') if row else 'MISSING' for row in (p[side] for p in pairs)))
                        for side in ('baseline','candidate')}}

def baseline_rows(comparison,reports,metrics,*,baseline_experiment_id):
    """Use the existing presentation only after complete paired verification."""
    if comparison.get('status')!='COMPARABLE' or comparison.get('unplanned'):
        raise ValueError('NOT_COMPARABLE')
    expected=comparison['planned_count']
    if any(r.sample_count!=expected or r.failure_count for r in reports):
        raise ValueError('REPORT_COVERAGE_MISMATCH')
    if len(reports)!=2 or len(metrics)!=1:
        raise ValueError('EXACT_PAIRED_REPORTS_AND_SINGLE_METRIC_REQUIRED')
    metric=list(metrics)[0]
    baseline=next((r for r in reports if r.experiment_id==baseline_experiment_id),None)
    candidate=next((r for r in reports if r.experiment_id!=baseline_experiment_id),None)
    if baseline is None or candidate is None:
        raise ValueError('EXACT_BASELINE_AND_CANDIDATE_REQUIRED')
    import math
    for side,report in [('baseline',baseline),('candidate',candidate)]:
        samples=getattr(report,'samples',None)
        ids=[p['key']['sample_id'] for p in comparison['pairs']]
        if samples is None or len(set(ids))!=len(ids) or len(samples)!=expected:
            raise ValueError('EXACT_RAW_SAMPLE_MEMBERSHIP_REQUIRED')
        observed={s.sample_id:s for s in samples}
        if len(observed)!=expected or set(observed)!=set(ids):
            raise ValueError('RAW_SAMPLE_IDENTITY_MISMATCH')
        for pair in comparison['pairs']:
            sample=observed[pair['key']['sample_id']]
            if (getattr(sample,'metadata',{}) or {}).get('full_chain_pair_identity')!=pair['key']:
                raise ValueError('RAW_SAMPLE_SCOPE_OR_REPLICATE_UNPROVEN')
            if not sample.success or sample.metrics.get(metric)!=pair[side]['value']:
                raise ValueError('RAW_SAMPLE_METRIC_MISMATCH')
        mean=sum(p[side]['value'] for p in comparison['pairs'])/expected
        value=report.aggregate_metrics.get(metric)
        if value is None or not math.isclose(mean,value,rel_tol=1e-12,abs_tol=1e-12):
            raise ValueError('UNVERIFIED_AGGREGATE_METRIC')
    from eval.rag.strategy.baseline import BaselineManager
    return BaselineManager(baseline_experiment_id).rows(reports,list(metrics))
