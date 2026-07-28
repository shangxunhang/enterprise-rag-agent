# -*- coding: utf-8 -*-
"""直接对 ``retrieval_result_v2`` 字典计算生产检索指标。

该实现不依赖旧版 dataclass 或 Pydantic 评测对象，可被运行时 Trace、离线策略评测和
兼容导出共同复用。相关性采用多粒度 OR 语义：文档、父块或子块任一黄金身份命中，
即视为当前结果相关；关键词覆盖独立计算。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set


def _as_set(values: Optional[Sequence[str]]) -> Set[str]:
    """把可选身份序列转换为去空值字符串集合。"""

    if not values:
        return set()
    return {
        str(value)
        for value in values
        if value is not None and str(value) != ""
    }


def _top_k(
    results: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """按当前排名截取前 k 条结果，非正 k 返回空列表。"""

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
    """判断 top-k 是否命中任一粒度的黄金身份。

    Returns:
        文档、父块或子块任一集合存在交集时返回 ``1.0``，否则返回 ``0.0``。
        完全没有黄金身份时也返回 ``0.0``。
    """

    # 第一步：分别规范化三种黄金身份。
    expected_docs = _as_set(expected_doc_ids)
    expected_parents = _as_set(expected_parent_chunk_ids)
    expected_children = _as_set(expected_child_chunk_ids)
    if not (expected_docs or expected_parents or expected_children):
        return 0.0

    # 第二步：按排名扫描 top-k，任一粒度命中后立即返回。
    for item in _top_k(results, top_k):
        if expected_docs and str(item.get("doc_id")) in expected_docs:
            return 1.0
        if (
            expected_parents
            and str(item.get("parent_chunk_id")) in expected_parents
        ):
            return 1.0
        if (
            expected_children
            and str(
                item.get("child_chunk_id") or item.get("chunk_id")
            )
            in expected_children
        ):
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
    """计算第一个多粒度黄金命中结果排名的倒数。"""

    # 第一步：建立黄金集合；无黄金标签时指标不可定义，按协议返回零。
    expected_docs = _as_set(expected_doc_ids)
    expected_parents = _as_set(expected_parent_chunk_ids)
    expected_children = _as_set(expected_child_chunk_ids)
    if not (expected_docs or expected_parents or expected_children):
        return 0.0

    # 第二步：从排名 1 开始扫描，首次命中任一粒度即返回 reciprocal rank。
    for rank, item in enumerate(_top_k(results, top_k), start=1):
        if expected_docs and str(item.get("doc_id")) in expected_docs:
            return 1.0 / rank
        if (
            expected_parents
            and str(item.get("parent_chunk_id")) in expected_parents
        ):
            return 1.0 / rank
        if (
            expected_children
            and str(
                item.get("child_chunk_id") or item.get("chunk_id")
            )
            in expected_children
        ):
            return 1.0 / rank

    return 0.0


def compute_context_keyword_hit(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_keywords: Optional[Sequence[str]] = None,
    text_fields: Sequence[str] = (
        "text",
        "parent_text",
        "child_text",
    ),
) -> float:
    """计算黄金关键词在 top-k 上下文文本中的字面覆盖比例。

    每个结果只使用 ``text_fields`` 中首个非空字段，防止同一父子证据重复拼接。
    匹配大小写敏感，不表达同义词或语义相关性。
    """

    # 第一步：过滤空关键词；没有标签时返回零。
    keywords = [
        str(value)
        for value in (expected_keywords or [])
        if value
    ]
    if not keywords:
        return 0.0

    # 第二步：按排名截断并为每个结果选择唯一文本表示。
    context_parts: List[str] = []
    for item in _top_k(results, top_k):
        for field in text_fields:
            value = item.get(field)
            if value:
                context_parts.append(str(value))
                break

    # 第三步：拼接上下文并计算关键词命中比例。
    context = "\n".join(context_parts)
    hit_count = sum(
        1 for keyword in keywords if keyword in context
    )
    return hit_count / len(keywords)


def evaluate_retrieval_results_v2(
    results: List[Dict[str, Any]],
    *,
    top_k: int,
    expected_doc_ids: Optional[Sequence[str]] = None,
    expected_parent_chunk_ids: Optional[Sequence[str]] = None,
    expected_child_chunk_ids: Optional[Sequence[str]] = None,
    expected_keywords: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """汇总一条检索结果列表的三项生产指标和黄金标签快照。

    Returns:
        包含 top_k、原始结果数、hit@k、MRR、关键词覆盖及全部黄金标签的字典。
    """

    # 分别调用独立指标函数，确保运行时和离线调用共享完全相同的口径。
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
        "expected_parent_chunk_ids": list(
            expected_parent_chunk_ids or []
        ),
        "expected_child_chunk_ids": list(
            expected_child_chunk_ids or []
        ),
        "expected_keywords": list(expected_keywords or []),
    }
