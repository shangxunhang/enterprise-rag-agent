"""Operational normalized Tool-result store.

The store is keyed by Target tool_operation_id when present, with call_id retained
only for historical compatibility rows. It is not a Domain truth or external-effect
committed-result resolver.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from apps.enterprise_document.schemas.agent_rag_v1 import (
    NormalizedToolResultSchema,
)
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink


def _identity(result: NormalizedToolResultSchema) -> str:
    return str(result.tool_operation_id or result.call_id)


class ToolExecutionStore(Protocol):
    def put(self, result: NormalizedToolResultSchema) -> bool:
        ...

    def get(self, operation_or_call_id: str) -> NormalizedToolResultSchema:
        ...


class InMemoryToolExecutionStore:
    def __init__(self) -> None:
        self._results: dict[str, NormalizedToolResultSchema] = {}
        self._lock = RLock()

    def put(self, result: NormalizedToolResultSchema) -> bool:
        identity = _identity(result)
        with self._lock:
            existing = self._results.get(identity)
            if existing is not None:
                if existing != result:
                    raise ValueError(
                        "tool operation identity collision with different result"
                    )
                return False
            self._results[identity] = result.model_copy(deep=True)
            return True

    def get(self, operation_or_call_id: str) -> NormalizedToolResultSchema:
        normalized = str(operation_or_call_id or "").strip()
        if not normalized:
            raise KeyError(operation_or_call_id)
        with self._lock:
            result = self._results.get(normalized)
            if result is None:
                raise KeyError(normalized)
            return result.model_copy(deep=True)


class JsonlToolExecutionStore:
    def __init__(self, path: str | Path) -> None:
        self._sink = CrashSafeJsonlRecordSink(path)
        self._lock = RLock()

    def put(self, result: NormalizedToolResultSchema) -> bool:
        serialized = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = _identity(result)
        id_field = "tool_operation_id" if result.tool_operation_id else "call_id"
        with self._lock:
            return self._sink.append_record_once(
                serialized,
                id_field=id_field,
                identity=identity,
            )

    def get(self, operation_or_call_id: str) -> NormalizedToolResultSchema:
        normalized = str(operation_or_call_id or "").strip()
        if not normalized:
            raise KeyError(operation_or_call_id)
        with self._lock:
            matches = []
            for record in self._sink.read_records():
                record_identity = record.get("tool_operation_id") or record.get("call_id")
                if record_identity == normalized:
                    matches.append(record)
        if not matches:
            raise KeyError(normalized)
        if len(matches) != 1:
            raise ValueError(f"duplicate tool operation records: {normalized}")
        return NormalizedToolResultSchema.model_validate(matches[0])

    def list(self) -> list[NormalizedToolResultSchema]:
        with self._lock:
            return [
                NormalizedToolResultSchema.model_validate(record)
                for record in self._sink.read_records()
            ]
