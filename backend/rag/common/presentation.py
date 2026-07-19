# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：compact_contexts。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Presentation-only compact views of full retrieval records."""

from __future__ import annotations

from typing import Any, Dict, List


# 阅读注释（函数）：处理 compact contexts 相关逻辑。
def compact_contexts(
    retrieval_results: List[Dict[str, Any]],
    max_text_chars: int = 500,
) -> List[Dict[str, Any]]:
    """处理 compact contexts 相关逻辑。

    参数:
        retrieval_results: 检索 结果集合，具体约束请结合类型标注和调用方确认。
        max_text_chars: max 文本 chars，具体约束请结合类型标注和调用方确认。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：str, item.get, contexts.append。
    """
    contexts: List[Dict[str, Any]] = []
    for item in retrieval_results:
        child_text = str(item.get("child_text") or item.get("text") or "")
        parent_text = str(item.get("parent_text") or item.get("text") or child_text)
        contexts.append(
            {
                "rank": item.get("rank"),
                "doc_id": item.get("doc_id"),
                "parent_chunk_id": item.get("parent_chunk_id"),
                "child_chunk_id": item.get("child_chunk_id"),
                "title": item.get("title"),
                "section": item.get("section"),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score"),
                "match_text_preview": child_text[:max_text_chars],
                "context_text_preview": parent_text[:max_text_chars],
                "text_preview": parent_text[:max_text_chars],
                "metadata": item.get("metadata") or {},
            }
        )
    return contexts
