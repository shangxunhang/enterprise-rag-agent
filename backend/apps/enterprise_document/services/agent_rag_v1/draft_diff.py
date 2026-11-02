"""以真实不可变 Paragraph Artifact 复核 Writer Change Manifest。"""

from __future__ import annotations

import hashlib

from apps.enterprise_document.schemas.agent_rag_v1 import (
    DraftDiffResultSchema,
    DraftSnapshotSchema,
    ParagraphArtifactSchema,
    ParagraphChangeSchema,
    ParagraphChangeType,
    WriterRevisionResultSchema,
)

from .artifact_store import ArtifactIntegrityError, FileArtifactStore


class DraftDiffService:
    def __init__(self, artifact_store: FileArtifactStore) -> None:
        self.artifact_store = artifact_store

    def compare(
        self,
        *,
        task_id: str,
        before: DraftSnapshotSchema,
        after: DraftSnapshotSchema,
        declared: WriterRevisionResultSchema,
    ) -> DraftDiffResultSchema:
        if before.section_id != after.section_id or before.section_id != declared.section_id:
            raise ValueError("draft diff section identity mismatch")
        if declared.base_draft_version != before.draft_version:
            raise ValueError("writer base draft version mismatch")
        if declared.new_draft_version != after.draft_version:
            raise ValueError("writer new draft version mismatch")
        if after.draft_version <= before.draft_version:
            raise ValueError("new draft version must increase")

        old = self._paragraph_map(task_id, before)
        new = self._paragraph_map(task_id, after)
        actual: list[ParagraphChangeSchema] = []
        for paragraph_id in sorted(set(old) | set(new)):
            old_item = old.get(paragraph_id)
            new_item = new.get(paragraph_id)
            if old_item is None:
                actual.append(
                    ParagraphChangeSchema(
                        paragraph_id=paragraph_id,
                        change_type=ParagraphChangeType.ADDED,
                        new_content_hash=new_item.content_hash,
                        reason_code="actual_paragraph_added",
                    )
                )
            elif new_item is None:
                actual.append(
                    ParagraphChangeSchema(
                        paragraph_id=paragraph_id,
                        change_type=ParagraphChangeType.DELETED,
                        old_content_hash=old_item.content_hash,
                        reason_code="actual_paragraph_deleted",
                    )
                )
            elif old_item.content_hash != new_item.content_hash:
                actual.append(
                    ParagraphChangeSchema(
                        paragraph_id=paragraph_id,
                        change_type=ParagraphChangeType.MODIFIED,
                        old_content_hash=old_item.content_hash,
                        new_content_hash=new_item.content_hash,
                        reason_code="actual_text_diff",
                    )
                )
            elif old_item.order != new_item.order:
                actual.append(
                    ParagraphChangeSchema(
                        paragraph_id=paragraph_id,
                        change_type=ParagraphChangeType.MOVED,
                        old_content_hash=old_item.content_hash,
                        new_content_hash=new_item.content_hash,
                        reason_code="actual_paragraph_moved",
                    )
                )

        declared_by_id = {
            item.paragraph_id: item for item in declared.change_manifest
        }
        if len(declared_by_id) != len(declared.change_manifest):
            raise ValueError("change manifest paragraph ids must be unique")
        actual_by_id = {item.paragraph_id: item for item in actual}
        undeclared = sorted(set(actual_by_id) - set(declared_by_id))
        mismatches: list[str] = []
        for paragraph_id in sorted(set(actual_by_id) & set(declared_by_id)):
            actual_item = actual_by_id[paragraph_id]
            declared_item = declared_by_id[paragraph_id]
            if actual_item.change_type != declared_item.change_type:
                mismatches.append(f"{paragraph_id}:change_type")
            if (
                declared_item.old_content_hash is not None
                and declared_item.old_content_hash != actual_item.old_content_hash
            ):
                mismatches.append(f"{paragraph_id}:old_content_hash")
            if (
                declared_item.new_content_hash is not None
                and declared_item.new_content_hash != actual_item.new_content_hash
            ):
                mismatches.append(f"{paragraph_id}:new_content_hash")
        for paragraph_id in sorted(set(declared_by_id) - set(actual_by_id)):
            mismatches.append(f"{paragraph_id}:declared_without_actual_change")
        return DraftDiffResultSchema(
            actual_changes=actual,
            undeclared_paragraph_ids=undeclared,
            declaration_mismatches=mismatches,
        )

    def _paragraph_map(
        self,
        task_id: str,
        draft: DraftSnapshotSchema,
    ) -> dict[str, ParagraphArtifactSchema]:
        result: dict[str, ParagraphArtifactSchema] = {}
        for paragraph in draft.paragraphs:
            if paragraph.paragraph_id in result:
                raise ValueError("draft paragraph ids must be unique")
            content = self.artifact_store.get_bytes(
                paragraph.content_ref,
                task_id=task_id,
            )
            actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            if actual_hash != paragraph.content_hash:
                raise ArtifactIntegrityError(
                    "paragraph artifact hash does not match draft metadata"
                )
            result[paragraph.paragraph_id] = paragraph
        return result
