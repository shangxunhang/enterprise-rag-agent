"""把一条 DataCapture 风格的 RAG 样本转换为可解释评测结果。"""

from __future__ import annotations

from typing import Any, Dict, List

from .metrics import (
    answer_relevance_proxy,
    citation_coverage,
    completeness_proxy,
    context_precision,
    context_recall_proxy,
    extract_answer,
    extract_context_text,
    extract_query,
    faithfulness_proxy,
    weighted_overall,
)
from .schemas import RAGEvalResultSchema


class RAGEvaluator:
    """使用确定性词法代理指标评估任务级 RAG 采集记录。

    输入通常来自 ``data/captures/eval_samples/<run_id>_eval_samples.jsonl``。
    评估器兼容显式顶层字段和旧版 ``eval_sample`` 嵌套字段，但计算时只使用一份
    归一化数据，避免同一证据被重复计数。
    """

    def evaluate(
        self,
        sample: Dict[str, Any],
        sample_index: int = 1,
    ) -> RAGEvalResultSchema:
        """计算单个采集样本的六项指标、总分和复核标记。

        Args:
            sample: DataCaptureRecord 风格字典，可包含查询、回答、上下文、召回块、
                引用、黄金章节和来源元数据。
            sample_index: 当样本缺少 ``record_id`` 时生成稳定回退标识所用的序号。

        Returns:
            完整 ``RAGEvalResultSchema``，包含指标解释、规模统计和质量标记。
        """

        # 第一步：通过统一提取函数兼容不同采集版本中的查询、回答和上下文字段。
        query = extract_query(sample)
        answer = extract_answer(sample)
        context_text = extract_context_text(sample)

        # 第二步：优先使用顶层显式召回与引用；旧记录再回退到 eval_sample 内部。
        retrieved_chunks = sample.get("retrieved_chunks") or []
        citations = sample.get("citations") or []
        eval_sample = sample.get("eval_sample") or {}

        if not retrieved_chunks and isinstance(eval_sample, dict):
            retrieved_chunks = eval_sample.get("retrieved_chunks") or []
        if not citations and isinstance(eval_sample, dict):
            citations = eval_sample.get("citations") or []

        # 第三步：从评测样本读取结构完整度要求，旧记录可从 label 回退恢复。
        required_sections: List[str] = []
        if isinstance(eval_sample, dict):
            required_sections = eval_sample.get("required_sections") or []
        if not required_sections:
            label = sample.get("label") or {}
            if isinstance(label, dict):
                required_sections = label.get("required_sections") or []

        # 第四步：分别计算检索精度、召回代理、忠实度代理和回答相关性代理。
        context_precision_metric = context_precision(
            query=query,
            retrieved_chunks=retrieved_chunks,
        )
        context_recall_metric = context_recall_proxy(
            query=query,
            context_text=context_text,
            retrieved_chunks=retrieved_chunks,
        )
        faithfulness_metric = faithfulness_proxy(
            answer=answer,
            context_text=context_text,
        )
        answer_relevance_metric = answer_relevance_proxy(
            query=query,
            answer=answer,
        )

        # 第五步：独立计算引用覆盖和章节完整度，避免被文本重叠分数间接替代。
        citation_coverage_metric = citation_coverage(
            answer=answer,
            citations=citations,
            retrieved_chunks=retrieved_chunks,
        )
        completeness_metric = completeness_proxy(
            answer=answer,
            required_sections=required_sections,
        )

        # 第六步：按稳定指标名称构造权重输入并计算归一化总分。
        metric_map = {
            "context_precision": context_precision_metric,
            "context_recall_proxy": context_recall_metric,
            "faithfulness_proxy": faithfulness_metric,
            "answer_relevance_proxy": answer_relevance_metric,
            "citation_coverage": citation_coverage_metric,
            "completeness_proxy": completeness_metric,
        }
        overall = weighted_overall(metric_map)

        # 第七步：根据关键维度阈值生成可筛选质量标记。
        quality_flags: List[str] = []
        if context_precision_metric.score < 0.3:
            quality_flags.append("low_context_precision")
        if faithfulness_metric.score < 0.3:
            quality_flags.append("low_faithfulness_proxy")
        if citation_coverage_metric.score < 0.5:
            quality_flags.append("low_citation_coverage")
        if overall < 0.5:
            quality_flags.append("need_human_review")

        # 第八步：优先沿用采集记录身份，缺失时按输入顺序生成回退样本标识。
        sample_id = sample.get("record_id") or f"sample_{sample_index:04d}"

        # 第九步：返回结构化结果；人工复核采用更保守的 0.75 阈值或任一质量标记。
        return RAGEvalResultSchema(
            sample_id=sample_id,
            task_id=sample.get("task_id"),
            run_id=sample.get("run_id"),
            capture_type=sample.get("capture_type"),
            query=query,
            answer=answer,
            context_precision=context_precision_metric,
            context_recall_proxy=context_recall_metric,
            faithfulness_proxy=faithfulness_metric,
            answer_relevance_proxy=answer_relevance_metric,
            citation_coverage=citation_coverage_metric,
            completeness_proxy=completeness_metric,
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
