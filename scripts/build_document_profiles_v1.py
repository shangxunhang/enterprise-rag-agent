"""Build and audit ``document_profiles_v1.jsonl`` from manifest-bound seeds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rag.catalog.document_classifier import (
    DocumentProfileSeedSchema,
    classify_manifest_record,
)
from rag.catalog.document_profile import DocumentProfileSchema
from rag.catalog.document_profile_registry import (
    DocumentProfileCatalogAuditSchema,
    DocumentProfileRegistry,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
        rows.append(payload)
    return rows


def build_catalog(
    manifest_path: Path,
    seed_path: Path,
) -> tuple[list[DocumentProfileSchema], DocumentProfileCatalogAuditSchema]:
    manifest_records = _read_jsonl(manifest_path)
    seeds = [DocumentProfileSeedSchema.model_validate(row) for row in _read_jsonl(seed_path)]

    seed_by_id: dict[str, DocumentProfileSeedSchema] = {}
    for seed in seeds:
        if seed.document_id in seed_by_id:
            raise ValueError(f"duplicate seed document_id: {seed.document_id}")
        seed_by_id[seed.document_id] = seed

    manifest_ids = [str(record.get("doc_id") or "") for record in manifest_records]
    missing_seed_ids = sorted(set(manifest_ids) - set(seed_by_id))
    orphan_seed_ids = sorted(set(seed_by_id) - set(manifest_ids))
    if missing_seed_ids or orphan_seed_ids:
        raise ValueError(
            "seed/manifest coverage mismatch: "
            f"missing_seeds={missing_seed_ids}, orphan_seeds={orphan_seed_ids}"
        )

    profiles = [
        classify_manifest_record(record, seed_by_id[str(record["doc_id"])])
        for record in manifest_records
    ]
    registry = DocumentProfileRegistry(profiles)
    audit = registry.audit_manifest(manifest_records)
    if not audit.passed:
        raise ValueError("document profile catalog audit failed")
    return profiles, audit


def _profiles_jsonl(profiles: list[DocumentProfileSchema]) -> str:
    rendered: list[str] = []
    for profile in profiles:
        payload = profile.model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
        payload["schema_version"] = profile.schema_version
        rendered.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(rendered) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    profiles, audit = build_catalog(args.manifest, args.seeds)
    rendered_profiles = _profiles_jsonl(profiles)
    rendered_audit = json.dumps(
        audit.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"

    if args.stdout:
        print("---DOCUMENT_PROFILES_V1---")
        print(rendered_profiles, end="")
        print("---DOCUMENT_PROFILE_CATALOG_AUDIT_V1---")
        print(rendered_audit, end="")

    if not args.check_only:
        if args.output is None or args.audit is None:
            parser.error("--output and --audit are required unless --check-only is used")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered_profiles, encoding="utf-8")
        args.audit.write_text(rendered_audit, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
