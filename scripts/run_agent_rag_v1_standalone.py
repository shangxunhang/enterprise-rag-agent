"""Run the injected-adapter Agent-RAG V1 vertical slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.enterprise_document.runtime.agent_rag_v1 import (  # noqa: E402
    render_result,
    run_scenario,
)


SCENARIOS = (
    "success",
    "research-more",
    "rewrite",
    "paragraph-repair",
    "replan",
    "partial-success",
    "failed",
    "budget-exhausted",
    "human-review",
    "crash-recovery",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone Multi-Agent vertical E2E with injected adapters; "
            "exports Markdown, not production DOCX."
        )
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="success")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "data" / "agent_rag_v1_standalone"),
    )
    parser.add_argument("--task-id")
    parser.add_argument("--run-id")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_scenario(
        args.scenario,
        output_dir=args.output_dir,
        task_id=args.task_id,
        run_id=args.run_id,
    )
    rendered = render_result(result)
    for label, value in rendered.items():
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        print(f"{label}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
