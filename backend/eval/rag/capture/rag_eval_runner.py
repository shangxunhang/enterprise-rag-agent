# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：_now_iso、_average、read_jsonl、RAGEvalRunner。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""RAGAS-compatible lightweight RAG eval runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .evaluator import RAGEvaluator
from .report_writer import write_reports
from .schemas import RAGEvalReportSchema, RAGEvalResultSchema


# 阅读注释（函数）：处理 now iso 相关逻辑。
def _now_iso() -> str:
    """处理 now iso 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 average 相关逻辑。
def _average(values: List[float]) -> float:
    """处理 average 相关逻辑。

    参数:
        values: values，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：sum, len。
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


# 阅读注释（函数）：读取 jsonl。
def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    """读取 jsonl。

    参数:
        path: 目标文件或目录路径。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：Path, path.exists, FileNotFoundError, splitlines, path.read_text, line.strip, records.append, json.loads。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Eval sample file not found: {path}")

    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


# 阅读注释（类）：封装 rageval runner，集中封装相关状态、依赖和行为。
class RAGEvalRunner:
    """封装 rageval runner，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 RAGEvalRunner，保存运行所需的依赖、配置或状态。
    def __init__(self, evaluator: Optional[RAGEvaluator] = None) -> None:
        """初始化 RAGEvalRunner，保存运行所需的依赖、配置或状态。

        参数:
            evaluator: evaluator，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：RAGEvaluator。
        """
        self.evaluator = evaluator or RAGEvaluator()

    # 阅读注释（函数）：评估 文件。
    def evaluate_file(
        self,
        eval_samples_path: str | Path,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> RAGEvalReportSchema:
        """评估 文件。

        参数:
            eval_samples_path: 评测 samples 路径，具体约束请结合类型标注和调用方确认。
            report_id: report 标识，具体约束请结合类型标注和调用方确认。
            run_id: 本次运行唯一标识。

        返回:
            RAGEvalReportSchema

        阅读提示:
            主要直接调用：Path, read_jsonl, enumerate, results.append, self.evaluator.evaluate, strftime, datetime.now, RAGEvalReportSchema。
        """
        eval_samples_path = Path(eval_samples_path)
        records = read_jsonl(eval_samples_path)

        results: List[RAGEvalResultSchema] = []
        for idx, record in enumerate(records, start=1):
            results.append(self.evaluator.evaluate(record, sample_index=idx))

        inferred_run_id = run_id
        if inferred_run_id is None and results:
            inferred_run_id = results[-1].run_id

        report_id = report_id or f"rag_eval_{inferred_run_id or datetime.now().strftime('%Y%m%d_%H%M%S')}"

        return RAGEvalReportSchema(
            report_id=report_id,
            run_id=inferred_run_id,
            source_path=str(eval_samples_path),
            created_at=_now_iso(),
            total=len(results),
            average_overall_score=_average([x.overall_score for x in results]),
            average_context_precision=_average([x.context_precision.score for x in results]),
            average_context_recall_proxy=_average([x.context_recall_proxy.score for x in results]),
            average_faithfulness_proxy=_average([x.faithfulness_proxy.score for x in results]),
            average_answer_relevance_proxy=_average([x.answer_relevance_proxy.score for x in results]),
            average_citation_coverage=_average([x.citation_coverage.score for x in results]),
            average_completeness_proxy=_average([x.completeness_proxy.score for x in results]),
            results=results,
            metadata={
                "runner": "RAGEvalRunner",
                "metric_profile": "ragas_compatible_proxy_v0.1",
            },
        )

    # 阅读注释（函数）：评估 and save。
    def evaluate_and_save(
        self,
        eval_samples_path: str | Path,
        output_dir: str | Path,
        report_name: Optional[str] = None,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """评估 and save。

        参数:
            eval_samples_path: 评测 samples 路径，具体约束请结合类型标注和调用方确认。
            output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
            report_name: report 名称，具体约束请结合类型标注和调用方确认。
            report_id: report 标识，具体约束请结合类型标注和调用方确认。
            run_id: 本次运行唯一标识。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.evaluate_file, write_reports, report.model_dump。
        """
        report = self.evaluate_file(
            eval_samples_path=eval_samples_path,
            report_id=report_id,
            run_id=run_id,
        )

        report_name = report_name or report.report_id
        output_paths = write_reports(report=report, output_dir=output_dir, report_name=report_name)

        return {
            "report": report.model_dump(),
            "output_paths": output_paths,
        }
