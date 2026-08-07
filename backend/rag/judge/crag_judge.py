"""C-RAG evidence relevance judge used by the production retrieval pipeline."""

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
    return value if len(value) <= limit else value[:limit].rstrip() + "...[TRUNCATED]"


def _result_text(result: Dict[str, Any]) -> str:
    return _safe_str(
        result.get("text")
        or result.get("parent_text")
        or result.get("context_text")
        or result.get("match_text")
        or result.get("child_text")
    )


def _result_id(result: Dict[str, Any]) -> str:
    return _safe_str(
        result.get("parent_chunk_id")
        or result.get("context_chunk_id")
        or result.get("child_chunk_id")
        or result.get("matched_chunk_id")
        or result.get("chunk_id")
        or result.get("doc_id")
    )


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


def _tokenize(text: str) -> List[str]:
    value = _safe_str(text).lower()
    tokens: List[str] = re.findall(r"[a-z0-9][a-z0-9_\-.]{1,}", value)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if len(run) <= 4:
            tokens.append(run)
            continue
        for width in (2, 3, 4):
            for index in range(0, len(run) - width + 1):
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


def _keyword_overlap_score(query: str, text: str) -> Tuple[float, List[str]]:
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
class CRAGJudgeResult:
    """Stable retrieval-evidence assessment consumed by the CRAG plugin."""

    enabled: bool
    method: str = "disabled"
    evidence_count: int = 0
    retrieval_confidence: float = 0.0
    item_judgements: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": self.method,
            "evidence_count": self.evidence_count,
            "retrieval_confidence": self.retrieval_confidence,
            "item_judgements": self.item_judgements,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


class CRAGJudge:
    """Observe reranked evidence and emit relevance labels without mutating it."""

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

    def evaluate(
        self,
        *,
        query: str,
        results: List[Dict[str, Any]],
        max_judge_chunks: int = 8,
        runtime_context: Dict[str, Any] | None = None,
    ) -> CRAGJudgeResult:
        started_at = self.timer.now()
        evidence = tuple(results or ())
        judgements: List[Dict[str, Any]] = []
        limit = max(1, int(max_judge_chunks))
        for rank, result in enumerate(evidence, start=1):
            judgement = (
                self.judge_chunk(
                    query=query,
                    result=result,
                    rank=rank,
                    runtime_context=runtime_context,
                )
                if rank <= limit
                else self._skip_judgement(result=result, rank=rank)
            )
            judgements.append(judgement)
        label_counts = {
            label: sum(
                item.get("relevance_label") == label for item in judgements
            )
            for label in ("relevant", "partial", "irrelevant")
        }
        return CRAGJudgeResult(
            enabled=True,
            method="llm" if self._llm_available() else "deterministic_fallback",
            evidence_count=len(evidence),
            retrieval_confidence=self._retrieval_confidence(judgements),
            item_judgements=judgements,
            latency_ms=elapsed_ms(self.timer, started_at),
            metadata={
                "judge": self.__class__.__name__,
                "llm_enabled": self._llm_available(),
                "max_judge_chunks": limit,
                "label_counts": label_counts,
                "assessment_only": True,
            },
        )

    def judge_chunk(
        self,
        *,
        query: str,
        result: Dict[str, Any],
        rank: int,
        runtime_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if self._llm_available():
            try:
                return self._llm_judge_chunk(
                    query=query,
                    result=result,
                    rank=rank,
                    runtime_context=runtime_context,
                )
            except (ModelCallBudgetExceeded, WorkflowExecutionCancelled):
                raise
            except Exception as exc:
                if not self.fallback_to_deterministic:
                    raise
                fallback = self._deterministic_judge_chunk(
                    query=query,
                    result=result,
                    rank=rank,
                )
                fallback["fallback_used"] = True
                fallback["fallback_reason"] = f"{exc.__class__.__name__}: {exc}"
                return fallback
        return self._deterministic_judge_chunk(
            query=query,
            result=result,
            rank=rank,
        )

    def _llm_available(self) -> bool:
        return bool(self.use_llm and self.llm_generator is not None)

    def _call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str,
        max_new_tokens: int = 256,
        call_purpose: str = "rag_evidence_assessment",
        runtime_context: Dict[str, Any] | None = None,
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
            value = self.llm_generator.generate(
                prompt,
                system_prompt=system_prompt,
                call_purpose=call_purpose,
                runtime_context=runtime_context,
                **params,
            )
        except TypeError:
            value = self.llm_generator.generate(
                f"{system_prompt}\n\n{prompt}",
                **params,
            )
        return _safe_str(value).strip()

    def _llm_judge_chunk(
        self,
        *,
        query: str,
        result: Dict[str, Any],
        rank: int,
        runtime_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        prompt = (
            "C-RAG 只判断片段是否与当前问题或章节主题相关。\n"
            "不要判断片段能否证明当前项目事实，也不要判断它是否具备当前项目声明权限。\n"
            "不是当前项目事实，不等于与当前章节无关。\n"
            "历史案例、技术参考或模板材料，如果能够支持通用方法、架构思路、"
            "参考经验或章节组织，应判为 relevant 或 partial。\n"
            "只有与当前主题没有实际可用关系时，才判为 irrelevant。\n"
            "输出 JSON 字段：relevance_label(relevant/partial/irrelevant), "
            "score(0到1), reason。\n"
            "只做相关性和检索完整性标注，不决定证据身份、声明权限、删除、降权或重排。\n\n"
            f"用户问题：{query}\n"
            f"片段标题：{_safe_str(result.get('title'))}\n"
            f"片段内容：{_clip(_result_text(result), 1200)}"
        )
        started_at = self.timer.now()
        raw = self._call_llm(
            prompt,
            system_prompt="你是企业级 C-RAG 检索质量评估器。只输出合法 JSON，不要解释。",
            runtime_context=runtime_context,
        )
        value = _extract_json_object(raw)
        if value is None:
            raise ValueError("LLM C-RAG judge did not return JSON")
        label = _safe_str(value.get("relevance_label")).lower()
        if label not in {"relevant", "partial", "irrelevant"}:
            label = "partial"
        return {
            "chunk_id": _result_id(result),
            "rank": rank,
            "relevance_label": label,
            "score": max(0.0, min(1.0, _safe_float(value.get("score"), 0.5))),
            "reason": _safe_str(
                value.get("reason") or "LLM retrieval judgement"
            ),
            "judge_method": "llm",
            "latency_ms": elapsed_ms(self.timer, started_at),
            "raw_output": raw,
        }

    def _deterministic_judge_chunk(
        self,
        *,
        query: str,
        result: Dict[str, Any],
        rank: int,
    ) -> Dict[str, Any]:
        overlap, matched = _keyword_overlap_score(query, _result_text(result))
        noise = _noise_terms_in_text(
            _result_text(result),
            absent_from_query=query,
            noise_terms=self.noise_terms,
        )
        rerank_score = _safe_float(result.get("rerank_score"), 0.0)
        dense_score = _safe_float(result.get("score"), 0.0)
        score = 0.72 * overlap + 0.18 * min(max(dense_score, 0.0), 1.0)
        if rerank_score > 0:
            score += 0.1
        if noise:
            score -= min(0.25, 0.08 * len(noise))
        score = max(0.0, min(1.0, score))
        if score >= 0.55 and not noise:
            label = "relevant"
            reason = "query terms are well covered by the chunk"
        elif score >= 0.22 or matched:
            label = "partial"
            reason = (
                "chunk is partially related but may not be strong enough "
                "as primary evidence"
            )
            if noise:
                reason = (
                    "chunk contains related-but-noisy terms not present in "
                    f"query: {', '.join(noise)}"
                )
        else:
            label = "irrelevant"
            reason = "chunk has weak lexical/topic overlap with the query"
        return {
            "chunk_id": _result_id(result),
            "rank": rank,
            "relevance_label": label,
            "score": round(score, 4),
            "reason": reason,
            "matched_query_terms": matched[:20],
            "noise_terms": noise,
            "judge_method": "deterministic_fallback",
            "fallback_used": False,
            "fallback_reason": None,
        }

    @staticmethod
    def _skip_judgement(
        *,
        result: Dict[str, Any],
        rank: int,
    ) -> Dict[str, Any]:
        return {
            "chunk_id": _result_id(result),
            "rank": rank,
            "relevance_label": "partial",
            "score": 0.5,
            "reason": "not judged because max_judge_chunks limit was reached",
            "judge_method": "skipped",
        }

    @staticmethod
    def _retrieval_confidence(judgements: List[Dict[str, Any]]) -> float:
        if not judgements:
            return 0.0
        weights = {"relevant": 1.0, "partial": 0.55, "irrelevant": 0.0}
        total = sum(
            weights.get(item.get("relevance_label"), 0.35)
            * _safe_float(item.get("score"), 0.5)
            for item in judgements
        )
        return round(total / len(judgements), 4)


__all__ = ["CRAGJudge", "CRAGJudgeResult"]
