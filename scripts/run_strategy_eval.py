"""Run a reproducible online RAG profile experiment matrix."""
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the same RAG eval set against multiple pipeline profiles."
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
            f"- {row.experiment_id}: profile={row.profile_id} "
            f"status={row.status} metrics={json.dumps(row.metrics, ensure_ascii=False)}"
        )
    print("\nOutput files:")
    for name, path in report.output_files.items():
        print(f"- {name}: {path}")
    if report.status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
