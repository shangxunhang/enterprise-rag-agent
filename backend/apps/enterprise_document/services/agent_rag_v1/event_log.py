"""Agent-RAG V1 Domain Event 的内存与崩溃安全 JSONL 实现。"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from apps.enterprise_document.schemas.agent_rag_v1 import DomainEventSchema
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink


class DomainEventLog(Protocol):
    def append(self, event: DomainEventSchema) -> bool:
        """追加事件；幂等重复返回 False，身份冲突抛错。"""

        ...

    def list_events(self, task_id: str, run_id: str) -> list[DomainEventSchema]:
        """按 sequence 返回指定运行的全部事件。"""

        ...


def _validate_next_sequence(
    events: list[DomainEventSchema],
    incoming: DomainEventSchema,
) -> None:
    matches = [event for event in events if event.event_id == incoming.event_id]
    if matches:
        if matches[0] != incoming:
            raise ValueError("domain event identity collision")
        return
    expected = events[-1].sequence + 1 if events else 1
    if incoming.sequence != expected:
        raise ValueError(
            f"domain event sequence mismatch: expected={expected}, "
            f"actual={incoming.sequence}"
        )


class InMemoryDomainEventLog:
    """线程安全的测试/组合实现，保持追加与顺序校验语义。"""

    def __init__(self) -> None:
        self._events: list[DomainEventSchema] = []
        self._lock = RLock()

    def append(self, event: DomainEventSchema) -> bool:
        with self._lock:
            run_events = self.list_events(event.task_id, event.run_id)
            _validate_next_sequence(run_events, event)
            if any(item.event_id == event.event_id for item in run_events):
                return False
            self._events.append(event.model_copy(deep=True))
            return True

    def list_events(self, task_id: str, run_id: str) -> list[DomainEventSchema]:
        with self._lock:
            events = [
                event.model_copy(deep=True)
                for event in self._events
                if event.task_id == task_id and event.run_id == run_id
            ]
            return sorted(events, key=lambda event: event.sequence)


class JsonlDomainEventLog:
    """使用现有 crash-safe JSONL 原语持久化追加式领域事件。"""

    def __init__(self, path: str | Path) -> None:
        self._sink = CrashSafeJsonlRecordSink(path)
        self._lock = RLock()

    def _read_all(self) -> list[DomainEventSchema]:
        return [
            DomainEventSchema.model_validate(record)
            for record in self._sink.read_records()
        ]

    def append(self, event: DomainEventSchema) -> bool:
        with self._lock:
            events = [
                item
                for item in self._read_all()
                if item.task_id == event.task_id and item.run_id == event.run_id
            ]
            events.sort(key=lambda item: item.sequence)
            _validate_next_sequence(events, event)
            serialized = json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return self._sink.append_record_once(
                serialized,
                id_field="event_id",
                identity=event.event_id,
            )

    def list_events(self, task_id: str, run_id: str) -> list[DomainEventSchema]:
        with self._lock:
            events = [
                event
                for event in self._read_all()
                if event.task_id == task_id and event.run_id == run_id
            ]
            return sorted(events, key=lambda event: event.sequence)
