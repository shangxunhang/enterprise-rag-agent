# -*- coding: utf-8 -*-
"""计算本地、确定性的 RAGAS 风格代理指标。

本模块不是官方 ``ragas`` 包，也不调用外部 LLM Judge。它面向父子块 RAG QA 的
工程回归，从运行记录和黄金样本计算五个代理维度：上下文精度、上下文召回、回答
忠实度、回答相关性和引用命中。指标结构刻意接近 RAGAS，便于以后替换为 DeepSeek
裁判或官方实现，但当前结果只应解释为低成本词法/身份代理。
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from eval.rag.engine.rag_eval_case_schema import RagEvalCase


def _as_list(values: Optional[Sequence[str]]) -> List[str]:
    """把可选字符串序列规范化为非空、去首尾空白的列表。"""

    # None 或空序列统一转换为空列表，简化后续指标分支。
    if not values:
        return []
    return [
        str(item).strip()
        for item in values
        if str(item).strip()
    ]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """尽力把任意值转换为浮点数，失败时返回默认值。"""

    # 聚合报告可能读取历史或部分损坏结果，单个非法值不应使整份报告崩溃。
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clip01(value: float) -> float:
    """把代理分数限制在 ``[0, 1]``。"""

    return max(0.0, min(1.0, float(value)))


def _normalize_text(text: Any) -> str:
    """把任意文本值转换为字符串并统一换行符。"""

    if text is None:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _keyword_hit_ratio(
    keywords: Sequence[str],
    text: str,
) -> float:
    """计算关键词在文本中的大小写敏感字面命中比例。"""

    # 第一步：规范化关键词并处理无标签、无文本的退化情况。
    normalized_keywords = _as_list(keywords)
    if not normalized_keywords:
        return 0.0
    if not text:
        return 0.0

    # 第二步：每个关键词最多计一次命中。
    hit_count = sum(
        1
        for keyword in normalized_keywords
        if keyword and keyword in text
    )
    return hit_count / len(normalized_keywords)


def _first_text_field(
    item: Dict[str, Any],
    fields: Sequence[str] = ("text", "parent_text", "child_text"),
) -> str:
    """从召回项中按优先级提取首个非空文本字段。"""

    # 每个结果只选择一个文本表示，防止父子文本同时拼接造成重复证据。
    for field in fields:
        value = item.get(field)
        if value:
            return _normalize_text(value)
    return ""


def _top_k_results(
    results: Sequence[Dict[str, Any]],
    top_k: Optional[int],
) -> List[Dict[str, Any]]:
    """按原始排名返回全部结果或前 k 条结果。"""

    # None 或非正值沿用旧行为：不截断，而不是返回空列表。
    if top_k is None or top_k <= 0:
        return list(results)
    return list(results)[: int(top_k)]


def _collect_context_text_from_results(
    results: Sequence[Dict[str, Any]],
    top_k: Optional[int] = None,
) -> str:
    """按排名拼接召回结果的首选文本字段。"""

    parts: List[str] = []

    # 先按 top_k 截断，再从每个结果提取唯一文本表示。
    for item in _top_k_results(results, top_k):
        text = _first_text_field(item)
        if text:
            parts.append(text)

    return "\n".join(parts)


def _collect_context_text_from_run(run_record: Dict[str, Any]) -> str:
    """按运行协议优先级恢复真正用于生成的上下文文本。

    优先级为 ``packed_context`` > ``context_pack.context`` >
    ``context_pack.selected_results`` > ``retrieval_results``。这样评估首先针对最终打包
    上下文，而不是把被 Context Packer 丢弃的召回结果也视为生成证据。
    """

    # 第一步：当前协议可能直接保存最终 packed_context。
    packed_context = _normalize_text(run_record.get("packed_context"))
    if packed_context:
        return packed_context

    # 第二步：兼容结构化 context_pack 的渲染文本或选中结果。
    context_pack = run_record.get("context_pack") or {}
    if isinstance(context_pack, dict):
        context = _normalize_text(context_pack.get("context"))
        if context:
            return context

        selected_results = context_pack.get("selected_results") or []
        if isinstance(selected_results, list):
            return _collect_context_text_from_results(selected_results)

    # 第三步：旧运行记录没有 Context Packer 时回退到全部召回结果。
    retrieval_results = run_record.get("retrieval_results") or []
    if isinstance(retrieval_results, list):
        return _collect_context_text_from_results(retrieval_results)

    return ""


def _result_relevance_score(
    item: Dict[str, Any],
    case: RagEvalCase,
) -> float:
    """为单个召回项计算 ``[0, 1]`` 的确定性相关性代理。

    精确子块或父块命中得 1.0，文档命中得 0.75；仅关键词命中时得到
    ``0.35 + 0.25 * keyword_ratio`` 的较弱分数。多个信号取最大值，避免同一证据因
    多粒度标签重复加分。
    """

    # 第一步：提取召回项多粒度身份和首选文本。
    doc_id = str(item.get("doc_id") or "")
    parent_id = str(item.get("parent_chunk_id") or "")
    child_id = str(
        item.get("child_chunk_id") or item.get("chunk_id") or ""
    )
    text = _first_text_field(item)

    # 第二步：收集当前样本实际可用的相关性信号。
    scores: List[float] = []
    if (
        case.expected_child_chunk_ids
        and child_id in set(case.expected_child_chunk_ids)
    ):
        scores.append(1.0)
    if (
        case.expected_parent_chunk_ids
        and parent_id in set(case.expected_parent_chunk_ids)
    ):
        scores.append(1.0)
    if case.expected_doc_ids and doc_id in set(case.expected_doc_ids):
        scores.append(0.75)

    # 第三步：关键词只能提供弱相关性，不能替代精确来源身份。
    if case.expected_keywords:
        keyword_score = _keyword_hit_ratio(
            case.expected_keywords,
            text,
        )
        if keyword_score > 0:
            scores.append(0.35 + 0.25 * keyword_score)

    # 第四步：取最强信号并限制到统一分数范围。
    return _clip01(max(scores) if scores else 0.0)


def compute_context_precision_proxy(
    retrieval_results: Sequence[Dict[str, Any]],
    case: RagEvalCase,
    *,
    top_k: Optional[int] = None,
    relevant_threshold: float = 0.35,
) -> Tuple[float, List[Dict[str, Any]]]:
    """用二值相关阈值上的 Average Precision 近似 Context Precision。

    Args:
        retrieval_results: 按最终排名排列的召回结果。
        case: 提供多粒度黄金 ID 和关键词的样本。
        top_k: 可选评测截断窗口。
        relevant_threshold: 将连续代理分数判定为相关的阈值。

    Returns:
        ``(score, rank_details)``。明细保留每个排名的身份、连续相关性分数和二值判定。
    """

    # 第一步：截断结果；没有结果时返回零和空明细。
    ranked = _top_k_results(retrieval_results, top_k)
    if not ranked:
        return 0.0, []

    details: List[Dict[str, Any]] = []
    relevant_seen = 0
    precision_sum = 0.0

    # 第二步：逐排名计算相关性，并在相关项处累计当前 precision@rank。
    for rank, item in enumerate(ranked, start=1):
        relevance = _result_relevance_score(item, case)
        is_relevant = relevance >= relevant_threshold
        if is_relevant:
            relevant_seen += 1
            precision_sum += relevant_seen / rank

        # 第三步：保存排名级诊断，便于定位头部噪声或粒度错误。
        details.append(
            {
                "rank": rank,
                "doc_id": item.get("doc_id"),
                "parent_chunk_id": item.get("parent_chunk_id"),
                "child_chunk_id": (
                    item.get("child_chunk_id") or item.get("chunk_id")
                ),
                "relevance_score": relevance,
                "is_relevant": is_relevant,
            }
        )

    # 第四步：无相关项时 AP 为零，否则除以实际相关项数量。
    if relevant_seen == 0:
        return 0.0, details
    return _clip01(precision_sum / relevant_seen), details


def _id_recall(
    expected: Sequence[str],
    observed: Sequence[str],
) -> Optional[float]:
    """计算某一身份粒度的去重召回率。

    没有该粒度黄金标签时返回 ``None``，表示不适用；这使多粒度总分只平均实际存在
    的黄金维度，而不会把缺失标签当作零分。
    """

    expected_set = {
        str(item)
        for item in expected
        if str(item)
    }
    if not expected_set:
        return None

    observed_set = {
        str(item)
        for item in observed
        if str(item)
    }
    return len(expected_set & observed_set) / len(expected_set)


def compute_context_recall_proxy(
    retrieval_results: Sequence[Dict[str, Any]],
    case: RagEvalCase,
    *,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """用多粒度 ID 召回和关键词覆盖近似 Context Recall。

    返回的总分是所有适用组件的算术平均：文档召回、父块召回、子块召回和关键词
    召回。未提供黄金标签的组件为 ``None``，不进入平均。
    """

    # 第一步：截断结果并提取多粒度观察身份。
    ranked = _top_k_results(retrieval_results, top_k)
    docs = [str(item.get("doc_id") or "") for item in ranked]
    parents = [
        str(item.get("parent_chunk_id") or "")
        for item in ranked
    ]
    children = [
        str(
            item.get("child_chunk_id")
            or item.get("chunk_id")
            or ""
        )
        for item in ranked
    ]

    # 第二步：拼接当前评测窗口内的上下文，用于关键词召回。
    context_text = _collect_context_text_from_results(ranked)

    # 第三步：分别计算每个有黄金标签的召回组件。
    components: Dict[str, Optional[float]] = {
        "doc_recall": _id_recall(case.expected_doc_ids, docs),
        "parent_recall": _id_recall(
            case.expected_parent_chunk_ids,
            parents,
        ),
        "child_recall": _id_recall(
            case.expected_child_chunk_ids,
            children,
        ),
        "keyword_recall": (
            _keyword_hit_ratio(case.expected_keywords, context_text)
            if case.expected_keywords
            else None
        ),
    }

    # 第四步：只平均适用组件；完全没有黄金信号时返回零。
    valid_values = [
        value
        for value in components.values()
        if value is not None
    ]
    score = mean(valid_values) if valid_values else 0.0

    return {
        "score": _clip01(score),
        "components": components,
        "observed_doc_ids": docs,
        "observed_parent_chunk_ids": parents,
        "observed_child_chunk_ids": children,
    }


def compute_faithfulness_proxy(
    *,
    answer: str,
    context_text: str,
    case: RagEvalCase,
) -> Dict[str, Any]:
    """用答案关键词在上下文中的支持比例近似 Faithfulness。

    只评估实际出现在答案中的黄金回答关键词；这些关键词若也出现在上下文中则视为
    supported。该方法不能识别语义矛盾、数值关系或未标注声明。
    """

    # 第一步：统一文本，并优先使用 answer_keywords，缺失时回退 expected_keywords。
    answer = _normalize_text(answer)
    context_text = _normalize_text(context_text)
    keywords = _as_list(
        case.answer_keywords or case.expected_keywords
    )

    # 第二步：任一关键输入缺失时无法建立支持判断。
    if not answer or not context_text or not keywords:
        return {
            "score": 0.0,
            "supported_keywords": [],
            "unsupported_keywords": [],
            "evaluated_keywords": keywords,
            "note": (
                "No answer/context/keywords available for proxy "
                "faithfulness."
            ),
        }

    # 第三步：只把答案实际使用的黄金关键词纳入分母。
    supported: List[str] = []
    unsupported: List[str] = []
    evaluated: List[str] = []
    for keyword in keywords:
        if keyword in answer:
            evaluated.append(keyword)
            if keyword in context_text:
                supported.append(keyword)
            else:
                unsupported.append(keyword)

    # 第四步：答案未包含任何可评关键词时返回零并明确原因。
    if not evaluated:
        return {
            "score": 0.0,
            "supported_keywords": [],
            "unsupported_keywords": [],
            "evaluated_keywords": [],
            "note": "None of the answer keywords appeared in answer.",
        }

    return {
        "score": _clip01(len(supported) / len(evaluated)),
        "supported_keywords": supported,
        "unsupported_keywords": unsupported,
        "evaluated_keywords": evaluated,
    }


def compute_answer_relevancy_proxy(
    *,
    answer: str,
    query: str,
    case: RagEvalCase,
) -> Dict[str, Any]:
    """用黄金关键词覆盖和弱查询锚点近似 Answer Relevancy。

    关键词覆盖占 85%；查询中的任一中文字符出现在答案中可贡献 15% 弱锚点。弱锚点
    避免要求“xxx 是什么”整句原样出现在回答中，但仍只能反映浅层字面相关性。
    """

    # 第一步：统一答案、查询和黄金关键词。
    answer = _normalize_text(answer)
    query = _normalize_text(query)
    keywords = _as_list(
        case.answer_keywords or case.expected_keywords
    )

    # 空答案直接返回零分和分项诊断。
    if not answer:
        return {
            "score": 0.0,
            "answer_keyword_hit": 0.0,
            "non_empty_answer": 0.0,
        }

    # 第二步：有标签时计算字面覆盖；无标签时只认可答案非空。
    if keywords:
        answer_keyword_hit = _keyword_hit_ratio(keywords, answer)
    else:
        answer_keyword_hit = 1.0

    # 第三步：检查查询中的任一中文字符是否出现在答案中，作为弱主题锚点。
    query_anchor = (
        1.0
        if query
        and any(
            character in answer
            for character in query
            if "\u4e00" <= character <= "\u9fff"
        )
        else 0.0
    )

    # 第四步：组合关键词覆盖和弱锚点。
    score = 0.85 * answer_keyword_hit + 0.15 * query_anchor
    return {
        "score": _clip01(score),
        "answer_keyword_hit": _clip01(answer_keyword_hit),
        "query_anchor_hit": query_anchor,
        "evaluated_keywords": keywords,
    }


def compute_citation_hit_proxy(
    run_record: Dict[str, Any],
    case: RagEvalCase,
) -> Dict[str, Any]:
    """判断显式引用或选中上下文是否命中黄金文档。

    优先读取 ``run_record.citations[*].doc_id``。没有显式引用时回退到
    ``context_pack.selected_results`` 的文档 ID，因此该指标在回退模式下只能表示
    “生成上下文包含黄金来源”，不能证明答案真正引用了它。
    """

    # 第一步：没有黄金文档时旧协议无法计算，返回零和空列表。
    expected = set(case.expected_doc_ids)
    if not expected:
        return {
            "score": 0.0,
            "expected_doc_ids": [],
            "cited_doc_ids": [],
        }

    # 第二步：收集显式结构化引用中的文档身份。
    cited: List[str] = []
    for citation in run_record.get("citations") or []:
        if isinstance(citation, dict) and citation.get("doc_id"):
            cited.append(str(citation.get("doc_id")))

    # 第三步：缺少显式引用时，回退到进入最终上下文的文档。
    if not cited:
        context_pack = run_record.get("context_pack") or {}
        for item in context_pack.get("selected_results") or []:
            if isinstance(item, dict) and item.get("doc_id"):
                cited.append(str(item.get("doc_id")))

    # 第四步：二值判断实际来源集合是否与黄金集合相交。
    cited_set = set(cited)
    return {
        "score": 1.0 if expected & cited_set else 0.0,
        "expected_doc_ids": sorted(expected),
        "cited_doc_ids": cited,
    }


def evaluate_ragas_style_proxy(
    *,
    run_record: Dict[str, Any],
    eval_case: RagEvalCase,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """对一条捕获的 RAG 运行计算五项本地代理指标。

    Args:
        run_record: 包含查询、召回结果、上下文、答案和引用的运行记录。
        eval_case: 与运行查询对应的 canonical 黄金样本。
        top_k: 可选指标截断窗口。

    Returns:
        包含稳定 Schema 版本、指标摘要、详细诊断和黄金样本快照的字典。
    """

    # 第一步：读取召回列表；异常类型按空列表处理以形成可诊断零分。
    retrieval_results = run_record.get("retrieval_results") or []
    if not isinstance(retrieval_results, list):
        retrieval_results = []

    # 第二步：运行记录查询优先，缺失时使用黄金样本查询；恢复答案和最终上下文。
    query = str(run_record.get("query") or eval_case.query)
    answer = _normalize_text(run_record.get("answer"))
    context_text = _collect_context_text_from_run(run_record)

    # 第三步：分别计算五个维度及其详细诊断。
    context_precision, rank_details = compute_context_precision_proxy(
        retrieval_results,
        eval_case,
        top_k=top_k,
    )
    context_recall = compute_context_recall_proxy(
        retrieval_results,
        eval_case,
        top_k=top_k,
    )
    faithfulness = compute_faithfulness_proxy(
        answer=answer,
        context_text=context_text,
        case=eval_case,
    )
    answer_relevancy = compute_answer_relevancy_proxy(
        answer=answer,
        query=query,
        case=eval_case,
    )
    citation_hit = compute_citation_hit_proxy(
        run_record,
        eval_case,
    )

    # 第四步：提取顶层标量指标，便于批量聚合和表格展示。
    metrics = {
        "context_precision_proxy": context_precision,
        "context_recall_proxy": context_recall["score"],
        "faithfulness_proxy": faithfulness["score"],
        "answer_relevancy_proxy": answer_relevancy["score"],
        "citation_hit_proxy": citation_hit["score"],
    }

    # 第五步：同时返回排名、召回分项和关键词支持等完整细节。
    return {
        "schema_version": "ragas_style_proxy_eval_v1",
        "case_id": eval_case.sample_id,
        "run_id": run_record.get("run_id"),
        "query": query,
        "top_k": top_k,
        "metrics": metrics,
        "details": {
            "context_precision_rank_details": rank_details,
            "context_recall": context_recall,
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "citation_hit": citation_hit,
        },
        "eval_case": eval_case.model_dump(),
    }


def aggregate_ragas_style_proxy(
    results: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """对逐运行代理结果计算五项宏平均。"""

    # 固定指标顺序形成稳定报告字段。
    metric_names = [
        "context_precision_proxy",
        "context_recall_proxy",
        "faithfulness_proxy",
        "answer_relevancy_proxy",
        "citation_hit_proxy",
    ]
    summary: Dict[str, Any] = {
        "schema_version": "ragas_style_proxy_eval_report_v1",
        "num_samples": len(results),
    }

    # 每个结果缺失或非法指标值按 0.0 处理，以兼容历史部分记录。
    for name in metric_names:
        values = [
            _safe_float(
                (item.get("metrics") or {}).get(name),
                0.0,
            )
            for item in results
        ]
        summary[f"avg_{name}"] = mean(values) if values else 0.0

    return summary


def load_jsonl_records(path: str | Path) -> List[Dict[str, Any]]:
    """从 JSONL 文件读取对象记录。

    Args:
        path: 每个非空行必须为 JSON 对象的文件路径。

    Returns:
        按文件顺序解析的字典列表。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        ValueError: 任一行解析后不是对象时抛出。
        json.JSONDecodeError: 行内容不是合法 JSON 时抛出。
    """

    # 第一步：标准化路径并拒绝不存在的文件。
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {source_path}")

    # 第二步：逐行解析并忽略空行，错误信息保留 1-based 行号。
    records: List[Dict[str, Any]] = []
    with source_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            normalized = line.strip()
            if not normalized:
                continue
            item = json.loads(normalized)
            if not isinstance(item, dict):
                raise ValueError(
                    f"Line {line_number} in {source_path} "
                    "must be a JSON object"
                )
            records.append(item)

    return records


def write_json(
    path: str | Path,
    data: Dict[str, Any],
) -> Path:
    """将代理评测报告写为缩进 JSON。"""

    # 第一步：创建父目录。
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：保留中文并写入便于人工审阅的格式。
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def write_jsonl(
    path: str | Path,
    records: Iterable[Dict[str, Any]],
) -> Path:
    """将逐运行代理结果写为 JSONL。"""

    # 第一步：创建父目录。
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：每条记录独占一行，便于流式汇总和失败筛选。
    with target.open("w", encoding="utf-8") as handle:
        for item in records:
            handle.write(
                json.dumps(item, ensure_ascii=False) + "\n"
            )

    return target
