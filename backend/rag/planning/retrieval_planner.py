"""Adaptive intent policy for one bounded retrieval request."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

QueryTransformMode = Literal["identity", "multi_query", "hyde"]


def _contains(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    matches: list[str] = []
    for term in terms:
        normalized = term.lower()
        matched = (
            re.search(rf"\b{re.escape(normalized)}\b", lowered) is not None
            if normalized.isascii() and normalized.isalnum()
            else normalized in lowered
        )
        if matched:
            matches.append(term)
    return matches


@dataclass(frozen=True)
class RetrievalPlan:
    """Request plan; it never predicts whether evidence correction is needed."""

    plan_id: str
    query_transform_mode: QueryTransformMode = "identity"
    correction_budget: int = 1
    reason: str = "baseline retrieval"
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "rag_request_plan_v2",
            "plan_id": self.plan_id,
            "query_transform_mode": self.query_transform_mode,
            "correction_budget": max(0, int(self.correction_budget)),
            "reason": self.reason,
            "signals": dict(self.signals),
        }


@runtime_checkable
class RetrievalPlannerPort(Protocol):
    def plan(
        self,
        *,
        query: str,
        request_context: dict[str, Any] | None = None,
    ) -> RetrievalPlan: ...


class AdaptiveRetrievalPlanner:
    """Choose one query transform; correction is decided after retrieval."""

    _AMBIGUOUS = ("这个", "那个", "这些", "怎么弄", "怎么办", "this", "it")
    _ABSTRACT = (
        "原理",
        "机制",
        "本质",
        "为什么",
        "架构",
        "思路",
        "architecture",
        "mechanism",
        "why",
    )
    _MULTI_ASPECT = (
        "比较",
        "分别",
        "同时",
        "以及",
        "优缺点",
        "compare",
        "versus",
        " and ",
    )
    _FORMAL = (
        "方案",
        "报告",
        "规划",
        "设计文档",
        "scheme_generation",
        "report_generation",
    )

    def __init__(
        self,
        *,
        short_query_max_chars: int = 16,
        correction_budget: int = 1,
    ) -> None:
        self.short_query_max_chars = max(1, int(short_query_max_chars))
        self.correction_budget = max(0, min(3, int(correction_budget)))

    def plan(
        self,
        *,
        query: str,
        request_context: dict[str, Any] | None = None,
    ) -> RetrievalPlan:
        text = str(query or "").strip()
        if not text:
            raise ValueError("query cannot be empty")
        context = dict(request_context or {})
        task_type = str(context.get("task_type") or "").strip().lower()
        compact_length = len(re.sub(r"\s+", "", text))
        matched = {
            "ambiguous": _contains(text, self._AMBIGUOUS),
            "abstract": _contains(text, self._ABSTRACT),
            "multi_aspect": _contains(text, self._MULTI_ASPECT),
            "formal": _contains(f"{text} {task_type}", self._FORMAL),
        }
        short_or_ambiguous = (
            compact_length <= self.short_query_max_chars
            or bool(matched["ambiguous"])
        )
        if short_or_ambiguous or matched["multi_aspect"] or matched["formal"]:
            mode: QueryTransformMode = "multi_query"
            reason = "query needs recall expansion"
        elif matched["abstract"]:
            mode = "hyde"
            reason = "abstract intent benefits from semantic expansion"
        else:
            mode = "identity"
            reason = "original query is sufficiently specific"

        overrides = dict(context.get("retrieval_plan_overrides") or {})
        override_mode = overrides.get("query_transform_mode")
        if override_mode is not None:
            if override_mode not in {"identity", "multi_query", "hyde"}:
                raise ValueError(
                    "query_transform_mode override must be identity, multi_query or hyde"
                )
            mode = override_mode
            reason = "request override selected query transform mode"
        budget = int(overrides.get("correction_budget", self.correction_budget))
        budget = max(0, min(3, budget))
        return RetrievalPlan(
            plan_id=f"{mode}:correction_budget={budget}",
            query_transform_mode=mode,
            correction_budget=budget,
            reason=reason,
            signals={
                "query_length_chars": compact_length,
                "task_type": task_type,
                "short_or_ambiguous": short_or_ambiguous,
                "matched_terms": matched,
            },
        )
