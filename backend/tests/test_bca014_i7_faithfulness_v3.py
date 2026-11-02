"""V3 verdict authority, minimal payload, exact identity and mechanical binding."""
import pytest
from pydantic import ValidationError
from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.post_write.quality import (
    FaithfulnessVerificationPayloadSchema, UpstreamSIF07ClaimAuthorityResolutionSchema,
    UpstreamSIF07ClaimAuthorityState,
)
from apps.enterprise_document.services.post_write.faithfulness import FaithfulnessProjector, FaithfulnessIntegrityError
from apps.enterprise_document.services.post_write.shared_claim_support import ArtifactClaimTargetMaterializer
from backend.tests.test_bca014_i5_verifier_project_input_basis import fixture
from backend.tests.test_bca014_i5_verifier_v2_semantics import parse
from backend.tests.test_bca014_i5_verification_result_v2_ledger import commit
from backend.tests.test_multi_agent_v3_post_write_semantic_quality import _artifact_ref, _subject_hash


def exact_target(claim, subject_hash=None):
    return ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(
            state=UpstreamSIF07ClaimAuthorityState.PRESENT, claim_refs=(claim.claim_ref,),
            resolution_reason_ref="authority://i7-exact"),
        artifact_state_ref=_artifact_ref(), validation_subject_hash=subject_hash or _subject_hash(),
        semantic_version="claim-projection-v1", resolved_claim_records={claim.claim_ref.claim_id:claim})[0]


def material(tmp_path, relation="SUPPORTED", verdict="VERIFIED"):
    fx=fixture(tmp_path, support_relation=SupportRelation(relation))
    candidate=parse(fx, positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref]
        if verdict=="VERIFIED" else [], verdict=verdict)
    result=commit(fx,candidate).verification_result
    args=dict(validation_run_id="validation-run:i7",subject_hash=_subject_hash(),
        claim_target=exact_target(fx.claim),verification_result=result,source_snapshot_sha256="a"*64)
    return fx,args


@pytest.mark.parametrize("relation,verdict,state", [
    ("NOT_SUPPORTED","VERIFIED","PASS"), ("PARTIALLY_SUPPORTED","VERIFIED","PASS"),
    ("SUPPORTED","UNSUPPORTED","FAIL"), ("SUPPORTED","REJECTED","FAIL"),
    ("SUPPORTED","CONFLICTED","FAIL"), ("SUPPORTED","VERIFIED","PASS")])
def test_verdict_owns_truth(tmp_path,relation,verdict,state):
    fx,args=material(tmp_path,relation,verdict)
    before=fx.runtime.ledger_path.read_bytes()
    fact=FaithfulnessProjector.project(**args)
    assert fact.semantic_state.value==state and fact.producer_validator_version=="v3"
    payload=FaithfulnessVerificationPayloadSchema.model_validate(fact.payload)
    assert payload.verification_result_ref==args["verification_result"].verification_result_ref
    assert payload.verification_verdict==fx.runtime.resolve_verification_result(payload.verification_result_ref).verdict
    assert fx.runtime.ledger_path.read_bytes()==before
    assert not {"support_relation","claim_evidence_support_ref"}&set(fact.payload)


@pytest.mark.parametrize("change", ["missing_ref","missing_verdict","hash","duplicate_rules","unsorted_rules",
    "duplicate_codes","unsorted_codes","support_relation","claim_evidence_support_ref"])
def test_payload_strict(tmp_path,change):
    _,args=material(tmp_path)
    data=FaithfulnessProjector.project(**args).payload.copy()
    if change=="missing_ref": del data["verification_result_ref"]
    elif change=="missing_verdict": del data["verification_verdict"]
    elif change=="hash": data["source_snapshot_sha256"]="bad"
    elif change=="duplicate_rules": data["rule_ids"]=["a","a"]
    elif change=="unsorted_rules": data["rule_ids"]=["b","a"]
    elif change=="duplicate_codes": data["observation_codes"]=["a","a"]
    elif change=="unsorted_codes": data["observation_codes"]=["b","a"]
    else: data[change]="forbidden"
    with pytest.raises(ValidationError): FaithfulnessVerificationPayloadSchema.model_validate(data)


def test_payload_frozen_and_identity_inputs(tmp_path):
    fx,args=material(tmp_path)
    one=FaithfulnessProjector.project(**args)
    payload=FaithfulnessVerificationPayloadSchema.model_validate(one.payload)
    with pytest.raises(ValidationError): payload.source_snapshot_sha256="b"*64
    for change in (dict(validation_run_id="other"),dict(source_snapshot_sha256="b"*64)):
        other=FaithfulnessProjector.project(**(args|change))
        assert other.fact_id!=one.fact_id and other.fact_sha256!=one.fact_sha256
    target=exact_target(fx.claim,"b"*64)
    other=FaithfulnessProjector.project(**(args|dict(subject_hash="b"*64,claim_target=target)))
    assert other.fact_id!=one.fact_id and other.fact_sha256!=one.fact_sha256
    target=ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(state=UpstreamSIF07ClaimAuthorityState.PRESENT,
            claim_refs=(fx.claim.claim_ref,),resolution_reason_ref="authority://changed"),
        artifact_state_ref=_artifact_ref(),validation_subject_hash=args["subject_hash"],
        semantic_version="claim-projection-v-next",resolved_claim_records={fx.claim.claim_ref.claim_id:fx.claim})[0]
    assert FaithfulnessProjector.project(**(args|dict(claim_target=target))).fact_id!=one.fact_id
    # Re-verification through the canonical owner changes exact Result ref/verdict.
    head=fx.runtime.resolve_claim_head(fx.claim.claim_ref)
    fx.context=fx.context.model_copy(update={"authorization_ref":"verification://i7-next"})
    fx.invocation,_=fx.composer.build_input(fx.claim,fx.binding,fx.support,fx.context)
    second=commit(fx,parse(fx,verdict="REJECTED"),head=head).verification_result
    other=FaithfulnessProjector.project(**(args|dict(verification_result=second)))
    assert other.fact_id!=one.fact_id and other.fact_sha256!=one.fact_sha256
    changed=second.model_copy(update={"verdict":args["verification_result"].verdict,
        "positive_basis_refs":args["verification_result"].positive_basis_refs})
    assert FaithfulnessProjector.project(**(args|dict(verification_result=changed))).fact_id!=other.fact_id


def test_result_claim_mismatch_and_untyped_input(tmp_path):
    fx,args=material(tmp_path/'one')
    # Valid Result B is paired with a mechanically valid, separately hashed
    # ClaimTarget A; the projector must check exact refs, not text similarity.
    other_claim=fx.claim.model_copy(update={"claim_ref":fx.claim.claim_ref.model_copy(update={"claim_id":"claim://other"})})
    other_target=exact_target(other_claim)
    with pytest.raises(FaithfulnessIntegrityError,match="different ClaimTarget"):
        FaithfulnessProjector.project(**(args|dict(claim_target=other_target)))
    wrong=args["verification_result"].model_copy(update={"claim_ref":
        args["verification_result"].claim_ref.model_copy(update={"claim_id":"claim://different"})})
    with pytest.raises((ValueError,FaithfulnessIntegrityError)):
        FaithfulnessProjector.project(**(args|dict(verification_result=wrong)))
    for result in (args["verification_result"].model_dump(mode="json"),
        args["verification_result"].model_copy(update={"schema_version":"verification_result_record_v1"})):
        with pytest.raises(FaithfulnessIntegrityError,match="typed Result V2"):
            FaithfulnessProjector.project(**(args|dict(verification_result=result)))


def test_foreign_target_cannot_enter_main_v3(tmp_path):
    from backend.tests.test_multi_agent_v3_post_write_semantic_quality import _foreign_candidate
    _,args=material(tmp_path)
    target=ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(
            state=UpstreamSIF07ClaimAuthorityState.NOT_APPLICABLE,resolution_reason_ref="authority://foreign"),
        artifact_state_ref=_artifact_ref(),validation_subject_hash=_subject_hash(),semantic_version="foreign-v1",
        foreign_candidates=(_foreign_candidate(),))[0]
    with pytest.raises(FaithfulnessIntegrityError,match="exact SIF07"):
        FaithfulnessProjector.project(**(args|dict(claim_target=target)))
