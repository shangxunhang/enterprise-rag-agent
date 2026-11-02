"""345 production Grounding and historical contract guards.

The original carrier guards remain; production tests use controlled fake providers.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.post_write.foundation import (
    ValidationCompletionItemSchema, ValidationIntermediateKind,
)
from apps.enterprise_document.schemas.post_write.quality import (
    CitationClaimAssociationSchema, CitationGroundingPayloadSchema,
)
from apps.enterprise_document.services.post_write.execution import ValidationIntermediateRuntime
from core.hashing import canonical_sha256
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from backend.tests.test_multi_agent_v3_post_write_semantic_quality import _artifact_ref
from backend.tests.test_post_write_validation_foundation import _created_run


def test_gnd345_07_committed_indeterminate_association_has_no_legal_completion_carrier(tmp_path):
    _database, store, _lifecycle, run = _created_run(tmp_path)
    association = CitationClaimAssociationSchema(
        association_ref="association://uncertain", artifact_state_ref=_artifact_ref(),
        occurrence_ref="occurrence://one", candidate_claim_target_ids=(),
        associated_claim_target_ids=(), association_state="INDETERMINATE",
        materializer_id="citation-association", materializer_version="v1",
        association_hash=canonical_sha256({"association": "uncertain"}),
    )
    intermediate_runtime = ValidationIntermediateRuntime(store)
    ref = intermediate_runtime.commit(validation_run_id=run.validation_run_id,
        producer_ref="post_write_citation_grounding", target_ref="citation:item:exact-artifact",
        typed_intermediate=association, intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,
        occurred_at_epoch=0.0)
    assert intermediate_runtime.resolve_typed(ref, CitationClaimAssociationSchema) == association
    assert association.association_state.value == "INDETERMINATE"
    # Persisting an association alone does not implicitly attach the approved
    # explicit uncertainty carrier to Completion or its execution accounting.
    with pytest.raises(ValidationError, match="INDETERMINATE requires canonical Fact refs or indeterminate proof refs"):
        ValidationCompletionItemSchema(expected_validation_item_ref="citation:item:exact-artifact",
            required=True, state="INDETERMINATE", fact_refs=())
    assert "intermediate_refs" not in ValidationCompletionItemSchema.model_fields
    assert "target_coverage_refs" not in ValidationCompletionItemSchema.model_fields


@pytest.mark.parametrize("version", ["citation_grounding_payload_v1", "citation_grounding_payload_v2"])
def test_gnd345_07_grounding_projection_cannot_invent_support_for_association(version):
    # Even with synthetic target/Claim placeholders, the owner correctly rejects
    # absent Support. Production has neither target nor Claim for this case.
    with pytest.raises(ValidationError, match="requires joint support ref"):
        CitationGroundingPayloadSchema(schema_version=version,
            grounding_target_ref="test-placeholder://not-a-materialized-target",
            claim_target_id="test-placeholder://not-an-associated-claim",
            occurrence_refs=("occurrence://one",), citation_target_refs=("citation://one",),
            joint_support_ref=None, grounding_relation="INDETERMINATE")


from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import ASSOCIATION_CONTRACT
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
import test_multi_agent_v3_claim_materialization_mainline as extraction_tests
import test_multi_agent_v3_claim_support_mainline as support_tests
import test_multi_agent_v3_workflow_recovery as workflow_tests

TEXT = "应依据已核实的项目资料制定建设方案。"


def _association_input(request):
    for message in request.messages:
        try:
            text=message.get("content", "")
            payload,_end=json.JSONDecoder().raw_decode(text[text.index("{"):])
        except (ValueError,TypeError): continue
        if isinstance(payload,dict) and "citation_association_input" in payload:
            return payload["citation_association_input"]
    raise AssertionError("association input missing from actual model messages")


def _grounding_run(root,scenario="supported",publication_window=None,*,project_override=None,rag_provider=None,large_budget=False,validation_hook=None,allow_failure=False):
    task_id,run_id="task-gnd345-"+scenario,"run-gnd345-"+scenario
    project=project_override or workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input(task_id))
    calls,observations,arguments=[],[],[]
    broad_refs=[]
    original_generate=FakeLLMClient.generate
    original_complete=TargetPostWriteComposition._complete_validation
    def provider(client,request):
        calls.append(request.model_copy(deep=True)); response=original_generate(client,request)
        contract=request.extra.get("response_contract")
        if request.extra.get("call_purpose")=="scheme_section_generation":
            token="[C99]" if scenario=="invalid" else "" if scenario=="empty" else "[C1]"
            if scenario in {"joint-partial","joint-mixed","repeat-source"}:token="[C1][C1]" if scenario=="repeat-source" else "[C1][C2]"
            if scenario=="broad-vs-cited":token="[C2]"
            return response.model_copy(update={"content":TEXT*5+token},deep=True)
        if contract==extraction_tests.CONTRACT:
            units=extraction_tests._task_contract(request)["coverage_units"]
            state="NO_APPLICABLE_FACTUAL_CLAIM" if scenario=="no-claims" else "INDETERMINATE" if scenario=="unknown-coverage" else "FACTUAL_CLAIMS_MATERIALIZED"
            claims=[] if scenario in {"no-claims","unknown-coverage"} else [{"quote":TEXT,"occurrence_index":index,"claim_type":"general_method"} for index in range(2 if scenario=="multi-claim" else 1)]
            return response.model_copy(update={"content":json.dumps({"units":[{"unit_id":unit["unit_id"],"accounting_state":state,"claims":claims} for unit in units]},ensure_ascii=False)},deep=True)
        if contract==support_tests.SUPPORT_RESPONSE_CONTRACT:
            refs=[row["exact_source_ref"] for row in support_tests._json_message(request,"support_evidence")["support_evidence"]]
            relation=scenario.upper() if scenario.upper() in {"SUPPORTED","PARTIALLY_SUPPORTED","NOT_SUPPORTED","CONTRADICTED","INDETERMINATE"} else "SUPPORTED"
            if scenario in {"joint-partial","joint-mixed","broad-vs-cited"}:
                if len(refs)>1:broad_refs[:]=refs
                relation="SUPPORTED" if len(refs)>1 else "PARTIALLY_SUPPORTED" if scenario=="joint-partial" else "SUPPORTED" if scenario=="joint-mixed" and refs==broad_refs[:1] else "NOT_SUPPORTED"
            return response.model_copy(update={"content":json.dumps({"relation":relation,"supporting_refs":refs[:1] if relation in {"SUPPORTED","PARTIALLY_SUPPORTED"} else [],"contradicting_refs":refs[:1] if relation=="CONTRADICTED" else [],"certainty_relation":"ALIGNED","certainty_reason_codes":["TEST_CERTAINTY_ALIGNED"]})},deep=True)
        if scenario=="fallback" and contract=="criterion_support_evaluator_v1":
            value=json.loads(response.content); value.update(support_state="not_supported",evidence_ids=[],conflicts=[])
            return response.model_copy(update={"content":json.dumps(value)},deep=True)
        if scenario=="broad-vs-cited" and contract=="canonical_claim_verifier_v2":
            # This scenario supplies its independent validity oracle explicitly;
            # generic fake Support never implies a VERIFIED Result.
            shared=support_tests._json_message(request,"shared_support")["shared_support"]
            assert shared["relation"]=="SUPPORTED" and shared["supporting_refs"]
            allowed=support_tests._json_message(request,"allowed_authority_refs")["allowed_authority_refs"]
            basis=shared["supporting_refs"][0]
            assert basis in allowed
            return response.model_copy(update={"content":json.dumps({"verdict":"VERIFIED",
                "positive_basis_refs":[basis],"rationale":"Controlled validity oracle for the complete broad source set.",
                "issues":[]})},deep=True)
        if contract==ASSOCIATION_CONTRACT:
            material=_association_input(request)
            ids=[row["claim_target_id"] for row in material["candidate_claim_targets"]]
            state="INDETERMINATE" if scenario in {"uncertain","unknown-coverage"} else "NO_APPLICABLE_CLAIM" if scenario=="no-claims" else "ASSOCIATED"
            rows=[{"occurrence_ref":ref,"state":state,"associated_claim_target_ids":ids if state=="ASSOCIATED" else []} for ref in material["eligible_occurrence_refs"]]
            if scenario=="bad-json":content="{bad"
            elif scenario=="omitted":content=json.dumps({"associations":[]})
            elif scenario=="duplicate":content=json.dumps({"associations":rows+rows})
            elif scenario=="foreign-claim":
                rows[0]["associated_claim_target_ids"]=["foreign-claim"]
                content=json.dumps({"associations":rows})
            else:content=json.dumps({"associations":rows})
            return response.model_copy(update={"content":content},deep=True)
        return response
    def complete(composition,**kwargs):
        result=validation_hook(composition,kwargs,original_complete) if validation_hook else original_complete(composition,**kwargs)
        completion=composition.validation_store.resolve_completion(result[0])
        item=next(row for row in completion.items if row.expected_validation_item_ref.startswith("citation:item:"))
        facts=[composition.validation_store.resolve_fact(ref) for ref in item.fact_refs]
        observations.append((kwargs["scope"].value,item,facts,completion,result[1]))
        arguments.append(kwargs)
        before=len(calls)
        import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citations
        def forbidden(*args,**kw):raise AssertionError("committed replay attempted cognition/scanning")
        with patch.object(citations,"scan_citation_markers",forbidden), patch.object(composition.citation_grounding,"_associate",forbidden), patch.object(composition.citation_grounding.support.runtime,"evaluate_support_candidate",forbidden):
            assert original_complete(composition,**kwargs)==result
        assert len(calls)==before
        return result
    settings=replace(get_settings(),data_root=root,default_model_name="fake_llm",supervisor_model_name="fake_llm",enable_llm_routing=False,
        trace_enabled=True,data_capture_enabled=True,gate_enforcement_mode="strict",gate_recovery_rounds_limit=0)
    factory=workflow_tests._W13InspectingFactory()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ,{"USE_REAL_RAG_TOOL":"false","FAKE_RAG_SCENARIO":"default","FAKE_LLM_SCENARIO":"default","ENABLE_AGENT_SELF_RAG":"false","ENABLE_SEMANTIC_GATE":"false"}))
        if project_override is not None:
            stack.enter_context(patch.object(workflow_tests,"_w13_single_section_project_input",return_value=project))
        if large_budget:
            root.mkdir(parents=True,exist_ok=True)
            stack.enter_context(patch.dict(os.environ,{"GROUNDED_GENERATION_POLICY_FILE":str(workflow_tests._w15_parallel_budget_policy_file(root))}))
        if rag_provider is not None:
            from tools.fake_rag_tool import FakeRAGTool
            stack.enter_context(patch.object(FakeRAGTool,"run",rag_provider))
        stack.enter_context(patch.object(FakeLLMClient,"generate",provider))
        stack.enter_context(patch.object(TargetPostWriteComposition,"_complete_validation",complete))
        if publication_window is not None:
            original_publish=TargetPostWriteComposition._publish_target_release
            def interrupted(composition,**kwargs):
                if publication_window=="before-publisher":raise RuntimeError("injected SUCCESS-before-Release interruption")
                original_put=composition.release_store.put_json
                def put(value,**options):
                    if options.get("artifact_type")=="post_write_release_manifest":
                        raise RuntimeError("injected SUCCESS-before-Release manifest interruption")
                    return original_put(value,**options)
                with patch.object(composition.release_store,"put_json",put):return original_publish(composition,**kwargs)
            stack.enter_context(patch.object(TargetPostWriteComposition,"_publish_target_release",interrupted))
        summary=MainlineApplicationService(supervisor_factory=factory).run(project_root=Path(__file__).resolve().parents[2],task_id=task_id,run_id=run_id,
            user_input=project.user_query,output_root=root,clean_existing=True,settings=settings,project_input=project.model_dump(mode="json"),allow_demo_defaults=False,enable_agent_self_rag=False)
    if not allow_failure and len(observations)!=len(project.generation_requirements.required_sections)+1:
        top=workflow_tests._latest_w13_top_level_payload(factory.engines[0])
        raise AssertionError(top.terminal_error.model_dump(mode="json") if top.terminal_error else summary)
    engine=factory.engines[0]
    (root/"production-grounding-evidence.json").write_text(json.dumps({"scenario":scenario,
        "calls":[{"model_call_id":request.model_call_id,"task_id":request.task_id,"run_id":request.run_id,
            "purpose":request.extra.get("call_purpose"),"contract":request.extra.get("response_contract")} for request in calls],
        "budget":engine.budget_runtime(task_id,run_id).canonical_state().model_dump(mode="json"),
        "outcomes":[{"scope":scope,"item":item.model_dump(mode="json")} for scope,item,*_ in observations]},indent=2),encoding="utf-8")
    return engine,calls,observations,arguments,summary


@pytest.mark.parametrize("scenario,expected",[("supported","PASS"),("uncertain","INDETERMINATE")])
def test_actual_factory_canonical_claim_citation_grounding(tmp_path,scenario,expected):
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path,scenario)
    assert {row[0] for row in observations}=={"SECTION","DOCUMENT"}
    assert len([request for request in calls if request.extra.get("response_contract")==ASSOCIATION_CONTRACT])==2
    assert len([request for request in calls if request.extra.get("response_contract")==support_tests.SUPPORT_RESPONSE_CONTRACT])==1
    for scope,item,facts,completion,gate_ref in observations:
        assert item.state.value==expected, item
        assert len(facts)==1
        if scenario=="uncertain":
            assert facts[0].payload["uncertainty_kind"]=="ASSOCIATION_INDETERMINATE"
            assert "joint_support_ref" not in facts[0].payload
        else:assert facts[0].payload["joint_support_ref"]
    for request in calls:
        if request.extra.get("response_contract")==ASSOCIATION_CONTRACT:
            assert _association_input(request)["candidate_claim_targets"]
            assert request.extra["llm_context_summary"]["managed"]


@pytest.fixture(scope="module",params=("supported","uncertain","PARTIALLY_SUPPORTED","NOT_SUPPORTED","CONTRADICTED","INDETERMINATE","no-claims","unknown-coverage","invalid","empty","bad-json","omitted","duplicate","foreign-claim"))
def grounding_mainline(tmp_path_factory,request):
    return request.param,_grounding_run(tmp_path_factory.mktemp("gnd345-"+request.param),request.param)


def test_grounding_outcomes_and_terminal_reopen_are_exact(grounding_mainline):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    import apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection as citations
    scenario,(engine,calls,observations,arguments,summary)=grounding_mainline
    expected="NOT_APPLICABLE" if scenario in {"no-claims","invalid","empty"} else "INDETERMINATE" if scenario in {"uncertain","unknown-coverage","INDETERMINATE"} else "FAIL" if scenario in {"PARTIALLY_SUPPORTED","NOT_SUPPORTED","CONTRADICTED"} else "TECHNICAL_FAILURE" if scenario in {"bad-json","omitted","duplicate","foreign-claim"} else "PASS"
    for scope,item,facts,completion,gate_ref in observations:
        assert item.state.value==expected,item
        if expected in {"TECHNICAL_FAILURE","NOT_APPLICABLE"}:assert not facts
        if scenario=="INDETERMINATE":assert facts[0].payload["joint_support_ref"] and "uncertainty_kind" not in facts[0].payload
    composition=engine.stage_service.target_post_write_composition
    reopened=integrity_tests._reopen(engine)
    before=_grounding_durable_state(engine)
    top=workflow_tests._latest_w13_top_level_payload(engine)
    budget=engine.budget_runtime(top.task_id,top.run_id).canonical_state()
    def forbidden(*args,**kwargs):raise AssertionError("terminal replay attempted new semantics")
    with _forbid_terminal_business_writes(reopened), ExitStack() as stack:
        stack.enter_context(patch.object(citations,"scan_citation_markers",forbidden))
        stack.enter_context(patch.object(citations,"assemble_document_citation_projection",forbidden))
        stack.enter_context(patch.object(reopened.citation_grounding,"_associate",forbidden))
        stack.enter_context(patch.object(reopened.citation_grounding.support.runtime,"evaluate_support_candidate",forbidden))
        stack.enter_context(patch.object(FakeLLMClient,"generate",forbidden))
        for options,observation in zip(arguments,observations):
            ref,_gate=reopened._complete_validation(**options,committed_gate_ref=observation[4])
            assert reopened.validation_store.resolve_completion(ref)==observation[3]
        replay=reopened.complete_target_post_write(task_id=top.task_id,run_id=top.run_id,document_id=top.document_id,
            project_input=top.project_input,section_execution_results=tuple(top.section_execution_results))
    assert replay.published_release.reuse_state.value=="EXACT_REQUEST_REPLAY"
    assert replay.published_release.manifest==top.target_release_publication.manifest
    assert replay.run_completion_assessment_ref==top.target_run_completion_assessment_ref
    assert replay.final_document.artifact_state_ref==top.target_release_publication.manifest.document_artifact_ref
    assert replay.final_document.control_decision_ref==top.target_release_publication.manifest.post_write_control_decision_ref
    assert before==_grounding_durable_state(engine)
    assert budget==engine.budget_runtime(top.task_id,top.run_id).canonical_state()


def _uncertainty_carrier(tmp_path,kind="ASSOCIATION_INDETERMINATE",blocking=False):
    import test_post_write_delivery_gate as gate_tests
    import test_multi_agent_v3_post_write_semantic_quality as quality_tests
    from apps.enterprise_document.schemas.post_write.foundation import (
        ValidationFactFamily,ValidationSemanticState,
        ExpectedItemExecutionAccountingSchema,ValidatorExecutionRecordSchema,
    )
    from apps.enterprise_document.schemas.post_write.gate import FactPolicyRuleSchema,QualityPolicyEffect,RequiredDimensionConditionSchema
    from apps.enterprise_document.services.post_write import ValidationRunFactory,ValidationRunLifecycleService,ValidationCompletionBuilder
    from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
    from apps.enterprise_document.services.post_write.artifact_state import ArtifactValidationViewResolver,ArtifactStateService
    from apps.enterprise_document.services.post_write.persistence import ArtifactStateStore
    from apps.enterprise_document.services.post_write.citation import CitationGroundingUncertaintyProjector,CitationIntegrityValidator
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import TargetPostWriteCitationGrounding,AssociationRow
    from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
    from apps.enterprise_document.services.post_write.shared_claim_support import ArtifactClaimTargetMaterializer
    from apps.enterprise_document.schemas.post_write.quality import UpstreamSIF07ClaimAuthorityResolutionSchema
    original_create=ArtifactStateService.create_initial
    def create(service,**options):
        projection=ArtifactValidationViewResolver.build_projection(artifact_scope=options["artifact_scope"],
            logical_target_ref=options["artifact_id"],visible_payload={"content":"Exact citation[C1]"})
        return original_create(service,**options,visible_projection=projection,visible_projection_ref=projection.as_ref())
    with patch.object(ArtifactStateService,"create_initial",create):
        fx=gate_tests._gate_fixture(tmp_path/"gate",fact_states=(),completion_states=(),conditions=(RequiredDimensionConditionSchema(
            dimension_id="citation",fact_families=(ValidationFactFamily.CITATION_GROUNDING,ValidationFactFamily.CITATION_INTEGRITY)),),
            rules=(FactPolicyRuleSchema(policy_rule_ref="block:independent-integrity",fact_family=ValidationFactFamily.CITATION_INTEGRITY,
                semantic_states=(ValidationSemanticState.FAIL,),effect=QualityPolicyEffect.BLOCKING,reason_code="INTEGRITY_FAIL"),))
    store=fx["validation_store"];original_run=store.resolve_run_exact(fx["run_id"])
    ids=gate_tests._Ids();ids.counter=100
    run=ValidationRunFactory(id_generator=ids,clock=gate_tests._Clock()).create(subject_key=original_run.subject_key,creation_command_id="uncertainty-run")
    lifecycle=ValidationRunLifecycleService(store=store,clock=gate_tests._Clock());lifecycle.create(run)
    run=lifecycle.start(run.validation_run_id,expected_state_version=1,transition_command_id="uncertainty-start")
    artifact=fx["artifact_ref"];subject=fx["subject_hash"]
    occurrence=quality_tests.CitationOccurrenceTargetSchema(occurrence_ref="occurrence:uncertainty",artifact_state_ref=artifact,
        validation_subject_hash=subject,citation_alias="C1",source_span_ref="span:one",occurrence_hash=canonical_sha256("one"))
    intermediate=ValidationIntermediateRuntime(store);receipts=FileArtifactStore(tmp_path/"receipts")
    identity=dict(task_id="task",run_id="run",section_id="section")
    kwargs=dict(store=store,intermediate_runtime=intermediate,validation_run_id=run.validation_run_id,subject_hash=subject,
        artifact_state_ref=artifact,occurrence=occurrence)
    if kind=="ASSOCIATION_INDETERMINATE":
        _target,claim,*_=quality_tests._sif07_target(tmp_path/"claim")
        targets=ArtifactClaimTargetMaterializer.materialize(authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(
            state="UPSTREAM_SIF07_CLAIM_AUTHORITY_PRESENT",claim_refs=(claim.claim_ref,),resolution_reason_ref="canonical-source"),
            artifact_state_ref=artifact,validation_subject_hash=subject,semantic_version="claim-projection-v1",resolved_claim_records={claim.claim_ref.claim_id:claim})
        material=dict(validation_run_id=run.validation_run_id,subject_hash=subject,artifact_state_ref=artifact.model_dump(mode="json"),
            occurrences=[{"target":occurrence.model_dump(mode="json")}],candidate_claim_targets=[row.model_dump(mode="json") for row in targets],
            policy_id="citation-claim-association",policy_version="v1")
        row=AssociationRow(occurrence_ref=occurrence.occurrence_ref,state="INDETERMINATE",associated_claim_target_ids=())
        intent="artifact://association/carrier/input"
        for ref,value in [(intent,{"input_hash":canonical_sha256(material),"input_material":material}),
                (intent.removesuffix("/input")+"/result",{"input_hash":canonical_sha256(material),"outcome":"completed","candidate":{"associations":[row.model_dump(mode="json")]}})]:
            receipts.put_json(value,artifact_ref=ref,artifact_type="citation_association",schema_version="v1",created_by="test-provider",**identity)
        evidence=TargetPostWriteCitationGrounding.association_evidence(row,material,intent)
        ref=intermediate.commit(validation_run_id=run.validation_run_id,producer_ref="citation-claim-association",target_ref=evidence.association.association_ref,
            typed_intermediate=evidence,intermediate_kind=ValidationIntermediateKind.MODEL_DERIVATION,occurred_at_epoch=0.0)
        kwargs.update(association_evidence_ref=ref,input_receipt_resolver=lambda ref:receipts.get_json(ref,**identity))
    else:
        prerequisite=CitationIntegrityValidator.project(validation_run_id=run.validation_run_id,subject_hash=subject,occurrence=occurrence,
            binding_state="INDETERMINATE",citation_target_ref=None,reason_codes=("SOURCE_SEMANTIC_UNCERTAINTY",))
        kwargs["prerequisite_fact_ref"]=store.put_fact_if_absent_or_same(prerequisite)
        kwargs["prerequisite_occurrence_ref"]=intermediate.commit(validation_run_id=run.validation_run_id,
            producer_ref="post_write_citation_grounding",target_ref=occurrence.occurrence_ref,typed_intermediate=occurrence,
            intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,occurred_at_epoch=0.0)
    fact=CitationGroundingUncertaintyProjector.project(**kwargs)
    ref=store.put_fact_if_absent_or_same(fact)
    plan=TargetPostWriteCitationGrounding.stage_plan()
    item_ref="citation:item:uncertainty"
    envelope=ValidatorInputEnvelopeBuilder(validation_view_resolver=ArtifactValidationViewResolver(ArtifactStateService(store=ArtifactStateStore(store.database)))).build(validation_run_id=run.validation_run_id,
        subject_hash=subject,artifact_state_ref=artifact,stage_plan=plan,applicable_expected_validation_item_refs=(item_ref,),exact_authority_refs=(fact.target_ref,))
    with store.database.read_transaction() as connection:sequence=store._next_event_sequence(connection,run.validation_run_id)
    event=store._event(validation_run_id=run.validation_run_id,event_id="uncertainty-terminal",sequence=sequence,event_kind="VALIDATOR_COMPLETED",
        payload={"validator_execution_ref":"execution:uncertainty"},occurred_at_epoch=0.0)
    accounting=ExpectedItemExecutionAccountingSchema(expected_validation_item_ref=item_ref,execution_state="COMPLETED",fact_refs=(ref,))
    base=dict(schema_version="validator_execution_record_v1",validator_execution_ref="execution:uncertainty",validation_run_id=run.validation_run_id,
        subject_hash=subject,validator_id=plan.validator_id,validator_version=plan.validator_version,stage_plan_ref=plan.stage_plan_ref,
        stage_plan_version=plan.stage_plan_version,input_hash=envelope.input_hash,terminal_event_id=event.event_id,terminal_execution_state="COMPLETED",
        expected_item_accounting=[accounting.model_dump(mode="json")],produced_fact_refs=[ref.model_dump(mode="json")],intermediate_refs=[])
    store.finalize_validator_execution(event,ValidatorExecutionRecordSchema(**base,record_sha256=canonical_sha256(base)),stage_plan=plan,input_envelope=envelope)
    items=[ValidationCompletionItemSchema(expected_validation_item_ref=item_ref,required=True,state="INDETERMINATE",fact_refs=(ref,))]
    if blocking:
        bad=CitationIntegrityValidator.project(validation_run_id=run.validation_run_id,subject_hash=subject,occurrence=occurrence,
            binding_state="INVALID",citation_target_ref=None,reason_codes=("MALFORMED",))
        badref=store.put_fact_if_absent_or_same(bad)
        items.append(ValidationCompletionItemSchema(expected_validation_item_ref="citation:integrity",required=True,state="FAIL",fact_refs=(badref,)))
    completion=ValidationCompletionBuilder.build(validation_run_id=run.validation_run_id,subject_hash=subject,items=tuple(items))
    completion_ref=store.put_completion_if_absent_or_same(completion)
    lifecycle.complete(run.validation_run_id,expected_state_version=2,transition_command_id="uncertainty-complete")
    decision=fx["service"].evaluate(validation_run_id=run.validation_run_id,validation_completion_ref=completion_ref,
        quality_delivery_contract_ref=fx["contract_ref"],currentness_proof=fx["currentness"])
    return kwargs,fact,store,decision


@pytest.mark.parametrize("kind",["ASSOCIATION_INDETERMINATE","PREREQUISITE_INDETERMINATE"])
@pytest.mark.parametrize("blocking",[False,True])
def test_uncertainty_real_basis_store_terminal_completion_gate(tmp_path,kind,blocking):
    kwargs,fact,store,decision=_uncertainty_carrier(tmp_path,kind,blocking)
    assert decision.overall_quality_state.value==("FAIL" if blocking else "UNRESOLVED")
    assert fact.semantic_state.value=="INDETERMINATE"
    assert fact.payload["uncertainty_kind"]==kind
    assert "joint_support_ref" not in fact.payload
    from apps.enterprise_document.services.post_write.citation import CitationGroundingUncertaintyProjector
    assert CitationGroundingUncertaintyProjector.project(**kwargs)==fact


@pytest.mark.parametrize("kind",["ASSOCIATION_INDETERMINATE","PREREQUISITE_INDETERMINATE"])
@pytest.mark.parametrize("damage",["run","subject","artifact","occurrence","same_id_span","basis_run","basis_hash","uncommitted","opposite_branch"])
def test_uncertainty_rejects_wrong_or_uncommitted_basis(tmp_path,kind,damage):
    from apps.enterprise_document.services.post_write.citation import CitationGroundingUncertaintyProjector
    kwargs,fact,store,decision=_uncertainty_carrier(tmp_path,kind)
    basis="association_evidence_ref" if kind=="ASSOCIATION_INDETERMINATE" else "prerequisite_fact_ref"
    if damage=="run":kwargs["validation_run_id"]="uncommitted-run"
    elif damage=="subject":kwargs["subject_hash"]="f"*64
    elif damage=="artifact":kwargs["artifact_state_ref"]=kwargs["artifact_state_ref"].model_copy(update={"artifact_id":"foreign-artifact"})
    elif damage=="occurrence":kwargs["occurrence"]=kwargs["occurrence"].model_copy(update={"occurrence_ref":"foreign-occurrence"})
    elif damage=="same_id_span":kwargs["occurrence"]=kwargs["occurrence"].model_copy(update={"source_span_ref":"foreign-span"})
    elif damage=="basis_run":kwargs[basis]=kwargs[basis].model_copy(update={"validation_run_id":"foreign-run"})
    elif damage=="basis_hash":kwargs[basis]=kwargs[basis].model_copy(update={"intermediate_sha256" if basis=="association_evidence_ref" else "fact_sha256":"f"*64})
    elif damage=="uncommitted":kwargs[basis]=kwargs[basis].model_copy(update={"event_id" if basis=="association_evidence_ref" else "fact_id":"never-committed"})
    else:
        # Neither typed reference can stand in for the opposite semantic branch.
        kwargs["prerequisite_fact_ref" if basis=="association_evidence_ref" else "association_evidence_ref"]=kwargs.pop(basis)
    with pytest.raises((ValueError,PostWritePersistenceIntegrityError)):
        CitationGroundingUncertaintyProjector.project(**kwargs)


@pytest.mark.parametrize("damage",["input_hash","input_subject","input_artifact","input_occurrence","candidates","policy",
    "failed","missing_result","result_hash","result_na","result_associated","duplicate"])
def test_uncertainty_association_requires_matching_completed_receipts(tmp_path,damage):
    from apps.enterprise_document.services.post_write.citation import CitationGroundingUncertaintyProjector
    kwargs,fact,store,decision=_uncertainty_carrier(tmp_path)
    original=kwargs["input_receipt_resolver"]
    def resolve(ref):
        value=json.loads(json.dumps(original(ref)))
        if ref.endswith("/input"):
            material=value["input_material"]
            if damage=="input_hash":value["input_hash"]="f"*64
            elif damage=="input_subject":material["subject_hash"]="f"*64
            elif damage=="input_artifact":material["artifact_state_ref"]["artifact_id"]="foreign"
            elif damage=="input_occurrence":material["occurrences"][0]["target"]["source_span_ref"]="foreign"
            elif damage=="candidates":material["candidate_claim_targets"]=[]
            elif damage=="policy":material["policy_version"]="v99"
        else:
            if damage=="missing_result":raise FileNotFoundError("uncommitted semantic receipt")
            if damage=="failed":value.update(outcome="failed",candidate={},failure={"kind":"TIMEOUT"})
            elif damage=="result_hash":value["input_hash"]="f"*64
            elif damage in {"result_na","result_associated"}:value["candidate"]["associations"][0]["state"]="NO_APPLICABLE_CLAIM" if damage=="result_na" else "ASSOCIATED"
            elif damage=="duplicate":value["candidate"]["associations"]*=2
        return value
    kwargs["input_receipt_resolver"]=resolve
    with pytest.raises((ValueError,FileNotFoundError)):
        CitationGroundingUncertaintyProjector.project(**kwargs)


@pytest.mark.parametrize("kind",["ASSOCIATION_INDETERMINATE","PREREQUISITE_INDETERMINATE"])
@pytest.mark.parametrize("damage",["pass","fail","grounded","joint","member","branch","version"])
def test_uncertainty_payload_cannot_bypass_state_or_support_contract(tmp_path,kind,damage):
    from apps.enterprise_document.schemas.post_write.quality import CitationGroundingUncertaintyPayloadSchema
    kwargs,fact,store,decision=_uncertainty_carrier(tmp_path,kind)
    raw=dict(fact.payload)
    if damage in {"pass","fail","grounded"}:raw["semantic_state"]=damage.upper()
    elif damage=="joint":raw["joint_support_ref"]={"unrelated":"support"}
    elif damage=="member":raw["member_support_refs"]=[]
    elif damage=="version":raw["schema_version"]="citation_grounding_uncertainty_v99"
    else:raw["uncertainty_kind"]="PREREQUISITE_INDETERMINATE" if kind=="ASSOCIATION_INDETERMINATE" else "ASSOCIATION_INDETERMINATE"
    with pytest.raises(ValidationError):CitationGroundingUncertaintyPayloadSchema.model_validate(raw)


def test_grounding_completion_only_preserves_semantic_and_technical_outcomes(grounding_mainline):
    scenario,(engine,calls,observations,arguments,summary)=grounding_mainline
    expected="NOT_APPLICABLE" if scenario in {"no-claims","invalid","empty"} else "INDETERMINATE" if scenario in {"uncertain","unknown-coverage","INDETERMINATE"} else "FAIL" if scenario in {"PARTIALLY_SUPPORTED","NOT_SUPPORTED","CONTRADICTED"} else "TECHNICAL_FAILURE" if scenario in {"bad-json","omitted","duplicate","foreign-claim"} else "PASS"
    for scope,item,facts,completion,gate_ref in observations:
        assert item.state.value==expected
        if expected in {"NOT_APPLICABLE","TECHNICAL_FAILURE"}:assert not facts
        elif scenario in {"uncertain","unknown-coverage"}:assert facts[0].payload["uncertainty_kind"]=="ASSOCIATION_INDETERMINATE"
        else:assert facts[0].payload["joint_support_ref"] and "uncertainty_kind" not in facts[0].payload
    counts={}
    for request in calls:
        purpose=request.extra.get("call_purpose") or "unspecified"
        counts[purpose]=counts.get(purpose,0)+1
    assert counts.get("citation_association",0)==(0 if scenario in {"invalid","empty"} else 2)
    assert len([request for request in calls if request.extra.get("response_contract")==support_tests.SUPPORT_RESPONSE_CONTRACT])==(0 if scenario in {"no-claims","unknown-coverage"} else 1)
    output=Path(engine.checkpointer.path).parent.parent/"grounding-call-evidence.json"
    output.write_text(json.dumps({"scenario":scenario,"calls_by_purpose":counts,
        "completion_only_replay_extra_calls":0,"whole_target_replay_test":"test_grounding_outcomes_and_terminal_reopen_are_exact",
        "results":[{"scope":scope,"item":item.model_dump(mode="json"),"facts":[f.model_dump(mode="json") for f in facts]} for scope,item,facts,*_ in observations]},ensure_ascii=False,indent=2),encoding="utf-8")


def _grounding_durable_state(engine):
    """Include business SQLite and Trace SQLite, plus every existing immutable file."""
    import sqlite3
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    root=Path(engine.checkpointer.path).parent
    databases={}
    for path in root.rglob("*.sqlite3"):
        with sqlite3.connect(path) as connection:
            databases[str(path.relative_to(root))]=tuple(connection.iterdump())
    return integrity_tests._durable_state(engine),databases


def _forbid_terminal_business_writes(composition,*,allow_publication=False):
    stack=ExitStack()
    def forbidden(*args,**kwargs):raise AssertionError("terminal exact recovery attempted new business write")
    objects=[(composition.control_service,["decide"]),(composition.assessment_store,["put_if_absent_or_same"]),
        (composition.artifact_store,["create_initial_if_absent_or_same","append_next_version"]),
        (composition.entry_store,["put_if_absent_or_same"]),(composition.requirement_store,["put_if_absent_or_same"]),
        (composition.contract_store,["put_if_absent_or_same"]),(composition.correction_policy_store,["put_if_absent_or_same"]),
        (composition.manifest_builder,["build_and_persist"]),
        (composition.validation_store,["put_completion_if_absent_or_same","put_fact_if_absent_or_same","finalize_validator_execution"]),
        (composition.validation_intermediate_runtime,["commit"]),(composition.gate_service,["evaluate"]),
        (composition.output_store,["put_bytes","put_json"])]
    if not allow_publication:objects.append((composition.release_store,["put_bytes","put_json"]))
    for obj,names in objects:
        for name in names:stack.enter_context(patch.object(obj,name,forbidden))
    return stack


@pytest.fixture(scope="module")
def exact_terminal_fixture(tmp_path_factory):
    return _grounding_run(tmp_path_factory.mktemp("gnd345-terminal-negative"),"supported")


def _terminal_options(engine):
    top=workflow_tests._latest_w13_top_level_payload(engine)
    return dict(task_id=top.task_id,run_id=top.run_id,document_id=top.document_id,
        project_input=top.project_input,section_execution_results=tuple(top.section_execution_results))


@pytest.mark.parametrize("damage",["task","document","query","project_metadata","sections_empty","sections_duplicate",
    "body","title","writer","evidence","claims","lineage"])
def test_terminal_replay_rejects_changed_request_before_writes(exact_terminal_fixture,damage):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    engine,*_=exact_terminal_fixture
    composition=integrity_tests._reopen(engine);options=_terminal_options(engine)
    sources=list(options["section_execution_results"]);source=sources[0]
    if damage=="task":options["task_id"]="another-task"
    elif damage=="document":options["document_id"]="another-document"
    elif damage in {"query","project_metadata"}:
        project=options["project_input"].model_dump(mode="json")
        if damage=="query":project["user_query"]+=" changed"
        else:project["metadata"]["unrequested"]="changed"
        options["project_input"]=type(options["project_input"]).model_validate(project)
    elif damage=="sections_empty":sources=[]
    elif damage=="sections_duplicate":sources*=2
    elif damage in {"body","title"}:
        field="content" if damage=="body" else "section_title"
        sources[0]=source.model_copy(update={"section":source.section.model_copy(update={field:getattr(source.section,field)+" changed"})})
    elif damage=="writer":sources[0]=source.model_copy(update={"writer_execution_ref":None})
    elif damage=="evidence":sources[0]=source.model_copy(update={"active_evidence_package_binding":None})
    elif damage=="claims":sources[0]=source.model_copy(update={"canonical_claim_coverage_proof_refs":()})
    else:sources[0]=source.model_copy(update={"active_execution_lineage":source.active_execution_lineage.model_copy(update={"attempt_id":"stale-attempt"})})
    options["section_execution_results"]=tuple(sources)
    before=_grounding_durable_state(engine)
    with _forbid_terminal_business_writes(composition),patch.object(FakeLLMClient,"generate",side_effect=AssertionError("replay called model")):
        with pytest.raises((ValueError,RuntimeError,KeyError)):
            composition.complete_target_post_write(**options)
    assert _grounding_durable_state(engine)==before


@pytest.mark.parametrize("damage",["assessment","control","assembly","gate","validation","output","grounding_execution",
    "coverage_execution","claim_currentness","evidence_currentness","policy_version"])
def test_terminal_replay_refuses_missing_or_stale_exact_owner_records(exact_terminal_fixture,damage):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    engine,*_=exact_terminal_fixture
    composition=integrity_tests._reopen(engine);options=_terminal_options(engine)
    before=_grounding_durable_state(engine)
    with _forbid_terminal_business_writes(composition),ExitStack() as stack:
        def missing(*args,**kwargs):raise PostWritePersistenceIntegrityError("injected exact record missing")
        if damage in {"assessment","control","assembly","gate","validation","output","evidence_currentness"}:
            owner,method={"assessment":(composition.assessment_store,"resolve_exact"),"control":(composition.control_store,"resolve_exact"),
                "assembly":(composition.binding_store,"resolve_assembly_manifest"),"gate":(composition.gate_store,"resolve_exact"),
                "validation":(composition.validation_store,"resolve_run_exact"),"output":(composition.output_store,"get_bytes"),
                "evidence_currentness":(composition.research_runtime,"validate_evidence_package_binding_current")}[damage]
            stack.enter_context(patch.object(owner,method,missing))
        elif damage=="grounding_execution":
            original=composition.validation_store.find_validator_execution_for_item
            stack.enter_context(patch.object(composition.validation_store,"find_validator_execution_for_item",
                lambda **kwargs:None if kwargs["validator_id"]==composition.citation_grounding.VALIDATOR_ID else original(**kwargs)))
        elif damage=="coverage_execution":
            original=composition.validation_store.find_validator_execution_for_item
            stack.enter_context(patch.object(composition.validation_store,"find_validator_execution_for_item",
                lambda **kwargs:None if kwargs["validator_id"]=="post_write_coverage" else original(**kwargs)))
        elif damage=="claim_currentness":stack.enter_context(patch.object(composition.claim_quality_projection.claim_runtime,"resolve_claim_head",return_value=None))
        else:stack.enter_context(patch.object(composition.control_service,"control_contract_version","unavailable-v99"))
        with pytest.raises((ValueError,RuntimeError,KeyError)):
            composition.complete_target_post_write(**options)
    assert _grounding_durable_state(engine)==before


@pytest.mark.parametrize("window",["before-publisher","before-manifest"])
def test_success_before_release_recovers_original_assessment_without_fake_replay(tmp_path,window):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path,"supported",publication_window=window)
    options=_terminal_options(engine)
    composition=integrity_tests._reopen(engine)
    terminal=composition.lifecycle_service.load_run_lifecycle(options["run_id"])
    assert terminal.status.value=="success" and terminal.run_completion_assessment_ref is not None
    def state_without_release():
        (database,files),databases=_grounding_durable_state(engine)
        return database,{path:value for path,value in files.items() if not path.startswith("target_release")},databases
    before=state_without_release()
    budget=engine.budget_runtime(options["task_id"],options["run_id"]).canonical_state()
    with _forbid_terminal_business_writes(composition,allow_publication=True),patch.object(FakeLLMClient,"generate",side_effect=AssertionError("recovery called model")):
        recovered=composition.complete_target_post_write(**options)
    assert recovered.published_release.reuse_state.value=="NEW_COMMIT"
    assert recovered.run_completion_assessment_ref==terminal.run_completion_assessment_ref
    assert recovered.terminal_run==terminal
    assert state_without_release()==before
    assert engine.budget_runtime(options["task_id"],options["run_id"]).canonical_state()==budget
    published_state=_grounding_durable_state(engine)
    with _forbid_terminal_business_writes(composition):replay=composition.complete_target_post_write(**options)
    assert replay.published_release.reuse_state.value=="EXACT_REQUEST_REPLAY"
    assert replay.published_release.manifest==recovered.published_release.manifest
    assert _grounding_durable_state(engine)==published_state


def _two_source_rag_provider():
    from tools.fake_rag_tool import FakeRAGTool
    from schemas.rag import RAGToolOutputSchema
    from contracts.evidence import build_evidence_bundle, project_evidence_bundle
    original=FakeRAGTool.run
    def provider(tool,call):
        with patch.dict(os.environ,{"FAKE_RAG_SCENARIO":"citation_collision"}):
            responses=[original(tool,call.model_copy(update={"tool_call_id":call.tool_call_id+"-source-"+str(i)})) for i in range(2)]
        outputs=[RAGToolOutputSchema.model_validate(row.result) for row in responses]
        citations=[output.citations[0].model_copy(update={"citation_id":"C"+str(i+1),"original_citation_id":"C"+str(i+1)}) for i,output in enumerate(outputs)]
        bundle=build_evidence_bundle(query=outputs[0].query,rewritten_queries=[],
            selected_chunks=[output.retrieved_chunks[0] for output in outputs],dropped_chunks=[],citations=citations,
            trace=outputs[0].trace,max_context_chars=12000,extra=outputs[0].evidence.extra)
        bundle.assessment=outputs[0].evidence.assessment
        bundle.item_assessments=[output.evidence.item_assessments[0].model_copy(update={"evidence_id":bundle.selected_evidence_ids[i]}) for i,output in enumerate(outputs)]
        context,chunks,citations=project_evidence_bundle(bundle)
        output=outputs[0].model_copy(update={"evidence":bundle,"context":context,"retrieved_chunks":chunks,"citations":citations})
        return responses[0].model_copy(update={"tool_call_id":call.tool_call_id,"result":output.model_dump(mode="json")})
    return provider


@pytest.mark.parametrize("scenario",("multi-claim","repeat-source","joint-partial","joint-mixed","broad-vs-cited","fallback"))
def test_production_grounding_claim_source_and_joint_matrix(tmp_path,scenario):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import GroundingAccounting
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path,scenario,
        rag_provider=_two_source_rag_provider() if scenario in {"joint-partial","joint-mixed","broad-vs-cited"} else None,large_budget=True)
    composition=engine.stage_service.target_post_write_composition
    top=workflow_tests._latest_w13_top_level_payload(engine)
    assert top.terminal_error is None,top.terminal_error
    assert len([request for request in calls if request.extra.get("response_contract")==ASSOCIATION_CONTRACT])==2
    assert len([request for request in calls if request.extra.get("response_contract")==support_tests.SUPPORT_RESPONSE_CONTRACT])=={
        "multi-claim":2,"repeat-source":1,"joint-partial":3,"joint-mixed":3,"broad-vs-cited":2,"fallback":1}[scenario]
    for scope,item,facts,completion,gate in observations:
        assert len(facts)==(2 if scenario=="multi-claim" else 1),item
        assert item.state.value==("FAIL" if scenario=="broad-vs-cited" else "PASS"),item
        record=composition.validation_store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
            validator_id=composition.citation_grounding.VALIDATOR_ID,expected_item_ref=item.expected_validation_item_ref)
        snapshot=composition.validation_intermediate_runtime.resolve_typed(record.intermediate_refs[0],GroundingAccounting)
        assert len(snapshot.groups)==len(facts)
        for group in snapshot.groups:
            target=group["target"]
            assert len(target["occurrence_refs"])==(2 if scenario in {"repeat-source","joint-partial","joint-mixed"} else 1)
            assert len({row["evidence_target_set_id"] for row in target["individual_evidence_target_set_refs"]})==(2 if scenario in {"joint-partial","joint-mixed"} else 1)
            from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRefSchema
            joint=composition.citation_grounding.support.runtime.resolve_support(ClaimEvidenceSupportRefSchema.model_validate(group["joint_support_ref"]))
            members=[composition.citation_grounding.support.runtime.resolve_support(ClaimEvidenceSupportRefSchema.model_validate(ref)) for ref in group["member_support_refs"]]
            assert joint.relation.value==("NOT_SUPPORTED" if scenario=="broad-vs-cited" else "SUPPORTED")
            if scenario in {"joint-partial","joint-mixed"}:
                assert {row.relation.value for row in members}==({"PARTIALLY_SUPPORTED"} if scenario=="joint-partial" else {"SUPPORTED","NOT_SUPPORTED"})
            if scenario=="repeat-source":assert members==[joint]
        if scenario=="broad-vs-cited":
            results=[composition.claim_quality_projection.claim_runtime.resolve_verification_result(ref)
                for section in top.section_execution_results for ref in section.canonical_claim_verification_refs]
            assert results and all(result.verdict.value=="VERIFIED" for result in results)
            faithful=[composition.validation_store.resolve_fact(ref) for row in completion.items for ref in row.fact_refs
                if row.expected_validation_item_ref.startswith("faithfulness:")]
            assert faithful and all(row.semantic_state.value=="PASS" for row in faithful)
    if scenario=="fallback":
        assert all(composition.writer_execution_store.resolve_writer_generation_invocation(row.writer_generation_invocation_ref).generation_class.value=="fallback_write" for row in top.section_execution_results)
    before=_grounding_durable_state(engine)
    reopened=integrity_tests._reopen(engine)
    with _forbid_terminal_business_writes(reopened),patch.object(FakeLLMClient,"generate",side_effect=AssertionError("replayed cognition")):
        replay=reopened.complete_target_post_write(**_terminal_options(engine))
    assert replay.published_release.reuse_state.value=="EXACT_REQUEST_REPLAY"
    assert before==_grounding_durable_state(engine)


def test_document_multi_origin_claims_and_section_order_reopen(tmp_path):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import GroundingAccounting
    from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRefSchema
    project=workflow_tests._w13_current_new_task_project_input(workflow_tests._w13_single_section_project_input("task-gnd345-multi-origin"))
    raw=project.model_dump(mode="json")
    names=["Section Alpha","Section Beta"]
    raw["generation_requirements"].update(required_sections=names,need_citation=True,citation_required_sections=names,
        extra={"enable_section_aware_retrieval":True,"enable_corrective_section_retrieval":False})
    raw["output_schema"].update(required_sections=names)
    project=type(project).model_validate(raw)
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path,"multi-origin",project_override=project,large_budget=True)
    composition=engine.stage_service.target_post_write_composition
    top=workflow_tests._latest_w13_top_level_payload(engine)
    assert top.terminal_error is None,top.terminal_error
    assert len(top.section_execution_results)==2
    final=composition.artifact_store.resolve_exact(top.target_document_artifact_ref)
    entries=final.canonical_payload_ref["citation_projection"]["catalog"]["entries"]
    assert len(entries)==1 and len(entries[0]["origin_refs"])==2
    document=next(row for row in observations if row[0]=="DOCUMENT")
    item,completion=document[1],document[3]
    assert item.state.value=="PASS" and len(item.fact_refs)==2
    record=composition.validation_store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
        validator_id=composition.citation_grounding.VALIDATOR_ID,expected_item_ref=item.expected_validation_item_ref)
    snapshot=composition.validation_intermediate_runtime.resolve_typed(record.intermediate_refs[0],GroundingAccounting)
    assert len(snapshot.groups)==2
    for group in snapshot.groups:
        assert len(group["target"]["occurrence_refs"])==2
        assert len(group["member_support_refs"])==1
        support=composition.citation_grounding.support.runtime.resolve_support(ClaimEvidenceSupportRefSchema.model_validate(group["joint_support_ref"]))
        candidate=next(row for request in calls if request.extra.get("response_contract")==ASSOCIATION_CONTRACT
            for row in _association_input(request)["candidate_claim_targets"] if row["claim_target_id"]==group["target"]["claim_target_id"])
        from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimRefSchema
        claim=composition.claim_quality_projection.claim_runtime.resolve_claim(ClaimRefSchema.model_validate(candidate["source_claim_ref"]))
        origin=next(row for row in entries[0]["origin_refs"] if row["section_id"]==claim.section_id)
        assert support.evidence_target_set_ref.evidence_member_refs==(origin["exact_evidence_ref"],)
    reopened=integrity_tests._reopen(engine)
    before=_grounding_durable_state(engine)
    options=_terminal_options(engine)
    with _forbid_terminal_business_writes(reopened),patch.object(FakeLLMClient,"generate",side_effect=AssertionError("terminal cognition")):
        with pytest.raises((ValueError,RuntimeError)):
            reopened.complete_target_post_write(**{**options,"section_execution_results":tuple(reversed(options["section_execution_results"]))})
        assert before==_grounding_durable_state(engine)
        replay=reopened.complete_target_post_write(**options)
    assert replay.published_release.manifest==top.target_release_publication.manifest
    assert before==_grounding_durable_state(engine)


class _GroundingProcessLoss(BaseException):
    pass


@pytest.mark.parametrize("window",("association-intent","association-result","association-intermediate",
    "grounding-fact","accounting","terminal","support-candidate","support-commit"))
def test_grounding_call_and_commit_windows_do_not_redispatch_or_recharge(tmp_path,window):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import GroundingAccounting,CitationGroundingError
    from apps.enterprise_document.schemas.post_write.quality import CitationAssociationEvidenceSchema
    seen=[]
    def hook(composition,options,complete):
        if seen or options["scope"].value!="SECTION":return complete(composition,**options)
        seen.append(window)
        grounding=composition.citation_grounding
        store=grounding.store
        original_put=store.put_json
        original_intermediate=composition.validation_intermediate_runtime.commit
        original_fact=composition.validation_store.put_fact_if_absent_or_same
        original_terminal=composition.validation_store.finalize_validator_execution
        original_support=grounding.support.runtime.commit_support
        def put(value,**kwargs):
            result=original_put(value,**kwargs)
            expected={"association-intent":"citation_association_input","association-result":"citation_association_result",
                      "support-candidate":"claim_support_candidate"}.get(window)
            if kwargs.get("artifact_type")==expected:raise _GroundingProcessLoss(window)
            return result
        def intermediate(**kwargs):
            result=original_intermediate(**kwargs)
            value=kwargs["typed_intermediate"]
            if window=="association-intermediate" and isinstance(value,CitationAssociationEvidenceSchema) or window=="accounting" and isinstance(value,GroundingAccounting):
                raise _GroundingProcessLoss(window)
            return result
        def fact(value):
            result=original_fact(value)
            if window=="grounding-fact" and value.family.value=="CITATION_GROUNDING":raise _GroundingProcessLoss(window)
            return result
        def terminal(event,record,**kwargs):
            result=original_terminal(event,record,**kwargs)
            if window=="terminal" and record.validator_id==grounding.VALIDATOR_ID:raise _GroundingProcessLoss(window)
            return result
        def support(*args,**kwargs):
            result=original_support(*args,**kwargs)
            if window=="support-commit":raise _GroundingProcessLoss(window)
            return result
        with patch.object(store,"put_json",put),patch.object(composition.validation_intermediate_runtime,"commit",intermediate),patch.object(composition.validation_store,"put_fact_if_absent_or_same",fact),patch.object(composition.validation_store,"finalize_validator_execution",terminal),patch.object(grounding.support.runtime,"commit_support",support):
            with pytest.raises(_GroundingProcessLoss,match=window):complete(composition,**options)
        identity,_=grounding._identity(options["source_sections"])
        budget=composition.budget_manager_provider(identity["task_id"],identity["run_id"])
        before_budget=budget.canonical_state()
        def state():
            import sqlite3
            databases={}
            for path in tmp_path.rglob("*.sqlite3"):
                with sqlite3.connect(path) as connection:databases[str(path)]=tuple(connection.iterdump())
            return databases,{str(path):path.read_bytes() for path in tmp_path.rglob("*.json")}
        before=state()
        with patch.object(FakeLLMClient,"generate",side_effect=AssertionError("window recovery external redispatch")):
            if window in {"association-intent","accounting"}:
                with pytest.raises(CitationGroundingError,match="intent|incomplete"):
                    complete(composition,**options)
                assert state()==before
                assert budget.canonical_state()==before_budget
                raise RuntimeError("incomplete Grounding safely refused")
            result=complete(composition,**options)
        assert budget.canonical_state()==before_budget
        return result
    refused=window in {"association-intent","accounting"}
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path,
        "broad-vs-cited" if window.startswith("support-") else "supported",large_budget=True,
        rag_provider=_two_source_rag_provider() if window.startswith("support-") else None,
        validation_hook=hook,allow_failure=refused)
    assert seen==[window]
    top=workflow_tests._latest_w13_top_level_payload(engine)
    assert (top.terminal_error is not None)==refused
    if not refused:
        assert len(observations)==2
        assert observations[0][1].state.value==("FAIL" if window.startswith("support-") else "PASS")


@pytest.mark.parametrize("boundary",("default-budget","association-budget","stale-before-model","stale-after-model"))
def test_grounding_budget_and_currentness_boundaries_preserve_failure_and_no_recharge(tmp_path,boundary):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import CitationGroundingError
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    seen=[]
    def hook(composition,options,complete):
        if seen or options["scope"].value!="SECTION":return complete(composition,**options)
        seen.append(boundary)
        grounding=composition.citation_grounding
        identity,_=grounding._identity(options["source_sections"])
        budget=composition.budget_manager_provider(identity["task_id"],identity["run_id"])
        if boundary in {"default-budget","association-budget"}:
            from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
            from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
            reserve=CanonicalInvocationBudgetAdapter.reserve_provider_attempt
            def denied(adapter,request):
                if boundary=="association-budget" and request.extra.get("response_contract")==ASSOCIATION_CONTRACT:
                    raise CanonicalBudgetCapacityExceeded("model_tokens_total",limit=24000,remaining=0,required=1)
                return reserve(adapter,request)
            with patch.object(CanonicalInvocationBudgetAdapter,"reserve_provider_attempt",denied):
                with pytest.raises(CitationGroundingError) as first:complete(composition,**options)
            assert first.value.failure_origin.value=="canonical_budget"
            assert first.value.failure_category.value=="resource_exhausted"
            assert budget.canonical_state().limits.model_tokens_total==24000
            assert budget.canonical_state().active_reserved.model_tokens_total==0
            before=integrity_tests._durable_state_from_composition(composition),budget.canonical_state()
            with patch.object(FakeLLMClient,"generate",side_effect=AssertionError("denied call redispatched")):
                with pytest.raises(CitationGroundingError) as replay:complete(composition,**options)
            assert first.value.failure_origin==replay.value.failure_origin
            assert first.value.failure_category==replay.value.failure_category
            assert before==(integrity_tests._durable_state_from_composition(composition),budget.canonical_state())
            raise replay.value
        original_associate=grounding._associate
        def associate(material,identity,lineage,validate_current):
            invoked=[]
            original_generate=FakeLLMClient.generate
            def generate(client,request):
                result=original_generate(client,request)
                if request.extra.get("response_contract")==ASSOCIATION_CONTRACT:invoked.append(request.model_call_id)
                return result
            def current():
                validate_current()
                if boundary=="stale-before-model" or invoked:raise CitationGroundingError("injected exact source no longer current")
            before=budget.canonical_state()
            with patch.object(FakeLLMClient,"generate",generate):
                if boundary=="stale-before-model":
                    with pytest.raises(CitationGroundingError,match="no longer current"):
                        original_associate(material,identity,lineage,current)
                    assert invoked==[] and budget.canonical_state()==before
                    raise CitationGroundingError("injected exact source no longer current")
                outcome=original_associate(material,identity,lineage,current)
            assert len(invoked)==1 and outcome[0] is None
            assert outcome[2]["kind"]=="ASSOCIATION_TECHNICAL_FAILURE"
            after=budget.canonical_state()
            with patch.object(FakeLLMClient,"generate",side_effect=AssertionError("stale candidate redispatched")):
                assert original_associate(material,identity,lineage,validate_current)==outcome
            assert budget.canonical_state()==after
            return outcome
        with patch.object(grounding,"_associate",associate):return complete(composition,**options)
    refused=boundary!="stale-after-model"
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path,"joint-partial" if boundary=="default-budget" else "supported",
        rag_provider=_two_source_rag_provider() if boundary=="default-budget" else None,validation_hook=hook,allow_failure=refused)
    assert seen==[boundary]
    top=workflow_tests._latest_w13_top_level_payload(engine)
    assert (top.terminal_error is not None)==refused
    if refused:
        assert len([request for request in calls if request.extra.get("response_contract")==ASSOCIATION_CONTRACT])==(1 if boundary=="default-budget" else 0)
    else:
        assert observations[0][1].state.value=="TECHNICAL_FAILURE" and not observations[0][2]


@pytest.mark.parametrize("scope", ("section", "document"))
def test_association_model_projection_retains_exact_domain_not_runtime_catalog(scope):
    from copy import deepcopy
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import TargetPostWriteCitationGrounding

    sections = [
        {"payload_kind": "section", "section_id": name, "section_title": name + "[C1]",
         "content": " exact body [C1]\\n[C2] ", "visible_projection_sha256": "runtime-only-hash"}
        for name in ("source-b", "source-a")
    ]
    visible = sections[0] if scope == "section" else {
        "payload_kind": "document", "document_logical_id": "document:exact",
        "included_sections": [{"section_key": row["section_id"], "visible_payload": row} for row in sections],
        "missing_section_keys": ["missing"], "withheld_section_keys": ["withheld"], "partial": True,
        "citation_projection": {"catalog": "runtime-only-catalog", "source_catalogs": [{"origins": "runtime-only-origins"}]},
    }
    material = {
        "visible_artifact": visible, "artifact_state_ref": {"artifact_id": "exact-artifact"},
        "candidate_claim_targets": [{"claim_target_id": "claim-b", "canonical_claim_text": "B"},
                                    {"claim_target_id": "claim-a", "canonical_claim_text": "A"}],
        "occurrences": [{"target": {"occurrence_ref": "occurrence-b"}, "span": {"start": 1, "end": 5}},
                        {"target": {"occurrence_ref": "occurrence-a"}, "span": {"start": 10, "end": 14}}],
        "eligible_occurrence_refs": ["occurrence-b", "occurrence-a"],
        "no_applicable_allowed": {"occurrence-b": False, "occurrence-a": True},
        "claim_source_hash": "claim-source", "citation_source_hash": "citation-source",
        "prerequisite_refs": [{"fact_id": "runtime-only-prerequisite"}],
    }
    before = deepcopy(material)
    actual = TargetPostWriteCitationGrounding._model_material(material)
    assert material == before
    assert actual["source_material_sha256"] == canonical_sha256(material)
    for key in ("artifact_state_ref", "candidate_claim_targets", "occurrences", "eligible_occurrence_refs",
                "no_applicable_allowed", "claim_source_hash", "citation_source_hash"):
        assert actual[key] == material[key]
    assert "prerequisite_refs" not in actual
    assert "runtime-only" not in json.dumps(actual, ensure_ascii=False)
    if scope == "section":
        assert actual["visible_artifact"] == {key: value for key, value in sections[0].items() if key != "visible_projection_sha256"}
    else:
        result = actual["visible_artifact"]
        assert [row["section_key"] for row in result["included_sections"]] == ["source-b", "source-a"]
        for old, new in zip(sections, result["included_sections"]):
            assert new["visible_payload"] == {key: value for key, value in old.items() if key != "visible_projection_sha256"}
        for key in ("document_logical_id", "missing_section_keys", "withheld_section_keys", "partial"):
            assert result[key] == visible[key]


def test_real_association_context_retains_full_receipt_and_exact_model_projection(tmp_path):
    from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import TargetPostWriteCitationGrounding

    engine, calls, observations, arguments, summary = _grounding_run(tmp_path)
    grounding = engine.stage_service.target_post_write_composition.citation_grounding
    associations = [request for request in calls if request.extra.get("response_contract") == ASSOCIATION_CONTRACT]
    assert len(associations) == 2
    seen = set()
    for request in associations:
        key = request.extra["logical_operation_id"].rsplit("/", 1)[-1]
        receipt = grounding.store.get_json("artifact://citation-association/" + key + "/input",
            task_id=request.task_id, run_id=request.run_id, section_id=request.extra["section_id"])
        material = receipt["input_material"]
        projected = _association_input(request)
        assert projected == TargetPostWriteCitationGrounding._model_material(material)
        assert "prerequisite_refs" in material and "prerequisite_refs" not in projected
        assert receipt["input_hash"] == canonical_sha256(material)
        assert receipt["request"]["messages"] == request.messages
        assert request.extra["association_representation_version"] == "v2"
        kind = material["visible_artifact"]["payload_kind"]
        seen.add(kind)
        if kind == "document":
            assert "citation_projection" in material["visible_artifact"]
            assert "citation_projection" not in projected["visible_artifact"]
    assert seen == {"section", "document"}


@pytest.mark.parametrize("damage",("execution-json","accounting-event","association-receipt","output-bytes"))
def test_damaged_terminal_grounding_storage_is_refused_without_repair(tmp_path,damage):
    import test_multi_agent_v3_citation_integrity_mainline as integrity_tests
    engine,calls,observations,arguments,summary=_grounding_run(tmp_path)
    composition=engine.stage_service.target_post_write_composition
    item,completion=observations[0][1],observations[0][3]
    record=composition.validation_store.find_validator_execution_for_item(validation_run_id=completion.validation_run_id,
        validator_id=composition.citation_grounding.VALIDATOR_ID,expected_item_ref=item.expected_validation_item_ref)
    if damage in {"execution-json","accounting-event"}:
        table,key,column,value=("validator_execution_records","validator_execution_ref","record_json",record.validator_execution_ref) if damage=="execution-json" else (
            "validation_execution_events","event_id","event_json",record.intermediate_refs[0].event_id)
        with composition.database.write_transaction(immediate=True) as connection:
            assert connection.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?",("{}",value)).rowcount==1
    elif damage=="association-receipt":
        from apps.enterprise_document.services.agent_rag_v1.post_write_citation_grounding import GroundingAccounting
        snapshot=composition.validation_intermediate_runtime.resolve_typed(record.intermediate_refs[0],GroundingAccounting)
        path=composition.citation_grounding.store._path_for_ref(snapshot.input_receipt_ref)
        assert path.is_file()
        path.write_bytes(b"corrupt association receipt")
    else:
        top=workflow_tests._latest_w13_top_level_payload(engine)
        final=composition.artifact_store.resolve_exact(top.target_document_artifact_ref)
        ref=final.validated_output_bindings[0].output_artifact_ref.artifact_ref
        path=composition.output_store._path_for_ref(ref)
        assert path.is_file()
        path.write_bytes(b"corrupt final output")
    before=_grounding_durable_state(engine)
    reopened=integrity_tests._reopen(engine)
    with _forbid_terminal_business_writes(reopened),patch.object(FakeLLMClient,"generate",side_effect=AssertionError("damaged terminal redispatch")):
        with pytest.raises((ValueError,RuntimeError,KeyError)):
            reopened.complete_target_post_write(**_terminal_options(engine))
    assert before==_grounding_durable_state(engine)
