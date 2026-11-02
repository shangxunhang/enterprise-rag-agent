"""Real all-validator -> COMPLETED -> read-only fence -> Gate ordering."""
from contextlib import ExitStack
from unittest.mock import patch
import pytest
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_claim_projection import PostWriteClaimProjectionError
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteCompositionError
from backend.tests.bca014_i7_fixtures import make_section
from backend.tests.test_bca014_i7_faithfulness_v3_pipeline import shared
from model_gateway.fake_llm_client import FakeLLMClient
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import TargetClaimVerifierError


def reverify(fx):
    pw=fx["post_write"];args=fx["arguments"];projection=pw.claim_quality_projection
    snapshot=projection.resolve_sources(post_write=pw,artifact_ref=args["artifact_ref"],scope=args["scope"],
        source_sections=args["source_sections"],assembly_input_manifest_ref=args.get("assembly_input_manifest_ref"))
    section=snapshot.sections[0];claim=section.claim_records[0];result=section.verification_records[0]
    binding=projection.claim_runtime.resolve_binding_facts(result.binding_facts_ref)
    support=projection.support.runtime.resolve_support(result.claim_evidence_support_ref)
    head=projection.claim_runtime.resolve_claim_head(claim.claim_ref)
    context=ClaimOperationContextSchema(active_lineage=projection.claim_runtime.lifecycle_service.resolve_active_lineage(
        args["run_id"],"section:"+claim.section_id),authorization_ref="verification://i7-stale",
        operation_purpose="claim-verification")
    invocation,_=projection.verifier.build_input(claim,binding,support,context)
    candidate=projection.verifier.parse_candidate({"verdict":"UNSUPPORTED","positive_basis_refs":[],
        "rationale":"I7 legal controlled re-verification","issues":[]},invocation=invocation,
        occurrence="verification://i7-stale",receipt_ref="artifact://i7-stale")[0]
    return projection.claim_runtime.commit_verification(claim.claim_ref,binding.binding_facts_ref,support.support_ref,candidate,head,context)


@pytest.mark.parametrize("stale",["head_result","policy"])
def test_stale_after_validation_complete_before_gate_fails_closed(tmp_path,stale):
    fx=make_section(tmp_path);pw=fx["post_write"];owner=pw.claim_quality_projection
    original=pw.validation_lifecycle.complete
    seen=[]
    with ExitStack() as stack:
        stack.enter_context(patch.object(type(owner),"CLAIM_PROJECTION_CONTRACT_VERSION","claim-projection-i7-fresh-fixture"))
        def complete(*args,**kwargs):
            run=original(*args,**kwargs)
            assert run.execution_state.value=="COMPLETED"
            seen.append(run.validation_run_id)
            if stale=="head_result": reverify(fx)
            else: stack.enter_context(patch.object(owner.verifier,"POLICY_VERSION","i7-current-policy-changed"))
            return run
        stack.enter_context(patch.object(pw.validation_lifecycle,"complete",side_effect=complete))
        gate=stack.enter_context(patch.object(pw.gate_service,"evaluate",side_effect=AssertionError("stale Gate forbidden")))
        control=stack.enter_context(patch.object(pw,"_control",side_effect=AssertionError("stale Control forbidden")))
        with pw.database.run_write_guard_context(fx["arguments"]["run_id"]):
            with pytest.raises((PostWriteClaimProjectionError, TargetClaimVerifierError)):
                pw._complete_validation(**fx["arguments"])
        assert len(seen)==1 and gate.call_count==0 and control.call_count==0
        assert pw.validation_store.resolve_run_exact(seen[0]).execution_state.value=="COMPLETED"
    # The original Gate is immutable history; the new stale run has no Gate.
    with pw.database.read_transaction() as connection:
        rows=connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names=[row[0] for row in rows if "gate" in row[0]]
        assert names
        for name in names:
            columns={row[1] for row in connection.execute('PRAGMA table_info('+name+')').fetchall()}
            if "validation_run_id" in columns:
                assert connection.execute('SELECT COUNT(*) FROM '+name+' WHERE validation_run_id=?',(seen[0],)).fetchone()[0]==0


def test_terminal_old_gate_rejects_legal_result_movement(tmp_path):
    fx=make_section(tmp_path);pw=fx["post_write"]
    reverify(fx)
    with patch.object(pw.validation_lifecycle,"create",side_effect=AssertionError("no replay repair")), \
         patch.object(pw.gate_service,"evaluate",side_effect=AssertionError("no new Gate")), \
         patch.object(FakeLLMClient,"generate",side_effect=AssertionError("no provider")):
        with pytest.raises(ValueError):
            pw._complete_validation(**fx["arguments"],committed_gate_ref=fx["outcome"][1])


def test_existing_gate_rechecks_after_validator_replay(tmp_path):
    from apps.enterprise_document.services.post_write.execution import ValidationCompletionBuilder
    fx=make_section(tmp_path);pw=fx["post_write"]
    original=ValidationCompletionBuilder.build
    moved=[]
    def build(**options):
        completion=original(**options)
        reverify(fx)
        moved.append(True)
        return completion
    with patch.object(ValidationCompletionBuilder,"build",side_effect=build), \
         patch.object(pw.gate_store,"resolve_exact",side_effect=AssertionError("stale existing Gate must not be accepted")), \
         patch.object(FakeLLMClient,"generate",side_effect=AssertionError("no provider")), \
         patch.object(pw.validation_store,"put_fact_if_absent_or_same",side_effect=AssertionError("no replay Fact writes")):
        with pytest.raises(PostWriteClaimProjectionError,match="current active"):
            pw._complete_validation(**fx["arguments"],committed_gate_ref=fx["outcome"][1])
    assert moved==[True]


def test_fence_reads_only_and_generation_equality_is_required(tmp_path):
    fx=make_section(tmp_path);pw=fx["post_write"];args=fx["arguments"]
    completion,_,_=shared(fx)
    generation=pw._verification_generation(**{key:args.get(key) for key in (
        "run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})
    options=dict(generation=generation,validation_run_id=completion.validation_run_id,subject_hash=completion.subject_hash,
        **{key:args.get(key) for key in ("run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})
    claim=pw.claim_quality_projection.claim_runtime
    before=claim.ledger_path.read_bytes()
    with ExitStack() as stack:
        for owner,name in ((claim,"commit_claim_records"),(claim,"commit_verification"),
            (pw.claim_quality_projection.support.runtime,"commit_support"),(FakeLLMClient,"generate"),
            (pw.validation_store,"put_fact_if_absent_or_same"),(pw.validation_intermediate_runtime,"commit")):
            stack.enter_context(patch.object(owner,name,side_effect=AssertionError("fence must only read")))
        pw._assert_pre_gate_verification_current(**options)
        assert claim.ledger_path.read_bytes()==before
        # Generation comparison is independent of Snapshot equality.
        with pytest.raises(TargetPostWriteCompositionError,match="generation is stale"):
            pw._assert_pre_gate_verification_current(**(options|dict(generation=generation.model_copy(update={"verification_generation_sha256":"f"*64}))))
