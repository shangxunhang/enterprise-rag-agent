"""Metric registry for online RAG strategy experiments."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from .schemas import RAGEvalSample


@dataclass(frozen=True)
class MetricContext:
    sample: RAGEvalSample
    output: dict[str, Any]
    latency_ms: float
    top_k: int


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    direction: str
    requires_answer: bool
    compute: Callable[[MetricContext], float | None]
    required_gold: str | None = None


def _results(context: MetricContext) -> list[dict[str, Any]]:
    raw = context.output.get("retrieval_results") or []
    return [item for item in raw if isinstance(item, dict)]


def _top_results(context: MetricContext) -> list[dict[str, Any]]:
    return _results(context)[: context.top_k]


def _matches_any(sample: RAGEvalSample, item: dict[str, Any]) -> bool:
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


def _primary_gold(sample: RAGEvalSample) -> tuple[str, set[str]]:
    if sample.expected_child_chunk_ids:
        return "child", set(sample.expected_child_chunk_ids)
    if sample.expected_parent_chunk_ids:
        return "parent", set(sample.expected_parent_chunk_ids)
    return "doc", set(sample.expected_doc_ids)


def _result_id(item: dict[str, Any], level: str) -> str:
    if level == "child":
        return str(item.get("child_chunk_id") or item.get("chunk_id") or "")
    if level == "parent":
        return str(item.get("parent_chunk_id") or "")
    return str(item.get("doc_id") or "")


def hit_at_k(context: MetricContext) -> float:
    return 1.0 if any(_matches_any(context.sample, item) for item in _top_results(context)) else 0.0


def recall_at_k(context: MetricContext) -> float:
    level, gold = _primary_gold(context.sample)
    if not gold:
        return 0.0
    retrieved = {_result_id(item, level) for item in _top_results(context)}
    retrieved.discard("")
    return len(retrieved & gold) / len(gold)


def reciprocal_rank(context: MetricContext) -> float:
    for rank, item in enumerate(_top_results(context), start=1):
        if _matches_any(context.sample, item):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(context: MetricContext) -> float:
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


def context_keyword_hit(context: MetricContext) -> float:
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


def answer_keyword_hit(context: MetricContext) -> float | None:
    keywords = context.sample.answer_keywords
    if not keywords:
        return None
    answer = str(context.output.get("answer") or "").casefold()
    return sum(1 for keyword in keywords if keyword.casefold() in answer) / len(keywords)


def citation_count(context: MetricContext) -> float:
    return float(len(context.output.get("citations") or []))


def result_count(context: MetricContext) -> float:
    return float(len(_results(context)))


def latency_ms(context: MetricContext) -> float:
    return float(context.latency_ms)


class MetricRegistry:
    def __init__(self) -> None:
        self._items: dict[str, MetricDefinition] = {}

    def register(self, definition: MetricDefinition) -> None:
        if definition.name in self._items:
            raise ValueError(f"duplicate metric: {definition.name}")
        if definition.direction not in {"maximize", "minimize", "neutral"}:
            raise ValueError(f"invalid metric direction: {definition.direction}")
        self._items[definition.name] = definition

    def require(self, names: list[str]) -> list[MetricDefinition]:
        missing = [name for name in names if name not in self._items]
        if missing:
            raise ValueError(f"unknown metrics: {', '.join(missing)}")
        return [self._items[name] for name in names]

    def directions(self, names: list[str]) -> dict[str, str]:
        return {item.name: item.direction for item in self.require(names)}

    def validate_samples(self, names: list[str], samples: list[RAGEvalSample]) -> None:
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


def build_default_metric_registry() -> MetricRegistry:
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
