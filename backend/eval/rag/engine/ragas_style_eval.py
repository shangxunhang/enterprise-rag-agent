# -*- coding: utf-8 -*-
"""
rag_template/eval/ragas_style_eval.py
=====================================

RAGAS-style proxy evaluation for parent-child RAG QA.

This is NOT the official ragas package. It is a local, deterministic, dependency-free
proxy evaluator designed for engineering regression tests.

Why proxy metrics first?
- They run locally without external LLM judge calls.
- They are stable and cheap for CI/regression.
- Their output structure is aligned with RAGAS-style dimensions, so the evaluator
  can later be replaced by DeepSeek-as-judge or official ragas metrics.

Implemented dimensions:
1. context_precision_proxy
2. context_recall_proxy
3. faithfulness_proxy
4. answer_relevancy_proxy
5. citation_hit_proxy
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from eval.rag.engine.rag_eval_case_schema import RagEvalCase


def _as_list(values: Optional[Sequence[str]]) -> List[str]:
    if not values:
        return []
    return [str(x).strip() for x in values if str(x).strip()]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_text(text: Any) -> str:
    if text is None:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _keyword_hit_ratio(keywords: Sequence[str], text: str) -> float:
    kws = _as_list(keywords)
    if not kws:
        return 0.0
    if not text:
        return 0.0
    hit = sum(1 for kw in kws if kw and kw in text)
    return hit / len(kws)


def _first_text_field(item: Dict[str, Any], fields: Sequence[str] = ("text", "parent_text", "child_text")) -> str:
    for field in fields:
        value = item.get(field)
        if value:
            return _normalize_text(value)
    return ""


def _top_k_results(results: Sequence[Dict[str, Any]], top_k: Optional[int]) -> List[Dict[str, Any]]:
    if top_k is None or top_k <= 0:
        return list(results)
    return list(results)[: int(top_k)]


def _collect_context_text_from_results(results: Sequence[Dict[str, Any]], top_k: Optional[int] = None) -> str:
    parts: List[str] = []
    for item in _top_k_results(results, top_k):
        text = _first_text_field(item)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _collect_context_text_from_run(run_record: Dict[str, Any]) -> str:
    packed_context = _normalize_text(run_record.get("packed_context"))
    if packed_context:
        return packed_context

    context_pack = run_record.get("context_pack") or {}
    if isinstance(context_pack, dict):
        context = _normalize_text(context_pack.get("context"))
        if context:
            return context
        selected_results = context_pack.get("selected_results") or []
        if isinstance(selected_results, list):
            return _collect_context_text_from_results(selected_results)

    retrieval_results = run_record.get("retrieval_results") or []
    if isinstance(retrieval_results, list):
        return _collect_context_text_from_results(retrieval_results)
    return ""


def _result_relevance_score(item: Dict[str, Any], case: RagEvalCase) -> float:
    """Return a deterministic relevance score in [0, 1] for one retrieved item."""
    doc_id = str(item.get("doc_id") or "")
    parent_id = str(item.get("parent_chunk_id") or "")
    child_id = str(item.get("child_chunk_id") or item.get("chunk_id") or "")
    text = _first_text_field(item)

    scores: List[float] = []
    if case.expected_child_chunk_ids and child_id in set(case.expected_child_chunk_ids):
        scores.append(1.0)
    if case.expected_parent_chunk_ids and parent_id in set(case.expected_parent_chunk_ids):
        scores.append(1.0)
    if case.expected_doc_ids and doc_id in set(case.expected_doc_ids):
        scores.append(0.75)
    if case.expected_keywords:
        kw_score = _keyword_hit_ratio(case.expected_keywords, text)
        if kw_score > 0:
            # Keyword-only relevance is useful but weaker than exact id matching.
            scores.append(0.35 + 0.25 * kw_score)
    return _clip01(max(scores) if scores else 0.0)


def compute_context_precision_proxy(
    retrieval_results: Sequence[Dict[str, Any]],
    case: RagEvalCase,
    *,
    top_k: Optional[int] = None,
    relevant_threshold: float = 0.35,
) -> Tuple[float, List[Dict[str, Any]]]:
    """Approximate RAGAS Context Precision with average precision over ranked contexts."""
    ranked = _top_k_results(retrieval_results, top_k)
    if not ranked:
        return 0.0, []

    details: List[Dict[str, Any]] = []
    relevant_seen = 0
    precision_sum = 0.0

    for idx, item in enumerate(ranked, start=1):
        rel = _result_relevance_score(item, case)
        is_relevant = rel >= relevant_threshold
        if is_relevant:
            relevant_seen += 1
            precision_sum += relevant_seen / idx
        details.append({
            "rank": idx,
            "doc_id": item.get("doc_id"),
            "parent_chunk_id": item.get("parent_chunk_id"),
            "child_chunk_id": item.get("child_chunk_id") or item.get("chunk_id"),
            "relevance_score": rel,
            "is_relevant": is_relevant,
        })

    if relevant_seen == 0:
        return 0.0, details
    return _clip01(precision_sum / relevant_seen), details


def _id_recall(expected: Sequence[str], observed: Sequence[str]) -> Optional[float]:
    expected_set = {str(x) for x in expected if str(x)}
    if not expected_set:
        return None
    observed_set = {str(x) for x in observed if str(x)}
    return len(expected_set & observed_set) / len(expected_set)


def compute_context_recall_proxy(
    retrieval_results: Sequence[Dict[str, Any]],
    case: RagEvalCase,
    *,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """Approximate RAGAS Context Recall with id recall + keyword recall."""
    ranked = _top_k_results(retrieval_results, top_k)
    docs = [str(x.get("doc_id") or "") for x in ranked]
    parents = [str(x.get("parent_chunk_id") or "") for x in ranked]
    children = [str(x.get("child_chunk_id") or x.get("chunk_id") or "") for x in ranked]
    context_text = _collect_context_text_from_results(ranked)

    components: Dict[str, Optional[float]] = {
        "doc_recall": _id_recall(case.expected_doc_ids, docs),
        "parent_recall": _id_recall(case.expected_parent_chunk_ids, parents),
        "child_recall": _id_recall(case.expected_child_chunk_ids, children),
        "keyword_recall": _keyword_hit_ratio(case.expected_keywords, context_text) if case.expected_keywords else None,
    }
    valid_values = [v for v in components.values() if v is not None]
    score = mean(valid_values) if valid_values else 0.0
    return {
        "score": _clip01(score),
        "components": components,
        "observed_doc_ids": docs,
        "observed_parent_chunk_ids": parents,
        "observed_child_chunk_ids": children,
    }


def compute_faithfulness_proxy(
    *,
    answer: str,
    context_text: str,
    case: RagEvalCase,
) -> Dict[str, Any]:
    """Approximate faithfulness by checking whether answer keywords are grounded in context."""
    answer = _normalize_text(answer)
    context_text = _normalize_text(context_text)
    keywords = case.answer_keywords or case.expected_keywords
    keywords = _as_list(keywords)
    if not answer or not context_text or not keywords:
        return {
            "score": 0.0,
            "supported_keywords": [],
            "unsupported_keywords": [],
            "evaluated_keywords": keywords,
            "note": "No answer/context/keywords available for proxy faithfulness.",
        }

    supported: List[str] = []
    unsupported: List[str] = []
    evaluated: List[str] = []
    for kw in keywords:
        if kw in answer:
            evaluated.append(kw)
            if kw in context_text:
                supported.append(kw)
            else:
                unsupported.append(kw)

    if not evaluated:
        return {
            "score": 0.0,
            "supported_keywords": [],
            "unsupported_keywords": [],
            "evaluated_keywords": [],
            "note": "None of the answer keywords appeared in answer.",
        }
    return {
        "score": _clip01(len(supported) / len(evaluated)),
        "supported_keywords": supported,
        "unsupported_keywords": unsupported,
        "evaluated_keywords": evaluated,
    }


def compute_answer_relevancy_proxy(*, answer: str, query: str, case: RagEvalCase) -> Dict[str, Any]:
    """Approximate answer relevancy with required answer keyword coverage and non-empty answer."""
    answer = _normalize_text(answer)
    query = _normalize_text(query)
    keywords = case.answer_keywords or case.expected_keywords
    keywords = _as_list(keywords)

    if not answer:
        return {"score": 0.0, "answer_keyword_hit": 0.0, "non_empty_answer": 0.0}

    if keywords:
        answer_keyword_hit = _keyword_hit_ratio(keywords, answer)
    else:
        # Fallback: if no labels exist, only judge whether answer is non-empty.
        answer_keyword_hit = 1.0

    # Very light query anchor: keep it weak because Chinese queries like "xxx是什么" should
    # not require the whole query string to appear verbatim.
    query_anchor = 1.0 if query and any(ch in answer for ch in query if "\u4e00" <= ch <= "\u9fff") else 0.0
    score = 0.85 * answer_keyword_hit + 0.15 * query_anchor
    return {
        "score": _clip01(score),
        "answer_keyword_hit": _clip01(answer_keyword_hit),
        "query_anchor_hit": query_anchor,
        "evaluated_keywords": keywords,
    }


def compute_citation_hit_proxy(run_record: Dict[str, Any], case: RagEvalCase) -> Dict[str, Any]:
    expected = set(case.expected_doc_ids)
    if not expected:
        return {"score": 0.0, "expected_doc_ids": [], "cited_doc_ids": []}

    cited: List[str] = []
    for citation in run_record.get("citations") or []:
        if isinstance(citation, dict) and citation.get("doc_id"):
            cited.append(str(citation.get("doc_id")))
    # If explicit citations are missing, fallback to selected context docs.
    if not cited:
        context_pack = run_record.get("context_pack") or {}
        for item in context_pack.get("selected_results") or []:
            if isinstance(item, dict) and item.get("doc_id"):
                cited.append(str(item.get("doc_id")))

    cited_set = set(cited)
    return {
        "score": 1.0 if expected & cited_set else 0.0,
        "expected_doc_ids": sorted(expected),
        "cited_doc_ids": cited,
    }


def evaluate_ragas_style_proxy(
    *,
    run_record: Dict[str, Any],
    eval_case: RagEvalCase,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate one captured RAG run with local RAGAS-style proxy metrics."""
    retrieval_results = run_record.get("retrieval_results") or []
    if not isinstance(retrieval_results, list):
        retrieval_results = []

    query = str(run_record.get("query") or eval_case.query)
    answer = _normalize_text(run_record.get("answer"))
    context_text = _collect_context_text_from_run(run_record)

    context_precision, rank_details = compute_context_precision_proxy(
        retrieval_results,
        eval_case,
        top_k=top_k,
    )
    context_recall = compute_context_recall_proxy(
        retrieval_results,
        eval_case,
        top_k=top_k,
    )
    faithfulness = compute_faithfulness_proxy(
        answer=answer,
        context_text=context_text,
        case=eval_case,
    )
    answer_relevancy = compute_answer_relevancy_proxy(
        answer=answer,
        query=query,
        case=eval_case,
    )
    citation_hit = compute_citation_hit_proxy(run_record, eval_case)

    metrics = {
        "context_precision_proxy": context_precision,
        "context_recall_proxy": context_recall["score"],
        "faithfulness_proxy": faithfulness["score"],
        "answer_relevancy_proxy": answer_relevancy["score"],
        "citation_hit_proxy": citation_hit["score"],
    }

    return {
        "schema_version": "ragas_style_proxy_eval_v1",
        "case_id": eval_case.case_id,
        "run_id": run_record.get("run_id"),
        "query": query,
        "top_k": top_k,
        "metrics": metrics,
        "details": {
            "context_precision_rank_details": rank_details,
            "context_recall": context_recall,
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "citation_hit": citation_hit,
        },
        "eval_case": eval_case.to_dict(),
    }


def aggregate_ragas_style_proxy(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of per-run RAGAS-style proxy results."""
    metric_names = [
        "context_precision_proxy",
        "context_recall_proxy",
        "faithfulness_proxy",
        "answer_relevancy_proxy",
        "citation_hit_proxy",
    ]
    summary: Dict[str, Any] = {
        "schema_version": "ragas_style_proxy_eval_report_v1",
        "num_samples": len(results),
    }
    for name in metric_names:
        values = [
            _safe_float((item.get("metrics") or {}).get(name), 0.0)
            for item in results
        ]
        summary[f"avg_{name}"] = mean(values) if values else 0.0
    return summary


def load_jsonl_records(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSONL file not found: {p}")
    records: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"Line {line_no} in {p} must be a JSON object")
            records.append(obj)
    return records


def write_json(path: str | Path, data: Dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_jsonl(path: str | Path, records: Iterable[Dict[str, Any]]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for item in records:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return p
