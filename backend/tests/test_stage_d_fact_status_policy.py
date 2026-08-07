from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.schema.evidence_assessment_v2 import EvidenceStatus

from test_support.stage_d_fixtures import (
    document_profile,
    evidence_requirements,
    retrieval_result,
)


def _evaluate(*, status: str, authority: str = "official", form: str = "procurement_document"):
    profile = document_profile(
        authority_level=authority,
        source_document_form=form,
    )
    return OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                profile=profile,
                text="预算金额：250万元",
                value="250万元",
                unit="万元",
                fact_status=status,
            )
        ],
        runtime_context=evidence_requirements(),
    )


def test_unknown_fact_status_cannot_be_grounded() -> None:
    result = _evaluate(status="unknown")

    assert result.set_assessment.status is EvidenceStatus.ABSENT
    assert result.gate_decision.delivery_mode is DeliveryMode.BLOCKED
    assert result.gate_decision.allowed_claim_types == []
    assert "fact_status_unknown" in result.item_assessments[0].reason_codes


def test_estimated_fact_is_qualified_with_estimate_language() -> None:
    result = _evaluate(status="estimated")

    assert result.set_assessment.status is EvidenceStatus.SUFFICIENT
    assert result.gate_decision.delivery_mode is DeliveryMode.QUALIFIED
    assert result.gate_decision.human_review_required is True
    assert "根据现有估算材料" in result.gate_decision.required_qualifiers
    assert "最终金额以审批或采购结果为准" in result.gate_decision.required_qualifiers
    assert "fact_status_estimated" in result.item_assessments[0].reason_codes


@pytest.mark.parametrize(
    ("status", "qualifier"),
    [("proposed", "拟"), ("planned", "计划")],
)
def test_proposed_and_planned_facts_require_qualified_expression(
    status: str,
    qualifier: str,
) -> None:
    result = _evaluate(status=status)

    assert result.gate_decision.delivery_mode is DeliveryMode.QUALIFIED
    assert result.gate_decision.human_review_required is True
    assert qualifier in result.gate_decision.required_qualifiers
    assert "尚待确认" in result.gate_decision.required_qualifiers


def test_confirmed_fact_requires_authoritative_document() -> None:
    official = _evaluate(status="confirmed", authority="official")
    reference = _evaluate(status="confirmed", authority="reference")

    assert official.gate_decision.delivery_mode is DeliveryMode.GROUNDED
    assert reference.gate_decision.delivery_mode is DeliveryMode.BLOCKED
    assert "confirmed_fact_requires_authoritative_document" in (
        reference.item_assessments[0].reason_codes
    )


def test_decided_fact_requires_formal_decision_source() -> None:
    formal = _evaluate(
        status="decided",
        authority="official",
        form="procurement_result",
    )
    informal = _evaluate(
        status="decided",
        authority="official",
        form="feasibility_study",
    )

    assert formal.gate_decision.delivery_mode is DeliveryMode.GROUNDED
    assert informal.gate_decision.delivery_mode is DeliveryMode.BLOCKED
    assert "decided_fact_requires_formal_authoritative_source" in (
        informal.item_assessments[0].reason_codes
    )


def test_other_project_drops_current_project_claim_but_retains_reference_capability() -> None:
    profile = document_profile(
        document_id="doc-history",
        kb_id="historical_case_kb",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        project_relation="other_project",
        project_id="other-project",
        authority_level="official",
        allowed_claim_types=["current_project_cost", "reference_case"],
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                profile=profile,
                text="预算金额：250万元",
                fact_status="confirmed",
            )
        ],
        runtime_context=evidence_requirements(),
    )

    assert result.gate_decision.delivery_mode is DeliveryMode.QUALIFIED
    assert [
        item.value for item in result.gate_decision.allowed_claim_types
    ] == ["reference_case"]
    assert "current_project_claim_requires_current_project_relation" in (
        result.item_assessments[0].reason_codes
    )


def test_real_structured_fact_conflict_requires_human_review() -> None:
    first_profile = document_profile(
        document_id="doc-current-a",
        profile_char="a",
    )
    second_profile = document_profile(
        document_id="doc-current-b",
        profile_char="b",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                child_id="child-a",
                profile=first_profile,
                text="预算金额：250万元",
                value="250万元",
                unit="万元",
                fact_status="confirmed",
            ),
            retrieval_result(
                child_id="child-b",
                profile=second_profile,
                text="预算金额：300万元",
                value="300万元",
                unit="万元",
                fact_status="confirmed",
                rank=2,
            ),
        ],
        runtime_context=evidence_requirements(),
    )

    assert result.set_assessment.status is EvidenceStatus.CONFLICTED
    assert result.gate_decision.delivery_mode is DeliveryMode.HUMAN_REVIEW
    assert result.gate_decision.allowed_claim_types == []
    assert result.gate_decision.human_review_required is True
    assert result.to_dict()["generation_allowed"] is False


@pytest.mark.parametrize(
    ("first_value", "first_unit", "second_value", "second_unit"),
    [
        ("250 万元", "万元", "250万元", "万元"),
        ("250万元", "万元", "2500000元", "元"),
    ],
)
def test_equivalent_explicit_money_values_do_not_conflict(
    first_value: str,
    first_unit: str,
    second_value: str,
    second_unit: str,
) -> None:
    first_profile = document_profile(
        document_id="doc-current-a",
        profile_char="a",
    )
    second_profile = document_profile(
        document_id="doc-current-b",
        profile_char="b",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                child_id="child-a",
                profile=first_profile,
                text=f"预算金额：{first_value}",
                value=first_value,
                unit=first_unit,
                fact_status="confirmed",
            ),
            retrieval_result(
                child_id="child-b",
                profile=second_profile,
                text=f"预算金额：{second_value}",
                value=second_value,
                unit=second_unit,
                fact_status="confirmed",
                rank=2,
            ),
        ],
        runtime_context=evidence_requirements(),
    )

    assert result.set_assessment.status is EvidenceStatus.SUFFICIENT
    assert result.set_assessment.conflicts == []


def test_qualified_inequality_value_is_not_silently_merged_with_exact_value() -> None:
    first_profile = document_profile(
        document_id="doc-current-a",
        profile_char="a",
    )
    second_profile = document_profile(
        document_id="doc-current-b",
        profile_char="b",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            retrieval_result(
                child_id="child-a",
                profile=first_profile,
                text="预算金额：250万元",
                value="250万元",
                unit="万元",
                fact_status="confirmed",
            ),
            retrieval_result(
                child_id="child-b",
                profile=second_profile,
                text="预算金额：不超过250万元",
                value="不超过250万元",
                unit="万元",
                fact_status="confirmed",
                rank=2,
            ),
        ],
        runtime_context=evidence_requirements(),
    )

    assert result.set_assessment.status is EvidenceStatus.CONFLICTED
