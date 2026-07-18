# -*- coding: utf-8 -*-
"""
rag_template/judge/adaptive_rag_router.py
=========================================

Adaptive-RAG-lite strategy router for the pre-enterprise-migration RAG project.

This is intentionally a lightweight router, not a full paper reproduction:
- It chooses an effective retrieval / quality-control strategy for one query.
- It supports optional LLM routing and deterministic fallback.
- It records stable metadata that can later be converted into Controller-model
  training samples: <TASK=ADAPTIVE_ROUTE>.

The enterprise architecture can later move this into backend/rag/strategies or
backend/rag/routing as a first-class RAG strategy router.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from rag.ports.generation import TextGenerator


_ALLOWED_STRATEGIES = {
    "hybrid",
    "rag_fusion",
    "hyde",
    "rag_fusion_hyde",
    "c_rag",
    "self_rag",
    "c_rag_self_rag",
}

_SHORT_QUERY_MAX_CHARS = 16


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    text = _safe_str(raw_text).strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_strategy(value: Any, default: str = "hybrid") -> str:
    strategy = _safe_str(value).strip().lower().replace("-", "_")
    aliases = {
        "default": "hybrid",
        "basic": "hybrid",
        "parent_child_hybrid": "hybrid",
        "fusion": "rag_fusion",
        "crag": "c_rag",
        "corrective_rag": "c_rag",
        "selfrag": "self_rag",
        "crag_self_rag": "c_rag_self_rag",
        "corrective_self_rag": "c_rag_self_rag",
    }
    strategy = aliases.get(strategy, strategy)
    return strategy if strategy in _ALLOWED_STRATEGIES else default


def _contains_any(text: str, terms: Iterable[str]) -> List[str]:
    lowered = _safe_str(text).lower()
    found: List[str] = []
    for term in terms:
        term_s = _safe_str(term).strip()
        if not term_s:
            continue
        if term_s.lower() in lowered:
            found.append(term_s)
    return found


@dataclass
class AdaptiveRAGDecision:
    enabled: bool = True
    method: str = "deterministic_fallback"
    selected_strategy: str = "hybrid"
    original_strategy: str = "adaptive_rag"
    candidate_strategies: List[str] = field(default_factory=lambda: [
        "hybrid",
        "rag_fusion",
        "hyde",
        "rag_fusion_hyde",
        "c_rag",
        "self_rag",
        "c_rag_self_rag",
    ])
    enable_hyde: bool = False
    enable_crag: bool = False
    enable_self_rag: bool = False
    confidence: float = 0.6
    reason: str = ""
    signals: Dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[int] = None
    raw_output: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": self.method,
            "selected_strategy": self.selected_strategy,
            "original_strategy": self.original_strategy,
            "candidate_strategies": self.candidate_strategies,
            "enable_hyde": self.enable_hyde,
            "enable_crag": self.enable_crag,
            "enable_self_rag": self.enable_self_rag,
            "confidence": self.confidence,
            "reason": self.reason,
            "signals": self.signals,
            "latency_ms": self.latency_ms,
            "raw_output": self.raw_output,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "metadata": self.metadata,
        }


class AdaptiveRAGRouter:
    """Adaptive-RAG-lite router.

    The first version uses deterministic rules that are easy to debug. If an LLM
    is configured, it can ask the controller model to select a strategy, but the
    deterministic router is always available as fallback.
    """

    ADAPTIVE_STRATEGIES = {"adaptive", "adaptive_rag", "adaptive-rag", "adaptive_rag_lite"}

    def __init__(
        self,
        *,
        llm_generator: Optional[TextGenerator] = None,
        use_llm: bool = False,
        generation_params: Optional[Dict[str, Any]] = None,
        fallback_to_deterministic: bool = True,
        timer: Timer | None = None,
    ):
        self.llm_generator = llm_generator
        self.use_llm = bool(use_llm)
        self.generation_params = dict(generation_params or {})
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.timer = timer or MonotonicTimer()

    @classmethod
    def is_adaptive_strategy(cls, strategy: Any) -> bool:
        normalized = _safe_str(strategy).strip().lower().replace("-", "_")
        return normalized in {x.replace("-", "_") for x in cls.ADAPTIVE_STRATEGIES}

    def route(
        self,
        *,
        query: str,
        task_type: Optional[str] = None,
        candidate_strategies: Optional[List[str]] = None,
    ) -> AdaptiveRAGDecision:
        t0 = self.timer.now()
        query = _safe_str(query).strip()
        candidates = candidate_strategies or [
            "hybrid",
            "rag_fusion",
            "hyde",
            "rag_fusion_hyde",
            "c_rag",
            "self_rag",
            "c_rag_self_rag",
        ]

        if self._llm_available():
            try:
                decision = self._llm_route(query=query, task_type=task_type, candidate_strategies=candidates)
                decision.latency_ms = elapsed_ms(self.timer, t0)
                return decision
            except Exception as exc:
                if not self.fallback_to_deterministic:
                    raise
                decision = self._deterministic_route(
                    query=query,
                    task_type=task_type,
                    candidate_strategies=candidates,
                )
                decision.fallback_used = True
                decision.fallback_reason = f"{exc.__class__.__name__}: {exc}"
                decision.latency_ms = elapsed_ms(self.timer, t0)
                return decision

        decision = self._deterministic_route(
            query=query,
            task_type=task_type,
            candidate_strategies=candidates,
        )
        decision.latency_ms = elapsed_ms(self.timer, t0)
        return decision

    def _llm_available(self) -> bool:
        return bool(self.use_llm and self.llm_generator is not None)

    def _call_llm(self, prompt: str, *, system_prompt: str, max_new_tokens: int = 256) -> str:
        if self.llm_generator is None:
            raise RuntimeError("llm_generator is not configured")
        params = dict(self.generation_params)
        params.pop("rewrite_max_new_tokens", None)
        params.pop("hyde_max_new_tokens", None)
        params.setdefault("max_new_tokens", max_new_tokens)
        params.setdefault("temperature", 0.0)
        params.setdefault("top_p", 0.9)
        params.setdefault("do_sample", False)
        try:
            text = self.llm_generator.generate(prompt, system_prompt=system_prompt, **params)
        except TypeError:
            text = self.llm_generator.generate(f"{system_prompt}\n\n{prompt}", **params)
        return _safe_str(text).strip()

    def _llm_route(
        self,
        *,
        query: str,
        task_type: Optional[str],
        candidate_strategies: List[str],
    ) -> AdaptiveRAGDecision:
        system_prompt = "你是企业级 Adaptive-RAG 策略路由器。只输出合法 JSON，不要解释。"
        prompt = (
            "请根据用户问题选择最合适的 RAG 策略。\n"
            "候选策略说明：\n"
            "- hybrid: 默认低开销检索，适合明确问题。\n"
            "- rag_fusion: 短问题、模糊问题、同义表达多时使用。\n"
            "- hyde: 抽象概念、语义检索困难时使用，但开销较高。\n"
            "- rag_fusion_hyde: 复杂长尾召回不足时使用，开销最高。\n"
            "- c_rag: 需要高上下文精度、担心检索噪声时使用。\n"
            "- self_rag: 需要最终答案忠实性检查时使用。\n"
            "- c_rag_self_rag: 高价值生成任务，既要过滤上下文又要检查答案时使用。\n\n"
            "输出 JSON 字段：selected_strategy, enable_hyde, enable_crag, enable_self_rag, confidence, reason。\n"
            "要求：如果问题明确且较长，优先 hybrid；如果用户要求根据资料生成正式方案，优先 c_rag_self_rag；"
            "不要为了复杂而默认选择高开销策略。\n\n"
            f"候选策略：{candidate_strategies}\n"
            f"任务类型：{task_type or ''}\n"
            f"用户问题：{query}"
        )
        raw = self._call_llm(prompt, system_prompt=system_prompt, max_new_tokens=256)
        obj = _extract_json_object(raw)
        if not obj:
            raise ValueError("LLM Adaptive-RAG router did not return JSON")
        selected = _normalize_strategy(obj.get("selected_strategy"), "hybrid")
        decision = AdaptiveRAGDecision(
            enabled=True,
            method="llm",
            selected_strategy=selected,
            candidate_strategies=candidate_strategies,
            enable_hyde=bool(obj.get("enable_hyde")) or selected in {"hyde", "rag_fusion_hyde"},
            enable_crag=bool(obj.get("enable_crag")) or selected in {"c_rag", "c_rag_self_rag"},
            enable_self_rag=bool(obj.get("enable_self_rag")) or selected in {"self_rag", "c_rag_self_rag"},
            confidence=max(0.0, min(1.0, _safe_float(obj.get("confidence"), 0.65))),
            reason=_safe_str(obj.get("reason") or "LLM adaptive routing decision"),
            raw_output=raw,
            metadata={"router": self.__class__.__name__, "llm_enabled": True},
        )
        decision.signals = self._signals(query=query, task_type=task_type)
        return decision

    def _deterministic_route(
        self,
        *,
        query: str,
        task_type: Optional[str],
        candidate_strategies: List[str],
    ) -> AdaptiveRAGDecision:
        signals = self._signals(query=query, task_type=task_type)
        selected = "hybrid"
        enable_hyde = False
        enable_crag = False
        enable_self_rag = False
        confidence = 0.62
        reason = "query is clear enough; use low-cost hybrid retrieval"

        if signals["high_grounding_required"] or signals["formal_generation_task"]:
            selected = "c_rag_self_rag"
            enable_crag = True
            enable_self_rag = True
            confidence = 0.78
            reason = "formal/grounded generation task; filter noisy context and check answer faithfulness"
        elif signals["short_or_ambiguous"]:
            selected = "rag_fusion"
            confidence = 0.72
            reason = "query is short or ambiguous; use query rewriting to improve recall"
        elif signals["abstract_semantic_query"]:
            selected = "hyde"
            enable_hyde = True
            confidence = 0.68
            reason = "query is abstract/semantic; use HyDE to enrich retrieval representation"
        elif signals["multi_hop_like"]:
            selected = "c_rag"
            enable_crag = True
            confidence = 0.66
            reason = "query appears multi-entity or relation-oriented; use retrieval quality control before generation"
        elif signals["noise_sensitive"]:
            selected = "c_rag"
            enable_crag = True
            confidence = 0.70
            reason = "query is noise-sensitive; use C-RAG to downrank off-topic chunks"

        if selected not in set(candidate_strategies):
            selected = "hybrid"
            enable_hyde = False
            enable_crag = False
            enable_self_rag = False
            confidence = 0.55
            reason = "selected strategy unavailable in candidates; fallback to hybrid"

        # Make flags consistent with selected strategy.
        enable_hyde = enable_hyde or selected in {"hyde", "rag_fusion_hyde"}
        enable_crag = enable_crag or selected in {"c_rag", "c_rag_self_rag"}
        enable_self_rag = enable_self_rag or selected in {"self_rag", "c_rag_self_rag"}

        return AdaptiveRAGDecision(
            enabled=True,
            method="deterministic_fallback",
            selected_strategy=selected,
            candidate_strategies=candidate_strategies,
            enable_hyde=enable_hyde,
            enable_crag=enable_crag,
            enable_self_rag=enable_self_rag,
            confidence=confidence,
            reason=reason,
            signals=signals,
            metadata={"router": self.__class__.__name__, "llm_enabled": False},
        )

    @staticmethod
    def _signals(*, query: str, task_type: Optional[str] = None) -> Dict[str, Any]:
        q = _safe_str(query).strip()
        q_lower = q.lower()
        compact_len = len(re.sub(r"\s+", "", q))
        task_type = _safe_str(task_type).strip().lower()

        grounding_terms = ["根据资料", "依据资料", "引用", "证据", "不能编造", "忠实", "溯源", "来源"]
        formal_generation_terms = ["生成", "建设方案", "概要设计", "详细设计", "报告", "方案", "规划", "文档"]
        abstract_terms = ["原理", "机制", "本质", "为什么", "如何设计", "架构", "思路", "方法论"]
        multi_hop_terms = ["比较", "关系", "依赖", "影响", "因果", "先后", "结合", "分别", "同时", "多跳"]
        ambiguous_terms = ["这个", "那个", "这些", "怎么搞", "咋办", "有用吗"]
        noise_terms = ["只根据", "不要跑题", "过滤", "偏题", "错误资料", "上下文"]

        found_grounding = _contains_any(q, grounding_terms)
        found_formal = _contains_any(q, formal_generation_terms)
        found_abstract = _contains_any(q, abstract_terms)
        found_multi = _contains_any(q, multi_hop_terms)
        found_ambiguous = _contains_any(q, ambiguous_terms)
        found_noise = _contains_any(q, noise_terms)

        return {
            "query_length_chars": compact_len,
            "task_type": task_type,
            "short_or_ambiguous": compact_len <= _SHORT_QUERY_MAX_CHARS or bool(found_ambiguous),
            "high_grounding_required": bool(found_grounding),
            "formal_generation_task": bool(found_formal) or task_type in {"scheme_generation", "report_generation"},
            "abstract_semantic_query": bool(found_abstract),
            "multi_hop_like": bool(found_multi),
            "noise_sensitive": bool(found_noise),
            "matched_terms": {
                "grounding": found_grounding,
                "formal_generation": found_formal,
                "abstract": found_abstract,
                "multi_hop": found_multi,
                "ambiguous": found_ambiguous,
                "noise": found_noise,
            },
        }
