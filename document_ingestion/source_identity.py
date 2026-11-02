"""Persistent, fail-closed doc_id to source identity projection."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import read_jsonl, validate_record
from .exceptions import (
    DocIdCollisionError,
    OutputArtifactConflictError,
    OutputIdentityMissingError,
    SourceIdentityError,
)
from .quality_checker import document_statistics
from .safe_paths import assert_within, safe_child, validate_safe_component

SOURCE_IDENTITY_SCHEMA_VERSION = "word_source_identity_v1"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    schema_version: str
    doc_id: str
    source_sha256: str
    source_format: str
    source_name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "doc_id": self.doc_id,
            "source_sha256": self.source_sha256,
            "source_format": self.source_format,
            "source_name": self.source_name,
        }


@dataclass(frozen=True, slots=True)
class IdentityClaim:
    identity: SourceIdentity
    created: bool


@dataclass(frozen=True, slots=True)
class ExistingParsedOutput:
    parsed_uri: str
    statistics: dict[str, Any]
    created_at: str
    source_name: str


class SourceIdentityStore:
    """Atomically reserve a doc_id before any formal parser artifact is written."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.identity_root = self.output_root / "identities"
        self.parsed_root = self.output_root / "parsed"

    def identity_path(self, doc_id: str) -> Path:
        validate_safe_component(doc_id, label="doc_id")
        return safe_child(self.identity_root, f"{doc_id}.source_identity.json")

    def parsed_path(self, doc_id: str) -> Path:
        validate_safe_component(doc_id, label="doc_id")
        return safe_child(self.parsed_root, f"{doc_id}.word.parsed.jsonl")

    def claim(
        self,
        *,
        doc_id: str,
        source_sha256: str,
        source_format: str,
        source_name: str,
    ) -> IdentityClaim:
        expected = SourceIdentity(
            schema_version=SOURCE_IDENTITY_SCHEMA_VERSION,
            doc_id=validate_safe_component(doc_id, label="doc_id"),
            source_sha256=_validate_sha256(source_sha256),
            source_format=_validate_source_format(source_format),
            source_name=validate_safe_component(source_name, label="source_name"),
        )
        identity_path = self.identity_path(doc_id)
        parsed_path = self.parsed_path(doc_id)

        if identity_path.exists():
            current = _read_identity(identity_path)
            _assert_compatible(current, expected)
            return IdentityClaim(current, created=False)

        if parsed_path.exists():
            raise OutputIdentityMissingError(
                f"formal parsed output exists without source identity: {parsed_path}"
            )

        self.identity_root.mkdir(parents=True, exist_ok=True)
        created = _atomic_create_identity(identity_path, expected, root=self.output_root)
        if created:
            return IdentityClaim(expected, created=True)
        current = _read_identity(identity_path)
        _assert_compatible(current, expected)
        return IdentityClaim(current, created=False)

    def inspect_existing_parsed(self, identity: SourceIdentity) -> ExistingParsedOutput | None:
        parsed_path = self.parsed_path(identity.doc_id)
        if not parsed_path.exists():
            return None
        try:
            records = read_jsonl(parsed_path)
            if not records:
                raise ValueError("parsed JSONL contains no records")
            for record in records:
                validate_record(record, word_output=True)
                if record["doc_id"] != identity.doc_id:
                    raise ValueError("record doc_id does not match source identity")
                if record["source_format"] != identity.source_format:
                    raise ValueError("record source_format does not match source identity")
                if record["source_name"] != identity.source_name:
                    raise ValueError("record source_name does not match source identity")
                _validate_asset_uri(self.output_root, record.get("table_uri"), label="table_uri")
                _validate_asset_uri(self.output_root, record.get("image_uri"), label="image_uri")
            created_at_values = {str(record["created_at"]) for record in records}
            if len(created_at_values) != 1:
                raise ValueError("parsed JSONL contains inconsistent created_at values")
            statistics = document_statistics(records)
        except Exception as exc:
            raise OutputArtifactConflictError(
                f"existing parsed output is invalid for {identity.doc_id}: {exc}"
            ) from exc
        return ExistingParsedOutput(
            parsed_uri=f"parsed/{parsed_path.name}",
            statistics=statistics,
            created_at=next(iter(created_at_values)),
            source_name=identity.source_name,
        )


def _validate_asset_uri(root: Path, value: Any, *, label: str) -> None:
    if value is None:
        return
    relative = Path(str(value))
    if relative.is_absolute():
        raise ValueError(f"{label} must be relative to output root")
    asset = assert_within(root, root / relative)
    if not asset.is_file() or asset.stat().st_size <= 0:
        raise ValueError(f"{label} is missing or empty: {value}")


def _validate_sha256(value: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise SourceIdentityError("source_sha256 must be a 64-character hexadecimal digest")
    return normalized


def _validate_source_format(value: str) -> str:
    if value not in {"doc", "docx"}:
        raise SourceIdentityError("source_format must be doc or docx")
    return value


def _read_identity(path: Path) -> SourceIdentity:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceIdentityError(f"cannot read source identity {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "doc_id",
        "source_sha256",
        "source_format",
        "source_name",
    }:
        raise SourceIdentityError(f"invalid source identity shape: {path}")
    identity = SourceIdentity(
        schema_version=str(raw["schema_version"]),
        doc_id=validate_safe_component(str(raw["doc_id"]), label="doc_id"),
        source_sha256=_validate_sha256(str(raw["source_sha256"])),
        source_format=_validate_source_format(str(raw["source_format"])),
        source_name=validate_safe_component(str(raw["source_name"]), label="source_name"),
    )
    if identity.schema_version != SOURCE_IDENTITY_SCHEMA_VERSION:
        raise SourceIdentityError(f"unsupported source identity schema: {identity.schema_version}")
    return identity


def _assert_compatible(current: SourceIdentity, expected: SourceIdentity) -> None:
    if current.doc_id != expected.doc_id or current.source_sha256 != expected.source_sha256:
        raise DocIdCollisionError(
            f"doc_id {expected.doc_id} is already bound to source SHA-256 {current.source_sha256}"
        )
    if current.source_format != expected.source_format:
        raise DocIdCollisionError(
            f"doc_id {expected.doc_id} is already bound to source format {current.source_format}"
        )


def _atomic_create_identity(path: Path, identity: SourceIdentity, *, root: Path) -> bool:
    assert_within(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe_child(path.parent, f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(identity.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)
