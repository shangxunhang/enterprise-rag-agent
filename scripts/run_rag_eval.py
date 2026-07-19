# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_latest_eval_samples、_infer_run_id、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run lightweight RAG evaluation from Agent capture eval_samples."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.config import get_settings

# RAG eval implementation has not been migrated yet. This import is intentionally
# kept until backend/agent/legacy_simple_agent/evaluation is moved to backend/eval/rag.
from eval.rag.capture.rag_eval_runner import RAGEvalRunner


# 阅读注释（函数）：处理 latest 评测 samples 相关逻辑。
def _latest_eval_samples(captures_dir: Path) -> Path:
    """处理 latest 评测 samples 相关逻辑。

    参数:
        captures_dir: captures dir，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：list, eval_dir.glob, FileNotFoundError, max。
    """
    eval_dir = captures_dir / "eval_samples"
    files = list(eval_dir.glob("*_eval_samples.jsonl"))

    if not files:
        raise FileNotFoundError(
            f"No *_eval_samples.jsonl files found in {eval_dir}"
        )

    return max(files, key=lambda path: path.stat().st_mtime)


# 阅读注释（函数）：处理 infer run 标识 相关逻辑。
def _infer_run_id(path: Path) -> str:
    """处理 infer run 标识 相关逻辑。

    参数:
        path: 目标文件或目录路径。

    返回:
        str

    阅读提示:
        主要直接调用：path.name.endswith, len。
    """
    suffix = "_eval_samples.jsonl"
    if path.name.endswith(suffix):
        return path.name[: -len(suffix)]
    return path.stem


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> None:
    """处理 main 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args, get_settings, resolve, expanduser, Path, _latest_eval_samples。
    """
    parser = argparse.ArgumentParser(
        description="Run lightweight RAG eval from eval_samples capture.",
    )
    parser.add_argument(
        "--eval-samples",
        type=str,
        default=None,
        help="Path to *_eval_samples.jsonl. Default: latest.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output dir. Default: settings.eval_output_dir/rag_eval.",
    )
    parser.add_argument(
        "--report-name",
        type=str,
        default=None,
        help="Report filename without extension.",
    )
    args = parser.parse_args()

    settings = get_settings()
    captures_dir = settings.data_capture_dir

    eval_samples_path = (
        Path(args.eval_samples).expanduser().resolve()
        if args.eval_samples
        else _latest_eval_samples(captures_dir)
    )
    run_id = _infer_run_id(eval_samples_path)

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else settings.eval_output_dir / "rag_eval"
    )
    report_name = args.report_name or f"rag_eval_report_{run_id}"

    runner = RAGEvalRunner()
    result = runner.evaluate_and_save(
        eval_samples_path=eval_samples_path,
        output_dir=output_dir,
        report_name=report_name,
        run_id=run_id,
    )

    report = result["report"]

    print("\nRAG eval finished.")
    print(f"run_id: {run_id}")
    print(f"source: {eval_samples_path}")
    print(f"total: {report['total']}")
    print(f"overall: {report['average_overall_score']:.4f}")
    print(f"context_precision: {report['average_context_precision']:.4f}")
    print(f"context_recall_proxy: {report['average_context_recall_proxy']:.4f}")
    print(f"faithfulness_proxy: {report['average_faithfulness_proxy']:.4f}")
    print(f"answer_relevance_proxy: {report['average_answer_relevance_proxy']:.4f}")
    print(f"citation_coverage: {report['average_citation_coverage']:.4f}")
    print(f"completeness_proxy: {report['average_completeness_proxy']:.4f}")

    print("\nOutput files:")
    for name, path in result["output_paths"].items():
        print(f"- {name}: {path}")

    print("\nJSON summary:")
    print(
        json.dumps(
            {
                "run_id": run_id,
                "output_paths": result["output_paths"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
