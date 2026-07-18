"""Run the production mainline from an explicit ProjectInput JSON object.

Unlike ``run_demo_back.py``, this entry point never injects a document title,
chapter list, citation policy or task type.  Business differences must arrive
through ProjectInput.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict

from mainline_runtime import run_mainline
from mainline_runtime import resolve_project_path


def load_project_input(path: str | Path) -> Dict[str, Any]:
    input_path = Path(path).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"ProjectInput file not found: {input_path}")
    with input_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("ProjectInput file must contain one JSON object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Agent-RAG mainline from an explicit ProjectInput."
    )
    parser.add_argument("--project-input-file", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--clean-existing", action="store_true")
    parser.add_argument(
        "--retrieval-strategy",
        default=None,
        choices=[
            "hybrid",
            "rag_fusion",
            "hyde",
            "rag_fusion_hyde",
            "c_rag",
            "self_rag",
            "c_rag_self_rag",
            "adaptive_rag",
        ],
    )
    parser.add_argument("--disable-agent-self-rag", action="store_true")
    parser.add_argument(
        "--rag-pipeline-config",
        type=str,
        default=None,
        help=(
            "YAML/JSON online RAG pipeline profile. "
            "Overrides RAG_PIPELINE_CONFIG_FILE for this process."
        ),
    )
    args = parser.parse_args()

    if args.rag_pipeline_config:
        os.environ["RAG_PIPELINE_CONFIG_FILE"] = str(
            resolve_project_path(args.rag_pipeline_config)
        )

    project_input = load_project_input(args.project_input_file)
    user_query = str(project_input.get("user_query") or "").strip()
    if not user_query:
        raise ValueError("ProjectInput.user_query is required")

    summary = run_mainline(
        user_input=user_query,
        run_id=args.run_id,
        output_root=args.output_root,
        clean_existing=args.clean_existing,
        retrieval_strategy=args.retrieval_strategy,
        enable_agent_self_rag=not args.disable_agent_self_rag,
        project_input=project_input,
        allow_demo_defaults=False,
    )

    print(json.dumps(
        {
            "task_id": summary["task_id"],
            "run_id": summary["run_id"],
            "status": summary["status"],
            "paths": summary["paths"],
            "error": (summary.get("supervisor_result") or {}).get("error"),
        },
        ensure_ascii=False,
        indent=2,
    ))
    if summary["status"] not in {"success", "partial_success"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
