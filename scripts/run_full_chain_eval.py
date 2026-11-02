"""Explicit full-chain entry point; default observation never constructs owners."""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from eval.full_chain.contracts import EvaluationRunIdentity
from eval.full_chain.execution import authorize_mode, AuthorizationError
from eval.full_chain.references import load_references
from eval.full_chain.report import emit_report
from eval.full_chain.runner import observe
from eval.full_chain.snapshot import load_source_snapshot

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',nargs='?',default='observe',choices=('observe','isolated-replay','fresh-benchmark'))
    parser.add_argument('--source-manifest',required=True)
    parser.add_argument('--allowed-root',action='append',required=True)
    parser.add_argument('--references')
    parser.add_argument('--reference-root',action='append',default=[])
    parser.add_argument('--output',required=True)
    parser.add_argument('--evaluation-id',required=True)
    parser.add_argument('--subject-id',default='evaluation-local')
    parser.add_argument('--authorization')
    parser.add_argument('--scenario',choices=('fixed-gateway',))
    parser.add_argument('--capture-output')
    parser.add_argument('--snapshot-output')
    parser.add_argument('--max-source-bytes',type=int,default=32*1024*1024)
    parser.add_argument('--max-source-records',type=int,default=100000)
    args=parser.parse_args(argv)
    authorization=json.loads(Path(args.authorization).read_text(encoding='utf-8')) if args.authorization else None
    try:
        mode=authorize_mode(args.mode,authorization)
        if mode=='ISOLATED_REPLAY':
            if args.scenario!='fixed-gateway' or not args.capture_output:
                raise AuthorizationError('EXPLICIT_TRUSTED_SCENARIO_REQUIRED')
            from eval.full_chain.snapshot import safe_path
            from eval.full_chain.execution import fixed_gateway_replay
            request_material=json.loads(safe_path(args.source_manifest,args.allowed_root).read_text(encoding='utf-8'))
            result=fixed_gateway_replay(request_material,capture_root=args.capture_output,
                allowed_root=Path(args.capture_output).parent)
            print(json.dumps({'status':'ISOLATED_REPLAY_COMPLETED',**result}))
            return 0
        if mode!='OBSERVE':
            raise AuthorizationError('EXPLICIT_TRUSTED_SCENARIO_REQUIRED_USE_EXECUTION_API')
        references=load_references(args.references,args.reference_root or [Path(args.references).parent]) if args.references else None
        if not 0<args.max_source_bytes<=512*1024*1024:
            raise ValueError('INVALID_SOURCE_BYTE_CAP')
        if not 0<args.max_source_records<=200000:
            raise ValueError('INVALID_SOURCE_RECORD_CAP')
        snapshot=load_source_snapshot(args.source_manifest,args.allowed_root,output_root=args.snapshot_output,max_bytes=args.max_source_bytes,max_records=args.max_source_records)
        identity=EvaluationRunIdentity(args.evaluation_id,args.subject_id,source_snapshot_id=snapshot.snapshot_id)
        report=observe(snapshot,identity,reference_view=references)
        paths=emit_report(report,args.output,source_roots=args.allowed_root)
        print(json.dumps({'status':'REPORT_GENERATED','paths':[str(p) for p in paths],
            'read_status':report.execution_coverage['read_status'],'diagnostic_readiness':report.diagnostic_readiness}))
        return 0
    except (ValueError,OSError) as error:
        print(json.dumps({'status':'EVALUATION_TOOL_FAILURE','reason':str(error)}))
        return 2

if __name__=='__main__':
    raise SystemExit(main())
