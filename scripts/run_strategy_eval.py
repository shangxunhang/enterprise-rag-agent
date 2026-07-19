# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run a reproducible online RAG static-spec experiment matrix."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval.rag.strategy import StrategyEvalRunner


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> None:
    """处理 main 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args, StrategyEvalRunner, runner.validate_from_file, print, json.dumps, runner.run_from_file。
    """
    parser = argparse.ArgumentParser(
        description="Run one RAG eval set with static-spec plan variants."
    )
    parser.add_argument("--experiment-config", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    runner = StrategyEvalRunner()
    if args.validate_only:
        validation = runner.validate_from_file(
            args.experiment_config,
            project_root=PROJECT_ROOT,
        )
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return

    report = runner.run_from_file(
        args.experiment_config,
        project_root=PROJECT_ROOT,
        output_dir_override=args.output_dir,
    )
    print("\nRAG strategy evaluation finished.")
    print(f"matrix_id: {report.matrix_id}")
    print(f"matrix_run_id: {report.matrix_run_id}")
    print(f"status: {report.status}")
    print(f"experiments: {report.experiment_count}")
    print(f"dataset_hash: {report.dataset_hash}")
    print("\nResults:")
    for row in report.rows:
        print(
            f"- {row.experiment_id}: static_spec={row.static_spec_id} "
            f"status={row.status} metrics={json.dumps(row.metrics, ensure_ascii=False)}"
        )
    print("\nOutput files:")
    for name, path in report.output_files.items():
        print(f"- {name}: {path}")
    if report.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
