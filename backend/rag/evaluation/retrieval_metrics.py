# -*- coding: utf-8 -*-
"""
rag_template/eval/p3_retrieval_eval.py
======================================

Dict-based retrieval evaluation for retrieval_result_v2.
This avoids depending on existing dataclass/Pydantic eval schemas and works directly
with P1/P2/P3 result dictionaries.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


def _as_set(values: Optional[Sequence[str]]) -> Set[str]:
    if not values:
        return set()
    return {str(x) for x in values if x is not None and str(x) != ""}


def _top_k(results: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    if top_k <= 0:
        return []
    return results[: int(top_k)]


def compute_hit_at_k(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_doc_ids: Optional[Sequence[str]] = None,
    expected_parent_chunk_ids: Optional[Sequence[str]] = None,
    expected_child_chunk_ids: Optional[Sequence[str]] = None,
) -> float:
    expected_docs = _as_set(expected_doc_ids)
    expected_parents = _as_set(expected_parent_chunk_ids)
    expected_children = _as_set(expected_child_chunk_ids)
    if not (expected_docs or expected_parents or expected_children):
        return 0.0

    for item in _top_k(results, top_k):
        if expected_docs and str(item.get("doc_id")) in expected_docs:
            return 1.0
        if expected_parents and str(item.get("parent_chunk_id")) in expected_parents:
            return 1.0
        if expected_children and str(item.get("child_chunk_id") or item.get("chunk_id")) in expected_children:
            return 1.0
    return 0.0


def compute_mrr(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_doc_ids: Optional[Sequence[str]] = None,
    expected_parent_chunk_ids: Optional[Sequence[str]] = None,
    expected_child_chunk_ids: Optional[Sequence[str]] = None,
) -> float:
    expected_docs = _as_set(expected_doc_ids)
    expected_parents = _as_set(expected_parent_chunk_ids)
    expected_children = _as_set(expected_child_chunk_ids)
    if not (expected_docs or expected_parents or expected_children):
        return 0.0

    for idx, item in enumerate(_top_k(results, top_k), start=1):
        if expected_docs and str(item.get("doc_id")) in expected_docs:
            return 1.0 / idx
        if expected_parents and str(item.get("parent_chunk_id")) in expected_parents:
            return 1.0 / idx
        if expected_children and str(item.get("child_chunk_id") or item.get("chunk_id")) in expected_children:
            return 1.0 / idx
    return 0.0


def compute_context_keyword_hit(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_keywords: Optional[Sequence[str]] = None,
    text_fields: Sequence[str] = ("text", "parent_text", "child_text"),
) -> float:
    keywords = [str(x) for x in (expected_keywords or []) if x]
    if not keywords:
        return 0.0
    context_parts: List[str] = []
    for item in _top_k(results, top_k):
        for field in text_fields:
            value = item.get(field)
            if value:
                context_parts.append(str(value))
                break
    context = "\n".join(context_parts)
    hit = sum(1 for kw in keywords if kw in context)
    return hit / len(keywords)


def evaluate_retrieval_results_v2(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_doc_ids: Optional[Sequence[str]] = None,
    expected_parent_chunk_ids: Optional[Sequence[str]] = None,
    expected_child_chunk_ids: Optional[Sequence[str]] = None,
    expected_keywords: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Evaluate one retrieval result list."""
    return {
        "top_k": int(top_k),
        "result_count": len(results),
        "hit_at_k": compute_hit_at_k(
            results,
            top_k=top_k,
            expected_doc_ids=expected_doc_ids,
            expected_parent_chunk_ids=expected_parent_chunk_ids,
            expected_child_chunk_ids=expected_child_chunk_ids,
        ),
        "mrr": compute_mrr(
            results,
            top_k=top_k,
            expected_doc_ids=expected_doc_ids,
            expected_parent_chunk_ids=expected_parent_chunk_ids,
            expected_child_chunk_ids=expected_child_chunk_ids,
        ),
        "context_keyword_hit": compute_context_keyword_hit(
            results,
            top_k=top_k,
            expected_keywords=expected_keywords,
        ),
        "expected_doc_ids": list(expected_doc_ids or []),
        "expected_parent_chunk_ids": list(expected_parent_chunk_ids or []),
        "expected_child_chunk_ids": list(expected_child_chunk_ids or []),
        "expected_keywords": list(expected_keywords or []),
    }
