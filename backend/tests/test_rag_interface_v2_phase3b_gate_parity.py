from __future__ import annotations

from pathlib import Path

import pytest

from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.services.scheme_writer.evidence_gate_policy import (
    APPLICATION_EVIDENCE_GATE_POLICY_ID,
    EvidenceGatePolicy,
)
from rag.evidence.governance_v2 import (
    EvidenceGovernanceV2Result,
    OnlineEvidenceGovernanceV2,
)
from rag.schema.prompt_evidence_candidate_v1 import (
    build_prompt_evidence_candidate_v1,
)
from test_support.stage_d_fixtures import (
    document_profile,
    evidence_requirements,
    prompt_selected,
    retrieval_result,
)


_CORE_FIELDS = (
    "evidence_status",
    "delivery_mode",
    "claim_risk_level",
    "allowed_claim_types",
    "forbidden_claim_types",
    "required_qualifiers",
    "covered_topics",
    "missing_topics",
    "human_review_required",
    "reason_codes",
)
_CORE_METADATA_FIELDS = (
    "authoritative_evidence_present",
    "historical_or_reference_evidence_present",
    "prompt_evidence_source",
)


def _new_gate(result: EvidenceGovernanceV2Result) -> GateDecisionSchema:
    return EvidenceGatePolicy().evaluate(result.public_assessment)


def _runtime_context(*, topics: list[str] | None = None) -> dict:
    """Use the normal production identity, not the shared fixture's test policy."""

    context = evidence_requirements(topics=topics)
    context["evidence_requirements"].pop("policy_id", None)
    return context


def _assert_parity(result: EvidenceGovernanceV2Result) -> GateDecisionSchema:
    return _new_gate(result)


def _evaluate(
    *,
    profile: dict | None = None,
    results: list[dict] | None = None,
    topics: list[str] | None = None,
    query: str = "预算金额",
) -> EvidenceGovernanceV2Result:
    return OnlineEvidenceGovernanceV2().evaluate(
        query=query,
        results=(
            results
            if results is not None
            else [retrieval_result(profile=profile)]
        ),
        runtime_context=_runtime_context(topics=topics),
    )


def test_default_application_policy_identity_is_frozen() -> None:
    result = _evaluate()
    decision = EvidenceGatePolicy().evaluate(result.public_assessment)

    assert APPLICATION_EVIDENCE_GATE_POLICY_ID == "candidate_capability_union_v1"
    assert decision.policy_id == APPLICATION_EVIDENCE_GATE_POLICY_ID


def test_sufficient_authoritative_application_gate_regression() -> None:
    decision = _assert_parity(_evaluate())

    assert decision.delivery_mode is DeliveryMode.GROUNDED
    assert decision.claim_risk_level is ClaimRiskLevel.HIGH


def test_sufficient_template_only_application_gate_regression() -> None:
    profile = document_profile(
        document_id="template-1",
        kb_id="template_style_kb",
        document_type="document_template",
        evidence_role="template_style",
        project_relation="general",
        project_id=None,
        authority_level="draft",
        allowed_claim_types=["template_structure", "style_guidance"],
    )
    result = _evaluate(
        query="章节结构",
        topics=["章节结构"],
        results=[
            retrieval_result(
                profile=profile,
                text="建设方案章节结构包括项目背景和建设内容",
                representation_type="template_section",
                fact_category=None,
                predicate=None,
                value=None,
                unit=None,
            )
        ],
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.GROUNDED
    assert decision.claim_risk_level is ClaimRiskLevel.LOW


def test_sufficient_historical_reference_application_gate_regression() -> None:
    profile = document_profile(
        document_id="history-1",
        kb_id="historical_case_kb",
        document_type="historical_construction_scheme",
        evidence_role="historical_case",
        project_relation="other_project",
        project_id="other-project",
        authority_level="reference",
        allowed_claim_types=["general_method", "reference_case"],
    )
    result = _evaluate(
        profile=profile,
        query="访问控制设计",
        topics=["访问控制"],
        results=[
            retrieval_result(
                profile=profile,
                text="历史项目访问控制采用统一认证和最小权限方法",
                fact_category="general",
                predicate=None,
                value=None,
                unit=None,
            )
        ],
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.QUALIFIED
    assert decision.required_qualifiers == ["建议", "参考同类项目", "需结合本项目确认"]


def test_sufficient_current_project_non_authoritative_application_gate_regression() -> None:
    profile = document_profile(
        document_type="current_project_requirement",
        evidence_role="project_fact",
        authority_level="reference",
        allowed_claim_types=["current_project_fact"],
    )
    result = _evaluate(
        profile=profile,
        query="项目背景",
        topics=["项目背景"],
        results=[
            retrieval_result(
                profile=profile,
                text="本项目背景为现有业务平台升级改造",
                representation_type="paragraph_text",
                fact_category=None,
                predicate=None,
                value=None,
                unit=None,
            )
        ],
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.QUALIFIED
    assert decision.human_review_required is True
    assert decision.required_qualifiers == ["依据当前可用材料", "相关事实仍需业务确认"]


@pytest.mark.parametrize("fact_status", ["estimated", "proposed", "planned"])
def test_sufficient_fact_status_qualifier_application_gate_regression(
    fact_status: str,
) -> None:
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[retrieval_result(fact_status=fact_status)],
        runtime_context=_runtime_context(),
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.QUALIFIED
    assert decision.human_review_required is True
    assert decision.required_qualifiers == result.public_assessment.required_qualifiers


def test_insufficient_partial_high_risk_application_gate_regression() -> None:
    decision = _assert_parity(
        _evaluate(topics=["预算金额", "资金来源"])
    )

    assert decision.delivery_mode is DeliveryMode.HUMAN_REVIEW
    assert decision.allowed_claim_types == []


def test_insufficient_partial_low_risk_application_gate_regression() -> None:
    profile = document_profile(
        document_id="history-2",
        kb_id="historical_case_kb",
        document_type="historical_construction_scheme",
        evidence_role="historical_case",
        project_relation="other_project",
        project_id="other-project",
        authority_level="reference",
        allowed_claim_types=["general_method", "reference_case"],
    )
    result = _evaluate(
        query="访问控制与审计设计",
        topics=["访问控制", "审计设计"],
        results=[
            retrieval_result(
                profile=profile,
                text="历史项目访问控制采用最小权限方法",
                fact_category="general",
                predicate=None,
                value=None,
                unit=None,
            )
        ],
    )
    decision = _assert_parity(result)

    assert decision.claim_risk_level is ClaimRiskLevel.LOW
    assert decision.delivery_mode is DeliveryMode.QUALIFIED
    assert decision.human_review_required is True


def test_insufficient_absent_application_gate_regression() -> None:
    decision = _assert_parity(_evaluate(results=[]))

    assert decision.delivery_mode is DeliveryMode.BLOCKED
    assert decision.allowed_claim_types == []


def test_conflicted_application_gate_regression() -> None:
    first = document_profile(document_id="doc-a", profile_char="a")
    second = document_profile(document_id="doc-b", profile_char="b")
    result = _evaluate(
        results=[
            retrieval_result(child_id="child-a", profile=first, value="250万元"),
            retrieval_result(
                child_id="child-b",
                profile=second,
                text="预算金额：300万元",
                value="300万元",
                rank=2,
            ),
        ]
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.HUMAN_REVIEW
    assert decision.human_review_required is True


def test_prompt_contract_missing_application_gate_regression() -> None:
    result = OnlineEvidenceGovernanceV2().evaluate_prompt(
        query="预算金额",
        prompt_candidates=[],
        runtime_context={
            **_runtime_context(),
            "prompt_evidence_contract_missing": True,
        },
        prompt_evidence_source="missing_blocked",
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.BLOCKED
    assert "prompt_evidence_contract_missing" in decision.reason_codes


def test_legacy_prompt_fallback_application_gate_regression() -> None:
    selected = prompt_selected(
        retrieval_result(),
        prompt_text="预算金额：250万元",
    )
    result = OnlineEvidenceGovernanceV2().evaluate_prompt(
        query="预算金额",
        prompt_candidates=[build_prompt_evidence_candidate_v1(selected)],
        runtime_context=_runtime_context(),
        prompt_evidence_source="legacy_fallback",
    )
    decision = _assert_parity(result)

    assert decision.delivery_mode is DeliveryMode.QUALIFIED
    assert decision.human_review_required is True
    assert "legacy_prompt_evidence_fallback" in decision.reason_codes


def test_application_policy_has_no_rag_internal_imports() -> None:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "enterprise_document"
        / "services"
        / "scheme_writer"
        / "evidence_gate_policy.py"
    )
    source = policy_path.read_text(encoding="utf-8")

    forbidden_imports = (
        "rag.evidence",
        "rag.schema.evidence_assessment_v2",
        "rag.schema.retrieval_candidate_v2",
        "rag.catalog.document_profile",
    )
    assert all(item not in source for item in forbidden_imports)
    assert "from schemas.rag import" in source
    assert "from schemas.claim import" in source
    assert "apps.enterprise_document.schemas.gate_decision" in source


def test_application_gate_behavior_has_complete_frozen_decision_shape() -> None:
    decision = _new_gate(_evaluate())

    for field_name in _CORE_FIELDS:
        assert getattr(decision, field_name) is not None
    for field_name in _CORE_METADATA_FIELDS:
        assert field_name in decision.metadata
