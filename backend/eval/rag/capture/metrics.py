"""计算不依赖模型裁判的轻量 RAG 代理指标。

本模块直接消费 Agent 项目产生的任务级 DataCapture 记录，通过稳定的中英文词法
特征估算检索相关性、上下文覆盖、回答忠实度、回答相关性、引用覆盖和结构完整度。
这些分数适合低成本回归和样本筛选，不等价于官方 RAGAS、语义模型或人工判断。
每个公开指标都返回统一 ``RAGEvalMetricSchema``，因此以后可以替换内部算法而不改
Runner 和报告协议。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Sequence, Set

from .schemas import RAGEvalMetricSchema


# 中文按单个 CJK 字符退化切分，英文和数字按连续词单元切分。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")

# 停用词用于削弱高频功能词对词法覆盖率的虚假贡献。
STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
    "is", "are", "was", "were", "be", "by", "as", "from", "that", "this",
    "根据", "资料", "生成", "系统", "方案", "项目", "建设", "进行", "一个",
    "以及", "通过", "当前", "需要", "实现", "能力", "模块", "包括", "相关",
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """把数值限制在指定闭区间内。

    Args:
        value: 待限制的数值。
        low: 区间下界。
        high: 区间上界。

    Returns:
        转换为 ``float`` 后位于 ``[low, high]`` 的值。
    """

    # 统一限制所有代理分数，防止组合公式或异常输入越过报告约定范围。
    return max(low, min(high, float(value)))


def tokenize(text: str) -> List[str]:
    """对中英文混合文本执行稳定的轻量词法切分。

    Args:
        text: 待分析文本。

    Returns:
        已转小写、去空值并过滤停用词的 token 列表。中文使用单字作为粗粒度
        fallback，英文、数字和下划线标识符按正则词单元切分。

    该实现追求跨环境确定性，不依赖外部分词模型；因此不能表达同义词和短语语义。
    """

    # 空文本没有可比较特征，直接返回空列表。
    if not text:
        return []

    # 先提取英文/数字词并转小写，再追加中文字符。
    tokens: List[str] = []
    tokens.extend(item.lower() for item in _WORD_RE.findall(text))
    tokens.extend(_CJK_RE.findall(text))

    # 最后过滤空 token 和预定义停用词，降低通用词造成的虚假命中。
    return [item for item in tokens if item and item not in STOPWORDS]


def token_set(text: str) -> Set[str]:
    """返回文本去重后的词法特征集合。"""

    # 集合形式适合计算交并比，不保留词频和原始顺序。
    return set(tokenize(text))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """计算两个 token 集合的 Jaccard 相似度。

    Args:
        a: 第一组 token。
        b: 第二组 token。

    Returns:
        交集大小除以并集大小；任一集合为空时返回 ``0.0``。
    """

    # 将任意可迭代输入归一化为集合，消除重复 token 的影响。
    left = set(a)
    right = set(b)
    if not left or not right:
        return 0.0

    # Jaccard 对长短接近的文本较直观，但对长回答会偏严格。
    return len(left & right) / len(left | right)


def coverage(
    source_tokens: Sequence[str],
    target_tokens: Sequence[str],
) -> float:
    """计算目标 token 中有多少被来源 token 覆盖。

    Args:
        source_tokens: 被视为证据或承载文本的 token。
        target_tokens: 希望被覆盖的 token。

    Returns:
        ``|source ∩ target| / |target|``；目标为空时返回 ``0.0``。
    """

    # 覆盖率方向是非对称的，分母始终使用目标集合。
    source_set = set(source_tokens)
    target_set = set(target_tokens)
    if not target_set:
        return 0.0
    return len(source_set & target_set) / len(target_set)


def extract_text_from_chunk(chunk: Dict[str, Any]) -> str:
    """从不同版本的召回块中提取首个可用文本字段。

    Args:
        chunk: RAG 召回块或证据块字典。

    Returns:
        按上下文优先级找到的首个非空字符串；没有文本时返回空字符串。
    """

    # 优先完整父级上下文，其次匹配片段和通用兼容字段。
    for key in (
        "context_text",
        "match_text",
        "text",
        "content",
        "quote_text",
        "summary",
    ):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_context_text(sample: Dict[str, Any]) -> str:
    """从新旧采集记录中提取已打包的 RAG 上下文文本。

    Args:
        sample: DataCaptureRecord 风格样本。

    Returns:
        顶层 ``rag_context.context_text``，或旧版
        ``eval_sample.rag_context.context_text``；均缺失时返回空字符串。
    """

    # 新版记录以顶层 rag_context 为事实来源。
    rag_context = sample.get("rag_context") or {}
    if isinstance(rag_context, dict):
        text = rag_context.get("context_text")
        if isinstance(text, str):
            return text

    # 旧版评测样本可能把上下文嵌套在 eval_sample 中，作为单向兼容回退。
    eval_sample = sample.get("eval_sample") or {}
    if isinstance(eval_sample, dict):
        nested = eval_sample.get("rag_context") or {}
        if isinstance(nested, dict) and isinstance(
            nested.get("context_text"),
            str,
        ):
            return nested["context_text"]

    return ""


def extract_answer(sample: Dict[str, Any]) -> str:
    """按交付优先级从采集记录中提取回答文本。

    Args:
        sample: DataCaptureRecord 风格样本。

    Returns:
        优先返回正式交付物，其次生成草稿和原始模型输出；顶层缺失时对旧版
        ``eval_sample`` 使用相同顺序回退。
    """

    # 新记录优先评价真正交付给用户的文本，而不是中间模型输出。
    answer_keys = (
        "deliverable_output",
        "generated_draft_output",
        "model_output",
    )
    for key in answer_keys:
        value = sample.get(key)
        if isinstance(value, str) and value.strip():
            return value

    # 兼容旧记录中的嵌套评测字段。
    eval_sample = sample.get("eval_sample") or {}
    if isinstance(eval_sample, dict):
        for key in answer_keys:
            value = eval_sample.get(key)
            if isinstance(value, str) and value.strip():
                return value

    return ""


def extract_query(sample: Dict[str, Any]) -> str:
    """从采集记录中提取触发本次 RAG 的原始查询。

    Args:
        sample: DataCaptureRecord 风格样本。

    Returns:
        顶层 ``user_input``、``query``、``input`` 中首个非空字符串；若缺失，
        回退到旧版 ``eval_sample.input``。
    """

    # 顶层字段按当前协议到通用兼容字段排序。
    for key in ("user_input", "query", "input"):
        value = sample.get(key)
        if isinstance(value, str) and value.strip():
            return value

    # 旧版 eval_sample 只约定 input 字段。
    eval_sample = sample.get("eval_sample") or {}
    if isinstance(eval_sample, dict):
        value = eval_sample.get("input")
        if isinstance(value, str):
            return value

    return ""


def _chunk_relevance_scores(
    query: str,
    chunks: List[Dict[str, Any]],
) -> List[float]:
    """为每个召回块计算查询相关性代理分数。

    Args:
        query: 原始查询文本。
        chunks: 按检索或重排顺序排列的召回块。

    Returns:
        与 ``chunks`` 一一对应的 ``[0, 1]`` 分数列表。

    词法覆盖是主信号；存在数值 ``rerank_score`` 时，将其经过 sigmoid 压缩后只
    作为 25% 的弱加成，避免不同重排模型的原始 logit 尺度直接主导结果。
    """

    # 第一步：查询只分词一次，供所有块复用。
    query_tokens = tokenize(query)
    scores: List[float] = []

    # 第二步：逐块提取文本并计算其对查询 token 的覆盖率。
    for chunk in chunks:
        chunk_text = extract_text_from_chunk(chunk)
        chunk_tokens = tokenize(chunk_text)
        lexical_score = coverage(chunk_tokens, query_tokens)

        # 第三步：可选重排分数经过 sigmoid 映射，兼容可能为负的 BGE logit。
        rerank_score = chunk.get("rerank_score")
        rerank_bonus = 0.0
        if isinstance(rerank_score, (int, float)):
            rerank_bonus = 1.0 / (1.0 + math.exp(-float(rerank_score)))

        # 第四步：有重排信号时采用 75/25 混合，否则保留纯词法分数。
        score = (
            0.75 * lexical_score + 0.25 * rerank_bonus
            if rerank_bonus
            else lexical_score
        )
        scores.append(clamp(score))

    return scores


def context_precision(
    query: str,
    retrieved_chunks: List[Dict[str, Any]],
) -> RAGEvalMetricSchema:
    """估算排序后召回结果与查询的相关程度。

    Args:
        query: 原始查询文本。
        retrieved_chunks: 按最终检索顺序排列的召回块。

    Returns:
        采用倒数排名权重的相关性均值，越靠前的块对结果影响越大。
    """

    # 没有召回块时明确记零，并保留数量诊断。
    if not retrieved_chunks:
        return RAGEvalMetricSchema(
            name="context_precision",
            score=0.0,
            reason="No retrieved chunks.",
            details={"retrieved_chunk_num": 0},
        )

    # 第一步：计算每个块的词法/重排混合相关性代理。
    scores = _chunk_relevance_scores(
        query=query,
        chunks=retrieved_chunks,
    )
    relevant_count = sum(1 for score in scores if score > 0.0)

    # 第二步：使用 1/rank 权重，让头部低质量结果受到更强惩罚。
    weighted_sum = 0.0
    weight_total = 0.0
    for rank, score in enumerate(scores, start=1):
        weight = 1.0 / rank
        weighted_sum += score * weight
        weight_total += weight

    final_score = weighted_sum / weight_total if weight_total else 0.0

    # 第三步：返回分数以及逐块分数，便于定位具体低相关召回。
    return RAGEvalMetricSchema(
        name="context_precision",
        score=clamp(final_score),
        reason="Rank-aware lexical relevance between query and retrieved chunks.",
        details={
            "retrieved_chunk_num": len(retrieved_chunks),
            "relevant_count_proxy": relevant_count,
            "chunk_scores": scores,
        },
    )


def context_recall_proxy(
    query: str,
    context_text: str,
    retrieved_chunks: List[Dict[str, Any]],
) -> RAGEvalMetricSchema:
    """估算打包上下文对查询关键 token 的覆盖程度。

    该指标没有使用黄金证据，因此只能称为 recall proxy。它回答“查询中可识别的
    词法信息有多少进入最终上下文”，不能证明真正相关证据是否全部召回。
    """

    # 第一步：分别切分查询和最终送入生成链的上下文。
    query_tokens = tokenize(query)
    context_tokens = tokenize(context_text)

    # 第二步：为空输入提供明确退化原因；正常情况计算查询 token 覆盖率。
    if not query_tokens:
        score = 0.0
        reason = "Empty query tokens."
    elif not context_tokens:
        score = 0.0
        reason = "Empty context."
    else:
        score = coverage(context_tokens, query_tokens)
        reason = "Proxy recall: how much query evidence appears in packed context."

    # 第三步：召回块数量只用于诊断，不直接参与该上下文级分数。
    return RAGEvalMetricSchema(
        name="context_recall_proxy",
        score=clamp(score),
        reason=reason,
        details={
            "query_token_num": len(set(query_tokens)),
            "context_token_num": len(set(context_tokens)),
            "retrieved_chunk_num": len(retrieved_chunks),
        },
    )


def faithfulness_proxy(
    answer: str,
    context_text: str,
) -> RAGEvalMetricSchema:
    """估算回答 token 是否能在证据上下文中找到词法支持。

    Returns:
        上下文对回答 token 的覆盖率，并附带最多 30 个未支持 token 预览。

    该代理无法识别否定、数值关系或逻辑矛盾，只适合筛查明显脱离上下文的回答。
    """

    # 第一步：切分回答和上下文；任一为空都无法建立忠实度支持。
    answer_tokens = tokenize(answer)
    context_tokens = tokenize(context_text)
    if not answer_tokens:
        return RAGEvalMetricSchema(
            name="faithfulness_proxy",
            score=0.0,
            reason="Empty answer.",
        )
    if not context_tokens:
        return RAGEvalMetricSchema(
            name="faithfulness_proxy",
            score=0.0,
            reason="Empty context.",
        )

    # 第二步：计算上下文对回答 token 的覆盖率。
    score = coverage(context_tokens, answer_tokens)

    # 第三步：提取未被上下文覆盖的 token 作为人工诊断线索。
    unsupported = sorted(set(answer_tokens) - set(context_tokens))[:30]

    return RAGEvalMetricSchema(
        name="faithfulness_proxy",
        score=clamp(score),
        reason="Proxy faithfulness: answer token coverage by context.",
        details={
            "answer_token_num": len(set(answer_tokens)),
            "context_token_num": len(set(context_tokens)),
            "unsupported_token_preview": unsupported,
        },
    )


def answer_relevance_proxy(
    query: str,
    answer: str,
) -> RAGEvalMetricSchema:
    """估算最终回答是否覆盖查询中的关键 token。

    长回答会天然包含额外 token，因此这里使用“回答覆盖查询”的非对称覆盖率，
    而不是对长文本惩罚更强的 Jaccard 相似度。
    """

    # 第一步：切分查询和回答。
    query_tokens = tokenize(query)
    answer_tokens = tokenize(answer)

    # 第二步：空查询或空回答无法判断相关性；否则计算查询 token 覆盖率。
    if not query_tokens or not answer_tokens:
        score = 0.0
        reason = "Empty query or answer tokens."
    else:
        score = coverage(answer_tokens, query_tokens)
        reason = "Proxy answer relevance: query token coverage by answer."

    return RAGEvalMetricSchema(
        name="answer_relevance_proxy",
        score=clamp(score),
        reason=reason,
        details={
            "query_token_num": len(set(query_tokens)),
            "answer_token_num": len(set(answer_tokens)),
        },
    )


def citation_coverage(
    answer: str,
    citations: List[Dict[str, Any]],
    retrieved_chunks: List[Dict[str, Any]],
) -> RAGEvalMetricSchema:
    """估算引用数量及其与召回块身份的一致性。

    Args:
        answer: 最终回答文本。
        citations: 回答输出的结构化引用列表。
        retrieved_chunks: 本次运行的召回块列表。

    Returns:
        引用数量饱和分和引用块身份重叠率的加权组合。该指标不验证引用是否绑定到
        每个具体声明，也不判断引用内容是否真正支持该声明。
    """

    # 第一步：记录回答长度，并为无回答或无引用提供明确零分原因。
    answer_chars = len(answer or "")
    if not answer.strip():
        return RAGEvalMetricSchema(
            name="citation_coverage",
            score=0.0,
            reason="Empty answer.",
        )
    if not citations:
        return RAGEvalMetricSchema(
            name="citation_coverage",
            score=0.0,
            reason="No citations returned.",
            details={
                "citation_num": 0,
                "retrieved_chunk_num": len(retrieved_chunks),
            },
        )

    # 第二步：从引用中收集各种兼容粒度的块标识。
    cited_chunk_ids: Set[str] = set()
    for citation in citations:
        for key in ("chunk_id", "child_chunk_id", "parent_chunk_id"):
            value = citation.get(key)
            if value:
                cited_chunk_ids.add(str(value))

    # 第三步：从召回结果中收集匹配块和上下文块标识。
    retrieved_ids: Set[str] = set()
    for chunk in retrieved_chunks:
        for key in (
            "matched_chunk_id",
            "context_chunk_id",
            "child_chunk_id",
            "parent_chunk_id",
            "chunk_id",
        ):
            value = chunk.get(key)
            if value:
                retrieved_ids.add(str(value))

    # 第四步：计算引用标识中有多少确实来自本次召回结果。
    citation_chunk_overlap = (
        len(cited_chunk_ids & retrieved_ids) / len(cited_chunk_ids)
        if cited_chunk_ids
        else 0.0
    )

    # 第五步：引用数量最多按三个有效引用饱和，避免堆叠引用无限抬高分数。
    expected_count = max(1, min(3, len(retrieved_chunks) or 3))
    count_score = min(1.0, len(citations) / expected_count)

    # 第六步：有块身份时数量占 70%、血缘一致性占 30%；否则只能使用数量代理。
    score = (
        0.7 * count_score + 0.3 * citation_chunk_overlap
        if cited_chunk_ids
        else count_score
    )

    return RAGEvalMetricSchema(
        name="citation_coverage",
        score=clamp(score),
        reason="Citation count and citation-to-retrieval overlap proxy.",
        details={
            "citation_num": len(citations),
            "retrieved_chunk_num": len(retrieved_chunks),
            "cited_chunk_ids": sorted(cited_chunk_ids),
            "citation_chunk_overlap": citation_chunk_overlap,
            "answer_chars": answer_chars,
        },
    )


def completeness_proxy(
    answer: str,
    required_sections: List[str] | None = None,
) -> RAGEvalMetricSchema:
    """估算回答对必需章节标题的覆盖程度。

    Args:
        answer: 最终回答文本。
        required_sections: 黄金样本要求出现的章节标题。

    Returns:
        有章节要求时返回标题字面命中比例；没有要求时退化为最多 800 字符饱和的
        长度代理。长度代理只能排除明显空洞回答，不代表内容完整。
    """

    # 第一步：归一化可选章节和回答值。
    required_sections = required_sections or []
    answer = answer or ""

    # 第二步：空回答直接记零。
    if not answer.strip():
        return RAGEvalMetricSchema(
            name="completeness_proxy",
            score=0.0,
            reason="Empty answer.",
        )

    # 第三步：无黄金章节时按非平凡回答长度给出弱代理分数。
    if not required_sections:
        score = min(1.0, len(answer) / 800)
        return RAGEvalMetricSchema(
            name="completeness_proxy",
            score=clamp(score),
            reason="No required sections provided; score by answer length.",
            details={"answer_chars": len(answer)},
        )

    # 第四步：逐一检查章节标题是否在回答中出现，并保留命中/缺失清单。
    hit: List[str] = []
    miss: List[str] = []
    for section in required_sections:
        if section and section in answer:
            hit.append(section)
        else:
            miss.append(section)

    # 第五步：使用黄金章节总数作为分母计算覆盖率。
    score = len(hit) / len(required_sections) if required_sections else 0.0

    return RAGEvalMetricSchema(
        name="completeness_proxy",
        score=clamp(score),
        reason="Required section coverage.",
        details={
            "required_sections": required_sections,
            "hit": hit,
            "miss": miss,
        },
    )


def weighted_overall(
    metrics: Dict[str, RAGEvalMetricSchema],
) -> float:
    """按固定指标配置计算归一化总分。

    Args:
        metrics: 以稳定指标名称为键的指标映射。缺失指标会从分子和分母同时排除。

    Returns:
        已按实际参与权重重新归一化并限制在 ``[0, 1]`` 的总分。
    """

    # 忠实度权重最高，检索精度和回答相关性次之；引用与结构各占 10%。
    weights = {
        "context_precision": 0.20,
        "context_recall_proxy": 0.15,
        "faithfulness_proxy": 0.25,
        "answer_relevance_proxy": 0.20,
        "citation_coverage": 0.10,
        "completeness_proxy": 0.10,
    }

    # 第一步：只累计实际存在的指标，允许渐进替换或裁剪指标集合。
    total = 0.0
    weight_total = 0.0
    for name, weight in weights.items():
        metric = metrics.get(name)
        if metric is None:
            continue
        total += metric.score * weight
        weight_total += weight

    # 第二步：按参与权重归一化；完全没有指标时返回零。
    return clamp(total / weight_total if weight_total else 0.0)
