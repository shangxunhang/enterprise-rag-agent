"""Atomically migrate the retired custom SQLite checkpoint schema."""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.runtime.official_checkpoint_saver import (
    _checkpoint_serde,
    create_sqlite_checkpoint_resource,
)


def migrate_legacy_database(database: Path) -> dict[str, Any]:
    """Replay legacy tuples into a new official database, then atomically swap."""

    database = database.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    temporary = database.with_name(database.name + ".official-migration.tmp")
    if temporary.exists():
        raise FileExistsError(
            f"migration temporary file already exists: {temporary}"
        )
    serde = _checkpoint_serde()
    with closing(sqlite3.connect(str(database))) as legacy:
        columns = {
            str(row[1])
            for row in legacy.execute(
                "PRAGMA table_info(checkpoints)"
            ).fetchall()
        }
        if not {"checkpoint_blob", "metadata_blob"} <= columns:
            raise ValueError("database is not the retired custom schema")
        rows = legacy.execute(
            """
            SELECT thread_id, checkpoint_ns, checkpoint_id,
                   parent_checkpoint_id, checkpoint_type, checkpoint_blob,
                   metadata_type, metadata_blob
            FROM checkpoints
            ORDER BY thread_id, checkpoint_ns, checkpoint_id
            """
        ).fetchall()
        write_rows = legacy.execute(
            """
            SELECT thread_id, checkpoint_ns, checkpoint_id, task_id,
                   write_index, channel, value_type, value_blob, task_path
            FROM checkpoint_writes
            ORDER BY thread_id, checkpoint_ns, checkpoint_id,
                     task_id, task_path, write_index
            """
        ).fetchall()

    writes_by_checkpoint: dict[
        tuple[str, str, str],
        dict[tuple[str, str], list[tuple[str, Any]]],
    ] = defaultdict(lambda: defaultdict(list))
    for (
        thread_id,
        namespace,
        checkpoint_id,
        task_id,
        _write_index,
        channel,
        value_type,
        value_blob,
        task_path,
    ) in write_rows:
        writes_by_checkpoint[
            (str(thread_id), str(namespace), str(checkpoint_id))
        ][(str(task_id), str(task_path or ""))].append(
            (
                str(channel),
                serde.loads_typed((str(value_type), value_blob)),
            )
        )

    checkpoint_resource = create_sqlite_checkpoint_resource(temporary)
    saver = checkpoint_resource.saver
    try:
        for (
            thread_id,
            namespace,
            checkpoint_id,
            parent_checkpoint_id,
            checkpoint_type,
            checkpoint_blob,
            metadata_type,
            metadata_blob,
        ) in rows:
            checkpoint = serde.loads_typed(
                (str(checkpoint_type), checkpoint_blob)
            )
            metadata = serde.loads_typed(
                (str(metadata_type), metadata_blob)
            )
            if str(checkpoint.get("id")) != str(checkpoint_id):
                raise ValueError(
                    "legacy checkpoint row id does not match payload id"
                )
            config = {
                "configurable": {
                    "thread_id": str(thread_id),
                    "checkpoint_ns": str(namespace),
                    **(
                        {"checkpoint_id": str(parent_checkpoint_id)}
                        if parent_checkpoint_id
                        else {}
                    ),
                }
            }
            saved = saver.put(
                config,
                checkpoint,
                dict(metadata),
                dict(checkpoint.get("channel_versions") or {}),
            )
            groups = writes_by_checkpoint.get(
                (
                    str(thread_id),
                    str(namespace),
                    str(checkpoint_id),
                ),
                {},
            )
            for (task_id, task_path), writes in groups.items():
                saver.put_writes(
                    saved,
                    writes,
                    task_id,
                    task_path=task_path,
                )
    finally:
        checkpoint_resource.close()

    with closing(sqlite3.connect(str(temporary))) as migrated:
        migrated_checkpoint_count = int(
            migrated.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        )
        migrated_write_count = int(
            migrated.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
        )
    if migrated_checkpoint_count != len(rows):
        raise RuntimeError("checkpoint count changed during migration")
    if migrated_write_count != len(write_rows):
        raise RuntimeError("pending-write count changed during migration")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = database.with_name(database.name + f".legacy-backup-{timestamp}")
    database.replace(backup)
    try:
        temporary.replace(database)
    except Exception:
        backup.replace(database)
        raise
    return {
        "schema_version": "checkpoint_migration_report_v1",
        "database": str(database),
        "legacy_backup": str(backup),
        "checkpoint_count": migrated_checkpoint_count,
        "pending_write_count": migrated_write_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    report = migrate_legacy_database(args.database)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
