"""Self-RAG-lite generation support checker.

This module owns post-generation answer relevance and evidence-support checks.
Retrieval evidence quality remains the separate responsibility of
``rag_quality_judge.RagQualityJudge``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from core.runtime.execution_control import WorkflowExecutionCancelled
from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from model_gateway.call_boundary import ModelCallBudgetExceeded

if TYPE_CHECKING:
    from rag.ports.generation import TextGenerator


_STOPWORDS = {
    "根据",
    "资料",
    "生成",
    "一个",
    "这个",
    "那个",
    "什么",
    "如何",
    "怎么",
    "进行",
    "相关",
    "系统",
    "方案",
    "建设",
    "企业级",
}


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except Exception:
        return default


def _clip(text: str, limit: int) -> str:
    value = _safe_str(text).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "...[TRUNCATED]"


def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    text = _safe_str(raw_text).strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _normalize_label(
    value: Any,
    allowed: Iterable[str],
    default: str,
) -> str:
    text = _safe_str(value).strip().lower()
    return text if text in set(allowed) else default


def _tokenize(text: str) -> List[str]:
    value = _safe_str(text).lower()
    tokens: List[str] = []
    tokens.extend(re.findall(r"[a-z0-9][a-z0-9_\-.]{1,}", value))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if len(run) <= 4:
            tokens.append(run)
            continue
        for width in (2, 3, 4):
            for index in range(0, max(0, len(run) - width + 1)):
                tokens.append(run[index : index + width])

    cleaned: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = token.strip().lower()
        if (
            not normalized
            or normalized in _STOPWORDS
            or len(normalized) < 2
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _keyword_overlap_score(
    query: str,
    text: str,
) -> Tuple[float, List[str]]:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0, []
    lowered = _safe_str(text).lower()
    matched = [token for token in query_tokens if token in lowered]
    return len(matched) / max(1, len(query_tokens)), matched


def _noise_terms_in_text(
    text: str,
    *,
    absent_from_query: str = "",
    noise_terms: Iterable[str] = (),
) -> List[str]:
    lowered = _safe_str(text).lower()
    query_lowered = _safe_str(absent_from_query).lower()
    return sorted(
        term
        for term in noise_terms
        if term.lower() in lowered and term.lower() not in query_lowered
    )


@dataclass
class SelfRAGJudgeResult:
    """Stable result consumed by the generation checker plugin and workflow."""

    enabled: bool
    method: str = "disabled"
    is_supported: bool = False
    faithfulness_label: str = "unknown"
    answer_relevance_label: str = "unknown"
    need_rewrite: bool = False
    need_retrieve_more: bool = False
    unsupported_claims: List[Dict[str, Any]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    score: float = 0.0
    latency_ms: Optional[int] = None
    raw_output: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": self.method,
            "is_supported": self.is_supported,
            "faithfulness_label": self.faithfulness_label,
            "answer_relevance_label": self.answer_relevance_label,
            "need_rewrite": self.need_rewrite,
            "need_retrieve_more": self.need_retrieve_more,
            "unsupported_claims": self.unsupported_claims,
            "problems": self.problems,
            "score": self.score,
            "latency_ms": self.latency_ms,
            "raw_output": self.raw_output,
            "metadata": self.metadata,
        }


class SelfRAGJudge:
    """Check whether generated text answers the query and is supported by context."""

    def __init__(
        self,
        *,
        llm_generator: Optional[TextGenerator] = None,
        use_llm: bool = False,
        generation_params: Optional[Dict[str, Any]] = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Optional[Iterable[str]] = None,
        timer: Timer | None = None,
    ) -> None:
        self.llm_generator = llm_generator
        self.use_llm = bool(use_llm)
        self.generation_params = dict(generation_params or {})
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.noise_terms = tuple(noise_terms or ())
        self.timer = timer or MonotonicTimer()

    def check_answer(
        self,
        *,
        query: str,
        answer: Optional[str],
        context: str,
        citations: Optional[List[Dict[str, Any]]] = None,
    ) -> SelfRAGJudgeResult:
        started_at = self.timer.now()
        if not _safe_str(answer).strip():
            return SelfRAGJudgeResult(
                enabled=True,
                method="no_answer",
                is_supported=False,
                faithfulness_label="unknown",
                answer_relevance_label="unknown",
                problems=["answer is empty; Self-RAG check skipped"],
                score=0.0,
                latency_ms=elapsed_ms(self.timer, started_at),
            )

        if self._llm_available():
            try:
                result = self._llm_check(
                    query=query,
                    answer=answer or "",
                    context=context,
                    citations=citations or [],
                )
                result.latency_ms = elapsed_ms(self.timer, started_at)
                return result
            except (ModelCallBudgetExceeded, WorkflowExecutionCancelled):
                raise
            except Exception as exc:
                if not self.fallback_to_deterministic:
                    raise
                result = self._deterministic_check(
                    query=query,
                    answer=answer or "",
                    context=context,
                    citations=citations or [],
                )
                result.metadata["fallback_used"] = True
                result.metadata["fallback_reason"] = (
                    f"{exc.__class__.__name__}: {exc}"
                )
                result.latency_ms = elapsed_ms(self.timer, started_at)
                return result

        result = self._deterministic_check(
            query=query,
            answer=answer or "",
            context=context,
            citations=citations or [],
        )
        result.latency_ms = elapsed_ms(self.timer, started_at)
        return result

    def _llm_available(self) -> bool:
        return bool(self.use_llm and self.llm_generator is not None)

    def _call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str,
        max_new_tokens: int = 384,
    ) -> str:
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
            text = self.llm_generator.generate(
                prompt,
                system_prompt=system_prompt,
                **params,
            )
        except TypeError:
            text = self.llm_generator.generate(
                f"{system_prompt}\n\n{prompt}",
                **params,
            )
        return _safe_str(text).strip()

    def _llm_check(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: List[Dict[str, Any]],
    ) -> SelfRAGJudgeResult:
        prompt = (
            "请检查答案是否忠实于资料、是否回答用户问题、是否需要重写或补充检索。\n"
            "输出 JSON 字段：is_supported(boolean), "
            "faithfulness_label(high/medium/low), "
            "answer_relevance_label(high/medium/low), "
            "need_rewrite(boolean), need_retrieve_more(boolean), "
            "unsupported_claims(list), problems(list), score(0到1)。\n"
            "如果答案把局部业务场景误写成主目标，必须标记 need_rewrite=true。\n\n"
            f"用户问题：{query}\n\n"
            f"资料：{_clip(context, 3500)}\n\n"
            f"答案：{_clip(answer, 2200)}\n\n"
            f"引用数量：{len(citations)}"
        )
        raw = self._call_llm(
            prompt,
            system_prompt=(
                "你是企业级 Self-RAG 答案忠实性检查器。"
                "只输出合法 JSON，不要解释。"
            ),
        )
        value = _extract_json_object(raw)
        if value is None:
            raise ValueError("LLM Self-RAG checker did not return JSON")

        unsupported = value.get("unsupported_claims") or []
        problems = value.get("problems") or []
        return SelfRAGJudgeResult(
            enabled=True,
            method="llm",
            is_supported=bool(value.get("is_supported")),
            faithfulness_label=_normalize_label(
                value.get("faithfulness_label"),
                ("high", "medium", "low", "unknown"),
                "medium",
            ),
            answer_relevance_label=_normalize_label(
                value.get("answer_relevance_label"),
                ("high", "medium", "low", "unknown"),
                "medium",
            ),
            need_rewrite=bool(value.get("need_rewrite")),
            need_retrieve_more=bool(value.get("need_retrieve_more")),
            unsupported_claims=(
                unsupported if isinstance(unsupported, list) else [unsupported]
            ),
            problems=problems if isinstance(problems, list) else [problems],
            score=max(
                0.0,
                min(1.0, _safe_float(value.get("score"), 0.5)),
            ),
            raw_output=raw,
            metadata={
                "checker": self.__class__.__name__,
                "llm_enabled": True,
            },
        )

    def _deterministic_check(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: List[Dict[str, Any]],
    ) -> SelfRAGJudgeResult:
        normalized_answer = _safe_str(answer)
        normalized_context = _safe_str(context)
        query_overlap, matched_query_terms = _keyword_overlap_score(
            query,
            normalized_answer,
        )
        answer_tokens = _tokenize(normalized_answer)
        lowered_context = normalized_context.lower()
        supported_terms = [
            token for token in answer_tokens if token in lowered_context
        ]
        support_ratio = (
            len(supported_terms) / max(1, len(answer_tokens))
            if answer_tokens
            else 0.0
        )

        answer_noise = _noise_terms_in_text(
            normalized_answer,
            absent_from_query=query,
            noise_terms=self.noise_terms,
        )
        context_noise = _noise_terms_in_text(
            normalized_context,
            absent_from_query=query,
            noise_terms=self.noise_terms,
        )
        topic_drift = bool(answer_noise)

        score = (
            0.45 * query_overlap
            + 0.45 * support_ratio
            + 0.10 * (1.0 if citations else 0.0)
        )
        if topic_drift:
            score -= min(0.25, 0.08 * len(answer_noise))
        score = max(0.0, min(1.0, score))

        problems: List[str] = []
        unsupported_claims: List[Dict[str, Any]] = []
        if query_overlap < 0.22:
            problems.append("answer has weak overlap with the user query")
        if support_ratio < 0.18:
            problems.append(
                "answer appears weakly grounded in the provided context"
            )
        if topic_drift:
            problems.append(
                "answer contains topic-drift terms absent from query: "
                + ", ".join(answer_noise)
            )
            unsupported_claims.append(
                {
                    "claim": (
                        "answer emphasizes related-but-possibly-off-topic content"
                    ),
                    "problem": (
                        "topic terms are absent from query and should not become "
                        "the main objective"
                    ),
                    "terms": answer_noise,
                }
            )

        faithfulness = (
            "high"
            if support_ratio >= 0.45 and not topic_drift
            else "medium"
            if support_ratio >= 0.18
            else "low"
        )
        relevance = (
            "high"
            if query_overlap >= 0.45 and not topic_drift
            else "medium"
            if query_overlap >= 0.22
            else "low"
        )
        need_rewrite = bool(score < 0.55 or topic_drift)
        need_retrieve_more = bool(
            support_ratio < 0.18 or (context_noise and not citations)
        )

        return SelfRAGJudgeResult(
            enabled=True,
            method="deterministic_fallback",
            is_supported=not need_rewrite,
            faithfulness_label=faithfulness,
            answer_relevance_label=relevance,
            need_rewrite=need_rewrite,
            need_retrieve_more=need_retrieve_more,
            unsupported_claims=unsupported_claims,
            problems=problems,
            score=round(score, 4),
            metadata={
                "checker": self.__class__.__name__,
                "llm_enabled": False,
                "matched_query_terms": matched_query_terms[:30],
                "answer_noise_terms": answer_noise,
                "context_noise_terms": context_noise,
                "support_ratio": round(support_ratio, 4),
                "query_overlap": round(query_overlap, 4),
            },
        )


__all__ = ["SelfRAGJudge", "SelfRAGJudgeResult"]
