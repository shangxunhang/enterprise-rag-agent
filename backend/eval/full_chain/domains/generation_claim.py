"""Generation, binding, support and verifier observations remain separate."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
from dataclasses import asdict
from typing import Any, Mapping
from ..contracts import (StageObservation, EdgeObservation, ObservationBatch, Finding,
    MetricResult, EvaluationEvidenceRef, canonical_hash)
from ..metrics import match_sets

REPRESENTATIONS=('MODEL_ATTEMPT_REQUEST','MODEL_CLIENT_RAW_RESPONSE',
    'MODEL_GATEWAY_PROJECTED_RESPONSE','WRITER_DELIVERY_PROJECTION')
_UNITS={'W-WRITER':'D05','W-PARA':'D06','W-CLAIM':'D07','W-BIND':'D08',
        'W-SUPPORT':'D09','W-VERIFY':'D10','W-CROSS':'F04','W-BYPASS':'D11'}
_EDGES={'X10':('W-RAW','W-PARA'),'X11':('W-PARA','W-CLAIM'),
    'X12':('W-CLAIM','W-BIND'),'X13':('W-BIND','W-SUPPORT'),
    'X14':('W-SUPPORT','W-VERIFY'),'X15':('W-VERIFY','W-CROSS')}


def _physical_writer_proven(group):
    """Only the original request role establishes this Writer invocation."""
    request = group.get('representations', {}).get('MODEL_ATTEMPT_REQUEST', {})
    material = request.get('material')
    if not isinstance(material, Mapping) or material.get('model_role') != 'section_generation':
        return False
    recorded_role = request.get('model_role')
    return recorded_role is None or recorded_role == material['model_role']

def _production_records(row):
    """Recognize existing immutable owner events, retain the untouched raw row."""
    kind=row.get('event_type'); operation=row.get('operation_ref') or {}
    if isinstance(operation,dict):
        identity={'operation_id':operation.get('logical_operation_id'),
            'scope':operation.get('attempt_scope_ref','unknown')}
    else: identity={'operation_id':str(operation),'scope':'unknown'}
    base={**identity,'original_record':row,'record_origin':'PERSISTED_OWNER'}
    if kind=='claim_records_committed':
        yield 'W-CLAIM',{**base,'claim_ids':[c.get('claim_ref',{}).get('claim_id') for c in row.get('claim_records',[])],
            'coverage_accounting':row.get('coverage_accounting'), 'output_ref':row.get('result_ref'),
            'observed_quality_status':(row.get('coverage_accounting') or {}).get('accounting_state','UNKNOWN')}
        for claim in row.get('claim_records',[]):
            yield 'W-CLAIM',{**base,'operation_id':canonical_hash(claim.get('claim_ref')),
                'claim_ids':[claim.get('claim_ref',{}).get('claim_id')],
                'claim':claim,'output_ref':claim.get('claim_ref')}
    elif kind=='binding_facts_committed':
        binding=row.get('record',{})
        yield 'W-BIND',{**base,'binding':binding,'output_ref':binding.get('binding_facts_ref')}
    elif kind in ('claim_evidence_support_committed','support_committed','claim_support_committed'):
        support=row.get('record') or row.get('support') or row.get('payload',{})
        yield 'W-SUPPORT',{**base,'support':support,'output_ref':support.get('support_ref')}
    elif kind=='verification_committed':
        result=row.get('verification_result') or row.get('record') or row.get('receipt',{})
        yield 'W-VERIFY',{**base,'result':result,'output_ref':row.get('result_ref')}
    elif kind in ('verifier_candidate_rejected','verifier_correction_authorized','verifier_correction_terminated'):
        yield 'W-VERIFY',{**base,'local_correction_event':kind,'payload':row.get('payload',{}),
            'observed_control_action':'REJECTED' if kind=='verifier_candidate_rejected' else kind}
    elif kind in ('paragraph_committed','paragraph_materialized'):
        yield 'W-PARA',{**base,'paragraph':row.get('record',row.get('content',{})),
            'output_ref':row.get('result_ref')}
    elif kind=='manifest_committed' and row.get('schema_version')=='paragraph_manifest_event_v1':
        receipt=row.get('receipt',{})
        yield 'W-PARA',{**base,'receipt':receipt,'paragraph_version_refs':receipt.get('resulting_members',[]),
            'output_ref':row.get('result_ref'),'identity_quality':'ORIGINAL_OWNER_REF'}
    elif isinstance(row.get('content'),dict) and row['content'].get('schema_version')=='paragraph_content_v1' and isinstance(row.get('version_ref'),dict):
        content=row['content']; subject=content.get('source_binding',{}).get('writer_subject_ref',{})
        yield 'W-PARA',{**base,'paragraphs':[content], 'paragraph_version_ref':row['version_ref'],
            'operation_id':row['version_ref'].get('artifact_version_id'), 'run_id':subject.get('run_id'),
            'section_id':subject.get('section_id'), 'paragraph':content}

def model_representations(events, *, capture_complete=True):
    """Physical key + explicit representation only, never heuristic backfill."""
    groups=defaultdict(dict); problems=[]; conflicts=set()
    for event in events:
        kind=event.get('representation_kind') or event.get('event_type')
        if kind not in REPRESENTATIONS and kind!='MODEL_CLIENT_EXCEPTION': continue
        material=event.get('material')
        call=event.get('model_call_id') or (material or {}).get('model_call_id')
        extra=(material or {}).get('extra') or {}
        attempt=event.get('provider_attempt_id') or extra.get('provider_attempt_id')
        ordinal=event.get('provider_attempt_ordinal') or extra.get('provider_attempt_ordinal')
        if not call or (not attempt and ordinal is None):
            problems.append('UNPROVEN_PHYSICAL_IDENTITY'); continue
        key=(call,attempt or 'ordinal:'+str(ordinal))
        if kind in groups[key] and groups[key][kind]!=event:
            problems.append('CONFLICTING_REPRESENTATION:'+kind)
            conflicts.add(key)
        groups[key][kind]=dict(event)
    result=[]
    for key,layers in sorted(groups.items()):
        raw=layers.get('MODEL_CLIENT_RAW_RESPONSE')
        exception=layers.get('MODEL_CLIENT_EXCEPTION')
        required=('MODEL_ATTEMPT_REQUEST','MODEL_GATEWAY_PROJECTED_RESPONSE')
        missing=[kind for kind in required if kind not in layers or not layers[kind].get('material')]
        if raw is None and exception is None: missing.append('MODEL_CLIENT_RAW_RESPONSE')
        if raw is not None and not raw.get('material'): missing.append('RAW_MATERIAL_NOT_RESOLVABLE')
        if raw and exception: problems.append('RAW_AND_EXCEPTION_FOR_SAME_PHYSICAL_ATTEMPT')
        if key in conflicts or (raw and exception): missing.append('CONFLICTING_PHYSICAL_REPRESENTATION')
        complete=capture_complete and not missing and all(e.get('completeness')!='CAPTURE_INCOMPLETE' for e in layers.values())
        result.append({'model_call_id':key[0],'provider_attempt_id':key[1],'representations':layers,
            'raw_state':'NO_RAW_RESPONSE' if exception and not raw else 'MEASURED' if raw and complete else 'UNMEASURED',
            'completeness':'COMPLETE' if complete else 'CAPTURE_INCOMPLETE','missing':missing})
    return {'physical_attempts':result,'limitations':problems,
        'completeness':'COMPLETE' if capture_complete and not problems and all(g['completeness']=='COMPLETE' for g in result) else 'CAPTURE_INCOMPLETE'}

def check_paragraph_projection(writer, paragraphs, *, allowed_text=None):
    if writer is None or paragraphs is None: return {'state':'UNMEASURED','reason':'MISSING_WRITER_OR_PARAGRAPHS'}
    text=writer.get('content') if isinstance(writer,dict) else writer
    expected=text if allowed_text is None else allowed_text
    bodies=[p.get('text',p.get('content','')) for p in paragraphs]
    actual='\n\n'.join(bodies)
    unique=len({canonical_hash(p) for p in paragraphs})==len(paragraphs)
    return {'state':'MEASURED','preserved':actual==expected,'duplicates':not unique,'actual':actual}

def check_binding_correctness(binding, expected):
    fields=('project_id','scope','source_ref','version','container_ref','time','unit')
    if expected is None: return {'state':'UNMEASURED','reason':'NO_INDEPENDENT_BINDING_REFERENCE'}
    if any(k not in binding or k not in expected for k in fields):
        return {'state':'UNMEASURED','reason':'MISSING_BINDING_FIELD'}
    wrong=[k for k in fields if binding[k]!=expected[k]]
    return {'state':'MEASURED','correct':not wrong,'mismatched_fields':wrong}

def check_support_correctness(support, expected):
    if expected is None: return {'state':'UNMEASURED','reason':'NO_INDEPENDENT_SUPPORT_REFERENCE'}
    if 'relation' not in support or 'supporting_refs' not in support:
        return {'state':'TECHNICAL_FAILURE','reason':'MISSING_SUPPORT_OUTPUT'}
    actual=set(support['supporting_refs']); allowed=expected.get('allowed_support_sets',[expected.get('supporting_refs',[])])
    return {'state':'MEASURED','relation_correct':support['relation']==expected['relation'],
            'members_correct':any(actual==set(s) for s in allowed)}

def check_verifier_correctness(result, *, expected_verdict=None, supporting_refs=(),
                               binding_allowed_refs=(), technical_failure=False):
    if technical_failure or result is None:
        return {'state':'TECHNICAL_FAILURE' if technical_failure else 'UNMEASURED','reason':'NO_SEMANTIC_OUTPUT'}
    if not result.get('verdict') or not isinstance(result.get('positive_basis_refs'),(list,tuple)):
        return {'state':'UNMEASURED','reason':'MISSING_VERDICT_OR_ORIGINAL_POSITIVE_BASIS'}
    basis=result.get('positive_basis_refs',[]); verdict=result.get('verdict')
    positive=verdict=='VERIFIED'
    invalid=[r for r in basis if r not in set(supporting_refs) or r not in set(binding_allowed_refs)] if positive else []
    return {'state':'MEASURED','positive_basis_valid':not invalid and (bool(basis) if positive else True),'invalid_positive_basis':invalid,
        'verdict_correct':None if expected_verdict is None else verdict==expected_verdict,
        'false_verified':None if expected_verdict is None else positive and expected_verdict!='VERIFIED',
        'original_positive_basis_refs':list(basis)}

def check_local_correction_chain(records):
    roots=defaultdict(list)
    for row in records: roots[row.get('root_ref')].append(row)
    failures=[]
    for root,rows in roots.items():
        if root is None: failures.append('MISSING_ORIGINAL_ROOT'); continue
        children=[r for r in rows if r.get('correction_ordinal') is not None]
        slots={(r.get('slot_ref'),r.get('correction_ordinal')) for r in children}
        if any(r.get('correction_ordinal')!=1 for r in children): failures.append('CORRECTION_ORDINAL_OUTSIDE_ONE_SLOT')
        if len(slots)>1: failures.append('CORRECTION_SLOT_IDENTITY_RESET')
        if any(r.get('outcome')=='UNKNOWN' and r.get('redispatched') for r in rows): failures.append('UNKNOWN_BLIND_REDISPATCH')
        if sum(r.get('formal_commit',False) for r in rows)>1: failures.append('MULTIPLE_FORMAL_COMMITS')
    return {'state':'MEASURED' if records else 'UNMEASURED','correct':not failures,'findings':failures}

def check_cross_section_consumption(source, consumer):
    if source is None or consumer is None: return {'state':'UNMEASURED','reason':'MISSING_DEPENDENCY_ENDPOINT'}
    failures=[]
    if source.get('verdict')!='VERIFIED': failures.append('UNVERIFIED_SOURCE')
    if not consumer.get('accepted_dependency'): failures.append('NO_ACCEPTED_DEPENDENCY')
    if source.get('revision')!=consumer.get('source_revision'): failures.append('STALE_SOURCE_REVISION')
    if consumer.get('role') not in source.get('allowed_roles',[]): failures.append('ROLE_NOT_AUTHORIZED')
    if consumer.get('scope') not in source.get('allowed_scopes',[]): failures.append('SCOPE_NOT_AUTHORIZED')
    return {'state':'MEASURED','correct':not failures,'findings':failures,
            'memory_enabled':consumer.get('memory_enabled'),'selected':consumer.get('selected')}

def check_provenance_chain(binding,support,result,*,expected_binding=None,expected_support=None,expected_verdict=None):
    return {'binding':check_binding_correctness(binding,expected_binding),
        'support':check_support_correctness(support,expected_support),
        'verifier':check_verifier_correctness(result,expected_verdict=expected_verdict,
            supporting_refs=support.get('supporting_refs',[]),binding_allowed_refs=binding.get('allowed_refs',[]))}

def evaluate_generation_claims(predicted_claims,expected_claims=None,*,reviewed=False,synthetic=False):
    return match_sets(predicted_claims,expected_claims,reviewed=reviewed,synthetic=synthetic,unit_id='D07')

def check_generation_edge(edge_id,source,target,*,subject_id='unknown',physical=None):
    """Concrete obligations for each Claim-chain transition; missing is unknown."""
    if source is None or target is None:
        return EdgeObservation(edge_id,subject_id,reason='MISSING_EDGE_ENDPOINT')
    valid=None; reason='MISSING_EXACT_EDGE_OBLIGATION'
    transformation='IMMUTABLE_REF_BINDING'
    if edge_id=='X10':
        transformation='RAW_TO_GATEWAY_TO_WRITER_DELIVERY_TO_PARAGRAPH'
        if physical and physical.get('raw_state')=='MEASURED':
            layers=physical['representations']; delivered=layers.get('WRITER_DELIVERY_PROJECTION')
            projected=layers.get('MODEL_GATEWAY_PROJECTED_RESPONSE')
            if delivered and delivered.get('material') and projected and projected.get('material'):
                d,p=delivered['material'],projected['material']
                paras=target.get('paragraphs')
                if paras is not None:
                    valid=all(d.get(k)==p.get(k) for k in ('content','success','finish_reason','model_call_id'))
                    valid=valid and check_paragraph_projection(d,paras)['preserved']
                    reason='WRITER_DELIVERY_LINEAGE_AND_PARAGRAPH_PRESERVATION'
    elif edge_id=='X11':
        transformation='PARAGRAPH_TO_INDEPENDENT_EXPECTED_CLAIM_OBLIGATIONS'
        expected=source.get('expected_claim_ids'); actual=target.get('claim_ids')
        if expected is not None and actual is not None:
            valid=set(expected)==set(actual) and len(actual)==len(set(actual))
            reason='CLAIM_COVERAGE_ACCOUNTING_DOES_NOT_REPLACE_EXPECTED_CLAIMS'
        elif source.get('paragraph_version_refs') is not None and isinstance(target.get('coverage_accounting'),dict):
            coverage=target['coverage_accounting']; refs=source['paragraph_version_refs']
            original=target.get('original_record',{}); claims=original.get('claim_records')
            members=coverage.get('paragraph_version_refs'); units=coverage.get('coverage_units')
            if isinstance(refs,list) and isinstance(members,list) and isinstance(units,list) and isinstance(claims,list):
                source_members={canonical_hash(ref) for ref in refs}
                occurrences=[ref for unit in units for ref in unit.get('claim_occurrence_refs',[])]
                claim_occurrences=[claim.get('claim_occurrence_ref') for claim in claims]
                valid=(len(refs)==len(source_members) and len(members)==len({canonical_hash(ref) for ref in members})
                    and source_members=={canonical_hash(ref) for ref in members}
                    and all(canonical_hash(unit.get('paragraph_version_ref')) in source_members for unit in units)
                    and {canonical_hash(ref) for ref in occurrences}=={canonical_hash(ref) for ref in claim_occurrences}
                    and len(occurrences)==len(claim_occurrences)
                    and all(canonical_hash(claim.get('paragraph_version_ref')) in source_members for claim in claims))
                reason='NATIVE_PARAGRAPH_CLAIM_COVERAGE_STRUCTURAL_LINEAGE'
    elif edge_id=='X12':
        claim=source.get('claim'); binding=target.get('binding')
        if claim and binding:
            fields=('claim_ref','project_id','scope','source_ref','version','time','unit')
            if all(k in claim and k in binding for k in fields):
                valid=all(claim[k]==binding[k] for k in fields); reason='CLAIM_BINDING_IDENTITY'
            elif claim.get('schema_version')=='claim_record_v1' and binding.get('schema_version')=='claim_binding_facts_record_v1':
                fields=('claim_ref','claim_occurrence_ref','paragraph_version_ref')
                if all(isinstance(claim.get(k),dict) and isinstance(binding.get(k),dict) for k in fields):
                    valid=all(claim[k]==binding[k] for k in fields)
                    reason='NATIVE_CLAIM_BINDING_EXACT_IDENTITY'
    elif edge_id=='X13':
        binding=source.get('binding'); support=target.get('support')
        if binding and support and binding.get('binding_ref') and support.get('binding_ref') and isinstance(binding.get('allowed_refs'),list) and isinstance(support.get('supporting_refs'),list):
            valid=(binding['binding_ref']==support['binding_ref'] and
                set(support.get('supporting_refs',[]))<=set(binding.get('allowed_refs',[])))
            reason='SUPPORT_RELATION_AND_BINDING_MEMBERSHIP'
        elif binding and support and binding.get('schema_version')=='claim_binding_facts_record_v1' and support.get('schema_version')=='claim_evidence_support_record_v1':
            subject=support.get('claim_support_subject_ref') or {}
            target_set=support.get('evidence_target_set_ref') or {}
            authorized=binding.get('exact_authorized_source_refs')
            members=target_set.get('evidence_member_refs'); supporting=support.get('supporting_refs')
            claim_ref=binding.get('claim_ref') or {}
            if (claim_ref.get('claim_id') and subject.get('subject_ref') and isinstance(authorized,list)
                and all(isinstance(item,dict) and item.get('exact_source_ref') for item in authorized)
                and isinstance(members,list) and isinstance(supporting,list)):
                allowed={item['exact_source_ref'] for item in authorized}
                valid=(subject.get('authority_kind')=='SIF07_CLAIM' and subject['subject_ref']==claim_ref['claim_id']
                    and target_set.get('claim_support_subject_ref')==subject
                    and set(members)<=allowed and set(supporting)<=set(members))
                reason='NATIVE_SUPPORT_CLAIM_AND_AUTHORIZED_MEMBER_BINDING'
    elif edge_id=='X14':
        support=source.get('support'); result=target.get('result')
        if support and result and support.get('support_ref') and result.get('support_ref'):
            check=check_verifier_correctness(result,supporting_refs=support.get('supporting_refs',[]),
                binding_allowed_refs=source.get('binding_allowed_refs',support.get('supporting_refs',[])))
            if check['state']=='MEASURED' and isinstance(support.get('supporting_refs'),list):
                valid=support['support_ref']==result['support_ref'] and check['positive_basis_valid']
                reason='VERIFIER_POSITIVE_BASIS_USES_EXACT_SUPPORT'
        elif support and result and support.get('schema_version')=='claim_evidence_support_record_v1' and result.get('schema_version')=='verification_result_record_v2':
            basis=result.get('positive_basis_refs'); supporting=support.get('supporting_refs')
            subject=support.get('claim_support_subject_ref') or {}; claim=result.get('claim_ref') or {}
            if (isinstance(basis,list) and isinstance(supporting,list) and result.get('verdict')
                and isinstance(result.get('claim_evidence_support_ref'),dict) and isinstance(support.get('support_ref'),dict)
                and subject.get('subject_ref') and claim.get('claim_id')):
                valid=(result['claim_evidence_support_ref']==support['support_ref']
                    and subject['subject_ref']==claim['claim_id'])
                if result['verdict']=='VERIFIED':
                    outside=set(basis)-set(supporting)
                    # A target-provided ProjectInput basis cannot certify its
                    # own authority. An independent Writer basis is required.
                    if valid and outside and outside <= {result.get('project_input_basis_ref')}:
                        valid=None
                    else:
                        valid=valid and bool(basis) and not outside and support.get('relation')=='SUPPORTED'
                reason='NATIVE_VERIFIER_EXACT_SUPPORT_AND_POSITIVE_BASIS' if valid is not None else 'INDEPENDENT_WRITER_PROJECT_INPUT_BASIS_NOT_RECORDED'
    elif edge_id=='X15':
        verified=source.get('verified_source'); consumer=target.get('consumer')
        if verified and consumer:
            result=check_cross_section_consumption(verified,consumer)
            valid=result.get('correct'); reason=';'.join(result.get('findings',[])) or 'ACCEPTED_DEPENDENCY_CURRENT_AND_AUTHORIZED'
        elif isinstance(source.get('result'),dict) and isinstance(target.get('decision'),dict):
            verification=source['result']; decision=target['decision']; subject=target.get('invocation_subject',{})
            intrinsic=decision.get('intrinsic_claim_assessment',{}); exact=intrinsic.get('claim_ref',{})
            accepted=exact.get('accepted_source',{}); consumer_plan=exact.get('consumer_plan_binding',{})
            authorization=decision.get('authorization_evidence_binding',{}) or {}
            dependency=authorization.get('matched_dependency_ref',{}) or {}
            original=source.get('original_record',{}); revision=original.get('verification_revision',{})
            head=original.get('receipt',{}).get('resulting_claim_head',{})
            selected=target.get('selected_source_binding')
            if (verification.get('schema_version')=='verification_result_record_v2' and revision.get('revision_ref')
                and head.get('head_revision') is not None and accepted.get('verification_refs') is not None
                and isinstance(selected,dict) and verification.get('verification_result_ref')):
                upstream=accepted.get('plan_binding',{}).get('section_id')
                downstream=consumer_plan.get('section_id')
                valid=(verification.get('verdict')=='VERIFIED' and verification.get('claim_ref')==exact.get('claim_ref')
                    and verification['verification_result_ref'] in accepted['verification_refs']
                    and intrinsic.get('assessed_verification_revision_ref')==revision['revision_ref']==head.get('active_verification_revision_ref')
                    and intrinsic.get('claim_head_revision')==head['head_revision']
                    and subject.get('agent_role')==decision.get('consumer_role')=='WRITER'
                    and subject.get('execution_mode')=='NORMAL'
                    and subject.get('context_purpose')==decision.get('context_purpose')=='SECTION_GENERATION'
                    and subject.get('consumer_scope_ref')==decision.get('consumer_scope_ref')=='section:'+str(downstream)
                    and decision.get('consumer_invocation_ref',{}).get('schema_version')=='context_consumer_invocation_ref_v1'
                    and all(decision.get('consumer_invocation_ref',{}).get(k)==subject.get(k) for k in ('run_id','attempt_id','invocation_id'))
                    and accepted.get('workflow_status')=='ACCEPTED' and upstream!=downstream
                    and dependency.get('upstream_section_id')==upstream and dependency.get('downstream_section_id')==downstream
                    and dependency.get('source_ref')==accepted.get('source_ref') and dependency.get('source_sha256')==accepted.get('source_sha256')
                    and authorization.get('owning_authority_ref')==consumer_plan
                    and selected.get('exact_source_ref')==exact and selected.get('source_family')=='ACTIVE_VERIFIED_CLAIM'
                    and selected.get('run_id')==subject.get('run_id')==consumer_plan.get('plan_version_ref',{}).get('logical_ref',{}).get('run_id')
                    and decision.get('decision')=='AUTHORIZED' and all(decision.get(k) is True for k in
                        ('role_authorized','scope_authorized','purpose_authorized','cross_section_authorized')))
                reason='NATIVE_VERIFICATION_REVISION_AND_CONSUMER_AUTHORIZATION_BINDING'
    return EdgeObservation(edge_id,subject_id,canonical_hash(source),canonical_hash(target),
        allowed_transformation=transformation,measurement_state='MEASURED' if valid is not None else 'UNMEASURED',
        assertion_result=valid,availability='PERSISTED',completeness='COMPLETE' if valid is not None else 'UNKNOWN',
        reason=reason,payload={'source_observation':source,'target_observation':target})

def extract_generation_claim(snapshot, catalog=None, *, subject_id='unknown') -> ObservationBatch:
    batch=ObservationBatch('04'); keyed=defaultdict(list); events=[]; capture_complete=True
    blobs={s.evidence.raw_sha256:s.data for s in snapshot.sources.values() if s.evidence.raw_sha256 and isinstance(s.data,dict)}
    for source in snapshot.sources.values():
        data=source.data
        rows=[data] if isinstance(data,dict) else data if isinstance(data,(list,tuple)) else []
        for ordinal,row in enumerate(rows):
            if not isinstance(row,dict): continue
            key=row.get('evidence_key') or row.get('event_type') or source.family
            material=row.get('material')
            if material is None and row.get('payload_ref'):
                material=blobs.get(row['payload_ref'].get('sha256'))
            event=dict(row)
            if material is not None: event['material']=material
            if key in REPRESENTATIONS or key=='MODEL_CLIENT_EXCEPTION':
                event['_source_evidence']=asdict(replace(source.evidence,pointer='/events/'+str(row.get('sequence',ordinal))))
                events.append(event)
            if key=='SESSION_SEAL':
                capture_complete=capture_complete and row.get('lost_count',0)==0 and row.get('completeness')=='COMPLETE'
            if key in _UNITS: keyed[key].append((row.get('payload',row),source.evidence))
            for production_key,projection in _production_records(row):
                keyed[production_key].append((projection,replace(source.evidence,pointer='/'+str(ordinal))))
            if source.family=='context_packages' and row.get('schema_version')=='context_package_v1':
                selected_ids={item.get('candidate_id') for item in row.get('selected_materializations',[])}
                candidates=[item.get('source_binding',{}) for item in row.get('candidate_decisions',[])
                    if item.get('candidate_id') in selected_ids and item.get('source_binding',{}).get('source_family')=='ACTIVE_VERIFIED_CLAIM']
                for decision in row.get('consumer_authorization_decisions',[]):
                    exact=decision.get('intrinsic_claim_assessment',{}).get('claim_ref',{})
                    matched=[binding for binding in candidates if binding.get('exact_source_ref')==exact]
                    keyed['W-CROSS'].append(({'decision':decision,'invocation_subject':row.get('invocation_subject',{}),
                        'claim_ref':exact.get('claim_ref'),'selected_source_binding':matched[0] if len(matched)==1 else None,
                        'operation_id':row.get('package_ref',{}).get('invocation_id'), 'run_id':row.get('package_ref',{}).get('run_id')},
                        replace(source.evidence,pointer='/'+str(ordinal)+'/consumer_authorization_decisions')))
            if row.get('completeness')=='CAPTURE_INCOMPLETE': capture_complete=False
        if source.family in ('diagnostic_events','diagnostics') and not any(isinstance(r,dict) and r.get('event_type')=='SESSION_SEAL' for r in rows):
            capture_complete=False
    capture_complete=capture_complete and not snapshot.limitations
    reps=model_representations(events,capture_complete=capture_complete)
    if reps['completeness']!='COMPLETE': batch.limitations.append('W-RAW:CAPTURE_INCOMPLETE')
    for group in reps['physical_attempts']:
        if not _physical_writer_proven(group):
            continue
        raw=group['representations'].get('MODEL_CLIENT_RAW_RESPONSE')
        projected=group['representations'].get('MODEL_GATEWAY_PROJECTED_RESPONSE')
        state=group['raw_state']
        batch.stages.append(StageObservation('D05',subject_id,group['model_call_id'],
            source_segment=snapshot.snapshot_id,attempt=group['provider_attempt_id'],
            executed=True,measurement_state='MEASURED' if state=='MEASURED' else 'UNMEASURED',
            evidence_refs=tuple(EvaluationEvidenceRef(**event['_source_evidence'])
                for event in group['representations'].values() if event.get('_source_evidence')),
            payload={'representations':group,'raw_state':state}))
        if raw and projected and raw.get('material') and projected.get('material') and state=='MEASURED':
            r,p=raw['material'],projected['material']
            if r.get('finish_reason')!=p.get('finish_reason') or r.get('success')!=p.get('success'):
                batch.findings.append(Finding('GATEWAY_POST_RETURN_PROJECTION',subject_id,
                    'Client raw and Gateway projection differ; both representations retained',unit_id='D05',edge_id='X10',evidence_level='ACTUAL_OBSERVATION'))
    stage_by_key=defaultdict(list)
    for key,unit in _UNITS.items():
        rows=keyed.get(key) or [(None,None)]
        for ordinal,(row,evidence) in enumerate(rows):
            operation=(row or {}).get('operation_id') or (row or {}).get('operation_ref') or key+':'+str(ordinal)
            bypass=unit=='D11' and row and row.get('bypassed') is True
            stage=StageObservation(unit,subject_id,operation,source_segment=snapshot.snapshot_id,
                scope=(row or {}).get('scope','unknown'),executed=None if row is None else row.get('executed',True),
                applicable=False if bypass else None,measurement_state='N/A' if bypass else 'MEASURED' if row else 'UNMEASURED',
                observed_quality_status=(row or {}).get('observed_quality_status','UNKNOWN'),
                evidence_refs=(evidence,) if evidence else (),payload=row or {'reason':'NOT_RECORDED'})
            batch.stages.append(stage); stage_by_key[key].append(stage)
    for edge,(before,after) in _EDGES.items():
        sources=[s for s in stage_by_key[before] if s.evidence_refs]
        targets=[s for s in stage_by_key[after] if s.evidence_refs]
        if edge=='X12':
            # Aggregate coverage and each committed Claim are distinct stage
            # observations; only an original Claim is a Binding operand.
            sources=[s for s in sources if s.payload.get('record_origin')!='PERSISTED_OWNER' or s.payload.get('claim')]
        if edge=='X10':
            targets=[s for s in targets if s.payload.get('record_origin')!='PERSISTED_OWNER' or s.payload.get('paragraphs')]
        if edge=='X11':
            sources=[s for s in sources if s.payload.get('record_origin')!='PERSISTED_OWNER' or
                s.payload.get('receipt',{}).get('resulting_generation_membership_state')=='SEALED']
            targets=[s for s in targets if s.payload.get('record_origin')!='PERSISTED_OWNER' or s.payload.get('coverage_accounting')]
        if before=='W-RAW':
            sources=[StageObservation('D05',subject_id,g['model_call_id'],attempt=g['provider_attempt_id'],
                evidence_refs=tuple(EvaluationEvidenceRef(**e['_source_evidence']) for e in g['representations'].values() if e.get('_source_evidence')),
                payload={'physical_attempt':g,'model_call_id':g['model_call_id'],'provider_attempt_id':g['provider_attempt_id']}) for g in reps['physical_attempts'] if _physical_writer_proven(g)]
        if edge=='X15' and any(target.payload.get('decision') for target in targets):
            used=set()
            by_claim=defaultdict(list)
            for i,source in enumerate(sources):
                claim=source.payload.get('result',{}).get('claim_ref')
                if claim is not None: by_claim[canonical_hash(claim)].append((i,source))
            for target in targets:
                candidates=by_claim.get(canonical_hash(target.payload.get('claim_ref')),[])
                assessed=target.payload.get('decision',{}).get('intrinsic_claim_assessment',{}).get('assessed_verification_revision_ref')
                exact=[pair for pair in candidates if pair[1].payload.get('original_record',{}).get('verification_revision',{}).get('revision_ref')==assessed]
                candidates=exact or candidates
                source=candidates[0][1] if len(candidates)==1 else None
                if source: used.add(candidates[0][0])
                result=check_generation_edge(edge,source.payload if source else None,target.payload,subject_id=subject_id)
                batch.edges.append(replace(result,source_refs=source.evidence_refs if source else tuple(ref for _,s in candidates for ref in s.evidence_refs),
                    target_refs=target.evidence_refs))
            for i,source in enumerate(sources):
                if i not in used:
                    batch.edges.append(replace(check_generation_edge(edge,source.payload,None,subject_id=subject_id),source_refs=source.evidence_refs))
            continue
        used=set()
        for source in sources or [None]:
            candidates=[]
            for i,target in enumerate(targets):
                shared=set(_join_identity(source.payload if source else {})) & set(_join_identity(target.payload))
                equal=shared and all(_join_identity(source.payload)[k]==_join_identity(target.payload)[k] for k in shared)
                if equal or (len(sources)==len(targets)==1 and not shared): candidates.append((i,target))
            target=candidates[0][1] if len(candidates)==1 else None
            if target: used.add(candidates[0][0])
            result=check_generation_edge(edge,source.payload if source else None,target.payload if target else None,
                subject_id=subject_id,physical=source.payload.get('physical_attempt') if source else None)
            batch.edges.append(replace(result,source_refs=source.evidence_refs if source else (),
                target_refs=target.evidence_refs if target else tuple(ref for _,candidate in candidates for ref in candidate.evidence_refs),
                reason='AMBIGUOUS_OR_UNPROVEN_ORIGINAL_LINEAGE' if len(candidates)!=1 and targets else result.reason))
        for i,target in enumerate(targets):
            if i not in used and sources:
                result=check_generation_edge(edge,None,target.payload,subject_id=subject_id)
                batch.edges.append(replace(result,target_refs=target.evidence_refs,reason='UNMATCHED_CONSUMER_ORIGINAL_LINEAGE'))
    for edge in batch.edges:
        if edge.assertion_result is False:
            batch.findings.append(Finding('GENERATION_EDGE_FAILURE',subject_id,edge.reason,
                edge_id=edge.edge_id,evidence_level='OBSERVED',evidence_refs=(*edge.source_refs, *edge.target_refs)))
    batch.limitations.extend(reps['limitations'])
    return batch


def _join_identity(payload):
    """Only original explicit identities; order/content/aliases cannot join."""
    keys=('run_id','section_id','claim_ref','paragraph_version_ref','model_call_id','provider_attempt_id')
    result={k:canonical_hash(payload[k]) for k in keys if payload.get(k) is not None}
    for name in ('claim','binding','support','result'):
        value=payload.get(name)
        if isinstance(value,dict):
            for key in keys:
                if value.get(key) is not None: result[key]=canonical_hash(value[key])
            claim=value.get('claim_ref')
            if isinstance(claim,dict) and claim.get('claim_id'):
                result['claim_id']=canonical_hash(claim['claim_id'])
                occurrence=claim.get('claim_occurrence_ref') or {}
                for key in ('run_id','section_id','paragraph_version_ref'):
                    if occurrence.get(key) is not None: result[key]=canonical_hash(occurrence[key])
            support_subject=value.get('claim_support_subject_ref') or {}
            if support_subject.get('authority_kind')=='SIF07_CLAIM' and support_subject.get('subject_ref'):
                result['claim_id']=canonical_hash(support_subject['subject_ref'])
    original=payload.get('original_record') or {}
    op=original.get('operation_ref') or (original.get('receipt') or {}).get('operation_ref') or {}
    if isinstance(op,dict):
        for key in ('run_id','section_id'):
            if op.get(key) is not None: result[key]=canonical_hash(op[key])
        if op.get('attempt_scope_ref') is not None: result['scope']=canonical_hash(op['attempt_scope_ref'])
    manifest=payload.get('receipt',{}).get('operation_ref',{}).get('manifest_ref',{})
    for key in ('run_id','section_id'):
        if manifest.get(key) is not None: result[key]=canonical_hash(manifest[key])
    physical=payload.get('physical_attempt',{}).get('representations',{}).get('MODEL_ATTEMPT_REQUEST',{}).get('material',{})
    if isinstance(physical,dict):
        if physical.get('run_id') is not None: result['run_id']=canonical_hash(physical['run_id'])
        if physical.get('extra',{}).get('section_id') is not None: result['section_id']=canonical_hash(physical['extra']['section_id'])
    return result
