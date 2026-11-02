from __future__ import annotations

import json

import pytest

from rag.retriever.bm25_child_retriever import normalize_child_chunk_record
from rag.retriever.milvus_child_retriever import normalize_milvus_child_hit
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.prompt_evidence_candidate_v1 import (
    build_prompt_evidence_candidate_v1,
)
from rag.schema.retrieval_candidate_v2 import build_retrieval_candidate_v2
from rag.schema.structured_evidence import build_structured_evidence

from test_support.stage_d_fixtures import (
    document_profile,
    prompt_selected,
    retrieval_result,
)


def _dense_hit(result: dict) -> dict:
    child = result["child_chunk"]
    entity = {
        key: child.get(key)
        for key in (
            "chunk_id",
            "child_chunk_id",
            "parent_chunk_id",
            "tenant_id",
            "kb_id",
            "file_id",
            "doc_id",
            "document_type",
            "evidence_role",
            "project_relation",
            "project_id",
            "authority_level",
            "text",
        )
    }
    entity["extra_json"] = json.dumps(child["extra"], ensure_ascii=False)
    return {"distance": 0.9, "entity": entity}


def test_dense_and_bm25_use_identical_structured_evidence_projection() -> None:
    result = retrieval_result(
        text="投资估算：250万元",
        fact_status="estimated",
        value="250万元",
        unit="万元",
    )
    dense = normalize_milvus_child_hit(_dense_hit(result), rank=1)
    keyword_child = normalize_child_chunk_record(result["child_chunk"])

    assert dense["structured_evidence"] == keyword_child["structured_evidence"]
    assert dense["structured_evidence"]["fact"] == {
        "record_id": "record-child-1",
        "fact_category": "cost",
        "subject": "doc-current-1",
        "predicate": "预算金额",
        "value": "250万元",
        "unit": "万元",
        "fact_status": "estimated",
        "source_span": {"text": "投资估算：250万元"},
    }


def test_real_fact_shape_propagates_to_result_candidate_and_prompt() -> None:
    result = retrieval_result(
        text="投资估算：250万元",
        fact_status="estimated",
        value="250万元",
        unit="万元",
    )
    child = normalize_child_chunk_record(result["child_chunk"])
    retrieval_v2 = build_retrieval_result_v2(
        child_chunk=child,
        parent_chunk={
            "parent_chunk_id": result["parent_chunk_id"],
            "doc_id": result["doc_id"],
            "tenant_id": result["tenant_id"],
            "kb_id": result["kb_id"],
            "text": "投资估算章节。投资估算：250万元",
            "extra": {"document_profile": result["extra"]["document_profile"]},
        },
        rank=1,
        score=0.9,
    )
    candidate = build_retrieval_candidate_v2(retrieval_v2)
    selected = prompt_selected(
        retrieval_v2,
        prompt_text="预算金额：250万元",
    )
    prompt = build_prompt_evidence_candidate_v1(selected)

    assert retrieval_v2["structured_evidence"]["fact"]["fact_status"] == "estimated"
    assert candidate.structured_evidence.fact is not None
    assert candidate.structured_evidence.fact.predicate == "预算金额"
    assert candidate.structured_evidence.fact.value == "250万元"
    assert prompt.structured_evidence == candidate.structured_evidence
    assert prompt.fact_visible_in_prompt is True


@pytest.mark.parametrize(
    ("representation_type", "expected_field"),
    [
        ("fact_record", "fact"),
        ("table_row", "table"),
        ("policy_clause", "policy"),
        ("template_section", "template"),
        ("text_evidence", "fallback"),
        ("paragraph_text", "fallback"),
    ],
)
def test_all_stage_c_representation_types_have_stable_structured_contract(
    representation_type: str,
    expected_field: str,
) -> None:
    profile = document_profile()
    if representation_type == "policy_clause":
        profile = document_profile(
            kb_id="policy_standard_kb",
            document_type="policy_standard",
            evidence_role="policy_standard",
            project_relation="general",
            project_id=None,
            authority_level="official",
            allowed_claim_types=["policy_requirement"],
        )
    elif representation_type == "template_section":
        profile = document_profile(
            kb_id="template_style_kb",
            document_type="document_template",
            evidence_role="template_style",
            project_relation="general",
            project_id=None,
            authority_level="draft",
            allowed_claim_types=["template_structure", "style_guidance"],
        )
    result = retrieval_result(
        profile=profile,
        representation_type=representation_type,
    )
    structured = build_structured_evidence(result)
    payload = structured.model_dump(mode="json", exclude_none=True)

    assert payload["schema_version"] == "structured_evidence_v1"
    assert payload["representation_type"] == representation_type
    assert expected_field in payload
    if representation_type == "text_evidence":
        assert payload["fallback"]["used"] is True
        assert payload["fallback"]["reason"] == "unsupported_fact_structure"
    if representation_type == "paragraph_text":
        assert payload["fallback"]["used"] is False
