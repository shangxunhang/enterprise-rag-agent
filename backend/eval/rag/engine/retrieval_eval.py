"""
retrieval_eval.py
=================

RAG 检索结果评估模块。

本文件负责评估 retriever 返回的 chunks 是否命中目标文档或目标内容，
主要用于衡量 RAG 系统的检索质量。

第一版重点实现轻量级检索指标：
1. hit@k：top-k 检索结果中是否命中目标文档
2. recall@k：目标文档中有多少被 top-k 检索结果召回
3. MRR：第一个正确结果的倒数排名
4. context_keyword_hit：检索到的上下文中是否包含期望关键词

本文件只评估“检索是否正确”，不评估 LLM 最终生成答案的质量。
生成答案的评估逻辑应放在 generation_eval.py 中。
"""

"""
retrieval_eval.py
=================

RAG 检索结果评估模块。

本文件负责评估 retriever 返回的 chunks 是否命中目标文档或目标内容，
主要用于衡量 RAG 系统的检索质量。

第一版重点实现轻量级检索指标：
1. hit@k：top-k 检索结果中是否命中目标文档
2. recall@k：目标文档中有多少被 top-k 检索结果召回
3. MRR：第一个正确结果的倒数排名
4. context_keyword_hit：检索到的上下文中是否包含期望关键词

本文件只评估“检索是否正确”，不评估 LLM 最终生成答案的质量。
生成答案的评估逻辑应放在 generation_eval.py 中。
"""

from typing import List, Set


def _get_top_k_results(
        retrieved_results: List[RetrievalResult],
        top_k: int,
) -> List[RetrievalResult]:
    """
    截取 top-k 检索结果。

    参数:
        retrieved_results: retriever 返回的标准化检索结果列表
        top_k: 参与评估的前 k 条结果

    返回:
        top_k_results: 前 k 条检索结果
    """
    if top_k <= 0:
        return []

    return retrieved_results[:top_k]


def _get_retrieved_doc_ids(
        retrieved_results: List[RetrievalResult],
) -> List[str]:
    """
    从检索结果中提取 doc_id 列表。

    参数:
        retrieved_results: 检索结果列表

    返回:
        retrieved_doc_ids: 检索到的 doc_id 列表
    """
    return [result.doc_id for result in retrieved_results if result.doc_id]


def compute_hit_at_k(
        expected_doc_ids: List[str],
        retrieved_results: List[RetrievalResult],
        top_k: int,
) -> float:
    """
    计算 hit@k。

    hit@k 表示 top-k 检索结果中是否命中任意一个目标文档。
    只要命中 expected_doc_ids 中的任意一个 doc_id，就返回 1.0；
    否则返回 0.0。
    """
    if not expected_doc_ids:
        return 0.0

    top_k_results = _get_top_k_results(retrieved_results, top_k)
    retrieved_doc_ids = set(_get_retrieved_doc_ids(top_k_results))
    expected_doc_id_set = set(expected_doc_ids)

    return 1.0 if retrieved_doc_ids & expected_doc_id_set else 0.0


def compute_recall_at_k(
        expected_doc_ids: List[str],
        retrieved_results: List[RetrievalResult],
        top_k: int,
) -> float:
    """
    计算 recall@k。

    recall@k = top-k 中命中的目标文档数量 / 目标文档总数量
    """
    if not expected_doc_ids:
        return 0.0

    top_k_results = _get_top_k_results(retrieved_results, top_k)
    retrieved_doc_ids: Set[str] = set(_get_retrieved_doc_ids(top_k_results))
    expected_doc_id_set: Set[str] = set(expected_doc_ids)

    hit_doc_ids = retrieved_doc_ids & expected_doc_id_set

    return len(hit_doc_ids) / len(expected_doc_id_set)


def compute_mrr(
        expected_doc_ids: List[str],
        retrieved_results: List[RetrievalResult],
        top_k: int,
) -> float:
    """
    计算 MRR。

    MRR = Mean Reciprocal Rank。
    对单条样本来说，就是第一个命中目标文档的排名倒数。

    例如：
        rank 1 命中 -> 1 / 1 = 1.0
        rank 2 命中 -> 1 / 2 = 0.5
        rank 3 命中 -> 1 / 3 = 0.333
        top-k 内没有命中 -> 0.0
    """
    if not expected_doc_ids:
        return 0.0

    expected_doc_id_set = set(expected_doc_ids)
    top_k_results = _get_top_k_results(retrieved_results, top_k)

    for index, result in enumerate(top_k_results):
        rank = index + 1
        if result.doc_id in expected_doc_id_set:
            return 1.0 / rank

    return 0.0


def compute_context_keyword_hit(
        expected_keywords: List[str],
        retrieved_results: List[RetrievalResult],
        top_k: int,
) -> float:
    """
    计算 context_keyword_hit。

    该指标用于判断 top-k 检索到的上下文中，是否包含期望关键词。

    context_keyword_hit = 命中的关键词数量 / 期望关键词总数量
    """
    if not expected_keywords:
        return 0.0

    top_k_results = _get_top_k_results(retrieved_results, top_k)

    context_text = "\n".join(
        result.text for result in top_k_results if result.text
    )

    hit_count = 0
    for keyword in expected_keywords:
        if keyword in context_text:
            hit_count += 1

    return hit_count / len(expected_keywords)


def evaluate_retrieval(
        sample: EvalSample,
        retrieved_results: List[RetrievalResult],
        top_k: int,
) -> RetrievalEvalResult:
    """
    对单条 EvalSample 的检索结果进行评估。

    参数:
        sample: 单条评估样本
        retrieved_results: retriever 返回的标准化检索结果
        top_k: 参与评估的前 k 条结果

    返回:
        RetrievalEvalResult: 单条样本的检索评估结果
    """
    hit_at_k = compute_hit_at_k(
        expected_doc_ids=sample.expected_doc_ids,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    recall_at_k = compute_recall_at_k(
        expected_doc_ids=sample.expected_doc_ids,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    mrr = compute_mrr(
        expected_doc_ids=sample.expected_doc_ids,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    context_keyword_hit = compute_context_keyword_hit(
        expected_keywords=sample.expected_keywords,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    return RetrievalEvalResult(
        hit_at_k=hit_at_k,
        recall_at_k=recall_at_k,
        mrr=mrr,
        context_keyword_hit=context_keyword_hit,
    )
