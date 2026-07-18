"""Run evaluation for the current Agent-RAG main pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Eval implementation has not been migrated yet. This import is intentionally
# kept until backend/agent/legacy_simple_agent/eval is moved to backend/eval/agent.
from eval.agent.eval_runner import (
    EvalRunner,
    build_default_eval_samples,
)

# run_demo_back.py is in the same scripts directory, so direct script execution works.
from run_demo_back import run_demo


def print_eval_report(report: dict, output_paths: dict) -> None:
    summary = report["summary"]

    print("\nAgent-RAG eval finished.")
    print(f"Total: {summary['total']}")
    print(f"Success count: {summary['success_count']}")
    print(f"Success rate: {summary['success_rate']:.2f}")
    print(f"Average score: {summary['average_score']:.2f}")
    print(f"Required sections rate: {summary['required_sections_rate']:.2f}")
    print(f"Keyword hit rate: {summary['keyword_hit_rate']:.2f}")

    print("\nEval output files:")
    for name, path in output_paths.items():
        print(f"- {name}: {path}")

    print("\nEval records:")
    for index, result in enumerate(report["results"], start=1):
        metrics = result["metrics"]
        checks = metrics.get("extra", {}).get("checks", {})

        print(f"\n[{index}] sample_id: {result['sample_id']}")
        print(f"run_id: {result['run_id']}")
        print(f"score: {result['score']:.2f}")
        print(f"success: {metrics['success']}")
        print(f"checks: {json.dumps(checks, ensure_ascii=False)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Agent-RAG eval.")
    parser.add_argument(
        "--output-root",
        type=str,
        default=str(PROJECT_ROOT / "data"),
        help="Output root for run_demo generated traces/captures.",
    )
    parser.add_argument(
        "--eval-output-dir",
        type=str,
        default=str(PROJECT_ROOT / "data" / "eval_outputs"),
        help="Directory for eval reports.",
    )
    parser.add_argument(
        "--report-name",
        type=str,
        default=None,
        help="Optional eval report filename without extension.",
    )
    parser.add_argument(
        "--run-id-prefix",
        type=str,
        default="run_eval",
        help="Prefix for eval run ids.",
    )
    parser.add_argument(
        "--clean-existing",
        action="store_true",
        help="Clean existing trace/capture files for generated run ids.",
    )
    args = parser.parse_args()

    samples = build_default_eval_samples()

    runner = EvalRunner(
        output_dir=args.eval_output_dir,
        min_output_chars=100,
    )

    report = runner.evaluate_samples(
        samples=samples,
        run_func=run_demo,
        run_id_prefix=args.run_id_prefix,
        output_root=args.output_root,
        clean_existing=args.clean_existing,
    )

    report_name = args.report_name
    if report_name is None:
        report_name = f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    output_paths = runner.save_report(
        report=report,
        report_name=report_name,
    )

    print_eval_report(report=report, output_paths=output_paths)


if __name__ == "__main__":
    main()
