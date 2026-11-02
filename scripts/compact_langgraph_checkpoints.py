"""Compress an existing official LangGraph SQLite checkpoint database."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.runtime.official_checkpoint_saver import (
    DEFAULT_CHECKPOINT_COMPRESSION_LEVEL,
    DEFAULT_CHECKPOINT_COMPRESSION_MIN_BYTES,
    compact_official_checkpoint_database,
)


def _default_backup_path(database: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return database.with_name(f"{database.name}.pre-compression-{stamp}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--min-bytes",
        type=int,
        default=DEFAULT_CHECKPOINT_COMPRESSION_MIN_BYTES,
    )
    parser.add_argument(
        "--compression-level",
        type=int,
        default=DEFAULT_CHECKPOINT_COMPRESSION_LEVEL,
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Optional explicit backup path. Defaults to a timestamped sibling file.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup creation. Use only when an independent verified backup exists.",
    )
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    if not database.is_file():
        parser.error(f"database not found: {database}")

    backup_path: Path | None = None
    if not args.no_backup:
        backup_path = (
            args.backup.expanduser().resolve()
            if args.backup is not None
            else _default_backup_path(database)
        )
        if backup_path.exists():
            parser.error(f"backup path already exists: {backup_path}")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(database, backup_path)

    report = compact_official_checkpoint_database(
        database,
        min_bytes=args.min_bytes,
        compression_level=args.compression_level,
        vacuum=not args.no_vacuum,
    )
    output = {
        "database": str(database),
        "backup": str(backup_path) if backup_path is not None else None,
        "compression_min_bytes": args.min_bytes,
        "compression_level": args.compression_level,
        "vacuum": not args.no_vacuum,
        **report,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
