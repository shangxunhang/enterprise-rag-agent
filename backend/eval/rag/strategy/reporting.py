# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：write_json、write_experiment_samples、write_comparison_csv、_format_metric、write_comparison_markdown。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""JSON, JSONL, CSV and Markdown outputs for strategy evaluation."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .schemas import ComparisonRow, ExperimentReport, MatrixReport


# 阅读注释（函数）：写入 JSON。
def write_json(path: Path, payload: object) -> None:
    """写入 JSON。

    参数:
        path: 目标文件或目录路径。
        payload: 跨层传递的数据载荷。

    返回:
        None

    阅读提示:
        主要直接调用：path.parent.mkdir, hasattr, payload.model_dump, path.write_text, json.dumps。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json", exclude_none=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# 阅读注释（函数）：写入 experiment samples。
def write_experiment_samples(path: Path, report: ExperimentReport) -> None:
    """写入 experiment samples。

    参数:
        path: 目标文件或目录路径。
        report: report，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：path.parent.mkdir, path.open, handle.write, json.dumps, sample.model_dump。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for sample in report.samples:
            handle.write(
                json.dumps(
                    sample.model_dump(mode="json", exclude_none=True),
                    ensure_ascii=False,
                )
                + "\n"
            )


# 阅读注释（函数）：写入 comparison csv。
def write_comparison_csv(
    path: Path,
    rows: Iterable[ComparisonRow],
    metrics: list[str],
) -> None:
    """写入 comparison csv。

    参数:
        path: 目标文件或目录路径。
        rows: rows，具体约束请结合类型标注和调用方确认。
        metrics: 指标，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：path.parent.mkdir, path.open, csv.DictWriter, writer.writeheader, payload.update, row.metrics.get, row.baseline_deltas.get, writer.writerow。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "experiment_id",
        "static_spec_id",
        "status",
        "sample_count",
        "success_count",
        "failure_count",
        *metrics,
        *[f"delta_{name}" for name in metrics],
        "static_retrieval_spec_hash",
        "duration_ms",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            payload = {
                "experiment_id": row.experiment_id,
                "static_spec_id": row.static_spec_id,
                "status": row.status,
                "sample_count": row.sample_count,
                "success_count": row.success_count,
                "failure_count": row.failure_count,
                "static_retrieval_spec_hash": row.static_retrieval_spec_hash,
                "duration_ms": row.duration_ms,
            }
            payload.update({name: row.metrics.get(name) for name in metrics})
            payload.update(
                {
                    f"delta_{name}": row.baseline_deltas.get(name)
                    for name in metrics
                }
            )
            writer.writerow(payload)


# 阅读注释（函数）：格式化 指标。
def _format_metric(value: float | None) -> str:
    """格式化 指标。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        str
    """
    return "-" if value is None else f"{value:.4f}"


# 阅读注释（函数）：写入 comparison markdown。
def write_comparison_markdown(
    path: Path,
    report: MatrixReport,
    directions: dict[str, str],
) -> None:
    """写入 comparison markdown。

    参数:
        path: 目标文件或目录路径。
        report: report，具体约束请结合类型标注和调用方确认。
        directions: directions，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：path.parent.mkdir, join, len, _format_metric, row.metrics.get, lines.append, lines.extend, directions.get。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = report.metrics
    header = ["Experiment", "Static spec", "Status", *metrics]
    lines = [
        f"# RAG Strategy Comparison: {report.matrix_id}",
        "",
        f"- Matrix run ID: `{report.matrix_run_id}`",
        f"- Dataset version: `{report.dataset_version}`",
        f"- Eval set version: `{report.eval_set_version}`",
        f"- Index version: `{report.index_version}`",
        f"- Dataset SHA256: `{report.dataset_hash}`",
        f"- Baseline: `{report.baseline_experiment_id or 'none'}`",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in report.rows:
        values = [
            row.experiment_id,
            row.static_spec_id,
            row.status,
            *[_format_metric(row.metrics.get(name)) for name in metrics],
        ]
        lines.append("| " + " | ".join(values) + " |")
    lines.extend(["", "## Metric directions", ""])
    for name in metrics:
        lines.append(f"- `{name}`: {directions.get(name, 'neutral')}")
    lines.extend(["", "## Reproducibility", ""])
    lines.append(f"- Matrix config SHA256: `{report.matrix_config_hash}`")
    lines.append(f"- Started at: `{report.started_at}`")
    lines.append(f"- Finished at: `{report.finished_at}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
