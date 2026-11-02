"""Immutable RunCompletionAssessment derived-fact persistence."""

from __future__ import annotations

import json
from pathlib import Path

from apps.enterprise_document.schemas.agent_rag_v1.lifecycle import (
    RunCompletionAssessmentRecordSchema,
    RunCompletionAssessmentRefSchema,
)
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink
from core.persistence.interprocess_lock import InterProcessFileLock
from core.runtime.side_effects import (
    CommittedResultResolution,
    CommittedResultResolutionStatus,
    LogicalOperationRef,
    validate_sif02_semantic_fingerprint,
)


class RunCompletionAssessmentStoreError(ValueError):
    pass


class RunCompletionAssessmentStoreIntegrityError(
    RunCompletionAssessmentStoreError
):
    pass


class RunCompletionAssessmentStoreConflict(
    RunCompletionAssessmentStoreError
):
    pass


class RunCompletionAssessmentStore:
    """One immutable derived-fact store; not lifecycle/Gate/Release authority."""

    def __init__(
        self,
        path: str | Path = (
            "data/runtime/agent_rag_v1/run_completion_assessments.jsonl"
        ),
    ) -> None:
        self.path = Path(path)
        self.sink = CrashSafeJsonlRecordSink(self.path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @staticmethod
    def _decode(raw: dict) -> RunCompletionAssessmentRecordSchema:
        try:
            return RunCompletionAssessmentRecordSchema.model_validate(raw)
        except Exception as exc:
            raise RunCompletionAssessmentStoreIntegrityError(
                "corrupt RunCompletionAssessment history"
            ) from exc

    def _records_unlocked(
        self,
    ) -> tuple[RunCompletionAssessmentRecordSchema, ...]:
        records = tuple(self._decode(item) for item in self.sink.read_records())
        assessment_ids = [item.assessment_id for item in records]
        operation_ids = [
            item.committed_operation_ref.logical_operation_id
            for item in records
        ]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise RunCompletionAssessmentStoreIntegrityError(
                "duplicate RunCompletionAssessment identity in history"
            )
        if len(operation_ids) != len(set(operation_ids)):
            raise RunCompletionAssessmentStoreIntegrityError(
                "duplicate RunCompletionAssessment operation in history"
            )
        return records

    def _resolve_by_id_unlocked(
        self,
        assessment_id: str,
    ) -> RunCompletionAssessmentRecordSchema:
        matches = [
            item
            for item in self._records_unlocked()
            if item.assessment_id == assessment_id
        ]
        if len(matches) != 1:
            raise RunCompletionAssessmentStoreIntegrityError(
                "required exact RunCompletionAssessment is missing or ambiguous"
            )
        return matches[0]

    def put_if_absent_or_same(
        self,
        record: RunCompletionAssessmentRecordSchema,
    ) -> RunCompletionAssessmentRefSchema:
        incoming = RunCompletionAssessmentRecordSchema.model_validate(record)
        serialized = json.dumps(
            incoming.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with InterProcessFileLock(self.lock_path):
            records = self._records_unlocked()
            by_id = next(
                (
                    item
                    for item in records
                    if item.assessment_id == incoming.assessment_id
                ),
                None,
            )
            if by_id is not None:
                if by_id == incoming:
                    return by_id.as_ref()
                if (
                    by_id.semantic_fingerprint
                    != incoming.semantic_fingerprint
                ):
                    raise RunCompletionAssessmentStoreConflict(
                        "same assessment identity has conflicting semantic fingerprint"
                    )
                raise RunCompletionAssessmentStoreConflict(
                    "same assessment identity has conflicting immutable payload"
                )

            operation_id = incoming.committed_operation_ref.logical_operation_id
            by_operation = next(
                (
                    item
                    for item in records
                    if item.committed_operation_ref.logical_operation_id
                    == operation_id
                ),
                None,
            )
            if by_operation is not None:
                if by_operation == incoming:
                    return by_operation.as_ref()
                raise RunCompletionAssessmentStoreConflict(
                    "RUN_COMPLETION_ASSESSMENT_COMMIT operation collision"
                )

            try:
                self.sink.append_record_once(
                    serialized,
                    id_field="assessment_id",
                    identity=incoming.assessment_id,
                )
            except ValueError as exc:
                raise RunCompletionAssessmentStoreConflict(str(exc)) from exc

            committed = self._resolve_by_id_unlocked(incoming.assessment_id)
            if committed != incoming:
                raise RunCompletionAssessmentStoreIntegrityError(
                    "RunCompletionAssessment durable verification mismatch"
                )
            return committed.as_ref()

    def resolve_exact(
        self,
        ref: RunCompletionAssessmentRefSchema,
    ) -> RunCompletionAssessmentRecordSchema:
        exact = RunCompletionAssessmentRefSchema.model_validate(ref)
        with InterProcessFileLock(self.lock_path):
            record = self._resolve_by_id_unlocked(exact.assessment_id)
        if record.as_ref() != exact:
            raise RunCompletionAssessmentStoreIntegrityError(
                "RunCompletionAssessment exact ref/hash mismatch"
            )
        return record

    def resolve_by_id(
        self,
        assessment_id: str,
    ) -> RunCompletionAssessmentRecordSchema:
        normalized = str(assessment_id or "").strip()
        if not normalized:
            raise ValueError("assessment_id is required")
        with InterProcessFileLock(self.lock_path):
            return self._resolve_by_id_unlocked(normalized)

    def resolve_committed_result(
        self,
        operation_ref: LogicalOperationRef,
        semantic_fingerprint: str,
    ) -> CommittedResultResolution:
        fingerprint = validate_sif02_semantic_fingerprint(
            semantic_fingerprint
        )
        with InterProcessFileLock(self.lock_path):
            matches = [
                item
                for item in self._records_unlocked()
                if item.committed_operation_ref.logical_operation_id
                == operation_ref.logical_operation_id
            ]
        if not matches:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=CommittedResultResolutionStatus.ABSENT,
            )
        if len(matches) != 1:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=(
                    CommittedResultResolutionStatus.INTEGRITY_INVALID
                ),
            )
        record = matches[0]
        committed = record.committed_operation_ref
        if (
            committed.operation_kind != operation_ref.operation_kind
            or committed.run_id != operation_ref.run_id
            or committed.operation_scope_ref
            != operation_ref.operation_scope_ref
            or operation_ref.attempt_scope_ref is not None
            or operation_ref.attempt_id is not None
        ):
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=(
                    CommittedResultResolutionStatus.INTEGRITY_INVALID
                ),
            )
        if record.semantic_fingerprint != fingerprint:
            return CommittedResultResolution(
                logical_operation_id=operation_ref.logical_operation_id,
                semantic_fingerprint=fingerprint,
                resolution=(
                    CommittedResultResolutionStatus.COMMITTED_CONFLICT
                ),
                committed_result_refs=(record.assessment_id,),
            )
        return CommittedResultResolution(
            logical_operation_id=operation_ref.logical_operation_id,
            semantic_fingerprint=fingerprint,
            resolution=CommittedResultResolutionStatus.COMMITTED_SAME,
            committed_result_refs=(record.assessment_id,),
        )


__all__ = [
    "RunCompletionAssessmentStore",
    "RunCompletionAssessmentStoreConflict",
    "RunCompletionAssessmentStoreError",
    "RunCompletionAssessmentStoreIntegrityError",
]
