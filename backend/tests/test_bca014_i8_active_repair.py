"""Actual repair reservation commits; late authorization loses to barrier."""
import pytest
from backend.tests.test_bca014_i8_finalization_recovery import target_fixture
from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import RunLifecycleStatus as RS
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from apps.enterprise_document.schemas.agent_rag_v1.repair import RepairIssueBindingSchema, ExactRepairBasisRefSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import SectionPlanSourceRefSchema
from apps.enterprise_document.services.agent_rag_v1.repair_authorization_runtime import (
    ParagraphRepairAuthorizationStore, ParagraphRepairAuthorizationRuntime)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import LifecycleCurrentnessError


def test_actual_reserve_then_barrier_rejects_authorization_and_finalizer(tmp_path,monkeypatch):
    post,request=target_fixture(tmp_path,monkeypatch);enter=post._enter_finalization
    source=request['section_execution_results'][0];projection=post.claim_quality_projection
    store=ParagraphRepairAuthorizationStore(post.runs_dir/'i8-repair-authorization.jsonl',
        run_commit_guard=post.lifecycle_service.assert_run_committable,require_run_commit_guard=True)
    manager=post.budget_manager_provider(request['task_id'],request['run_id'])
    repair=ParagraphRepairAuthorizationRuntime(lifecycle_service=post.lifecycle_service,
        paragraph_runtime=projection.paragraph_runtime,verification_runtime=projection.claim_runtime,
        budget_manager=manager,store=store)
    put=store.put_if_absent_or_same;observed=[]
    def enter_after_reservation(run_id,barrier):
        args=post._i8_test_fixture['arguments']
        snapshot=projection.resolve_sources(post_write=post,artifact_ref=args['artifact_ref'],scope=args['scope'],
            source_sections=(source,))
        claim=snapshot.sections[0].claim_records[0];result=snapshot.sections[0].verification_records[0]
        binding=projection.claim_runtime.resolve_binding_facts(result.binding_facts_ref)
        support=projection.support.runtime.resolve_support(result.claim_evidence_support_ref)
        context=ClaimOperationContextSchema(active_lineage=post.lifecycle_service.resolve_active_lineage(
            run_id,'section:'+source.section.section_id),authorization_ref='verification:i8-repair',operation_purpose='claim-verification')
        invocation,_=projection.verifier.build_input(claim,binding,support,context)
        candidate=projection.verifier.parse_candidate({'verdict':'UNSUPPORTED','positive_basis_refs':[],
            'rationale':'I8 pending repair race','issues':[{'issue_code':'I8_REPAIR','message':'Repair race input',
                'severity':'MAJOR','source_refs':[invocation.allowed_authority_refs[0]]}]},
            invocation=invocation,occurrence='verification:i8-repair',receipt_ref='trace:i8-repair')[0]
        committed=projection.claim_runtime.commit_verification(claim.claim_ref,binding.binding_facts_ref,support.support_ref,
            candidate,projection.claim_runtime.resolve_claim_head(claim.claim_ref),context)
        def losing_put(record):
            assert manager.canonical_state().active_reserved.repair_attempts==1
            observed.append(enter(run_id,barrier))
            return put(record)
        monkeypatch.setattr(store,'put_if_absent_or_same',losing_put)
        with pytest.raises(LifecycleCurrentnessError):
            repair.authorize(task_id=request['task_id'],run_id=run_id,section_id=source.section.section_id,
                paragraph_logical_ref=claim.paragraph_version_ref.logical_ref,manifest_ref=source.section_paragraph_manifest.manifest_ref,
                issue_bindings=(RepairIssueBindingSchema(issue_ref=committed.issue_records[0].issue_ref,
                    expected_issue_head_revision=committed.issue_heads[0].issue_revision),),
                verification_result_refs=(committed.verification_result.verification_result_ref,),
                semantic_repair_basis_refs=(ExactRepairBasisRefSchema(authority_kind='POST_WRITE_CONTROL_DECISION',
                    exact_ref=barrier.final_control_decision_ref.source_scope_ref,
                    exact_hash=barrier.final_control_decision_ref.exact_ref_sha256),),
                section_plan_ref=SectionPlanSourceRefSchema(plan_version_ref=source.canonical_plan_binding.plan_version_ref,
                    section_id=source.section.section_id),evidence_package_ref=source.active_evidence_package_binding.version_ref,
                repair_policy_id='i8-race-policy',repair_policy_version='v1')
        assert not store.path.exists()
        return observed[0]
    monkeypatch.setattr(post,'_enter_finalization',enter_after_reservation)
    with pytest.raises(Exception):post.complete_target_post_write(**request)
    assert len(observed)==1 and observed[0].status is RS.FINALIZING
    assert post.lifecycle_service.load_run_lifecycle(request['run_id']).status is RS.RUNNING
    assert manager.canonical_state().active_reserved.repair_attempts==1
    assert not post.assessment_store.path.exists()
