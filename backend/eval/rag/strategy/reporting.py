"""JSON, JSONL, CSV and Markdown outputs for strategy evaluation."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .schemas import ComparisonRow, ExperimentReport, MatrixReport


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json", exclude_none=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_experiment_samples(path: Path, report: ExperimentReport) -> None:
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


def write_comparison_csv(
    path: Path,
    rows: Iterable[ComparisonRow],
    metrics: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "experiment_id",
        "profile_id",
        "status",
        "sample_count",
        "success_count",
        "failure_count",
        *metrics,
        *[f"delta_{name}" for name in metrics],
        "pipeline_config_hash",
        "duration_ms",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            payload = {
                "experiment_id": row.experiment_id,
                "profile_id": row.profile_id,
                "status": row.status,
                "sample_count": row.sample_count,
                "success_count": row.success_count,
                "failure_count": row.failure_count,
                "pipeline_config_hash": row.pipeline_config_hash,
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


def _format_metric(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def write_comparison_markdown(
    path: Path,
    report: MatrixReport,
    directions: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = report.metrics
    header = ["Experiment", "Profile", "Status", *metrics]
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
            row.profile_id,
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
