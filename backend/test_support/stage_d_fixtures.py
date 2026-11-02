"""Reusable realistic Stage D test records based on Stage C child.extra shape."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def document_profile(
    *,
    document_id: str = "doc-current-1",
    tenant_id: str = "default",
    kb_id: str = "project_fact_kb",
    document_type: str = "tender_requirement",
    evidence_role: str = "procurement_requirement",
    project_relation: str = "current_project",
    project_id: str | None = "project-1",
    authority_level: str = "official",
    allowed_claim_types: list[str] | None = None,
    forbidden_claim_types: list[str] | None = None,
    source_document_form: str = "procurement_document",
    profile_char: str = "a",
) -> dict[str, Any]:
    return {
        "schema_version": "document_profile_v1",
        "document_id": document_id,
        "document_type": document_type,
        "evidence_role": evidence_role,
        "kb_id": kb_id,
        "tenant_id": tenant_id,
        "project_relation": project_relation,
        "project_id": project_id,
        "authority": {
            "level": authority_level,
            "version": "v1",
            "issuer": "stage-d-test",
            "effective_date": None,
            "expiration_date": None,
        },
        "content_capabilities": {
            "allowed_claim_types": list(
                allowed_claim_types
                or ["current_project_cost", "current_project_fact"]
            ),
            "forbidden_claim_types": list(forbidden_claim_types or []),
        },
        "parsing": {
            "parser_strategy": "project_fact_extraction",
            "chunk_strategy": "fact_record",
            "fallback_chunk_strategy": "fixed_token_fallback",
        },
        "profile_sha256": profile_char * 64,
        "source_sha256": profile_char.upper() * 64,
        "source_document_form": source_document_form,
        "taxonomy_mapping": None,
        "should_index": True,
        "is_gold_reference": False,
    }


def retrieval_result(
    *,
    child_id: str = "child-1",
    parent_id: str | None = None,
    profile: dict[str, Any] | None = None,
    text: str = "预算金额：250万元",
    representation_type: str = "fact_record",
    fact_category: str | None = "cost",
    predicate: str | None = "预算金额",
    value: Any = "250万元",
    unit: str | None = "万元",
    fact_status: str = "confirmed",
    rank: int = 1,
) -> dict[str, Any]:
    profile = deepcopy(profile or document_profile())
    parent_id = parent_id or f"parent-{child_id}"
    if representation_type == "template_section":
        original_strategy = actual_strategy = "template_section"
        structured = {
            "section_path": ["建设方案", "投资估算"],
            "section_title": "投资估算",
            "required_fields": ["预算金额"],
            "placeholder_fields": ["XXXX万元"],
            "style_hints": ["使用正式书面语"],
            "is_example_content": False,
        }
    elif representation_type == "policy_clause":
        original_strategy = actual_strategy = "policy_article_clause"
        structured = {
            "policy_name": "信息化项目管理办法",
            "chapter_id": "chapter-1",
            "chapter_title": "投资管理",
            "article_id": "article-2",
            "article_title": "概算要求",
            "clause_id": "clause-3",
            "clause_path": ["chapter-1", "article-2", "clause-3"],
            "is_table_of_contents": False,
        }
    elif representation_type == "table_row":
        original_strategy = actual_strategy = "table_row_record"
        structured = {
            "table_id": "table-1",
            "table_title": "投资估算表",
            "header": ["项目", "金额"],
            "row_index": 2,
            "cells": ["软件开发", "250万元"],
            "normalized_fields": {"项目": "软件开发", "金额": "250万元"},
            "inherited_cells": {},
            "merged_cell_relations": [],
        }
    elif representation_type in {"text_evidence", "paragraph_text"}:
        original_strategy = (
            "fact_record"
            if representation_type == "text_evidence"
            else "heading_parent_paragraph_child"
        )
        actual_strategy = representation_type
        structured = {}
        if representation_type == "text_evidence":
            structured.update(
                {
                    "record_id": f"record-{child_id}",
                    "fact_category": "unstructured_text",
                    "subject": profile["document_id"],
                    "predicate": None,
                    "value": None,
                    "unit": None,
                    "fact_status": "unknown",
                    "source_span": {"text": text},
                }
            )
    else:
        original_strategy = actual_strategy = "fact_record"
        structured = {
            "record_id": f"record-{child_id}",
            "fact_category": fact_category,
            "subject": profile["document_id"],
            "predicate": predicate,
            "value": value,
            "unit": unit,
            "fact_status": fact_status,
            "source_span": {"text": text},
        }

    type_aware = {
        "strategy": original_strategy,
        "original_strategy": original_strategy,
        "actual_strategy": actual_strategy,
        "representation_type": representation_type,
        "fallback_used": representation_type == "text_evidence",
        "fallback_reason": (
            "unsupported_fact_structure"
            if representation_type == "text_evidence"
            else None
        ),
        "fallback_strategy": (
            "text_evidence" if representation_type == "text_evidence" else None
        ),
        "structure_path": [],
        "parser_version": "stage-c-test-parser",
        "chunker_version": "child_chunk_profiled_v2.0",
    }
    extra = {
        "document_profile": profile,
        "type_aware_chunking": type_aware,
        **structured,
    }
    child = {
        "chunk_id": child_id,
        "child_chunk_id": child_id,
        "parent_chunk_id": parent_id,
        "doc_id": profile["document_id"],
        "tenant_id": profile["tenant_id"],
        "kb_id": profile["kb_id"],
        "file_id": f"{profile['document_id']}.docx",
        "document_type": profile["document_type"],
        "evidence_role": profile["evidence_role"],
        "project_relation": profile["project_relation"],
        "project_id": profile["project_id"],
        "authority_level": profile["authority"]["level"],
        "text": text,
        "extra": extra,
    }
    return {
        "rank": rank,
        "score": max(0.1, 0.95 - rank * 0.01),
        "rerank_score": max(0.1, 0.93 - rank * 0.01),
        "chunk_id": child_id,
        "child_chunk_id": child_id,
        "parent_chunk_id": parent_id,
        "doc_id": profile["document_id"],
        "tenant_id": profile["tenant_id"],
        "kb_id": profile["kb_id"],
        "file_id": f"{profile['document_id']}.docx",
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
        "retrieval_sources": ["dense", "keyword"],
        "metadata": {
            "retrieval_sources": ["dense", "keyword"],
            "matched_child_chunk_ids": [child_id],
            "document_profile": profile,
            "type_aware_chunking": type_aware,
        },
        "extra": extra,
        "child_chunk": child,
    }


def prompt_selected(
    result: dict[str, Any],
    *,
    prompt_text: str,
    context_rank: int = 1,
    truncated: bool = False,
    citation_ids: list[str] | None = None,
) -> dict[str, Any]:
    selected = deepcopy(result)
    metadata = dict(selected.get("metadata") or {})
    metadata.update(
        {
            "context_truncated": truncated,
            "prompt_citation_ids": list(citation_ids or [f"C{context_rank}"]),
            "prompt_visible_char_count": len(prompt_text),
        }
    )
    if truncated:
        metadata["truncated_source_id"] = selected["parent_chunk_id"]
        metadata["truncation_reason"] = "token_or_character_budget"
    selected["metadata"] = metadata
    selected["prompt_visible_text"] = prompt_text
    selected["context_text"] = prompt_text
    selected["context_rank"] = context_rank
    selected["rank"] = context_rank
    return selected


def evidence_requirements(
    *,
    topics: list[str] | None = None,
    claims: list[str] | None = None,
    risk: str = "high",
    allow_framework_only: bool = False,
) -> dict[str, Any]:
    topics = topics or ["预算金额"]
    claims = claims or ["current_project_cost"]
    return {
        "section_id": "investment",
        "section_title": "投资估算",
        "section_type": "investment",
        "evidence_requirements": {
            "policy_id": "stage_d_test_policy_v1",
            "section_id": "investment",
            "section_type": "investment",
            "required_topics": topics,
            "requested_claim_types": claims,
            "claim_risk_level": risk,
            "allow_framework_only": allow_framework_only,
        },
    }
