"""Immediate, idempotent persistence for paragraph-level Writer progress."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Protocol, TypeAlias

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ParagraphGeneratedEventSchema,
    SectionGenerationCompletedEventSchema,
)
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink


WriterProgressEvent: TypeAlias = (
    ParagraphGeneratedEventSchema | SectionGenerationCompletedEventSchema
)


class WriterProgressEventLog(Protocol):
    def append(self, event: WriterProgressEvent) -> bool:
        ...

    def list_events(
        self,
        task_id: str,
        run_id: str,
        section_id: str | None = None,
    ) -> list[WriterProgressEvent]:
        ...


class InMemoryWriterProgressEventLog:
    def __init__(self) -> None:
        self._events: list[WriterProgressEvent] = []
        self._lock = RLock()

    def append(self, event: WriterProgressEvent) -> bool:
        with self._lock:
            matches = [
                item for item in self._events if item.event_id == event.event_id
            ]
            if matches:
                if matches[0] != event:
                    raise ValueError("writer progress event identity collision")
                return False
            self._events.append(event.model_copy(deep=True))
            return True

    def list_events(
        self,
        task_id: str,
        run_id: str,
        section_id: str | None = None,
    ) -> list[WriterProgressEvent]:
        with self._lock:
            return [
                event.model_copy(deep=True)
                for event in self._events
                if event.task_id == task_id
                and event.run_id == run_id
                and (section_id is None or event.section_id == section_id)
            ]


class JsonlWriterProgressEventLog:
    def __init__(self, path: str | Path) -> None:
        self._sink = CrashSafeJsonlRecordSink(path)

    @staticmethod
    def _parse(record: dict) -> WriterProgressEvent:
        if "generation_complete" in record:
            return SectionGenerationCompletedEventSchema.model_validate(record)
        return ParagraphGeneratedEventSchema.model_validate(record)

    def append(self, event: WriterProgressEvent) -> bool:
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

    def list_events(
        self,
        task_id: str,
        run_id: str,
        section_id: str | None = None,
    ) -> list[WriterProgressEvent]:
        return [
            event
            for event in (
                self._parse(record) for record in self._sink.read_records()
            )
            if event.task_id == task_id
            and event.run_id == run_id
            and (section_id is None or event.section_id == section_id)
        ]
