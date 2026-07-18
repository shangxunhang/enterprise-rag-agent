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


def _latest_eval_samples(captures_dir: Path) -> Path:
    eval_dir = captures_dir / "eval_samples"
    files = list(eval_dir.glob("*_eval_samples.jsonl"))

    if not files:
        raise FileNotFoundError(
            f"No *_eval_samples.jsonl files found in {eval_dir}"
        )

    return max(files, key=lambda path: path.stat().st_mtime)


def _infer_run_id(path: Path) -> str:
    suffix = "_eval_samples.jsonl"
    if path.name.endswith(suffix):
        return path.name[: -len(suffix)]
    return path.stem


def main() -> None:
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
