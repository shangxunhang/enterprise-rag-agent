"""Explicitly bound independent references, used only after runtime extraction."""
from dataclasses import replace
from .contracts import Finding,MetricResult,ObservationBatch
from .metrics import match_sets,ranked_metrics
from .domains.generation_claim import check_provenance_chain

def _field_path(value, path):
    """Resolve only bounded explicit field paths, with list-member projection."""
    if not isinstance(path,str):
        raise ValueError('INVALID_NATIVE_REFERENCE_FIELD_PATH')
    parts=path.split('.')
    if len(parts)>12 or any(not part or (part!='*' and not part.replace('_','').isalnum()) for part in parts):
        raise ValueError('INVALID_NATIVE_REFERENCE_FIELD_PATH')
    def walk(node, remaining):
        if not remaining:
            return node
        part,*tail=remaining
        if part=='*':
            if not isinstance(node,(list,tuple)):
                raise KeyError(path)
            return [walk(item,tail) for item in node]
        if not isinstance(node,dict) or part not in node:
            raise KeyError(path)
        return walk(node[part],tail)
    return walk(value,parts)


def _native_reference_rows(stages, references, batch):
    """Use review-bound exact native observations; never guess a Gold binding."""
    allowed={'G1':{'retained_atoms'},'G2':{'results','grain','top_k'},'G3':{'claim_ids'},
        'G4':{'binding','support','result'},'G5':{'requirements','citation_refs'}}
    required={**allowed,'G5':{'requirements'}}
    rows=[]
    def unavailable(ref, reason):
        batch.limitations.append(reason+':'+ref['reference_id'])
        unit={'G1':'A03','G2':'C12','G3':'D07','G4':'D10','G5':'E08'}[ref['family']]
        batch.metrics.append(MetricResult(ref['family']+'_reference_quality',unit,reason=reason,reference_ref=ref['reference_id']))
    for ref in references.values():
        binding=ref.get('match_rules',{}).get('native_observation')
        if binding is None:
            continue
        if not isinstance(binding,dict) or not binding.get('unit_id') or not binding.get('source_raw_sha256') or not binding.get('operation_id'):
            unavailable(ref,'NATIVE_REFERENCE_EXACT_BINDING_REQUIRED'); continue
        matches=[s for s in stages if s.unit_id==binding['unit_id'] and s.operation_id==binding['operation_id']
            and all(getattr(s,k)==binding[k] for k in ('query','round','scope') if k in binding)
            and any(e.raw_sha256==binding['source_raw_sha256'] for e in s.evidence_refs)]
        if len(matches)!=1:
            unavailable(ref,'NATIVE_REFERENCE_BINDING_MISSING_OR_AMBIGUOUS'); continue
        stage=matches[0]
        if ref['family']=='G5' and 'source_scope' in stage.payload and stage.payload['source_scope']!=stage.scope:
            unavailable(ref,'REFERENCE_SCOPE_MISSING_OR_CONFLICTING'); continue
        paths=binding.get('field_paths',{})
        mandatory=required[ref['family']]
        if ref['family']=='G5' and ref.get('labels',{}).get('expected_citation_refs') is not None:
            mandatory=mandatory|{'citation_refs'}
        if not isinstance(paths,dict) or not mandatory<=set(paths) or set(paths)-allowed[ref['family']]:
            unavailable(ref,'NATIVE_REFERENCE_FIELD_MAP_REQUIRED'); continue
        complete=stage.measurement_state=='MEASURED' and bool(stage.evidence_refs) and all(e.integrity=='VALID' and e.completeness=='COMPLETE' for e in stage.evidence_refs)
        row={'reference_id':ref['reference_id'],'source_sha256':ref['source_sha256'],
             'input_completeness':'COMPLETE' if complete else 'PARTIAL'}
        if ref['family']=='G5':
            row['scope']=stage.scope
        try:
            row.update({name:_field_path(dict(stage.payload),path) for name,path in paths.items()})
        except (KeyError,ValueError,TypeError):
            row['input_completeness']='PARTIAL'
            batch.limitations.append('NATIVE_REFERENCE_FIELD_NOT_RECORDED:'+ref['reference_id'])
        if row['input_completeness']=='COMPLETE':
            family=ref['family']
            if family=='G2':
                values=row.get('results')
                valid=(isinstance(values,(list,tuple)) and all(isinstance(v,dict) and
                    isinstance(v.get('child_chunk_id') or v.get('chunk_id'),str) and
                    bool(v.get('child_chunk_id') or v.get('chunk_id')) for v in values)
                    and row.get('grain')=='child' and type(row.get('top_k')) is int and row['top_k']>0)
            elif family=='G4':
                valid=all(isinstance(row.get(k),dict) for k in mandatory)
            else:
                valid=all(isinstance(row.get(k),(list,tuple)) and all(isinstance(v,str) for v in row[k]) for k in mandatory)
            if not valid:
                row['input_completeness']='PARTIAL'
                batch.limitations.append('NATIVE_REFERENCE_MATERIAL_NOT_MEASURABLE:'+ref['reference_id'])
        rows.append(row)
    return rows


def evaluate_references(snapshot,reference_view,*,subject_id='unknown',stages=()):
    batch=ObservationBatch('references')
    if reference_view is None: return batch
    references={r['reference_id']:r for r in reference_view.records}
    rows=list(snapshot.records('reference_observations'))+_native_reference_rows(stages,references,batch)
    for row in rows:
        ref=references.get(row.get('reference_id'))
        if ref is None:
            batch.limitations.append('UNKNOWN_REFERENCE_BINDING'); continue
        family=ref['family']; unit={'G1':'A03','G2':'C12','G3':'D07','G4':'D10','G5':'E08'}[family]
        status=ref['effective_review_status']; synthetic=status=='SYNTHETIC_MECHANISM'
        reviewed=status=='REVIEWED_BY_INDEPENDENT_HUMAN'
        if row.get('source_sha256')!=ref['source_sha256'] or not (synthetic or reviewed):
            batch.metrics.append(MetricResult(family+'_reference_quality',unit,reason='REFERENCE_NOT_REVIEWED_OR_SOURCE_MISMATCH',reference_ref=ref['reference_id']))
            continue
        if row.get('input_completeness')!='COMPLETE':
            batch.metrics.append(MetricResult(family+'_reference_quality',unit,reason='INCOMPLETE_REFERENCE_OBSERVATION',reference_ref=ref['reference_id']))
            continue
        labels=ref['labels']; metrics=[]
        if family=='G1':
            metrics=match_sets(row.get('retained_atoms',[]),labels.get('required_atoms'),reviewed=reviewed,synthetic=synthetic,unit_id=unit)
        elif family=='G2':
            results=row.get('results')
            gold=labels.get('relevant_allowed')
            if row.get('grain')!='child' or type(row.get('top_k')) is not int or row['top_k']<=0:
                metrics=[MetricResult('retrieval_use',unit,reason='GRAIN_OR_K_NOT_FROZEN')]
            elif not isinstance(results,(list,tuple)) or any(not isinstance(r,dict) or
                not isinstance(r.get('child_chunk_id') or r.get('chunk_id'),str) or
                not (r.get('child_chunk_id') or r.get('chunk_id')) for r in results):
                metrics=[MetricResult('retrieval_use',unit,reason='REFERENCE_CANDIDATE_IDENTITY_MISSING')]
            elif gold is not None and (not isinstance(gold,(list,tuple)) or
                any(not isinstance(v,str) or not v for v in gold)):
                metrics=[MetricResult('retrieval_use',unit,reason='REFERENCE_RELEVANT_IDS_INVALID')]
            else:
                metrics=ranked_metrics(results,gold,grain='child',top_k=row['top_k'],reviewed=reviewed,synthetic=synthetic,unit_id=unit)
                forbidden=set(labels.get('relevant_forbidden',[]))
                count=sum((r.get('child_chunk_id') or r.get('chunk_id')) in forbidden for r in results)
                metrics.append(MetricResult('forbidden_use_count',unit,value=count,measurement_state='MEASURED',assertion_result=count==0))
        elif family=='G3':
            metrics=match_sets(row.get('claim_ids',[]),labels.get('expected_claims'),reviewed=reviewed,synthetic=synthetic,unit_id=unit)
        elif family=='G4':
            checks=check_provenance_chain(row.get('binding',{}),row.get('support',{}),row.get('result',{}),
                expected_binding=labels.get('binding'),expected_support={'relation':labels.get('relation','SUPPORTED'),'supporting_refs':labels.get('supporting_refs',[])},
                expected_verdict=labels.get('verdict'))
            for kind,value in checks.items():
                state=value['state']; verdicts=[v for k,v in value.items() if k in ('correct','relation_correct','members_correct','positive_basis_valid','verdict_correct') and v is not None]
                metrics.append(MetricResult('provenance_'+kind,unit,value=value,measurement_state=state,assertion_result=all(verdicts) if verdicts else None))
        else:
            scope=row.get('scope')
            binding=ref.get('match_rules',{}).get('native_observation')
            other_scopes=[row['source_scope']] if 'source_scope' in row else []
            if binding is not None:
                if not isinstance(binding,dict):
                    other_scopes.append(None)
                elif 'scope' in binding:
                    other_scopes.append(binding['scope'])
            if scope not in ('SECTION','DOCUMENT') or any(s!=scope for s in other_scopes):
                metrics=[MetricResult('G5_reference_quality',unit,reason='REFERENCE_SCOPE_MISSING_OR_CONFLICTING')]
            else:
                metrics=match_sets(row.get('requirements',[]),labels.get('requirements'),reviewed=reviewed,synthetic=synthetic,unit_id=unit,scope=scope)
                if row.get('citation_refs') is not None and labels.get('expected_citation_refs') is not None:
                    metrics += match_sets(row['citation_refs'],labels['expected_citation_refs'],reviewed=reviewed,synthetic=synthetic,unit_id='E05',scope=scope)
        for metric in metrics:
            if family=='G2' and metric.metric_id=='recall_at_k' and metric.measurement_state=='MEASURED':
                # Match the calculator's unique Gold denominator; keep raw Recall@K.
                relevant_count=len(set(labels['relevant_allowed']))
                metric=replace(metric,assertion_result=metric.value==min(row['top_k'],relevant_count)/relevant_count)
            elif metric.metric_id in ('precision','recall','f1','recall_at_k') and metric.measurement_state=='MEASURED':
                metric=replace(metric,assertion_result=metric.value==1)
            batch.metrics.append(replace(metric,reference_ref=ref['reference_id'],input_completeness='COMPLETE',representation='SYNTHETIC_MECHANISM' if synthetic else 'ACTUAL_OBSERVATION'))
            if metric.assertion_result is False:
                batch.findings.append(Finding('REFERENCE_QUALITY_FAILURE',subject_id,family+':'+metric.metric_id,unit_id=metric.unit_id,evidence_level='OBSERVED' if synthetic else 'INDEPENDENT_SEMANTIC_REVIEW'))
    return batch
