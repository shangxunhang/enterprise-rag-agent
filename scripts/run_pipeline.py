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

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import importlib.util as _source_util
import sys as _source_sys
from pathlib import Path as _SourcePath

_source_file = PROJECT_ROOT / 'source_bootstrap.py'
_source_name = 'source_bootstrap'
if _source_name in _source_sys.modules:
    _source_module = _source_sys.modules[_source_name]
    try:
        _source_values = vars(_source_module)
        _source_spec = _source_values.get('__spec__')
        if (
            _source_spec is None
            or _source_values.get('__path__') is not None
            or _source_spec.submodule_search_locations is not None
            or _SourcePath(_source_values['__file__']).resolve() != _source_file
            or _SourcePath(_source_spec.origin).resolve() != _source_file
            or not callable(_source_values.get('bootstrap_source_root'))
        ):
            raise ImportError('SOURCE_ROOT_REJECTED: cached trusted helper')
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as _source_error:
        raise ImportError('SOURCE_ROOT_REJECTED: malformed cached trusted helper') from _source_error
else:
    _source_spec = _source_util.spec_from_file_location(_source_name, _source_file)
    if _source_spec is None or _source_spec.loader is None:
        raise ImportError('SOURCE_ROOT_REJECTED: unavailable trusted helper')
    _source_module = _source_util.module_from_spec(_source_spec)
    _source_sys.modules[_source_name] = _source_module
    # Preserve the cached module if execution/initialization fails; never evict.
    _source_spec.loader.exec_module(_source_module)
_source_contract = _source_module.bootstrap_source_root(PROJECT_ROOT)

from mainline_runtime import run_mainline
from mainline_runtime import resolve_project_path
from schemas.rag import RetrievalAccessScopeSchema

_source_module.assert_loaded_project_origins()


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


def load_retrieval_access_scope(
    path: str | Path,
) -> RetrievalAccessScopeSchema:
    """Load authorization truth from a channel separate from ProjectInput."""

    payload = load_project_input(path)
    return RetrievalAccessScopeSchema.model_validate(payload, strict=True)


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
    parser.add_argument(
        "--retrieval-access-scope-file",
        required=True,
        help=(
            "Trusted application/security scope JSON. This is intentionally "
            "separate from caller-controlled ProjectInput."
        ),
    )
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
    retrieval_access_scope = load_retrieval_access_scope(
        args.retrieval_access_scope_file
    )
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
        retrieval_access_scope=retrieval_access_scope,
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
