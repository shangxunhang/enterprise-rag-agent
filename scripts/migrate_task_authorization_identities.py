"""Explicit offline migration for legacy Task authorization identities.

The command is dry-run by default.  It reads full Task event logs only because
an administrator invoked this migration explicitly; application startup and
online Existing-run authorization never call it.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.persistence.safe_paths import SafePathResolver
from task.task_manager import (
    JsonlTaskManager,
    TaskAuthorizationIdentityMigrationResult,
    TaskAuthorizationIdentityMigrationStatus,
)


_STATE_SUFFIX = "_state.jsonl"
_ERROR_STATUSES = {
    TaskAuthorizationIdentityMigrationStatus.CONFLICT,
    TaskAuthorizationIdentityMigrationStatus.INVALID_TASK_LOG,
    TaskAuthorizationIdentityMigrationStatus.FAILED,
}


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _discover_task_ids(tasks_root: Path) -> list[str]:
    return sorted(
        path.name[: -len(_STATE_SUFFIX)]
        for path in tasks_root.glob(f"*{_STATE_SUFFIX}")
        if path.name.endswith(_STATE_SUFFIX)
    )


def migrate_task_authorization_identities(
    tasks_dir: str | Path,
    *,
    task_ids: Sequence[str] = (),
    apply: bool = False,
) -> dict[str, object]:
    """Run an explicit privileged migration and return its audit report."""

    resolver = SafePathResolver(tasks_dir)
    tasks_root = resolver.root
    if not tasks_root.is_dir():
        raise FileNotFoundError(f"tasks directory does not exist: {tasks_root}")

    selected = _unique(list(task_ids)) if task_ids else _discover_task_ids(tasks_root)
    manager = JsonlTaskManager(tasks_root)
    results: list[TaskAuthorizationIdentityMigrationResult] = [
        manager.migrate_legacy_authorization_identity(task_id, apply=apply)
        for task_id in selected
    ]
    counts = Counter(result.status.value for result in results)
    status_counts = {
        status.value: counts.get(status.value, 0)
        for status in TaskAuthorizationIdentityMigrationStatus
    }
    return {
        "schema_version": "task_authorization_identity_migration_report_v1",
        "mode": "apply" if apply else "dry-run",
        "tasks_dir": str(tasks_root),
        "scanned": len(results),
        **status_counts,
        "results": [result.as_dict() for result in results],
    }


def report_has_errors(report: dict[str, object]) -> bool:
    return any(int(report[status.value]) > 0 for status in _ERROR_STATUSES)


def _write_report(path: Path, report: dict[str, object]) -> None:
    target = path.expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=Path("data/tasks"),
        help="trusted Task persistence directory (default: data/tasks)",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="migrate only this task_id; repeat to select multiple tasks",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="publish missing identities; without this flag the command is dry-run",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        help="optionally write the complete JSON report to this path",
    )
    args = parser.parse_args(argv)

    try:
        report = migrate_task_authorization_identities(
            args.tasks_dir,
            task_ids=args.task_id,
            apply=args.apply,
        )
    except Exception as exc:
        report = {
            "schema_version": "task_authorization_identity_migration_report_v1",
            "mode": "apply" if args.apply else "dry-run",
            "tasks_dir": str(args.tasks_dir.expanduser().resolve(strict=False)),
            "scanned": 0,
            **{
                status.value: (
                    1
                    if status == TaskAuthorizationIdentityMigrationStatus.FAILED
                    else 0
                )
                for status in TaskAuthorizationIdentityMigrationStatus
            },
            "results": [
                {
                    "task_id": "<scan>",
                    "status": (
                        TaskAuthorizationIdentityMigrationStatus.FAILED.value
                    ),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ],
        }

    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.report_json is not None:
        try:
            _write_report(args.report_json, report)
        except Exception as exc:
            print(
                f"failed to write migration report: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
    return 1 if report_has_errors(report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
