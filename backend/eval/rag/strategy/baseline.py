"""Baseline selection and metric delta calculation."""
from __future__ import annotations

from .schemas import ComparisonRow, ExperimentReport


class BaselineManager:
    def __init__(self, baseline_experiment_id: str | None) -> None:
        self.baseline_experiment_id = baseline_experiment_id

    def select(
        self,
        reports: list[ExperimentReport],
    ) -> ExperimentReport | None:
        if not self.baseline_experiment_id:
            return None
        return next(
            (
                report
                for report in reports
                if report.experiment_id == self.baseline_experiment_id
            ),
            None,
        )

    def rows(
        self,
        reports: list[ExperimentReport],
        metrics: list[str],
    ) -> list[ComparisonRow]:
        baseline = self.select(reports)
        rows: list[ComparisonRow] = []
        for report in reports:
            deltas: dict[str, float | None] = {}
            for metric in metrics:
                current = report.aggregate_metrics.get(metric)
                base = baseline.aggregate_metrics.get(metric) if baseline else None
                deltas[metric] = (
                    float(current) - float(base)
                    if current is not None and base is not None
                    else None
                )
            rows.append(
                ComparisonRow(
                    experiment_id=report.experiment_id,
                    profile_id=report.profile_id,
                    status=report.status,
                    sample_count=report.sample_count,
                    success_count=report.success_count,
                    failure_count=report.failure_count,
                    metrics=report.aggregate_metrics,
                    baseline_deltas=deltas,
                    pipeline_config_hash=report.pipeline_config_hash,
                    duration_ms=report.duration_ms,
                )
            )
        return rows
