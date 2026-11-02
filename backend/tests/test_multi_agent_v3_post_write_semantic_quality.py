from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    ClaimSupportAuthorityKind,
    EvidenceTargetSetRefSchema,
    SupportRelation,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    ValidationFactFamily,
    ValidationIntermediateKind,
    ValidationIntermediateRefSchema,
    ValidationSemanticState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    ArtifactClaimTargetSchema,
    CitationClaimAssociationSchema,
    CitationClaimAssociationState,
    CitationGroundingRelation,
    CitationOccurrenceBindingState,
    CitationOccurrenceTargetSchema,
    CitationTargetRefSchema,
    ClaimTargetAuthorityKind,
    ForeignClaimTargetCandidateSchema,
    UpstreamSIF07ClaimAuthorityResolutionSchema,
    UpstreamSIF07ClaimAuthorityState,
)
from apps.enterprise_document.services.agent_rag_v1.claim_support_runtime import (
    ClaimSupportCurrentnessError,
    ClaimSupportRuntime,
)
from apps.enterprise_document.services.post_write.citation import (
    CitationEvidenceTargetResolver,
    CitationGroundingProjector,
    CitationGroundingTargetMaterializer,
    CitationGroundingValidator,
    CitationIntegrityValidator,
    CitationQualityIntegrityError,
)
from apps.enterprise_document.services.post_write.faithfulness import (
    FaithfulnessIntegrityError,
    FaithfulnessValidator, FaithfulnessEvidenceTargetResolver,
)
from apps.enterprise_document.services.post_write.shared_claim_support import (
    ArtifactClaimTargetMaterializer,
    ClaimTargetIntegrityError,
    ClaimTargetSupportSubjectAdapter,
    ForeignClaimFallbackForbidden,
    SharedClaimEvidenceSupportEvaluator,
    UpstreamClaimAuthorityUnresolved,
)
from backend.tests.test_multi_agent_v3_claim_support_runtime import (
    _setup_support_runtime,
)
from backend.tests.test_post_write_validation_foundation import _created_run
from core.hashing import canonical_sha256


def _artifact_ref() -> ArtifactStateRefSchema:
    return ArtifactStateRefSchema(
        artifact_id="artifact:section:s1",
        artifact_version=1,
        artifact_hash=canonical_sha256({"artifact": "section:s1", "version": 1}),
        artifact_scope=ArtifactScope.SECTION,
    )


def _subject_hash() -> str:
    return canonical_sha256({"subject": "post-write:s1"})


def _sif07_target(tmp_path):
    (
        _env,
        _claim_runtime,
        claim_record,
        _subject,
        support_runtime,
        context,
        calls,
        state,
    ) = _setup_support_runtime(tmp_path)
    resolution = UpstreamSIF07ClaimAuthorityResolutionSchema(
        state=UpstreamSIF07ClaimAuthorityState.PRESENT,
        claim_refs=(claim_record.claim_ref,),
        resolution_reason_ref="claim-authority://present",
    )
    targets = ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=resolution,
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        semantic_version="claim-target-v1",
        resolved_claim_records={claim_record.claim_ref.claim_id: claim_record},
    )
    assert len(targets) == 1
    return (
        targets[0],
        claim_record,
        support_runtime,
        context,
        calls,
        state,
    )


def _prepare_support(*, support_adapter, claim_target, selected_evidence_refs,
                     target_set_contract_version, support_semantic_contract_version,
                     operation_context, structurally_excluded_refs=()):
    """Prepare shared Support for citation/certainty tests; no final Claim Fact."""
    target_set=FaithfulnessEvidenceTargetResolver(support_adapter).resolve(
        claim_target=claim_target,selected_evidence_refs=selected_evidence_refs,
        structurally_excluded_refs=structurally_excluded_refs,
        target_set_contract_version=target_set_contract_version)
    support=support_adapter.evaluate_and_commit(claim_target=claim_target,
        evidence_target_set_ref=target_set,support_semantic_contract_version=support_semantic_contract_version,
        operation_context=operation_context)
    return target_set,support


def _foreign_candidate() -> ForeignClaimTargetCandidateSchema:
    return ForeignClaimTargetCandidateSchema(
        local_claim_occurrence_ref="foreign-occurrence://1",
        canonical_claim_text="外部文档包含一个可验证事实。",
        source_span_ref="foreign-span://1",
        local_semantic_contract_version="foreign-claim-v1",
    )


def test_present_projects_exact_sif07_claim_and_forbids_foreign_fallback(tmp_path) -> None:
    (
        _env,
        _claim_runtime,
        claim_record,
        _subject,
        _runtime,
        _context,
        _calls,
        _state,
    ) = _setup_support_runtime(tmp_path)
    resolution = UpstreamSIF07ClaimAuthorityResolutionSchema(
        state=UpstreamSIF07ClaimAuthorityState.PRESENT,
        claim_refs=(claim_record.claim_ref,),
        resolution_reason_ref="claim-authority://present",
    )

    target = ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=resolution,
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        semantic_version="claim-target-v1",
        resolved_claim_records={claim_record.claim_ref.claim_id: claim_record},
    )[0]

    assert target.authority_kind is ClaimTargetAuthorityKind.SIF07_PROJECTION
    assert target.source_claim_ref == claim_record.claim_ref
    assert target.foreign_local_ref is None
    shared_subject = ClaimTargetSupportSubjectAdapter.adapt(target)
    assert shared_subject.authority_kind is ClaimSupportAuthorityKind.SIF07_CLAIM
    assert shared_subject.subject_ref == claim_record.claim_ref.claim_id

    with pytest.raises(ForeignClaimFallbackForbidden):
        ArtifactClaimTargetMaterializer.materialize(
            authority_resolution=resolution,
            artifact_state_ref=_artifact_ref(),
            validation_subject_hash=_subject_hash(),
            semantic_version="claim-target-v1",
            resolved_claim_records={claim_record.claim_ref.claim_id: claim_record},
            foreign_candidates=(_foreign_candidate(),),
        )


def test_unresolved_and_missing_present_claims_fail_closed(tmp_path) -> None:
    unresolved = UpstreamSIF07ClaimAuthorityResolutionSchema(
        state=UpstreamSIF07ClaimAuthorityState.UNRESOLVED,
        resolution_reason_ref="claim-authority://technical-failure",
    )
    with pytest.raises(UpstreamClaimAuthorityUnresolved):
        ArtifactClaimTargetMaterializer.materialize(
            authority_resolution=unresolved,
            artifact_state_ref=_artifact_ref(),
            validation_subject_hash=_subject_hash(),
            semantic_version="claim-target-v1",
            foreign_candidates=(_foreign_candidate(),),
        )

    (
        _env,
        _claim_runtime,
        claim_record,
        _subject,
        _runtime,
        _context,
        _calls,
        _state,
    ) = _setup_support_runtime(tmp_path / "present")
    present = UpstreamSIF07ClaimAuthorityResolutionSchema(
        state=UpstreamSIF07ClaimAuthorityState.PRESENT,
        claim_refs=(claim_record.claim_ref,),
        resolution_reason_ref="claim-authority://present",
    )
    with pytest.raises(ClaimTargetIntegrityError, match="exact resolved ClaimRecord set"):
        ArtifactClaimTargetMaterializer.materialize(
            authority_resolution=present,
            artifact_state_ref=_artifact_ref(),
            validation_subject_hash=_subject_hash(),
            semantic_version="claim-target-v1",
            resolved_claim_records={},
        )


def test_not_applicable_foreign_identity_is_disjoint_and_tagged() -> None:
    resolution = UpstreamSIF07ClaimAuthorityResolutionSchema(
        state=UpstreamSIF07ClaimAuthorityState.NOT_APPLICABLE,
        resolution_reason_ref="claim-authority://foreign-artifact",
    )
    target = ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=resolution,
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        semantic_version="claim-target-v1",
        foreign_candidates=(_foreign_candidate(),),
    )[0]

    assert target.authority_kind is ClaimTargetAuthorityKind.POST_WRITE_FOREIGN_LOCAL
    assert target.source_claim_ref is None
    assert target.foreign_local_ref is not None
    assert target.claim_target_id.startswith("post-write-foreign-claim://sha256/")
    assert not target.claim_target_id.startswith("claim://sha256/")

    subject = ClaimTargetSupportSubjectAdapter.adapt(target)
    assert subject.authority_kind is ClaimSupportAuthorityKind.POST_WRITE_FOREIGN_LOCAL
    assert subject.subject_ref == target.claim_target_id


def test_artifact_claim_target_schema_rejects_tampered_projection_hash(tmp_path) -> None:
    target, _claim, _runtime, _context, _calls, _state = _sif07_target(tmp_path)
    tampered = target.model_dump(mode="json")
    tampered["canonical_claim_text"] = target.canonical_claim_text + " tampered"

    with pytest.raises(
        ValueError,
        match="id/hash preimage mismatch|canonical hash mismatch",
    ):
        ArtifactClaimTargetSchema.model_validate(tampered)


def test_shared_support_adapter_reuses_one_canonical_support_fact(tmp_path) -> None:
    target, _claim, runtime, context, calls, _state = _sif07_target(tmp_path)
    adapter = SharedClaimEvidenceSupportEvaluator(runtime)
    target_set = adapter.materialize_evidence_target_set(
        claim_target=target,
        evidence_member_refs=("evidence:C1",),
        target_set_contract_version="target-set-v1",
    )

    first = adapter.evaluate_and_commit(
        claim_target=target,
        evidence_target_set_ref=target_set,
        support_semantic_contract_version="support-contract-v1",
        operation_context=context,
    )
    second = adapter.evaluate_and_commit(
        claim_target=target,
        evidence_target_set_ref=target_set,
        support_semantic_contract_version="support-contract-v1",
        operation_context=context,
    )

    assert second == first
    assert calls["evaluator"] == 1


def test_foreign_support_still_requires_explicit_shared_runtime_validator(tmp_path) -> None:
    (
        _env,
        _claim_runtime,
        _claim,
        _subject,
        runtime,
        context,
        _calls,
        _state,
    ) = _setup_support_runtime(tmp_path)
    resolution = UpstreamSIF07ClaimAuthorityResolutionSchema(
        state=UpstreamSIF07ClaimAuthorityState.NOT_APPLICABLE,
        resolution_reason_ref="claim-authority://foreign-artifact",
    )
    target = ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=resolution,
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        semantic_version="claim-target-v1",
        foreign_candidates=(_foreign_candidate(),),
    )[0]
    adapter = SharedClaimEvidenceSupportEvaluator(runtime)
    target_set = adapter.materialize_evidence_target_set(
        claim_target=target,
        evidence_member_refs=("evidence:C1",),
        target_set_contract_version="target-set-v1",
    )

    with pytest.raises(ClaimSupportCurrentnessError, match="no authorized validator"):
        adapter.evaluate_and_commit(
            claim_target=target,
            evidence_target_set_ref=target_set,
            support_semantic_contract_version="support-contract-v1",
            operation_context=context,
        )


def test_faithfulness_v3_projects_result_and_retains_support_target_resolver(tmp_path):
    from backend.tests.test_bca014_i7_faithfulness_v3 import material
    fx,args=material(tmp_path)
    fact=FaithfulnessValidator().evaluate(**args)
    assert fact.family is ValidationFactFamily.FAITHFULNESS
    assert fact.semantic_state is ValidationSemanticState.PASS
    assert fact.payload["verification_result_ref"]==args["verification_result"].verification_result_ref.model_dump(mode="json")
    target, _claim, runtime, context, calls, _state = _sif07_target(tmp_path/"support")
    target_set,support=_prepare_support(support_adapter=SharedClaimEvidenceSupportEvaluator(runtime),
        claim_target=target,selected_evidence_refs=("evidence:C1","evidence:C2"),
        structurally_excluded_refs=("evidence:C2",),target_set_contract_version="faithfulness-target-v1",
        support_semantic_contract_version="support-contract-v1",operation_context=context)
    assert target_set.evidence_member_refs==("evidence:C1",) and calls["evaluator"]==1
    with pytest.raises(FaithfulnessIntegrityError,match="escape"):
        FaithfulnessEvidenceTargetResolver(SharedClaimEvidenceSupportEvaluator(runtime)).resolve(
            claim_target=target,selected_evidence_refs=("evidence:C1",),
            structurally_excluded_refs=("evidence:DROPPED",),target_set_contract_version="faithfulness-target-v1")

def test_faithfulness_replay_does_not_rejudge_existing_result(tmp_path):
    from backend.tests.test_bca014_i7_faithfulness_v3 import material
    fx,args=material(tmp_path)
    before=fx.runtime.ledger_path.read_bytes()
    assert FaithfulnessValidator().evaluate(**args)==FaithfulnessValidator().evaluate(**args)
    assert fx.runtime.ledger_path.read_bytes()==before

def test_citation_integrity_is_deterministic_and_does_not_ground_invalid_source() -> None:
    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://1",
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        citation_alias="C1",
        source_span_ref="artifact-span://citation/1",
        occurrence_hash=canonical_sha256({"occurrence": 1}),
    )
    target = CitationTargetRefSchema(
        citation_target_ref="citation-target://C1",
        exact_evidence_ref="evidence:C1",
        target_hash=canonical_sha256({"target": "C1"}),
    )

    valid = CitationIntegrityValidator.project(
        validation_run_id="validation-run:1",
        subject_hash=_subject_hash(),
        occurrence=occurrence,
        binding_state=CitationOccurrenceBindingState.VALID,
        citation_target_ref=target,
    )
    invalid = CitationIntegrityValidator.project(
        validation_run_id="validation-run:1",
        subject_hash=_subject_hash(),
        occurrence=occurrence,
        binding_state=CitationOccurrenceBindingState.INVALID,
        citation_target_ref=None,
        reason_codes=("UNRESOLVED_ALIAS",),
    )

    assert valid.family is ValidationFactFamily.CITATION_INTEGRITY
    assert valid.semantic_state is ValidationSemanticState.PASS
    assert invalid.semantic_state is ValidationSemanticState.FAIL
    assert invalid.payload["citation_target_ref"] is None


def test_citation_specific_target_cannot_widen_or_reintroduce_dropped_evidence(tmp_path) -> None:
    target, _claim, runtime, _context, _calls, _state = _sif07_target(tmp_path)
    resolver = CitationEvidenceTargetResolver(
        SharedClaimEvidenceSupportEvaluator(runtime)
    )
    citation = CitationTargetRefSchema(
        citation_target_ref="citation-target://C2",
        exact_evidence_ref="evidence:C2",
        target_hash=canonical_sha256({"target": "C2"}),
    )

    with pytest.raises(CitationQualityIntegrityError, match="outside exact selected"):
        resolver.resolve(
            claim_target=target,
            citation_target_ref=citation,
            selected_evidence_refs=("evidence:C1",),
            target_set_contract_version="citation-target-v1",
        )


def test_broad_faithfulness_support_cannot_rescue_wrong_citation(tmp_path) -> None:
    target, _claim, runtime, context, calls, state = _sif07_target(tmp_path)
    adapter = SharedClaimEvidenceSupportEvaluator(runtime)

    broad_target, broad_support = _prepare_support(
        support_adapter=adapter,
        claim_target=target,
        selected_evidence_refs=("evidence:C1", "evidence:C2"),
        target_set_contract_version="faithfulness-target-v1",
        support_semantic_contract_version="support-contract-v1",
        operation_context=context,
    )
    assert broad_support.relation is SupportRelation.SUPPORTED

    state["relation"] = SupportRelation.NOT_SUPPORTED
    state["supporting_refs"] = ()
    citation_ref = CitationTargetRefSchema(
        citation_target_ref="citation-target://C2",
        exact_evidence_ref="evidence:C2",
        target_hash=canonical_sha256({"target": "C2"}),
    )
    citation_resolver = CitationEvidenceTargetResolver(adapter)
    individual = citation_resolver.resolve(
        claim_target=target,
        citation_target_ref=citation_ref,
        selected_evidence_refs=("evidence:C1", "evidence:C2"),
        target_set_contract_version="citation-target-v1",
    )
    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://2",
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        citation_alias="C2",
        source_span_ref="artifact-span://citation/2",
        occurrence_hash=canonical_sha256({"occurrence": 2}),
    )
    association = CitationClaimAssociationSchema(
        association_ref="citation-association://2",
        artifact_state_ref=_artifact_ref(),
        occurrence_ref=occurrence.occurrence_ref,
        candidate_claim_target_ids=(target.claim_target_id,),
        associated_claim_target_ids=(target.claim_target_id,),
        association_state=CitationClaimAssociationState.ASSOCIATED,
        materializer_id="semantic-association",
        materializer_version="v1",
        association_hash=canonical_sha256({"association": 2}),
    )
    grounding_target = CitationGroundingTargetMaterializer(adapter).materialize(
        claim_target=target,
        associations=(association,),
        occurrences=(occurrence,),
        citation_targets=(citation_ref,),
        individual_evidence_target_sets=(individual,),
        joint_target_set_contract_version="citation-target-v1",
    )
    members, joint, grounding_fact = CitationGroundingValidator(
        support_adapter=adapter
    ).evaluate(
        validation_run_id="validation-run:1",
        subject_hash=_subject_hash(),
        claim_target=target,
        grounding_target=grounding_target,
        support_semantic_contract_version="support-contract-v1",
        operation_context=context,
    )

    assert broad_target != individual
    assert broad_support.support_ref != joint.support_ref
    assert joint.relation is SupportRelation.NOT_SUPPORTED
    assert grounding_fact.semantic_state is ValidationSemanticState.FAIL
    assert grounding_fact.payload["grounding_relation"] == (
        CitationGroundingRelation.NOT_GROUNDED.value
    )
    assert len(members) == 1
    assert calls["evaluator"] == 2

    with pytest.raises(CitationQualityIntegrityError, match="unrelated Evidence target"):
        CitationGroundingProjector.project(
            validation_run_id="validation-run:1",
            subject_hash=_subject_hash(),
            grounding_target=grounding_target,
            joint_support_record=joint,
            member_support_records=(joint, broad_support),
        )


def test_single_member_grounding_reuses_identical_individual_and_joint_support(tmp_path) -> None:
    target, _claim, runtime, context, calls, state = _sif07_target(tmp_path)
    adapter = SharedClaimEvidenceSupportEvaluator(runtime)
    state["relation"] = SupportRelation.SUPPORTED
    state["supporting_refs"] = ("evidence:C1",)

    citation_ref = CitationTargetRefSchema(
        citation_target_ref="citation-target://C1",
        exact_evidence_ref="evidence:C1",
        target_hash=canonical_sha256({"target": "C1"}),
    )
    individual = CitationEvidenceTargetResolver(adapter).resolve(
        claim_target=target,
        citation_target_ref=citation_ref,
        selected_evidence_refs=("evidence:C1",),
        target_set_contract_version="citation-target-v1",
    )
    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://1",
        artifact_state_ref=_artifact_ref(),
        validation_subject_hash=_subject_hash(),
        citation_alias="C1",
        source_span_ref="artifact-span://citation/1",
        occurrence_hash=canonical_sha256({"occurrence": 1}),
    )
    association = CitationClaimAssociationSchema(
        association_ref="citation-association://1",
        artifact_state_ref=_artifact_ref(),
        occurrence_ref=occurrence.occurrence_ref,
        candidate_claim_target_ids=(target.claim_target_id,),
        associated_claim_target_ids=(target.claim_target_id,),
        association_state=CitationClaimAssociationState.ASSOCIATED,
        materializer_id="semantic-association",
        materializer_version="v1",
        association_hash=canonical_sha256({"association": 1}),
    )
    grounding_target = CitationGroundingTargetMaterializer(adapter).materialize(
        claim_target=target,
        associations=(association,),
        occurrences=(occurrence,),
        citation_targets=(citation_ref,),
        individual_evidence_target_sets=(individual,),
        joint_target_set_contract_version="citation-target-v1",
    )
    assert grounding_target.joint_evidence_target_set_ref == individual

    members, joint, fact = CitationGroundingValidator(
        support_adapter=adapter
    ).evaluate(
        validation_run_id="validation-run:1",
        subject_hash=_subject_hash(),
        claim_target=target,
        grounding_target=grounding_target,
        support_semantic_contract_version="support-contract-v1",
        operation_context=context,
    )

    assert members == (joint,)
    assert fact.semantic_state is ValidationSemanticState.PASS
    assert calls["evaluator"] == 1


def test_validation_subject_mismatch_fails_before_verification_projection(tmp_path):
    from backend.tests.test_bca014_i7_faithfulness_v3 import material
    fx,args=material(tmp_path)
    before=fx.runtime.ledger_path.read_bytes()
    with pytest.raises(FaithfulnessIntegrityError,match="different ValidationSubject"):
        FaithfulnessValidator().evaluate(**(args|dict(subject_hash=canonical_sha256({"subject":"different"}))))
    assert fx.runtime.ledger_path.read_bytes()==before

def test_claim_target_and_faithfulness_fact_exact_replay_through_validation_store(tmp_path):
    from backend.tests.test_bca014_i7_faithfulness_v3 import material,exact_target
    _database,store,_lifecycle,run=_created_run(tmp_path/"validation")
    fx,args=material(tmp_path/"claim")
    target=exact_target(fx.claim,run.subject_key.subject_hash)
    payload=target.model_dump(mode="json")
    event_id="claim-target:"+canonical_sha256(payload)
    ref=ValidationIntermediateRefSchema(validation_run_id=run.validation_run_id,event_id=event_id,
        intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING,intermediate_sha256=canonical_sha256(payload))
    options=dict(producer_ref="artifact-claim-target-materializer:v1",target_ref=target.claim_target_id,occurred_at_epoch=1002.0)
    first=store.append_intermediate_if_absent_or_same(run.validation_run_id,event_id,payload,ref,**options)
    assert store.append_intermediate_if_absent_or_same(run.validation_run_id,event_id,payload,ref,
        **(options|dict(occurred_at_epoch=9999.0)))==first
    assert ArtifactClaimTargetSchema.model_validate(store.resolve_intermediate(first))==target
    fact=FaithfulnessValidator().evaluate(**(args|dict(validation_run_id=run.validation_run_id,
        subject_hash=run.subject_key.subject_hash,claim_target=target)))
    fact_ref=store.put_fact_if_absent_or_same(fact)
    assert store.put_fact_if_absent_or_same(fact)==fact_ref and store.resolve_fact(fact_ref)==fact

def test_quality_module_reuses_canonical_m6_support_types() -> None:
    from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
        EvidenceTargetSetRefSchema as CanonicalEvidenceTargetSetRefSchema,
    )
    from apps.enterprise_document.schemas.post_write import quality

    assert quality.EvidenceTargetSetRefSchema is CanonicalEvidenceTargetSetRefSchema
    assert ArtifactClaimTargetSchema.model_fields["source_claim_ref"].annotation is not None


@pytest.mark.parametrize("same_source", [False, True])
@pytest.mark.parametrize("reverse_sources", [False, True])
def test_grounding_joint_reversed_associations_and_repeated_source(tmp_path, same_source, reverse_sources):
    target, _claim, runtime, context, calls, state = _sif07_target(tmp_path)
    adapter = SharedClaimEvidenceSupportEvaluator(runtime)
    state["relation"] = SupportRelation.NOT_SUPPORTED
    state["supporting_refs"] = ()
    occurrences, associations, citations, sets = [], [], [], []
    for index, suffix in enumerate(("a", "b")):
        occurrence = CitationOccurrenceTargetSchema(
            occurrence_ref="occ-" + suffix, artifact_state_ref=target.artifact_state_ref,
            validation_subject_hash=target.validation_subject_hash, citation_alias="C1",
            source_span_ref="span-" + suffix, occurrence_hash=canonical_sha256(suffix),
        )
        association = CitationClaimAssociationSchema(
            association_ref="association-" + ("z" if index == 0 else "a"),
            artifact_state_ref=target.artifact_state_ref, occurrence_ref=occurrence.occurrence_ref,
            candidate_claim_target_ids=(target.claim_target_id,), associated_claim_target_ids=(target.claim_target_id,),
            association_state=CitationClaimAssociationState.ASSOCIATED,
            materializer_id="semantic-association", materializer_version="v1", association_hash=canonical_sha256(index),
        )
        source = "C1" if same_source or (index == (1 if reverse_sources else 0)) else "C2"
        citation = CitationTargetRefSchema(citation_target_ref="source-" + source,
            exact_evidence_ref="evidence:" + source, target_hash=canonical_sha256(source))
        occurrences.append(occurrence); associations.append(association); citations.append(citation)
        sets.append(CitationEvidenceTargetResolver(adapter).resolve(claim_target=target,
            citation_target_ref=citation, selected_evidence_refs=("evidence:C1", "evidence:C2"),
            target_set_contract_version="citation-target-v1"))
    def materialize(order):
        return CitationGroundingTargetMaterializer(adapter).materialize(claim_target=target,
            associations=tuple(associations[i] for i in order), occurrences=tuple(occurrences[i] for i in order),
            citation_targets=tuple(citations[i] for i in order), individual_evidence_target_sets=tuple(sets[i] for i in order),
            joint_target_set_contract_version="citation-target-v1")
    group = materialize((0, 1))
    assert group == materialize((1, 0))
    assert group.association_refs == ("association-a", "association-z")
    members, joint, fact = CitationGroundingValidator(support_adapter=adapter).evaluate(
        validation_run_id="validation-run:1", subject_hash=target.validation_subject_hash,
        claim_target=target, grounding_target=group, support_semantic_contract_version="support-contract-v1",
        operation_context=context)
    assert fact.payload["occurrence_refs"] == ["occ-a", "occ-b"]
    assert fact.payload["citation_target_refs"] == [x.citation_target_ref for x in citations]
    assert len(members) == (1 if same_source else 2)
    assert calls["evaluator"] == (1 if same_source else 3)

    from apps.enterprise_document.schemas.post_write.quality import CitationGroundingPayloadSchema
    from pydantic import ValidationError
    parsed = CitationGroundingPayloadSchema.model_validate(fact.payload)
    assert parsed.schema_version == "citation_grounding_payload_v2"
    historical = dict(fact.payload, schema_version="citation_grounding_payload_v1")
    if same_source or reverse_sources:
        with pytest.raises(ValidationError, match="citation_target_refs must be canonical ordered-unique"):
            CitationGroundingPayloadSchema.model_validate(historical)
    else:
        assert CitationGroundingPayloadSchema.model_validate(historical).schema_version == "citation_grounding_payload_v1"
