"""计算旧版 RAG 检索链的文档级轻量指标。

本模块只判断 top-k 结果是否命中黄金 doc_id，以及检索文本是否覆盖黄金关键词。
它不评估父子块粒度、语义相关性或最终生成答案；更完整的策略评测位于
``eval.rag.strategy``。
"""

from typing import List, Set

from rag.schema.eval_schema import (
    EvalSample,
    RetrievalEvalResult,
    RetrievalResult,
)


def _get_top_k_results(
    retrieved_results: List[RetrievalResult],
    top_k: int,
) -> List[RetrievalResult]:
    """按原始排序截取参与评测的前 k 条结果。

    Args:
        retrieved_results: Retriever 返回的标准化有序结果。
        top_k: 参与指标计算的最大结果数。

    Returns:
        ``top_k <= 0`` 时返回空列表，否则返回前 k 条结果的切片。
    """

    # 非正 top_k 没有有效评测窗口，按原协议返回空集合。
    if top_k <= 0:
        return []
    return retrieved_results[:top_k]


def _get_retrieved_doc_ids(
    retrieved_results: List[RetrievalResult],
) -> List[str]:
    """提取结果中的非空文档标识并保持排名顺序。"""

    # 不去重，MRR 等顺序指标仍需保留原排名；集合化由具体指标自行处理。
    return [
        result.doc_id
        for result in retrieved_results
        if result.doc_id
    ]


def compute_hit_at_k(
    expected_doc_ids: List[str],
    retrieved_results: List[RetrievalResult],
    top_k: int,
) -> float:
    """计算 top-k 是否至少命中一个黄金文档。

    Returns:
        top-k 文档集合与黄金集合有交集时返回 ``1.0``，否则返回 ``0.0``。
    """

    # 没有黄金文档时无法定义命中，旧版协议返回零。
    if not expected_doc_ids:
        return 0.0

    # 截断后集合化，重复召回不会多次影响二值指标。
    top_results = _get_top_k_results(retrieved_results, top_k)
    retrieved_doc_ids = set(_get_retrieved_doc_ids(top_results))
    expected_doc_id_set = set(expected_doc_ids)
    return 1.0 if retrieved_doc_ids & expected_doc_id_set else 0.0


def compute_recall_at_k(
    expected_doc_ids: List[str],
    retrieved_results: List[RetrievalResult],
    top_k: int,
) -> float:
    """计算 top-k 对黄金文档集合的去重召回率。"""

    if not expected_doc_ids:
        return 0.0

    # 第一步：提取 top-k 文档集合和黄金文档集合。
    top_results = _get_top_k_results(retrieved_results, top_k)
    retrieved_doc_ids: Set[str] = set(
        _get_retrieved_doc_ids(top_results)
    )
    expected_doc_id_set: Set[str] = set(expected_doc_ids)

    # 第二步：交集大小除以黄金集合大小，重复结果不会重复计数。
    hit_doc_ids = retrieved_doc_ids & expected_doc_id_set
    return len(hit_doc_ids) / len(expected_doc_id_set)


def compute_mrr(
    expected_doc_ids: List[str],
    retrieved_results: List[RetrievalResult],
    top_k: int,
) -> float:
    """计算单条样本第一个黄金文档排名的倒数。"""

    if not expected_doc_ids:
        return 0.0

    # 第一步：建立黄金集合并截断结果窗口。
    expected_doc_id_set = set(expected_doc_ids)
    top_results = _get_top_k_results(retrieved_results, top_k)

    # 第二步：从排名 1 开始查找第一个命中项。
    for index, result in enumerate(top_results):
        rank = index + 1
        if result.doc_id in expected_doc_id_set:
            return 1.0 / rank

    return 0.0


def compute_context_keyword_hit(
    expected_keywords: List[str],
    retrieved_results: List[RetrievalResult],
    top_k: int,
) -> float:
    """计算黄金关键词在 top-k 检索文本中的字面覆盖比例。"""

    if not expected_keywords:
        return 0.0

    # 第一步：按排名截断并拼接所有非空文本。
    top_results = _get_top_k_results(retrieved_results, top_k)
    context_text = "\n".join(
        result.text
        for result in top_results
        if result.text
    )

    # 第二步：使用大小写敏感的直接子串匹配统计命中数。
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
    """对一条样本的检索结果计算全部旧版文档级指标。

    Args:
        sample: 提供 expected_doc_ids 和 expected_keywords 的黄金样本。
        retrieved_results: Retriever 返回的标准化结果。
        top_k: 参与计算的结果窗口。

    Returns:
        包含 hit@k、recall@k、MRR 和关键词覆盖的结果对象。
    """

    # 第一步：计算至少一次命中的二值指标。
    hit_at_k = compute_hit_at_k(
        expected_doc_ids=sample.expected_doc_ids,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    # 第二步：计算黄金文档集合的去重召回率。
    recall_at_k = compute_recall_at_k(
        expected_doc_ids=sample.expected_doc_ids,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    # 第三步：计算首个正确结果的排名质量。
    mrr = compute_mrr(
        expected_doc_ids=sample.expected_doc_ids,
        retrieved_results=retrieved_results,
        top_k=top_k,
    )

    # 第四步：检查检索文本对黄金关键词的字面覆盖。
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
