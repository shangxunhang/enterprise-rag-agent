"""Real shared V3 output, generic Gate, FTH-03 independence and I6 auto-cutover."""
from contextlib import ExitStack
from unittest.mock import patch
import pytest
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.foundation import ValidationFactSchema
from apps.enterprise_document.services.post_write.faithfulness import FaithfulnessProjector, FaithfulnessCertaintyProjector
from apps.enterprise_document.services.agent_rag_v1.post_write_pipeline_identity import (
    pipeline_identity_material,semantic_pipeline_sha256,validation_run_identity)
from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import PostWriteClaimProjectionError
from model_gateway.fake_llm_client import FakeLLMClient
from backend.tests.bca014_i7_fixtures import make_section


def shared(fx):
    pw=fx["post_write"]; args=fx["arguments"]
    completion=pw.validation_store.resolve_completion(fx["outcome"][0])
    snapshot=pw.claim_quality_projection.resolve_committed_source_snapshot(post_write=pw,
        validation_run_id=completion.validation_run_id,subject_hash=completion.subject_hash,
        artifact_ref=args["artifact_ref"],scope=args["scope"])
    item=next(item for item in completion.items if item.expected_validation_item_ref.startswith("faithfulness:shared:"))
    facts=tuple(pw.validation_store.resolve_fact(ref) for ref in item.fact_refs)
    return completion,snapshot,facts


@pytest.mark.parametrize("relation,verdict,certainty,state,blocking",[
    ("NOT_SUPPORTED","VERIFIED","ALIGNED","PASS",False),
    ("PARTIALLY_SUPPORTED","VERIFIED","ALIGNED","PASS",False),
    ("SUPPORTED","UNSUPPORTED","ALIGNED","FAIL",True),
    ("SUPPORTED","REJECTED","ALIGNED","FAIL",True),
    ("SUPPORTED","CONFLICTED","ALIGNED","FAIL",True),
    ("SUPPORTED","VERIFIED","ESCALATED","PASS",True),
    ("SUPPORTED","VERIFIED","INDETERMINATE","PASS",True),
])
def test_actual_shared_cutover_and_generic_gate(tmp_path,relation,verdict,certainty,state,blocking):
    fx=make_section(tmp_path,relation,verdict,certainty)
    completion,snapshot,facts=shared(fx)
    claims=[fact for fact in facts if fact.contract_version==FaithfulnessProjector.FACT_CONTRACT_VERSION]
    count=sum(len(section.claim_records) for section in snapshot.sections)
    assert len(claims)==count>0 and len(facts)==count+1
    assert facts[0].contract_version=="faithfulness_body_accounting_v1"
    assert all(fact.producer_validator_version=="v3" for fact in facts)
    assert all(section.support_records for section in snapshot.sections)
    assert all(record.relation.value==relation for section in snapshot.sections for record in section.support_records)
    assert all(fact.semantic_state.value==state and fact.payload["schema_version"]=="faithfulness_verification_payload_v1"
        and fact.payload["source_snapshot_sha256"]==canonical_sha256(snapshot.model_dump(mode="json")) for fact in claims)
    assert all(not {"support_relation","claim_evidence_support_ref"}&set(fact.payload) for fact in claims)
    assert all(fact.contract_version!="faithfulness_fact_v1" for fact in facts)
    pw=fx["post_write"]
    fth=next(item for item in completion.items if item.expected_validation_item_ref.startswith("faithfulness:item:"))
    assert all(pw.validation_store.resolve_fact(ref).producer_validator_version=="v2" for ref in fth.fact_refs)
    gate=pw.gate_store.resolve_exact(fx["outcome"][1])
    condition=next(item for item in gate.quality_policy_projection.condition_results if item.dimension_id=="faithfulness")
    assert (condition.state.value!="SATISFIED")==blocking
    assert FaithfulnessCertaintyProjector.VALIDATOR_VERSION=="v2"


def test_empty_generation_has_only_body_accounting(tmp_path):
    _,snapshot,facts=shared(make_section(tmp_path,no_claims=True))
    assert all(not section.claim_records for section in snapshot.sections)
    assert len(facts)==1 and facts[0].contract_version=="faithfulness_body_accounting_v1"


def test_actual_pipeline_auto_changes_and_generation_stays(tmp_path):
    fx=make_section(tmp_path);pw=fx["post_write"];args=fx["arguments"]
    completion,snapshot,_=shared(fx)
    owner=pw.claim_quality_projection
    plan=owner.stage_plan()
    assert (plan.validator_id,plan.validator_version,plan.stage_plan_ref,plan.stage_plan_version)==(
        "post_write_faithfulness","v3","stage-plan:post-write-shared-faithfulness:v3","v3")
    base=plan.model_dump(mode="json",exclude={"stage_plan_hash"})
    assert plan.stage_plan_hash==canonical_sha256(base)
    new=pipeline_identity_material(pw,args["scope"])
    node=next(item for item in new["composition_topology"] if item["role"]=="claim_quality_projection")
    identities={row["contract_identity"] for row in node["contracts"]}
    assert {FaithfulnessProjector.FACT_CONTRACT_VERSION,FaithfulnessProjector.PROJECTION_CONTRACT_VERSION,
        FaithfulnessProjector.PAYLOAD_CONTRACT_VERSION}<=identities
    stage=plan.stages[0]
    old_stage=stage.model_copy(update=dict(stage_ref="post-write-shared-faithfulness-stage:v1",stage_version="v1",
        stage_output_schema_ref="schema://faithfulness-fact/v1",execution_policy_ref="policy://post-write-shared-faithfulness/v1",execution_policy_version="v1"))
    old_base=base|dict(validator_version="v1",stage_plan_ref="stage-plan:post-write-shared-faithfulness:v1",
        stage_plan_version="v1",stages=[old_stage.model_dump(mode="json")])
    old_plan=type(plan)(**old_base,stage_plan_hash=canonical_sha256(old_base))
    generation=owner.build_verification_generation(snapshot=snapshot,run_id=args["run_id"],scope=args["scope"],target_ref=args["target_ref"])
    with patch.object(owner,"stage_plan",return_value=old_plan):
        old=pipeline_identity_material(pw,args["scope"])
        same=owner.build_verification_generation(snapshot=snapshot,run_id=args["run_id"],scope=args["scope"],target_ref=args["target_ref"])
    assert same==generation and semantic_pipeline_sha256(old)!=semantic_pipeline_sha256(new)
    def identity(material):
        return validation_run_identity(subject_hash=completion.subject_hash,quality_contract_ref=args["quality_contract_ref"],
            semantic_pipeline_sha256=semantic_pipeline_sha256(material),verification_generation_sha256=generation.verification_generation_sha256)
    assert identity(old)!=identity(new)==completion.validation_run_id
    # Each changed stage field participates in the canonical plan hash.
    for field in ("stage_ref","stage_version","stage_output_schema_ref","execution_policy_ref","execution_policy_version"):
        other=stage.model_copy(update={field:"identity://other"})
        assert canonical_sha256(base|dict(stages=[other.model_dump(mode="json")]))!=plan.stage_plan_hash


def test_replay_no_provider_or_writes_and_payload_spoof_rejected(tmp_path):
    fx=make_section(tmp_path);pw=fx["post_write"]
    completion,snapshot,facts=shared(fx)
    forbidden=AssertionError("replay must be read only")
    with ExitStack() as stack:
        for owner,name in ((FakeLLMClient,"generate"),(pw.validation_store,"put_fact_if_absent_or_same"),
            (pw.validation_store,"put_completion_if_absent_or_same"),(pw.validation_intermediate_runtime,"commit"),
            (pw.gate_service,"evaluate"),(pw.claim_quality_projection.claim_runtime,"commit_verification"),
            (pw.claim_quality_projection.support.runtime,"commit_support")):
            stack.enter_context(patch.object(owner,name,side_effect=forbidden))
        assert pw._complete_validation(**fx["arguments"],committed_gate_ref=fx["outcome"][1])==fx["outcome"]
    fact=facts[1];base=fact.model_dump(mode="json",exclude={"fact_sha256"})
    base["payload"]=base["payload"]|dict(verification_verdict="REJECTED")
    spoof=ValidationFactSchema(**base,fact_sha256=canonical_sha256(base))
    original=pw.validation_store.resolve_fact
    with patch.object(pw.validation_store,"resolve_fact",side_effect=lambda ref:spoof if ref.fact_id==fact.fact_id else original(ref)):
        with pytest.raises(PostWriteClaimProjectionError,match="facts differ"):
            pw.claim_quality_projection.resolve_committed_source_snapshot(post_write=pw,
                validation_run_id=completion.validation_run_id,subject_hash=completion.subject_hash,
                artifact_ref=fx["arguments"]["artifact_ref"],scope=fx["arguments"]["scope"])


@pytest.mark.parametrize("field,value",[("validator_id","other"),("validator_version","v1"),
    ("stage_plan_ref","stage-plan:post-write-shared-faithfulness:v1"),("stage_plan_version","v1"),
    ("input_hash","b"*64),("subject_hash","b"*64),("validation_run_id","wrong-run")])
def test_shared_replay_requires_exact_v3_execution(tmp_path,field,value):
    fx=make_section(tmp_path);pw=fx["post_write"];args=fx["arguments"]
    completion,_,_=shared(fx)
    owner=pw.claim_quality_projection
    original=pw.validation_store.resolve_validator_execution_record
    def corrupt(ref,**options):
        record=original(ref,**options)
        if record.validator_id==FaithfulnessProjector.VALIDATOR_ID and record.validator_version=="v3":
            return record.model_copy(update={field:value})
        return record
    with patch.object(pw.validation_store,"resolve_validator_execution_record",side_effect=corrupt):
        with pytest.raises(PostWriteClaimProjectionError,match="differs|differ"):
            owner.complete_item(post_write=pw,validation_run_id=completion.validation_run_id,
                subject_hash=completion.subject_hash,artifact_ref=args["artifact_ref"],scope=args["scope"],
                source_sections=args["source_sections"])


def test_fresh_shared_projection_has_zero_model_support_verifier_calls(tmp_path):
    fx=make_section(tmp_path);pw=fx["post_write"];args=fx["arguments"]
    completion,_,_=shared(fx)
    old=pw.validation_store.resolve_run_exact(completion.validation_run_id)
    from apps.enterprise_document.schemas.post_write.foundation import ValidationRunState
    new=type(old).model_validate(old.model_dump(mode="json")|dict(
        validation_run_id="validation-run:i7-fresh-projection",execution_state=ValidationRunState.CREATED,
        state_version=1,started_at_epoch=None,finished_at_epoch=None))
    owner=pw.claim_quality_projection
    with pw.database.run_write_guard_context(args["run_id"]):
        pw.validation_lifecycle.create(new)
        pw.validation_lifecycle.start(new.validation_run_id, expected_state_version=1,
            transition_command_id="validation-start:i7-fresh-projection")
        with ExitStack() as stack:
            provider=stack.enter_context(patch.object(FakeLLMClient,"generate",side_effect=AssertionError("no model gateway")))
            support=stack.enter_context(patch.object(owner.support.runtime,"evaluate_support_candidate",side_effect=AssertionError("no Support evaluator")))
            verifier=stack.enter_context(patch.object(owner.verifier,"verify_claims",side_effect=AssertionError("no Verifier")))
            options=dict(post_write=pw,validation_run_id=new.validation_run_id,subject_hash=completion.subject_hash,
                artifact_ref=args["artifact_ref"],scope=args["scope"],source_sections=args["source_sections"])
            first=owner.complete_item(**options)
            assert first==owner.complete_item(**options)
            assert provider.call_count==support.call_count==verifier.call_count==0


@pytest.mark.parametrize("kind",["missing","duplicate","extra","no_claim_extra"])
def test_keyed_result_cardinality_fails_closed(tmp_path,kind):
    fx=make_section(tmp_path);completion,snapshot,_=shared(fx)
    owner=fx["post_write"].claim_quality_projection
    section=snapshot.sections[0]
    if kind=="missing": changes=dict(verification_records=())
    elif kind=="duplicate": changes=dict(verification_records=section.verification_records*2)
    elif kind=="no_claim_extra": changes=dict(claim_records=())
    else:
        result=section.verification_records[0]
        other=result.model_copy(update={"claim_ref":result.claim_ref.model_copy(update={"claim_id":"claim:foreign"})})
        changes=dict(verification_records=section.verification_records+(other,))
    invalid=snapshot.model_copy(update=dict(sections=(section.model_copy(update=changes),)))
    with pytest.raises(PostWriteClaimProjectionError,match="duplicate|exact sets differ"):
        owner._facts(invalid,validation_run_id=completion.validation_run_id,subject_hash=completion.subject_hash)
