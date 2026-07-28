"""把 RAG 策略评测结果写为 JSON、JSONL、CSV 和 Markdown。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .schemas import ComparisonRow, ExperimentReport, MatrixReport


def write_json(path: Path, payload: object) -> None:
    """将任意可序列化对象写为缩进 JSON。

    Args:
        path: 目标文件路径。
        payload: 普通 Python 对象或提供 ``model_dump`` 的 Pydantic 对象。
    """

    # 第一步：创建父目录，允许调用方直接传入尚不存在的输出层级。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：Pydantic 对象先转换为 JSON 兼容投影，并省略 None 字段减少噪声。
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json", exclude_none=True)

    # 第三步：保留中文并使用缩进，便于审计单个实验或矩阵报告。
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_experiment_samples(path: Path, report: ExperimentReport) -> None:
    """把逐样本实验结果写为 JSONL。

    Args:
        path: JSONL 目标路径。
        report: 包含逐样本结果的实验报告。

    每行独立保存一个 ``SampleMetricResult``，便于流式处理、失败筛选和跨实验拼接。
    """

    # 第一步：确保目标目录存在。
    path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：按报告中的样本顺序逐行写入，保持与原数据集顺序一致。
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
    """写出适合表格分析的实验对比 CSV。

    Args:
        path: CSV 目标路径。
        rows: 每个实验对应一条的对比记录。
        metrics: 需要输出绝对值和基线增量的指标顺序。
    """

    # 第一步：建立固定身份列、动态指标列和对应 delta 列。
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

    # 第二步：使用 UTF-8 BOM，保证 Excel 直接打开时中文不乱码。
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        # 第三步：逐行拼接固定字段、指标值和基线差值。
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


def _format_metric(value: float | None) -> str:
    """把可选指标格式化为四位小数或缺失标记。"""

    # None 表示没有可比较值，不能显示为 0.0000。
    return "-" if value is None else f"{value:.4f}"


def write_comparison_markdown(
    path: Path,
    report: MatrixReport,
    directions: dict[str, str],
) -> None:
    """写出面向人工阅读的策略矩阵对比摘要。

    Args:
        path: Markdown 目标路径。
        report: 完整矩阵报告。
        directions: 指标名称到 ``higher/lower/neutral`` 等方向说明的映射。

    Markdown 只展示绝对指标，不展开逐样本明细和基线 delta；完整数据仍由 JSON、
    JSONL 和 CSV 产物承载。
    """

    # 第一步：准备输出目录和动态表头。
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = report.metrics
    header = ["Experiment", "Static spec", "Status", *metrics]

    # 第二步：写入矩阵身份、数据版本和基线信息，保证报告可复现。
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

    # 第三步：按矩阵报告顺序渲染实验行，缺失指标显示为短横线。
    for row in report.rows:
        values = [
            row.experiment_id,
            row.static_spec_id,
            row.status,
            *[_format_metric(row.metrics.get(name)) for name in metrics],
        ]
        lines.append("| " + " | ".join(values) + " |")

    # 第四步：附加每个指标的优化方向，避免读者把 latency 等指标方向看反。
    lines.extend(["", "## Metric directions", ""])
    for name in metrics:
        lines.append(f"- `{name}`: {directions.get(name, 'neutral')}")

    # 第五步：记录配置哈希和运行时间，形成最小可重复性信息。
    lines.extend(["", "## Reproducibility", ""])
    lines.append(f"- Matrix config SHA256: `{report.matrix_config_hash}`")
    lines.append(f"- Started at: `{report.started_at}`")
    lines.append(f"- Finished at: `{report.finished_at}`")

    # 第六步：确保文件以换行结尾，便于版本控制和命令行工具处理。
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
