"""Focused contract tests for Interface V2 Phase 1 authority convergence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from rag.catalog.document_profile import ClaimType as LegacyClaimType
from rag.evidence.governance_v2 import resolve_evidence_requirements_v2
from rag.schema.evidence_assessment_v2 import (
    CoverageStrategy,
    EvidenceConflictSchema,
    EvidenceSetAssessmentSchema,
    EvidenceStatus as InternalEvidenceStatus,
)
from schemas.claim import ClaimType
from schemas.rag import (
    EvidenceAssessmentStatus,
    EvidenceRequirementSchema,
    EvidenceRequirementsSchema,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
)


EXPECTED_CLAIM_VALUES = [
    "general_method",
    "typical_architecture",
    "reference_case",
    "current_project_fact",
    "current_project_cost",
    "current_project_quantity",
    "current_project_schedule",
    "current_project_configuration",
    "policy_requirement",
    "technical_specification",
    "procurement_requirement",
    "template_structure",
    "style_guidance",
]


def test_claim_type_old_and_new_paths_share_one_thirteen_value_enum() -> None:
    assert LegacyClaimType is ClaimType
    assert [item.value for item in ClaimType] == EXPECTED_CLAIM_VALUES


def test_evidence_status_has_one_public_three_state_enum() -> None:
    assert InternalEvidenceStatus is EvidenceStatus
    assert EvidenceAssessmentStatus is EvidenceStatus
    assert [item.value for item in EvidenceStatus] == [
        "sufficient",
        "insufficient",
        "conflicted",
    ]


@pytest.mark.parametrize("legacy_status", ["partial", "absent"])
def test_legacy_assessment_status_projects_to_insufficient(legacy_status: str) -> None:
    assessment = RAGEvidenceAssessmentSchema.model_validate(
        {
            "status": legacy_status,
            "evidence_available": legacy_status == "partial",
            "coverage_ratio": 0.5 if legacy_status == "partial" else 0.0,
            "required_topics": ["主题A", "主题B"],
            "covered_topics": ["主题A"] if legacy_status == "partial" else [],
            "missing_topics": ["主题B"] if legacy_status == "partial" else ["主题A", "主题B"],
        }
    )

    assert assessment.assessment_state == "completed"
    assert assessment.status is EvidenceStatus.INSUFFICIENT


def test_conflicted_status_remains_conflicted() -> None:
    conflict = {
        "conflict_id": "conflict-1",
        "topic": "预算",
        "evidence_ids": ["E1", "E2"],
    }
    assessment = RAGEvidenceAssessmentSchema(
        assessment_state="completed",
        status=EvidenceStatus.CONFLICTED,
        conflicts=[conflict],
    )
    assert assessment.status is EvidenceStatus.CONFLICTED


def test_assessment_lifecycle_requires_null_pending_and_status_completed() -> None:
    assert RAGEvidenceAssessmentSchema().status is None
    with pytest.raises(ValidationError, match="pending assessment requires status=null"):
        RAGEvidenceAssessmentSchema(
            assessment_state="pending",
            status=EvidenceStatus.SUFFICIENT,
        )
    with pytest.raises(ValidationError, match="completed assessment requires"):
        RAGEvidenceAssessmentSchema(assessment_state="completed")


def test_requirement_envelope_supports_multiple_items_and_stable_ids() -> None:
    first = EvidenceRequirementsSchema.from_topics(
        section_id="section-1",
        section_title="安全设计",
        section_type="security",
        topics=["访问控制", "日志审计"],
    )
    replay = EvidenceRequirementsSchema.from_topics(
        section_id="section-1",
        section_title="安全设计",
        section_type="security",
        topics=["访问控制", "日志审计"],
    )

    assert len(first.requirements) == 2
    assert first.required_topics == ["访问控制", "日志审计"]
    assert [item.requirement_id for item in first.requirements] == [
        item.requirement_id for item in replay.requirements
    ]


def test_evidence_requirements_canonical_json_dump_round_trips() -> None:
    requirements = EvidenceRequirementsSchema.from_topics(
        section_id="section-round-trip",
        section_title="安全设计",
        section_type="security",
        topics=["访问控制", "日志审计"],
    )

    payload = requirements.model_dump(mode="json")
    reloaded = EvidenceRequirementsSchema.model_validate(payload)

    assert reloaded == requirements


def test_evidence_requirements_canonical_dump_excludes_legacy_projection() -> None:
    requirements = EvidenceRequirementsSchema.from_topics(
        section_id="section-canonical",
        section_type="security",
        topics=["访问控制", "日志审计"],
    )

    payload = requirements.model_dump(mode="json")

    assert "required_topics" not in payload
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceRequirementsSchema.model_validate(
            {**payload, "required_topics": ["伪造的第二套主题 authority"]}
        )


def test_evidence_requirements_required_topics_remains_read_only_projection() -> None:
    requirements = EvidenceRequirementsSchema.from_topics(
        section_id="section-projection",
        section_type="security",
        topics=["访问控制", "日志审计"],
    )

    assert requirements.required_topics == [
        item.topic for item in requirements.requirements
    ]


def test_typed_requirements_are_authority_over_legacy_requested_claim_types() -> None:
    requirement = EvidenceRequirementSchema(
        requirement_id="requirement-access-control",
        topic="访问控制",
        purpose="support the section",
        expected_source_types=["policy_standard"],
    )
    resolved = resolve_evidence_requirements_v2(
        query="安全设计",
        runtime_context={
            "evidence_requirements": {
                "section_id": "section-security",
                "section_title": "安全设计",
                "section_type": "security",
                "requirements": [requirement.model_dump()],
                "requested_claim_types": ["current_project_cost"],
            }
        },
    )

    assert resolved.requirements == [requirement]
    assert resolved.required_topics == ["访问控制"]
    assert resolved.requested_claim_types == [ClaimType.CURRENT_PROJECT_COST]
    assert resolved.metadata["requested_claim_types_ignored"] is True


def test_legacy_set_and_gate_inputs_project_without_expanding_enum() -> None:
    partial = EvidenceSetAssessmentSchema.model_validate(
        {
            "assessment_id": "assessment-1",
            "section_id": "section-1",
            "section_type": "security",
            "strategy": CoverageStrategy.TOPIC_UNION,
            "status": "partial",
            "coverage_ratio": 0.5,
            "required_topics": ["访问控制", "日志审计"],
            "covered_topics": ["访问控制"],
            "missing_topics": ["日志审计"],
            "direct_evidence_count": 1,
            "evidence_ids": ["E1"],
            "distinct_document_count": 1,
        }
    )
    gate = GateDecisionSchema.model_validate(
        {
            "policy_id": "legacy-partial",
            "evidence_status": "partial",
            "delivery_mode": DeliveryMode.QUALIFIED,
            "claim_risk_level": ClaimRiskLevel.LOW,
            "allowed_claim_types": [ClaimType.GENERAL_METHOD],
            "required_qualifiers": ["仅覆盖已具备证据的部分"],
            "covered_topics": ["访问控制"],
            "missing_topics": ["日志审计"],
        }
    )

    assert partial.status is EvidenceStatus.INSUFFICIENT
    assert gate.evidence_status is EvidenceStatus.INSUFFICIENT


def test_conflicted_set_requires_conflict_not_insufficient_projection() -> None:
    conflict = EvidenceConflictSchema(
        conflict_id="conflict-1",
        topic="预算",
        evidence_ids=["E1", "E2"],
        description="预算值冲突",
    )
    assessment = EvidenceSetAssessmentSchema(
        assessment_id="assessment-conflicted",
        section_id="section-1",
        section_type="cost",
        strategy=CoverageStrategy.TOPIC_UNION,
        status=EvidenceStatus.CONFLICTED,
        coverage_ratio=1.0,
        required_topics=["预算"],
        covered_topics=["预算"],
        direct_evidence_count=2,
        distinct_document_count=2,
        evidence_ids=["E1", "E2"],
        conflicts=[conflict],
    )
    assert assessment.status is EvidenceStatus.CONFLICTED
