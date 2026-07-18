"""Evaluator for task-level RAG capture records."""

from __future__ import annotations

from typing import Any, Dict, List

from .metrics import (
    answer_relevance_proxy,
    citation_coverage,
    completeness_proxy,
    context_precision,
    context_recall_proxy,
    faithfulness_proxy,
    extract_answer,
    extract_context_text,
    extract_query,
    weighted_overall,
)
from .schemas import RAGEvalResultSchema


class RAGEvaluator:
    """RAGAS-style lightweight evaluator.

    Input is one DataCaptureRecord-like dict from:
    data/captures/eval_samples/<run_id>_eval_samples.jsonl
    """

    def evaluate(self, sample: Dict[str, Any], sample_index: int = 1) -> RAGEvalResultSchema:
        query = extract_query(sample)
        answer = extract_answer(sample)
        context_text = extract_context_text(sample)

        retrieved_chunks = sample.get("retrieved_chunks") or []
        citations = sample.get("citations") or []
        eval_sample = sample.get("eval_sample") or {}

        if not retrieved_chunks and isinstance(eval_sample, dict):
            retrieved_chunks = eval_sample.get("retrieved_chunks") or []
        if not citations and isinstance(eval_sample, dict):
            citations = eval_sample.get("citations") or []

        required_sections: List[str] = []
        if isinstance(eval_sample, dict):
            required_sections = eval_sample.get("required_sections") or []
        if not required_sections:
            label = sample.get("label") or {}
            if isinstance(label, dict):
                required_sections = label.get("required_sections") or []

        m_context_precision = context_precision(query=query, retrieved_chunks=retrieved_chunks)
        m_context_recall = context_recall_proxy(
            query=query,
            context_text=context_text,
            retrieved_chunks=retrieved_chunks,
        )
        m_faithfulness = faithfulness_proxy(answer=answer, context_text=context_text)
        m_answer_relevance = answer_relevance_proxy(query=query, answer=answer)
        m_citation_coverage = citation_coverage(
            answer=answer,
            citations=citations,
            retrieved_chunks=retrieved_chunks,
        )
        m_completeness = completeness_proxy(answer=answer, required_sections=required_sections)

        metric_map = {
            "context_precision": m_context_precision,
            "context_recall_proxy": m_context_recall,
            "faithfulness_proxy": m_faithfulness,
            "answer_relevance_proxy": m_answer_relevance,
            "citation_coverage": m_citation_coverage,
            "completeness_proxy": m_completeness,
        }
        overall = weighted_overall(metric_map)

        quality_flags: List[str] = []
        if m_context_precision.score < 0.3:
            quality_flags.append("low_context_precision")
        if m_faithfulness.score < 0.3:
            quality_flags.append("low_faithfulness_proxy")
        if m_citation_coverage.score < 0.5:
            quality_flags.append("low_citation_coverage")
        if overall < 0.5:
            quality_flags.append("need_human_review")

        sample_id = sample.get("record_id") or f"sample_{sample_index:04d}"

        return RAGEvalResultSchema(
            sample_id=sample_id,
            task_id=sample.get("task_id"),
            run_id=sample.get("run_id"),
            capture_type=sample.get("capture_type"),
            query=query,
            answer=answer,
            context_precision=m_context_precision,
            context_recall_proxy=m_context_recall,
            faithfulness_proxy=m_faithfulness,
            answer_relevance_proxy=m_answer_relevance,
            citation_coverage=m_citation_coverage,
            completeness_proxy=m_completeness,
            overall_score=overall,
            need_human_review=overall < 0.75 or bool(quality_flags),
            quality_flags=quality_flags,
            retrieved_chunk_num=len(retrieved_chunks),
            citation_num=len(citations),
            context_chars=len(context_text),
            answer_chars=len(answer),
            metadata={
                "evaluator": "RAGEvaluator",
                "metric_profile": "ragas_compatible_proxy_v0.1",
            },
            extra={
                "required_sections": required_sections,
                "source_record_schema_version": sample.get("schema_version"),
            },
        )
