"""Inspect, continue, cancel, or stream durable enterprise-document workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from application.mainline_service import MainlineApplicationService


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
    service = MainlineApplicationService()
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
