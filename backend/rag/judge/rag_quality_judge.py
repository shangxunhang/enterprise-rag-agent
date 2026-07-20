# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 质量判断器：集中实现 CRAG 证据评估与 Self-RAG 生成支持性检查。
# 主要定义：_safe_str、_safe_float、_clip、_result_text、_result_id、_extract_json_object、_normalize_label、_tokenize、_keyword_overlap_score、_noise_terms_in_text等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/judge/rag_quality_judge.py
=======================================

Lite implementations of C-RAG and Self-RAG quality checks.

This module is intentionally lightweight for the pre-enterprise-migration stage:
- C-RAG-lite observes retrieved chunks after rerank and reports quality labels.
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


# 阅读注释（函数）：处理 safe str 相关逻辑。
def _safe_str(value: Any, default: str = "") -> str:
    """处理 safe str 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：isinstance, str。
    """
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


# 阅读注释（函数）：处理 safe float 相关逻辑。
def _safe_float(value: Any, default: float = 0.0) -> float:
    """处理 safe float 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：float。
    """
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


# 阅读注释（函数）：处理 clip 相关逻辑。
def _clip(text: str, limit: int) -> str:
    """处理 clip 相关逻辑。

    参数:
        text: 待处理文本。
        limit: limit，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：strip, _safe_str, len, rstrip。
    """
    text = _safe_str(text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[TRUNCATED]"


# 阅读注释（函数）：处理 结果 文本 相关逻辑。
def _result_text(result: Dict[str, Any]) -> str:
    """处理 结果 文本 相关逻辑。

    参数:
        result: 待处理的结果对象。

    返回:
        str

    阅读提示:
        主要直接调用：_safe_str, result.get。
    """
    return _safe_str(
        result.get("text")
        or result.get("parent_text")
        or result.get("context_text")
        or result.get("match_text")
        or result.get("child_text")
    )


# 阅读注释（函数）：处理 结果 标识 相关逻辑。
def _result_id(result: Dict[str, Any]) -> str:
    """处理 结果 标识 相关逻辑。

    参数:
        result: 待处理的结果对象。

    返回:
        str

    阅读提示:
        主要直接调用：_safe_str, result.get。
    """
    return _safe_str(
        result.get("parent_chunk_id")
        or result.get("context_chunk_id")
        or result.get("child_chunk_id")
        or result.get("matched_chunk_id")
        or result.get("chunk_id")
        or result.get("doc_id")
    )


# 阅读注释（函数）：提取 JSON object。
def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    """提取 JSON object。

    参数:
        raw_text: raw 文本，具体约束请结合类型标注和调用方确认。

    返回:
        Optional[Dict[str, Any]]

    阅读提示:
        主要直接调用：strip, _safe_str, json.loads, isinstance, re.search, match.group。
    """
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


# 阅读注释（函数）：规范化 label。
def _normalize_label(value: Any, allowed: Iterable[str], default: str) -> str:
    """规范化 label。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        allowed: allowed，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：lower, strip, _safe_str, set。
    """
    text = _safe_str(value).strip().lower()
    allowed_set = set(allowed)
    return text if text in allowed_set else default


# 阅读注释（函数）：处理 tokenize 相关逻辑。
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


# 阅读注释（函数）：处理 keyword overlap score 相关逻辑。
def _keyword_overlap_score(query: str, text: str) -> Tuple[float, List[str]]:
    """处理 keyword overlap score 相关逻辑。

    参数:
        query: 当前检索或生成查询。
        text: 待处理文本。

    返回:
        Tuple[float, List[str]]

    阅读提示:
        主要直接调用：_tokenize, lower, _safe_str, len, max。
    """
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0, []
    lowered = _safe_str(text).lower()
    matched = [tok for tok in q_tokens if tok in lowered]
    return len(matched) / max(1, len(q_tokens)), matched


# 阅读注释（函数）：处理 noise terms in 文本 相关逻辑。
def _noise_terms_in_text(
    text: str,
    *,
    absent_from_query: str = "",
    noise_terms: Iterable[str] = _DEFAULT_NOISE_TERMS,
) -> List[str]:
    """处理 noise terms in 文本 相关逻辑。

    参数:
        text: 待处理文本。
        absent_from_query: absent from 查询，具体约束请结合类型标注和调用方确认。
        noise_terms: noise terms，具体约束请结合类型标注和调用方确认。

    返回:
        List[str]

    阅读提示:
        主要直接调用：lower, _safe_str, term.lower, terms.append, sorted。
    """
    lowered = _safe_str(text).lower()
    query_lowered = _safe_str(absent_from_query).lower()
    terms = []
    for term in noise_terms:
        term_l = term.lower()
        if term_l in lowered and term_l not in query_lowered:
            terms.append(term)
    return sorted(terms)


# 阅读注释（类）：封装 cragjudge 结果，集中封装相关状态、依赖和行为。
@dataclass
class CRAGJudgeResult:
    """封装 cragjudge 结果，集中封装相关状态、依赖和行为。"""
    enabled: bool
    method: str = "disabled"
    evidence_count: int = 0
    retrieval_confidence: float = 0.0
    item_judgements: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 阅读注释（函数）：把 CRAGJudgeResult 转换为 字典。
    def to_dict(self) -> Dict[str, Any]:
        """把 CRAGJudgeResult 转换为 字典。

        返回:
            Dict[str, Any]
        """
        return {
            "enabled": self.enabled,
            "method": self.method,
            "evidence_count": self.evidence_count,
            "retrieval_confidence": self.retrieval_confidence,
            "item_judgements": self.item_judgements,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


# 阅读注释（类）：封装 cragjudge，集中封装相关状态、依赖和行为。
class CRAGJudge:
    """C-RAG-lite retrieval evaluator.

    It only observes evidence and emits labels. It cannot filter, reorder or
    annotate the evidence collection supplied by the caller.
    """

    # 阅读注释（函数）：初始化 CRAGJudge，保存运行所需的依赖、配置或状态。
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
        """初始化 CRAGJudge，保存运行所需的依赖、配置或状态。

        参数:
            llm_generator: LLM generator，具体约束请结合类型标注和调用方确认。
            use_llm: use LLM，具体约束请结合类型标注和调用方确认。
            generation_params: 生成 params，具体约束请结合类型标注和调用方确认。
            fallback_to_deterministic: fallback to deterministic，具体约束请结合类型标注和调用方确认。
            noise_terms: noise terms，具体约束请结合类型标注和调用方确认。
            timer: timer，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：bool, dict, tuple, lower, strip, str, ValueError, MonotonicTimer。
        """
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
    ) -> CRAGJudgeResult:
        """Observe reranked evidence and return quality labels only."""
        t0 = self.timer.now()
        evidence = tuple(results or ())
        judgements: List[Dict[str, Any]] = []

        for index, result in enumerate(evidence, start=1):
            if index <= int(max_judge_chunks):
                judgement = self.judge_chunk(
                    query=query,
                    result=result,
                    rank=index,
                )
            else:
                judgement = self._skip_judgement(result=result, rank=index)
            judgements.append(judgement)

        label_counts = {
            label: sum(
                1
                for item in judgements
                if str(item.get("relevance_label") or "") == label
            )
            for label in ("relevant", "partial", "irrelevant")
        }
        return CRAGJudgeResult(
            enabled=True,
            method="llm" if self._llm_available() else "deterministic_fallback",
            evidence_count=len(evidence),
            retrieval_confidence=self._retrieval_confidence(judgements),
            item_judgements=judgements,
            latency_ms=elapsed_ms(self.timer, t0),
            metadata={
                "judge": self.__class__.__name__,
                "llm_enabled": self._llm_available(),
                "max_judge_chunks": max(1, int(max_judge_chunks)),
                "label_counts": label_counts,
                "assessment_only": True,
            },
        )

    # 阅读注释（函数）：处理 judge 文本块 相关逻辑。
    def judge_chunk(self, *, query: str, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        """处理 judge 文本块 相关逻辑。

        参数:
            query: 当前检索或生成查询。
            result: 待处理的结果对象。
            rank: rank，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self._llm_available, self._llm_judge_chunk, self._deterministic_judge_chunk。
        """
        # 优先使用配置的 Judge 模型；模型不可用时退化为确定性规则，保证主链可运行。
        # Self-RAG 优先使用 Judge 模型；失败时可回退到确定性规则。
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

    # 阅读注释（函数）：处理 LLM available 相关逻辑。
    def _llm_available(self) -> bool:
        """处理 LLM available 相关逻辑。

        返回:
            bool

        阅读提示:
            主要直接调用：bool。
        """
        return bool(self.use_llm and self.llm_generator is not None)

    # 阅读注释（函数）：处理 call LLM 相关逻辑。
    def _call_llm(
        self,
        prompt: str,
        *,
        system_prompt: str,
        max_new_tokens: int = 256,
        call_purpose: str = "rag_evidence_assessment",
    ) -> str:
        """处理 call LLM 相关逻辑。

        参数:
            prompt: 提示词，具体约束请结合类型标注和调用方确认。
            system_prompt: system 提示词，具体约束请结合类型标注和调用方确认。
            max_new_tokens: max new tokens，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：RuntimeError, dict, params.pop, params.setdefault, self.llm_generator.generate, strip, _safe_str。
        """
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
                call_purpose=call_purpose,
                **params,
            )
        except TypeError:
            text = self.llm_generator.generate(f"{system_prompt}\n\n{prompt}", **params)
        return _safe_str(text).strip()

    # 阅读注释（函数）：处理 LLM judge 文本块 相关逻辑。
    def _llm_judge_chunk(self, *, query: str, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        """处理 LLM judge 文本块 相关逻辑。

        参数:
            query: 当前检索或生成查询。
            result: 待处理的结果对象。
            rank: rank，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：_result_id, _clip, _result_text, _safe_str, result.get, self.timer.now, self._call_llm, _extract_json_object。
        """
        chunk_id = _result_id(result)
        text = _clip(_result_text(result), 1200)
        title = _safe_str(result.get("title"))
        system_prompt = "你是企业级 C-RAG 检索质量评估器。只输出合法 JSON，不要解释。"
        prompt = (
            "请判断检索片段是否适合用于回答用户问题。\n"
            "输出 JSON 字段：relevance_label(relevant/partial/irrelevant), "
            "score(0到1), reason。\n"
            "只做质量标注，不决定删除、降权或重排证据。\n\n"
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
        score = max(0.0, min(1.0, _safe_float(obj.get("score"), 0.5)))
        return {
            "chunk_id": chunk_id,
            "rank": rank,
            "relevance_label": label,
            "score": score,
            "reason": _safe_str(obj.get("reason") or "LLM retrieval judgement"),
            "judge_method": "llm",
            "latency_ms": elapsed_ms(self.timer, t0),
            "raw_output": raw,
        }

    # 阅读注释（函数）：处理 deterministic judge 文本块 相关逻辑。
    def _deterministic_judge_chunk(self, *, query: str, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        """处理 deterministic judge 文本块 相关逻辑。

        参数:
            query: 当前检索或生成查询。
            result: 待处理的结果对象。
            rank: rank，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：_result_text, _result_id, _keyword_overlap_score, _noise_terms_in_text, _safe_float, result.get, min, max。
        """
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
            reason = "query terms are well covered by the chunk"
        elif score >= 0.22 or matched:
            label = "partial"
            reason = "chunk is partially related but may not be strong enough as primary evidence"
            if noise:
                reason = f"chunk contains related-but-noisy terms not present in query: {', '.join(noise)}"
        else:
            label = "irrelevant"
            reason = "chunk has weak lexical/topic overlap with the query"

        return {
            "chunk_id": chunk_id,
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

    # 阅读注释（函数）：处理 skip judgement 相关逻辑。
    @staticmethod
    def _skip_judgement(*, result: Dict[str, Any], rank: int) -> Dict[str, Any]:
        """处理 skip judgement 相关逻辑。

        参数:
            result: 待处理的结果对象。
            rank: rank，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：_result_id。
        """
        return {
            "chunk_id": _result_id(result),
            "rank": rank,
            "relevance_label": "partial",
            "score": 0.5,
            "reason": "not judged because max_judge_chunks limit was reached",
            "judge_method": "skipped",
        }

    # 阅读注释（函数）：处理 检索 置信度 相关逻辑。
    @staticmethod
    def _retrieval_confidence(judgements: List[Dict[str, Any]]) -> float:
        """处理 检索 置信度 相关逻辑。

        参数:
            judgements: judgements，具体约束请结合类型标注和调用方确认。

        返回:
            float

        阅读提示:
            主要直接调用：weights.get, j.get, _safe_float, round, max, len。
        """
        if not judgements:
            return 0.0
        weights = {"relevant": 1.0, "partial": 0.55, "irrelevant": 0.0}
        total = 0.0
        for j in judgements:
            total += weights.get(j.get("relevance_label"), 0.35) * _safe_float(j.get("score"), 0.5)
        return round(total / max(1, len(judgements)), 4)


# 阅读注释（类）：封装 Self ragjudge 结果，集中封装相关状态、依赖和行为。
@dataclass
class SelfRAGJudgeResult:
    """封装 Self ragjudge 结果，集中封装相关状态、依赖和行为。"""
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

    # 阅读注释（函数）：把 SelfRAGJudgeResult 转换为 字典。
    def to_dict(self) -> Dict[str, Any]:
        """把 SelfRAGJudgeResult 转换为 字典。

        返回:
            Dict[str, Any]
        """
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


# 阅读注释（类）：封装 Self ragjudge，集中封装相关状态、依赖和行为。
class SelfRAGJudge:
    """Self-RAG-lite answer checker.

    The lite version does not automatically rewrite answers. It records a stable
    answer_check object so later Agent/workflow code can decide whether to rewrite,
    retrieve more, escalate to a stronger model, or request human review.
    """

    # 阅读注释（函数）：初始化 SelfRAGJudge，保存运行所需的依赖、配置或状态。
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
        """初始化 SelfRAGJudge，保存运行所需的依赖、配置或状态。

        参数:
            llm_generator: LLM generator，具体约束请结合类型标注和调用方确认。
            use_llm: use LLM，具体约束请结合类型标注和调用方确认。
            generation_params: 生成 params，具体约束请结合类型标注和调用方确认。
            fallback_to_deterministic: fallback to deterministic，具体约束请结合类型标注和调用方确认。
            noise_terms: noise terms，具体约束请结合类型标注和调用方确认。
            timer: timer，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：bool, dict, tuple, MonotonicTimer。
        """
        self.llm_generator = llm_generator
        self.use_llm = bool(use_llm)
        self.generation_params = dict(generation_params or {})
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.noise_terms = tuple(noise_terms or ())
        self.timer = timer or MonotonicTimer()

    # 阅读注释（函数）：检查 answer。
    def check_answer(
        self,
        *,
        query: str,
        answer: Optional[str],
        context: str,
        citations: Optional[List[Dict[str, Any]]] = None,
    ) -> SelfRAGJudgeResult:
        """检查 answer。

        参数:
            query: 当前检索或生成查询。
            answer: answer，具体约束请结合类型标注和调用方确认。
            context: 当前执行上下文。
            citations: 引用信息集合。

        返回:
            SelfRAGJudgeResult

        阅读提示:
            主要直接调用：self.timer.now, strip, _safe_str, SelfRAGJudgeResult, elapsed_ms, self._llm_available, self._llm_check, self._deterministic_check。
        """
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

    # 阅读注释（函数）：处理 LLM available 相关逻辑。
    def _llm_available(self) -> bool:
        """处理 LLM available 相关逻辑。

        返回:
            bool

        阅读提示:
            主要直接调用：bool。
        """
        return bool(self.use_llm and self.llm_generator is not None)

    # 阅读注释（函数）：处理 call LLM 相关逻辑。
    def _call_llm(self, prompt: str, *, system_prompt: str, max_new_tokens: int = 384) -> str:
        """处理 call LLM 相关逻辑。

        参数:
            prompt: 提示词，具体约束请结合类型标注和调用方确认。
            system_prompt: system 提示词，具体约束请结合类型标注和调用方确认。
            max_new_tokens: max new tokens，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：RuntimeError, dict, params.pop, params.setdefault, self.llm_generator.generate, strip, _safe_str。
        """
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

    # 阅读注释（函数）：处理 LLM check 相关逻辑。
    def _llm_check(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: List[Dict[str, Any]],
    ) -> SelfRAGJudgeResult:
        """处理 LLM check 相关逻辑。

        参数:
            query: 当前检索或生成查询。
            answer: answer，具体约束请结合类型标注和调用方确认。
            context: 当前执行上下文。
            citations: 引用信息集合。

        返回:
            SelfRAGJudgeResult

        阅读提示:
            主要直接调用：_clip, len, self._call_llm, _extract_json_object, ValueError, _normalize_label, obj.get, SelfRAGJudgeResult。
        """
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

    # 阅读注释（函数）：处理 deterministic check 相关逻辑。
    def _deterministic_check(
        self,
        *,
        query: str,
        answer: str,
        context: str,
        citations: List[Dict[str, Any]],
    ) -> SelfRAGJudgeResult:
        """处理 deterministic check 相关逻辑。

        参数:
            query: 当前检索或生成查询。
            answer: answer，具体约束请结合类型标注和调用方确认。
            context: 当前执行上下文。
            citations: 引用信息集合。

        返回:
            SelfRAGJudgeResult

        阅读提示:
            主要直接调用：_safe_str, _keyword_overlap_score, _tokenize, context.lower, len, max, _noise_terms_in_text, bool。
        """
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
        # 收集无法被当前证据支持的声明，供后续局部改写使用。
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
