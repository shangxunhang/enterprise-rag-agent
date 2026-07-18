"""RAGAS-compatible lightweight RAG eval runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .evaluator import RAGEvaluator
from .report_writer import write_reports
from .schemas import RAGEvalReportSchema, RAGEvalResultSchema


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _average(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
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


class RAGEvalRunner:
    def __init__(self, evaluator: Optional[RAGEvaluator] = None) -> None:
        self.evaluator = evaluator or RAGEvaluator()

    def evaluate_file(
        self,
        eval_samples_path: str | Path,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> RAGEvalReportSchema:
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

    def evaluate_and_save(
        self,
        eval_samples_path: str | Path,
        output_dir: str | Path,
        report_name: Optional[str] = None,
        report_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
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
