"""Project reviewed document profiles onto cleaned-text-unit records.

Stage C treats ``document_profiles_v1.jsonl`` as the authoritative document
catalog.  The projection performed here is intentionally strict:

* every source document must have one reviewed profile;
* the source SHA-256 carried by cleaned records must match the profile;
* profile governance overrides legacy record-level KB/default metadata;
* non-indexable and Gold profiles never reach chunking;
* the complete profile boundary is copied into ``extra.document_profile`` so
  parent/child chunks and vector records retain evidence-role lineage.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from rag.catalog.document_profile import DocumentProfileSchema
from rag.schema.cleaned_text_unit import (
    CleanedTextUnitSchema,
    migrate_legacy_cleaned_text_unit_payload,
)


def canonical_profile_hash(profile: DocumentProfileSchema) -> str:
    """Return a stable SHA-256 for one canonical document profile."""

    payload = profile.model_dump(mode="json", exclude_none=False)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_record_sha256(record: Mapping[str, Any]) -> str | None:
    """Read producer source SHA-256 from approved top-level or migrated fields."""

    direct = record.get("source_sha256")
    if direct not in (None, ""):
        return str(direct)

    extra = record.get("extra")
    if not isinstance(extra, Mapping):
        return None

    producer = extra.get("producer_top_level_extensions")
    if isinstance(producer, Mapping):
        value = producer.get("source_sha256")
        if value not in (None, ""):
            return str(value)
    return None


def profile_should_index(profile: DocumentProfileSchema) -> bool:
    """Return the reviewed catalog disposition and reject ambiguous values."""

    value = profile.metadata.get("should_index")
    if not isinstance(value, bool):
        raise TypeError(
            "document profile metadata.should_index must be boolean; "
            f"document_id={profile.document_id!r}"
        )
    return value


def profile_is_gold(profile: DocumentProfileSchema) -> bool:
    """Return the reviewed Gold marker and reject ambiguous values."""

    value = profile.metadata.get("is_gold_reference", False)
    if not isinstance(value, bool):
        raise TypeError(
            "document profile metadata.is_gold_reference must be boolean; "
            f"document_id={profile.document_id!r}"
        )
    return value


def project_source_record(
    source_record: Mapping[str, Any],
    *,
    profile: DocumentProfileSchema,
    catalog_sha256: str,
) -> dict[str, Any] | None:
    """Validate and project one producer record into the Stage C RAG boundary.

    Returns ``None`` for reviewed non-indexable records.  Any identity or hash
    mismatch is a hard failure because silently falling back to old KB metadata
    would reintroduce the evidence-boundary defect Stage C is intended to fix.
    """

    raw = dict(source_record)
    document_id = str(raw.get("doc_id") or "").strip()
    if not document_id:
        raise ValueError("profile-aware source record requires doc_id")
    if document_id != profile.document_id:
        raise ValueError(
            "source/profile document identity mismatch: "
            f"source={document_id!r} profile={profile.document_id!r}"
        )

    expected_hash = str(profile.metadata.get("source_sha256") or "").strip()
    actual_hash = str(source_record_sha256(raw) or "").strip()
    if not expected_hash:
        raise ValueError(
            "document profile missing metadata.source_sha256; "
            f"document_id={document_id!r}"
        )
    if actual_hash != expected_hash:
        raise ValueError(
            "cleaned record source hash does not match document profile; "
            f"document_id={document_id!r} expected={expected_hash!r} "
            f"actual={actual_hash!r}"
        )

    should_index = profile_should_index(profile)
    is_gold = profile_is_gold(profile)
    if is_gold and should_index:
        raise ValueError(
            "Gold document profile must not be indexable; "
            f"document_id={document_id!r}"
        )
    if not should_index:
        return None

    migrated = migrate_legacy_cleaned_text_unit_payload(raw)
    canonical = CleanedTextUnitSchema.model_validate(migrated).model_dump()

    # Document Profile is authoritative over dataset defaults and stale record
    # values.  The source document name remains the user-facing file identity.
    canonical["tenant_id"] = profile.tenant_id
    canonical["kb_id"] = profile.kb_id
    canonical["source_name"] = (
        profile.source_name or canonical.get("source_name")
    )
    canonical["source_uri"] = (
        profile.source_uri or canonical.get("source_uri")
    )
    canonical["title"] = profile.title or canonical.get("title")
    profile_source_format = profile.metadata.get("source_format")
    if profile_source_format not in (None, ""):
        canonical["source_format"] = str(profile_source_format)
    canonical["file_id"] = (
        profile.source_name
        or canonical.get("source_name")
        or profile.document_id
    )

    extra = dict(canonical.get("extra") or {})
    authority = profile.authority.model_dump(mode="json", exclude_none=False)
    authority["level"] = profile.authority.level.value
    project_relation = profile.project_relation.relation.value
    allowed_claim_types = [
        item.value for item in profile.content_capabilities.allowed_claim_types
    ]
    forbidden_claim_types = [
        item.value for item in profile.content_capabilities.forbidden_claim_types
    ]
    document_profile = {
        "schema_version": profile.schema_version,
        "document_id": profile.document_id,
        "source_sha256": expected_hash,
        "document_type": profile.document_type.value,
        "evidence_role": profile.evidence_role.value,
        "kb_id": profile.kb_id,
        "tenant_id": profile.tenant_id,
        "project_relation": project_relation,
        "project_id": profile.project_relation.project_id,
        "authority": authority,
        "content_capabilities": {
            "allowed_claim_types": allowed_claim_types,
            "forbidden_claim_types": forbidden_claim_types,
        },
        "parsing": {
            "parser_strategy": profile.parsing.parser_strategy.value,
            "chunk_strategy": profile.parsing.chunk_strategy.value,
            "fallback_chunk_strategy": (
                profile.parsing.fallback_chunk_strategy.value
                if profile.parsing.fallback_chunk_strategy is not None
                else None
            ),
        },
        "classification": {
            "method": profile.classification.method.value,
            "confidence": profile.classification.confidence,
            "reviewed": profile.classification.reviewed,
            "reviewed_by": profile.classification.reviewer,
            "reviewed_at": profile.metadata.get("reviewed_at"),
            "reason_codes": list(profile.classification.reason_codes),
        },
        "source_document_form": profile.metadata.get(
            "source_document_form"
        ),
        "taxonomy_mapping": profile.metadata.get("taxonomy_mapping"),
        "should_index": should_index,
        "is_gold_reference": is_gold,
        "profile_sha256": canonical_profile_hash(profile),
        "catalog_sha256": catalog_sha256,
    }
    extra["document_profile"] = document_profile

    # These scalar mirrors are filter aids only.  The nested profile above is
    # the single source of truth and the values are derived from it here.
    extra.update(
        {
            "document_type": document_profile["document_type"],
            "evidence_role": document_profile["evidence_role"],
            "project_relation": document_profile["project_relation"],
            "project_id": document_profile["project_id"],
            "authority_level": authority["level"],
        }
    )
    canonical["extra"] = extra
    return CleanedTextUnitSchema.model_validate(canonical).model_dump()


__all__ = [
    "canonical_profile_hash",
    "profile_is_gold",
    "profile_should_index",
    "project_source_record",
    "source_record_sha256",
]
