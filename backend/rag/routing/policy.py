"""Explainable, deterministic Adaptive Profile routing policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re

from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from rag.routing.schema import RoutingConditionConfig, RoutingRuleConfig


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(text: str, terms: list[str]) -> list[str]:
    lowered = _safe_str(text).lower()
    return [term for term in terms if term and term.lower() in lowered]


@dataclass
class AdaptiveProfileDecision:
    router_id: str
    router_version: str
    method: str
    selected_profile_id: str
    matched_rule_id: str | None
    reason: str
    signals: dict[str, Any]
    candidate_profile_ids: list[str]
    fallback_used: bool = False
    fallback_reason: str | None = None
    latency_ms: int | None = None
    route_config_file: str | None = None
    route_config_hash: str | None = None
    selected_profile_path: str | None = None
    selected_profile_hash: str | None = None
    selected_profile_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "adaptive_profile_decision_v1",
            "router_id": self.router_id,
            "router_version": self.router_version,
            "method": self.method,
            "selected_profile_id": self.selected_profile_id,
            "matched_rule_id": self.matched_rule_id,
            "reason": self.reason,
            "signals": self.signals,
            "candidate_profile_ids": self.candidate_profile_ids,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "latency_ms": self.latency_ms,
            "route_config_file": self.route_config_file,
            "route_config_hash": self.route_config_hash,
            "selected_profile_path": self.selected_profile_path,
            "selected_profile_hash": self.selected_profile_hash,
            "selected_profile_version": self.selected_profile_version,
            "metadata": self.metadata,
        }


class ExplainableRuleProfileRouterPlugin:
    """Match generic configured conditions against deterministic request signals."""

    def __init__(
        self,
        *,
        build_context: dict[str, Any],
        short_query_max_chars: int = 16,
        formal_task_types: list[str] | None = None,
        timer: Timer | None = None,
    ) -> None:
        self.router_id = str(build_context["router_id"])
        self.router_version = str(build_context["router_version"])
        self.default_profile_id = str(build_context["default_profile_id"])
        self.profile_ids = list(build_context["profile_ids"])
        self.rules: list[RoutingRuleConfig] = sorted(
            list(build_context["rules"]),
            key=lambda item: (-int(item.priority), item.rule_id),
        )
        self.short_query_max_chars = max(1, int(short_query_max_chars))
        self.formal_task_types = {
            str(item).strip().lower()
            for item in (formal_task_types or ["scheme_generation", "report_generation"])
            if str(item).strip()
        }
        self.timer = timer or MonotonicTimer()

    def route(
        self,
        *,
        query: str,
        request_context: dict[str, Any] | None = None,
        requested_profile_id: str | None = None,
    ) -> AdaptiveProfileDecision:
        started = self.timer.now()
        context = dict(request_context or {})
        signals = self._signals(query=query, request_context=context)
        explicit = _safe_str(requested_profile_id)
        if explicit:
            if explicit in self.profile_ids:
                decision = AdaptiveProfileDecision(
                    router_id=self.router_id,
                    router_version=self.router_version,
                    method="explicit_profile_request",
                    selected_profile_id=explicit,
                    matched_rule_id="explicit_profile_request",
                    reason="caller explicitly requested an allowed profile",
                    signals=signals,
                    candidate_profile_ids=list(self.profile_ids),
                )
            else:
                decision = AdaptiveProfileDecision(
                    router_id=self.router_id,
                    router_version=self.router_version,
                    method="configured_rules",
                    selected_profile_id=self.default_profile_id,
                    matched_rule_id=None,
                    reason="requested profile is not allowed; use configured default",
                    signals=signals,
                    candidate_profile_ids=list(self.profile_ids),
                    fallback_used=True,
                    fallback_reason=f"unknown requested_profile_id: {explicit}",
                )
            decision.latency_ms = elapsed_ms(self.timer, started)
            return decision

        for rule in self.rules:
            if self._matches(rule, signals):
                decision = AdaptiveProfileDecision(
                    router_id=self.router_id,
                    router_version=self.router_version,
                    method="configured_rules",
                    selected_profile_id=rule.profile_id,
                    matched_rule_id=rule.rule_id,
                    reason=rule.reason,
                    signals=signals,
                    candidate_profile_ids=list(self.profile_ids),
                    latency_ms=elapsed_ms(self.timer, started),
                )
                return decision

        return AdaptiveProfileDecision(
            router_id=self.router_id,
            router_version=self.router_version,
            method="configured_default",
            selected_profile_id=self.default_profile_id,
            matched_rule_id=None,
            reason="no configured rule matched; use default profile",
            signals=signals,
            candidate_profile_ids=list(self.profile_ids),
            latency_ms=elapsed_ms(self.timer, started),
        )

    @staticmethod
    def _condition_value(signals: dict[str, Any], field: str) -> Any:
        current: Any = signals
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @classmethod
    def _condition_matches(
        cls,
        condition: RoutingConditionConfig,
        signals: dict[str, Any],
    ) -> bool:
        actual = cls._condition_value(signals, condition.field)
        expected = condition.value
        op = condition.operator
        if op == "truthy":
            return bool(actual)
        if op == "falsy":
            return not bool(actual)
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gte":
            try:
                return float(actual) >= float(expected)
            except (TypeError, ValueError):
                return False
        if op == "lte":
            try:
                return float(actual) <= float(expected)
            except (TypeError, ValueError):
                return False
        if op == "in":
            return actual in list(expected or [])
        if op == "contains_any":
            actual_values = actual if isinstance(actual, list) else [actual]
            expected_values = list(expected or [])
            return bool(set(actual_values) & set(expected_values))
        raise ValueError(f"unsupported routing condition operator: {op}")

    @classmethod
    def _matches(cls, rule: RoutingRuleConfig, signals: dict[str, Any]) -> bool:
        all_match = all(
            cls._condition_matches(item, signals) for item in rule.all_conditions
        )
        any_match = (
            True
            if not rule.any_conditions
            else any(cls._condition_matches(item, signals) for item in rule.any_conditions)
        )
        return all_match and any_match

    def _signals(
        self,
        *,
        query: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        q = _safe_str(query)
        compact_len = len(re.sub(r"\s+", "", q))
        task_type = _safe_str(request_context.get("task_type")).lower()
        required_sections = list(request_context.get("required_sections") or [])
        citation_sections = list(
            request_context.get("citation_required_sections") or []
        )
        need_citation = bool(request_context.get("need_citation", False))

        grounding_terms = ["根据资料", "依据资料", "引用", "证据", "不能编造", "溯源", "来源"]
        formal_terms = ["生成", "建设方案", "报告", "规划", "文档", "标书", "可研"]
        abstract_terms = ["原理", "机制", "本质", "为什么", "如何设计", "架构", "方法论"]
        multi_terms = ["比较", "关系", "依赖", "影响", "结合", "分别", "同时", "多个", "各章节"]
        ambiguous_terms = ["这个", "那个", "这些", "怎么搞", "咋办", "有用吗"]
        high_risk_terms = ["安全", "合规", "法律", "财务", "政策", "审计", "验收"]

        matched = {
            "grounding": _contains_any(q, grounding_terms),
            "formal": _contains_any(q, formal_terms),
            "abstract": _contains_any(q, abstract_terms),
            "multi_aspect": _contains_any(q, multi_terms),
            "ambiguous": _contains_any(q, ambiguous_terms),
            "high_risk": _contains_any(q, high_risk_terms),
        }
        formal_generation = bool(matched["formal"]) or task_type in self.formal_task_types
        high_grounding = bool(matched["grounding"]) or bool(citation_sections) or need_citation
        multi_aspect = bool(matched["multi_aspect"]) or len(required_sections) >= 4
        return {
            "query_length_chars": compact_len,
            "task_type": task_type,
            "required_section_count": len(required_sections),
            "citation_required_section_count": len(citation_sections),
            "need_citation": need_citation,
            "formal_generation_task": formal_generation,
            "high_grounding_required": high_grounding,
            "multi_aspect_task": multi_aspect,
            "abstract_semantic_query": bool(matched["abstract"]),
            "short_or_ambiguous": (
                compact_len <= self.short_query_max_chars or bool(matched["ambiguous"])
            ),
            "high_risk_task": bool(matched["high_risk"]),
            "matched_terms": matched,
            # Explicitly record that uncalibrated post-retrieval scores are not
            # available to or consumed by the pre-retrieval router.
            "uses_retrieval_confidence": False,
            "uses_quality_threshold": False,
        }
