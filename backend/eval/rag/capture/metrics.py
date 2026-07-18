"""RAGAS-style lightweight proxy metrics.

This module intentionally does not depend on official ragas or an LLM judge.
It consumes the task-level capture record produced by the agent project and
returns deterministic proxy scores. Later, each function can be replaced by
official RAGAS metrics or LLM-as-judge without changing the runner/report layer.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Sequence, Set

from .schemas import RAGEvalMetricSchema


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
    "is", "are", "was", "were", "be", "by", "as", "from", "that", "this",
    "根据", "资料", "生成", "系统", "方案", "项目", "建设", "进行", "一个",
    "以及", "通过", "当前", "需要", "实现", "能力", "模块", "包括", "相关",
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def tokenize(text: str) -> List[str]:
    """Small mixed Chinese/English tokenizer.

    For Chinese, use individual CJK characters as a coarse fallback.
    For English/numbers, use regex words.
    """

    if not text:
        return []

    tokens: List[str] = []
    tokens.extend(x.lower() for x in _WORD_RE.findall(text))
    tokens.extend(_CJK_RE.findall(text))
    return [x for x in tokens if x and x not in STOPWORDS]


def token_set(text: str) -> Set[str]:
    return set(tokenize(text))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def coverage(source_tokens: Sequence[str], target_tokens: Sequence[str]) -> float:
    """How much of target is covered by source."""

    ss = set(source_tokens)
    ts = set(target_tokens)
    if not ts:
        return 0.0
    return len(ss & ts) / len(ts)


def extract_text_from_chunk(chunk: Dict[str, Any]) -> str:
    for key in ("context_text", "match_text", "text", "content", "quote_text", "summary"):
        value = chunk.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_context_text(sample: Dict[str, Any]) -> str:
    rag_context = sample.get("rag_context") or {}
    if isinstance(rag_context, dict):
        text = rag_context.get("context_text")
        if isinstance(text, str):
            return text

    eval_sample = sample.get("eval_sample") or {}
    if isinstance(eval_sample, dict):
        nested = eval_sample.get("rag_context") or {}
        if isinstance(nested, dict) and isinstance(nested.get("context_text"), str):
            return nested["context_text"]

    return ""


def extract_answer(sample: Dict[str, Any]) -> str:
    for key in ("final_output", "model_output"):
        value = sample.get(key)
        if isinstance(value, str) and value.strip():
            return value

    eval_sample = sample.get("eval_sample") or {}
    if isinstance(eval_sample, dict):
        for key in ("final_output", "model_output"):
            value = eval_sample.get(key)
            if isinstance(value, str) and value.strip():
                return value

    return ""


def extract_query(sample: Dict[str, Any]) -> str:
    for key in ("user_input", "query", "input"):
        value = sample.get(key)
        if isinstance(value, str) and value.strip():
            return value

    eval_sample = sample.get("eval_sample") or {}
    if isinstance(eval_sample, dict):
        value = eval_sample.get("input")
        if isinstance(value, str):
            return value

    return ""


def _chunk_relevance_scores(query: str, chunks: List[Dict[str, Any]]) -> List[float]:
    q_tokens = tokenize(query)
    scores: List[float] = []

    for chunk in chunks:
        text = extract_text_from_chunk(chunk)
        c_tokens = tokenize(text)
        lexical = coverage(c_tokens, q_tokens)

        rerank_score = chunk.get("rerank_score")
        rerank_bonus = 0.0
        if isinstance(rerank_score, (int, float)):
            # bge-reranker scores can be negative. Use a soft squashing only as a weak bonus.
            rerank_bonus = 1.0 / (1.0 + math.exp(-float(rerank_score)))

        score = 0.75 * lexical + 0.25 * rerank_bonus if rerank_bonus else lexical
        scores.append(clamp(score))

    return scores


def context_precision(query: str, retrieved_chunks: List[Dict[str, Any]]) -> RAGEvalMetricSchema:
    if not retrieved_chunks:
        return RAGEvalMetricSchema(
            name="context_precision",
            score=0.0,
            reason="No retrieved chunks.",
            details={"retrieved_chunk_num": 0},
        )

    scores = _chunk_relevance_scores(query=query, chunks=retrieved_chunks)
    relevant_count = sum(1 for x in scores if x > 0.0)

    # Rank-aware precision: earlier chunks get larger weights.
    weighted_sum = 0.0
    weight_total = 0.0
    for idx, score in enumerate(scores, start=1):
        weight = 1.0 / idx
        weighted_sum += score * weight
        weight_total += weight

    final_score = weighted_sum / weight_total if weight_total else 0.0

    return RAGEvalMetricSchema(
        name="context_precision",
        score=clamp(final_score),
        reason="Rank-aware lexical relevance between query and retrieved chunks.",
        details={
            "retrieved_chunk_num": len(retrieved_chunks),
            "relevant_count_proxy": relevant_count,
            "chunk_scores": scores,
        },
    )


def context_recall_proxy(query: str, context_text: str, retrieved_chunks: List[Dict[str, Any]]) -> RAGEvalMetricSchema:
    q_tokens = tokenize(query)
    context_tokens = tokenize(context_text)

    if not q_tokens:
        score = 0.0
        reason = "Empty query tokens."
    elif not context_tokens:
        score = 0.0
        reason = "Empty context."
    else:
        score = coverage(context_tokens, q_tokens)
        reason = "Proxy recall: how much query evidence appears in packed context."

    return RAGEvalMetricSchema(
        name="context_recall_proxy",
        score=clamp(score),
        reason=reason,
        details={
            "query_token_num": len(set(q_tokens)),
            "context_token_num": len(set(context_tokens)),
            "retrieved_chunk_num": len(retrieved_chunks),
        },
    )


def faithfulness_proxy(answer: str, context_text: str) -> RAGEvalMetricSchema:
    answer_tokens = tokenize(answer)
    context_tokens = tokenize(context_text)

    if not answer_tokens:
        return RAGEvalMetricSchema(
            name="faithfulness_proxy",
            score=0.0,
            reason="Empty answer.",
        )

    if not context_tokens:
        return RAGEvalMetricSchema(
            name="faithfulness_proxy",
            score=0.0,
            reason="Empty context.",
        )

    score = coverage(context_tokens, answer_tokens)

    unsupported = sorted(set(answer_tokens) - set(context_tokens))[:30]

    return RAGEvalMetricSchema(
        name="faithfulness_proxy",
        score=clamp(score),
        reason="Proxy faithfulness: answer token coverage by context.",
        details={
            "answer_token_num": len(set(answer_tokens)),
            "context_token_num": len(set(context_tokens)),
            "unsupported_token_preview": unsupported,
        },
    )


def answer_relevance_proxy(query: str, answer: str) -> RAGEvalMetricSchema:
    q_tokens = tokenize(query)
    a_tokens = tokenize(answer)

    if not q_tokens or not a_tokens:
        score = 0.0
        reason = "Empty query or answer tokens."
    else:
        # Query terms should be represented in answer; Jaccard is too harsh for long answers.
        score = coverage(a_tokens, q_tokens)
        reason = "Proxy answer relevance: query token coverage by answer."

    return RAGEvalMetricSchema(
        name="answer_relevance_proxy",
        score=clamp(score),
        reason=reason,
        details={
            "query_token_num": len(set(q_tokens)),
            "answer_token_num": len(set(a_tokens)),
        },
    )


def citation_coverage(answer: str, citations: List[Dict[str, Any]], retrieved_chunks: List[Dict[str, Any]]) -> RAGEvalMetricSchema:
    answer_chars = len(answer or "")

    if not answer.strip():
        return RAGEvalMetricSchema(name="citation_coverage", score=0.0, reason="Empty answer.")

    if not citations:
        return RAGEvalMetricSchema(
            name="citation_coverage",
            score=0.0,
            reason="No citations returned.",
            details={"citation_num": 0, "retrieved_chunk_num": len(retrieved_chunks)},
        )

    cited_chunk_ids = set()
    for c in citations:
        for key in ("chunk_id", "child_chunk_id", "parent_chunk_id"):
            value = c.get(key)
            if value:
                cited_chunk_ids.add(str(value))

    retrieved_ids = set()
    for chunk in retrieved_chunks:
        for key in ("matched_chunk_id", "context_chunk_id", "child_chunk_id", "parent_chunk_id", "chunk_id"):
            value = chunk.get(key)
            if value:
                retrieved_ids.add(str(value))

    citation_chunk_overlap = len(cited_chunk_ids & retrieved_ids) / len(cited_chunk_ids) if cited_chunk_ids else 0.0
    count_score = min(1.0, len(citations) / max(1, min(3, len(retrieved_chunks) or 3)))

    score = 0.7 * count_score + 0.3 * citation_chunk_overlap if cited_chunk_ids else count_score

    return RAGEvalMetricSchema(
        name="citation_coverage",
        score=clamp(score),
        reason="Citation count and citation-to-retrieval overlap proxy.",
        details={
            "citation_num": len(citations),
            "retrieved_chunk_num": len(retrieved_chunks),
            "cited_chunk_ids": sorted(cited_chunk_ids),
            "citation_chunk_overlap": citation_chunk_overlap,
            "answer_chars": answer_chars,
        },
    )


def completeness_proxy(answer: str, required_sections: List[str] | None = None) -> RAGEvalMetricSchema:
    required_sections = required_sections or []
    answer = answer or ""

    if not answer.strip():
        return RAGEvalMetricSchema(name="completeness_proxy", score=0.0, reason="Empty answer.")

    if not required_sections:
        # Fallback: non-trivial answer length.
        score = min(1.0, len(answer) / 800)
        return RAGEvalMetricSchema(
            name="completeness_proxy",
            score=clamp(score),
            reason="No required sections provided; score by answer length.",
            details={"answer_chars": len(answer)},
        )

    hit = []
    miss = []
    for section in required_sections:
        if section and section in answer:
            hit.append(section)
        else:
            miss.append(section)

    score = len(hit) / len(required_sections) if required_sections else 0.0

    return RAGEvalMetricSchema(
        name="completeness_proxy",
        score=clamp(score),
        reason="Required section coverage.",
        details={"required_sections": required_sections, "hit": hit, "miss": miss},
    )


def weighted_overall(metrics: Dict[str, RAGEvalMetricSchema]) -> float:
    weights = {
        "context_precision": 0.20,
        "context_recall_proxy": 0.15,
        "faithfulness_proxy": 0.25,
        "answer_relevance_proxy": 0.20,
        "citation_coverage": 0.10,
        "completeness_proxy": 0.10,
    }

    total = 0.0
    weight_total = 0.0
    for name, weight in weights.items():
        metric = metrics.get(name)
        if metric is None:
            continue
        total += metric.score * weight
        weight_total += weight

    return clamp(total / weight_total if weight_total else 0.0)
