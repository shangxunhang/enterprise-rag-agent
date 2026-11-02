"""Stage A contract-freeze tests for evidence-aware document generation."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.gate_decision import (
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from rag.catalog.document_profile import (
    AuthorityLevel,
    AuthorityProfileSchema,
    ChunkStrategy,
    ClaimType,
    ClassificationMethod,
    ClassificationProfileSchema,
    ContentCapabilitiesSchema,
    DocumentProfileSchema,
    DocumentType,
    EvidenceRole,
    ParserStrategy,
    ParsingProfileSchema,
    ProjectRelation,
    ProjectRelationSchema,
)
from rag.evidence.eligibility_policy import (
    EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
    EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
)
from rag.schema.evidence_assessment_v2 import (
    CoverageStrategy,
    EvidenceAssessmentV2Schema,
    EvidenceConflictSchema,
    EvidenceRelevance,
    EvidenceSetAssessmentSchema,
    EvidenceStatus,
)


def _historical_profile() -> DocumentProfileSchema:
    return DocumentProfileSchema(
        document_id="doc_history_001",
        document_type=DocumentType.HISTORICAL_CONSTRUCTION_SCHEME,
        evidence_role=EvidenceRole.HISTORICAL_CASE,
        kb_id="historical_case_kb",
        tenant_id="default",
        source_uri="data/raw/jsonl/doc_history_001.jsonl",
        source_name="历史项目建设方案.pdf",
        title="历史项目建设方案",
        project_relation=ProjectRelationSchema(
            relation=ProjectRelation.OTHER_PROJECT,
            project_id="project_history_001",
        ),
        authority=AuthorityProfileSchema(
            level=AuthorityLevel.REFERENCE,
            issuer="某设计单位",
            version="v1",
        ),
        content_capabilities=ContentCapabilitiesSchema(
            allowed_claim_types=[
                ClaimType.GENERAL_METHOD,
                ClaimType.TYPICAL_ARCHITECTURE,
                ClaimType.REFERENCE_CASE,
            ],
            forbidden_claim_types=[
                ClaimType.CURRENT_PROJECT_FACT,
                ClaimType.CURRENT_PROJECT_COST,
                ClaimType.CURRENT_PROJECT_QUANTITY,
                ClaimType.CURRENT_PROJECT_SCHEDULE,
                ClaimType.CURRENT_PROJECT_CONFIGURATION,
            ],
        ),
        parsing=ParsingProfileSchema(
            parser_strategy=ParserStrategy.HIERARCHICAL_DOCUMENT,
            chunk_strategy=ChunkStrategy.HEADING_PARENT_PARAGRAPH_CHILD,
        ),
        classification=ClassificationProfileSchema(
            method=ClassificationMethod.MANUAL_SEED,
            confidence=1.0,
            reviewed=True,
        ),
    )


def test_document_profile_round_trips_as_json_contract() -> None:
    profile = _historical_profile()

    payload = profile.model_dump(mode="json")
    restored = DocumentProfileSchema.model_validate(payload)

    assert restored == profile
    assert payload["schema_version"] == "document_profile_v1"
    assert payload["document_type"] == "historical_construction_scheme"
    assert payload["content_capabilities"]["allowed_claim_types"] == [
        "general_method",
        "typical_architecture",
        "reference_case",
    ]


def test_document_profile_rejects_cross_project_fact_capability() -> None:
    payload = _historical_profile().model_dump()
    payload["content_capabilities"]["allowed_claim_types"].append(
        ClaimType.CURRENT_PROJECT_QUANTITY
    )
    payload["content_capabilities"]["forbidden_claim_types"].remove(
        ClaimType.CURRENT_PROJECT_QUANTITY
    )

    with pytest.raises(ValidationError, match="historical-case documents cannot allow"):
        DocumentProfileSchema.model_validate(payload)


def test_document_profile_rejects_role_mismatch_and_unknown_fields() -> None:
    payload = _historical_profile().model_dump()
    payload["evidence_role"] = EvidenceRole.PROJECT_FACT

    with pytest.raises(ValidationError, match="requires evidence_role=historical_case"):
        DocumentProfileSchema.model_validate(payload)

    payload = _historical_profile().model_dump()
    payload["undeclared_field"] = "must fail"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DocumentProfileSchema.model_validate(payload)


def test_authority_profile_rejects_invalid_effective_window() -> None:
    with pytest.raises(ValidationError, match="expiration_date"):
        AuthorityProfileSchema(
            level=AuthorityLevel.OFFICIAL,
            effective_date=date(2026, 8, 2),
            expiration_date=date(2026, 8, 1),
        )


def test_evidence_item_separates_relevance_from_eligibility() -> None:
    item = EvidenceAssessmentV2Schema(
        evidence_id="parent_001",
        document_id="doc_history_001",
        document_type=DocumentType.HISTORICAL_CONSTRUCTION_SCHEME,
        evidence_role=EvidenceRole.HISTORICAL_CASE,
        project_relation=ProjectRelation.OTHER_PROJECT,
        authority_level=AuthorityLevel.REFERENCE,
        eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
        eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
        semantic_relevance=0.78,
        eligibility=True,
        relevance=EvidenceRelevance.PARTIAL,
        supported_topics=["访问控制", "日志审计"],
        supported_claim_types=[ClaimType.GENERAL_METHOD, ClaimType.REFERENCE_CASE],
        forbidden_claim_types=[ClaimType.CURRENT_PROJECT_FACT],
    )

    assert item.eligibility is True
    assert item.semantic_relevance == pytest.approx(0.78)
    assert item.relevance is EvidenceRelevance.PARTIAL


def test_ineligible_evidence_cannot_expose_supported_claim_types() -> None:
    with pytest.raises(ValidationError, match="ineligible evidence"):
        EvidenceAssessmentV2Schema(
            evidence_id="template_001",
            document_id="doc_template_001",
            document_type=DocumentType.DOCUMENT_TEMPLATE,
            evidence_role=EvidenceRole.TEMPLATE_STYLE,
            project_relation=ProjectRelation.GENERAL,
            authority_level=AuthorityLevel.REFERENCE,
            eligibility_semantic_policy_id=EVIDENCE_USE_ELIGIBILITY_POLICY_ID,
            eligibility_semantic_policy_version=EVIDENCE_USE_ELIGIBILITY_POLICY_VERSION,
            semantic_relevance=0.95,
            eligibility=False,
            relevance=EvidenceRelevance.RELEVANT,
            supported_topics=["章节结构"],
            supported_claim_types=[ClaimType.TEMPLATE_STRUCTURE],
        )


def test_insufficient_evidence_set_preserves_partial_coverage_facts() -> None:
    assessment = EvidenceSetAssessmentSchema(
        assessment_id="assessment_security_001",
        section_id="security_design",
        section_type="security_design",
        strategy=CoverageStrategy.TOPIC_UNION,
        status=EvidenceStatus.INSUFFICIENT,
        coverage_ratio=0.57,
        required_topics=[
            "身份认证",
            "访问控制",
            "最小权限",
            "数据加密",
            "日志审计",
            "等级保护",
            "接口安全",
        ],
        covered_topics=["身份认证", "访问控制", "数据加密", "日志审计"],
        missing_topics=["最小权限", "等级保护", "接口安全"],
        direct_evidence_count=1,
        partial_evidence_count=3,
        distinct_document_count=3,
        evidence_ids=["parent_001", "parent_002", "parent_003", "parent_004"],
    )

    assert assessment.status is EvidenceStatus.INSUFFICIENT
    assert assessment.coverage_ratio == pytest.approx(0.57)

    with pytest.raises(ValidationError, match="requires missing_topics"):
        EvidenceSetAssessmentSchema(
            assessment_id="invalid_partial",
            section_id="security_design",
            section_type="security_design",
            strategy=CoverageStrategy.TOPIC_UNION,
            status=EvidenceStatus.INSUFFICIENT,
            coverage_ratio=0.5,
            required_topics=["访问控制"],
            covered_topics=["访问控制"],
            missing_topics=[],
        )


def test_conflicted_evidence_set_requires_explicit_conflict() -> None:
    conflict = EvidenceConflictSchema(
        conflict_id="conflict_quantity_001",
        topic="GPU 服务器数量",
        claim_type=ClaimType.CURRENT_PROJECT_QUANTITY,
        evidence_ids=["fact_001", "fact_002"],
        description="两个当前项目确认材料给出不同数量",
        candidate_values={"fact_001": 2, "fact_002": 4},
    )
    assessment = EvidenceSetAssessmentSchema(
        assessment_id="assessment_inventory_001",
        section_id="resource_configuration",
        section_type="resource_configuration",
        strategy=CoverageStrategy.VERSIONED_FACT_RESOLUTION,
        status=EvidenceStatus.CONFLICTED,
        coverage_ratio=1.0,
        required_topics=["GPU 服务器数量"],
        covered_topics=["GPU 服务器数量"],
        direct_evidence_count=2,
        distinct_document_count=2,
        evidence_ids=["fact_001", "fact_002"],
        conflicts=[conflict],
    )

    assert assessment.conflicts[0].claim_type is ClaimType.CURRENT_PROJECT_QUANTITY

    with pytest.raises(ValidationError, match="requires at least one conflict"):
        EvidenceSetAssessmentSchema(
            assessment_id="invalid_conflicted",
            section_id="resource_configuration",
            section_type="resource_configuration",
            strategy=CoverageStrategy.VERSIONED_FACT_RESOLUTION,
            status=EvidenceStatus.CONFLICTED,
            coverage_ratio=1.0,
        )


def test_gate_decision_supports_all_delivery_modes() -> None:
    grounded = GateDecisionSchema(
        policy_id="project_fact_v1",
        evidence_status=EvidenceStatus.SUFFICIENT,
        delivery_mode=DeliveryMode.GROUNDED,
        claim_risk_level=ClaimRiskLevel.HIGH,
        allowed_claim_types=[ClaimType.CURRENT_PROJECT_FACT],
        covered_topics=["建设范围"],
    )
    qualified = GateDecisionSchema(
        policy_id="historical_case_scheme_v1",
        evidence_status=EvidenceStatus.INSUFFICIENT,
        delivery_mode=DeliveryMode.QUALIFIED,
        claim_risk_level=ClaimRiskLevel.LOW,
        allowed_claim_types=[ClaimType.GENERAL_METHOD, ClaimType.REFERENCE_CASE],
        forbidden_claim_types=[ClaimType.CURRENT_PROJECT_QUANTITY],
        required_qualifiers=["建议", "参考同类项目", "需结合本项目确认"],
        covered_topics=["访问控制"],
        missing_topics=["等级保护"],
        human_review_required=True,
    )
    framework = GateDecisionSchema(
        policy_id="ordinary_section_absent_v1",
        evidence_status=EvidenceStatus.INSUFFICIENT,
        delivery_mode=DeliveryMode.FRAMEWORK_ONLY,
        allowed_claim_types=[ClaimType.TEMPLATE_STRUCTURE],
        missing_topics=["资源配置"],
    )
    blocked = GateDecisionSchema(
        policy_id="high_risk_absent_v1",
        evidence_status=EvidenceStatus.INSUFFICIENT,
        delivery_mode=DeliveryMode.BLOCKED,
        claim_risk_level=ClaimRiskLevel.CRITICAL,
        forbidden_claim_types=[ClaimType.CURRENT_PROJECT_COST],
        missing_topics=["投资金额"],
    )
    human_review = GateDecisionSchema(
        policy_id="conflicted_project_fact_v1",
        evidence_status=EvidenceStatus.CONFLICTED,
        delivery_mode=DeliveryMode.HUMAN_REVIEW,
        claim_risk_level=ClaimRiskLevel.HIGH,
        forbidden_claim_types=[ClaimType.CURRENT_PROJECT_QUANTITY],
        human_review_required=True,
        reason_codes=["current_project_fact_conflict"],
    )

    assert {item.delivery_mode for item in (
        grounded,
        qualified,
        framework,
        blocked,
        human_review,
    )} == set(DeliveryMode)


def test_gate_rejects_insufficient_high_risk_qualified_delivery() -> None:
    with pytest.raises(ValidationError, match="insufficient high-risk claims"):
        GateDecisionSchema(
            policy_id="invalid_high_risk_partial_v1",
            evidence_status=EvidenceStatus.INSUFFICIENT,
            delivery_mode=DeliveryMode.QUALIFIED,
            claim_risk_level=ClaimRiskLevel.HIGH,
            allowed_claim_types=[ClaimType.CURRENT_PROJECT_QUANTITY],
            required_qualifiers=["待确认"],
            covered_topics=["设备类型"],
            missing_topics=["设备数量"],
        )


def test_gate_rejects_silent_assertion_when_evidence_is_conflicted() -> None:
    with pytest.raises(ValidationError, match="must be blocked or sent to human_review"):
        GateDecisionSchema(
            policy_id="invalid_conflict_v1",
            evidence_status=EvidenceStatus.CONFLICTED,
            delivery_mode=DeliveryMode.GROUNDED,
            allowed_claim_types=[ClaimType.CURRENT_PROJECT_FACT],
        )


def test_json_schema_freezes_required_enum_values() -> None:
    document_schema = DocumentProfileSchema.model_json_schema()
    gate_schema = GateDecisionSchema.model_json_schema()
    evidence_set_schema = EvidenceSetAssessmentSchema.model_json_schema()

    assert set(document_schema["$defs"]["DocumentType"]["enum"]) == {
        item.value for item in DocumentType
    }
    assert set(gate_schema["$defs"]["DeliveryMode"]["enum"]) == {
        item.value for item in DeliveryMode
    }
    assert set(evidence_set_schema["$defs"]["EvidenceStatus"]["enum"]) == {
        item.value for item in EvidenceStatus
    }


def test_json_schema_freezes_top_level_contract_fields() -> None:
    schemas = {
        DocumentProfileSchema: {
            "schema_version",
            "document_id",
            "document_type",
            "evidence_role",
            "kb_id",
            "tenant_id",
            "source_uri",
            "source_name",
            "title",
            "project_relation",
            "authority",
            "content_capabilities",
            "parsing",
            "classification",
            "metadata",
        },
        EvidenceAssessmentV2Schema: {
            "schema_version",
            "evidence_id",
            "document_id",
            "document_type",
            "evidence_role",
            "project_relation",
            "authority_level",
            "eligibility_semantic_policy_id",
            "eligibility_semantic_policy_version",
            "semantic_relevance",
            "eligibility",
            "relevance",
            "supported_topics",
            "supported_claim_types",
            "forbidden_claim_types",
            "reason_codes",
            "metadata",
        },
        EvidenceSetAssessmentSchema: {
            "schema_version",
            "assessment_id",
            "section_id",
            "section_type",
            "strategy",
            "status",
            "coverage_ratio",
            "required_topics",
            "covered_topics",
            "missing_topics",
            "direct_evidence_count",
            "partial_evidence_count",
            "distinct_document_count",
            "evidence_ids",
            "conflicts",
            "reason_codes",
            "metadata",
        },
        GateDecisionSchema: {
            "schema_version",
            "policy_id",
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
            "metadata",
        },
    }

    for schema_class, expected_fields in schemas.items():
        json_schema = schema_class.model_json_schema()
        assert set(json_schema["properties"]) == expected_fields
        assert json_schema["additionalProperties"] is False
