"""Batch manifest and report publication."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .atomic_writer import atomic_write_json, atomic_write_jsonl


def write_batch_metadata(
    output_root: Path,
    *,
    created_at: str,
    documents: Iterable[Mapping[str, Any]],
) -> None:
    """Write metadata-only manifest plus machine-readable batch reports."""

    output_root = output_root.resolve()
    materialized = [dict(document) for document in documents]
    manifest_records = [_manifest_record(document) for document in materialized]
    reports_root = output_root / "reports"
    atomic_write_jsonl(output_root / "manifest.jsonl", manifest_records, root=output_root)
    atomic_write_jsonl(reports_root / "ingestion_report.jsonl", materialized, root=output_root)
    atomic_write_json(
        reports_root / "ingestion_report.json",
        {
            "created_at": created_at,
            "document_count": len(materialized),
            "success_count": sum(
                document.get("status") in {"success", "skipped_unchanged"}
                for document in materialized
            ),
            "failure_count": sum(document.get("status") == "failed" for document in materialized),
            "documents": materialized,
        },
        root=output_root,
    )


def _manifest_record(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": document.get("status"),
        "source_uri": document.get("source_uri"),
        "source_name": document.get("source_name"),
        "source_format": document.get("source_format"),
        "source_sha256": document.get("source_sha256"),
        "source_size": document.get("source_size"),
        "doc_id": document.get("doc_id"),
        "parsed_uri": document.get("parsed_uri"),
        "normalized_uri": document.get("normalized_uri"),
        "created_at": document.get("created_at"),
        "statistics": document.get("statistics"),
        "quality_flags": document.get("quality_flags", []),
        "governance": document.get("governance", {}),
        "error": document.get("error"),
    }
