from __future__ import annotations

import pytest

from apps.enterprise_document.services.scheme_writer.evidence_gate_policy import (
    EvidenceGatePolicy,
)
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.schema.prompt_evidence_candidate_v1 import (
    build_prompt_evidence_candidate_v1,
)
from schemas.rag import (
    EvidenceRequirementsSchema,
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
)

from test_support.stage_d_fixtures import (
    document_profile,
    evidence_requirements,
    prompt_selected,
    retrieval_result,
)


def _request() -> tuple[RAGToolInputSchema, object]:
    requirements = evidence_requirements()
    legacy_requirements = requirements["evidence_requirements"]
    request = RAGToolInputSchema(
        task_id="stage-d-prompt-task",
        run_id="stage-d-prompt-run",
        agent_name="ResearchAgent",
        query="项目预算金额",
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="default",
            authorized_kb_ids=["project_fact_kb"],
        ),
        max_context_chars=6000,
        max_context_items=5,
        retrieval_trace_id="stage-d-prompt-trace",
        retrieval_scope="section",
        evidence_requirements=EvidenceRequirementsSchema.from_topics(
            section_id=requirements["section_id"],
            section_title=requirements["section_title"],
            section_type=requirements["section_type"],
            topics=legacy_requirements["required_topics"],
        ),
    )
    return request, RAGRequestMapper().map(request)


def _map(
    *,
    context_pack: dict,
    retrieval_results: list[dict],
):
    request, invocation = _request()
    pre_context = OnlineEvidenceGovernanceV2().evaluate(
        query=request.query,
        results=retrieval_results,
        runtime_context=evidence_requirements(),
    )
    raw = {
        "query_expansion": {"rewritten_queries": []},
        "retrieval_plan": {"plan_id": "stage_d_prompt_test"},
        "evidence_quality": {
            "metadata": {"judge": "legacy_crag"},
            "final_assessment": {"sufficient": True, "confidence": 0.99},
        },
        "retrieval_results": retrieval_results,
        "evidence_governance_v2": pre_context.to_dict(),
        "context_pack": context_pack,
        "run_id": "stage-d-prompt-rag-run",
    }
    return RAGResultMapper().map(
        request=request,
        invocation=invocation,
        raw_result={"data": raw, "metadata": {}},
        latency_ms=1,
        rag_project_root="D:/MyCode/rag-agent",
        service_name="stage_d_prompt_test",
    )


def test_empty_selected_results_is_authoritative_and_never_falls_back() -> None:
    result = retrieval_result()
    bundle = _map(
        retrieval_results=[result],
        context_pack={
            "context": "",
            "rendered_text": "",
            "selected_results": [],
            "dropped_results": [result],
            "selected_count": 0,
            "dropped_count": 1,
            "max_context_chars": 6000,
            "used_chars": 0,
            "token_budget": 1000,
            "tokens_used": 0,
            "truncated_item_ids": [],
        },
    )

    governance = bundle.extra["evidence_governance_v2"]
    assert governance["prompt_evidence_candidates"] == []
    assert governance["set_assessment"]["status"] == "insufficient"
    assert governance["set_assessment"]["coverage_ratio"] == 0.0
    assert governance["set_assessment"]["metadata"]["legacy_coverage_shape"] == "absent"
    assert governance["set_assessment"]["coverage_ratio"] == 0.0
    decision = EvidenceGatePolicy().evaluate(bundle.assessment)
    assert decision.delivery_mode.value == "blocked"
    assert decision.allowed_claim_types == []
    assert "generation_allowed" not in bundle.extra
    assert bundle.extra["prompt_evidence_source"] == "context_pack_selected_results"
    assert bundle.selected_evidence_ids == []


def test_missing_selected_results_blocks_in_strong_gate_mode() -> None:
    result = retrieval_result()
    bundle = _map(
        retrieval_results=[result],
        context_pack={
            "context": "",
            "rendered_text": "",
            "dropped_results": [],
            "max_context_chars": 6000,
            "used_chars": 0,
            "token_budget": 1000,
            "tokens_used": 0,
        },
    )

    gate = EvidenceGatePolicy().evaluate(bundle.assessment)
    assert bundle.extra["prompt_evidence_source"] == "missing_blocked"
    assert bundle.extra["prompt_evidence_contract"]["contract_missing"] is True
    assert gate.delivery_mode.value == "blocked"
    assert "prompt_evidence_contract_missing" in gate.reason_codes
    assert "generation_allowed" not in bundle.extra


def test_truncated_topic_is_not_counted_from_full_parent() -> None:
    full_text = "前部为一般说明。" + "其他说明。" * 30 + "预算金额为250万元"
    result = retrieval_result(text=full_text, value="250万元", unit="万元")
    visible = "前部为一般说明。其他说明。\n...[TRUNCATED]"
    selected = prompt_selected(result, prompt_text=visible, truncated=True)
    bundle = _map(
        retrieval_results=[result],
        context_pack={
            "context": visible,
            "rendered_text": visible,
            "selected_results": [selected],
            "dropped_results": [],
            "selected_count": 1,
            "dropped_count": 0,
            "max_context_chars": 6000,
            "used_chars": len(visible),
            "token_budget": 1000,
            "tokens_used": 30,
            "truncated_item_ids": [result["parent_chunk_id"]],
        },
    )

    governance = bundle.extra["evidence_governance_v2"]
    prompt_candidate = governance["prompt_evidence_candidates"][0]
    assert prompt_candidate["context_truncated"] is True
    assert prompt_candidate["fact_visible_in_prompt"] is False
    assert "预算金额" not in governance["set_assessment"]["covered_topics"]
    assert EvidenceGatePolicy().evaluate(bundle.assessment).allowed_claim_types == []
    assert "generation_allowed" not in bundle.extra


def test_structured_value_not_visible_in_prompt_cannot_support_claim() -> None:
    result = retrieval_result(
        text="预算金额为250万元。项目采用统一认证。",
        value="250万元",
        unit="万元",
    )
    selected = prompt_selected(result, prompt_text="项目采用统一认证。")
    candidate = build_prompt_evidence_candidate_v1(selected)
    governance = OnlineEvidenceGovernanceV2().evaluate_prompt(
        query="预算金额",
        prompt_candidates=[candidate],
        runtime_context=evidence_requirements(),
    )

    assert candidate.fact_visible_in_prompt is False
    assert governance.set_assessment.status.value == "insufficient"
    assert governance.set_assessment.coverage_ratio == 0.0
    assert governance.set_assessment.metadata["legacy_coverage_shape"] == "absent"
    decision = EvidenceGatePolicy().evaluate(governance.public_assessment)
    assert decision.delivery_mode.value == "blocked"
    assert decision.allowed_claim_types == []


def test_selected_results_with_empty_rendered_context_is_hard_failure() -> None:
    result = retrieval_result()
    selected = prompt_selected(result, prompt_text="预算金额：250万元")
    with pytest.raises(ValueError, match="rendered context is empty"):
        _map(
            retrieval_results=[result],
            context_pack={
                "context": "",
                "rendered_text": "",
                "selected_results": [selected],
                "dropped_results": [],
                "selected_count": 1,
                "dropped_count": 0,
            },
        )
