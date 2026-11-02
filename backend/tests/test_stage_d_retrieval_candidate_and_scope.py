from __future__ import annotations

import json

import pytest

from rag.application.retrieval_scope import (
    parse_generated_filter_expr,
    resolve_canonical_retrieval_scope,
)
from rag.retriever.milvus_child_retriever import normalize_milvus_child_hit
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.retrieval_candidate_v2 import RetrievalCandidateV2Schema


def _profile(*, document_id: str = "doc-1", kb_id: str = "project_fact_kb") -> dict:
    return {
        "schema_version": "document_profile_v1",
        "document_id": document_id,
        "document_type": "current_project_requirement",
        "evidence_role": "project_fact",
        "kb_id": kb_id,
        "tenant_id": "default",
        "project_relation": "current_project",
        "project_id": "project-1",
        "authority": {
            "level": "official",
            "version": "v1",
            "issuer": "test",
            "effective_date": None,
            "expiration_date": None,
        },
        "content_capabilities": {
            "allowed_claim_types": [
                "current_project_fact",
                "current_project_cost",
            ],
            "forbidden_claim_types": [],
        },
        "parsing": {
            "parser_strategy": "project_fact_extraction",
            "chunk_strategy": "fact_record",
            "fallback_chunk_strategy": "fixed_token_fallback",
        },
        "profile_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "source_document_form": "feasibility_study",
        "taxonomy_mapping": None,
        "should_index": True,
        "is_gold_reference": False,
    }


def _child(profile: dict | None = None) -> dict:
    extra = {
        "type_aware_chunking": {
            "strategy": "fact_record",
            "actual_strategy": "fact_record",
            "representation_type": "fact_record",
            "fallback_used": False,
            "structure": {
                "record_id": "fact-1",
                "fact_category": "cost",
                "predicate": "预算金额",
                "value": "250000元",
                "fact_status": "estimated",
            },
        }
    }
    if profile is not None:
        extra["document_profile"] = profile
    return {
        "chunk_id": "child-1",
        "child_chunk_id": "child-1",
        "parent_chunk_id": "parent-1",
        "doc_id": "doc-1",
        "tenant_id": "default",
        "kb_id": "project_fact_kb",
        "file_id": "file-1",
        "text": "预算金额：250000元",
        "extra": extra,
    }


def test_filter_expr_is_parsed_into_one_canonical_scope() -> None:
    expression = (
        'tenant_id == "default" and '
        'kb_id in ["project_fact_kb", "policy_standard_kb"] and '
        'doc_id in ["doc-1"]'
    )
    scope = parse_generated_filter_expr(expression)

    assert scope.tenant_id == "default"
    assert scope.kb_ids == ("policy_standard_kb", "project_fact_kb")
    assert scope.doc_ids == ("doc-1",)
    assert scope.to_filter_expr() == (
        'tenant_id == "default" and '
        'kb_id in ["policy_standard_kb", "project_fact_kb"] and '
        'doc_id in ["doc-1"]'
    )


def test_filter_only_direct_call_derives_keyword_scope() -> None:
    scope = resolve_canonical_retrieval_scope(
        filter_expr=(
            'tenant_id == "default" and '
            'kb_id in ["project_fact_kb"] and '
            'file_id in ["file-1"]'
        ),
        keyword_scope=None,
    )

    assert scope.to_keyword_scope() == {
        "tenant_id": "default",
        "kb_ids": ["project_fact_kb"],
        "file_ids": ["file-1"],
        "doc_ids": [],
        "access_scope_enforced": False,
    }


def test_dense_and_keyword_scope_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="scopes disagree for kb_ids"):
        resolve_canonical_retrieval_scope(
            filter_expr=(
                'tenant_id == "default" and '
                'kb_id in ["project_fact_kb"]'
            ),
            keyword_scope={
                "tenant_id": "default",
                "kb_ids": ["template_style_kb"],
                "access_scope_enforced": True,
            },
        )


def test_untrusted_filter_grammar_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported retrieval filter"):
        parse_generated_filter_expr('kb_id != "project_fact_kb"')


def test_dense_hit_restores_profile_and_type_aware_metadata() -> None:
    profile = _profile()
    extra = {
        "document_profile": profile,
        "type_aware_chunking": {
            "strategy": "fact_record",
            "representation_type": "fact_record",
            "fallback_used": False,
        },
    }
    hit = {
        "distance": 0.91,
        "entity": {
            "chunk_id": "child-1",
            "child_chunk_id": "child-1",
            "parent_chunk_id": "parent-1",
            "doc_id": "doc-1",
            "tenant_id": "default",
            "kb_id": "project_fact_kb",
            "file_id": "file-1",
            "document_type": "current_project_requirement",
            "evidence_role": "project_fact",
            "project_relation": "current_project",
            "project_id": "project-1",
            "authority_level": "official",
            "text": "预算金额：250000元",
            "extra_json": json.dumps(extra, ensure_ascii=False),
        },
    }

    normalized = normalize_milvus_child_hit(hit, rank=1)

    assert normalized["profile_parse_error"] is None
    assert normalized["document_profile"] == profile
    assert normalized["child_chunk"]["extra"]["document_profile"] == profile
    assert normalized["type_aware_chunking"]["strategy"] == "fact_record"


def test_retrieval_result_contains_valid_candidate_v2() -> None:
    profile = _profile()
    result = build_retrieval_result_v2(
        child_chunk=_child(profile),
        parent_chunk={
            "parent_chunk_id": "parent-1",
            "doc_id": "doc-1",
            "tenant_id": "default",
            "kb_id": "project_fact_kb",
            "text": "项目投资与预算说明",
            "extra": {"document_profile": profile},
        },
        rank=1,
        score=0.91,
        metadata={"retrieval_sources": ["dense", "keyword"]},
    )

    candidate = RetrievalCandidateV2Schema.model_validate(
        result["metadata"]["retrieval_candidate_v2"]
    )
    assert candidate.document_id == "doc-1"
    assert candidate.profile_sha256 == "a" * 64
    assert candidate.allowed_claim_types[1].value == "current_project_cost"
    assert result["extra"]["document_profile"] == profile


def test_missing_profile_is_explicit_not_silent() -> None:
    with pytest.raises(ValueError, match="raw document_profile is required"):
        build_retrieval_result_v2(
            child_chunk=_child(None),
            parent_chunk={"parent_chunk_id": "parent-1", "text": "context"},
            rank=1,
            score=0.5,
        )
