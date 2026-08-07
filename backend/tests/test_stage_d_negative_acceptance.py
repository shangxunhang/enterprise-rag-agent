from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag.application.retrieval_scope import resolve_canonical_retrieval_scope
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from schemas.rag import RAGToolInputSchema

from test_support.stage_d_fixtures import (
    document_profile,
    evidence_requirements,
    retrieval_result,
)


def test_scope_identifier_order_is_set_semantics() -> None:
    scope = resolve_canonical_retrieval_scope(
        filter_expr=(
            'tenant_id == "default" and '
            'kb_id in ["project_fact_kb", "policy_standard_kb"] and '
            'doc_id in ["doc-b", "doc-a"]'
        ),
        keyword_scope={
            "tenant_id": "default",
            "kb_ids": ["policy_standard_kb", "project_fact_kb"],
            "doc_ids": ["doc-a", "doc-b"],
            "access_scope_enforced": True,
        },
        access_scope_enforced=True,
    )

    assert scope.kb_ids == ("policy_standard_kb", "project_fact_kb")
    assert scope.doc_ids == ("doc-a", "doc-b")


def test_unknown_keyword_scope_field_is_hard_failure() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        resolve_canonical_retrieval_scope(
            filter_expr=(
                'tenant_id == "default" and '
                'kb_id in ["project_fact_kb"]'
            ),
            keyword_scope={
                "tenant_id": "default",
                "kb_ids": ["project_fact_kb"],
                "kb_idz": ["template_style_kb"],
                "access_scope_enforced": True,
            },
            access_scope_enforced=True,
        )


@pytest.mark.parametrize(
    ("filter_expr", "message"),
    [
        ('kb_id in ["project_fact_kb"]', "requires tenant_id"),
        ('tenant_id == "default"', "requires at least one kb_id"),
    ],
)
def test_enforced_scope_missing_tenant_or_kb_fails_closed(
    filter_expr: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_canonical_retrieval_scope(
            filter_expr=filter_expr,
            keyword_scope=None,
            access_scope_enforced=True,
        )


def test_empty_prompt_does_not_create_framework_only_from_template_profile() -> None:
    profile = document_profile(
        document_id="template-doc",
        kb_id="template_style_kb",
        document_type="document_template",
        evidence_role="template_style",
        project_relation="general",
        project_id=None,
        authority_level="draft",
        allowed_claim_types=["template_structure", "style_guidance"],
        forbidden_claim_types=["current_project_fact"],
    )
    retrieval = retrieval_result(
        profile=profile,
        representation_type="template_section",
        text="建设方案章节结构",
    )
    governance = OnlineEvidenceGovernanceV2().evaluate_prompt(
        query="建设方案章节结构",
        prompt_candidates=[],
        runtime_context=evidence_requirements(
            topics=["章节结构"],
            claims=["template_structure"],
            risk="low",
            allow_framework_only=True,
        ),
    )

    assert retrieval  # prove a retrievable template exists but is not prompt-visible
    assert governance.set_assessment.status.value == "absent"
    assert governance.gate_decision.delivery_mode.value == "blocked"
    assert governance.gate_decision.allowed_claim_types == []
    assert governance.to_dict()["generation_allowed"] is False


def test_legacy_prompt_fallback_is_explicit_and_requires_review() -> None:
    request = RAGToolInputSchema(
        task_id="legacy-task",
        run_id="legacy-run",
        agent_name="LegacyResearchAgent",
        query="预算金额",
        max_context_chars=6000,
        max_context_items=3,
        extra={"retrieval_scope": "预算金额"},
    )
    invocation = RAGRequestMapper(allow_legacy_unscoped=True).map(request)
    result = retrieval_result()
    bundle = RAGResultMapper().map(
        request=request,
        invocation=invocation,
        raw_result={
            "data": {
                "query_expansion": {"rewritten_queries": []},
                "retrieval_plan": {"plan_id": "legacy-test"},
                "evidence_quality": {
                    "final_assessment": {
                        "sufficient": True,
                        "confidence": 1.0,
                    }
                },
                "retrieval_results": [result],
                # Legacy output intentionally omits selected_results.
                "context_pack": {
                    "context": result["text"],
                    "rendered_text": result["text"],
                    "dropped_results": [],
                    "max_context_chars": 6000,
                    "token_budget": 1000,
                    "tokens_used": 20,
                },
            },
            "metadata": {},
        },
        latency_ms=1,
        rag_project_root="D:/MyCode/rag-agent",
        service_name="legacy-stage-d-test",
    )

    gate = bundle.extra["gate_decision"]
    assert bundle.extra["prompt_evidence_source"] == "legacy_fallback"
    assert gate["delivery_mode"] == "qualified"
    assert gate["human_review_required"] is True
    assert "legacy_prompt_evidence_fallback" in gate["reason_codes"]
    assert "基于旧版 Prompt 证据兼容投影，需人工复核" in gate[
        "required_qualifiers"
    ]


def test_legacy_crag_result_cannot_expand_document_profile_capabilities() -> None:
    profile = document_profile(
        document_id="history-doc",
        kb_id="historical_case_kb",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        project_relation="other_project",
        project_id="history-project",
        authority_level="reference",
        allowed_claim_types=["general_method", "reference_case"],
        forbidden_claim_types=["current_project_cost"],
    )
    result = retrieval_result(
        profile=profile,
        text="历史项目预算金额：250万元",
        fact_status="confirmed",
    )
    governance = OnlineEvidenceGovernanceV2().evaluate(
        query="当前项目预算金额",
        results=[result],
        runtime_context=evidence_requirements(),
    )

    assert governance.gate_decision.delivery_mode.value == "qualified"
    assert {
        item.value for item in governance.gate_decision.allowed_claim_types
    } == {"general_method", "reference_case"}
    assert "current_project_cost" not in {
        item.value for item in governance.gate_decision.allowed_claim_types
    }
    # An external/legacy "sufficient" flag is deliberately absent from the
    # function inputs and therefore cannot expand the candidate Profile.
