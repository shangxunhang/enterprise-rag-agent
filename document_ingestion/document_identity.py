"""Persistent source identity shared by the document router."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .exceptions import DocIdCollisionError, SourceIdentityError
from .safe_paths import assert_within, safe_child, validate_safe_component

SCHEMA_VERSION = "document_source_identity_v1"
SUPPORTED_FORMATS = {"doc", "docx", "pdf"}
SUPPORTED_BACKENDS = {"word", "pdf"}


@dataclass(frozen=True, slots=True)
class DocumentSourceIdentity:
    schema_version: str
    doc_id: str
    source_sha256: str
    source_format: str
    source_name: str
    backend: str

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "doc_id": self.doc_id,
            "source_sha256": self.source_sha256,
            "source_format": self.source_format,
            "source_name": self.source_name,
            "backend": self.backend,
        }


class DocumentIdentityStore:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.identity_root = self.output_root / "identities"

    def path_for(self, doc_id: str) -> Path:
        validate_safe_component(doc_id, label="doc_id")
        return safe_child(
            self.identity_root,
            f"{doc_id}.document_source_identity.json",
        )

    def claim(
        self,
        *,
        doc_id: str,
        source_sha256: str,
        source_format: str,
        source_name: str,
        backend: str,
    ) -> DocumentSourceIdentity:
        expected = DocumentSourceIdentity(
            schema_version=SCHEMA_VERSION,
            doc_id=validate_safe_component(doc_id, label="doc_id"),
            source_sha256=_validate_sha256(source_sha256),
            source_format=_validate_format(source_format),
            source_name=validate_safe_component(source_name, label="source_name"),
            backend=_validate_backend(backend),
        )
        path = self.path_for(doc_id)
        if path.exists():
            current = _read(path)
            _assert_compatible(current, expected)
            return current
        self.identity_root.mkdir(parents=True, exist_ok=True)
        if _atomic_create(path, expected, root=self.output_root):
            return expected
        current = _read(path)
        _assert_compatible(current, expected)
        return current


def _validate_sha256(value: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise SourceIdentityError("source_sha256 must be a 64-character hexadecimal digest")
    return normalized


def _validate_format(value: str) -> str:
    if value not in SUPPORTED_FORMATS:
        raise SourceIdentityError(f"unsupported source format: {value}")
    return value


def _validate_backend(value: str) -> str:
    if value not in SUPPORTED_BACKENDS:
        raise SourceIdentityError(f"unsupported parser backend: {value}")
    return value


def _read(path: Path) -> DocumentSourceIdentity:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceIdentityError(f"cannot read document source identity {path}: {exc}") from exc
    expected_fields = {
        "schema_version",
        "doc_id",
        "source_sha256",
        "source_format",
        "source_name",
        "backend",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise SourceIdentityError(f"invalid document source identity shape: {path}")
    identity = DocumentSourceIdentity(
        schema_version=str(raw["schema_version"]),
        doc_id=validate_safe_component(str(raw["doc_id"]), label="doc_id"),
        source_sha256=_validate_sha256(str(raw["source_sha256"])),
        source_format=_validate_format(str(raw["source_format"])),
        source_name=validate_safe_component(str(raw["source_name"]), label="source_name"),
        backend=_validate_backend(str(raw["backend"])),
    )
    if identity.schema_version != SCHEMA_VERSION:
        raise SourceIdentityError(
            f"unsupported document source identity schema: {identity.schema_version}"
        )
    return identity


def _assert_compatible(
    current: DocumentSourceIdentity,
    expected: DocumentSourceIdentity,
) -> None:
    if current.doc_id != expected.doc_id or current.source_sha256 != expected.source_sha256:
        raise DocIdCollisionError(
            f"doc_id {expected.doc_id} is already bound to source SHA-256 "
            f"{current.source_sha256}"
        )
    if current.source_format != expected.source_format or current.backend != expected.backend:
        raise DocIdCollisionError(
            f"doc_id {expected.doc_id} is already bound to "
            f"{current.source_format}/{current.backend}"
        )


def _atomic_create(
    path: Path,
    identity: DocumentSourceIdentity,
    *,
    root: Path,
) -> bool:
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
