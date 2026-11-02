"""文档和 Generation Report 的不可变正式版本发布。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AssembledDocumentSchema,
    GenerationReportSchema,
    PublishedDocumentVersionSchema,
)

from .artifact_store import FileArtifactStore


class DocumentVersionPublisher:
    def __init__(
        self,
        *,
        artifact_store: FileArtifactStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def publish(
        self,
        *,
        assembled: AssembledDocumentSchema,
        report: GenerationReportSchema,
        attempt_id: str,
        input_snapshot_version: str,
    ) -> PublishedDocumentVersionSchema:
        if assembled.task_status != report.task_status:
            raise ValueError("document/report task status mismatch")
        document_content = self.artifact_store.get_bytes(
            assembled.document_artifact_ref,
            task_id=assembled.task_id,
        )
        document_ref = self.artifact_store.put_bytes(
            document_content,
            artifact_type="published_document",
            schema_version="construction_scheme_markdown_v1",
            created_by="supervisor",
            task_id=assembled.task_id,
            attempt_id=attempt_id,
            input_snapshot_version=input_snapshot_version,
            artifact_ref=(
                f"artifact://{assembled.task_id}/document/"
                f"{report.document_version}"
            ),
            content_encoding="markdown-utf8",
        )
        report_ref = self.artifact_store.put_json(
            report.model_dump(mode="json"),
            artifact_type="generation_report",
            schema_version=report.schema_version,
            created_by="supervisor",
            task_id=assembled.task_id,
            attempt_id=attempt_id,
            input_snapshot_version=input_snapshot_version,
            artifact_ref=(
                f"artifact://{assembled.task_id}/generation-report/"
                f"{report.document_version}"
            ),
        )
        return PublishedDocumentVersionSchema(
            task_id=assembled.task_id,
            run_id=assembled.run_id,
            document_version=report.document_version,
            task_status=report.task_status,
            document_artifact_ref=document_ref.artifact_ref,
            generation_report_ref=report_ref.artifact_ref,
            published_at=self.clock(),
        )
