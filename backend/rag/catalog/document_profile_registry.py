"""Strict loader and reconciliation checks for document profile catalogs."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .document_profile import DocumentProfileSchema, DocumentType


class DocumentProfileCatalogAuditSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["document_profile_catalog_audit_v1"] = (
        "document_profile_catalog_audit_v1"
    )
    manifest_document_count: int = Field(ge=0)
    profile_document_count: int = Field(ge=0)
    indexable_document_count: int = Field(ge=0)
    non_indexable_document_count: int = Field(ge=0)
    reviewed_document_count: int = Field(ge=0)
    unknown_document_count: int = Field(ge=0)
    missing_profile_document_ids: list[str] = Field(default_factory=list)
    orphan_profile_document_ids: list[str] = Field(default_factory=list)
    duplicate_profile_document_ids: list[str] = Field(default_factory=list)
    source_hash_mismatch_document_ids: list[str] = Field(default_factory=list)
    document_type_counts: dict[str, int] = Field(default_factory=dict)
    evidence_role_counts: dict[str, int] = Field(default_factory=dict)
    kb_counts: dict[str, int] = Field(default_factory=dict)
    passed: bool

    @model_validator(mode="after")
    def _passed_matches_findings(self) -> "DocumentProfileCatalogAuditSchema":
        has_hard_findings = any(
            (
                self.missing_profile_document_ids,
                self.orphan_profile_document_ids,
                self.duplicate_profile_document_ids,
                self.source_hash_mismatch_document_ids,
            )
        )
        expected = (
            not has_hard_findings
            and self.manifest_document_count == self.profile_document_count
            and self.unknown_document_count == 0
            and self.reviewed_document_count == self.profile_document_count
        )
        if self.passed != expected:
            raise ValueError("audit passed flag does not match catalog findings")
        return self


class DocumentProfileRegistry:
    """In-memory profile registry with duplicate-safe identity lookup."""

    def __init__(self, profiles: Iterable[DocumentProfileSchema]) -> None:
        profile_list = list(profiles)
        ids = [profile.document_id for profile in profile_list]
        duplicates = sorted(doc_id for doc_id, count in Counter(ids).items() if count > 1)
        if duplicates:
            raise ValueError("duplicate document profiles: " + ", ".join(duplicates))
        self._profiles = {profile.document_id: profile for profile in profile_list}

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "DocumentProfileRegistry":
        source = Path(path)
        profiles: list[DocumentProfileSchema] = []
        for line_number, raw_line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
                profiles.append(DocumentProfileSchema.model_validate(payload))
            except Exception as exc:
                raise ValueError(
                    f"invalid document profile at {source}:{line_number}: {exc}"
                ) from exc
        return cls(profiles)

    def get(self, document_id: str) -> DocumentProfileSchema:
        try:
            return self._profiles[document_id]
        except KeyError as exc:
            raise KeyError(f"document profile not found: {document_id}") from exc

    def all(self) -> list[DocumentProfileSchema]:
        return list(self._profiles.values())

    def __len__(self) -> int:
        return len(self._profiles)

    def audit_manifest(
        self,
        manifest_records: Iterable[Mapping[str, Any]],
    ) -> DocumentProfileCatalogAuditSchema:
        manifest_list = list(manifest_records)
        manifest_by_id = {
            str(record["doc_id"]): record
            for record in manifest_list
            if record.get("doc_id")
        }
        profile_ids = set(self._profiles)
        manifest_ids = set(manifest_by_id)
        missing = sorted(manifest_ids - profile_ids)
        orphan = sorted(profile_ids - manifest_ids)
        hash_mismatches: list[str] = []

        for document_id in sorted(profile_ids & manifest_ids):
            manifest_hash = manifest_by_id[document_id].get("source_sha256")
            profile_hash = self._profiles[document_id].metadata.get("source_sha256")
            if manifest_hash != profile_hash:
                hash_mismatches.append(document_id)

        profiles = self.all()
        type_counts = Counter(profile.document_type.value for profile in profiles)
        role_counts = Counter(profile.evidence_role.value for profile in profiles)
        kb_counts = Counter(profile.kb_id for profile in profiles)
        indexable_count = sum(
            1 for profile in profiles if profile.metadata.get("should_index") is True
        )
        non_indexable_count = len(profiles) - indexable_count
        reviewed_count = sum(1 for profile in profiles if profile.classification.reviewed)
        unknown_count = sum(
            1 for profile in profiles if profile.document_type is DocumentType.UNKNOWN
        )
        passed = (
            not missing
            and not orphan
            and not hash_mismatches
            and len(manifest_by_id) == len(profiles)
            and unknown_count == 0
            and reviewed_count == len(profiles)
        )

        return DocumentProfileCatalogAuditSchema(
            manifest_document_count=len(manifest_by_id),
            profile_document_count=len(profiles),
            indexable_document_count=indexable_count,
            non_indexable_document_count=non_indexable_count,
            reviewed_document_count=reviewed_count,
            unknown_document_count=unknown_count,
            missing_profile_document_ids=missing,
            orphan_profile_document_ids=orphan,
            duplicate_profile_document_ids=[],
            source_hash_mismatch_document_ids=hash_mismatches,
            document_type_counts=dict(sorted(type_counts.items())),
            evidence_role_counts=dict(sorted(role_counts.items())),
            kb_counts=dict(sorted(kb_counts.items())),
            passed=passed,
        )


__all__ = [
    "DocumentProfileCatalogAuditSchema",
    "DocumentProfileRegistry",
]
