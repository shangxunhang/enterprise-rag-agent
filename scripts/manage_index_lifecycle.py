# =============================================================================
# 中文阅读说明：命令行脚本模块，用于启动、验收、调试或离线维护。
# 主要定义：_parser、_dump、main。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for path in (str(BACKEND_ROOT), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from rag.offline.lifecycle import IndexLifecycleManager  # noqa: E402


# 阅读注释（函数）：处理 parser 相关逻辑。
def _parser() -> argparse.ArgumentParser:
    """处理 parser 相关逻辑。

    返回:
        argparse.ArgumentParser

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, str, parser.add_subparsers, sub.add_parser, register.add_argument, activate.add_argument, rollback.add_argument。
    """
    parser = argparse.ArgumentParser(description="Manage immutable RAG index lifecycle")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root used to resolve relative lifecycle paths",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover")
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("history")
    sub.add_parser("resolve")

    register = sub.add_parser("register")
    register.add_argument("--manifest", required=True)

    activate = sub.add_parser("activate")
    activate.add_argument("--index-version", required=True)
    activate.add_argument("--actor", default="manual_cli")
    activate.add_argument("--reason", default=None)
    activate.add_argument("--skip-artifact-hashes", action="store_true")
    activate.add_argument("--skip-milvus", action="store_true")
    activate.add_argument("--self-retrieval-samples", type=int, default=3)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--target-index-version", default=None)
    rollback.add_argument("--actor", default="manual_cli")
    rollback.add_argument("--reason", default=None)
    rollback.add_argument("--skip-artifact-hashes", action="store_true")
    rollback.add_argument("--skip-milvus", action="store_true")
    rollback.add_argument("--self-retrieval-samples", type=int, default=3)
    return parser


# 阅读注释（函数）：处理 dump 相关逻辑。
def _dump(value: object) -> None:
    """处理 dump 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：hasattr, value.model_dump, isinstance, item.model_dump, print, json.dumps。
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in value
        ]
    print(json.dumps(value, ensure_ascii=False, indent=2))


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> int:
    """处理 main 相关逻辑。

    返回:
        int

    阅读提示:
        主要直接调用：parse_args, _parser, IndexLifecycleManager, _dump, manager.discover, manager.list_indexes, manager.status, manager.history。
    """
    args = _parser().parse_args()
    manager = IndexLifecycleManager(project_root=args.project_root)
    try:
        if args.command == "discover":
            _dump(manager.discover(persist=True))
        elif args.command == "list":
            _dump(manager.list_indexes(refresh=True))
        elif args.command == "status":
            _dump(manager.status())
        elif args.command == "history":
            _dump(manager.history())
        elif args.command == "resolve":
            _dump(manager.resolve_active(verify_artifacts=True))
        elif args.command == "register":
            _dump(manager.register(args.manifest))
        elif args.command == "activate":
            _dump(
                manager.activate(
                    args.index_version,
                    actor=args.actor,
                    reason=args.reason,
                    verify_artifact_hashes=not args.skip_artifact_hashes,
                    verify_milvus=not args.skip_milvus,
                    self_retrieval_samples=args.self_retrieval_samples,
                )
            )
        elif args.command == "rollback":
            _dump(
                manager.rollback(
                    args.target_index_version,
                    actor=args.actor,
                    reason=args.reason,
                    verify_artifact_hashes=not args.skip_artifact_hashes,
                    verify_milvus=not args.skip_milvus,
                    self_retrieval_samples=args.self_retrieval_samples,
                )
            )
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
