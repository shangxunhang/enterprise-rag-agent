# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_as_set、_top_k、compute_hit_at_k、compute_mrr、compute_context_keyword_hit、evaluate_retrieval_results_v2。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/eval/p3_retrieval_eval.py
======================================

Dict-based retrieval evaluation for retrieval_result_v2.
This avoids depending on existing dataclass/Pydantic eval schemas and works directly
with P1/P2/P3 result dictionaries.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


# 阅读注释（函数）：处理 as set 相关逻辑。
def _as_set(values: Optional[Sequence[str]]) -> Set[str]:
    """处理 as set 相关逻辑。

    参数:
        values: values，具体约束请结合类型标注和调用方确认。

    返回:
        Set[str]

    阅读提示:
        主要直接调用：set, str。
    """
    if not values:
        return set()
    return {str(x) for x in values if x is not None and str(x) != ""}


# 阅读注释（函数）：处理 top k 相关逻辑。
def _top_k(results: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    """处理 top k 相关逻辑。

    参数:
        results: 待处理的结果集合。
        top_k: top k，具体约束请结合类型标注和调用方确认。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：int。
    """
    if top_k <= 0:
        return []
    return results[: int(top_k)]


# 阅读注释（函数）：计算 hit at k。
def compute_hit_at_k(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_doc_ids: Optional[Sequence[str]] = None,
    expected_parent_chunk_ids: Optional[Sequence[str]] = None,
    expected_child_chunk_ids: Optional[Sequence[str]] = None,
) -> float:
    """计算 hit at k。

    参数:
        results: 待处理的结果集合。
        top_k: top k，具体约束请结合类型标注和调用方确认。
        expected_doc_ids: expected doc 标识集合，具体约束请结合类型标注和调用方确认。
        expected_parent_chunk_ids: expected 父块 文本块 标识集合，具体约束请结合类型标注和调用方确认。
        expected_child_chunk_ids: expected 子块 文本块 标识集合，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：_as_set, _top_k, str, item.get。
    """
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


# 阅读注释（函数）：计算 mrr。
def compute_mrr(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_doc_ids: Optional[Sequence[str]] = None,
    expected_parent_chunk_ids: Optional[Sequence[str]] = None,
    expected_child_chunk_ids: Optional[Sequence[str]] = None,
) -> float:
    """计算 mrr。

    参数:
        results: 待处理的结果集合。
        top_k: top k，具体约束请结合类型标注和调用方确认。
        expected_doc_ids: expected doc 标识集合，具体约束请结合类型标注和调用方确认。
        expected_parent_chunk_ids: expected 父块 文本块 标识集合，具体约束请结合类型标注和调用方确认。
        expected_child_chunk_ids: expected 子块 文本块 标识集合，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：_as_set, enumerate, _top_k, str, item.get。
    """
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


# 阅读注释（函数）：计算 上下文 keyword hit。
def compute_context_keyword_hit(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_keywords: Optional[Sequence[str]] = None,
    text_fields: Sequence[str] = ("text", "parent_text", "child_text"),
) -> float:
    """计算 上下文 keyword hit。

    参数:
        results: 待处理的结果集合。
        top_k: top k，具体约束请结合类型标注和调用方确认。
        expected_keywords: expected keywords，具体约束请结合类型标注和调用方确认。
        text_fields: 文本 fields，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：str, _top_k, item.get, context_parts.append, join, sum, len。
    """
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


# 阅读注释（函数）：评估 检索 结果集合 v2。
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
