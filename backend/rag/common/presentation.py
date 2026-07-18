"""Presentation-only compact views of full retrieval records."""

from __future__ import annotations

from typing import Any, Dict, List


def compact_contexts(
    retrieval_results: List[Dict[str, Any]],
    max_text_chars: int = 500,
) -> List[Dict[str, Any]]:
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
