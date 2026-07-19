# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：compute_answer_keyword_hit、compute_answer_context_overlap、compute_citation_hit、evaluate_generation。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
generation_eval.py
==================

RAG 生成答案评估模块。

本文件负责评估 LLM 基于检索结果生成的最终 answer 是否满足基本质量要求。

第一版只实现轻量级生成评估指标：
1. answer_keyword_hit：答案中是否包含期望关键词
2. citation_hit：答案引用的来源是否命中期望文档

本文件暂不实现复杂语义评估，例如：
1. faithfulness
2. answer relevance
3. context relevance
4. LLM-as-a-judge
5. RAGAS / ARES 风格评估

这些高级评估可以在后续版本中继续扩展。
当前阶段的目标是先完成一个简单、可运行、可量化的生成质量评估闭环。
"""




from typing import List, Optional, Set


# 阅读注释（函数）：计算 answer keyword hit。
def compute_answer_keyword_hit(
    answer: str,
    answer_keywords: List[str],
) -> float:
    """
    计算 answer_keyword_hit。

    answer_keyword_hit 用于判断最终生成答案中是否包含期望关键词。

    该指标可以作为 Answer Relevance 的轻量代理指标：
    如果答案中包含问题期望的核心关键词，说明它大概率回答到了问题。

    公式：
        answer_keyword_hit = 命中的 answer_keywords 数量 / answer_keywords 总数量
    """
    if not answer_keywords:
        return 0.0

    if not answer:
        return 0.0

    hit_count = 0

    for keyword in answer_keywords:
        if keyword in answer:
            hit_count += 1

    return hit_count / len(answer_keywords)


# 阅读注释（函数）：计算 answer 上下文 overlap。
def compute_answer_context_overlap(
    answer: str,
    retrieved_results: List[RetrievalResult],
    answer_keywords: Optional[List[str]] = None,
) -> float:
    """
    计算 answer_context_overlap。

    answer_context_overlap 用于判断答案中的关键信息是否能在 retrieved context 中找到依据。

    该指标可以作为 Answer Faithfulness 的轻量代理指标：
    如果答案中出现的核心关键词也能在检索上下文中找到，
    说明答案大概率是基于 context 生成的，而不是完全凭空编造。

    第一版采用关键词级别的简单实现：
    1. 如果提供 answer_keywords，则检查这些关键词是否同时出现在 answer 和 context 中
    2. 如果没有提供 answer_keywords，则暂时返回 0.0

    后续可以升级为：
    1. LLM-as-a-judge faithfulness
    2. NLI contradiction detection
    3. RAGAS faithfulness
    4. ARES-style judge model
    """
    if not answer:
        return 0.0

    if not answer_keywords:
        return 0.0

    context_text = "\n".join(
        result.text for result in retrieved_results if result.text
    )

    if not context_text:
        return 0.0

    supported_count = 0
    valid_keyword_count = 0

    for keyword in answer_keywords:
        # 只评估确实出现在 answer 中的关键词
        if keyword in answer:
            valid_keyword_count += 1

            if keyword in context_text:
                supported_count += 1

    if valid_keyword_count == 0:
        return 0.0

    return supported_count / valid_keyword_count


# 阅读注释（函数）：计算 引用 hit。
def compute_citation_hit(
    expected_doc_ids: List[str],
    cited_doc_ids: Optional[List[str]] = None,
) -> float:
    """
    计算 citation_hit。

    citation_hit 用于判断最终答案引用的来源文档是否命中 expected_doc_ids。

    如果答案引用的 doc_id 中，至少有一个出现在 expected_doc_ids 中，则返回 1.0；
    否则返回 0.0。

    注意：
        第一版中 cited_doc_ids 需要由上游生成模块或 eval_runner 传入。
        如果你的 generator 暂时还不能输出引用来源，可以传 None 或空列表。
    """
    if not expected_doc_ids:
        return 0.0

    if not cited_doc_ids:
        return 0.0

    expected_doc_id_set: Set[str] = set(expected_doc_ids)
    cited_doc_id_set: Set[str] = set(cited_doc_ids)

    return 1.0 if expected_doc_id_set & cited_doc_id_set else 0.0


# 阅读注释（函数）：评估 生成。
def evaluate_generation(
    sample: EvalSample,
    answer: str,
    retrieved_results: List[RetrievalResult],
    cited_doc_ids: Optional[List[str]] = None,
) -> GenerationEvalResult:
    """
    对单条 EvalSample 的生成答案进行评估。

    参数:
        sample:
            单条评估样本，包含 query、expected_doc_ids、answer_keywords 等信息。

        answer:
            LLM 最终生成的答案。

        retrieved_results:
            当前 query 对应的检索结果，用于判断 answer 是否被 context 支持。

        cited_doc_ids:
            answer 中实际引用的 doc_id 列表。
            如果当前生成模块还没有 citation 功能，可以传 None。

    返回:
        GenerationEvalResult:
            单条样本的生成评估结果。
    """
    answer_keyword_hit = compute_answer_keyword_hit(
        answer=answer,
        answer_keywords=sample.answer_keywords,
    )

    answer_context_overlap = compute_answer_context_overlap(
        answer=answer,
        retrieved_results=retrieved_results,
        answer_keywords=sample.answer_keywords,
    )

    citation_hit = compute_citation_hit(
        expected_doc_ids=sample.expected_doc_ids,
        cited_doc_ids=cited_doc_ids,
    )

    return GenerationEvalResult(
        answer_keyword_hit=answer_keyword_hit,
        answer_context_overlap=answer_context_overlap,
        citation_hit=citation_hit,
    )