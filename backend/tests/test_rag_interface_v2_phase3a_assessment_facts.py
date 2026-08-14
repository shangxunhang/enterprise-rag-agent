from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.services.scheme_writer.evidence_gate_policy import (
    EvidenceGatePolicy,
)
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.result_mapper import RAGResultMapper
from rag.schema.prompt_evidence_candidate_v1 import (
    build_prompt_evidence_candidate_v1,
)
from schemas.claim import ClaimType
from schemas.rag import RAGEvidenceAssessmentSchema
from test_support.stage_d_fixtures import (
    document_profile,
    evidence_requirements,
    prompt_selected,
    retrieval_result,
)


def _evaluate(
    *,
    profile: dict | None = None,
    topics: list[str] | None = None,
    fact_status: str = "confirmed",
):
    return OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                profile=profile,
                text="预算金额：250万元",
                fact_status=fact_status,
            )
        ],
        runtime_context=evidence_requirements(topics=topics),
    )


def _gate(result):
    return EvidenceGatePolicy().evaluate(result.public_assessment)


def test_public_assessment_json_round_trip_uses_the_public_claim_enum() -> None:
    assessment = _evaluate().public_assessment

    reloaded = RAGEvidenceAssessmentSchema.model_validate(
        assessment.model_dump(mode="json")
    )

    assert reloaded == assessment
    assert reloaded.supported_claim_types
    assert all(isinstance(item, ClaimType) for item in reloaded.supported_claim_types)


def test_set_claim_capabilities_follow_eligible_union_and_forbidden_subtraction() -> None:
    profile = document_profile(
        allowed_claim_types=["current_project_cost", "procurement_requirement"],
        forbidden_claim_types=["general_method"],
    )
    result = _evaluate(profile=profile)
    assessment = result.public_assessment
    eligible = [item for item in result.item_assessments if item.eligibility]
    expected_supported = set().union(
        *(set(item.supported_claim_types) for item in eligible),
        set(),
    )
    expected_forbidden = set().union(
        *(set(item.forbidden_claim_types) for item in result.item_assessments),
        set(),
    ) - expected_supported

    assert set(assessment.supported_claim_types) == expected_supported
    assert set(assessment.forbidden_claim_types) == expected_forbidden
    assert ClaimType.PROCUREMENT_REQUIREMENT in assessment.supported_claim_types
    assert assessment.forbidden_claim_types == [ClaimType.GENERAL_METHOD]
    assert set(assessment.supported_claim_types).isdisjoint(
        assessment.forbidden_claim_types
    )
    assert set(_gate(result).allowed_claim_types) == expected_supported


def test_authority_and_reference_role_are_explicit_public_evidence_facts() -> None:
    authoritative = _evaluate().public_assessment
    history_profile = document_profile(
        document_id="history-1",
        kb_id="historical_case_kb",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        project_relation="other_project",
        project_id="other-project",
        authority_level="reference",
        allowed_claim_types=["general_method", "reference_case"],
    )
    historical_result = _evaluate(profile=history_profile)

    assert authoritative.authoritative_evidence_present is True
    assert authoritative.historical_or_reference_evidence_present is False
    assert historical_result.public_assessment.authoritative_evidence_present is False
    assert (
        historical_result.public_assessment
        .historical_or_reference_evidence_present
        is True
    )
    assert _gate(historical_result).delivery_mode is DeliveryMode.QUALIFIED


def test_fact_status_qualifiers_are_stable_public_usage_constraints() -> None:
    assessment = _evaluate(fact_status="estimated").public_assessment

    assert assessment.required_qualifiers == [
        "根据现有估算材料",
        "最终金额以审批或采购结果为准",
    ]
    assert RAGEvidenceAssessmentSchema.model_validate(
        assessment.model_dump(mode="json")
    ).required_qualifiers == assessment.required_qualifiers


@pytest.mark.parametrize(
    ("topics", "results", "expected_ratio", "expected_missing", "delivery"),
    [
        (
            ["预算金额", "资金来源"],
            [retrieval_result()],
            0.5,
            ["资金来源"],
            DeliveryMode.HUMAN_REVIEW,
        ),
        (
            ["预算金额"],
            [],
            0.0,
            ["预算金额"],
            DeliveryMode.BLOCKED,
        ),
    ],
)
def test_partial_like_and_absent_like_facts_drive_application_gate(
    topics: list[str],
    results: list[dict],
    expected_ratio: float,
    expected_missing: list[str],
    delivery: DeliveryMode,
) -> None:
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额和资金来源",
        results=results,
        runtime_context=evidence_requirements(topics=topics),
    )

    assert result.public_assessment.coverage_ratio == pytest.approx(expected_ratio)
    assert result.public_assessment.missing_topics == expected_missing
    assert _gate(result).delivery_mode is delivery


def test_conflicts_are_public_facts_for_the_application_gate() -> None:
    first = document_profile(document_id="doc-a", profile_char="a")
    second = document_profile(document_id="doc-b", profile_char="b")
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                child_id="child-a",
                profile=first,
                value="250万元",
            ),
            retrieval_result(
                child_id="child-b",
                profile=second,
                value="300万元",
                text="预算金额：300万元",
                rank=2,
            ),
        ],
        runtime_context=evidence_requirements(),
    )

    assert result.public_assessment.conflicts == [
        item.model_dump(mode="json", exclude_none=True)
        for item in result.set_assessment.conflicts
    ]
    assert result.public_assessment.conflicts
    assert _gate(result).delivery_mode is DeliveryMode.HUMAN_REVIEW


def test_prompt_source_and_missing_contract_are_explicit_public_facts() -> None:
    missing = OnlineEvidenceGovernanceV2().evaluate_prompt(
        query="预算金额",
        prompt_candidates=[],
        runtime_context={
            **evidence_requirements(),
            "prompt_evidence_contract_missing": True,
        },
        prompt_evidence_source="missing_blocked",
    )
    selected = prompt_selected(
        retrieval_result(),
        prompt_text="预算金额：250万元",
    )
    legacy = OnlineEvidenceGovernanceV2().evaluate_prompt(
        query="预算金额",
        prompt_candidates=[build_prompt_evidence_candidate_v1(selected)],
        runtime_context=evidence_requirements(),
        prompt_evidence_source="legacy_fallback",
    )

    assert missing.public_assessment.prompt_evidence_source == "missing_blocked"
    assert missing.public_assessment.prompt_evidence_contract_missing is True
    assert _gate(missing).delivery_mode is DeliveryMode.BLOCKED
    assert legacy.public_assessment.prompt_evidence_source == "legacy_fallback"
    assert legacy.public_assessment.prompt_evidence_contract_missing is False
    assert _gate(legacy).delivery_mode is DeliveryMode.QUALIFIED

    mapped = RAGResultMapper._assessment_from_governance(
        missing.to_dict(),
        legacy_assessment=RAGEvidenceAssessmentSchema(),
    )
    assert mapped.prompt_evidence_source == "missing_blocked"
    assert mapped.prompt_evidence_contract_missing is True


def test_business_actions_are_not_public_assessment_fields() -> None:
    fields = RAGEvidenceAssessmentSchema.model_fields

    for forbidden in (
        "human_review_required",
        "claim_risk_level",
        "delivery_mode",
        "generation_allowed",
        "gate_decision",
    ):
        assert forbidden not in fields


def test_legacy_gate_details_cannot_restore_public_evidence_fact_authority() -> None:
    public = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status="sufficient",
        coverage_ratio=1.0,
        supported_claim_types=[ClaimType.GENERAL_METHOD],
        authoritative_evidence_present=False,
        details={
            "gate_decision": {
                "allowed_claim_types": ["current_project_cost"],
                "authoritative_evidence_present": True,
            }
        },
    )
    mapped = RAGResultMapper._assessment_from_governance(
        {
            "public_assessment": public.model_dump(mode="json"),
            "set_assessment": {"status": "sufficient", "coverage_ratio": 1.0},
            "gate_decision": {
                "allowed_claim_types": ["current_project_cost"],
                "metadata": {"authoritative_evidence_present": True},
            },
        },
        legacy_assessment=RAGEvidenceAssessmentSchema(),
    )

    assert mapped.supported_claim_types == [ClaimType.GENERAL_METHOD]
    assert mapped.authoritative_evidence_present is False
