# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：load_project_input、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run the production mainline from an explicit ProjectInput JSON object.

Unlike the interactive ``run_demo.py``, this entry point never injects a document title,
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


# 阅读注释（函数）：加载 项目 输入。
def load_project_input(path: str | Path) -> Dict[str, Any]:
    """加载 项目 输入。

    参数:
        path: 目标文件或目录路径。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：resolve, expanduser, Path, input_path.is_file, FileNotFoundError, input_path.open, json.load, isinstance。
    """
    input_path = Path(path).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"ProjectInput file not found: {input_path}")
    with input_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("ProjectInput file must contain one JSON object")
    return payload


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> None:
    """处理 main 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args, str, resolve_project_path, load_project_input, strip, project_input.get。
    """
    parser = argparse.ArgumentParser(
        description="Run the Agent-RAG mainline from an explicit ProjectInput."
    )
    parser.add_argument("--project-input-file", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--clean-existing", action="store_true")
    parser.add_argument("--disable-agent-self-rag", action="store_true")
    parser.add_argument(
        "--rag-static-spec",
        type=str,
        default=None,
        help=(
            "YAML/JSON static retrieval specification. "
            "Overrides RAG_STATIC_RETRIEVAL_SPEC_FILE for this process."
        ),
    )
    args = parser.parse_args()

    if args.rag_static_spec:
        os.environ["RAG_STATIC_RETRIEVAL_SPEC_FILE"] = str(
            resolve_project_path(args.rag_static_spec)
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
