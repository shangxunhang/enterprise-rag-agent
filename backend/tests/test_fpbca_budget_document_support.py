"""Pending Support owner controls, not full Graph/cursor acceptance.

Claim/Binding/Evidence/Paragraph/Support, Gateway, Budget and Lifecycle are native.
The completed DOCUMENT association envelopes are controlled caller fixtures in
the existing ArtifactStore. Full production association eligibility/currentness
is separately required by the native BT03 and Graph integration rows.
"""
from copy import deepcopy
from types import SimpleNamespace
import base64
import hashlib
import json

import pytest

from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import (
    TargetClaimSupportError, SUPPORT_SEMANTIC_CONTRACT, TARGET_SET_CONTRACT,
)
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus
from apps.enterprise_document.schemas.post_write.foundation import ArtifactStateRefSchema, ArtifactScope
from apps.enterprise_document.schemas.post_write.quality import CitationOccurrenceTargetSchema, CitationTargetRefSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    CitationAliasCatalog, CitationAliasEntry, CitationIntegritySnapshot,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import (
    TargetPostWriteCitationGrounding, ASSOCIATION_CONTRACT, POLICY_ID, POLICY_VERSION,
)
from apps.enterprise_document.services.post_write.shared_claim_support import (
    ArtifactClaimTargetMaterializer, SharedClaimEvidenceSupportEvaluator,
)
from apps.enterprise_document.services.post_write.citation import CitationGroundingTargetMaterializer
from core.hashing import canonical_sha256
from local_verifier_recovery_helpers import create_fixture
from test_fpbca_budget_document_continuation_guards import (
    _authorize_exact_native_refusal, _consume_test_owned_token_preimage,
)
from test_fpbca_budget_document_phase_proof import _gateway as _fallback_gateway


def _observe(fx, suffix, value):
    path = fx.root / ('support-budget-' + suffix + '.json')
    assert not path.exists()
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _fresh(root, *, drain=True):
    fx = create_fixture(root, budget_tokens=24000)
    owner = fx.support_composition
    owner.model_gateway = fx.gateway
    subject = fx.support.claim_support_subject_ref
    member = fx.support.evidence_target_set_ref.evidence_member_refs[1]
    target = owner.runtime.materialize_evidence_target_set(subject, (member,), TARGET_SET_CONTRACT)
    assert owner.runtime.resolve_support_by_identity(subject, target, SUPPORT_SEMANTIC_CONTRACT) is None
    fx.client.script = [{'relation':'SUPPORTED', 'supporting_refs':[member], 'contradicting_refs':[],
                         'certainty_relation':'ALIGNED', 'certainty_reason_codes':['LOCAL_CONTROL_ALIGNED']}]
    identity = dict(task_id=fx.budget.task_id, run_id=fx.budget.run_id, section_id=fx.claim.section_id)
    # This is an explicitly controlled completed caller envelope. It is not a
    # claim that a production DOCUMENT graph or association owner ran here.
    artifact = ArtifactStateRefSchema(artifact_id='local-controlled-document',artifact_version=1,
        artifact_scope=ArtifactScope.DOCUMENT,artifact_hash=canonical_sha256('local-doc'))
    subject_hash=canonical_sha256('local-controlled-native-document-subject')
    claim_target=ArtifactClaimTargetMaterializer._sif07_target(record=fx.claim,artifact_state_ref=artifact,
        validation_subject_hash=subject_hash,semantic_version=TargetPostWriteCitationGrounding.CLAIM_PROJECTION_CONTRACT_VERSION)
    occurrence=CitationOccurrenceTargetSchema(occurrence_ref='local-controlled-citation',artifact_state_ref=artifact,
        validation_subject_hash=subject_hash,citation_alias='C1',source_span_ref='local-controlled-citation-span',
        occurrence_hash=canonical_sha256('local-controlled-citation'))
    origin={'section_id':fx.claim.section_id,'exact_evidence_ref':member}
    catalog=CitationAliasCatalog.build(scope='DOCUMENT',source_refs=({'claim_ref':fx.claim.claim_ref.model_dump(mode='json')},),
        entries=(CitationAliasEntry(alias='C1',citation_ref='local-controlled-citation-source',exact_evidence_ref=member,
            source_identity_consistent=True,origin_refs=(origin,)),))
    snapshot=CitationIntegritySnapshot(artifact_state_ref=artifact.model_dump(mode='json'),validation_subject_hash=subject_hash,
        catalog=catalog,occurrences=({'target':occurrence.model_dump(mode='json'),'span':{'field':'content','section_id':fx.claim.section_id}},))
    material = {'contract':ASSOCIATION_CONTRACT,'policy_id':POLICY_ID,'policy_version':POLICY_VERSION,
        'association_representation_version':'v2','validation_run_id':'local-controlled-document-validation',
        'subject_hash':subject_hash,'visible_artifact':{'payload_kind':'document'},
        'artifact_state_ref':artifact.model_dump(mode='json'),
        'candidate_claim_targets':[claim_target.model_dump(mode='json')],
        'eligible_occurrence_refs':[occurrence.occurrence_ref],
        'no_applicable_allowed':{occurrence.occurrence_ref:False},
        'occurrences':list(snapshot.occurrences),'citation_source_hash':canonical_sha256(snapshot.model_dump(mode='json'))}
    doc_hash = canonical_sha256(material)
    doc_input = owner.store.put_json({'schema_version':'citation_association_input_v1',
        'input_material':material, 'input_hash':doc_hash},
        artifact_ref=f'artifact://citation-association/{doc_hash}/input',
        artifact_type='citation_association_input', schema_version='citation_association_input_v1',
        created_by='explicit-owner-controlled-caller-fixture', **identity)
    reply={'associations':[{'occurrence_ref':occurrence.occurrence_ref,'state':'ASSOCIATED',
                           'associated_claim_target_ids':[claim_target.claim_target_id]}]}
    doc_result = owner.store.put_json({'input_hash':doc_hash, 'outcome':'completed', 'candidate':reply, 'token_usage':{}},
        artifact_ref=f'artifact://citation-association/{doc_hash}/result',
        artifact_type='citation_association_result', schema_version='citation_association_result_v1',
        created_by='explicit-owner-controlled-caller-fixture', **identity)
    association=TargetPostWriteCitationGrounding.association_evidence(
        TargetPostWriteCitationGrounding.parse_reply(reply,material)[0],material,doc_input.artifact_ref).association
    cite_base={'citation_ref':catalog.entries[0].citation_ref,'exact_origin':origin,'artifact_state_ref':artifact.model_dump(mode='json')}
    cite_hash=canonical_sha256(cite_base)
    citation=CitationTargetRefSchema(citation_target_ref='citation-target://sha256/'+cite_hash,exact_evidence_ref=member,target_hash=cite_hash)
    group=CitationGroundingTargetMaterializer(SharedClaimEvidenceSupportEvaluator(owner.runtime)).materialize(
        claim_target=claim_target,associations=(association,),occurrences=(occurrence,),citation_targets=(citation,),
        individual_evidence_target_sets=(target,),joint_target_set_contract_version=TARGET_SET_CONTRACT)
    document_context = {'schema_version':'document_support_budget_context_v1',
        'document_identity':identity,
        'document_input_ref':doc_input.artifact_ref,
        'document_input_sha256':doc_input.content_hash.removeprefix('sha256:'),
        'document_result_ref':doc_result.artifact_ref,
        'document_result_sha256':doc_result.content_hash.removeprefix('sha256:'),
        'association_budget_continuation':None,'citation_source_snapshot':snapshot.model_dump(mode='json'),
        'grounding_target':group.model_dump(mode='json')}
    key = canonical_sha256({'subject':subject.model_dump(mode='json'),
        'target':target.model_dump(mode='json'), 'contract':SUPPORT_SEMANTIC_CONTRACT})
    fx.subject, fx.target, fx.identity = subject, target, identity
    fx.semantic_key, fx.document_context = key, document_context
    fx.input_ref, fx.result_ref = f'artifact://claim-support/{key}/input', f'artifact://claim-support/{key}/candidate'
    if drain:
        _consume_test_owned_token_preimage(fx.budget, fx.budget.canonical_state().remaining.model_tokens_total,
                                          'pending-document-support')
    return fx


def _invoke(fx, binding=None, *, document_context=True):
    options = {'document_budget_context':fx.document_context} if document_context else {}
    if binding is not None:
        options['budget_continuation'] = binding
    return fx.support_composition.evaluate(fx.subject, fx.target, SUPPORT_SEMANTIC_CONTRACT, fx.context, **options)


def _refuse(fx):
    before = fx.budget.canonical_state()
    with pytest.raises(TargetClaimSupportError) as caught:
        _invoke(fx)
    owner = fx.support_composition
    result = owner.store.get_json(fx.result_ref, **fx.identity)
    _observe(fx, 'first-refusal', {'scope':'NATIVE_PENDING_SUPPORT_OWNER_CONTROL_NOT_GRAPH',
        'input_ref':fx.input_ref, 'result_ref':fx.result_ref,
        'result':result, 'error':str(caught.value), 'before':before.model_dump(mode='json'),
        'after':fx.budget.canonical_state().model_dump(mode='json'),
        'physical_requests':[row.model_dump(mode='json') for row in fx.client.calls]})
    refusal = caught.value.budget_failure_receipt
    assert refusal['operation_kind'] == 'DOCUMENT_SUPPORT'
    assert refusal['semantic_operation_ref'] == 'claim-support-evaluation://sha256/' + fx.semantic_key
    assert refusal['semantic_input_sha256'] == result['input_fingerprint']
    assert owner.resolve_budget_refusal(receipt_ref=fx.result_ref,
        expected_sha256=refusal['native_denial_sha256'], identity=fx.identity) == refusal
    assert fx.client.calls == [] and fx.gateway._active_calls == 0
    assert fx.budget.canonical_state().used == before.used
    assert fx.budget.canonical_state().active_reserved.is_zero()
    return refusal


def _authorize(fx, refusal):
    owner = fx.support_composition
    projection = SimpleNamespace(store=owner.store)
    binding = _authorize_exact_native_refusal(projection, fx.budget, fx.lifecycle,
                                            fx.identity, refusal, 12000)
    fx.context = fx.context.model_copy(update={'active_lineage':fx.lifecycle.resolve_active_lineage(
        fx.identity['run_id'], 'section:'+fx.identity['section_id'])},deep=True)
    return binding


def test_pending_document_support_first_refusal_has_positive_native_closed_proof(tmp_path):
    fx = _fresh(tmp_path)
    refusal = _refuse(fx)
    result = fx.support_composition.store.get_json(fx.result_ref, **fx.identity)
    proof = fx.gateway.budget_runtime.validate_pre_physical_token_rejection_proof(result['failure']['phase_proof'])
    assert proof.proof_sha256 == refusal['phase_proof_sha256']
    assert proof.native_denial.model_dump(mode='json') == result['failure']['native_denial']
    assert proof.routed_request.extra['logical_operation_id'] == refusal['semantic_operation_ref']


def test_pending_support_new_grant_native_commit_and_both_certainty_readers_once(tmp_path):
    fx = _fresh(tmp_path); owner = fx.support_composition; refusal = _refuse(fx)
    original = owner.store.get_bytes(fx.result_ref, **fx.identity)
    original_input = owner.store.get_bytes(fx.input_ref, **fx.identity)
    binding = _authorize(fx, refusal); before = fx.budget.canonical_state()
    candidate = _invoke(fx, binding)
    record = owner.runtime.commit_support(candidate, fx.context)
    after = fx.budget.canonical_state()
    _observe(fx, 'successor', {'binding':binding, 'candidate':candidate.model_dump(mode='json'),
        'record':record.model_dump(mode='json'), 'before':before.model_dump(mode='json'),
        'after':after.model_dump(mode='json'), 'physical':[row.model_dump(mode='json') for row in fx.client.calls]})
    assert len(fx.client.calls) == 1 and '/admission/' in candidate.evaluation_occurrence_ref
    assert after.limits.model_tokens_total == 24000 and after.effective_limits.model_tokens_total == 36000
    assert after.used.logical_model_calls == before.used.logical_model_calls + 1
    assert after.used.model_tokens_total == before.used.model_tokens_total + 22
    assert _invoke(fx, binding) == candidate
    assert fx.budget.canonical_state() == after and len(fx.client.calls) == 1
    certainty = owner.resolve_certainty_observation(support_record=record, operation_context=fx.context)
    snapshot_certainty = owner.resolve_certainty_observation_from_validated_snapshot(
        support_record=record, claim_record=fx.claim, active_lineage=fx.context.active_lineage,
        task_id=fx.identity['task_id'])
    assert certainty == snapshot_certainty and certainty.certainty_relation == 'ALIGNED'
    assert '/admission/' in certainty.physical_support_receipt_ref
    assert owner.store.get_bytes(fx.result_ref, **fx.identity) == original
    assert owner.store.get_bytes(fx.input_ref, **fx.identity) == original_input
    with pytest.raises(TargetClaimSupportError):_invoke(fx)
    assert len(fx.client.calls) == 1 and fx.budget.canonical_state() == after


@pytest.mark.parametrize('cut',['intent','candidate'])
def test_pending_support_native_admission_cuts_do_not_redispatch(tmp_path,monkeypatch,cut):
    fx = _fresh(tmp_path); owner = fx.support_composition; refusal = _refuse(fx)
    binding = _authorize(fx, refusal); put = owner.store.put_json
    class Cut(BaseException):pass
    def interrupted(value,**options):
        ref = options.get('artifact_ref','')
        if '/admission/' in ref:
            if cut == 'intent' and ref.endswith('/input'):
                put(value,**options); raise Cut()
            if cut == 'candidate' and ref.endswith('/candidate'):raise Cut()
        return put(value,**options)
    monkeypatch.setattr(owner.store,'put_json',interrupted)
    with pytest.raises(Cut):_invoke(fx,binding)
    monkeypatch.setattr(owner.store,'put_json',put)
    before = fx.budget.canonical_state(); count = len(fx.client.calls)
    _observe(fx,'cut-'+cut,{'physical_count':count,'native_budget':before.model_dump(mode='json'),'binding':binding})
    assert count == (0 if cut == 'intent' else 1)
    with pytest.raises(TargetClaimSupportError):_invoke(fx,binding)
    assert len(fx.client.calls) == count and fx.budget.canonical_state() == before


@pytest.mark.parametrize('damage',['grant','resume','children','cancel','capability','document-result'])
def test_pending_support_changed_native_binding_never_dispatches(tmp_path,monkeypatch,damage):
    fx = _fresh(tmp_path); owner = fx.support_composition; refusal = _refuse(fx)
    binding = _authorize(fx,refusal)
    if damage == 'grant':binding['accepted_grant_sha256']='0'*64
    elif damage == 'resume':binding['resume_event_sha256']='0'*64
    elif damage == 'children':fx.lifecycle.open_attempt(fx.identity['run_id'],'section:extra','snapshot-extra',attempt_id='extra')
    elif damage == 'cancel':
        run=fx.lifecycle.budget_preimage(fx.identity['run_id']).run
        fx.lifecycle.transition_run(fx.identity['run_id'],run.lifecycle_revision,RunLifecycleStatus.CANCELLED,reason_ref='test-explicit-cancel')
    elif damage == 'capability':
        routing=fx.gateway.routing_capabilities
        monkeypatch.setattr(fx.gateway,'routing_capabilities',lambda **kw:{**routing(**kw),'safe_context_window':32768})
    else:fx.document_context={**fx.document_context,'document_result_sha256':'0'*64}
    before=fx.budget.canonical_state()
    _observe(fx,'changed-'+damage,{'binding':binding,'document_context':fx.document_context,
        'native_budget':before.model_dump(mode='json'),'native_run':fx.lifecycle.budget_preimage(fx.identity['run_id']).run.model_dump(mode='json')})
    with pytest.raises((ValueError,RuntimeError)):_invoke(fx,binding)
    assert fx.client.calls == [] and fx.budget.canonical_state() == before


def test_pending_support_generic_budget_failure_cannot_mint_phase_proof(tmp_path,monkeypatch):
    fx=_fresh(tmp_path,drain=False); owner=fx.support_composition
    def generic(_request):raise CanonicalBudgetCapacityExceeded('model_tokens_total',limit=24000,remaining=0,required=8000)
    monkeypatch.setattr(fx.gateway.budget_runtime,'reserve_provider_attempt',generic)
    with pytest.raises(TargetClaimSupportError):_invoke(fx)
    reference,result=owner.store.get_json_with_reference(fx.result_ref,**fx.identity)
    _observe(fx,'generic',{'result':result,'physical_count':len(fx.client.calls),'native_budget':fx.budget.canonical_state().model_dump(mode='json')})
    assert not result['failure'].get('phase_proof') and fx.client.calls == []
    with pytest.raises((ValueError,RuntimeError)):
        owner.resolve_budget_refusal(receipt_ref=fx.result_ref,expected_sha256=reference.content_hash.removeprefix('sha256:'),identity=fx.identity)


def test_pending_support_postdispatch_failure_cannot_mint_phase_proof(tmp_path,monkeypatch):
    fx=_fresh(tmp_path,drain=False); owner=fx.support_composition
    def settlement(*_args):raise CanonicalBudgetCapacityExceeded('model_tokens_total',limit=24000,remaining=0,required=1)
    monkeypatch.setattr(fx.gateway.budget_runtime,'settle_provider_attempt',settlement)
    with pytest.raises(TargetClaimSupportError):_invoke(fx)
    reference,result=owner.store.get_json_with_reference(fx.result_ref,**fx.identity)
    _observe(fx,'postdispatch',{'result':result,'physical':[row.model_dump(mode='json') for row in fx.client.calls],
        'native_budget':fx.budget.canonical_state().model_dump(mode='json')})
    assert len(fx.client.calls) == 1 and not result['failure'].get('phase_proof')
    with pytest.raises((ValueError,RuntimeError)):
        owner.resolve_budget_refusal(receipt_ref=fx.result_ref,expected_sha256=reference.content_hash.removeprefix('sha256:'),identity=fx.identity)


def test_pending_support_fallback_capacity_rejection_retains_first_actual_effect(tmp_path):
    fx=_fresh(tmp_path,drain=False); owner=fx.support_composition
    gateway,clients,physical=_fallback_gateway(fx.budget,fallback=True)
    owner.model_gateway=gateway
    clients[0].unavailable=True; clients[0].usage=1
    first=clients[0].generate
    def consume_remaining_during_first_effect(request):
        available=fx.budget.canonical_state().remaining.model_tokens_total
        _consume_test_owned_token_preimage(fx.budget,available,'fallback-first-effect')
        return first(request)
    clients[0].generate=consume_remaining_during_first_effect
    with pytest.raises(TargetClaimSupportError):_invoke(fx)
    reference,result=owner.store.get_json_with_reference(fx.result_ref,**fx.identity)
    _observe(fx,'fallback',{'result':result,'physical':[row.model_dump(mode='json') for row in physical],
        'native_budget':fx.budget.canonical_state().model_dump(mode='json')})
    assert len(physical)==1 and len(clients[0].calls)==1 and clients[1].calls==[]
    assert not result['failure'].get('phase_proof')
    with pytest.raises((ValueError,RuntimeError)):
        owner.resolve_budget_refusal(receipt_ref=fx.result_ref,expected_sha256=reference.content_hash.removeprefix('sha256:'),identity=fx.identity)


def test_old_unproved_failed_support_candidate_is_immutable_and_not_upgraded(tmp_path):
    fx=_fresh(tmp_path); owner=fx.support_composition
    # Exact old producer path creates its own native input/result. This test
    # deliberately omits DOCUMENT scope; no historical Run is recovered.
    with pytest.raises(TargetClaimSupportError):
        owner.evaluate(fx.subject,fx.target,SUPPORT_SEMANTIC_CONTRACT,fx.context)
    reference,result=owner.store.get_json_with_reference(fx.result_ref,**fx.identity)
    original=owner.store.get_bytes(fx.result_ref,**fx.identity); before=fx.budget.canonical_state()
    _observe(fx,'legacy-unproved',{'result':result,'result_ref':reference.model_dump(mode='json'),'native_budget':before.model_dump(mode='json')})
    assert not result['failure'].get('phase_proof')
    with pytest.raises((ValueError,RuntimeError)):
        owner.resolve_budget_refusal(receipt_ref=fx.result_ref,expected_sha256=reference.content_hash.removeprefix('sha256:'),identity=fx.identity)
    with pytest.raises(TargetClaimSupportError):_invoke(fx)
    assert owner.store.get_bytes(fx.result_ref,**fx.identity) == original
    assert fx.budget.canonical_state() == before and fx.client.calls == []


def test_pending_support_old_native_intent_has_no_automatic_dispatch(tmp_path,monkeypatch):
    fx=_fresh(tmp_path,drain=False); owner=fx.support_composition; put=owner.store.put_json
    class Cut(BaseException):pass
    def stopped(value,**options):
        result=put(value,**options)
        if options.get('artifact_ref') == fx.input_ref:raise Cut()
        return result
    monkeypatch.setattr(owner.store,'put_json',stopped)
    with pytest.raises(Cut):_invoke(fx)
    monkeypatch.setattr(owner.store,'put_json',put)
    before=fx.budget.canonical_state()
    _observe(fx,'old-intent',{'native_input':owner.store.get_json(fx.input_ref,**fx.identity),
        'native_budget':before.model_dump(mode='json'),'physical_count':len(fx.client.calls)})
    with pytest.raises(TargetClaimSupportError):_invoke(fx)
    assert fx.budget.canonical_state() == before and fx.client.calls == []


def test_failed_support_candidate_rejects_coherent_fresh_input_document_header_substitution(tmp_path):
    """Fresh corruption control; immutable producer overwrite is NOT permitted."""
    fx=_fresh(tmp_path); owner=fx.support_composition; refusal=_refuse(fx)
    original_result=owner.store.get_bytes(fx.result_ref,**fx.identity)
    old_context=fx.document_context
    old_input=owner.store.get_json(old_context['document_input_ref'],**fx.identity)
    doc=deepcopy(old_input['input_material'])
    artifact=ArtifactStateRefSchema.model_validate(doc['artifact_state_ref']).model_copy(
        update={'artifact_version':2,'artifact_hash':canonical_sha256('different-local-doc')},deep=True)
    subject_hash=canonical_sha256('different-local-document-subject')
    claim_target=ArtifactClaimTargetMaterializer._sif07_target(record=fx.claim,artifact_state_ref=artifact,
        validation_subject_hash=subject_hash,semantic_version=TargetPostWriteCitationGrounding.CLAIM_PROJECTION_CONTRACT_VERSION)
    snapshot=CitationIntegritySnapshot.model_validate(old_context['citation_source_snapshot'])
    occurrence=CitationOccurrenceTargetSchema.model_validate(snapshot.occurrences[0]['target']).model_copy(
        update={'artifact_state_ref':artifact,'validation_subject_hash':subject_hash},deep=True)
    snapshot=snapshot.model_copy(update={'artifact_state_ref':artifact.model_dump(mode='json'),
        'validation_subject_hash':subject_hash,'occurrences':({'target':occurrence.model_dump(mode='json'),
            'span':snapshot.occurrences[0]['span']},)},deep=True)
    doc.update(artifact_state_ref=artifact.model_dump(mode='json'),subject_hash=subject_hash,
        candidate_claim_targets=[claim_target.model_dump(mode='json')],occurrences=list(snapshot.occurrences),
        citation_source_hash=canonical_sha256(snapshot.model_dump(mode='json')),
        visible_artifact={'payload_kind':'document','controlled_changed_document':'B'})
    key=canonical_sha256(doc)
    input_b=owner.store.put_json({**old_input,'input_material':doc,'input_hash':key},
        artifact_ref=f'artifact://citation-association/{key}/input',artifact_type='citation_association_input',
        schema_version='citation_association_input_v1',created_by='fresh-negative-controlled-doc-B',**fx.identity)
    reply={'associations':[{'occurrence_ref':occurrence.occurrence_ref,'state':'ASSOCIATED',
        'associated_claim_target_ids':[claim_target.claim_target_id]}]}
    result_b=owner.store.put_json({'input_hash':key,'outcome':'completed','candidate':reply},
        artifact_ref=f'artifact://citation-association/{key}/result',artifact_type='citation_association_result',
        schema_version='citation_association_result_v1',created_by='fresh-negative-controlled-doc-B',**fx.identity)
    association=TargetPostWriteCitationGrounding.association_evidence(
        TargetPostWriteCitationGrounding.parse_reply(reply,doc)[0],doc,input_b.artifact_ref).association
    origin=snapshot.catalog.entries[0].origin_refs[0]
    cite_hash=canonical_sha256({'citation_ref':snapshot.catalog.entries[0].citation_ref,
        'exact_origin':origin,'artifact_state_ref':artifact.model_dump(mode='json')})
    citation=CitationTargetRefSchema(citation_target_ref='citation-target://sha256/'+cite_hash,
        exact_evidence_ref=fx.target.evidence_member_refs[0],target_hash=cite_hash)
    group=CitationGroundingTargetMaterializer(SharedClaimEvidenceSupportEvaluator(owner.runtime)).materialize(
        claim_target=claim_target,associations=(association,),occurrences=(occurrence,),citation_targets=(citation,),
        individual_evidence_target_sets=(fx.target,),joint_target_set_contract_version=TARGET_SET_CONTRACT)
    context_b={**old_context,'document_input_ref':input_b.artifact_ref,
        'document_input_sha256':input_b.content_hash.removeprefix('sha256:'),
        'document_result_ref':result_b.artifact_ref,'document_result_sha256':result_b.content_hash.removeprefix('sha256:'),
        'citation_source_snapshot':snapshot.model_dump(mode='json'),'grounding_target':group.model_dump(mode='json')}
    # Only this explicitly fresh negative test injects native store corruption.
    # Save original bytes before changing header/body coherently; candidate,
    # Gateway request and locked Budget journal remain byte-exact unchanged.
    path=owner.store._path_for_ref(fx.input_ref); original=path.read_bytes()
    envelope=json.loads(original); intent=json.loads(base64.b64decode(envelope['content_base64']))
    intent['document_budget_context']=context_b
    body=json.dumps(intent,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    envelope['content_base64']=base64.b64encode(body).decode('ascii')
    envelope['reference']['content_hash']='sha256:'+hashlib.sha256(body).hexdigest()
    envelope['reference']['size_bytes']=len(body)
    _observe(fx,'header-original-bytes',{'scope':'FRESH_CORRUPTION_NEGATIVE_NOT_PRODUCTION_OVERWRITE',
        'original_input_envelope_base64':base64.b64encode(original).decode('ascii'),
        'original_input_envelope_sha256':hashlib.sha256(original).hexdigest(),
        'original_candidate_sha256':refusal['native_denial_sha256'],'native_budget':fx.budget.canonical_state().model_dump(mode='json')})
    path.write_bytes(json.dumps(envelope,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))
    before=fx.budget.canonical_state()
    with pytest.raises((ValueError,RuntimeError)):
        owner.resolve_budget_refusal(receipt_ref=fx.result_ref,expected_sha256=refusal['native_denial_sha256'],identity=fx.identity)
    assert owner.store.get_bytes(fx.result_ref,**fx.identity)==original_result
    assert fx.client.calls==[] and fx.budget.canonical_state()==before
