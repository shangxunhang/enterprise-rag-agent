"""Real terminal transitions; controlled quality/usability oracles isolate I9 routing.

These oracles do not claim business-quality acceptance. Canonical Gate/Control,
Assembly, Assessment, lifecycle CAS and publisher remain real owners.
"""
from unittest.mock import patch
import pytest
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from apps.enterprise_document.schemas.post_write import (
    ArtifactScope, ArtifactUsabilityIntegrityState, EnforcementAction, OverallQualityState)
from apps.enterprise_document.schemas.post_write.control import ArtifactUsabilityIntegrityProofSchema
from apps.enterprise_document.schemas.post_write.gate import OverallQualityProjectionSchema
from core.hashing import canonical_sha256
from backend.tests.bca014_i9_fixtures import make_target, terminal_case, changed_request, manifests, add_orphan_control
from backend.tests.test_bca014_i9_release_terminal_chain import call


def rehash(model, field, **changes):
    payload=model.model_dump(mode='json');payload.update(changes);payload.pop(field)
    return type(model)(**payload,**{field:canonical_sha256(payload)})


@pytest.mark.parametrize('quality',['PASS','FAIL','UNRESOLVED'])
def test_real_terminal_release_action_matrix(tmp_path,quality):
    post,request=make_target(tmp_path, verdict='UNSUPPORTED' if quality=='FAIL' else 'VERIFIED')
    # Only STANDARD needs a controlled aggregate-quality oracle; ordinary FAIL
    # and UNRESOLVED keep all canonical semantic fixtures and diagnostics.
    original=post.gate_service._overall_projection
    def passed(resolved,dimensions):
        if resolved.post_write_entry.identity.artifact_state_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            return original(resolved,dimensions)
        return rehash(original(resolved,dimensions),'projection_hash',
            overall_quality_state='PASS',blocking_fact_refs=[],unresolved_reason_refs=[])
    context=patch.object(post.gate_service,'_overall_projection',side_effect=passed) if quality=='PASS' else patch.object(post.gate_service,'_overall_projection',wraps=original)
    with context:
        result=post.complete_target_post_write(**request)
        replay=post.complete_target_post_write(**request)
    assert result.terminal_run.status is RS.SUCCESS
    manifest=result.published_release.manifest
    assert manifest.quality_state_passthrough.value==quality
    assert manifest.release_mode.value==('STANDARD' if quality=='PASS' else 'BEST_EFFORT')
    assert manifest.enforcement_action_passthrough.value==('OUTPUT' if quality=='PASS' else 'OUTPUT_BEST_EFFORT')
    assert replay.published_release.manifest==manifest
    assert len(manifests(post))==1
    control=post.control_store.resolve_exact(manifest.post_write_control_decision_ref)
    assert manifest.blocking_fact_refs==control.blocking_fact_refs
    assert manifest.unresolved_reason_refs==control.unresolved_reason_refs
    if quality=='UNRESOLVED':assert manifest.unresolved_reason_refs


def test_real_block_success_and_terminal_replay_never_publish(tmp_path):
    post,request=make_target(tmp_path)
    resolve=post.usability_resolver.resolve
    def withheld(**kw):
        proof=resolve(**kw)
        if kw['artifact_state_ref'].artifact_scope is ArtifactScope.DOCUMENT:
            return rehash(proof,'proof_sha256',state='INTEGRITY_INVALID',
                artifact_record_hash=None,reason_codes=['I9_CONTROLLED_WITHHELD_USABILITY'])
        return proof
    with patch.object(post.usability_resolver,'resolve',side_effect=withheld), \
            patch.object(post.release_service,'publish',side_effect=AssertionError('BLOCK publisher')):
        result=post.complete_target_post_write(**request)
        replay=post.complete_target_post_write(**request)
    assert result.terminal_run.status is RS.SUCCESS
    assert post.control_store.resolve_exact(result.final_document.control_decision_ref).enforcement_action is EnforcementAction.BLOCK
    assert result.published_release is replay.published_release is None
    assert not manifests(post)


def test_durable_orphan_control_cannot_first_publish_same_output(tmp_path):
    post,request=make_target(tmp_path)
    enter=post._commit_and_terminalize_run;orphan=[]
    def capture(*a,**kw):
        selected=kw['final_document']
        orphan.append(add_orphan_control(post, selected))
        return enter(*a,**kw)
    with patch.object(post,'_commit_and_terminalize_run',side_effect=capture),patch.object(post,'_publish_target_release',return_value=None):
        result=post.complete_target_post_write(**request)
    assert orphan and post.control_store.resolve_exact(orphan[0]).as_ref()==orphan[0]
    req=post.release_service.build_request(release_contract_version=post.release_service.release_contract_version,
        task_id=request['task_id'],run_id=request['run_id'],document_logical_id=f"document-logical:{request['run_id']}:{request['document_id']}",
        document_artifact_ref=result.final_document.artifact_state_ref,post_write_control_decision_ref=orphan[0],
        output_format=result.final_document.output_binding.output_format,
        validated_output_binding_sha256=result.final_document.output_binding.binding_sha256,publication_target_id='target://orphan')
    from apps.enterprise_document.services.post_write.release import PostWriteReleaseError
    with pytest.raises(PostWriteReleaseError,match='another final ControlDecision'):
        call((post,request,result,req),req)
    assert not manifests(post)
