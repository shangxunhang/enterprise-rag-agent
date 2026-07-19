# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：write_json_report、write_markdown_report、write_reports。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Write RAG eval reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .schemas import RAGEvalReportSchema


# 阅读注释（函数）：写入 JSON report。
def write_json_report(report: RAGEvalReportSchema, output_path: str | Path) -> Path:
    """写入 JSON report。

    参数:
        report: report，具体约束请结合类型标注和调用方确认。
        output_path: 输出 路径，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：Path, path.parent.mkdir, path.write_text, json.dumps, report.model_dump。
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# 阅读注释（函数）：写入 markdown report。
def write_markdown_report(report: RAGEvalReportSchema, output_path: str | Path) -> Path:
    """写入 markdown report。

    参数:
        report: report，具体约束请结合类型标注和调用方确认。
        output_path: 输出 路径，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：Path, path.parent.mkdir, lines.append, enumerate, join, format, path.write_text。
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append(f"# RAG Eval Report")
    lines.append("")
    lines.append(f"- report_id: `{report.report_id}`")
    lines.append(f"- run_id: `{report.run_id}`")
    lines.append(f"- source_path: `{report.source_path}`")
    lines.append(f"- created_at: `{report.created_at}`")
    lines.append(f"- total: **{report.total}**")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Score |")
    lines.append("|---|---:|")
    lines.append(f"| overall | {report.average_overall_score:.4f} |")
    lines.append(f"| context_precision | {report.average_context_precision:.4f} |")
    lines.append(f"| context_recall_proxy | {report.average_context_recall_proxy:.4f} |")
    lines.append(f"| faithfulness_proxy | {report.average_faithfulness_proxy:.4f} |")
    lines.append(f"| answer_relevance_proxy | {report.average_answer_relevance_proxy:.4f} |")
    lines.append(f"| citation_coverage | {report.average_citation_coverage:.4f} |")
    lines.append(f"| completeness_proxy | {report.average_completeness_proxy:.4f} |")
    lines.append("")

    lines.append("## Samples")
    lines.append("")
    lines.append("| # | sample_id | overall | ctx_precision | faithfulness | answer_rel | citation | flags |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|---|")

    for idx, result in enumerate(report.results, start=1):
        flags = ", ".join(result.quality_flags) if result.quality_flags else "-"
        lines.append(
            "| {idx} | `{sample_id}` | {overall:.4f} | {cp:.4f} | {faith:.4f} | {ar:.4f} | {cit:.4f} | {flags} |".format(
                idx=idx,
                sample_id=result.sample_id,
                overall=result.overall_score,
                cp=result.context_precision.score,
                faith=result.faithfulness_proxy.score,
                ar=result.answer_relevance_proxy.score,
                cit=result.citation_coverage.score,
                flags=flags,
            )
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("This is a RAGAS-compatible lightweight proxy report. Scores are deterministic lexical proxies, not LLM-judge scores.")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# 阅读注释（函数）：写入 reports。
def write_reports(report: RAGEvalReportSchema, output_dir: str | Path, report_name: str) -> Dict[str, str]:
    """写入 reports。

    参数:
        report: report，具体约束请结合类型标注和调用方确认。
        output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
        report_name: report 名称，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, str]

    阅读提示:
        主要直接调用：Path, write_json_report, write_markdown_report, str。
    """
    output_dir = Path(output_dir)
    json_path = write_json_report(report, output_dir / f"{report_name}.json")
    md_path = write_markdown_report(report, output_dir / f"{report_name}.md")
    return {"json": str(json_path), "markdown": str(md_path)}
