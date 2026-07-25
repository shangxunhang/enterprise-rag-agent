"""Crash-recoverable append primitive for canonical JSONL event logs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable


SemanticProjection = Callable[[dict[str, Any]], Any]


def canonical_json_sha256(value: Any) -> str:
    """Return a deterministic fingerprint for one JSON-compatible value."""

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class CrashSafeJsonlEventLog:
    """Treat only newline-terminated records as committed.

    A process crash can leave bytes after the final committed newline. Those
    bytes are an uncommitted trailing frame and are truncated during recovery.
    Invalid UTF-8 or invalid JSON inside newline-terminated history remains the
    caller's responsibility to reject; this class never skips committed lines.
    """

    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS: dict[str, threading.RLock] = {}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_key = str(self.path.resolve())
        with self._LOCKS_GUARD:
            self._lock = self._PATH_LOCKS.setdefault(
                lock_key,
                threading.RLock(),
            )

    def _truncate_unlocked(self, size: int) -> None:
        with self.path.open("r+b") as handle:
            handle.truncate(size)
            handle.flush()
            os.fsync(handle.fileno())

    def _recover_trailing_torn_write_unlocked(self) -> bytes:
        if not self.path.is_file():
            return b""
        data = self.path.read_bytes()
        if not data or data.endswith(b"\n"):
            return data

        # A canonical record is committed only after its terminating newline
        # and fsync. Everything after the last newline is therefore a safe
        # trailing torn write, even if those bytes happen to form valid JSON.
        last_newline = data.rfind(b"\n")
        committed_size = last_newline + 1 if last_newline >= 0 else 0
        self._truncate_unlocked(committed_size)
        return data[:committed_size]

    def read_complete_lines(self) -> list[str]:
        """Recover a trailing torn frame and return every committed line."""

        with self._lock:
            data = self._recover_trailing_torn_write_unlocked()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"invalid UTF-8 in committed JSONL history: {self.path}"
                ) from exc
            return text.splitlines()

    def append_serialized(self, serialized: str) -> None:
        """Append one newline-framed record; fsync is the commit point."""

        if "\n" in serialized or "\r" in serialized:
            raise ValueError("canonical JSONL event must serialize to one line")
        encoded = serialized.encode("utf-8") + b"\n"
        with self._lock:
            self._recover_trailing_torn_write_unlocked()
            with self.path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    def append_many_serialized(self, records: Iterable[str]) -> None:
        for record in records:
            self.append_serialized(record)


class CrashSafeJsonlRecordSink(CrashSafeJsonlEventLog):
    """Crash-safe JSON object records with fail-closed history validation."""

    @staticmethod
    def _validate_serialized_object(serialized: str) -> dict[str, Any]:
        if "\n" in serialized or "\r" in serialized:
            raise ValueError("canonical JSONL record must serialize to one line")
        try:
            record = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise ValueError("canonical JSONL record must be valid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError("canonical JSONL record must be a JSON object")
        return record

    def _read_records_unlocked(self) -> list[dict[str, Any]]:
        data = self._recover_trailing_torn_write_unlocked()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"invalid UTF-8 in committed JSONL history: {self.path}"
            ) from exc

        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid committed JSONL record at {self.path}:{line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"committed JSONL record must be an object at "
                    f"{self.path}:{line_number}"
                )
            records.append(record)
        return records

    def read_records(self) -> list[dict[str, Any]]:
        """Recover an uncommitted tail and parse all committed records."""

        with self._lock:
            return self._read_records_unlocked()

    def _append_record_unlocked(self, serialized: str) -> None:
        encoded = serialized.encode("utf-8") + b"\n"
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def append_record(self, serialized: str) -> None:
        """Validate committed history, append, fsync, then verify readability."""

        expected = self._validate_serialized_object(serialized)
        with self._lock:
            self._read_records_unlocked()
            self._append_record_unlocked(serialized)
            records = self._read_records_unlocked()
            if not records or records[-1] != expected:
                raise OSError("durable JSONL record verification failed")

    def append_record_once(
        self,
        serialized: str,
        *,
        id_field: str,
        identity: str,
        semantic_projection: SemanticProjection | None = None,
        semantic_fingerprint: str | None = None,
    ) -> bool:
        """Append one exact logical effect or fail on semantic ID collision."""

        expected = self._validate_serialized_object(serialized)
        if expected.get(id_field) != identity:
            raise ValueError("serialized record identity does not match")
        project = semantic_projection or (lambda record: record)
        expected_fingerprint = canonical_json_sha256(project(expected))
        if (
            semantic_fingerprint is not None
            and semantic_fingerprint != expected_fingerprint
        ):
            raise ValueError(
                "provided semantic fingerprint does not match serialized record"
            )
        with self._lock:
            records = self._read_records_unlocked()
            existing_matches = [
                record for record in records if record.get(id_field) == identity
            ]
            if len(existing_matches) > 1:
                raise ValueError(
                    "committed JSONL history contains duplicate logical record: "
                    f"{id_field}={identity}"
                )
            if existing_matches:
                existing_fingerprint = canonical_json_sha256(
                    project(existing_matches[0])
                )
                if existing_fingerprint != expected_fingerprint:
                    raise ValueError(
                        "logical record identity collision with different "
                        f"semantic payload: {id_field}={identity}"
                    )
                return False
            self._append_record_unlocked(serialized)
            verified = self._read_records_unlocked()
            matching = [
                record for record in verified if record.get(id_field) == identity
            ]
            if len(matching) != 1:
                raise OSError("durable JSONL idempotent record verification failed")
            if canonical_json_sha256(project(matching[0])) != expected_fingerprint:
                raise OSError(
                    "durable JSONL semantic payload verification failed"
                )
            return True
