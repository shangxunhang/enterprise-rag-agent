"""把完整检索记录投影为适合日志和接口展示的紧凑上下文。"""

from __future__ import annotations

from typing import Any, Dict, List


def compact_contexts(
    retrieval_results: List[Dict[str, Any]],
    max_text_chars: int = 500,
) -> List[Dict[str, Any]]:
    """生成不丢失身份与分数信息的检索结果预览。

    Args:
        retrieval_results: 完整父子块检索记录列表。
        max_text_chars: 每个匹配文本和上下文文本最多保留的字符数。

    Returns:
        与输入顺序一致的紧凑字典列表。输出保留排名、文档/块身份、分数、标题、
        章节和 metadata，但只保存截断文本预览。

    本函数只服务展示，不应把紧凑结果重新送回证据判断或生成链，因为预览可能已
    截断关键事实。
    """

    contexts: List[Dict[str, Any]] = []

    # 逐项投影，保持 Retriever 或 Reranker 已确定的排名顺序。
    for item in retrieval_results:
        # child_text 表示实际命中的细粒度片段，缺失时回退通用 text。
        child_text = str(
            item.get("child_text") or item.get("text") or ""
        )

        # parent_text 表示提供给生成链的上下文；旧记录可能只有 text。
        parent_text = str(
            item.get("parent_text")
            or item.get("text")
            or child_text
        )

        # 身份和分数保持完整，只有文本字段按统一上限截断。
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
