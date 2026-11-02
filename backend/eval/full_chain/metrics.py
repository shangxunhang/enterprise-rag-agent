"""Pure checks with guarded reuse of the existing retrieval MetricRegistry."""
from collections import Counter
from typing import Any, Iterable, Mapping
from .contracts import MetricResult


def match_sets(predictions: Iterable[str], expected: Iterable[str] | None, *, reviewed=False,
               synthetic=False, unit_id='unknown', scope='unknown') -> list[MetricResult]:
    if expected is None or not (reviewed or synthetic):
        return [MetricResult('precision_recall', unit_id, scope, reason='NO_VALID_INDEPENDENT_REFERENCE')]
    predicted, gold = Counter(predictions), Counter(expected)
    # Reference keys describe independent obligations; duplicate predictions cannot
    # consume the same obligation twice.
    gold_keys = set(gold)
    tp = sum(1 for key in gold_keys if predicted[key])
    fp = sum(predicted.values()) - tp
    fn = len(gold_keys) - tp
    values = [('precision', tp, tp+fp), ('recall', tp, tp+fn), ('f1', 2*tp, 2*tp+fp+fn)]
    result = [MetricResult(name, unit_id, scope, n/d if d else None, n, d,
        'MEASURED' if d else 'N/A', reason='' if d else 'REVIEWED_EMPTY_DENOMINATOR',
        input_completeness='COMPLETE', representation='SYNTHETIC_MECHANISM' if synthetic else 'ACTUAL_OBSERVATION')
        for name, n, d in values]
    result.append(MetricResult('false_positive_count', unit_id, scope, fp, fp, len(gold_keys),
        'MEASURED', assertion_result=(fp == 0), input_completeness='COMPLETE'))
    return result


def ranked_metrics(results: list[dict[str, Any]], gold_ids: list[str] | None, *, grain='child',
                   top_k=5, reviewed=False, synthetic=False, judged_complete=True,
                   pool_complete=True, unit_id='C04', scope='unknown') -> list[MetricResult]:
    names = ('hit_at_k','recall_at_k','mrr','ndcg_at_k')
    if grain not in ('child','parent','doc') or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError('FIXED_GRAIN_AND_POSITIVE_K_REQUIRED')
    if gold_ids is None or not (reviewed or synthetic) or not judged_complete or not pool_complete:
        return [MetricResult(n, unit_id, scope, reason='GOLD_OR_POOL_NOT_COMPLETE') for n in names]
    if not gold_ids:
        return [MetricResult('recall_at_k',unit_id,scope,measurement_state='N/A',reason='REVIEWED_EMPTY_GOLD'),
                MetricResult('false_positive_count',unit_id,scope,len(results[:top_k]),measurement_state='MEASURED')]
    from eval.rag.strategy.metrics import MetricContext, build_default_metric_registry
    from eval.rag.strategy.schemas import RAGEvalSample
    field = {'child':'expected_child_chunk_ids','parent':'expected_parent_chunk_ids','doc':'expected_doc_ids'}[grain]
    sample = RAGEvalSample(sample_id='evaluation-view',query='frozen',**{field: gold_ids})
    context = MetricContext(sample,{'retrieval_results':results},0.0,top_k)
    return [MetricResult(item.name,unit_id,scope,item.compute(context),measurement_state='MEASURED',
                version='legacy_rag_strategy_v1',calculator_ref='eval.rag.strategy.metrics.'+item.name,
                input_completeness='COMPLETE',representation='SYNTHETIC_MECHANISM' if synthetic else 'ACTUAL_OBSERVATION')
            for item in build_default_metric_registry().require(list(names))]


def check_preservation(source: Mapping[str, Any] | None, target: Mapping[str, Any] | None,
                       fields: Iterable[str], *, unit_id='unknown', scope='unknown') -> MetricResult:
    fields = tuple(fields)
    if source is None or target is None or any(k not in source or k not in target for k in fields):
        return MetricResult('field_preservation',unit_id,scope,reason='MISSING_EDGE_ENDPOINT_OR_FIELD')
    kept = sum(source[k] == target[k] for k in fields)
    return MetricResult('field_preservation',unit_id,scope,kept/len(fields) if fields else None,
        kept,len(fields),'MEASURED' if fields else 'N/A',kept == len(fields),input_completeness='COMPLETE')
