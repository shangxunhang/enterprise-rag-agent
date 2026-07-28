"""选择策略评测基线并计算实验相对增量。"""

from __future__ import annotations

from .schemas import ComparisonRow, ExperimentReport


class BaselineManager:
    """基于 experiment_id 管理基线报告和对比表。

    Attributes:
        baseline_experiment_id: 配置指定的基线实验标识。为空时不计算任何增量，
            但仍生成所有实验的绝对指标行。
    """

    def __init__(self, baseline_experiment_id: str | None) -> None:
        """保存可选基线实验标识。"""

        self.baseline_experiment_id = baseline_experiment_id

    def select(
        self,
        reports: list[ExperimentReport],
    ) -> ExperimentReport | None:
        """从报告列表中选择配置指定的基线。

        Args:
            reports: 本次矩阵执行产生的实验报告。

        Returns:
            experiment_id 匹配的首个报告；未配置或未找到时返回 ``None``。
        """

        # 未配置基线时显式返回 None，调用方只展示绝对指标。
        if not self.baseline_experiment_id:
            return None

        # 按报告原顺序查找，experiment_id 唯一性由配置 Schema 负责保证。
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
        """构建每个实验相对基线的指标对比行。

        Args:
            reports: 待比较实验报告。
            metrics: 需要计算 delta 的稳定指标名称列表。

        Returns:
            与 ``reports`` 顺序一致的对比行。当前值或基线值缺失时，相应 delta
            设为 ``None``，不把“缺失”误写成零改进。
        """

        # 第一步：只选择一次基线，保证整张表使用同一参考对象。
        baseline = self.select(reports)
        rows: list[ComparisonRow] = []

        # 第二步：逐实验计算指定指标相对基线的绝对差值。
        for report in reports:
            deltas: dict[str, float | None] = {}
            for metric in metrics:
                current = report.aggregate_metrics.get(metric)
                base = (
                    baseline.aggregate_metrics.get(metric)
                    if baseline is not None
                    else None
                )
                deltas[metric] = (
                    float(current) - float(base)
                    if current is not None and base is not None
                    else None
                )

            # 第三步：保留实验身份、静态检索配置哈希和运行统计，形成完整对比记录。
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
