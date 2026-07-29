"""Paragraph 原子持久化、Claim/Precheck 与显式章节完成事件。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable, Iterable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    FactSnapshotSchema,
    GeneratedParagraphInputSchema,
    ParagraphArtifactSchema,
    ParagraphGeneratedEventSchema,
    SectionGenerationCompletedEventSchema,
    StreamingWriterOutcomeSchema,
    WriterResultPayloadSchema,
)

from .artifact_store import FileArtifactStore
from .paragraph_identity import ParagraphIdentityService
from .paragraph_precheck import ParagraphPrecheckService
from .writer_event_log import (
    InMemoryWriterProgressEventLog,
    WriterProgressEventLog,
)


class StreamingWriterService:
    def __init__(
        self,
        *,
        artifact_store: FileArtifactStore,
        precheck: ParagraphPrecheckService,
        identity: ParagraphIdentityService | None = None,
        progress_log: WriterProgressEventLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.precheck = precheck
        self.identity = identity or ParagraphIdentityService()
        self.progress_log = progress_log or InMemoryWriterProgressEventLog()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def generate(
        self,
        *,
        context: AgentExecutionContextSchema,
        section_id: str,
        draft_version: int,
        paragraphs: Iterable[GeneratedParagraphInputSchema],
        fact_snapshot: FactSnapshotSchema,
        expected_paragraph_ids: list[str] | None = None,
        retired_paragraph_ids: set[str] | None = None,
        prior_paragraphs: list[ParagraphArtifactSchema] | None = None,
    ) -> StreamingWriterOutcomeSchema:
        if context.agent_role != AgentRole.WRITER:
            raise ValueError("streaming writer requires writer context")
        if context.section_id != section_id:
            raise ValueError("streaming writer section identity mismatch")
        if fact_snapshot.snapshot_version != context.input_snapshot_version:
            raise ValueError("streaming writer FactSnapshot is stale")
        if fact_snapshot.artifact_ref not in context.authorized_artifact_refs:
            raise ValueError("streaming writer FactSnapshot is not authorized")
        artifacts = sorted(
            [
                ParagraphArtifactSchema.model_validate(item)
                for item in prior_paragraphs or []
            ],
            key=lambda item: item.order,
        )
        self._validate_prior_paragraphs(
            context=context,
            section_id=section_id,
            paragraphs=artifacts,
        )
        reserved = set(retired_paragraph_ids or set()) | {
            item.paragraph_id for item in artifacts
        }
        paragraph_events: list[ParagraphGeneratedEventSchema] = []
        prechecks = []
        blocked_paragraph_id = None
        seen_orders = {item.order for item in artifacts}

        for raw_item in paragraphs:
            item = GeneratedParagraphInputSchema.model_validate(raw_item)
            if item.order in seen_orders:
                raise ValueError("paragraph order must be unique")
            paragraph_id = (
                self.identity.preserve(item.paragraph_id)
                if item.paragraph_id
                else self.identity.new_id(reserved)
            )
            if paragraph_id in reserved:
                raise ValueError("paragraph id is already active or retired")
            reserved.add(paragraph_id)
            seen_orders.add(item.order)
            artifact_ref = (
                f"artifact://{context.task_id}/paragraph/{section_id}/"
                f"{paragraph_id}/v{item.paragraph_version}"
            )
            reference = self.artifact_store.put_bytes(
                item.content.encode("utf-8"),
                artifact_type="paragraph",
                schema_version="paragraph_text_v1",
                created_by="writer",
                task_id=context.task_id,
                section_id=section_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
                artifact_ref=artifact_ref,
                content_encoding="text-utf8",
            )
            paragraph = ParagraphArtifactSchema(
                paragraph_id=paragraph_id,
                paragraph_version=item.paragraph_version,
                order=item.order,
                role=item.role,
                content_ref=reference.artifact_ref,
                content_hash=reference.content_hash,
            )
            artifacts.append(paragraph)
            paragraph_event = ParagraphGeneratedEventSchema(
                event_id=(
                    f"{context.invocation_id}:paragraph:{paragraph_id}:"
                    f"v{item.paragraph_version}"
                ),
                task_id=context.task_id,
                run_id=context.run_id,
                section_id=section_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
                paragraph=paragraph,
                occurred_at=self.clock(),
            )
            self.progress_log.append(paragraph_event)
            paragraph_events.append(paragraph_event)
            precheck = self.precheck.check(
                section_id=section_id,
                paragraph_id=paragraph_id,
                content=item.content,
                fact_snapshot=fact_snapshot,
            )
            prechecks.append(precheck)
            if precheck.blocking:
                blocked_paragraph_id = paragraph_id
                break

        actual_ids = [item.paragraph_id for item in artifacts]
        expected_ids = (
            list(expected_paragraph_ids)
            if expected_paragraph_ids is not None
            else list(actual_ids)
        )
        generation_complete = (
            blocked_paragraph_id is None
            and bool(actual_ids)
            and actual_ids == expected_ids
        )
        draft_reference = self.artifact_store.put_json(
            {
                "section_id": section_id,
                "draft_version": draft_version,
                "paragraphs": [
                    item.model_dump(mode="json") for item in artifacts
                ],
                "generation_complete": generation_complete,
                "expected_paragraph_ids": expected_ids,
            },
            artifact_type="section_draft",
            schema_version="draft_snapshot_v1",
            created_by="writer",
            task_id=context.task_id,
            section_id=section_id,
            attempt_id=context.attempt_id,
            input_snapshot_version=context.input_snapshot_version,
            artifact_ref=(
                f"artifact://{context.task_id}/draft/{section_id}/"
                f"v{draft_version}"
            ),
        )
        section_event = None
        if generation_complete:
            section_event = SectionGenerationCompletedEventSchema(
                event_id=f"{context.invocation_id}:section_generation_completed",
                task_id=context.task_id,
                run_id=context.run_id,
                section_id=section_id,
                attempt_id=context.attempt_id,
                input_snapshot_version=context.input_snapshot_version,
                expected_paragraph_ids=expected_ids,
                occurred_at=self.clock(),
            )
            self.progress_log.append(section_event)
        return StreamingWriterOutcomeSchema(
            writer_result=WriterResultPayloadSchema(
                section_id=section_id,
                draft_ref=draft_reference.artifact_ref,
                draft_version=draft_version,
                paragraphs=artifacts,
                generation_complete=generation_complete,
                expected_paragraph_ids=expected_ids,
                generation_cursor=(
                    blocked_paragraph_id
                    or (actual_ids[-1] if actual_ids else None)
                ),
            ),
            paragraph_events=paragraph_events,
            section_completed_event=section_event,
            precheck_results=prechecks,
            blocked_paragraph_id=blocked_paragraph_id,
        )

    def resume_after_precheck(
        self,
        *,
        context: AgentExecutionContextSchema,
        section_id: str,
        draft_version: int,
        blocked_outcome: StreamingWriterOutcomeSchema,
        corrected_and_remaining: Iterable[GeneratedParagraphInputSchema],
        fact_snapshot: FactSnapshotSchema,
        expected_paragraph_ids: list[str],
        retired_paragraph_ids: set[str] | None = None,
    ) -> StreamingWriterOutcomeSchema:
        blocked_id = blocked_outcome.blocked_paragraph_id
        if not blocked_id or blocked_outcome.section_completed_event is not None:
            raise ValueError("writer outcome is not paused at paragraph precheck")
        previous = blocked_outcome.writer_result.paragraphs
        blocked_matches = [
            paragraph
            for paragraph in previous
            if paragraph.paragraph_id == blocked_id
        ]
        if len(blocked_matches) != 1:
            raise ValueError("blocked paragraph artifact is missing or duplicated")
        blocked = blocked_matches[0]
        iterator = iter(corrected_and_remaining)
        try:
            corrected = GeneratedParagraphInputSchema.model_validate(next(iterator))
        except StopIteration as exc:
            raise ValueError("corrected blocked paragraph is required") from exc
        if corrected.paragraph_id != blocked_id:
            raise ValueError("resume must begin with the blocked paragraph id")
        if corrected.paragraph_version <= blocked.paragraph_version:
            raise ValueError("corrected paragraph version must increase")
        if corrected.order != blocked.order:
            raise ValueError("corrected paragraph order cannot change")
        accepted_prefix = [
            paragraph
            for paragraph in previous
            if paragraph.order < blocked.order
        ]

        def resumed_items() -> Iterable[GeneratedParagraphInputSchema]:
            yield corrected
            yield from iterator

        return self.generate(
            context=context,
            section_id=section_id,
            draft_version=draft_version,
            paragraphs=resumed_items(),
            fact_snapshot=fact_snapshot,
            expected_paragraph_ids=expected_paragraph_ids,
            retired_paragraph_ids=retired_paragraph_ids,
            prior_paragraphs=accepted_prefix,
        )

    def _validate_prior_paragraphs(
        self,
        *,
        context: AgentExecutionContextSchema,
        section_id: str,
        paragraphs: list[ParagraphArtifactSchema],
    ) -> None:
        paragraph_ids = [item.paragraph_id for item in paragraphs]
        orders = [item.order for item in paragraphs]
        if len(paragraph_ids) != len(set(paragraph_ids)):
            raise ValueError("prior paragraph ids must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("prior paragraph orders must be unique")
        for paragraph in paragraphs:
            content = self.artifact_store.get_bytes(
                paragraph.content_ref,
                task_id=context.task_id,
                section_id=section_id,
                input_snapshot_version=context.input_snapshot_version,
            )
            actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            if actual_hash != paragraph.content_hash:
                raise ValueError(
                    f"prior paragraph content hash mismatch: "
                    f"{paragraph.paragraph_id}"
                )
