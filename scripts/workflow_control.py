"""Inspect, continue, cancel, or stream durable enterprise-document workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / 'backend'

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

from application.mainline_service import MainlineApplicationService
from bootstrap.document_catalog_adapter import DocumentProfileCatalogAdapter

_source_module.assert_loaded_project_origins()


def _common_existing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--allow-demo-defaults",
        action="store_true",
        help="Permit fake/demo dependencies; never implied for production.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser("inspect")
    _common_existing_arguments(inspect_parser)

    resume_parser = commands.add_parser("resume")
    _common_existing_arguments(resume_parser)
    resume_parser.add_argument(
        "--decision",
        choices=("approved", "rejected"),
        required=True,
    )
    resume_parser.add_argument("--feedback")
    resume_parser.add_argument("--reviewer-id")

    cancel_parser = commands.add_parser("cancel")
    _common_existing_arguments(cancel_parser)
    cancel_parser.add_argument(
        "--reason",
        default="workflow_cancelled",
    )

    stream_resume_parser = commands.add_parser("stream-resume")
    _common_existing_arguments(stream_resume_parser)
    stream_resume_parser.add_argument(
        "--decision",
        choices=("approved", "rejected"),
        required=True,
    )
    stream_resume_parser.add_argument("--feedback")
    stream_resume_parser.add_argument("--reviewer-id")

    stream_cancel_parser = commands.add_parser("stream-cancel")
    _common_existing_arguments(stream_cancel_parser)
    stream_cancel_parser.add_argument(
        "--reason",
        default="workflow_cancelled",
    )

    stream_parser = commands.add_parser("stream")
    stream_parser.add_argument("--user-input", required=True)
    stream_parser.add_argument("--run-id", required=True)
    stream_parser.add_argument("--task-id")
    stream_parser.add_argument("--output-root", type=Path, required=True)
    stream_parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    stream_parser.add_argument("--clean-existing", action="store_true")
    stream_parser.add_argument("--allow-demo-defaults", action="store_true")
    return parser


def _dump(value: Any) -> None:
    print(
        json.dumps(value, ensure_ascii=False, default=str),
        flush=True,
    )


def main() -> int:
    args = build_parser().parse_args()
    service = MainlineApplicationService(
        document_catalog=DocumentProfileCatalogAdapter(),
    )
    common = {
        "project_root": args.project_root.resolve(),
        "output_root": args.output_root.resolve(),
        "allow_demo_defaults": bool(args.allow_demo_defaults),
    }
    if args.command == "inspect":
        _dump(
            service.inspect_run(
                task_id=args.task_id,
                run_id=args.run_id,
                **common,
            )
        )
    elif args.command == "resume":
        _dump(
            service.resume_run(
                task_id=args.task_id,
                run_id=args.run_id,
                decision=args.decision,
                feedback=args.feedback,
                reviewer_id=args.reviewer_id,
                **common,
            )
        )
    elif args.command == "cancel":
        _dump(
            service.cancel_run(
                task_id=args.task_id,
                run_id=args.run_id,
                reason=args.reason,
                **common,
            )
        )
    elif args.command == "stream-resume":
        for event in service.stream_resume_run(
            task_id=args.task_id,
            run_id=args.run_id,
            decision=args.decision,
            feedback=args.feedback,
            reviewer_id=args.reviewer_id,
            **common,
        ):
            _dump(event.model_dump(mode="json"))
    elif args.command == "stream-cancel":
        for event in service.stream_cancel_run(
            task_id=args.task_id,
            run_id=args.run_id,
            reason=args.reason,
            **common,
        ):
            _dump(event.model_dump(mode="json"))
    else:
        for event in service.stream_run(
            user_input=args.user_input,
            run_id=args.run_id,
            task_id=args.task_id,
            clean_existing=bool(args.clean_existing),
            **common,
        ):
            _dump(event.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
