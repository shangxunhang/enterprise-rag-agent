"""Crash-safe atomic publication for parser artifacts."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .safe_paths import assert_within


def _atomic_write(target: Path, writer: Any, *, root: Path | None = None) -> None:
    target = target.resolve()
    if root is not None:
        assert_within(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    if root is not None:
        assert_within(root, temporary)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def atomic_write_text(target: Path, text: str, *, root: Path | None = None) -> None:
    _atomic_write(target, lambda handle: handle.write(text), root=root)


def atomic_write_json(target: Path, value: Any, *, root: Path | None = None) -> None:
    def write(handle: Any) -> None:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")

    _atomic_write(target, write, root=root)


def atomic_write_jsonl(
    target: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    root: Path | None = None,
) -> None:
    def write(handle: Any) -> None:
        for record in records:
            handle.write(json.dumps(dict(record), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    _atomic_write(target, write, root=root)
