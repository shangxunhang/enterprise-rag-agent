"""JSON and Markdown are projections of one observational result."""
from __future__ import annotations
import json
from pathlib import Path
from .contracts import EvaluationReport, EvaluationRunIdentity

def build_report(identity: EvaluationRunIdentity, batches, comparison=None, readiness_evidence=None,
                 *, catalog=None, reference_view=None):
    stages=tuple(s for b in batches for s in b.stages)
    edges=tuple(e for b in batches for e in b.edges)
    metrics=tuple(m for b in batches for m in b.metrics)
    findings=tuple(f for b in batches for f in b.findings)
    from .findings import observed_edge_failures
    findings += tuple(observed_edge_failures(edges,subject_id=identity.subject_id,stages=stages))
    limits=tuple(l for b in batches for l in b.limitations)
    unit_ids=sorted({s.unit_id for s in stages}); edge_ids=sorted({e.edge_id for e in edges})
    expected_units=[u for d in (catalog or {}).get('domains',[]) for u in d['units']]
    expected_edges=[e for d in (catalog or {}).get('domains',[]) for e in d['owned_edges']]
    coverage={'stage_instances':len(stages),'unit_ids':unit_ids,'edge_ids':edge_ids,
        'expected_unit_count':len(expected_units),'expected_edge_count':len(expected_edges),
        'missing_units':sorted(set(expected_units)-set(unit_ids)),
        'missing_edges':sorted(set(expected_edges)-set(edge_ids)),
        'measurement_counts':{state:sum(s.measurement_state==state for s in stages)
            for state in ('MEASURED','NOT_RUN','N/A','UNMEASURED','TECHNICAL_FAILURE')},
        'read_status':'PARTIAL' if limits else 'COMPLETE'}
    review=dict(reference_view.review_status) if reference_view else {f'G{i}':'PROVISIONAL' for i in range(1,6)}
    # This implementation phase cannot establish diagnostic readiness. Receipts
    # supplied later are displayed, never automatically promoted into authority.
    limits += tuple(reference_view.limitations if reference_view else ('INDEPENDENT_REFERENCES_NOT_REVIEWED',))
    limits += ('T15_PENDING_RUN_AUTHORIZATION',)
    release={'findings':[f.code for f in findings if (f.unit_id and f.unit_id.startswith('E')) or f.edge_id in ('X21','X27','X28')],
        'original_axes':[{'unit_id':s.unit_id,'execution':s.observed_execution_status,
            'quality':s.observed_quality_status,'action':s.observed_control_action,
            'measurement_state':s.measurement_state} for s in stages if s.unit_id.startswith('E')]}
    from .domains.runtime_reliability import compute_timeline,timeline_spans_from_stages
    # Use only completed, clock-bound spans. CPU scopes overlap and are not
    # partitions of workflow wall time; compute_timeline retains this distinction.
    spans=timeline_spans_from_stages(stages)
    windows=[r for r in spans if r.get('category')=='WORKFLOW_WALL' and r.get('clock_domain') is not None
        and r.get('boundary_measurement_state')=='MEASURED'
        and isinstance(r.get('begin_ns'),int) and isinstance(r.get('end_ns'),int) and r['end_ns']>=r['begin_ns']]
    window=windows[0] if len(windows)==1 else None
    performance={'metrics':[m.metric_id for m in metrics if m.unit_id in ('F01','F06')],
        'findings':[f.code for f in findings if 'INTERVAL' in f.code or 'USAGE' in f.code],
        'timeline':compute_timeline(spans,window=window),
        'workflow_wall_ns':window['end_ns']-window['begin_ns'] if window else None,
        'workflow_wall_state':'MEASURED' if window else 'UNMEASURED',
        'clock_alignment':'NO_CROSS_PROCESS_SUBTRACTION',
        'cost_state':'UNMEASURED_WITHOUT_FROZEN_PRICE_VERSION'}
    return EvaluationReport(identity,coverage,stages,edges,metrics,release,performance,comparison or {},
        findings,tuple((readiness_evidence or {}).get('badcases',[])),limits,
        reference_review=review)

def render_markdown(report: EvaluationReport) -> str:
    data=report.to_dict()
    lines=['# Full-Chain Evaluation Report','',f"Evaluation: `{report.identity.evaluation_id}`",
           f"Subject: `{report.identity.subject_id}`",f"Mode: `{report.identity.mode}`",
           f"DIAGNOSTIC_READINESS: `{report.diagnostic_readiness}`",'']
    sections=(('Execution coverage','execution_coverage'),('Stage quality','stage_quality'),
        ('Edge fidelity','edge_fidelity'),('Release / close','release_close'),
        ('Performance / cost','performance_cost'),('Comparison','comparison'),
        ('Metrics','metrics'),('Findings','findings'),('Badcases','badcases'),
        ('Reference review','reference_review'),('Limitations','limitations'))
    for title,key in sections:
        value=data[key]
        if key=='stage_quality':
            from collections import Counter
            value={'instances':len(value),'by_unit':dict(Counter(row['unit_id'] for row in value)),
                'by_measurement':dict(Counter(row['measurement_state'] for row in value)),
                'exact_machine_records':'evaluation_report.json#/stage_quality',
                'material':'Original source refs and hashes remain in each machine record; summary is not an empty pool'}
        elif key=='performance_cost':
            value={**value,'timeline':{k:v for k,v in value.get('timeline',{}).items() if k!='spans'}}
            value['timeline']['span_count']=len(data[key].get('timeline',{}).get('spans',[]))
            value['timeline']['exact_span_records']='evaluation_report.json#/performance_cost/timeline/spans'
        lines += ['## '+title,'','```json',json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2),'```','']
    return '\n'.join(lines)

def emit_report(report: EvaluationReport,new_output_root, *, source_roots=()):
    root=Path(new_output_root).absolute()
    for part in (root,*root.parents):
        if part.exists() and (part.is_symlink() or getattr(part.stat(),'st_file_attributes',0)&0x400):
            raise ValueError('REPORT_REPARSE_POINT_REJECTED')
    root=root.resolve()
    for source in source_roots:
        source=Path(source).resolve(strict=True)
        if root==source or root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError('OUTPUT_SOURCE_OVERLAP')
    root.mkdir(parents=True,exist_ok=False)
    json_path=root/'evaluation_report.json'; md_path=root/'evaluation_report.md'
    json_path.write_text(json.dumps(report.to_dict(),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False),encoding='utf-8')
    md_path.write_text(render_markdown(report),encoding='utf-8')
    return json_path,md_path
