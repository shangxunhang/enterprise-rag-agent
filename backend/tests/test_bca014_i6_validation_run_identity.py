"""Real Target ValidationRun identity, exact replay and clean-cut rejection."""
import inspect
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.foundation import ValidationRunSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_pipeline_identity import (
    pipeline_identity_material, semantic_pipeline_sha256, validation_run_identity,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteComposition, TargetPostWriteCompositionError
from backend.tests.test_bca014_finalizing_read_current import canonical_section
from backend.tests.test_bca014_i6_verification_generation import current, snapshot, build
from backend.tests.test_bca014_i5_verification_result_v2_ledger import commit


def test_four_exact_inputs_and_changes():
    quality=SimpleNamespace(model_dump=lambda **kw: {"quality":"one"})
    values=dict(subject_hash="a"*64,quality_contract_ref=quality,
        semantic_pipeline_sha256="b"*64,verification_generation_sha256="c"*64)
    one=validation_run_identity(**values)
    assert one==validation_run_identity(**values)
    for field in values:
        other=dict(values)
        other[field]=SimpleNamespace(model_dump=lambda **kw:{"quality":"two"}) if field=="quality_contract_ref" else "d"*64
        assert validation_run_identity(**other)!=one


def test_real_target_path_uses_real_pipeline_generation_and_exact_replay(canonical_section):
    fx=canonical_section; pw=fx["post_write"]; args=fx["arguments"]
    generation=pw._verification_generation(**{key:args.get(key) for key in (
        "run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})
    assert generation.entries
    pipeline=semantic_pipeline_sha256(pipeline_identity_material(pw,args["scope"]))
    completion=pw.validation_store.resolve_completion(fx["outcome"][0])
    expected="validation_run://sha256/"+canonical_sha256(dict(subject_hash=completion.subject_hash,
        quality_contract_ref=args["quality_contract_ref"].model_dump(mode="json"),
        semantic_pipeline_sha256=pipeline,verification_generation_sha256=generation.verification_generation_sha256))
    assert completion.validation_run_id==expected
    run=pw.validation_store.resolve_run_exact(expected)
    assert run.schema_version=="validation_run_v1"
    before=pw.claim_quality_projection.claim_runtime.ledger_path.read_bytes()
    with pw.database.run_write_guard_context(args["run_id"]):
        assert pw._complete_validation(**args)==fx["outcome"]
    assert pw._complete_validation(**args,committed_gate_ref=fx["outcome"][1])==fx["outcome"]
    assert pw.claim_quality_projection.claim_runtime.ledger_path.read_bytes()==before


def test_terminal_replay_rejects_changed_pipeline_without_new_run(canonical_section):
    fx=canonical_section; pw=fx["post_write"]; owner=pw.claim_quality_projection
    plan=owner.stage_plan()
    with patch.object(owner,"stage_plan",return_value=plan.model_copy(update={"validator_version":"i6-fixture-next"})), patch.object(pw.validation_lifecycle,"create",side_effect=AssertionError("no new terminal run")):
        with pytest.raises(TargetPostWriteCompositionError,match="missing its exact ValidationRun"):
            pw._complete_validation(**fx["arguments"],committed_gate_ref=fx["outcome"][1])


def test_legal_head_result_mutation_changes_run_identity(tmp_path):
    fx, first, projection=current(tmp_path)
    one=build(fx,projection,snapshot(fx,first.verification_result))
    quality=SimpleNamespace(model_dump=lambda **kw:{"quality":"exact"})
    def identity(generation):
        return validation_run_identity(subject_hash="a"*64,quality_contract_ref=quality,
            semantic_pipeline_sha256="b"*64,verification_generation_sha256=generation.verification_generation_sha256)
    old=identity(one)
    fx.context=fx.context.model_copy(update={"authorization_ref":"verification://i6-run-next"})
    fx.invocation,_=fx.composer.build_input(fx.claim,fx.binding,fx.support,fx.context)
    second=commit(fx,head=first.resulting_claim_head)
    two=build(fx,projection,snapshot(fx,second.verification_result))
    assert identity(two)!=old


def test_production_no_manual_identity_or_silent_empty(canonical_section):
    source=inspect.getsource(TargetPostWriteComposition._complete_validation)
    assert all(word not in source for word in ("execution_composition_version","claim_projection_version","semantic_pipeline_version"))
    fx=canonical_section; pw=fx["post_write"]
    with patch.object(pw,"claim_quality_projection",None):
        with pytest.raises(TargetPostWriteCompositionError,match="empty generation forbidden"):
            pw._verification_generation(**{key:fx["arguments"].get(key) for key in (
                "run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})


def test_actual_document_path_and_scope_separation(canonical_section):
    from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
    from apps.enterprise_document.schemas.post_write import ActiveComparisonMode, ValidationScope
    fx=canonical_section;pw=fx["post_write"];args=fx["arguments"]
    section=args["source_sections"][0]
    invocation=pw.writer_execution_store.resolve_writer_generation_invocation(section.writer_generation_invocation_ref)
    project=ProjectInputSchema.model_validate(invocation.model_dump(mode="json")["unified_writer_input_snapshot"]["project_input"])
    task_id=pw.lifecycle_service.load_run_identity(args["run_id"]).task_id
    document_id=pw.entry_store.resolve_exact(args["entry_ref"]).identity.document_id
    materialized=pw.materialize_sections_and_document(task_id=task_id,run_id=args["run_id"],
        document_id=document_id,project_input=project,section_execution_results=args["source_sections"])
    doc=materialized.document
    proof=pw.currentness_resolver.prove(historical_entry_ref=doc.document_entry_ref,
        active_projection=doc.active_projection,comparison_mode=ActiveComparisonMode.MATERIALIZED_ENTRY,
        active_materialized_post_write_entry_ref=doc.document_entry_ref)
    options=dict(run_id=args["run_id"],entry_ref=doc.document_entry_ref,artifact_ref=doc.document_artifact_ref,
        scope=ValidationScope.DOCUMENT,target_ref=document_id,quality_contract_ref=args["quality_contract_ref"],
        currentness_proof=proof,requirement_ref=args["requirement_ref"],assembly_input_manifest_ref=doc.manifest_ref,
        source_sections=args["source_sections"])
    with pw.database.run_write_guard_context(args["run_id"]):
        completion_ref,gate=pw._complete_validation(**options)
    generation=pw._verification_generation(**{key:options.get(key) for key in (
        "run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})
    completion=pw.validation_store.resolve_completion(completion_ref)
    assert completion.validation_run_id==validation_run_identity(subject_hash=completion.subject_hash,
        quality_contract_ref=args["quality_contract_ref"],semantic_pipeline_sha256=semantic_pipeline_sha256(pipeline_identity_material(pw,ValidationScope.DOCUMENT)),
        verification_generation_sha256=generation.verification_generation_sha256)
    section_generation=pw._verification_generation(**{key:args.get(key) for key in (
        "run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})
    assert generation.entries==section_generation.entries
    assert generation.verification_generation_sha256!=section_generation.verification_generation_sha256
    assert pw._complete_validation(**options,committed_gate_ref=gate)==(completion_ref,gate)


def test_actual_new_generation_creates_new_run_and_refuses_old_gate(canonical_section):
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
    fx=canonical_section;pw=fx["post_write"];args=dict(fx["arguments"])
    projection=pw.claim_quality_projection
    material=projection.resolve_sources(post_write=pw,artifact_ref=args["artifact_ref"],scope=args["scope"],
        source_sections=args["source_sections"],assembly_input_manifest_ref=None)
    section_snapshot=material.sections[0]
    claim=section_snapshot.claim_records[0]
    result=section_snapshot.verification_records[0]
    binding=projection.claim_runtime.resolve_binding_facts(result.binding_facts_ref)
    support=projection.support.runtime.resolve_support(result.claim_evidence_support_ref)
    head=projection.claim_runtime.resolve_claim_head(claim.claim_ref)
    context=ClaimOperationContextSchema(active_lineage=projection.claim_runtime.lifecycle_service.resolve_active_lineage(
        args["run_id"],"section:"+claim.section_id),authorization_ref="verification://i6-production-next",operation_purpose="claim-verification")
    invocation,_=projection.verifier.build_input(claim,binding,support,context)
    candidate=projection.verifier.parse_candidate({"verdict":"UNSUPPORTED","positive_basis_refs":[],
        "rationale":"I6 deterministic re-verification fixture","issues":[]},invocation=invocation,
        occurrence="verification://i6-production-next",receipt_ref="artifact://i6-production-next")[0]
    updated=projection.claim_runtime.commit_verification(claim.claim_ref,binding.binding_facts_ref,support.support_ref,candidate,head,context)
    source=args["source_sections"][0]
    refs=tuple(updated.verification_result.verification_result_ref if ref.claim_ref==claim.claim_ref else ref
        for ref in source.canonical_claim_verification_refs)
    args["source_sections"]=(source.model_copy(update={"canonical_claim_verification_refs":refs}),)
    with pytest.raises(TargetPostWriteCompositionError,match="missing its exact ValidationRun"):
        pw._complete_validation(**args,committed_gate_ref=fx["outcome"][1])
    with pw.database.run_write_guard_context(args["run_id"]):
        new=pw._complete_validation(**args)
    assert new[0].validation_run_id!=fx["outcome"][0].validation_run_id
    assert pw.validation_store.resolve_completion(fx["outcome"][0]).validation_run_id==fx["outcome"][0].validation_run_id
    generation=pw._verification_generation(**{key:args.get(key) for key in (
        "run_id","artifact_ref","scope","target_ref","source_sections","assembly_input_manifest_ref")})
    completion=pw.validation_store.resolve_completion(new[0])
    assert new[0].validation_run_id==validation_run_identity(subject_hash=completion.subject_hash,
        quality_contract_ref=args["quality_contract_ref"],semantic_pipeline_sha256=semantic_pipeline_sha256(pipeline_identity_material(pw,args["scope"])),
        verification_generation_sha256=generation.verification_generation_sha256)
