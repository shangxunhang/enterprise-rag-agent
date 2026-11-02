from __future__ import annotations

from apps.enterprise_document.schemas.gate_decision import DeliveryMode
from apps.enterprise_document.services.scheme_writer.evidence_gate_policy import (
    EvidenceGatePolicy,
)
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from schemas.rag import (
    EvidenceAssessmentStatus,
    EvidenceDisposition,
    EvidenceRequirementsSchema,
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
)


def _profile(
    *,
    document_id: str,
    document_type: str,
    evidence_role: str,
    kb_id: str,
    relation: str,
    authority: str,
    allowed: list[str],
    forbidden: list[str],
    char: str,
) -> dict:
    return {
        "schema_version": "document_profile_v1",
        "document_id": document_id,
        "document_type": document_type,
        "evidence_role": evidence_role,
        "kb_id": kb_id,
        "tenant_id": "default",
        "project_relation": relation,
        "project_id": "project-1" if relation != "general" else None,
        "authority": {
            "level": authority,
            "version": "v1",
            "issuer": "test",
            "effective_date": None,
            "expiration_date": None,
        },
        "content_capabilities": {
            "allowed_claim_types": allowed,
            "forbidden_claim_types": forbidden,
        },
        "parsing": {
            "parser_strategy": "project_fact_extraction",
            "chunk_strategy": "fact_record",
            "fallback_chunk_strategy": "fixed_token_fallback",
        },
        "profile_sha256": char * 64,
        "source_sha256": char.upper() * 64,
        "source_document_form": "test",
        "taxonomy_mapping": None,
        "should_index": True,
        "is_gold_reference": False,
    }


def _result(
    *,
    child_id: str,
    parent_id: str,
    document_id: str,
    kb_id: str,
    text: str,
    profile: dict,
    rank: int,
) -> dict:
    chunking = {
        "strategy": "fact_record",
        "original_strategy": "fact_record",
        "actual_strategy": "fact_record",
        "representation_type": "fact_record",
        "fallback_used": False,
    }
    structured_extra = {
        "record_id": f"record-{child_id}",
        "fact_category": "cost",
        "subject": document_id,
        "predicate": "预算金额",
        "value": "250000元" if document_id == "current-1" else "300000元",
        "unit": "元",
        "fact_status": "confirmed",
        "source_span": {"text": text},
    }
    extra = {
        "document_profile": profile,
        "type_aware_chunking": chunking,
        **structured_extra,
    }
    return {
        "rank": rank,
        "score": 0.95 - rank * 0.01,
        "rerank_score": 0.93 - rank * 0.01,
        "chunk_id": child_id,
        "child_chunk_id": child_id,
        "parent_chunk_id": parent_id,
        "doc_id": document_id,
        "tenant_id": "default",
        "kb_id": kb_id,
        "file_id": f"{document_id}.docx",
        "document_type": profile["document_type"],
        "evidence_role": profile["evidence_role"],
        "project_relation": profile["project_relation"],
        "project_id": profile["project_id"],
        "authority_level": profile["authority"]["level"],
        "child_text": text,
        "parent_text": text,
        "text": text,
        "title": "投资估算",
        "section": "投资估算",
        "page_start": 1,
        "page_end": 1,
        "metadata": {
            "retrieval_sources": ["dense", "keyword"],
            "matched_child_chunk_ids": [child_id],
            "matched_child_chunks": [
                {
                    "chunk_id": child_id,
                    "child_chunk_id": child_id,
                    "parent_chunk_id": parent_id,
                    "doc_id": document_id,
                    "text": text,
                    "title": "投资估算",
                    "section": "投资估算",
                    "page_start": 1,
                    "page_end": 1,
                    "document_profile": profile,
                    "type_aware_chunking": chunking,
                    "extra": extra,
                }
            ],
            "document_profile": profile,
            "type_aware_chunking": chunking,
        },
        "extra": extra,
        "child_chunk": {
            "chunk_id": child_id,
            "child_chunk_id": child_id,
            "parent_chunk_id": parent_id,
            "doc_id": document_id,
            "tenant_id": "default",
            "kb_id": kb_id,
            "file_id": f"{document_id}.docx",
            "text": text,
            "extra": extra,
        },
    }


def test_result_mapper_publishes_v2_gate_and_context_selection() -> None:
    current_profile = _profile(
        document_id="current-1",
        document_type="tender_requirement",
        evidence_role="procurement_requirement",
        kb_id="project_fact_kb",
        relation="current_project",
        authority="official",
        allowed=["current_project_cost", "procurement_requirement"],
        forbidden=[],
        char="a",
    )
    historical_profile = _profile(
        document_id="history-1",
        document_type="historical_feasibility_study",
        evidence_role="historical_case",
        kb_id="historical_case_kb",
        relation="other_project",
        authority="reference",
        allowed=["general_method", "reference_case"],
        forbidden=["current_project_cost"],
        char="b",
    )
    current = _result(
        child_id="current-child",
        parent_id="current-parent",
        document_id="current-1",
        kb_id="project_fact_kb",
        text="预算金额：250000元",
        profile=current_profile,
        rank=1,
    )
    historical = _result(
        child_id="history-child",
        parent_id="history-parent",
        document_id="history-1",
        kb_id="historical_case_kb",
        text="历史同类项目预算金额：300000元",
        profile=historical_profile,
        rank=2,
    )
    requirements = {
        "policy_id": "current_cost_v1",
        "section_id": "investment",
        "section_type": "investment",
        "required_topics": ["预算金额"],
        "requested_claim_types": ["current_project_cost"],
        "claim_risk_level": "high",
    }
    request = RAGToolInputSchema(
        task_id="task-stage-d",
        run_id="run-stage-d",
        agent_name="ResearchAgent",
        query="项目预算金额",
        access_scope=RetrievalAccessScopeSchema(
            tenant_id="default",
            authorized_kb_ids=["project_fact_kb", "historical_case_kb"],
        ),
        max_context_chars=6000,
        max_context_items=3,
        retrieval_trace_id="trace-stage-d",
        retrieval_scope="section",
        evidence_requirements=EvidenceRequirementsSchema.from_topics(
            section_id="investment",
            section_title="投资估算",
            section_type="investment",
            topics=["预算金额"],
        ),
    )
    invocation = RAGRequestMapper().map(request)
    pre_context = OnlineEvidenceGovernanceV2().evaluate(
        query=request.query,
        results=[current, historical],
        runtime_context={
            "section_id": "investment",
            "section_title": "投资估算",
            "section_type": "investment",
            "evidence_requirements": requirements,
        },
    )
    assert pre_context.eligible_candidate_ids == (
        "current-child",
        "history-child",
    )
    historical_assessment = next(
        item
        for item in pre_context.item_assessments
        if item.evidence_id == "history-child"
    )
    assert {
        item.value for item in historical_assessment.supported_claim_types
    } == {"general_method", "reference_case"}
    assert "current_project_cost" not in {
        item.value for item in historical_assessment.supported_claim_types
    }

    selected_current = {
        **current,
        "prompt_visible_text": "预算金额：250000元",
        "context_text": "预算金额：250000元",
        "context_rank": 1,
        "metadata": {
            **dict(current["metadata"]),
            "context_truncated": False,
            "prompt_citation_ids": ["C1"],
        },
    }
    raw = {
        "query_expansion": {"rewritten_queries": []},
        "retrieval_plan": {"plan_id": "stage_d_test"},
        "evidence_quality": {
            # Deliberately make the old correction grader pessimistic. Stage D
            # claim eligibility and delivery mode must remain the final truth.
            "metadata": {"judge": "legacy_crag"},
            "final_assessment": {
                "sufficient": False,
                "confidence": 0.1,
            },
        },
        "retrieval_results": [current, historical],
        "evidence_governance_v2": pre_context.to_dict(),
        "context_pack": {
            "context": "预算金额：250000元",
            "rendered_text": "预算金额：250000元",
            "selected_results": [selected_current],
            "dropped_results": [],
            "max_context_chars": 6000,
            "used_chars": len("预算金额：250000元"),
            "selected_count": 1,
            "dropped_count": 1,
            "packing_strategy": "default",
            "token_budget": 1000,
            "tokens_used": 20,
            "truncated_item_ids": [],
        },
        "run_id": "rag-run-stage-d",
    }
    bundle = RAGResultMapper().map(
        request=request,
        invocation=invocation,
        raw_result={
            "data": raw,
            "metadata": {
                "offline_index": {
                    "index_version": "idx-stage-c-v2",
                    "dataset_version": "cleaned_text_units_document_profiled_v2",
                    "embedding_model": "m3e-base",
                    "embedding_version": "local_m3e_base",
                    "embedding_dim": 768,
                    "collection_name": "rag_child_chunks",
                    "backend": "milvus_lite",
                }
            },
        },
        latency_ms=12,
        rag_project_root="D:/MyCode/rag-agent",
        service_name="stage_d_test_service",
    )

    assert bundle.assessment.status is EvidenceAssessmentStatus.SUFFICIENT
    assert bundle.assessment.judge_name == "document_profile_gate_v2"
    gate = EvidenceGatePolicy().evaluate(bundle.assessment)
    assert gate.delivery_mode is DeliveryMode.GROUNDED
    assert [item.value for item in gate.allowed_claim_types] == [
        "current_project_cost",
        "procurement_requirement",
    ]
    assert "gate_decision" not in bundle.extra
    assert "generation_allowed" not in bundle.extra
    assert bundle.selected_evidence_ids == ["E1"]
    assert bundle.items[0].doc_id == "current-1"
    assert bundle.items[0].disposition is EvidenceDisposition.SELECTED
    assert bundle.items[1].doc_id == "history-1"
    assert bundle.items[1].disposition is EvidenceDisposition.DROPPED
    assert bundle.items[1].drop_reason == "not_selected_by_context_packer"
    assert bundle.items[1].citation_ids == []
    assert "历史同类项目" not in bundle.context.context_text
    details = bundle.assessment.details
    assert "gate_decision" not in details
    assert "generation_allowed" not in details
    assert "human_review_required" not in details
    assert details["legacy_corrective_assessment"]["status"] == "insufficient"
    assert details["presence_is_not_semantic_sufficiency"] is True
