# -*- coding: utf-8 -*-
"""
rag_template/judge/rag_quality_judge.py
=======================================

Lite implementations of C-RAG and Self-RAG quality checks.

This module is intentionally lightweight for the pre-enterprise-migration stage:
- C-RAG-lite judges retrieved chunks after rerank and can filter/downrank noisy chunks.
- Self-RAG-lite judges the generated answer after generation and records whether it
  appears supported by the selected context.

Both classes support an optional LLM judge, but always keep deterministic fallback so
unit tests and low-resource runs remain stable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.runtime.timing import MonotonicTimer, Timer, elapsed_ms
from rag.ports.generation import TextGenerator


_DEFAULT_NOISE_TERMS: tuple[str, ...] = ()

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
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clip(text: str, limit: int) -> str:
    text = _safe_str(text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[TRUNCATED]"


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


def _normalize_label(value: Any, allowed: Iterable[str], default: str) -> str:
    text = _safe_str(value).strip().lower()
    allowed_set = set(allowed)
    return text if text in allowed_set else default


def _tokenize(text: str) -> List[str]:
    """Small mixed Chinese/English tokenizer for deterministic fallback.

    It extracts English/alphanumeric words and short Chinese n-grams. This is not
    a replacement for a real tokenizer; it only provides stable fallback signals.
    """
    text = _safe_str(text).lower()
    tokens: List[str] = []
    tokens.extend(re.findall(r"[a-z0-9][a-z0-9_\-.]{1,}", text))

    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for run in chinese_runs:
        if len(run) <= 4:
            tokens.append(run)
        else:
            # Bigram/trigram anchors are more useful than single characters.
            for n in (2, 3, 4):
                for i in range(0, max(0, len(run) - n + 1)):
                    tokens.append(run[i : i + n])

    cleaned: List[str] = []
    seen = set()
    for tok in tokens:
        tok = tok.strip().lower()
        if not tok or tok in _STOPWORDS:
            continue
        if len(tok) < 2:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        cleaned.append(tok)
    return cleaned


def _keyword_overlap_score(query: str, text: str) -> Tuple[float, List[str]]:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0, []
    lowered = _safe_str(text).lower()
    matched = [tok for tok in q_tokens if tok in lowered]
    return len(matched) / max(1, len(q_tokens)), matched


def _noise_terms_in_text(
    text: str,
    *,
    absent_from_query: str = "",
    noise_terms: Iterable[str] = _DEFAULT_NOISE_TERMS,
) -> List[str]:
    lowered = _safe_str(text).lower()
    query_lowered = _safe_str(absent_from_query).lower()
    terms = []
    for term in noise_terms:
        term_l = term.lower()
        if term_l in lowered and term_l not in query_lowered:
            terms.append(term)
    return sorted(terms)


@dataclass
class CRAGJudgeResult:
    enabled: bool
    method: str = "disabled"
    original_count: int = 0
    filtered_count: int = 0
    retrieval_confidence: float = 0.0
    chunk_judgements: List[Dict[str, Any]] = field(default_factory=list)
    corrective_action: Dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "method": self.method,
            "original_count": self.original_count,
            "filtered_count": self.filtered_count,
            "retrieval_confidence": self.retrieval_confidence,
            "chunk_judgements": self.chunk_judgements,
            "corrective_action": self.corrective_action,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


class CRAGJudge:
    """C-RAG-lite retrieval evaluator.

    It does not perform external web search. The corrective action is limited to
    chunk-level keep/downrank/drop plus trace metadata. Enterprise migration can
    replace this with a richer retrieval evaluator and re-retrieval policy.
    """

    def __init__(
        self,
        *,
        llm_generator: Optional[TextGenerator] = None,
        use_llm: bool = False,
        generation_params: Optional[Dict[str, Any]] = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Optional[Iterable[str]] = None,
        ranking_policy: str = "demotion_only",
        timer: Timer | None = None,
    ):
        self.llm_generator = llm_generator
        self.use_llm = bool(use_llm)
        self.generation_params = dict(generation_params or {})
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.noise_terms = tuple(noise_terms or ())
        normalized_policy = str(ranking_policy or "demotion_only").strip().lower()
        if normalized_policy not in {"demotion_only", "legacy_label_score"}:
            raise ValueError(
                "ranking_policy must be 'demotion_only' or 'legacy_label_score'"
            )
        self.ranking_policy = normalized_policy
        self.timer = timer or MonotonicTimer()

    def evaluate_and_filter(
        self,
        *,
        query: str,
        results: List[Dict[str, Any]],
        max_judge_chunks: int = 8,
        drop_irrelevant: bool = True,
        keep_at_least: int = 1,
    ) -> Tuple[List[Dict[str, Any]], CRAGJudgeResult]:
        t0 = self.timer.now()
        original = list(results or [])
        judgements: List[Dict[str, Any]] = []

        for idx, result in enumerate(original, start=1):
            if idx <= int(max_judge_chunks):
                judgement = self.judge_chunk(query=query, result=result, rank=idx)
            else:
                judgement = self._skip_judgement(result=result, rank=idx)
            judgements.append(judgement)

        judgement_by_id = {j.get("chunk_id"): j for j in judgements if j.get("chunk_id")}
        filtered: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []

        for idx, result in enumerate(original, start=1):
            item = dict(result)
            cid = _result_id(item)
            judgement = judgement_by_id.get(cid) or judgements[idx - 1]
            metadata = dict(item.get("metadata") or {})
            metadata["c_rag_judgement"] = judgement
            item["metadata"] = metadata

            decision = judgement.get("decision")
            if drop_irrelevant and decision == "drop":
                dropped.append(item)
                continue
            filtered.append(item)

        if not filtered and original:
            # Never let C-RAG erase all context in this lite version.
            filtered = [dict(original[0])]
            md = dict(filtered[0].get("metadata") or {})
            md["c_rag_forced_keep"] = True
            filtered[0]["metadata"] = md

        # C-RAG is a quality gate layered on top of the cross-encoder reranker.
        # An unstable LLM judgement must never promote a low-rerank candidate above
        # stronger evidence merely because it emitted a larger 0..1 score.  The
        # default policy is therefore *demotion only*: the complete reranker
        # order is preserved, downrank remains an advisory annotation, and only
        # dropped candidates are removed. No candidate can be promoted by judge
        # score or label.
        for original_position, item in enumerate(filtered, start=1):
            metadata = item.setdefault("metadata", {})
            metadata["c_rag_pre_filter_rank"] = int(
                _safe_float(item.get("rank"), original_position)
            )
            metadata["c_rag_rank_policy"] = self.ranking_policy

        if self.ranking_policy == "legacy_label_score":
            label_weight = {"relevant": 2, "partial": 1, "irrelevant": 0}

            def _legacy_sort_key(item: Dict[str, Any]) -> tuple:
                md = item.get("metadata") or {}
                judgement = md.get("c_rag_judgement") or {}
                return (
                    label_weight.get(judgement.get("relevance_label"), 0),
                    _safe_float(judgement.get("score"), 0.0),
                    -int(_safe_float(item.get("rank"), 9999)),
                )

            filtered.sort(key=_legacy_sort_key, reverse=True)
        else:
            # Preserve the complete cross-encoder order. ``downrank`` is kept as
            # an advisory quality annotation for trace/metrics, while only a
            # ``drop`` decision may remove a candidate. This gives the LLM judge
            # zero authority to promote any candidate over the reranker.
            filtered = list(filtered)

        for new_rank, item in enumerate(filtered, start=1):
            metadata = item.setdefault("metadata", {})
            previous_rank = int(metadata.get("c_rag_pre_filter_rank") or new_rank)
            judgement = metadata.get("c_rag_judgement") or {}
            item["rank"] = new_rank
            metadata["c_rag_post_filter_rank"] = new_rank
            metadata["c_rag_rank_action"] = (
                "downrank_advisory_reranker_order_preserved"
                if str(judgement.get("decision") or "").strip().lower() == "downrank"
                else "reranker_order_preserved"
            )
            metadata["c_rag_judge_score_used_for_promotion"] = (
                self.ranking_policy == "legacy_label_score"
            )
            metadata["c_rag_rank_delta"] = new_rank - previous_rank

        confidence = self._retrieval_confidence(judgements)
        action_name = "accept"
        reason = "retrieval context accepted"
        if dropped:
            action_name = "filter_context"
            reason = f"dropped {len(dropped)} irrelevant chunk(s) before context packing"
        elif any(j.get("decision") == "downrank" for j in judgements):
            action_name = "downrank_partial_context"
            reason = (
                "some chunks were judged partially relevant; reranker order was "
                "preserved and the downrank decision was recorded as advisory"
            )

        crag_result = CRAGJudgeResult(
            enabled=True,
            method="llm" if self._llm_available() else "deterministic_fallback",
            original_count=len(original),
            filtered_count=len(filtered),
            retrieval_confidence=confidence,
            chunk_judgements=judgements,
            corrective_action={
                "action": action_name,
                "reason": reason,
                "dropped_count": len(dropped),
                "kept_count": len(filtered),
            },
            latency_ms=elapsed_ms(self.timer, t0),
            metadata={
                "judge": self.__class__.__name__,
                "llm_enabled": self._llm_available(),
                "drop_irrelevant": bool(drop_irrelevant),
                "keep_at_least": int(keep_at_least),
                "ranking_policy": self.ranking_policy,
                "judge_score_used_for_promotion": (
                    self.ranking_policy == "legacy_label_score"
                ),
            },
        )
        return filtered, crag_result

    def judge_chunk(self, *, query: str, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        if self._llm_available():
            try:
                return self._llm_judge_chunk(query=query, result=result, rank=rank)
            except Exception as exc:
                if not self.fallback_to_deterministic:
                    raise
                fallback = self._deterministic_judge_chunk(query=query, result=result, rank=rank)
                fallback["fallback_used"] = True
                fallback["fallback_reason"] = f"{exc.__class__.__name__}: {exc}"
                return fallback
        return self._deterministic_judge_chunk(query=query, result=result, rank=rank)

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

    def _llm_judge_chunk(self, *, query: str, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        chunk_id = _result_id(result)
        text = _clip(_result_text(result), 1200)
        title = _safe_str(result.get("title"))
        system_prompt = "你是企业级 C-RAG 检索质量评估器。只输出合法 JSON，不要解释。"
        prompt = (
            "请判断检索片段是否适合用于回答用户问题。\n"
            "输出 JSON 字段：relevance_label(relevant/partial/irrelevant), "
            "decision(keep/downrank/drop), score(0到1), reason。\n"
            "判断规则：如果片段只是相关业务场景但不适合作为用户问题的主证据，应标为 partial/downrank；"
            "如果明显偏题，应标为 irrelevant/drop。\n\n"
            f"用户问题：{query}\n"
            f"片段标题：{title}\n"
            f"片段内容：{text}"
        )
        t0 = self.timer.now()
        raw = self._call_llm(prompt, system_prompt=system_prompt, max_new_tokens=256)
        obj = _extract_json_object(raw)
        if not obj:
            raise ValueError("LLM C-RAG judge did not return JSON")
        label = _normalize_label(obj.get("relevance_label"), ["relevant", "partial", "irrelevant"], "partial")
        decision_default = {"relevant": "keep", "partial": "downrank", "irrelevant": "drop"}[label]
        decision = _normalize_label(obj.get("decision"), ["keep", "downrank", "drop"], decision_default)
        score = max(0.0, min(1.0, _safe_float(obj.get("score"), 0.5)))
        return {
            "chunk_id": chunk_id,
            "rank": rank,
            "relevance_label": label,
            "decision": decision,
            "score": score,
            "reason": _safe_str(obj.get("reason") or "LLM retrieval judgement"),
            "judge_method": "llm",
            "latency_ms": elapsed_ms(self.timer, t0),
            "raw_output": raw,
        }

    def _deterministic_judge_chunk(self, *, query: str, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        text = _result_text(result)
        chunk_id = _result_id(result)
        overlap, matched = _keyword_overlap_score(query, text)
        noise = _noise_terms_in_text(
            text,
            absent_from_query=query,
            noise_terms=self.noise_terms,
        )

        # Rerank scores can be negative for cross-encoders. We use them only as a weak signal.
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
            decision = "keep"
            reason = "query terms are well covered by the chunk"
        elif score >= 0.22 or matched:
            label = "partial"
            decision = "downrank" if noise else "keep"
            reason = "chunk is partially related but may not be strong enough as primary evidence"
            if noise:
                reason = f"chunk contains related-but-noisy terms not present in query: {', '.join(noise)}"
        else:
            label = "irrelevant"
            decision = "drop"
            reason = "chunk has weak lexical/topic overlap with the query"

        return {
            "chunk_id": chunk_id,
            "rank": rank,
            "relevance_label": label,
            "decision": decision,
            "score": round(score, 4),
            "reason": reason,
            "matched_query_terms": matched[:20],
            "noise_terms": noise,
            "judge_method": "deterministic_fallback",
            "fallback_used": False,
            "fallback_reason": None,
        }

    @staticmethod
    def _skip_judgement(*, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        return {
            "chunk_id": _result_id(result),
            "rank": rank,
            "relevance_label": "partial",
            "decision": "keep",
            "score": 0.5,
            "reason": "not judged because max_judge_chunks limit was reached",
            "judge_method": "skipped",
        }

    @staticmethod
    def _retrieval_confidence(judgements: List[Dict[str, Any]]) -> float:
        if not judgements:
            return 0.0
        weights = {"relevant": 1.0, "partial": 0.55, "irrelevant": 0.0}
        total = 0.0
        for j in judgements:
            total += weights.get(j.get("relevance_label"), 0.35) * _safe_float(j.get("score"), 0.5)
        return round(total / max(1, len(judgements)), 4)


@dataclass
class SelfRAGJudgeResult:
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
    """Self-RAG-lite answer checker.

    The lite version does not automatically rewrite answers. It records a stable
    answer_check object so later Agent/workflow code can decide whether to rewrite,
    retrieve more, escalate to a stronger model, or request human review.
    """

    def __init__(
        self,
        *,
        llm_generator: Optional[TextGenerator] = None,
        use_llm: bool = False,
        generation_params: Optional[Dict[str, Any]] = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Optional[Iterable[str]] = None,
        timer: Timer | None = None,
    ):
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
        t0 = self.timer.now()
        if not _safe_str(answer).strip():
            return SelfRAGJudgeResult(
                enabled=True,
                method="no_answer",
                is_supported=False,
                faithfulness_label="unknown",
                answer_relevance_label="unknown",
                need_rewrite=False,
                need_retrieve_more=False,
                problems=["answer is empty; Self-RAG check skipped"],
                score=0.0,
                latency_ms=elapsed_ms(self.timer, t0),
            )

        if self._llm_available():
            try:
                result = self._llm_check(query=query, answer=answer or "", context=context, citations=citations or [])
                result.latency_ms = elapsed_ms(self.timer, t0)
                return result
            except Exception as exc:
                if not self.fallback_to_deterministic:
                    raise
                result = self._deterministic_check(query=query, answer=answer or "", context=context, citations=citations or [])
                result.metadata["fallback_used"] = True
                result.metadata["fallback_reason"] = f"{exc.__class__.__name__}: {exc}"
                result.latency_ms = elapsed_ms(self.timer, t0)
                return result

        result = self._deterministic_check(query=query, answer=answer or "", context=context, citations=citations or [])
        result.latency_ms = elapsed_ms(self.timer, t0)
        return result

    def _llm_available(self) -> bool:
        return bool(self.use_llm and self.llm_generator is not None)

    def _call_llm(self, prompt: str, *, system_prompt: str, max_new_tokens: int = 384) -> str:
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

    def _llm_check(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: List[Dict[str, Any]],
    ) -> SelfRAGJudgeResult:
        system_prompt = "你是企业级 Self-RAG 答案忠实性检查器。只输出合法 JSON，不要解释。"
        prompt = (
            "请检查答案是否忠实于资料、是否回答用户问题、是否需要重写或补充检索。\n"
            "输出 JSON 字段：is_supported(boolean), faithfulness_label(high/medium/low), "
            "answer_relevance_label(high/medium/low), need_rewrite(boolean), need_retrieve_more(boolean), "
            "unsupported_claims(list), problems(list), score(0到1)。\n"
            "如果答案把局部业务场景误写成主目标，必须标记 need_rewrite=true。\n\n"
            f"用户问题：{query}\n\n"
            f"资料：{_clip(context, 3500)}\n\n"
            f"答案：{_clip(answer, 2200)}\n\n"
            f"引用数量：{len(citations)}"
        )
        raw = self._call_llm(prompt, system_prompt=system_prompt, max_new_tokens=384)
        obj = _extract_json_object(raw)
        if not obj:
            raise ValueError("LLM Self-RAG checker did not return JSON")

        faithfulness = _normalize_label(obj.get("faithfulness_label"), ["high", "medium", "low", "unknown"], "medium")
        relevance = _normalize_label(obj.get("answer_relevance_label"), ["high", "medium", "low", "unknown"], "medium")
        unsupported = obj.get("unsupported_claims") or []
        problems = obj.get("problems") or []
        return SelfRAGJudgeResult(
            enabled=True,
            method="llm",
            is_supported=bool(obj.get("is_supported")),
            faithfulness_label=faithfulness,
            answer_relevance_label=relevance,
            need_rewrite=bool(obj.get("need_rewrite")),
            need_retrieve_more=bool(obj.get("need_retrieve_more")),
            unsupported_claims=unsupported if isinstance(unsupported, list) else [unsupported],
            problems=problems if isinstance(problems, list) else [problems],
            score=max(0.0, min(1.0, _safe_float(obj.get("score"), 0.5))),
            raw_output=raw,
            metadata={"checker": self.__class__.__name__, "llm_enabled": True},
        )

    def _deterministic_check(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: List[Dict[str, Any]],
    ) -> SelfRAGJudgeResult:
        answer = _safe_str(answer)
        context = _safe_str(context)
        query_overlap, matched_query_terms = _keyword_overlap_score(query, answer)
        answer_tokens = _tokenize(answer)
        if answer_tokens:
            supported_terms = [tok for tok in answer_tokens if tok in context.lower()]
            support_ratio = len(supported_terms) / max(1, len(answer_tokens))
        else:
            support_ratio = 0.0

        answer_noise = _noise_terms_in_text(
            answer,
            absent_from_query=query,
            noise_terms=self.noise_terms,
        )
        context_noise = _noise_terms_in_text(
            context,
            absent_from_query=query,
            noise_terms=self.noise_terms,
        )
        topic_drift = bool(answer_noise and len(answer_noise) >= 1)

        score = 0.45 * query_overlap + 0.45 * support_ratio + 0.10 * (1.0 if citations else 0.0)
        if topic_drift:
            score -= min(0.25, 0.08 * len(answer_noise))
        score = max(0.0, min(1.0, score))

        problems: List[str] = []
        unsupported_claims: List[Dict[str, Any]] = []
        if query_overlap < 0.22:
            problems.append("answer has weak overlap with the user query")
        if support_ratio < 0.18:
            problems.append("answer appears weakly grounded in the provided context")
        if topic_drift:
            problems.append(
                "answer contains topic-drift terms absent from query: " + ", ".join(answer_noise)
            )
            unsupported_claims.append(
                {
                    "claim": "answer emphasizes related-but-possibly-off-topic content",
                    "problem": "topic terms are absent from query and should not become the main objective",
                    "terms": answer_noise,
                }
            )

        faithfulness = "high" if support_ratio >= 0.45 and not topic_drift else "medium" if support_ratio >= 0.18 else "low"
        relevance = "high" if query_overlap >= 0.45 and not topic_drift else "medium" if query_overlap >= 0.22 else "low"
        need_rewrite = bool(score < 0.55 or topic_drift)
        need_more = bool(support_ratio < 0.18 or (context_noise and not citations))

        return SelfRAGJudgeResult(
            enabled=True,
            method="deterministic_fallback",
            is_supported=not need_rewrite,
            faithfulness_label=faithfulness,
            answer_relevance_label=relevance,
            need_rewrite=need_rewrite,
            need_retrieve_more=need_more,
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
