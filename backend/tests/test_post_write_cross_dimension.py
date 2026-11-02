from __future__ import annotations

from pathlib import Path

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import (
    SupportRelation,
)
from apps.enterprise_document.schemas.post_write import (
    ArtifactConsistencyRelation,
    CitationClaimAssociationSchema,
    CitationClaimAssociationState,
    CitationOccurrenceBindingState,
    CitationOccurrenceTargetSchema,
    CitationTargetRefSchema,
    CoverageRelation,
    OutputConformanceState,
    RequirementAdherenceObservation,
    SemanticPresenceObservation,
    UpstreamSIF07ClaimAuthorityResolutionSchema,
    UpstreamSIF07ClaimAuthorityState,
    ValidationCompletionItemSchema,
    ValidationCompletionItemState,
    ValidationFactFamily,
    ValidationSemanticState,
)
from apps.enterprise_document.services.post_write import (
    ArtifactClaimTargetMaterializer,
    CitationEvidenceTargetResolver,
    CitationGroundingTargetMaterializer,
    CitationGroundingValidator,
    CitationIntegrityValidator,
    ConsistencyProjector,
    CoverageProjector,
    CoverageSemanticEvaluator,
    FaithfulnessValidator,
    RelevancyValidator,
    SharedClaimEvidenceSupportEvaluator,
    ValidationCompletionBuilder,
)
from core.hashing import canonical_sha256

from backend.tests.test_multi_agent_v3_claim_support_runtime import (
    _setup_support_runtime,
)
from backend.tests.test_post_write_consistency import _consistency_fixture
from backend.tests.test_post_write_coverage import _coverage_fixture
from backend.tests.test_post_write_relevancy import _relevancy_fixture


def _faithfulness_pass(tmp_path, *, artifact_ref, subject_hash, validation_run_id):
    from backend.tests.test_bca014_i5_verifier_project_input_basis import fixture
    from backend.tests.test_bca014_i5_verifier_v2_semantics import parse
    from backend.tests.test_bca014_i5_verification_result_v2_ledger import commit
    from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportCandidateSchema
    fx=fixture(tmp_path)
    result=commit(fx,parse(fx,positive=[fx.invocation.verifier_project_input_basis.project_input_basis_ref])).verification_result
    target=ArtifactClaimTargetMaterializer.materialize(
        authority_resolution=UpstreamSIF07ClaimAuthorityResolutionSchema(
            state=UpstreamSIF07ClaimAuthorityState.PRESENT,claim_refs=(fx.claim.claim_ref,),
            resolution_reason_ref="authority://cross-dimension"),artifact_state_ref=artifact_ref,
        validation_subject_hash=subject_hash,semantic_version="claim-projection-v1",
        resolved_claim_records={fx.claim.claim_ref.claim_id:fx.claim})[0]
    calls={"evaluator":0};state={"relation":SupportRelation.SUPPORTED,"supporting_refs":("evidence:C1",)}
    def evaluator(subject,target_set,contract,context):
        fx.runtime.validate_operation_context(context)
        calls["evaluator"]+=1
        return ClaimEvidenceSupportCandidateSchema(claim_support_subject_ref=subject,
            evidence_target_set_ref=target_set,support_semantic_contract_version=contract,
            relation=state["relation"],supporting_refs=state["supporting_refs"],
            evaluation_occurrence_ref="support-eval://cross",evaluator_policy_id="shared-support-evaluator",evaluator_policy_version="v1")
    fx.support_owner.support_evaluator=evaluator
    fact=FaithfulnessValidator().evaluate(validation_run_id=validation_run_id,subject_hash=subject_hash,
        claim_target=target,verification_result=result,source_snapshot_sha256="a"*64)
    return {"target":target,"adapter":SharedClaimEvidenceSupportEvaluator(fx.support_owner),
        "context":fx.context,"calls":calls,"state":state,"target_set":fx.support.evidence_target_set_ref,
        "support":fx.support,"fact":fact}


def test_faithfulness_supported_and_relevancy_violated_can_coexist(tmp_path) -> None:
    relevancy = _relevancy_fixture(
        tmp_path / "relevancy",
        adherence=RequirementAdherenceObservation.VIOLATED,
    )
    faithful = _faithfulness_pass(
        tmp_path / "faithfulness",
        artifact_ref=relevancy["artifact_ref"],
        subject_hash=relevancy["run"].subject_key.subject_hash,
        validation_run_id=relevancy["run"].validation_run_id,
    )

    assert faithful["fact"].family is ValidationFactFamily.FAITHFULNESS
    assert faithful["fact"].semantic_state is ValidationSemanticState.PASS
    assert relevancy["fact"].family is ValidationFactFamily.REQUIREMENT_ADHERENCE
    assert relevancy["fact"].semantic_state is ValidationSemanticState.FAIL


def test_faithfulness_supported_and_citation_grounding_not_grounded_can_coexist(
    tmp_path,
) -> None:
    relevancy = _relevancy_fixture(tmp_path / "subject")
    ctx = _faithfulness_pass(
        tmp_path / "claim",
        artifact_ref=relevancy["artifact_ref"],
        subject_hash=relevancy["run"].subject_key.subject_hash,
        validation_run_id=relevancy["run"].validation_run_id,
    )
    ctx["state"]["relation"] = SupportRelation.NOT_SUPPORTED
    ctx["state"]["supporting_refs"] = ()

    citation_ref = CitationTargetRefSchema(
        citation_target_ref="citation-target://wrong",
        exact_evidence_ref="evidence:C2",
        target_hash=canonical_sha256({"target": "wrong"}),
    )
    individual = CitationEvidenceTargetResolver(ctx["adapter"]).resolve(
        claim_target=ctx["target"],
        citation_target_ref=citation_ref,
        selected_evidence_refs=("evidence:C1", "evidence:C2"),
        target_set_contract_version="citation-target-v1",
    )
    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://wrong",
        artifact_state_ref=relevancy["artifact_ref"],
        validation_subject_hash=relevancy["run"].subject_key.subject_hash,
        citation_alias="C2",
        source_span_ref="span://citation/wrong",
        occurrence_hash=canonical_sha256({"occurrence": "wrong"}),
    )
    association = CitationClaimAssociationSchema(
        association_ref="citation-association://wrong",
        artifact_state_ref=relevancy["artifact_ref"],
        occurrence_ref=occurrence.occurrence_ref,
        candidate_claim_target_ids=(ctx["target"].claim_target_id,),
        associated_claim_target_ids=(ctx["target"].claim_target_id,),
        association_state=CitationClaimAssociationState.ASSOCIATED,
        materializer_id="semantic-association",
        materializer_version="v1",
        association_hash=canonical_sha256({"association": "wrong"}),
    )
    grounding_target = CitationGroundingTargetMaterializer(
        ctx["adapter"]
    ).materialize(
        claim_target=ctx["target"],
        associations=(association,),
        occurrences=(occurrence,),
        citation_targets=(citation_ref,),
        individual_evidence_target_sets=(individual,),
        joint_target_set_contract_version="citation-target-v1",
    )
    _members, _joint, grounding_fact = CitationGroundingValidator(
        support_adapter=ctx["adapter"]
    ).evaluate(
        validation_run_id=relevancy["run"].validation_run_id,
        subject_hash=relevancy["run"].subject_key.subject_hash,
        claim_target=ctx["target"],
        grounding_target=grounding_target,
        support_semantic_contract_version="support-contract-v1",
        operation_context=ctx["context"],
    )

    assert ctx["fact"].semantic_state is ValidationSemanticState.PASS
    assert grounding_fact.family is ValidationFactFamily.CITATION_GROUNDING
    assert grounding_fact.semantic_state is ValidationSemanticState.FAIL


def test_faithfulness_pass_does_not_imply_consistency_pass(tmp_path) -> None:
    consistency = _consistency_fixture(
        tmp_path / "consistency",
        lhs_value=100,
        rhs_value=120,
    )
    faithful = _faithfulness_pass(
        tmp_path / "faithfulness",
        artifact_ref=consistency["artifact_ref"],
        subject_hash=consistency["run"].subject_key.subject_hash,
        validation_run_id=consistency["run"].validation_run_id,
    )
    consistency_fact = ConsistencyProjector.project(
        validation_run_id=consistency["run"].validation_run_id,
        subject_hash=consistency["run"].subject_key.subject_hash,
        plan=consistency["plan"],
        conformance=consistency["conformance"],
        binding=consistency["binding"],
        evaluation=consistency["evaluation"],
    )

    assert faithful["fact"].semantic_state is ValidationSemanticState.PASS
    assert consistency_fact.semantic_state is ValidationSemanticState.FAIL
    assert (
        consistency_fact.payload["relation"]
        == ArtifactConsistencyRelation.INCONSISTENT.value
    )


def test_coverage_missing_and_grounding_not_applicable_when_citation_absent(
    tmp_path,
) -> None:
    fx = _coverage_fixture(
        tmp_path,
        semantic_presence=SemanticPresenceObservation.NO_MATCH_AFTER_COMPLETE_DOMAIN,
        matched_refs=(),
    )
    relation = CoverageSemanticEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
        assessment=fx["assessment"],
    )
    coverage_fact = CoverageProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target=fx["target"],
        match_target_set=fx["match_set"],
        relation=relation,
        observed_count=0,
    )

    grounding_item = ValidationCompletionItemSchema(
        expected_validation_item_ref="dimension:citation-grounding",
        required=False,
        state=ValidationCompletionItemState.NOT_APPLICABLE,
    )
    assert coverage_fact.semantic_state is ValidationSemanticState.FAIL
    assert relation is CoverageRelation.MISSING
    assert grounding_item.state is ValidationCompletionItemState.NOT_APPLICABLE
    assert not grounding_item.fact_refs


def test_invalid_citation_propagates_grounding_not_applicable_not_fake_fail(
    tmp_path,
) -> None:
    fx = _relevancy_fixture(tmp_path)
    occurrence = CitationOccurrenceTargetSchema(
        occurrence_ref="citation-occurrence://invalid",
        artifact_state_ref=fx["artifact_ref"],
        validation_subject_hash=fx["run"].subject_key.subject_hash,
        citation_alias="BAD",
        source_span_ref="span://citation/invalid",
        occurrence_hash=canonical_sha256({"occurrence": "invalid"}),
    )
    integrity = CitationIntegrityValidator.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        occurrence=occurrence,
        binding_state=CitationOccurrenceBindingState.INVALID,
        citation_target_ref=None,
        reason_codes=("UNRESOLVED_ALIAS",),
    )
    integrity_ref = fx["store"].put_fact_if_absent_or_same(integrity)
    completion = ValidationCompletionBuilder.build(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="dimension:citation-integrity",
                required=True,
                state=ValidationCompletionItemState.FAIL,
                fact_refs=(integrity_ref,),
            ),
            ValidationCompletionItemSchema(
                expected_validation_item_ref="dimension:citation-grounding",
                required=False,
                state=ValidationCompletionItemState.NOT_APPLICABLE,
            ),
        ),
    )

    assert integrity.semantic_state is ValidationSemanticState.FAIL
    grounding = [
        item
        for item in completion.items
        if item.expected_validation_item_ref == "dimension:citation-grounding"
    ][0]
    assert grounding.state is ValidationCompletionItemState.NOT_APPLICABLE
    assert not grounding.fact_refs


def test_shared_claim_target_does_not_merge_support_and_consistency_authority(
    tmp_path,
) -> None:
    consistency = _consistency_fixture(tmp_path / "consistency")
    faithful = _faithfulness_pass(
        tmp_path / "claim",
        artifact_ref=consistency["artifact_ref"],
        subject_hash=consistency["run"].subject_key.subject_hash,
        validation_run_id=consistency["run"].validation_run_id,
    )
    consistency_fact = ConsistencyProjector.project(
        validation_run_id=consistency["run"].validation_run_id,
        subject_hash=consistency["run"].subject_key.subject_hash,
        plan=consistency["plan"],
        conformance=consistency["conformance"],
        binding=consistency["binding"],
        evaluation=consistency["evaluation"],
    )

    assert faithful["target"].claim_target_id != consistency_fact.target_ref
    assert faithful["fact"].family is ValidationFactFamily.FAITHFULNESS
    assert consistency_fact.family is ValidationFactFamily.ARTIFACT_CONSISTENCY


def test_shared_requirement_call_preserves_independent_relevancy_and_coverage_facts(
    tmp_path,
) -> None:
    fx = _coverage_fixture(tmp_path)
    relevancy_fact = RelevancyValidator.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target_set=fx["relevancy_target"],
        assessment=fx["assessment"],
    )
    relation = CoverageSemanticEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
        assessment=fx["assessment"],
    )
    coverage_fact = CoverageProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target=fx["target"],
        match_target_set=fx["match_set"],
        relation=relation,
        observed_count=1,
        related_refs=(fx["assessment"].assessment_ref,),
    )

    assert relevancy_fact.fact_id != coverage_fact.fact_id
    assert relevancy_fact.family is ValidationFactFamily.REQUIREMENT_ADHERENCE
    assert coverage_fact.family is ValidationFactFamily.COVERAGE_COMPLETENESS
    assert fx["assessment"].assessment_ref in coverage_fact.payload["related_refs"]


def test_physical_requirement_sharing_preserves_independent_completion_items(
    tmp_path,
) -> None:
    fx = _coverage_fixture(tmp_path)
    relevancy_fact = RelevancyValidator.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target_set=fx["relevancy_target"],
        assessment=fx["assessment"],
    )
    coverage_relation = CoverageSemanticEvaluator.evaluate(
        target=fx["target"],
        match_target_set=fx["match_set"],
        assessment=fx["assessment"],
    )
    coverage_fact = CoverageProjector.project(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        target=fx["target"],
        match_target_set=fx["match_set"],
        relation=coverage_relation,
        observed_count=1,
    )
    relevancy_ref = fx["store"].put_fact_if_absent_or_same(relevancy_fact)
    coverage_ref = fx["store"].put_fact_if_absent_or_same(coverage_fact)
    completion = ValidationCompletionBuilder.build(
        validation_run_id=fx["run"].validation_run_id,
        subject_hash=fx["run"].subject_key.subject_hash,
        items=(
            ValidationCompletionItemSchema(
                expected_validation_item_ref="dimension:relevancy",
                required=True,
                state=ValidationCompletionItemState.PASS,
                fact_refs=(relevancy_ref,),
            ),
            ValidationCompletionItemSchema(
                expected_validation_item_ref="dimension:coverage",
                required=True,
                state=ValidationCompletionItemState.PASS,
                fact_refs=(coverage_ref,),
            ),
        ),
    )

    assert len(completion.items) == 2
    assert completion.items[0].fact_refs != completion.items[1].fact_refs


def test_not_applicable_completion_cannot_be_silently_promoted_to_pass() -> None:
    item = ValidationCompletionItemSchema(
        expected_validation_item_ref="dimension:grounding",
        required=False,
        state=ValidationCompletionItemState.NOT_APPLICABLE,
    )
    assert item.state is ValidationCompletionItemState.NOT_APPLICABLE

    with pytest.raises(ValueError, match="PASS requires canonical Fact refs or positive proof refs"):
        ValidationCompletionItemSchema(
            expected_validation_item_ref="dimension:grounding",
            required=False,
            state=ValidationCompletionItemState.PASS,
        )


def test_output_conformance_does_not_create_a_sixth_quality_dimension() -> None:
    assert OutputConformanceState.CONFORMING.value == "CONFORMING"
    assert "OUTPUT_CONFORMANCE" not in {
        family.value for family in ValidationFactFamily
    }
    assert set(ValidationFactFamily) == {
        ValidationFactFamily.CLAIM_EVIDENCE_SUPPORT,
        ValidationFactFamily.FAITHFULNESS,
        ValidationFactFamily.REQUIREMENT_ADHERENCE,
        ValidationFactFamily.CITATION_INTEGRITY,
        ValidationFactFamily.CITATION_GROUNDING,
        ValidationFactFamily.COVERAGE_COMPLETENESS,
        ValidationFactFamily.ARTIFACT_CONSISTENCY,
    }


def test_p3_validator_services_have_no_cross_authority_dependencies() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    service_root = (
        backend_root
        / "apps"
        / "enterprise_document"
        / "services"
        / "post_write"
    )
    files = (
        "shared_requirement.py",
        "relevancy.py",
        "coverage.py",
        "consistency.py",
    )
    forbidden = (
        "ModelGateway",
        "ToolGateway",
        "PostWriteControl",
        "DeliveryGate",
        "PostWriteRelease",
        "ReleasePublication",
        "SchemeDraft",
        "latest_document",
        "latest_scheme",
        "retrieval.",
        "correction.",
    )
    for filename in files:
        source = (service_root / filename).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, (
                f"{filename} crosses validator authority via {token}"
            )


def test_p3_validator_services_reuse_common_intermediate_store_without_new_head() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    service_root = (
        backend_root
        / "apps"
        / "enterprise_document"
        / "services"
        / "post_write"
    )
    schema_source = (
        backend_root
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "post_write"
        / "quality.py"
    ).read_text(encoding="utf-8")
    sources = "\n".join(
        (service_root / filename).read_text(encoding="utf-8")
        for filename in (
            "shared_requirement.py",
            "relevancy.py",
            "coverage.py",
            "consistency.py",
        )
    )

    assert "ValidationIntermediateRuntime" in sources
    forbidden = (
        "RequirementArtifactAssessmentStore",
        "RelevancyStore",
        "CoverageStore",
        "ConsistencyStore",
        "RequirementArtifactAssessmentHead",
        "RelevancyHead",
        "CoverageHead",
        "ConsistencyHead",
    )
    for token in forbidden:
        assert token not in sources
        assert token not in schema_source
