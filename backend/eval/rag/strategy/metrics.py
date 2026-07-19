# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：MetricContext、MetricDefinition、_results、_top_results、_matches_any、_primary_gold、_result_id、hit_at_k、recall_at_k、reciprocal_rank等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Metric registry for online RAG strategy experiments."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from .schemas import RAGEvalSample


# 阅读注释（类）：封装 指标 上下文，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class MetricContext:
    """封装 指标 上下文，集中封装相关状态、依赖和行为。"""
    sample: RAGEvalSample
    output: dict[str, Any]
    latency_ms: float
    top_k: int


# 阅读注释（类）：封装 指标 definition，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class MetricDefinition:
    """封装 指标 definition，集中封装相关状态、依赖和行为。"""
    name: str
    direction: str
    requires_answer: bool
    compute: Callable[[MetricContext], float | None]
    required_gold: str | None = None


# 阅读注释（函数）：处理 结果集合 相关逻辑。
def _results(context: MetricContext) -> list[dict[str, Any]]:
    """处理 结果集合 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        list[dict[str, Any]]

    阅读提示:
        主要直接调用：context.output.get, isinstance。
    """
    raw = context.output.get("retrieval_results") or []
    return [item for item in raw if isinstance(item, dict)]


# 阅读注释（函数）：处理 top 结果集合 相关逻辑。
def _top_results(context: MetricContext) -> list[dict[str, Any]]:
    """处理 top 结果集合 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        list[dict[str, Any]]

    阅读提示:
        主要直接调用：_results。
    """
    return _results(context)[: context.top_k]


# 阅读注释（函数）：处理 matches any 相关逻辑。
def _matches_any(sample: RAGEvalSample, item: dict[str, Any]) -> bool:
    """处理 matches any 相关逻辑。

    参数:
        sample: sample，具体约束请结合类型标注和调用方确认。
        item: 数据项，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：str, item.get, bool, set。
    """
    doc_id = str(item.get("doc_id") or "")
    parent_id = str(item.get("parent_chunk_id") or "")
    child_id = str(item.get("child_chunk_id") or item.get("chunk_id") or "")
    return bool(
        (sample.expected_doc_ids and doc_id in set(sample.expected_doc_ids))
        or (
            sample.expected_parent_chunk_ids
            and parent_id in set(sample.expected_parent_chunk_ids)
        )
        or (
            sample.expected_child_chunk_ids
            and child_id in set(sample.expected_child_chunk_ids)
        )
    )


# 阅读注释（函数）：处理 primary gold 相关逻辑。
def _primary_gold(sample: RAGEvalSample) -> tuple[str, set[str]]:
    """处理 primary gold 相关逻辑。

    参数:
        sample: sample，具体约束请结合类型标注和调用方确认。

    返回:
        tuple[str, set[str]]

    阅读提示:
        主要直接调用：set。
    """
    if sample.expected_child_chunk_ids:
        return "child", set(sample.expected_child_chunk_ids)
    if sample.expected_parent_chunk_ids:
        return "parent", set(sample.expected_parent_chunk_ids)
    return "doc", set(sample.expected_doc_ids)


# 阅读注释（函数）：处理 结果 标识 相关逻辑。
def _result_id(item: dict[str, Any], level: str) -> str:
    """处理 结果 标识 相关逻辑。

    参数:
        item: 数据项，具体约束请结合类型标注和调用方确认。
        level: level，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：str, item.get。
    """
    if level == "child":
        return str(item.get("child_chunk_id") or item.get("chunk_id") or "")
    if level == "parent":
        return str(item.get("parent_chunk_id") or "")
    return str(item.get("doc_id") or "")


# 阅读注释（函数）：处理 hit at k 相关逻辑。
def hit_at_k(context: MetricContext) -> float:
    """处理 hit at k 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：any, _matches_any, _top_results。
    """
    return 1.0 if any(_matches_any(context.sample, item) for item in _top_results(context)) else 0.0


# 阅读注释（函数）：处理 recall at k 相关逻辑。
def recall_at_k(context: MetricContext) -> float:
    """处理 recall at k 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：_primary_gold, _result_id, _top_results, retrieved.discard, len。
    """
    level, gold = _primary_gold(context.sample)
    if not gold:
        return 0.0
    retrieved = {_result_id(item, level) for item in _top_results(context)}
    retrieved.discard("")
    return len(retrieved & gold) / len(gold)


# 阅读注释（函数）：处理 reciprocal rank 相关逻辑。
def reciprocal_rank(context: MetricContext) -> float:
    """处理 reciprocal rank 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：enumerate, _top_results, _matches_any。
    """
    for rank, item in enumerate(_top_results(context), start=1):
        if _matches_any(context.sample, item):
            return 1.0 / rank
    return 0.0


# 阅读注释（函数）：处理 ndcg at k 相关逻辑。
def ndcg_at_k(context: MetricContext) -> float:
    """处理 ndcg at k 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：_primary_gold, set, enumerate, _top_results, _result_id, seen.add, math.log2, min。
    """
    level, gold = _primary_gold(context.sample)
    if not gold:
        return 0.0
    seen: set[str] = set()
    dcg = 0.0
    for rank, item in enumerate(_top_results(context), start=1):
        candidate_id = _result_id(item, level)
        gain = 1.0 if candidate_id in gold and candidate_id not in seen else 0.0
        if gain:
            seen.add(candidate_id)
            dcg += gain / math.log2(rank + 1)
    ideal_hits = min(len(gold), context.top_k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


# 阅读注释（函数）：处理 上下文 keyword hit 相关逻辑。
def context_keyword_hit(context: MetricContext) -> float:
    """处理 上下文 keyword hit 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：_top_results, item.get, parts.append, str, casefold, join, sum, keyword.casefold。
    """
    keywords = context.sample.expected_keywords
    if not keywords:
        return 0.0
    parts: list[str] = []
    for item in _top_results(context):
        for field in ("text", "parent_text", "child_text"):
            value = item.get(field)
            if value:
                parts.append(str(value))
                break
    joined = "\n".join(parts).casefold()
    return sum(1 for keyword in keywords if keyword.casefold() in joined) / len(keywords)


# 阅读注释（函数）：处理 answer keyword hit 相关逻辑。
def answer_keyword_hit(context: MetricContext) -> float | None:
    """处理 answer keyword hit 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float | None

    阅读提示:
        主要直接调用：casefold, str, context.output.get, sum, keyword.casefold, len。
    """
    keywords = context.sample.answer_keywords
    if not keywords:
        return None
    answer = str(context.output.get("answer") or "").casefold()
    return sum(1 for keyword in keywords if keyword.casefold() in answer) / len(keywords)


# 阅读注释（函数）：处理 引用 count 相关逻辑。
def citation_count(context: MetricContext) -> float:
    """处理 引用 count 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：float, len, context.output.get。
    """
    return float(len(context.output.get("citations") or []))


# 阅读注释（函数）：处理 结果 count 相关逻辑。
def result_count(context: MetricContext) -> float:
    """处理 结果 count 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：float, len, _results。
    """
    return float(len(_results(context)))


# 阅读注释（函数）：处理 latency ms 相关逻辑。
def latency_ms(context: MetricContext) -> float:
    """处理 latency ms 相关逻辑。

    参数:
        context: 当前执行上下文。

    返回:
        float

    阅读提示:
        主要直接调用：float。
    """
    return float(context.latency_ms)


# 阅读注释（类）：封装 指标 注册表，集中封装相关状态、依赖和行为。
class MetricRegistry:
    """封装 指标 注册表，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 MetricRegistry，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 MetricRegistry，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self._items: dict[str, MetricDefinition] = {}

    # 阅读注释（函数）：注册 MetricRegistry。
    def register(self, definition: MetricDefinition) -> None:
        """注册 MetricRegistry。

        参数:
            definition: definition，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError。
        """
        if definition.name in self._items:
            raise ValueError(f"duplicate metric: {definition.name}")
        if definition.direction not in {"maximize", "minimize", "neutral"}:
            raise ValueError(f"invalid metric direction: {definition.direction}")
        self._items[definition.name] = definition

    # 阅读注释（函数）：处理 require 相关逻辑。
    def require(self, names: list[str]) -> list[MetricDefinition]:
        """处理 require 相关逻辑。

        参数:
            names: names，具体约束请结合类型标注和调用方确认。

        返回:
            list[MetricDefinition]

        阅读提示:
            主要直接调用：ValueError, join。
        """
        missing = [name for name in names if name not in self._items]
        if missing:
            raise ValueError(f"unknown metrics: {', '.join(missing)}")
        return [self._items[name] for name in names]

    # 阅读注释（函数）：处理 directions 相关逻辑。
    def directions(self, names: list[str]) -> dict[str, str]:
        """处理 directions 相关逻辑。

        参数:
            names: names，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, str]

        阅读提示:
            主要直接调用：self.require。
        """
        return {item.name: item.direction for item in self.require(names)}

    # 阅读注释（函数）：校验 samples。
    def validate_samples(self, names: list[str], samples: list[RAGEvalSample]) -> None:
        """校验 samples。

        参数:
            names: names，具体约束请结合类型标注和调用方确认。
            samples: samples，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：self.require, join, len, errors.append, ValueError。
        """
        definitions = self.require(names)
        errors: list[str] = []
        for definition in definitions:
            if definition.required_gold == "retrieval_ids":
                missing = [
                    sample.sample_id
                    for sample in samples
                    if not (
                        sample.expected_doc_ids
                        or sample.expected_parent_chunk_ids
                        or sample.expected_child_chunk_ids
                    )
                ]
            elif definition.required_gold == "expected_keywords":
                missing = [
                    sample.sample_id for sample in samples if not sample.expected_keywords
                ]
            elif definition.required_gold == "answer_keywords":
                missing = [
                    sample.sample_id for sample in samples if not sample.answer_keywords
                ]
            else:
                missing = []
            if missing:
                preview = ", ".join(missing[:5])
                suffix = "..." if len(missing) > 5 else ""
                errors.append(
                    f"metric {definition.name} missing {definition.required_gold} "
                    f"for samples: {preview}{suffix}"
                )
        if errors:
            raise ValueError("; ".join(errors))


# 阅读注释（函数）：构建 default 指标 注册表。
def build_default_metric_registry() -> MetricRegistry:
    """构建 default 指标 注册表。

    返回:
        MetricRegistry

    阅读提示:
        主要直接调用：MetricRegistry, MetricDefinition, registry.register。
    """
    registry = MetricRegistry()
    for definition in (
        MetricDefinition("hit_at_k", "maximize", False, hit_at_k, "retrieval_ids"),
        MetricDefinition("recall_at_k", "maximize", False, recall_at_k, "retrieval_ids"),
        MetricDefinition("mrr", "maximize", False, reciprocal_rank, "retrieval_ids"),
        MetricDefinition("ndcg_at_k", "maximize", False, ndcg_at_k, "retrieval_ids"),
        MetricDefinition(
            "context_keyword_hit", "maximize", False, context_keyword_hit, "expected_keywords"
        ),
        MetricDefinition(
            "answer_keyword_hit", "maximize", True, answer_keyword_hit, "answer_keywords"
        ),
        MetricDefinition("citation_count", "neutral", True, citation_count),
        MetricDefinition("result_count", "neutral", False, result_count),
        MetricDefinition("latency_ms", "minimize", False, latency_ms),
    ):
        registry.register(definition)
    return registry
