"""Canonical Post-write Requirement materialization/resolution."""

from __future__ import annotations

from apps.enterprise_document.schemas.post_write.requirement import (
    PostWriteRequirementEntrySchema,
    PostWriteRequirementSnapshotSchema,
    RequirementRefSchema,
)
from core.hashing import canonical_sha256

from .persistence import RequirementStore


class RequirementMaterializer:
    def __init__(self, store: RequirementStore) -> None:
        self.store = store

    @staticmethod
    def _canonical_payload(
        *,
        semantic_contract_version: str,
        source_business_ref: str,
        source_task_ref: str,
        requirements: tuple[PostWriteRequirementEntrySchema, ...],
    ) -> dict:
        normalized = tuple(
            sorted(requirements, key=lambda item: item.requirement_id)
        )
        ids = [item.requirement_id for item in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("Post-write requirement_id values must be unique")
        return {
            "schema_version": "post_write_requirement_snapshot_v1",
            "semantic_contract_version": semantic_contract_version,
            "source_business_ref": source_business_ref,
            "source_task_ref": source_task_ref,
            "requirements": [item.model_dump(mode="json") for item in normalized],
        }

    def materialize(
        self,
        *,
        semantic_contract_version: str,
        source_business_ref: str,
        source_task_ref: str,
        requirements: tuple[PostWriteRequirementEntrySchema, ...],
    ) -> RequirementRefSchema:
        payload = self._canonical_payload(
            semantic_contract_version=semantic_contract_version,
            source_business_ref=source_business_ref,
            source_task_ref=source_task_ref,
            requirements=requirements,
        )
        snapshot_id = "post-write-requirements:" + canonical_sha256(
            {
                "source_business_ref": source_business_ref,
                "source_task_ref": source_task_ref,
                "semantic_contract_version": semantic_contract_version,
            }
        )
        requirement_hash = canonical_sha256(
            {
                **payload,
                "requirement_snapshot_id": snapshot_id,
            }
        )
        snapshot = PostWriteRequirementSnapshotSchema(
            semantic_contract_version=semantic_contract_version,
            requirement_snapshot_id=snapshot_id,
            source_business_ref=source_business_ref,
            source_task_ref=source_task_ref,
            requirements=tuple(
                sorted(requirements, key=lambda item: item.requirement_id)
            ),
            requirement_hash=requirement_hash,
        )
        return self.store.put_if_absent_or_same(snapshot)


class RequirementResolver:
    def __init__(self, store: RequirementStore) -> None:
        self.store = store

    def resolve(self, ref: RequirementRefSchema) -> PostWriteRequirementSnapshotSchema:
        return self.store.resolve_exact(ref)


__all__ = ["RequirementMaterializer", "RequirementResolver"]
