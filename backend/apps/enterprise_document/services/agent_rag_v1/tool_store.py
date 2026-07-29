"""完整标准化工具结果的不可变 JSONL Store。"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from apps.enterprise_document.schemas.agent_rag_v1 import (
    NormalizedToolResultSchema,
)
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink


class ToolExecutionStore(Protocol):
    def put(self, result: NormalizedToolResultSchema) -> bool:
        ...

    def get(self, call_id: str) -> NormalizedToolResultSchema:
        ...


class InMemoryToolExecutionStore:
    def __init__(self) -> None:
        self._results: dict[str, NormalizedToolResultSchema] = {}
        self._lock = RLock()

    def put(self, result: NormalizedToolResultSchema) -> bool:
        with self._lock:
            existing = self._results.get(result.call_id)
            if existing is not None:
                if existing != result:
                    raise ValueError(
                        "tool call identity collision with different result"
                    )
                return False
            self._results[result.call_id] = result.model_copy(deep=True)
            return True

    def get(self, call_id: str) -> NormalizedToolResultSchema:
        with self._lock:
            result = self._results.get(call_id)
            if result is None:
                raise KeyError(call_id)
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
        with self._lock:
            return self._sink.append_record_once(
                serialized,
                id_field="call_id",
                identity=result.call_id,
            )

    def get(self, call_id: str) -> NormalizedToolResultSchema:
        with self._lock:
            matches = [
                record
                for record in self._sink.read_records()
                if record.get("call_id") == call_id
            ]
        if not matches:
            raise KeyError(call_id)
        if len(matches) != 1:
            raise ValueError(f"duplicate tool call records: {call_id}")
        return NormalizedToolResultSchema.model_validate(matches[0])

    def list(self) -> list[NormalizedToolResultSchema]:
        with self._lock:
            return [
                NormalizedToolResultSchema.model_validate(record)
                for record in self._sink.read_records()
            ]
