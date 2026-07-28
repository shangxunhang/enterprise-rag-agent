"""将轻量 RAG 评测报告写为 JSON 和 Markdown。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .schemas import RAGEvalReportSchema


def write_json_report(
    report: RAGEvalReportSchema,
    output_path: str | Path,
) -> Path:
    """把完整结构化报告写为便于机器处理的 JSON 文件。

    Args:
        report: 已完成聚合的报告对象。
        output_path: 目标文件路径；父目录不存在时自动创建。

    Returns:
        标准化后的实际写入路径。
    """

    # 第一步：将调用方路径转换为 Path 并创建父目录。
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：序列化完整 Schema，保留中文并使用缩进便于人工核查。
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 第三步：返回目标路径，供调用方建立报告索引或展示链接。
    return path


def write_markdown_report(
    report: RAGEvalReportSchema,
    output_path: str | Path,
) -> Path:
    """把报告渲染为面向人工阅读的 Markdown 摘要。

    Args:
        report: 已完成聚合的报告对象。
        output_path: Markdown 目标路径。

    Returns:
        标准化后的实际写入路径。

    Markdown 只展示关键均值和逐样本摘要；完整 details、metadata 和 extra 仍以
    JSON 报告为事实源，避免 Markdown 表格无限膨胀。
    """

    # 第一步：准备目标路径和父目录。
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：写入报告身份和数据来源，保证摘要可独立追溯。
    lines: list[str] = [
        "# RAG Eval Report",
        "",
        f"- report_id: `{report.report_id}`",
        f"- run_id: `{report.run_id}`",
        f"- source_path: `{report.source_path}`",
        f"- created_at: `{report.created_at}`",
        f"- total: **{report.total}**",
        "",
        "## Summary",
        "",
        "| Metric | Score |",
        "|---|---:|",
        f"| overall | {report.average_overall_score:.4f} |",
        f"| context_precision | {report.average_context_precision:.4f} |",
        f"| context_recall_proxy | {report.average_context_recall_proxy:.4f} |",
        f"| faithfulness_proxy | {report.average_faithfulness_proxy:.4f} |",
        f"| answer_relevance_proxy | {report.average_answer_relevance_proxy:.4f} |",
        f"| citation_coverage | {report.average_citation_coverage:.4f} |",
        f"| completeness_proxy | {report.average_completeness_proxy:.4f} |",
        "",
        "## Samples",
        "",
        "| # | sample_id | overall | ctx_precision | faithfulness | answer_rel | citation | flags |",
        "|---:|---|---:|---:|---:|---:|---:|---|",
    ]

    # 第三步：按原输入顺序渲染逐样本关键指标和质量标记。
    for index, result in enumerate(report.results, start=1):
        flags = ", ".join(result.quality_flags) if result.quality_flags else "-"
        lines.append(
            "| {index} | `{sample_id}` | {overall:.4f} | {precision:.4f} | "
            "{faithfulness:.4f} | {answer_relevance:.4f} | {citation:.4f} | "
            "{flags} |".format(
                index=index,
                sample_id=result.sample_id,
                overall=result.overall_score,
                precision=result.context_precision.score,
                faithfulness=result.faithfulness_proxy.score,
                answer_relevance=result.answer_relevance_proxy.score,
                citation=result.citation_coverage.score,
                flags=flags,
            )
        )

    # 第四步：明确代理指标边界，防止读者把词法分数误解为人工或模型裁判结论。
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "This is a RAGAS-compatible lightweight proxy report. Scores are "
            "deterministic lexical proxies, not LLM-judge scores.",
        ]
    )

    # 第五步：一次性写入完整 Markdown 文本并返回路径。
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_reports(
    report: RAGEvalReportSchema,
    output_dir: str | Path,
    report_name: str,
) -> Dict[str, str]:
    """使用同一文件名前缀写出 JSON 和 Markdown 两种报告。

    Args:
        report: 已完成聚合的报告对象。
        output_dir: 两种产物共用的输出目录。
        report_name: 不带扩展名的文件名前缀。

    Returns:
        包含 ``json`` 和 ``markdown`` 实际路径的字典。
    """

    # 第一步：统一输出目录，避免两种格式被写到不同位置。
    output_dir = Path(output_dir)

    # 第二步：分别调用单格式写入函数，复用其目录创建和序列化规则。
    json_path = write_json_report(report, output_dir / f"{report_name}.json")
    markdown_path = write_markdown_report(
        report,
        output_dir / f"{report_name}.md",
    )

    # 第三步：返回字符串路径，便于 CLI 或 JSON 响应直接序列化。
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
