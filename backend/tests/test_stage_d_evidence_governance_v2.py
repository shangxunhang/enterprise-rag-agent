from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.services.scheme_writer.evidence_gate_policy import (
    EvidenceGatePolicy,
)
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.schema.evidence_assessment_v2 import EvidenceStatus


def _gate(result):
    return EvidenceGatePolicy().evaluate(result.public_assessment)


def _profile(
    *,
    document_id: str,
    document_type: str,
    evidence_role: str,
    project_relation: str,
    authority_level: str,
    allowed: list[str],
    forbidden: list[str] | None = None,
    kb_id: str = "project_fact_kb",
    profile_char: str = "a",
) -> dict:
    return {
        "schema_version": "document_profile_v1",
        "document_id": document_id,
        "document_type": document_type,
        "evidence_role": evidence_role,
        "kb_id": kb_id,
        "tenant_id": "default",
        "project_relation": project_relation,
        "project_id": "project-1" if project_relation != "general" else None,
        "authority": {
            "level": authority_level,
            "version": "v1",
            "issuer": "test",
            "effective_date": None,
            "expiration_date": None,
        },
        "content_capabilities": {
            "allowed_claim_types": allowed,
            "forbidden_claim_types": forbidden or [],
        },
        "parsing": {
            "parser_strategy": "project_fact_extraction",
            "chunk_strategy": "fact_record",
            "fallback_chunk_strategy": "fixed_token_fallback",
        },
        "profile_sha256": profile_char * 64,
        "source_sha256": profile_char.upper() * 64,
        "source_document_form": "test",
        "taxonomy_mapping": None,
        "should_index": True,
        "is_gold_reference": False,
    }


def _result(
    *,
    child_id: str,
    document_id: str,
    text: str,
    profile: dict,
    rank: int = 1,
    predicate: str | None = None,
    value: str | None = None,
    fact_category: str = "cost",
    fact_status: str = "confirmed",
    unit: str | None = None,
) -> dict:
    is_template = profile["evidence_role"] == "template_style"
    if is_template:
        type_aware = {
            "strategy": "template_section",
            "original_strategy": "template_section",
            "actual_strategy": "template_section",
            "representation_type": "template_section",
            "fallback_used": False,
        }
        structured_extra = {
            "section_path": ["建设方案"],
            "section_title": "章节结构",
            "required_fields": ["项目背景", "建设内容"],
            "placeholder_fields": [],
            "style_hints": ["按章节组织"],
            "is_example_content": False,
        }
    else:
        type_aware = {
            "strategy": "fact_record",
            "original_strategy": "fact_record",
            "actual_strategy": "fact_record",
            "representation_type": "fact_record",
            "fallback_used": False,
        }
        structured_extra = {
            "record_id": f"record-{child_id}",
            "fact_category": fact_category,
            "subject": document_id,
            "predicate": predicate,
            "value": value,
            "unit": unit,
            "fact_status": fact_status,
            "source_span": {"text": text},
        }
    extra = {
        "document_profile": profile,
        "type_aware_chunking": type_aware,
        **structured_extra,
    }
    return {
        "rank": rank,
        "score": 0.9,
        "rerank_score": 0.9,
        "chunk_id": child_id,
        "child_chunk_id": child_id,
        "parent_chunk_id": f"parent-{child_id}",
        "doc_id": document_id,
        "tenant_id": "default",
        "kb_id": profile["kb_id"],
        "file_id": f"{document_id}.docx",
        "document_type": profile["document_type"],
        "evidence_role": profile["evidence_role"],
        "project_relation": profile["project_relation"],
        "project_id": profile["project_id"],
        "authority_level": profile["authority"]["level"],
        "child_text": text,
        "parent_text": text,
        "text": text,
        "metadata": {
            "document_profile": profile,
            "type_aware_chunking": type_aware,
            "retrieval_sources": ["dense", "keyword"],
        },
        "extra": extra,
        "child_chunk": {
            "chunk_id": child_id,
            "child_chunk_id": child_id,
            "parent_chunk_id": f"parent-{child_id}",
            "doc_id": document_id,
            "tenant_id": "default",
            "kb_id": profile["kb_id"],
            "file_id": f"{document_id}.docx",
            "text": text,
            "extra": extra,
        },
    }


def _context(
    *,
    topics: list[str],
    claims: list[str],
    risk: str = "low",
) -> dict:
    return {
        "section_id": "section-1",
        "section_title": topics[0],
        "section_type": "test_section",
        "evidence_requirements": {
            "policy_id": "test_policy_v1",
            "section_id": "section-1",
            "section_type": "test_section",
            "required_topics": topics,
            "requested_claim_types": claims,
            "claim_risk_level": risk,
        },
    }


def test_historical_evidence_cannot_support_current_project_cost_but_retains_reference_capabilities() -> None:
    profile = _profile(
        document_id="history-1",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        project_relation="other_project",
        authority_level="reference",
        allowed=["general_method", "reference_case"],
        forbidden=["current_project_cost"],
        kb_id="historical_case_kb",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="项目预算金额",
        results=[
            _result(
                child_id="history-child",
                document_id="history-1",
                text="历史项目预算金额为250万元",
                profile=profile,
                predicate="预算金额",
                value="250万元",
            )
        ],
        runtime_context=_context(
            topics=["预算金额"],
            claims=["current_project_cost"],
            risk="high",
        ),
    )

    assert result.item_assessments[0].eligibility is True
    assert result.set_assessment.status is EvidenceStatus.SUFFICIENT
    assert _gate(result).delivery_mode is DeliveryMode.QUALIFIED
    assert "current_project_cost" not in {
        item.value for item in _gate(result).allowed_claim_types
    }
    assert {item.value for item in _gate(result).allowed_claim_types} == {
        "general_method",
        "reference_case",
    }
    assert result.eligible_candidate_ids == ("history-child",)


def test_historical_method_evidence_is_qualified_not_grounded() -> None:
    profile = _profile(
        document_id="history-1",
        document_type="historical_construction_scheme",
        evidence_role="historical_case",
        project_relation="other_project",
        authority_level="reference",
        allowed=["general_method", "reference_case"],
        kb_id="historical_case_kb",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="访问控制设计方法",
        results=[
            _result(
                child_id="history-child",
                document_id="history-1",
                text="访问控制设计采用最小权限和统一身份认证方法",
                profile=profile,
                fact_category="general",
            )
        ],
        runtime_context=_context(
            topics=["访问控制"],
            claims=["general_method"],
        ),
    )

    assert result.set_assessment.status is EvidenceStatus.SUFFICIENT
    assert _gate(result).delivery_mode is DeliveryMode.QUALIFIED
    assert "参考同类项目" in _gate(result).required_qualifiers


def test_official_current_project_cost_can_be_grounded() -> None:
    profile = _profile(
        document_id="current-1",
        document_type="tender_requirement",
        evidence_role="procurement_requirement",
        project_relation="current_project",
        authority_level="official",
        allowed=["current_project_cost", "procurement_requirement"],
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            _result(
                child_id="current-child",
                document_id="current-1",
                text="预算金额：250000元",
                profile=profile,
                predicate="预算金额",
                value="250000元",
            )
        ],
        runtime_context=_context(
            topics=["预算金额"],
            claims=["current_project_cost"],
            risk="high",
        ),
    )

    assert result.item_assessments[0].eligibility is True
    assert result.set_assessment.status is EvidenceStatus.SUFFICIENT
    assert _gate(result).delivery_mode is DeliveryMode.GROUNDED
    assert _gate(result).allowed_claim_types[0].value == "current_project_cost"


def test_reference_current_project_cost_requires_qualification_and_review() -> None:
    profile = _profile(
        document_id="current-1",
        document_type="current_project_requirement",
        evidence_role="project_fact",
        project_relation="current_project",
        authority_level="reference",
        allowed=["current_project_cost"],
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="概算投资",
        results=[
            _result(
                child_id="current-child",
                document_id="current-1",
                text="概算投资为250万元",
                profile=profile,
                predicate="概算投资",
                value="250万元",
                unit="万元",
                fact_status="estimated",
            )
        ],
        runtime_context=_context(
            topics=["概算投资"],
            claims=["current_project_cost"],
            risk="high",
        ),
    )

    assert result.set_assessment.status is EvidenceStatus.SUFFICIENT
    assert _gate(result).delivery_mode is DeliveryMode.QUALIFIED
    assert _gate(result).human_review_required is True


def test_partial_high_risk_coverage_requires_human_review() -> None:
    profile = _profile(
        document_id="current-1",
        document_type="tender_requirement",
        evidence_role="procurement_requirement",
        project_relation="current_project",
        authority_level="official",
        allowed=["current_project_cost"],
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额和资金来源",
        results=[
            _result(
                child_id="current-child",
                document_id="current-1",
                text="预算金额：250000元",
                profile=profile,
                predicate="预算金额",
                value="250000元",
            )
        ],
        runtime_context=_context(
            topics=["预算金额", "资金来源"],
            claims=["current_project_cost"],
            risk="high",
        ),
    )

    assert result.set_assessment.status is EvidenceStatus.INSUFFICIENT
    assert result.set_assessment.coverage_ratio == pytest.approx(0.5)
    assert result.set_assessment.missing_topics == ["资金来源"]
    assert result.set_assessment.metadata["legacy_coverage_shape"] == "partial"
    assert _gate(result).delivery_mode is DeliveryMode.HUMAN_REVIEW
    assert _gate(result).allowed_claim_types == []


def test_conflicting_current_project_facts_are_not_silently_selected() -> None:
    first_profile = _profile(
        document_id="current-1",
        document_type="tender_requirement",
        evidence_role="procurement_requirement",
        project_relation="current_project",
        authority_level="official",
        allowed=["current_project_cost"],
        profile_char="a",
    )
    second_profile = _profile(
        document_id="current-2",
        document_type="current_project_decision",
        evidence_role="project_fact",
        project_relation="current_project",
        authority_level="confirmed",
        allowed=["current_project_cost"],
        profile_char="b",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[
            _result(
                child_id="child-1",
                document_id="current-1",
                text="预算金额：250000元",
                profile=first_profile,
                predicate="预算金额",
                value="250000元",
            ),
            _result(
                child_id="child-2",
                document_id="current-2",
                text="预算金额：300000元",
                profile=second_profile,
                predicate="预算金额",
                value="300000元",
                rank=2,
            ),
        ],
        runtime_context=_context(
            topics=["预算金额"],
            claims=["current_project_cost"],
            risk="high",
        ),
    )

    assert result.set_assessment.status is EvidenceStatus.CONFLICTED
    assert len(result.set_assessment.conflicts) == 1
    assert _gate(result).delivery_mode is DeliveryMode.HUMAN_REVIEW
    assert _gate(result).human_review_required is True


def test_template_evidence_only_supports_template_claims() -> None:
    profile = _profile(
        document_id="template-1",
        document_type="document_template",
        evidence_role="template_style",
        project_relation="general",
        authority_level="draft",
        allowed=["template_structure", "style_guidance"],
        forbidden=["current_project_fact"],
        kb_id="template_style_kb",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="建设方案章节结构",
        results=[
            _result(
                child_id="template-child",
                document_id="template-1",
                text="建设方案章节结构包括项目背景、建设内容和投资估算",
                profile=profile,
                fact_category="template",
            )
        ],
        runtime_context=_context(
            topics=["章节结构"],
            claims=["template_structure"],
        ),
    )

    assert result.item_assessments[0].eligibility is True
    assert _gate(result).delivery_mode is DeliveryMode.GROUNDED
    assert [item.value for item in _gate(result).allowed_claim_types] == [
        "style_guidance",
        "template_structure",
    ]


def test_query_wording_does_not_change_candidate_derived_claim_capabilities() -> None:
    profile = _profile(
        document_id="history-framework-1",
        document_type="historical_construction_scheme",
        evidence_role="historical_case",
        project_relation="other_project",
        authority_level="reference",
        allowed=["general_method", "reference_case", "typical_architecture"],
        kb_id="historical_case_kb",
    )
    results = [
        _result(
            child_id="history-framework-child",
            document_id="history-framework-1",
            text="建设方案采用分层架构和分阶段实施方法，可作为同类项目参考案例。",
            profile=profile,
            fact_category="general",
        )
    ]
    runtime_context = {
        "section_id": "section-framework",
        "section_title": "建设方案",
        "section_type": "framework",
        "evidence_requirements": {
            "policy_id": "candidate_capability_union_test_v1",
            "section_id": "section-framework",
            "section_type": "framework",
            "required_topics": ["建设方案"],
        },
    }

    allowed_by_query = {}
    for query in (
        "生成项目建设方案",
        "金额不得编造",
        "请给出预算金额",
        "品牌型号不得虚构",
    ):
        result = OnlineEvidenceGovernanceV2().evaluate(
            query=query,
            results=results,
            runtime_context=runtime_context,
        )
        allowed_by_query[query] = tuple(
            item.value for item in _gate(result).allowed_claim_types
        )

    assert len(set(allowed_by_query.values())) == 1
    assert next(iter(allowed_by_query.values())) == (
        "general_method",
        "reference_case",
        "typical_architecture",
    )


def test_other_project_profile_cannot_retain_current_project_cost_capability() -> None:
    profile = _profile(
        document_id="history-overbroad-1",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        project_relation="other_project",
        authority_level="reference",
        allowed=["current_project_cost", "reference_case", "typical_architecture"],
        kb_id="historical_case_kb",
    )
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="金额不得编造",
        results=[
            _result(
                child_id="history-overbroad-child",
                document_id="history-overbroad-1",
                text="历史项目预算金额及总体架构可作为同类项目参考案例。",
                profile=profile,
                predicate="预算金额",
                value="250万元",
            )
        ],
        runtime_context={
            "section_id": "section-history",
            "section_title": "历史项目参考",
            "section_type": "reference",
            "evidence_requirements": {
                "section_id": "section-history",
                "section_type": "reference",
                "required_topics": ["历史项目"],
            },
        },
    )

    allowed = {item.value for item in _gate(result).allowed_claim_types}
    assert "current_project_cost" not in allowed
    assert allowed == {"reference_case", "typical_architecture"}
    assert result.item_assessments[0].eligibility is True


def test_negative_cost_wording_does_not_eliminate_all_reference_evidence() -> None:
    profiles = [
        _profile(
            document_id="general-1",
            document_type="technical_whitepaper",
            evidence_role="technical_reference",
            project_relation="general",
            authority_level="reference",
            allowed=["general_method", "typical_architecture"],
            kb_id="technical_reference_kb",
            profile_char="c",
        ),
        _profile(
            document_id="history-2",
            document_type="historical_construction_scheme",
            evidence_role="historical_case",
            project_relation="other_project",
            authority_level="reference",
            allowed=["reference_case", "typical_architecture"],
            kb_id="historical_case_kb",
            profile_char="d",
        ),
    ]
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="生成项目建设方案，具体数量、型号、品牌和金额不得编造",
        results=[
            _result(
                child_id="general-child",
                document_id="general-1",
                text="建设方案可采用分层架构，并按照业务规模开展资源测算。",
                profile=profiles[0],
                fact_category="general",
            ),
            _result(
                child_id="history-child-2",
                document_id="history-2",
                text="历史项目采用分层架构，可作为建设方案参考案例。",
                profile=profiles[1],
                rank=2,
                fact_category="general",
            ),
        ],
        runtime_context={
            "section_id": "section-plan",
            "section_title": "建设方案",
            "section_type": "framework",
            "evidence_requirements": {
                "section_id": "section-plan",
                "section_type": "framework",
                "required_topics": ["建设方案"],
            },
        },
    )

    assert result.eligible_candidate_ids
    assert _gate(result).delivery_mode is not DeliveryMode.BLOCKED
    assert {item.value for item in _gate(result).allowed_claim_types} == {
        "general_method",
        "reference_case",
        "typical_architecture",
    }


def test_missing_profile_is_rejected_and_absent() -> None:
    raw = {
        "rank": 1,
        "score": 0.9,
        "chunk_id": "child-1",
        "child_chunk_id": "child-1",
        "parent_chunk_id": "parent-1",
        "doc_id": "doc-1",
        "tenant_id": "default",
        "kb_id": "project_fact_kb",
        "child_text": "预算金额：250000元",
        "parent_text": "预算金额：250000元",
        "metadata": {},
        "extra": {},
    }
    result = OnlineEvidenceGovernanceV2().evaluate(
        query="预算金额",
        results=[raw],
        runtime_context=_context(
            topics=["预算金额"],
            claims=["current_project_cost"],
            risk="high",
        ),
    )

    assert result.candidates == ()
    assert result.rejected_candidate_ids == ("child-1",)
    assert result.set_assessment.status is EvidenceStatus.INSUFFICIENT
    assert result.set_assessment.coverage_ratio == 0.0
    assert result.set_assessment.metadata["legacy_coverage_shape"] == "absent"
    assert _gate(result).delivery_mode is DeliveryMode.BLOCKED


def test_governance_cannot_revive_candidate_rejected_by_final_crag() -> None:
    profile = _profile(
        document_id="history-storage",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        project_relation="other_project",
        authority_level="reference",
        allowed=["general_method", "reference_case", "typical_architecture"],
        forbidden=[
            "current_project_configuration",
            "current_project_quantity",
        ],
        kb_id="historical_case_kb",
    )
    candidate = _result(
        child_id="history-storage-child",
        document_id="history-storage",
        text="历史项目采用 XSSD 存储并包含具体容量测算。",
        profile=profile,
        fact_category="general",
    )
    candidate["metadata"]["crag_final_judgement"] = {
        "evidence_id": "parent-history-storage-child",
        "relevance": "irrelevant",
        "confidence": 0.0,
        "reason": "not usable for the current section",
    }

    result = OnlineEvidenceGovernanceV2().evaluate(
        query="存储资源建设方案",
        results=[candidate],
        runtime_context=_context(
            topics=["存储资源建设方案"],
            claims=[],
        ),
    )

    assert result.eligible_candidate_ids == ()
    assert result.rejected_candidate_ids == ("history-storage-child",)
    assert result.set_assessment.status is EvidenceStatus.INSUFFICIENT
    assert result.set_assessment.coverage_ratio == 0.0
    assert result.set_assessment.metadata["legacy_coverage_shape"] == "absent"
    assert _gate(result).delivery_mode is DeliveryMode.BLOCKED
    assert "rejected_by_final_crag_irrelevant" in result.item_assessments[0].reason_codes
