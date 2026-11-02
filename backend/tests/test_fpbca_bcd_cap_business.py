"""Frozen BS01-12: local owners and authored Fake semantics, never real Model proof."""
from types import SimpleNamespace

import pytest

from core.hashing import canonical_sha256
from schemas.claim import ClaimType, CURRENT_PROJECT_CLAIM_TYPES
from schemas.evidence_source import AuthorityLevel, DocumentType, ProjectRelation, EvidenceRole
from schemas.evidence_assessment import EvidenceAssessmentV2Schema, EvidenceRelevance
from schemas.rag import EvidenceBundleSchema, RAGEvidenceItemSchema, RAGContextSchema
from rag.context.context_packer import ContextPacker
from rag.evidence.governance_v2 import resolve_fact_claim_eligibility
from rag.schema.structured_evidence import FactStatus
from apps.enterprise_document.services.agent_rag_v1.claim_pipeline import TargetClaimMaterializer
from apps.enterprise_document.services.agent_rag_v1.claim_composition import TargetClaimRuntimeValidators
from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer
from apps.enterprise_document.services.agent_rag_v1.claim_support_composition import TargetClaimSupportComposition
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimBindingRelation, ClaimBindingSupportKind
from backend.tests import local_verifier_recovery_helpers as native
from backend.tests.test_multi_agent_v3_writer_input_boundary import _instruction_pair, _project_input, r1_mainline
from backend.tests.test_multi_agent_v3_faithfulness_fth03 import _target_support, _requirement_ref, _subject_hash
from apps.enterprise_document.services.post_write.faithfulness import FaithfulnessCertaintyProjector


HISTORY = "历史项目采用双活架构，部署16台服务器。GOVERNED_BODY_NONCE"


def historical_candidate(**overrides):
    row = {
        "rank": 1, "doc_id": "history-doc", "chunk_id": "history-child",
        "child_chunk_id": "history-child", "parent_chunk_id": "history-parent",
        "title": "历史16台服务器章节", "section": "双活架构",
        "text": HISTORY,
        "metadata": {"evidence_assessment_v2": {
            "evidence_role": "historical_case", "project_relation": "other_project",
            "eligibility": True, "relevance": "relevant",
            "supported_claim_types": ["general_method", "reference_case"],
            "forbidden_claim_types": sorted(t.value for t in CURRENT_PROJECT_CLAIM_TYPES),
        }},
    }
    return {**row, **overrides}


def reference_bundle(text=HISTORY):
    assessment = EvidenceAssessmentV2Schema(
        evidence_id="history", document_id="history-doc",
        document_type="historical_construction_scheme", evidence_role="historical_case",
        project_relation="other_project", authority_level="reference",
        eligibility_semantic_policy_id="controlled-existing-policy", eligibility_semantic_policy_version="v1",
        semantic_relevance=1, eligibility=True, relevance="relevant",
        supported_claim_types=["general_method", "reference_case"],
        forbidden_claim_types=sorted(CURRENT_PROJECT_CLAIM_TYPES, key=lambda t: t.value),
    )
    item = RAGEvidenceItemSchema(evidence_id="history", disposition="selected", rank=1,
        matched_chunk_id="history-child", context_chunk_id="history-child", doc_id="history-doc",
        context_text=text, match_text=text, title="历史16台服务器")
    bundle = EvidenceBundleSchema(query="reference", items=[item], item_assessments=[assessment],
        selected_evidence_ids=["history"], context=RAGContextSchema(context_text=text,
            used_context_chars=len(text), context_item_count=1),
        assessment={"assessment_state": "completed", "status": "sufficient"})
    package = SimpleNamespace(selections=(SimpleNamespace(evidence_id="history"),))
    return bundle, package


def test_bs01_bs07_writer_current_fact_precedence_and_reference_proposal_instructions():
    _, material = _instruction_pair(_project_input("cap-controlled-task"))
    role = material.role_instruction
    assert "authoritative" in role.lower() and "historical" in role.lower()
    assert "replace" in role.lower()
    assert "qualified proposal" in role.lower()
    assert "practice as a fact supplied by this customer" in role


def test_bs01_bs07_actual_normal_fallback_writer_messages_keep_qualified_proposal_instruction(r1_mainline):
    """Real local route/provider input; the Fake response is not semantic proof."""
    _, request, package, services, _, invocation, _, _ = r1_mainline
    text = "\n".join(item["content"] for item in request.messages)
    for instruction in (
        "preserve that authoritative value; historical/reference values must never replace it",
        "clearly qualified proposal, estimate, planning assumption or suggested design",
        "keep the parameter unresolved/to be confirmed",
        "practice as a fact supplied by this customer",
    ):
        assert instruction in text
    actual = {"input_kind": "MESSAGES", "messages": request.messages}
    assert package.model_input.final_input_material.inline_final_input_payload == actual
    assert canonical_sha256(actual) == package.model_input.final_model_input_sha256
    route = services.route_decision_store.resolve_exact(invocation.route_decision_ref)
    expected = "fallback" if invocation.writer_subject_ref.run_id.endswith("fallback") else "normal"
    assert route.selected_route.value == expected
    assert invocation.generation_class.value == f"{expected}_write"


def test_bs02_bs11_reference_view_is_contextual_not_current_factual_eligibility():
    bundle, package = reference_bundle()
    before = bundle.model_dump(mode="json")
    assert TargetClaimMaterializer.eligible_evidence_ids(bundle, ClaimType.CURRENT_PROJECT_QUANTITY) == ()
    assert TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.CURRENT_PROJECT_QUANTITY) == ("history",)
    assert TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.GENERAL_METHOD) == ()
    assert bundle.model_dump(mode="json") == before


@pytest.mark.parametrize("variant", ["dropped", "empty", "irrelevant", "ineligible", "current", "unfinished"])
def test_bs04_bs11_no_source_promotion(variant):
    bundle, package = reference_bundle()
    if variant == "dropped":
        package = SimpleNamespace(selections=())
    elif variant == "empty":
        bundle = bundle.model_copy(update={"items": [bundle.items[0].model_copy(update={"context_text": " "})]})
    elif variant in {"irrelevant", "ineligible"}:
        assessment = bundle.item_assessments[0].model_copy(update={"eligibility": False,
            "supported_claim_types": [], "relevance": EvidenceRelevance.IRRELEVANT if variant == "irrelevant" else EvidenceRelevance.PARTIAL})
        bundle = EvidenceBundleSchema.model_validate(bundle.model_copy(update={"item_assessments": [assessment]}).model_dump(mode="json"))
    elif variant == "current":
        assessment = bundle.item_assessments[0].model_copy(update={"project_relation": ProjectRelation.CURRENT_PROJECT, "evidence_role": EvidenceRole.PROJECT_FACT, "authority_level": AuthorityLevel.AUTHORITATIVE})
        bundle = EvidenceBundleSchema.model_validate(bundle.model_copy(update={"item_assessments": [assessment]}).model_dump(mode="json"))
    else:
        bundle = bundle.model_copy(update={"assessment": bundle.assessment.model_copy(update={"assessment_state": "not_assessed"})})
    if variant == "unfinished":
        with pytest.raises(ValueError):
            TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.CURRENT_PROJECT_QUANTITY)
    else:
        assert TargetClaimRuntimeValidators.reference_proposal_evidence_ids(bundle, package, ClaimType.CURRENT_PROJECT_QUANTITY) == ()


@pytest.mark.parametrize("status", [FactStatus.ESTIMATED, FactStatus.PROPOSED, FactStatus.PLANNED])
def test_bs05_existing_current_fact_qualifiers(status):
    policy = resolve_fact_claim_eligibility(claim_type=ClaimType.CURRENT_PROJECT_QUANTITY,
        fact_status=status, authority_level=AuthorityLevel.AUTHORITATIVE,
        document_type=DocumentType.HISTORICAL_FEASIBILITY_STUDY,
        source_document_form=None, project_relation=ProjectRelation.CURRENT_PROJECT)
    assert policy.eligible and policy.required_qualifiers


def test_bs06_governed_historical_body_and_labels_visible():
    pack = ContextPacker(max_context_chars=6000).pack([historical_candidate()], token_budget=4000)
    assert HISTORY in pack.context and "历史16台服务器章节" in pack.context
    assert pack.selected_results[0]["metadata"]["raw_fact_visibility"] == "reference"
    assert "未向生成模型暴露" not in pack.context


@pytest.mark.parametrize("variant", ["ineligible", "irrelevant", "missing_eligibility"])
def test_bs06_unusable_historical_body_stays_restricted(variant):
    candidate = historical_candidate()
    assessment = candidate["metadata"]["evidence_assessment_v2"]
    if variant == "ineligible":
        assessment["eligibility"] = False
    elif variant == "irrelevant":
        assessment["relevance"] = "irrelevant"
    else:
        assessment.pop("eligibility")
    # Packer consumes the existing partial projection; full typed validation is
    # still owned upstream and by the Claim reference view.
    pack = ContextPacker(max_context_chars=6000).pack([candidate], token_budget=4000)
    assert HISTORY not in pack.context
    assert "GOVERNED_BODY_NONCE" not in pack.context
    assert pack.selected_results[0]["metadata"]["raw_fact_visibility"] == "restricted"
    assert pack.selected_results[0]["metadata"]["prompt_projection_mode"] == "historical_reference_restricted"


def test_bs08_raw_assessment_metadata_does_not_bypass_visible_projection():
    bundle, _ = reference_bundle()
    assessment = bundle.item_assessments[0].model_copy(update={"metadata": {"hidden_nonce": "RAW_OUTER_NONCE"}})
    material, _ = TargetClaimRuntimeValidators._visible_evidence_material(bundle.items[0], assessment)
    assert "RAW_OUTER_NONCE" not in str(material)
    assert material["context_text_sha256"] == canonical_sha256(HISTORY)


def test_bs09_same_projection_priority_membership_before_presentation():
    rows = [historical_candidate(rank=i, chunk_id=f"child-{i}", child_chunk_id=f"child-{i}", parent_chunk_id=f"parent-{i}") for i in range(1, 5)]
    packs = [ContextPacker(max_context_chars=10000, max_items=3, packing_strategy=s).pack(rows, token_budget=4000) for s in ("default", "lost_in_middle_aware")]
    assert [sorted(r["child_chunk_id"] for r in p.items) for p in packs] == [["child-1", "child-2", "child-3"]] * 2


def test_bs10_native_contextual_sources_commit_read_reopen_support_verifier(monkeypatch, tmp_path):
    """Actual native source reconstruction; existing authored support/Fake verifier."""
    original = native._completed_two_evidence_bundle
    def historical_native_bundle():
        bundle = original()
        assessments = [a.model_copy(update={"document_type": DocumentType.HISTORICAL_CONSTRUCTION_SCHEME,
            "evidence_role": EvidenceRole.HISTORICAL_CASE, "project_relation": ProjectRelation.OTHER_PROJECT, "authority_level": AuthorityLevel.REFERENCE,
            "supported_claim_types": [ClaimType.GENERAL_METHOD],
            "forbidden_claim_types": sorted(CURRENT_PROJECT_CLAIM_TYPES, key=lambda t: t.value)}) for a in bundle.item_assessments]
        items = [i.model_copy(update={"context_text": native.TEXT}) for i in bundle.items]
        return EvidenceBundleSchema.model_validate(bundle.model_copy(update={"item_assessments": assessments, "items": items}).model_dump(mode="json"))
    monkeypatch.setattr(native, "_completed_two_evidence_bundle", historical_native_bundle)
    fx = native.create_fixture(tmp_path / "native", script=[native.legal_reply("VERIFIED", ["@supporting"])])
    rows = fx.sources.materialize_sources(fx.claim, fx.context.active_lineage)
    evidence = tuple(r for r in rows if r.source_ref.source_kind == "EVIDENCE")
    assert len(evidence) == 2
    assert all(r.fact.support_kind is ClaimBindingSupportKind.CONTEXTUAL_SUPPORT and r.fact.relation is ClaimBindingRelation.CONTEXT_ONLY for r in evidence)
    read_rows = fx.sources.read_current_sources(fx.claim, fx.context.active_lineage)
    assert read_rows == rows
    reopened = native.reopen_fixture(fx.root)
    assert reopened.sources.read_current_sources(reopened.claim, reopened.context.active_lineage) == rows
    target = fx.support.evidence_target_set_ref
    assert set(target.evidence_member_refs) == {r.source_ref.exact_source_ref for r in evidence}
    assert fx.support_composition._scope(target, fx.context)[2] == evidence
    fx.composer._validate_resolved_sources(
        fx.runtime._binding_record_candidate(fx.binding), fx.binding, fx.support, rows
    )
    native.verify(fx)
    assert len(native.provider_rows(fx)) == 1  # Controlled Fake verdict, never real semantic proof.


def test_bs12_support_prompt_uses_existing_one_call_qualified_proposal_semantics(tmp_path):
    fx = native.create_fixture(tmp_path / "support-prompt")
    support = fx.support_composition
    support.model_gateway = fx.gateway
    target = fx.support.evidence_target_set_ref
    claim, binding, evidence = support._scope(target, fx.context)
    material = support._input_material(claim, binding, evidence, target)
    package, _ = support._package(material, target=target, context=fx.context, occurrence="cap-support-instruction")
    projected = str(package.model_dump(mode="json"))
    assert "CONFIRMED current-project fact" in projected
    assert "qualified proposal" in projected
    assert "ESCALATED" in projected and "INDETERMINATE" in projected
    assert not native.provider_rows(fx)  # Packaging itself dispatches no model.


@pytest.mark.parametrize("relation,expected", [("ALIGNED", "pass"), ("ESCALATED", "fail"), ("INDETERMINATE", "indeterminate")])
def test_bs03_bs12_existing_fth03_authored_certainty_outcome(tmp_path, relation, expected):
    target, support = _target_support(tmp_path)
    fact = FaithfulnessCertaintyProjector.project(validation_run_id="cap-certainty-control",
        subject_hash=_subject_hash(), claim_target=target, support_record=support,
        requirement_ref=_requirement_ref(), applicable_requirement_ids=(), certainty_relation=relation,
        certainty_reason_codes=("AUTHORED_FAKE_CERTAINTY_CONTROL",),
        physical_support_receipt_ref="artifact://controlled-cap/support", related_refs=())
    assert fact.semantic_state.value.lower() == expected
