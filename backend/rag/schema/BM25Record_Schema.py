"""Canonical BM25 child-record projection with Dense-equivalent metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_bm25_record_v1(
    *,
    child_chunk: dict[str, Any],
    dataset_version: str,
    offline_index_config_hash: str,
) -> dict[str, Any]:
    """Project one child chunk into the immutable BM25 artifact contract."""

    child_id = str(
        child_chunk.get("child_chunk_id")
        or child_chunk.get("chunk_id")
        or ""
    ).strip()
    parent_id = str(child_chunk.get("parent_chunk_id") or "").strip()
    document_id = str(child_chunk.get("doc_id") or "").strip()
    if not child_id or not parent_id or not document_id:
        raise ValueError("BM25 records require child_id, parent_id and document_id")

    child_extra = child_chunk.get("extra")
    if not isinstance(child_extra, dict):
        raise ValueError(f"BM25 child extra must be an object: {child_id}")
    profile = child_extra.get("document_profile")
    chunking = child_extra.get("type_aware_chunking")
    if not isinstance(profile, dict) or not isinstance(chunking, dict):
        raise ValueError(
            "BM25 records require document_profile and type_aware_chunking; "
            f"child_id={child_id!r}"
        )
    if str(profile.get("document_id") or "") != document_id:
        raise ValueError(
            "BM25 document identity conflicts with canonical profile; "
            f"child_id={child_id!r}"
        )
    if str(profile.get("kb_id") or "") != str(child_chunk.get("kb_id") or ""):
        raise ValueError(
            "BM25 kb_id conflicts with canonical profile; "
            f"child_id={child_id!r}"
        )

    return {
        "schema_version": "bm25_child_record_v1",
        "record_id": child_id,
        "child_id": child_id,
        "child_chunk_id": child_id,
        "parent_id": parent_id,
        "parent_chunk_id": parent_id,
        "document_id": document_id,
        "doc_id": document_id,
        "tenant_id": str(child_chunk.get("tenant_id") or ""),
        "kb_id": str(child_chunk.get("kb_id") or ""),
        "file_id": str(child_chunk.get("file_id") or ""),
        "document_type": profile.get("document_type"),
        "evidence_role": profile.get("evidence_role"),
        "project_relation": profile.get("project_relation"),
        "project_id": profile.get("project_id"),
        "authority_level": (profile.get("authority") or {}).get("level"),
        "text": str(child_chunk.get("text") or ""),
        "text_hash": str(child_chunk.get("text_hash") or ""),
        "title": child_chunk.get("title"),
        "section": child_chunk.get("section"),
        "page_start": child_chunk.get("page_start"),
        "page_end": child_chunk.get("page_end"),
        "source_unit_ids": list(child_chunk.get("source_unit_ids") or []),
        "extra": {
            "dataset_version": dataset_version,
            "offline_index_config_hash": offline_index_config_hash,
            "document_profile": deepcopy(profile),
            "type_aware_chunking": deepcopy(chunking),
        },
        "document_profile": deepcopy(profile),
        "type_aware_chunking": deepcopy(chunking),
    }


__all__ = ["build_bm25_record_v1"]
