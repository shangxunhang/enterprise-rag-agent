"""计算旧版 RAG 生成链的轻量答案质量指标。

本模块只评估字面关键词、答案与检索上下文的关键词重叠，以及引用文档是否命中
黄金文档。它不实现语义忠实度、答案相关性、NLI、RAGAS 或 LLM-as-Judge，适合
早期回归和接口闭环，不应被解释为完整生成质量评估。
"""

from typing import List, Optional, Set

from rag.schema.eval_schema import (
    EvalSample,
    GenerationEvalResult,
    RetrievalResult,
)


def compute_answer_keyword_hit(
    answer: str,
    answer_keywords: List[str],
) -> float:
    """计算答案对黄金关键词的字面覆盖比例。

    Args:
        answer: LLM 最终生成答案。
        answer_keywords: 预期在答案中出现的核心关键词。

    Returns:
        命中关键词数量除以关键词总数；关键词或答案为空时返回 ``0.0``。
    """

    # 没有黄金关键词时，该旧版指标无法计算，按原协议返回零。
    if not answer_keywords:
        return 0.0

    # 空答案不可能覆盖任何关键词。
    if not answer:
        return 0.0

    # 逐关键词执行大小写敏感的直接子串匹配。
    hit_count = 0
    for keyword in answer_keywords:
        if keyword in answer:
            hit_count += 1

    return hit_count / len(answer_keywords)


def compute_answer_context_overlap(
    answer: str,
    retrieved_results: List[RetrievalResult],
    answer_keywords: Optional[List[str]] = None,
) -> float:
    """估算答案中已出现关键词有多少能在检索上下文中找到。

    Args:
        answer: LLM 最终答案。
        retrieved_results: 当前查询的标准化检索结果。
        answer_keywords: 用作支持判断锚点的黄金答案关键词。

    Returns:
        同时出现在答案和上下文中的关键词数，除以实际出现在答案中的黄金关键词数。
        缺少答案、关键词、上下文或有效关键词时返回 ``0.0``。

    该指标只检查词法重叠，无法识别上下文是否支持答案中的关系、数值或否定结论。
    """

    # 第一步：缺少答案或黄金关键词时无法建立支持关系。
    if not answer:
        return 0.0
    if not answer_keywords:
        return 0.0

    # 第二步：按检索顺序拼接所有非空上下文文本。
    context_text = "\n".join(
        result.text
        for result in retrieved_results
        if result.text
    )
    if not context_text:
        return 0.0

    # 第三步：只评估确实出现在答案中的黄金关键词。
    supported_count = 0
    valid_keyword_count = 0
    for keyword in answer_keywords:
        if keyword in answer:
            valid_keyword_count += 1
            if keyword in context_text:
                supported_count += 1

    # 第四步：答案没有包含任何黄金关键词时，重叠代理记零。
    if valid_keyword_count == 0:
        return 0.0

    return supported_count / valid_keyword_count


def compute_citation_hit(
    expected_doc_ids: List[str],
    cited_doc_ids: Optional[List[str]] = None,
) -> float:
    """判断答案引用是否至少命中一个黄金文档。

    Args:
        expected_doc_ids: 黄金来源文档标识。
        cited_doc_ids: 生成链实际输出的引用文档标识。

    Returns:
        两个集合存在交集时返回 ``1.0``，否则返回 ``0.0``。
    """

    # 缺少黄金文档或实际引用时，无法形成命中。
    if not expected_doc_ids:
        return 0.0
    if not cited_doc_ids:
        return 0.0

    # 转换为集合以消除重复引用对二值指标的影响。
    expected_doc_id_set: Set[str] = set(expected_doc_ids)
    cited_doc_id_set: Set[str] = set(cited_doc_ids)
    return 1.0 if expected_doc_id_set & cited_doc_id_set else 0.0


def evaluate_generation(
    sample: EvalSample,
    answer: str,
    retrieved_results: List[RetrievalResult],
    cited_doc_ids: Optional[List[str]] = None,
) -> GenerationEvalResult:
    """对一条黄金样本的生成结果计算全部旧版轻量指标。

    Args:
        sample: 提供 answer_keywords 和 expected_doc_ids 的评测样本。
        answer: LLM 最终答案。
        retrieved_results: 用于生成答案的检索结果。
        cited_doc_ids: 答案实际引用的文档标识。

    Returns:
        包含关键词命中、上下文重叠和引用命中的 ``GenerationEvalResult``。
    """

    # 第一步：检查最终答案是否覆盖黄金回答关键词。
    answer_keyword_hit = compute_answer_keyword_hit(
        answer=answer,
        answer_keywords=sample.answer_keywords,
    )

    # 第二步：估算答案中的黄金关键词是否能在检索上下文中找到。
    answer_context_overlap = compute_answer_context_overlap(
        answer=answer,
        retrieved_results=retrieved_results,
        answer_keywords=sample.answer_keywords,
    )

    # 第三步：检查结构化引用是否命中黄金文档集合。
    citation_hit = compute_citation_hit(
        expected_doc_ids=sample.expected_doc_ids,
        cited_doc_ids=cited_doc_ids,
    )

    # 第四步：封装稳定结果协议，供旧版 eval_runner 聚合。
    return GenerationEvalResult(
        answer_keyword_hit=answer_keyword_hit,
        answer_context_overlap=answer_context_overlap,
        citation_hit=citation_hit,
    )
