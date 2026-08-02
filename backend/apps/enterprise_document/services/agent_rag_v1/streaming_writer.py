"""Paragraph 原子持久化、Claim/Precheck 与显式章节完成事件。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentRole,
    ClaimSchema,
    ExcelDeviceFactSchema,
    FactSnapshotSchema,
    GeneratedParagraphInputSchema,
    ParagraphArtifactSchema,
    ParagraphGeneratedEventSchema,
    ParagraphPrecheckResultSchema,
    SectionGenerationCompletedEventSchema,
    StreamingWriterOutcomeSchema,
    WriterResultPayloadSchema,
)
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.table_agent_schema import StructuredFactSchema

from .artifact_store import FileArtifactStore
from .paragraph_identity import ParagraphIdentityService
from .claim_pipeline import ClaimPipeline, IncrementalClaimPipeline
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


class CheckpointParagraphPipeline:
    """Production Paragraph pipeline persisted by the existing Section checkpoint.

    The pipeline is deliberately stateless. Every committed paragraph carries
    its content, stable checkpoint URI and integrity hash in the canonical
    ``SectionExecutionGraphPayloadSchema``. Replaying a node with the same
    paragraph is therefore idempotent and does not require a second store.
    """

    _PARAGRAPH_SPLIT = re.compile(r"(?:\r?\n){2,}")

    def __init__(
        self,
        *,
        identity: ParagraphIdentityService | None = None,
        precheck: ParagraphPrecheckService | None = None,
        incremental_claims: IncrementalClaimPipeline | None = None,
    ) -> None:
        claim_pipeline = ClaimPipeline()
        self.identity = identity or ParagraphIdentityService()
        self.precheck = precheck or ParagraphPrecheckService(claim_pipeline)
        self.incremental_claims = incremental_claims or IncrementalClaimPipeline(
            claim_pipeline
        )

    def plan(
        self,
        *,
        section_id: str,
        content: str,
        paragraph_version: int,
        prior_paragraphs: Iterable[ParagraphArtifactSchema] = (),
    ) -> list[GeneratedParagraphInputSchema]:
        """Project canonical section prose into stable logical paragraph slots."""

        normalized = str(content).strip()
        if not normalized:
            return []
        parts = [
            item.strip()
            for item in self._PARAGRAPH_SPLIT.split(normalized)
            if item.strip()
        ]
        if not parts:
            parts = [normalized]
        prior_by_order = {
            item.order: item
            for item in prior_paragraphs
        }
        result: list[GeneratedParagraphInputSchema] = []
        for order, paragraph_content in enumerate(parts):
            role = "body"
            prior = prior_by_order.get(order)
            paragraph_id = (
                prior.paragraph_id
                if prior is not None
                else self.identity.stable_id(
                    section_id=section_id,
                    order=order,
                    role=role,
                )
            )
            version = (
                prior.paragraph_version + 1
                if prior is not None and prior.content != paragraph_content
                else prior.paragraph_version
                if prior is not None
                else paragraph_version
            )
            result.append(
                GeneratedParagraphInputSchema(
                    paragraph_id=paragraph_id,
                    paragraph_version=max(1, version),
                    order=order,
                    role=role,
                    content=paragraph_content,
                )
            )
        return result

    @staticmethod
    def persist(
        *,
        task_id: str,
        run_id: str,
        section_id: str,
        candidate: GeneratedParagraphInputSchema,
    ) -> ParagraphArtifactSchema:
        """Create one immutable checkpoint artifact for a generated paragraph."""

        paragraph_id = str(candidate.paragraph_id or "").strip()
        if not paragraph_id:
            raise ValueError("checkpoint paragraph requires stable paragraph_id")
        content = candidate.content.strip()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        artifact = ParagraphArtifactSchema(
            paragraph_id=paragraph_id,
            paragraph_version=candidate.paragraph_version,
            order=candidate.order,
            role=candidate.role,
            content_ref=(
                f"checkpoint://{task_id}/{run_id}/sections/{section_id}/"
                f"paragraphs/{paragraph_id}/v{candidate.paragraph_version}"
            ),
            content_hash=f"sha256:{digest}",
            content=content,
        )
        artifact.verify_content_integrity()
        return artifact

    @staticmethod
    def assert_idempotent_commit(
        existing: ParagraphArtifactSchema,
        candidate: ParagraphArtifactSchema,
    ) -> None:
        """Reject replay drift while allowing the exact committed atom."""

        comparable = (
            "paragraph_id",
            "paragraph_version",
            "order",
            "role",
            "content_ref",
            "content_hash",
            "content",
        )
        if any(
            getattr(existing, field) != getattr(candidate, field)
            for field in comparable
        ):
            raise ValueError(
                f"paragraph replay conflicts with committed artifact: "
                f"{candidate.paragraph_id}"
            )

    def precheck_paragraph(
        self,
        *,
        section_id: str,
        artifact: ParagraphArtifactSchema,
        fact_snapshot: FactSnapshotSchema,
    ) -> ParagraphPrecheckResultSchema:
        artifact.verify_content_integrity()
        result = self.precheck.check(
            section_id=section_id,
            paragraph_id=artifact.paragraph_id,
            content=artifact.content,
            fact_snapshot=fact_snapshot,
        )
        return result.model_copy(
            update={"paragraph_version": artifact.paragraph_version},
            deep=True,
        )

    def update_claims(
        self,
        *,
        section_id: str,
        artifacts: Iterable[ParagraphArtifactSchema],
        previous_claims: Iterable[ClaimSchema],
        changed_paragraph_ids: set[str],
        fact_snapshot: FactSnapshotSchema,
    ) -> list[ClaimSchema]:
        current = {
            item.paragraph_id: item.content
            for item in artifacts
        }
        previous = list(previous_claims)
        previous_paragraph_ids = {item.paragraph_id for item in previous}
        result = self.incremental_claims.update(
            section_id=section_id,
            current_paragraphs=current,
            previous_claims=previous,
            added_paragraph_ids=(
                set(changed_paragraph_ids) - previous_paragraph_ids
            ),
            modified_paragraph_ids=(
                set(changed_paragraph_ids) & previous_paragraph_ids
            ),
            deleted_paragraph_ids=(
                previous_paragraph_ids - set(current)
            ),
            fact_snapshot=fact_snapshot,
        )
        return result.claims

    @staticmethod
    def assemble_content(
        artifacts: Iterable[ParagraphArtifactSchema],
        *,
        exclude_ids: set[str] | None = None,
    ) -> str:
        excluded = set(exclude_ids or set())
        ordered = sorted(
            (item for item in artifacts if item.paragraph_id not in excluded),
            key=lambda item: item.order,
        )
        for item in ordered:
            item.verify_content_integrity()
        return "\n\n".join(item.content for item in ordered).strip()

    @staticmethod
    def writer_payload(
        *,
        section: SchemeSectionSchema,
        artifacts: Iterable[ParagraphArtifactSchema],
        draft_version: int,
        blocked_paragraph_id: str | None,
    ) -> WriterResultPayloadSchema:
        materialized = sorted(list(artifacts), key=lambda item: item.order)
        expected_ids = [item.paragraph_id for item in materialized]
        generation_complete = bool(materialized) and blocked_paragraph_id is None
        return WriterResultPayloadSchema(
            section_id=section.section_id,
            draft_ref=(
                f"checkpoint://section-draft/{section.section_id}/v{draft_version}"
            ),
            draft_version=draft_version,
            paragraphs=materialized,
            generation_complete=generation_complete,
            expected_paragraph_ids=expected_ids,
            generation_cursor=(
                blocked_paragraph_id
                or (expected_ids[-1] if expected_ids else None)
            ),
            section_snapshot=section,
            writer_metadata={
                "paragraph_pipeline_attached": True,
                "paragraph_count": len(materialized),
                "blocked_paragraph_id": blocked_paragraph_id,
                "artifact_authority": "section_langgraph_checkpoint",
                "paragraph_integration_round": "round6",
            },
        )

    @staticmethod
    def fact_snapshot(
        *,
        task_id: str,
        run_id: str,
        section_id: str,
        snapshot_version: str,
        structured_facts: Iterable[StructuredFactSchema],
    ) -> FactSnapshotSchema:
        devices: list[ExcelDeviceFactSchema] = []
        construction_scope: list[str] = []
        calculation_parameters: dict[str, Any] = {}
        for raw_fact in structured_facts:
            fact = StructuredFactSchema.model_validate(raw_fact)
            extra = dict(fact.extra or {})
            if fact.fact_type == "construction_scope":
                construction_scope.append(fact.content)
                continue
            if fact.fact_type in {"calculation_parameter", "estimate_summary"}:
                calculation_parameters[fact.fact_id] = extra or fact.content
            device_name = str(
                extra.get("device_name")
                or extra.get("name")
                or (
                    fact.content
                    if fact.fact_type in {"device_summary", "resource_config"}
                    else ""
                )
            ).strip()
            if not device_name:
                continue
            devices.append(
                ExcelDeviceFactSchema(
                    fact_id=fact.fact_id,
                    device_name=device_name,
                    aliases=[
                        str(item).strip()
                        for item in extra.get("aliases", [])
                        if str(item).strip()
                    ],
                    device_type=_optional_text(extra.get("device_type")),
                    model=_optional_text(extra.get("model")),
                    specification=_optional_text(extra.get("specification")),
                    quantity=_optional_decimal(extra.get("quantity")),
                    unit=_optional_text(extra.get("unit")),
                    unit_price=_optional_decimal(extra.get("unit_price")),
                    total_amount=_optional_decimal(extra.get("total_amount")),
                    formula=_optional_text(extra.get("formula")),
                    calculation_result=_optional_decimal(
                        extra.get("calculation_result")
                    ),
                    source_ref=(
                        str(fact.source_ids[0])
                        if fact.source_ids
                        else f"fact://{fact.fact_id}"
                    ),
                )
            )
        return FactSnapshotSchema(
            snapshot_version=snapshot_version,
            artifact_ref=(
                f"checkpoint://{task_id}/{run_id}/sections/{section_id}/"
                "fact-snapshot"
            ),
            devices=devices,
            construction_scope=list(dict.fromkeys(construction_scope)),
            calculation_parameters=calculation_parameters,
        )


def _optional_text(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid structured numeric fact: {value}") from exc
