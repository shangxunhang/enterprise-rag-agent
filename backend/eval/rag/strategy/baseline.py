# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：BaselineManager。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Baseline selection and metric delta calculation."""
from __future__ import annotations

from .schemas import ComparisonRow, ExperimentReport


# 阅读注释（类）：封装 baseline 管理器，集中封装相关状态、依赖和行为。
class BaselineManager:
    """封装 baseline 管理器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 BaselineManager，保存运行所需的依赖、配置或状态。
    def __init__(self, baseline_experiment_id: str | None) -> None:
        """初始化 BaselineManager，保存运行所需的依赖、配置或状态。

        参数:
            baseline_experiment_id: baseline experiment 标识，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.baseline_experiment_id = baseline_experiment_id

    # 阅读注释（函数）：选择 BaselineManager。
    def select(
        self,
        reports: list[ExperimentReport],
    ) -> ExperimentReport | None:
        """选择 BaselineManager。

        参数:
            reports: reports，具体约束请结合类型标注和调用方确认。

        返回:
            ExperimentReport | None

        阅读提示:
            主要直接调用：next。
        """
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

    # 阅读注释（函数）：处理 rows 相关逻辑。
    def rows(
        self,
        reports: list[ExperimentReport],
        metrics: list[str],
    ) -> list[ComparisonRow]:
        """处理 rows 相关逻辑。

        参数:
            reports: reports，具体约束请结合类型标注和调用方确认。
            metrics: 指标，具体约束请结合类型标注和调用方确认。

        返回:
            list[ComparisonRow]

        阅读提示:
            主要直接调用：self.select, report.aggregate_metrics.get, baseline.aggregate_metrics.get, float, rows.append, ComparisonRow。
        """
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
                    static_spec_id=report.static_spec_id,
                    status=report.status,
                    sample_count=report.sample_count,
                    success_count=report.success_count,
                    failure_count=report.failure_count,
                    metrics=report.aggregate_metrics,
                    baseline_deltas=deltas,
                    static_retrieval_spec_hash=(
                        report.static_retrieval_spec_hash
                    ),
                    duration_ms=report.duration_ms,
                )
            )
        return rows
